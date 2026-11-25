"""QUANTX autonomous agent — the persistent backend process that runs the live paper-trading desk.

It runs inside the FastAPI server (not the browser): closing the website does not stop it, and after a
server restart it resumes from the database (positions, resting orders, tiers, settings).

Event flow
  Coinbase WebSocket ticks / Kraken & DEX polling ─► desk.on_quote ─► broker fills: limit entries,
        stops, take-profits, trailing stops, time exits ─► trade reports ─► evidence update
  candle-close scheduler (per asset × timeframe) ─► fetch only new candles ─► analyse ─► rank
        ─► desk.on_candle: HOLD / SELL decisions for open positions, entry decisions for new setups
  universe 30 min · news 5 min · evidence calibration on start + every 6 h · equity 60 s · reports hourly check
"""
from __future__ import annotations

import asyncio
import collections
import logging
import math
import re
import time
from dataclasses import dataclass, field

import numpy as np

from ..analysis.structure import analyse, market_structure_label
from ..core import clock, db
from ..core.http import UpstreamError
from ..market import feeds
from ..market.candles import TF_SECONDS, Candle, Series
from ..paper.broker import Broker, Quote
from ..paper.risk import entries_suspended
from ..paper.settings import get_settings
from ..providers import crypto as crypto_provider
from ..providers import news as news_provider
from ..quant import backtest, pairs
from ..quant.probability import ProbabilityModel
from ..quant.selector import Selector
from ..strategies.library import RESEARCH_ONLY, STRATEGIES
from ..trading import reports
from ..trading.desk import MAX_HOLD, AssetCtx, Desk

log = logging.getLogger("quantx.agent")

STABLES = {"USDT", "USDC", "DAI", "USDS", "USDE", "FDUSD", "TUSD", "PYUSD", "USD1", "EURC", "GUSD", "USDP", "RLUSD", "PAXG"}
CLASS_CFG = {
    "crypto":    {"tfs": ["15m", "1h", "1d"], "tf": "15m", "max_hold": 64, "history_bars": {"15m": 2400, "1h": 2400, "1d": 1000}},
    "commodity": {"tfs": ["15m", "1h", "1d"], "tf": "15m", "max_hold": 64, "history_bars": {"15m": 2400, "1h": 2400, "1d": 1000}},
    "fx":        {"tfs": ["15m", "1h", "1d"], "tf": "1h", "max_hold": 48, "history_bars": {"15m": 720, "1h": 720, "1d": 720}},
    "memecoin":  {"tfs": ["5m"], "tf": "5m", "max_hold": 48, "history_bars": {"5m": 1000}},
}
FX_PAIRS = ["EUR/USD", "GBP/USD", "AUD/USD", "EUR/GBP", "EUR/JPY", "AUD/JPY", "EUR/CHF", "EUR/CAD"]
WINDOW = 400
ACCOUNT = "live"


@dataclass
class AssetState:
    symbol: str                      # Coinbase product id, Kraken altname, or pool address
    display: str
    asset_class: str
    source: str                      # coinbase | kraken | geckoterminal
    tfs: list[str]
    series: dict[str, Series] = field(default_factory=dict)
    next_close: dict[str, int] = field(default_factory=dict)
    bid: float | None = None
    ask: float | None = None
    last: float | None = None
    quote_ts: float | None = None
    volume_24h_usd: float | None = None
    liquidity_usd: float | None = None
    market_cap_usd: float | None = None
    rank_score: float = 0.0
    rank_parts: dict = field(default_factory=dict)
    excluded: str | None = None
    last_analysis: dict | None = None
    analysed_at: float | None = None
    names: list[str] = field(default_factory=list)
    news_count: int = 0
    meta: dict = field(default_factory=dict)
    delayed: bool = False

    @property
    def tf(self) -> str:
        return self.tfs[0]

    @property
    def spread_pct(self) -> float | None:
        if self.bid and self.ask and self.ask >= self.bid > 0:
            return 100 * (self.ask - self.bid) / ((self.ask + self.bid) / 2)
        return None

    def ctx(self) -> AssetCtx:
        return AssetCtx(self.symbol, self.display, self.asset_class, self.volume_24h_usd, self.liquidity_usd,
                        self.spread_pct, self.excluded, self.names)

    def quote(self, max_age: float = 120) -> Quote | None:
        if self.bid and self.ask and self.quote_ts and time.time() - self.quote_ts < max_age:
            return Quote(self.bid, self.ask, self.last or (self.bid + self.ask) / 2, time.time())
        return None

    def public(self) -> dict:
        primary = self.series.get(self.tf)
        return {"symbol": self.symbol, "display": self.display, "asset_class": self.asset_class, "tf": self.tf, "tfs": self.tfs,
                "source": self.source, "price": self.last, "bid": self.bid, "ask": self.ask, "spread_pct": self.spread_pct,
                "volume_24h_usd": self.volume_24h_usd, "liquidity_usd": self.liquidity_usd,
                "market_cap_usd": self.market_cap_usd, "rank_score": round(self.rank_score, 1),
                "rank_parts": self.rank_parts, "excluded": self.excluded, "analysis": self.last_analysis,
                "analysed_at": self.analysed_at, "bars": {tf: len(s) for tf, s in self.series.items()},
                "gaps": primary.gaps if primary else None, "news_mentions_24h": self.news_count,
                "quote_age_s": (time.time() - self.quote_ts) if self.quote_ts else None,
                "data_note": "price polled every ~10 s" if self.source == "kraken" else
                             ("DEX price polled; no bid/ask" if self.source == "geckoterminal" else "live WebSocket"),
                **self.meta}


class Agent:
    def __init__(self) -> None:
        self.state = "offline"
        self.active = False
        self.mode = "off"                  # off | trading | managing | emergency_stopped
        self.current = {"task": None, "asset": None, "strategy": None}
        self.assets: dict[str, AssetState] = {}
        self.logbuf: collections.deque = collections.deque(maxlen=800)
        self.subscribers: set[asyncio.Queue] = set()
        self.tasks: list[asyncio.Task] = []
        self.stream = feeds.CoinbaseTickerStream(self._on_tick, self._on_stream_status)
        self.broker = Broker(ACCOUNT)
        self.selector = Selector(ACCOUNT)
        self.desk = Desk(ACCOUNT, self.broker, self.selector, self.emit)
        self.model: ProbabilityModel | None = None
        self.calibration: dict = db.kv_get("calibration_summary") or {}
        self.news: list[dict] = []
        self.pairs: list[dict] = []
        self.sources: dict[str, dict] = {}
        self.counters = collections.Counter()
        self.latency = {"candle_to_decision_ms": collections.deque(maxlen=200),
                        "analysis_ms": collections.deque(maxlen=200), "tick_ms": collections.deque(maxlen=500)}
        self.errors: collections.deque = collections.deque(maxlen=50)
        self.started_at: float | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ logging / status
    async def emit(self, message: str, kind: str = "info", level: str = "info", asset: str | None = None,
                   strategy: str | None = None, latency_ms: float | None = None, data: dict | None = None,
                   account: str | None = None) -> None:
        ev = {"ts": time.time(), "level": level, "kind": kind, "asset": asset, "strategy": strategy,
              "message": message, "latency_ms": None if latency_ms is None else round(latency_ms, 1), "data": data}
        self.logbuf.append(ev)
        if level == "error":
            self.errors.append(ev)
        try:
            with db.engine().begin() as c:
                c.execute(db.insert(db.activity).values(account=ACCOUNT, **{**ev, "data": db.jsonable(data) if data else None}))
        except Exception as e:  # logging must never kill the agent
            log.warning("activity persist failed: %s", e)
        for q in list(self.subscribers):
            if q.qsize() < 1000:
                q.put_nowait({"type": "log", "event": ev})

    def set_state(self, state: str, task: str | None = None, asset: str | None = None, strategy: str | None = None) -> None:
        if self.mode == "emergency_stopped":
            state = "stopped"
        self.state = state
        self.current = {"task": task, "asset": asset, "strategy": strategy}

    def status(self) -> dict:
        def avg(d):
            return round(sum(d) / len(d), 2) if d else None

        def p95(d):
            return round(float(np.percentile(list(d), 95)), 2) if d else None
        acct = self.broker.state()
        by_class = collections.Counter(a.asset_class for a in self.assets.values())
        return {
            "state": self.state, "active": self.active, "mode": self.mode, "entries_enabled": self.desk.entries_enabled,
            "current": self.current, "started_at": self.started_at,
            "counters": {**dict(self.counters), **self.desk.counters},
            "last_decision": self.desk.last_decision,
            "assets": {"total": len(self.assets), "eligible": sum(1 for a in self.assets.values() if not a.excluded and a.last_analysis),
                       "by_class": dict(by_class),
                       "markets": sorted({f"{a.asset_class}" for a in self.assets.values()})},
            "positions_open": acct["open_positions"], "account": acct,
            "entries_suspended": entries_suspended(self.broker, time.time()),
            "tiers": self.selector.summary()["counts"],
            "latency": {k: {"avg": avg(v), "p95": p95(v), "n": len(v)} for k, v in self.latency.items()},
            "stream": {"connected": self.stream.connected, "messages": self.stream.messages,
                       "last_msg_age_s": (time.time() - self.stream.last_msg_at) if self.stream.last_msg_at else None},
            "sources": self.sources, "errors": list(self.errors)[-10:],
            "calibration": {k: v for k, v in self.calibration.items() if k not in ("trades", "strategies", "pairs")},
            "research_only": RESEARCH_ONLY,
            "data_coverage": {
                "crypto": "Coinbase Exchange: live WebSocket quotes, 15m/1h/1d candles",
                "commodity": "Gold via PAXG (Coinbase, live) and XAUT (Kraken, polled ~10 s) — tokenised gold, not futures",
                "fx": "Kraken spot FX (polled ~10 s), 720-candle history limit per timeframe",
                "memecoin": "GeckoTerminal Solana pools (5m candles, DEX price polled); no holder data",
                "stocks": "NOT AVAILABLE — needs a market-data key (e.g. free Tiingo); declined, so stocks are not traded",
                "indices": "S&P 500 / Nasdaq / Dow from FRED are daily and delayed — shown on Global Markets, not traded",
            },
        }

    async def _broadcast_status(self) -> None:
        while True:
            try:
                st = self.status()
                for q in list(self.subscribers):
                    if q.qsize() < 1000:
                        q.put_nowait({"type": "status", "status": st})
            except Exception as e:
                log.warning("status broadcast failed: %s", e)
            await asyncio.sleep(1.0)

    def _source(self, name: str, ok: bool, detail: str = "") -> None:
        self.sources[name] = {"ok": ok, "detail": detail, "at": time.time()}

    # ------------------------------------------------------------------ lifecycle
    async def boot(self) -> None:
        """Server start: resume after a restart. Positions and resting orders come back from the database."""
        self.tasks.append(asyncio.create_task(self._broadcast_status(), name="status"))
        self.broker.ensure_account()
        opens, orders = self.broker.positions("open"), self.broker.open_orders()
        if opens or orders:
            await self.emit(f"Recovered from database: {len(opens)} open simulated position(s), {len(orders)} resting order(s)", "system")
        flag = db.kv_get("agent_mode") or ("trading" if db.kv_get("agent_active") else "off")
        if flag == "trading":
            await self.emit("Resuming autonomous trading after server restart", "system")
            await self.activate()
        elif opens:
            await self._start_managing("server restarted while inactive")

    async def activate(self) -> dict:
        async with self._lock:
            if self.active:
                return self.status()
            self.active, self.mode = True, "trading"
            self.desk.entries_enabled = True
            db.kv_set("agent_mode", "trading")
            self.started_at = time.time()
            self.set_state("initialising", "Initialising market-data connections")
            await self.emit("ACTIVATE received — autonomous paper trading started (simulated capital only)", "system")
            self._cancel_workers()
            self.stream.start()
            for name, coro in (("universe", self._universe_loop()), ("candles", self._candle_loop()),
                               ("news", self._news_loop()), ("calibration", self._calibration_loop()),
                               ("equity", self._equity_loop()), ("polled-quotes", self._polled_quote_loop()),
                               ("reports", self._report_loop())):
                self.tasks.append(asyncio.create_task(self._guard(name, coro), name=name))
            return self.status()

    async def deactivate(self) -> dict:
        async with self._lock:
            st = get_settings()
            self.desk.entries_enabled = False
            self.active = False
            for o in self.broker.open_orders(purpose="entry"):
                self.broker.cancel_order(o["id"], "agent deactivated")
            await self.emit("DEACTIVATE received — no new entries; pending entry orders cancelled; portfolio preserved", "system")
            if st["deactivate_mode"] == "flatten":
                await self.desk.flatten(await self._fresh_quotes(), "deactivate_flatten", "agent deactivated (flatten mode)")
            if self.broker.positions("open"):
                await self._start_managing("agent deactivated")
            else:
                self._cancel_workers()
                await self.stream.stop()
                self.mode = "off"
                db.kv_set("agent_mode", "off")
                self.set_state("stopped", None)
                await self.emit("Agent stopped. Final state recorded.", "system", data=self.broker.state())
            return self.status()

    async def emergency_stop(self) -> dict:
        async with self._lock:
            self.desk.entries_enabled = False
            self.active = False
            await self.emit("EMERGENCY STOP — cancelling all orders and closing all simulated positions at market", "system", "warn")
            quotes = await self._fresh_quotes()
            for o in self.broker.open_orders():
                if o["purpose"] == "entry":
                    self.broker.cancel_order(o["id"], "emergency stop")
            closed = await self.desk.flatten(quotes, "emergency_stop", "emergency stop")
            self._cancel_workers()
            left = self.broker.positions("open")
            if left:   # no price available: keep protecting them with their stop orders
                self.mode = "emergency_stopped"
                await self._start_managing("emergency stop: positions without a live price stay protected", keep_mode=True)
            else:
                await self.stream.stop()
            self.mode = "emergency_stopped"
            db.kv_set("agent_mode", "emergency_stopped")
            self.set_state("stopped", "Emergency stop")
            await self.emit(f"Emergency stop complete: {len(closed)} position(s) closed, {len(left)} could not be priced. Final equity "
                            f"${self.broker.state()['equity']:,.2f}", "system", "warn")
            return self.status()

    async def _start_managing(self, why: str, keep_mode: bool = False) -> None:
        self._cancel_workers()
        if not keep_mode:
            self.mode = "managing"
            db.kv_set("agent_mode", "managing")
        self.desk.entries_enabled = False
        if not self.assets:
            await self._rebuild_for_positions()
        await self.stream.set_products({a.symbol for a in self.assets.values() if a.source == "coinbase"})
        self.stream.start()
        for name, coro in (("candles", self._candle_loop(manage_only=True)), ("polled-quotes", self._polled_quote_loop()),
                           ("equity", self._equity_loop()), ("reports", self._report_loop())):
            self.tasks.append(asyncio.create_task(self._guard(name, coro), name=name))
        n = len(self.broker.positions("open"))
        self.set_state("monitoring", f"Managing {n} open position(s); no new entries ({why})")
        await self.emit(f"Managing {n} open simulated position(s) with their stops, targets and exit rules — no new entries ({why})", "system")

    async def _rebuild_for_positions(self) -> None:
        for p in self.broker.positions("open"):
            src = "coinbase" if "-" in p["asset"] else ("kraken" if p["asset_class"] in ("fx", "commodity") else "geckoterminal")
            a = AssetState(p["asset"], p["display"] or p["asset"], p["asset_class"], src, [p["tf"]])
            self.assets[p["asset"]] = a
            await self._load_series(a, p["tf"])

    def _cancel_workers(self) -> None:
        keep = [t for t in self.tasks if t.get_name() == "status"]
        for t in self.tasks:
            if t.get_name() != "status":
                t.cancel()
        self.tasks = keep

    async def shutdown(self) -> None:
        for t in self.tasks:
            t.cancel()
        await self.stream.stop()

    async def _guard(self, name: str, coro) -> None:
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.set_state("error", f"{name} worker failed")
            await self.emit(f"{name} worker crashed: {type(e).__name__}: {e}", "error", "error")
            log.exception("worker %s crashed", name)

    # ------------------------------------------------------------------ universe
    async def _universe_loop(self) -> None:
        while self.active:
            await self.refresh_universe()
            await asyncio.sleep(1800)

    async def refresh_universe(self) -> None:
        self.set_state("scanning", "Building asset universe (crypto, gold, forex, Solana pools)")
        t0 = time.perf_counter()
        keep: set[str] = set()
        try:
            products = {p["base_currency"]: p["id"] for p in await feeds.coinbase_products()}
            self._source("coinbase", True, f"{len(products)} USD products")
            mk = await crypto_provider.coingecko_markets(250)
            self._source("coingecko", True, f"{len(mk['coins'])} coins")
            ranked = sorted(((c.get("total_volume") or 0, (c["symbol"] or "").upper(), c) for c in mk["coins"]
                             if (c["symbol"] or "").upper() not in STABLES and (c["symbol"] or "").upper() in products), reverse=True)
            for vol, sym, coin in ranked[:20]:
                pid = products[sym]
                keep.add(pid)
                a = self.assets.get(pid) or AssetState(pid, f"{sym}/USD", "crypto", "coinbase", CLASS_CFG["crypto"]["tfs"])
                a.volume_24h_usd, a.market_cap_usd = vol, coin.get("market_cap")
                a.names = [sym, coin.get("name") or sym]
                self.assets[pid] = a
            if "PAXG" in products:
                keep.add("PAXG-USD")
                a = self.assets.get("PAXG-USD") or AssetState("PAXG-USD", "PAXG/USD (gold)", "commodity", "coinbase", CLASS_CFG["commodity"]["tfs"])
                a.names = ["gold", "PAXG"]
                paxg = next((c for c in mk["coins"] if (c["symbol"] or "").upper() == "PAXG"), None)
                a.volume_24h_usd = (paxg or {}).get("total_volume")
                self.assets["PAXG-USD"] = a
        except UpstreamError as e:
            self._source(e.source, False, e.message)
            await self.emit(f"Crypto universe unavailable: {e}", "data", "error")
        try:
            kp = await feeds.kraken_pairs()
            self._source("kraken", True, f"{len(kp)} pairs")
            for ws in FX_PAIRS + ["XAUT/USD"]:
                if ws not in kp:
                    continue
                alt = kp[ws]["altname"]
                keep.add(alt)
                cls = "commodity" if ws.startswith("XAUT") else "fx"
                a = self.assets.get(alt) or AssetState(alt, ws + (" (gold)" if cls == "commodity" else ""), cls, "kraken", CLASS_CFG[cls]["tfs"])
                a.names = [ws.split("/")[0]] if cls == "fx" else ["gold", "XAUT"]
                self.assets[alt] = a
        except UpstreamError as e:
            self._source("kraken", False, e.message)
        try:
            pools = await feeds.gecko_pools("trending_pools")
            self._source("geckoterminal", True, f"{len(pools)} trending Solana pools")
            pools = sorted([p for p in pools if (p["liquidity_usd"] or 0) >= 100_000], key=lambda p: -(p["volume_usd"].get("h24") or 0))
            for p in pools[:6]:
                keep.add(p["pool"])
                a = self.assets.get(p["pool"]) or AssetState(p["pool"], p["name"] or p["pool"][:6], "memecoin", "geckoterminal", ["5m"])
                a.liquidity_usd, a.market_cap_usd = p["liquidity_usd"], p["market_cap_usd"] or p["fdv_usd"]
                a.volume_24h_usd = p["volume_usd"].get("h24")
                a.last = p["price_usd"] or a.last
                a.names = [(p["name"] or "").split(" / ")[0]]
                a.meta = {"token_address": p["token_address"], "dex": p["dex"], "pool": p["pool"],
                          "is_pump_fun": bool((p["token_address"] or "").endswith("pump")) or "pump" in (p["dex"] or ""),
                          "pool_created_at": p["created_at"], "holder_data": "not available from free sources"}
                self.assets[p["pool"]] = a
        except UpstreamError as e:
            self._source("geckoterminal", False, e.message)
        held = {p["asset"] for p in self.broker.positions("open")}
        for k in list(self.assets):
            if k not in keep and k not in held:
                del self.assets[k]
        await self.stream.set_products({a.symbol for a in self.assets.values() if a.source == "coinbase"})
        self.counters["universe_refreshes"] += 1
        cls = collections.Counter(a.asset_class for a in self.assets.values())
        await self.emit(f"Universe: {cls.get('crypto', 0)} crypto, {cls.get('commodity', 0)} gold, {cls.get('fx', 0)} forex, "
                        f"{cls.get('memecoin', 0)} Solana pools × their timeframes. Stocks unavailable (no data key).", "scan",
                        latency_ms=(time.perf_counter() - t0) * 1000)
        await self._load_history()

    async def _load_series(self, a: AssetState, tf: str) -> None:
        cs = await self._fetch(a, tf, history=True)
        a.series[tf] = Series.from_candles(a.symbol, a.asset_class, tf, a.source, cs,
                                           volume_unit="usd" if a.asset_class == "memecoin" else "base")
        a.next_close[tf] = self._next_close(tf)

    async def _load_history(self) -> None:
        for a in list(self.assets.values()):
            for tf in a.tfs:
                if tf in a.series and len(a.series[tf]) >= 200:
                    continue
                if not self.active:
                    return
                self.set_state("scanning", f"Loading {tf} candle history", a.display)
                t0 = time.perf_counter()
                try:
                    await self._load_series(a, tf)
                    s = a.series[tf]
                    await self.emit(f"{a.display} {tf}: {len(s)} closed candles loaded ({s.gaps} missing, not filled)",
                                    "data", asset=a.display, latency_ms=(time.perf_counter() - t0) * 1000)
                    await self.analyse_asset(a, tf, initial=True)
                except UpstreamError as e:
                    self._source(e.source, False, e.message)
                    await self.emit(f"{a.display} {tf}: history unavailable ({e.message})", "data", "warn", asset=a.display)

    async def _fetch(self, a: AssetState, tf: str, history: bool = False) -> list[Candle]:
        hb = CLASS_CFG[a.asset_class]["history_bars"][tf]
        if a.source == "coinbase":
            return await (feeds.coinbase_history(a.symbol, tf, hb) if history else feeds.coinbase_candles(a.symbol, tf, ttl=1))
        if a.source == "kraken":
            return await feeds.kraken_candles(a.symbol, tf)
        return await feeds.gecko_pool_candles(a.symbol, tf, hb if history else 10)

    @staticmethod
    def _next_close(tf: str) -> int:
        step = TF_SECONDS[tf]
        return (int(time.time()) // step + 1) * step

    # ------------------------------------------------------------------ candle-close events
    async def _candle_loop(self, manage_only: bool = False) -> None:
        while True:
            now = time.time()
            held = {p["asset"]: p["tf"] for p in self.broker.positions("open")}
            due = [(a, tf) for a in list(self.assets.values()) for tf in a.tfs
                   if tf in a.series and a.next_close.get(tf) and now >= a.next_close[tf] + 3
                   and (not manage_only or held.get(a.symbol) == tf)]
            if due:
                self.set_state("scanning", f"{len(due)} candle(s) closed")

            async def fetch(a: AssetState, tf: str):
                try:
                    return a, tf, await self._fetch(a, tf), None
                except UpstreamError as e:
                    return a, tf, None, e
            results = list(await asyncio.gather(*[fetch(a, tf) for a, tf in due if a.source == "coinbase"]))
            for a, tf in due:
                if a.source != "coinbase":
                    results.append(await fetch(a, tf))
            for a, tf, new, err in sorted(results, key=lambda r: -r[0].rank_score):
                close_t = a.next_close[tf]
                a.next_close[tf] = self._next_close(tf)
                if err is not None:
                    self._source(err.source, False, err.message)
                    await self.emit(f"{a.display} {tf}: candle update failed ({err.message}); retry next bar", "data", "warn", asset=a.display)
                    continue
                if a.series[tf].merge(new) == 0:
                    continue
                await self.analyse_asset(a, tf, t_event=close_t)
                self.latency["candle_to_decision_ms"].append((time.time() - close_t) * 1000)
            if not due:
                self.set_state("monitoring" if self.broker.positions("open") else ("running" if self.active else "monitoring"),
                               "Waiting for the next candle close; live prices update positions continuously")
            await asyncio.sleep(2)

    # ------------------------------------------------------------------ analysis → desk
    async def analyse_asset(self, a: AssetState, tf: str, initial: bool = False, t_event: float | None = None) -> None:
        s = a.series.get(tf)
        if s is None or len(s) < 80:
            a.excluded = a.excluded or "insufficient candle history"
            return
        self.set_state("analysing", f"Analysing {tf} candle", a.display)
        t0 = time.perf_counter()
        w = s.tail(WINDOW)
        an = analyse(w)
        t = len(w) - 1
        ms = (time.perf_counter() - t0) * 1000
        self.latency["analysis_ms"].append(ms)
        self.counters["candles_analysed"] += 1
        if tf == a.tf:
            a.last_analysis = {"trend": an.trend, "structure": market_structure_label(an.swings),
                               "atr_pct": None if math.isnan(an.atr[t]) else float(100 * an.atr[t] / w.c[t]),
                               "rsi": None if math.isnan(an.rsi[t]) else float(an.rsi[t]),
                               "macd_hist": None if math.isnan(an.macd_hist[t]) else float(an.macd_hist[t]),
                               "rel_volume": None if math.isnan(an.rel_volume[t]) else float(an.rel_volume[t]),
                               "pools": an.pools}
            a.analysed_at = time.time()
            self.rank(a, an, w)
            if not a.last and len(w):
                a.last = float(w.c[-1])
        if initial or self.mode == "off":
            return
        if a.asset_class == "memecoin" and a.last:
            q = Quote(a.last, a.last, a.last, time.time())      # DEX: price only; impact modelled as slippage
        else:
            q = a.quote()
        if a.source == "kraken" and q is None:
            q = await self._kraken_quote(a)
        self.set_state("evaluating", f"Deciding on {tf} close", a.display)
        closes = {k: v.series[v.tf].c[-200:] for k, v in self.assets.items() if v.tf in v.series}
        news = [n for n in self.news if a.display in n.get("assets", [])]
        res = await self.desk.on_candle(a.ctx(), w, an, q, closes, news)
        since = f"; decided {(time.time() - t_event):.1f}s after close" if t_event else ""
        await self.emit(f"{a.display} {tf} analysed: trend {an.trend}, RSI {a.last_analysis['rsi'] or 0:.0f} → "
                        f"{res['signals']} setup(s), {res['accepted']} order(s), {res['holds']} hold, {res['exits']} exit{since}",
                        "analysis", asset=a.display, latency_ms=ms)

    def rank(self, a: AssetState, an, s: Series) -> None:
        t = len(s) - 1
        atr_pct = float(100 * an.atr[t] / s.c[t]) if not math.isnan(an.atr[t]) else 0.0
        if a.asset_class == "memecoin":
            liq = min(1.0, max(0.0, (math.log10(max(a.liquidity_usd or 1, 1)) - 5) / 2))
        elif a.asset_class == "fx":
            liq = 0.9                                     # major FX pairs: deep markets (Kraken venue volume is not market volume)
        else:
            liq = min(1.0, max(0.0, (math.log10(max(a.volume_24h_usd or 1, 1)) - 6) / 4))
        rv = an.rel_volume[t]
        sp = a.spread_pct
        max_sp = {"crypto": 0.25, "commodity": 0.25, "fx": 0.05, "memecoin": 1.0}[a.asset_class]
        parts = {"liquidity": liq, "relative_volume": 0.0 if math.isnan(rv) else min(1.0, rv / 3),
                 "spread": 0.5 if sp is None else max(0.0, 1 - sp / max_sp),
                 "volatility": min(1.0, atr_pct / 1.0), "momentum": min(1.0, abs(s.c[t] / s.c[max(0, t - 12)] - 1) / 0.03),
                 "structure": 1.0 if any(d.i >= t - 2 for d in an.recent(12, len(s)) if d.kind in ("liquidity_sweep", "bos", "choch")) else 0.0,
                 "news": min(1.0, a.news_count / 3),
                 "data_quality": max(0.0, 1.0 - min(0.5, (s.gaps or 0) / max(len(s), 1) * 10) - (0.3 if a.asset_class == "memecoin" else 0.0))}
        w = {"liquidity": 0.25, "relative_volume": 0.15, "spread": 0.15, "volatility": 0.1, "momentum": 0.1, "structure": 0.1, "news": 0.05, "data_quality": 0.1}
        a.rank_parts = {k: round(v, 3) for k, v in parts.items()}
        a.rank_score = 100 * sum(parts[k] * w[k] for k in w) * (0.4 + 0.6 * liq) * max(0.2, parts["data_quality"])
        ex = None
        if a.asset_class in ("crypto", "commodity") and (a.volume_24h_usd or 0) < 2_000_000:
            ex = "24h volume below $2M"
        if a.asset_class == "memecoin" and (a.liquidity_usd or 0) < 100_000:
            ex = "pool liquidity below $100k"
        if sp is not None and sp > max_sp:
            ex = f"spread {sp:.3f}% above {max_sp}%"
        a.excluded = ex

    # ------------------------------------------------------------------ live prices
    async def _on_tick(self, product: str, tick: dict) -> None:
        t0 = time.perf_counter()
        a = self.assets.get(product)
        if a is not None:
            a.bid, a.ask, a.last, a.quote_ts = tick["bid"], tick["ask"], tick["price"], tick["recv"]
        if self._has_orders(product):
            await self.desk.on_quote(product, Quote(tick["bid"], tick["ask"], tick["price"], tick["recv"]))
        self.latency["tick_ms"].append((time.perf_counter() - t0) * 1000)

    _open_cache: tuple[float, set] = (0.0, set())

    def _has_orders(self, asset: str) -> bool:
        ts, assets = self._open_cache
        if time.time() - ts > 1.0:
            assets = {p["asset"] for p in self.broker.positions("open")} | {o["asset"] for o in self.broker.open_orders()}
            self._open_cache = (time.time(), assets)
        return asset in assets

    async def _on_stream_status(self, status: str, message: str) -> None:
        self._source("coinbase-ws", status == "connected", message)
        await self.emit(message, "data", "info" if status == "connected" else "error")

    async def _kraken_quote(self, a: AssetState) -> Quote | None:
        try:
            tk = (await feeds.kraken_tickers([a.symbol])).get(a.symbol)
        except UpstreamError as e:
            self._source("kraken", False, e.message)
            return None
        if not tk:
            return None
        a.bid, a.ask, a.last, a.quote_ts = tk["bid"], tk["ask"], tk["price"], time.time()
        return a.quote()

    async def _polled_quote_loop(self) -> None:
        """Kraken FX/gold every ~10 s (one batched request); DEX pools with orders every ~30 s."""
        n = 0
        while True:
            kr = [a for a in self.assets.values() if a.source == "kraken"]
            if kr:
                try:
                    tks = await feeds.kraken_tickers([a.symbol for a in kr])
                    for a in kr:
                        tk = tks.get(a.symbol)
                        if tk:
                            a.bid, a.ask, a.last, a.quote_ts = tk["bid"], tk["ask"], tk["price"], time.time()
                            if self._has_orders(a.symbol):
                                await self.desk.on_quote(a.symbol, a.quote())
                    self._source("kraken", True, f"{len(tks)} quotes")
                except UpstreamError as e:
                    self._source("kraken", False, e.message)
            if n % 3 == 0:
                for a in [x for x in self.assets.values() if x.source == "geckoterminal" and self._has_orders(x.symbol)]:
                    try:
                        cs = await feeds.gecko_pool_candles(a.symbol, "1m", 2)
                        if cs:
                            px = cs[-1].c
                            a.last, a.quote_ts = px, time.time()
                            await self.desk.on_quote(a.symbol, Quote(px, px, px, time.time()))
                    except UpstreamError as e:
                        self._source(e.source, False, e.message)
            n += 1
            await asyncio.sleep(10)

    async def _fresh_quotes(self) -> dict[str, Quote]:
        out = {}
        for p in self.broker.positions("open") + [{"asset": o["asset"], "asset_class": o["asset_class"]} for o in self.broker.open_orders()]:
            sym = p["asset"]
            if sym in out:
                continue
            a = self.assets.get(sym)
            q = a.quote() if a else None
            try:
                if q is None and "-" in sym:
                    tk = await feeds.coinbase_ticker(sym)
                    q = Quote(tk["bid"], tk["ask"], tk["price"], time.time())
                elif q is None and p["asset_class"] in ("fx", "commodity"):
                    tk = (await feeds.kraken_tickers([sym])).get(sym)
                    q = Quote(tk["bid"], tk["ask"], tk["price"], time.time()) if tk else None
                elif q is None:
                    cs = await feeds.gecko_pool_candles(sym, "1m", 2)
                    q = Quote(cs[-1].c, cs[-1].c, cs[-1].c, time.time()) if cs else None
            except (UpstreamError, IndexError, KeyError):
                q = None
            if q:
                out[sym] = q
        return out

    # ------------------------------------------------------------------ news
    async def _news_loop(self) -> None:
        while self.active:
            t0 = time.perf_counter()
            items = []
            for name, make in (("fed", lambda: news_provider.rss("fed")), ("ecb", lambda: news_provider.rss("ecb")),
                               ("gdelt", lambda: news_provider.gdelt("(bitcoin OR ethereum OR crypto OR solana OR gold OR forex OR dollar) sourcelang:english", 75))):
                try:
                    r = await make()
                    items += r["items"]
                    self._source(name, True, f"{len(r['items'])} items")
                except UpstreamError as e:
                    self._source(name, False, e.message)
            items = news_provider.dedupe(items)
            for a in self.assets.values():
                a.news_count = 0
            linked = 0
            for it in items:
                it["assets"] = []
                for a in self.assets.values():
                    for nm in a.names:
                        if len(nm) >= 3 and nm.upper() not in ("THE", "ONE", "NEAR", "SUN") and re.search(rf"\b{re.escape(nm)}\b", it["title"], re.I):
                            it["assets"].append(a.display)
                            a.news_count += 1
                            break
                linked += bool(it["assets"])
            self.news = items
            await self.emit(f"News checked: {len(items)} items, {linked} linked to traded assets (media headlines unverified)", "news",
                            latency_ms=(time.perf_counter() - t0) * 1000)
            await asyncio.sleep(300)

    # ------------------------------------------------------------------ evidence calibration
    async def _calibration_loop(self) -> None:
        waited = 0
        while self.active:
            total = sum(len(a.tfs) for a in self.assets.values())
            loaded = sum(len(a.series) for a in self.assets.values())
            if total and (loaded >= total or (waited >= 240 and loaded >= 0.6 * total)):
                await self.calibrate()
                await asyncio.sleep(6 * 3600)
                waited = 0
            else:
                await asyncio.sleep(10)
                waited += 10

    async def calibrate(self) -> None:
        self.set_state("analysing", "Evidence calibration (walk-forward backtests of every strategy × market × timeframe)")
        await self.emit("Evidence calibration started: backtesting every strategy on every market and timeframe with live execution rules", "validation")
        t0 = time.perf_counter()
        st = get_settings()
        trades = []
        for a in list(self.assets.values()):
            for tf, s in list(a.series.items()):
                if len(s) < 200:
                    continue
                tr, _ = await asyncio.to_thread(backtest.run, s, STRATEGIES, MAX_HOLD.get(tf, 48), 1000.0, a.liquidity_usd,
                                                st["targets_pct"], 60, None, st["fees"].get(a.asset_class, st["fees"]["crypto"]),
                                                st["entry_order"], st["entry_limit_bars"])
                trades += tr
                await asyncio.sleep(0)
        self.selector.fit(trades)
        self.model = await asyncio.to_thread(ProbabilityModel, trades)
        changes = self.selector.refresh(time.time())
        summary = {}
        for c, v in self.selector.backtest.items():
            strat, cls, tf, d = c.split("|")
            g = [x for x in trades if x.combo == c]
            summary[c] = {**backtest.summarise(g), "strategy": strat, "asset_class": cls, "tf": tf, "direction": d,
                          "tier": self.selector.tiers.get(c, {}).get("tier"), "reason": self.selector.tiers.get(c, {}).get("reason")}
        closes = {a.display: a.series[a.tf].c for a in self.assets.values() if a.asset_class == "crypto" and a.tf in a.series}
        times = {a.display: a.series[a.tf].t for a in self.assets.values() if a.asset_class == "crypto" and a.tf in a.series}
        top = sorted(closes, key=lambda k: -max((x.volume_24h_usd or 0) for x in self.assets.values() if x.display == k))[:10]
        self.pairs = await asyncio.to_thread(pairs.scan, {k: closes[k] for k in top}, {k: times[k] for k in top})
        self.calibration = {"updated": time.time(), "trades_total": len(trades), "combos": len(summary),
                            "tiers": self.selector.summary()["counts"], "combo_stats": summary,
                            "selection_validation": self.selector.validation, "pairs": self.pairs[:15],
                            "note": "Backtest evidence uses the same execution rules as the paper broker (limit entries, maker/taker fees, "
                                    "stop-first fills). Live paper results are tracked separately."}
        db.kv_set("calibration_summary", self.calibration)
        self.counters["calibrations"] += 1
        c = self.selector.summary()["counts"]
        await self.emit(f"Evidence calibration complete: {len(trades)} simulated historical setups → {c.get('active', 0)} active, "
                        f"{c.get('exploration', 0)} exploration, {c.get('disabled', 0)} disabled strategy combinations", "validation",
                        latency_ms=(time.perf_counter() - t0) * 1000)
        for ch in changes:
            await self.emit(f"Strategy selection updated: {ch['combo']} {ch['old_tier']} → {ch['new_tier']}", "learning", data=db.jsonable(ch))

    # ------------------------------------------------------------------ equity & reports
    async def _equity_loop(self) -> None:
        while True:
            try:
                self.broker.snapshot_equity()
            except Exception as e:
                log.warning("equity snapshot failed: %s", e)
            await asyncio.sleep(60)

    async def _report_loop(self) -> None:
        """Daily report after each UTC midnight (+ weekly on Mondays, monthly on the 1st)."""
        while True:
            now = time.time()
            last = db.kv_get("last_daily_report_day")
            day = int(now // 86400)
            if last is None:
                db.kv_set("last_daily_report_day", day)
            elif day > last:
                events = [{"title": n["title"], "source": n["source"], "url": n["url"]} for n in self.news
                          if n.get("primary_source") or n.get("assets")][:10]
                prev = (day - 1) * 86400 + 1
                rep = reports.period_report(ACCOUNT, "daily", prev, events)
                db.kv_set("last_daily_report_day", day)
                await self.emit(f"Daily report {rep['label']}: {rep['trades_closed']} trades closed, net {rep['realized_pnl']:+.2f} USD, "
                                f"portfolio {rep['total_return_pct']:+.2f}%", "report")
                d = time.gmtime(now)
                if d.tm_wday == 0:
                    reports.period_report(ACCOUNT, "weekly", prev, events)
                if d.tm_mday == 1:
                    reports.period_report(ACCOUNT, "monthly", prev, events)
            await asyncio.sleep(600)


agent = Agent()
