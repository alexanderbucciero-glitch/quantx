"""QUANTX AI agent: an event-driven scanner, analyser and paper trader.

Event flow
  Coinbase WebSocket tick ──► update quote ──► check simulated positions (stops/targets)      [ms]
  candle-close scheduler  ──► fetch only the new candle(s) for assets whose bar just closed
                          ──► re-analyse that asset only ──► run strategies ──► gates ──► report
                          ──► (accepted) open simulated position
  universe refresh (30 min), news refresh (5 min), calibration (on start + every 6 h),
  equity snapshot (60 s), memecoin pool refresh (90 s)

Every log line is emitted from the code path that did the work, with the measured latency.
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
from ..core import db
from ..core.http import UpstreamError
from ..market import feeds
from ..market.candles import TF_SECONDS, Candle, Series
from ..paper import engine as paper
from ..providers import crypto as crypto_provider
from ..providers import news as news_provider
from ..quant import backtest, pairs
from ..quant.probability import ProbabilityModel
from ..quant.targets import PlanRejection
from ..strategies.library import RESEARCH_ONLY, STRATEGIES
from . import report as report_builder

log = logging.getLogger("quantx.agent")

STABLES = {"USDT", "USDC", "DAI", "USDS", "USDE", "FDUSD", "TUSD", "PYUSD", "USD1", "EURC", "GUSD", "USDP", "RLUSD"}
CLASS_CFG = {
    "crypto":   {"tf": "15m", "max_hold": 64, "history_bars": 2400, "window": 600},
    "memecoin": {"tf": "5m", "max_hold": 48, "history_bars": 1000, "window": 600},
}
STATES = ("offline", "initialising", "running", "scanning", "analysing", "evaluating", "monitoring", "error", "stopped")


@dataclass
class AssetState:
    symbol: str                      # BTC-USD, or pool address for memecoins
    display: str
    asset_class: str
    tf: str
    source: str
    series: Series | None = None
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
    next_close: int = 0
    names: list[str] = field(default_factory=list)
    news_count: int = 0
    meta: dict = field(default_factory=dict)

    @property
    def spread_pct(self) -> float | None:
        if self.bid and self.ask and self.ask >= self.bid > 0:
            return 100 * (self.ask - self.bid) / ((self.ask + self.bid) / 2)
        return None

    def public(self) -> dict:
        return {"symbol": self.symbol, "display": self.display, "asset_class": self.asset_class, "tf": self.tf,
                "price": self.last, "bid": self.bid, "ask": self.ask, "spread_pct": self.spread_pct,
                "volume_24h_usd": self.volume_24h_usd, "liquidity_usd": self.liquidity_usd,
                "market_cap_usd": self.market_cap_usd, "rank_score": round(self.rank_score, 1),
                "rank_parts": self.rank_parts, "excluded": self.excluded, "analysis": self.last_analysis,
                "analysed_at": self.analysed_at, "bars": len(self.series) if self.series else 0,
                "gaps": self.series.gaps if self.series else None, "news_mentions_24h": self.news_count,
                "quote_age_s": (time.time() - self.quote_ts) if self.quote_ts else None, **self.meta}


class Agent:
    def __init__(self) -> None:
        self.state = "offline"
        self.active = False
        self.current = {"task": None, "asset": None, "strategy": None}
        self.assets: dict[str, AssetState] = {}
        self.logbuf: collections.deque = collections.deque(maxlen=600)
        self.subscribers: set[asyncio.Queue] = set()
        self.tasks: list[asyncio.Task] = []
        self.stream = feeds.CoinbaseTickerStream(self._on_tick, self._on_stream_status)
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
                   strategy: str | None = None, latency_ms: float | None = None, data: dict | None = None) -> None:
        ev = {"ts": time.time(), "level": level, "kind": kind, "asset": asset, "strategy": strategy,
              "message": message, "latency_ms": None if latency_ms is None else round(latency_ms, 1), "data": data}
        self.logbuf.append(ev)
        if level == "error":
            self.errors.append(ev)
        try:
            with db.engine().begin() as c:
                c.execute(db.insert(db.activity).values(**ev))
        except Exception as e:  # logging must never kill the agent
            log.warning("activity persist failed: %s", e)
        for q in list(self.subscribers):
            if q.qsize() < 1000:
                q.put_nowait({"type": "log", "event": ev})

    def set_state(self, state: str, task: str | None = None, asset: str | None = None, strategy: str | None = None) -> None:
        self.state = state
        self.current = {"task": task, "asset": asset, "strategy": strategy}

    def status(self) -> dict:
        def avg(d):
            return round(sum(d) / len(d), 2) if d else None

        def p95(d):
            return round(float(np.percentile(list(d), 95)), 2) if d else None
        return {
            "state": self.state, "active": self.active, "current": self.current, "started_at": self.started_at,
            "counters": dict(self.counters),
            "assets": {"total": len(self.assets), "eligible": sum(1 for a in self.assets.values() if not a.excluded and a.last_analysis),
                       "by_class": dict(collections.Counter(a.asset_class for a in self.assets.values()))},
            "positions_open": len(paper.open_positions()),
            "latency": {k: {"avg": avg(v), "p95": p95(v), "n": len(v)} for k, v in self.latency.items()},
            "stream": {"connected": self.stream.connected, "messages": self.stream.messages,
                       "last_msg_age_s": (time.time() - self.stream.last_msg_at) if self.stream.last_msg_at else None},
            "sources": self.sources, "errors": list(self.errors)[-10:],
            "calibration": {k: v for k, v in self.calibration.items() if k != "trades"},
            "research_only": RESEARCH_ONLY,
        }

    async def _broadcast_status(self) -> None:
        while True:
            st = self.status()
            for q in list(self.subscribers):
                if q.qsize() < 1000:
                    q.put_nowait({"type": "status", "status": st})
            await asyncio.sleep(1.0)

    def _source(self, name: str, ok: bool, detail: str = "") -> None:
        self.sources[name] = {"ok": ok, "detail": detail, "at": time.time()}

    # ------------------------------------------------------------------ lifecycle
    async def boot(self) -> None:
        """Called at server start: start the status broadcaster; resume if the agent was active."""
        self.tasks.append(asyncio.create_task(self._broadcast_status(), name="status"))
        if db.kv_get("agent_active"):
            await self.emit("Resuming agent after server restart", "system")
            await self.activate()
        elif paper.open_positions():
            await self._start_position_monitor_only()

    async def activate(self) -> dict:
        async with self._lock:
            if self.active:
                return self.status()
            self.active = True
            db.kv_set("agent_active", True)
            self.started_at = time.time()
            self.set_state("initialising", "Initialising market-data connections")
            await self.emit("ACTIVATE AI received — initialising market-data connections", "system")
            self._cancel_workers()
            self.stream.start()
            for name, coro in (("universe", self._universe_loop()), ("candles", self._candle_loop()),
                               ("news", self._news_loop()), ("calibration", self._calibration_loop()),
                               ("equity", self._equity_loop()), ("memecoin-quotes", self._memecoin_quote_loop())):
                self.tasks.append(asyncio.create_task(self._guard(name, coro), name=name))
            return self.status()

    async def deactivate(self) -> dict:
        async with self._lock:
            self.active = False
            db.kv_set("agent_active", False)
            self._cancel_workers()
            await self.emit("Agent deactivated — no new research jobs or simulated positions. History preserved.", "system")
            if paper.open_positions():
                await self._start_position_monitor_only()
            else:
                await self.stream.stop()
                self.set_state("stopped", None)
            return self.status()

    async def _start_position_monitor_only(self) -> None:
        opens = paper.open_positions()
        await self.stream.set_products({p["asset"] for p in opens if p["asset_class"] == "crypto"})
        self.stream.start()
        self.tasks.append(asyncio.create_task(self._guard("memecoin-quotes", self._memecoin_quote_loop()), name="memecoin-quotes"))
        self.tasks.append(asyncio.create_task(self._guard("equity", self._equity_loop()), name="equity"))
        self.set_state("monitoring", f"Monitoring {len(opens)} open simulated position(s) only")
        await self.emit(f"Monitoring {len(opens)} open simulated position(s); no new positions will be opened", "system")

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
        self.set_state("scanning", "Building asset universe")
        t0 = time.perf_counter()
        try:
            products = {p["base_currency"]: p["id"] for p in await feeds.coinbase_products()}
            self._source("coinbase", True, f"{len(products)} USD products")
            mk = await crypto_provider.coingecko_markets(250)
            self._source("coingecko", True, f"{len(mk['coins'])} coins")
        except UpstreamError as e:
            self._source(e.source, False, e.message)
            await self.emit(f"Universe refresh failed: {e}", "error", "error")
            return
        ranked = []
        for coin in mk["coins"]:
            sym = (coin["symbol"] or "").upper()
            if sym in STABLES or sym not in products:
                continue
            ranked.append((coin.get("total_volume") or 0, sym, coin))
        ranked.sort(reverse=True)
        keep = set()
        for vol, sym, coin in ranked[:25]:
            pid = products[sym]
            keep.add(pid)
            a = self.assets.get(pid) or AssetState(pid, f"{sym}/USD", "crypto", CLASS_CFG["crypto"]["tf"], "coinbase")
            a.volume_24h_usd = vol
            a.market_cap_usd = coin.get("market_cap")
            a.names = [sym, coin.get("name") or sym]
            self.assets[pid] = a
        # memecoins: trending Solana pools from GeckoTerminal
        try:
            pools = await feeds.gecko_pools("trending_pools")
            self._source("geckoterminal", True, f"{len(pools)} trending Solana pools")
            pools = [p for p in pools if (p["liquidity_usd"] or 0) >= 50_000]
            pools.sort(key=lambda p: -(p["volume_usd"].get("h24") or 0))
            for p in pools[:8]:
                keep.add(p["pool"])
                a = self.assets.get(p["pool"]) or AssetState(p["pool"], p["name"] or p["pool"][:6], "memecoin",
                                                             CLASS_CFG["memecoin"]["tf"], "geckoterminal")
                a.liquidity_usd, a.market_cap_usd = p["liquidity_usd"], p["market_cap_usd"] or p["fdv_usd"]
                a.volume_24h_usd = p["volume_usd"].get("h24")
                a.last = a.last or p["price_usd"]
                a.names = [(p["name"] or "").split(" / ")[0]]
                a.meta = {"token_address": p["token_address"], "dex": p["dex"], "pool": p["pool"],
                          "is_pump_fun": bool((p["token_address"] or "").endswith("pump")) or "pump" in (p["dex"] or ""),
                          "pool_created_at": p["created_at"], "price_change": p["price_change"],
                          "holder_data": "not available from free sources (needs Helius key)"}
                self.assets[p["pool"]] = a
        except UpstreamError as e:
            self._source("geckoterminal", False, e.message)
            await self.emit(f"Memecoin pool list unavailable: {e.message}", "error", "warn")
        for k in list(self.assets):
            if k not in keep and not paper.open_positions(k):
                del self.assets[k]
        await self.stream.set_products({a.symbol for a in self.assets.values() if a.asset_class == "crypto"}
                                       | {p["asset"] for p in paper.open_positions() if p["asset_class"] == "crypto"})
        self.counters["universe_refreshes"] += 1
        await self.emit(f"Universe: {sum(a.asset_class == 'crypto' for a in self.assets.values())} high-volume crypto pairs, "
                        f"{sum(a.asset_class == 'memecoin' for a in self.assets.values())} Solana pools. "
                        "Stocks: no free intraday source configured (Tiingo key needed).", "scan",
                        latency_ms=(time.perf_counter() - t0) * 1000)
        await self._load_history()

    async def _load_history(self) -> None:
        for a in list(self.assets.values()):
            if a.series is not None and len(a.series) >= 200:
                continue
            if not self.active:
                return
            self.set_state("scanning", "Loading candle history", a.display)
            t0 = time.perf_counter()
            try:
                cs = await self._fetch(a, history=True)
                a.series = Series.from_candles(a.symbol, a.asset_class, a.tf, a.source, cs,
                                               volume_unit="usd" if a.asset_class == "memecoin" else "base")
                a.next_close = self._next_close(a)
                self._persist_candles(a, cs)
                await self.emit(f"{a.display}: {len(a.series)} closed {a.tf} candles loaded ({a.series.gaps} missing bars, not filled)",
                                "data", asset=a.display, latency_ms=(time.perf_counter() - t0) * 1000)
                await self.analyse_asset(a, reason="initial")
            except UpstreamError as e:
                self._source(e.source, False, e.message)
                await self.emit(f"{a.display}: history unavailable ({e.message})", "data", "warn", asset=a.display)

    async def _fetch(self, a: AssetState, history: bool = False) -> list[Candle]:
        cfg = CLASS_CFG[a.asset_class]
        if a.asset_class == "crypto":
            if history:
                return await feeds.coinbase_history(a.symbol, a.tf, cfg["history_bars"])
            return await feeds.coinbase_candles(a.symbol, a.tf, ttl=1)
        return await feeds.gecko_pool_candles(a.symbol, a.tf, cfg["history_bars"] if history else 10)

    def _persist_candles(self, a: AssetState, cs: list[Candle]) -> None:
        if not cs:
            return
        with db.engine().begin() as c:
            ts = [x.t for x in cs]
            c.execute(db.delete(db.candles).where(db.candles.c.symbol == a.symbol, db.candles.c.tf == a.tf,
                                                  db.candles.c.t.in_(ts)))
            c.execute(db.insert(db.candles), [{"symbol": a.symbol, "tf": a.tf, "t": x.t, "source": a.source,
                                               "o": x.o, "h": x.h, "l": x.l, "c": x.c, "v": x.v} for x in cs])

    @staticmethod
    def _next_close(a: AssetState) -> int:
        step = TF_SECONDS[a.tf]
        return (int(time.time()) // step + 1) * step

    # ------------------------------------------------------------------ candle-close events
    async def _candle_loop(self) -> None:
        while self.active:
            now = time.time()
            due = [a for a in self.assets.values() if a.series is not None and a.next_close and now >= a.next_close + 3]
            if due:
                self.set_state("scanning", f"{len(due)} candle(s) closed", None)
                await self.emit(f"Market data: {len(due)} asset(s) have a newly closed candle", "data")
            async def fetch(a: AssetState):
                try:
                    return a, await self._fetch(a), None
                except UpstreamError as e:
                    return a, None, e
            # crypto: concurrent (host throttle keeps us under Coinbase's limit); memecoins: one at a time
            crypto_due = [a for a in due if a.asset_class == "crypto"]
            other_due = [a for a in due if a.asset_class != "crypto"]
            results = list(await asyncio.gather(*[fetch(a) for a in crypto_due]))
            for a in other_due:
                results.append(await fetch(a))
            for a, new, err in sorted(results, key=lambda r: -r[0].rank_score):
                if not self.active:
                    break
                close_t = a.next_close
                a.next_close = self._next_close(a)
                if err is not None:
                    self._source(err.source, False, err.message)
                    await self.emit(f"{a.display}: candle update failed ({err.message}); will retry next bar", "data", "warn", asset=a.display)
                    continue
                added = a.series.merge(new)
                self._persist_candles(a, new[-3:])
                if added == 0:
                    continue                       # nothing new closed -> no recompute
                await self.analyse_asset(a, reason="candle_close", t_event=close_t)
                self.latency["candle_to_decision_ms"].append((time.time() - close_t) * 1000)
            self.set_state("monitoring" if paper.open_positions() else "running", "Waiting for next candle close / live ticks")
            await asyncio.sleep(2)

    # ------------------------------------------------------------------ analysis + strategies
    async def analyse_asset(self, a: AssetState, reason: str, t_event: float | None = None) -> None:
        s = a.series
        if s is None or len(s) < 80:
            a.excluded = "insufficient candle history"
            return
        self.set_state("analysing", "Analysing new candle", a.display)
        t0 = time.perf_counter()
        w = s.tail(CLASS_CFG[a.asset_class]["window"])
        an = analyse(w)
        t = len(w) - 1
        self.latency["analysis_ms"].append((time.perf_counter() - t0) * 1000)
        self.counters["assets_analysed"] += 1
        new_events = [d for d in an.at(t) if d.kind in ("liquidity_sweep", "bos", "choch", "failed_breakout", "volume_imbalance", "volatility_expansion")]
        a.last_analysis = {"trend": an.trend, "structure": market_structure_label(an.swings),
                           "atr_pct": float(100 * an.atr[t] / w.c[t]) if not math.isnan(an.atr[t]) else None,
                           "rsi": None if math.isnan(an.rsi[t]) else float(an.rsi[t]),
                           "rel_volume": None if math.isnan(an.rel_volume[t]) else float(an.rel_volume[t]),
                           "pools": an.pools, "recent": [d.to_json() for d in an.recent(6, len(w))][-12:],
                           "provisional": None if s.provisional is None else s.provisional.__dict__}
        a.analysed_at = time.time()
        analysis_ms = (time.perf_counter() - t0) * 1000
        for d in new_events:
            ev = d.evidence
            extra = ""
            if d.kind == "liquidity_sweep":
                extra = (f": swept {ev['level_type']} {ev['swept_level']:.6g} by {ev['penetration_pct']:.2f}%, closed back inside; "
                         f"volume {ev['volume_ratio'] or 0:.1f}×; reversal {'confirmation pending' if ev['confirmation_pending'] else 'not confirmed'}")
            await self.emit(f"{a.display}: {d.title}{extra}", "pattern", asset=a.display, data=d.to_json())
            self.counters["patterns_detected"] += 1
        self.rank(a, an, w)
        if not self.active:
            return
        n_sig = await self.evaluate(a, w, an)
        since_close = f"; {(time.time() - t_event):.1f}s after candle close" if t_event else ""
        await self.emit(f"{a.display} analysed: {len(w)} bars, trend {an.trend}, ATR {a.last_analysis['atr_pct'] or 0:.2f}%, "
                        f"{len(STRATEGIES)} strategies → {n_sig} setup(s){since_close}", "analysis", asset=a.display,
                        latency_ms=analysis_ms)

    def rank(self, a: AssetState, an, s: Series) -> None:
        t = len(s) - 1
        cfg = CLASS_CFG[a.asset_class]
        atr_pct = float(100 * an.atr[t] / s.c[t]) if not math.isnan(an.atr[t]) else 0.0
        horizon = atr_pct * math.sqrt(cfg["max_hold"])
        targets = paper.get_settings()["targets_pct"]
        if a.asset_class == "memecoin":
            liq = min(1.0, max(0.0, (math.log10(max(a.liquidity_usd or 1, 1)) - 5) / 2))
        else:
            liq = min(1.0, max(0.0, (math.log10(max(a.volume_24h_usd or 1, 1)) - 6) / 4))
        rv = an.rel_volume[t]
        rv_s = 0.0 if math.isnan(rv) else min(1.0, rv / 3)
        sp = a.spread_pct
        max_sp = 0.25 if a.asset_class == "crypto" else 1.0
        spread_s = 0.5 if sp is None else max(0.0, 1 - sp / max_sp)
        feas = sum(1 for x in targets if x <= horizon) / len(targets)
        mom = min(1.0, abs(s.c[t] / s.c[max(0, t - 12)] - 1) / 0.03)
        recent = [d for d in an.recent(12, len(s)) if d.kind in ("liquidity_sweep", "bos", "choch")]
        struct = 1.0 if any(d.i >= t - 2 for d in recent) else 0.5 if recent else 0.0
        hist = 0.0
        if self.model:
            vals = [self.model.estimate(st.name, a.asset_class, "with-trend/high-vol").get("expected_net_pct") for st in STRATEGIES]
            vals = [v for v in vals if v is not None]
            hist = min(1.0, max(0.0, max(vals) / 0.5)) if vals else 0.0
        news_s = min(1.0, a.news_count / 3)
        quality = 1.0 - min(0.5, (s.gaps or 0) / max(len(s), 1) * 10) - (0.3 if a.asset_class == "memecoin" else 0.0)
        if a.quote_ts and time.time() - a.quote_ts > 120:
            quality -= 0.3
        parts = {"liquidity": liq, "relative_volume": rv_s, "spread": spread_s, "volatility_fit": feas, "momentum": mom,
                 "structure": struct, "history": hist, "news": news_s, "data_quality": max(0.0, quality)}
        w = {"liquidity": 0.2, "relative_volume": 0.15, "spread": 0.1, "volatility_fit": 0.15, "momentum": 0.1,
             "structure": 0.1, "history": 0.1, "news": 0.03, "data_quality": 0.07}
        a.rank_parts = {k: round(v, 3) for k, v in parts.items()}
        # liquidity and data quality are multiplicative: a huge % volume spike on a thin market cannot rank high
        a.rank_score = 100 * sum(parts[k] * w[k] for k in w) * (0.4 + 0.6 * liq) * max(0.2, parts["data_quality"])
        ex = None
        if a.asset_class == "crypto" and (a.volume_24h_usd or 0) < 5_000_000:
            ex = "24h volume below $5M"
        if a.asset_class == "memecoin" and (a.liquidity_usd or 0) < 100_000:
            ex = "pool liquidity below $100k (simulated fills would be unrealistic)"
        if sp is not None and sp > max_sp:
            ex = f"spread {sp:.2f}% above {max_sp}%"
        if feas == 0:
            ex = ex or f"2–5% targets unrealistic: typical {cfg['max_hold']}-bar move ≈ {horizon:.2f}%"
        a.excluded = ex

    async def evaluate(self, a: AssetState, s: Series, an) -> int:
        n_sig = 0
        t = len(s) - 1
        st_cfg = paper.get_settings()
        cfg = CLASS_CFG[a.asset_class]
        self.set_state("evaluating", "Evaluating strategies", a.display)
        for strat in STRATEGIES:
            self.current["strategy"] = strat.label
            sig = strat.evaluate(s, an, t)
            self.counters["strategy_evaluations"] += 1
            if sig is None:
                continue
            if sig.direction == "short" and not st_cfg["allow_short"]:
                continue
            self.counters["opportunities_detected"] += 1
            n_sig += 1
            price = a.last or float(s.c[t])
            plan_obj, ce = backtest.plan_for(s, an, sig, notional=1000.0, max_hold=cfg["max_hold"],
                                             liquidity_usd=a.liquidity_usd, spread_pct=a.spread_pct,
                                             targets=st_cfg["targets_pct"],
                                             cost_overrides={"fee_pct": st_cfg["fees"][a.asset_class]},
                                             vol_24h_usd=a.volume_24h_usd)
            regime = backtest.regime_of(s, an, t, sig.direction)
            prob = self.model.estimate(sig.strategy, a.asset_class, regime) if self.model else {
                "insufficient": True, "reason": "calibration has not finished yet", "regime": regime}
            plan = None if isinstance(plan_obj, PlanRejection) else plan_obj.to_json()
            rej = {"reason": plan_obj.reason, "detail": plan_obj.detail} if isinstance(plan_obj, PlanRejection) else None
            if plan and plan["rr_net"] < st_cfg["min_rr"]:
                rej, plan = {"reason": "insufficient_risk_reward", "detail": plan}, None
            gates = [
                {"gate": "Closed candle", "passed": True, "detail": f"signal on closed {s.tf} candle {time.strftime('%H:%M', time.gmtime(int(s.t[t])))} UTC"},
                {"gate": "Tradeable market", "passed": a.excluded is None, "detail": a.excluded or "liquidity, spread and data quality OK"},
                {"gate": "Realistic 2–5% target & risk/reward", "passed": plan is not None,
                 "detail": (f"TP1 +{plan['tp1_pct']:.0f}%, R/R after costs {plan['rr_net']:.2f}" if plan else
                            f"{rej['reason'].replace('_', ' ')}")},
                {"gate": "Enough historical evidence", "passed": prob.get("calibrated_probability") is not None and not (st_cfg["require_calibration"] and prob.get("insufficient")),
                 "detail": prob.get("reason") or f"{prob.get('historical_samples')} samples, calibration verified"},
                {"gate": "Positive expected net return", "passed": (prob.get("expected_net_pct") or -1) >= st_cfg["min_expected_net_pct"],
                 "detail": "no estimate" if prob.get("expected_net_pct") is None else f"{prob['expected_net_pct']:+.2f}% per trade after costs (minimum {st_cfg['min_expected_net_pct']}%)"},
            ]
            if all(g["passed"] for g in gates):
                qty, notional = paper.size_position(a.asset_class, price, plan["stop"])
                risk = paper.risk_check(a.symbol, a.asset_class, notional)
                gates.append({"gate": "Portfolio risk limits", "passed": risk is None, "detail": risk or "within limits"})
            news = [n for n in self.news if a.display in n.get("assets", [])]
            rep = report_builder.build(a.public() | {"symbol": a.display}, s, an, sig, plan, rej, prob, news, gates, price)
            accepted = rep["accepted"]
            with db.engine().begin() as c:
                oid = c.execute(db.insert(db.opportunities).values(
                    ts=time.time(), asset=a.display, asset_class=a.asset_class, strategy=sig.strategy,
                    direction=sig.direction, status="accepted" if accepted else "rejected",
                    reason=None if accepted else next(g["detail"] for g in gates if not g["passed"]), report=rep)).inserted_primary_key[0]
            await self.emit(f"{strat.label} strategy evaluated on {a.display}: {sig.direction.upper()} setup", "strategy",
                            asset=a.display, strategy=strat.name)
            if not accepted:
                self.counters["opportunities_rejected"] += 1
                failed = next(g for g in gates if not g["passed"])
                await self.emit(f"Paper-trading opportunity rejected ({a.display}, {strat.label}): {failed['gate']} — {failed['detail']}",
                                "decision", asset=a.display, strategy=strat.name, data={"opportunity_id": oid})
                continue
            q = self._quote(a)
            if q is None:
                await self.emit(f"{a.display}: no live quote, position not opened", "decision", "warn", asset=a.display)
                continue
            fill = paper.open_position(a.symbol, a.asset_class, sig.strategy, sig.direction, q, plan,
                                       cfg["max_hold"] * TF_SECONDS[a.tf], rep, oid, a.liquidity_usd, a.volume_24h_usd)
            self.counters["positions_opened"] += 1
            await self.emit(f"SIMULATED {sig.direction.upper()} opened: {a.display} {fill['qty']:.6g} @ {fill['price']:.6g} "
                            f"(stop {plan['stop']:.6g}, TP1 {plan['tp1']:.6g}, TP2 {plan['tp2']:.6g})", "trade",
                            asset=a.display, strategy=strat.name, data={"position": fill, "opportunity_id": oid})
            await self.stream.set_products(self.stream.products | ({a.symbol} if a.asset_class == "crypto" else set()))
        return n_sig

    def _quote(self, a: AssetState):
        if a.bid and a.ask and a.quote_ts and time.time() - a.quote_ts < 120:
            return paper.Quote(a.bid, a.ask, a.last or (a.bid + a.ask) / 2, time.time())
        if a.asset_class == "memecoin" and a.last:
            # DEX price only: model the spread as part of slippage (cost model adds impact)
            return paper.Quote(a.last, a.last, a.last, time.time())
        return None

    # ------------------------------------------------------------------ live ticks
    async def _on_tick(self, product: str, tick: dict) -> None:
        t0 = time.perf_counter()
        a = self.assets.get(product)
        if a is not None:
            a.bid, a.ask, a.last, a.quote_ts = tick["bid"], tick["ask"], tick["price"], tick["recv"]
        events = paper.on_quote(product, paper.Quote(tick["bid"], tick["ask"], tick["price"], tick["recv"])) if self._has_open(product) else []
        for ev in events:
            self.counters["position_exits"] += 1
            await self.emit(f"SIMULATED exit ({ev['kind']}): {product} {ev['qty']:.6g} @ {ev['price']:.6g}, P&L {ev['pnl']:+.2f} USD"
                            + (" — position closed" if ev["closed"] else ""), "trade", asset=product, data=ev)
        self.latency["tick_ms"].append((time.perf_counter() - t0) * 1000)

    _open_cache: tuple[float, set] = (0.0, set())

    def _has_open(self, asset: str) -> bool:
        ts, assets = self._open_cache
        if time.time() - ts > 1.0:
            assets = {p["asset"] for p in paper.open_positions()}
            self._open_cache = (time.time(), assets)
        return asset in assets

    async def _on_stream_status(self, status: str, message: str) -> None:
        self._source("coinbase-ws", status == "connected", message)
        await self.emit(message, "data", "info" if status == "connected" else "error")

    async def _memecoin_quote_loop(self) -> None:
        """DEX pools have no free push feed: poll prices for memecoins with open positions (and the universe)."""
        while True:
            opens = [p for p in paper.open_positions() if p["asset_class"] == "memecoin"]
            for p in opens:
                try:
                    cs = await feeds.gecko_pool_candles(p["asset"], "1m", 2)
                    if cs:
                        px = cs[-1].c
                        a = self.assets.get(p["asset"])
                        if a:
                            a.last, a.quote_ts = px, time.time()
                        for ev in paper.on_quote(p["asset"], paper.Quote(px, px, px, time.time())):
                            await self.emit(f"SIMULATED exit ({ev['kind']}): {p['asset'][:8]}… @ {ev['price']:.6g}, P&L {ev['pnl']:+.2f} USD",
                                            "trade", asset=p["asset"], data=ev)
                except UpstreamError as e:
                    self._source(e.source, False, e.message)
            await asyncio.sleep(20 if opens else 60)

    # ------------------------------------------------------------------ news
    async def _news_loop(self) -> None:
        while self.active:
            self.set_state(self.state, "Checking news sources")
            t0 = time.perf_counter()
            items = []
            for name, make in (("fed", lambda: news_provider.rss("fed")), ("ecb", lambda: news_provider.rss("ecb")),
                               ("gdelt", lambda: news_provider.gdelt("(bitcoin OR ethereum OR crypto OR solana OR memecoin) sourcelang:english", 75))):
                try:
                    r = await make()
                    items += r["items"]
                    self._source(name, True, f"{len(r['items'])} items")
                except UpstreamError as e:
                    self._source(name, False, e.message)
            items = news_provider.dedupe(items)
            linked = 0
            for a in self.assets.values():
                a.news_count = 0
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
            await self.emit(f"News sources checked: {len(items)} items, {linked} linked to scanned assets "
                            "(media headlines unverified; central-bank items are primary sources)", "news",
                            latency_ms=(time.perf_counter() - t0) * 1000)
            await asyncio.sleep(300)

    # ------------------------------------------------------------------ calibration
    async def _calibration_loop(self) -> None:
        waited = 0
        while self.active:
            loaded = sum(a.series is not None for a in self.assets.values())
            if self.assets and (loaded == len(self.assets) or (waited >= 180 and loaded >= 0.6 * len(self.assets))):
                await self.calibrate()
                await asyncio.sleep(6 * 3600)
                waited = 0
            else:
                await asyncio.sleep(10)
                waited += 10

    async def calibrate(self) -> None:
        self.set_state("analysing", "Statistical validation (walk-forward backtests)")
        await self.emit("Statistical validation started: walk-forward backtests of every strategy on stored candle history", "validation")
        t0 = time.perf_counter()
        trades = []
        per_class: dict[str, int] = collections.Counter()
        for a in list(self.assets.values()):
            if a.series is None or len(a.series) < 200:
                continue
            st_cfg = paper.get_settings()
            tr, _ = await asyncio.to_thread(backtest.run, a.series, STRATEGIES, CLASS_CFG[a.asset_class]["max_hold"], 1000.0,
                                            a.liquidity_usd, st_cfg["targets_pct"], 60, {"fee_pct": st_cfg["fees"][a.asset_class]})
            trades += tr
            per_class[a.asset_class] += len(tr)
            await asyncio.sleep(0)
        self.model = await asyncio.to_thread(ProbabilityModel, trades)
        summary = {}
        for st in STRATEGIES:
            for cls in ("crypto", "memecoin"):
                g = [x for x in trades if x.strategy == st.name and x.asset_class == cls]
                summary[f"{st.name}|{cls}"] = {**backtest.summarise(g), "strategy": st.name, "label": st.label,
                                               "asset_class": cls, "validation": self.model.validation.get(f"{st.name}|{cls}"),
                                               "by_regime": self._by_regime(g)}
        closes = {a.display: a.series.c for a in self.assets.values() if a.asset_class == "crypto" and a.series is not None}
        times = {a.display: a.series.t for a in self.assets.values() if a.asset_class == "crypto" and a.series is not None}
        vol = {a.display: a.volume_24h_usd or 0 for a in self.assets.values()}
        top = sorted(closes, key=lambda k: -vol.get(k, 0))[:10]
        self.pairs = await asyncio.to_thread(pairs.scan, {k: closes[k] for k in top}, {k: times[k] for k in top})
        self.calibration = {"updated": time.time(), "trades_total": len(trades), "per_class": dict(per_class),
                            "strategies": summary, "pairs": self.pairs[:15],
                            "history_window": {cls: f"{CLASS_CFG[cls]['history_bars']} × {CLASS_CFG[cls]['tf']} bars per asset" for cls in CLASS_CFG},
                            "survivorship_warning": "Memecoin history comes from currently trending pools only (survivors); "
                                                    "results for that class are biased upward."}
        db.kv_set("calibration_summary", self.calibration)
        self.counters["calibrations"] += 1
        await self.emit(f"Statistical validation completed: {len(trades)} simulated historical setups across "
                        f"{len([a for a in self.assets.values() if a.series is not None])} assets", "validation",
                        latency_ms=(time.perf_counter() - t0) * 1000)

    @staticmethod
    def _by_regime(g: list) -> dict:
        out = {}
        for r in sorted({x.regime for x in g}):
            out[r] = backtest.summarise([x for x in g if x.regime == r])
        return out

    # ------------------------------------------------------------------ equity
    async def _equity_loop(self) -> None:
        while True:
            paper.snapshot_equity()
            await asyncio.sleep(60)


agent = Agent()
