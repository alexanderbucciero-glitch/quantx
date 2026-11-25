"""Historical replay: drives the REAL desk + broker with real historical candles and a simulated clock.

  1. Download history for each asset (real candles from Coinbase / Kraken).
  2. CALIBRATION window (first `calib_frac` of time): backtest every strategy and fit the evidence
     selector using only trades that CLOSED before the replay starts — no look-ahead.
  3. REPLAY window: walk forward candle by candle across all assets in time order. Inside each candle the
     price path open → (low, high in the adverse order) → close is fed as quotes, so resting orders, stops
     and targets fill exactly as in live paper trading. At each candle close the desk decides.
  4. Equity is snapshotted every candle; daily reports are generated as simulated days end.

Results live in their own account ("replay-<n>") and are never mixed with live paper trading.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

import numpy as np

from ..analysis.structure import analyse
from ..core import clock, db
from ..market import feeds
from ..market.candles import TF_SECONDS, Series
from ..paper.broker import Broker, Quote
from ..paper.settings import get_settings
from ..quant import backtest
from ..quant.selector import Selector
from ..strategies.library import STRATEGIES
from . import reports
from .desk import MAX_HOLD, AssetCtx, Desk

log = logging.getLogger("quantx.replay")

DEFAULT_ASSETS = [
    ("BTC-USD", "BTC/USD", "crypto", "coinbase"), ("ETH-USD", "ETH/USD", "crypto", "coinbase"),
    ("SOL-USD", "SOL/USD", "crypto", "coinbase"), ("XRP-USD", "XRP/USD", "crypto", "coinbase"),
    ("DOGE-USD", "DOGE/USD", "crypto", "coinbase"), ("ADA-USD", "ADA/USD", "crypto", "coinbase"),
    ("AVAX-USD", "AVAX/USD", "crypto", "coinbase"), ("LINK-USD", "LINK/USD", "crypto", "coinbase"),
    ("LTC-USD", "LTC/USD", "crypto", "coinbase"), ("BCH-USD", "BCH/USD", "crypto", "coinbase"),
    ("PAXG-USD", "PAXG/USD (gold)", "commodity", "coinbase"),
]
HALF_SPREAD = {"crypto": 0.0002, "commodity": 0.0003, "fx": 0.00005, "memecoin": 0.003}


@dataclass
class ReplayState:
    id: int
    account: str
    status: str = "starting"
    progress: float = 0.0
    message: str = ""
    started_at: float = 0.0
    finished_at: float | None = None
    result: dict | None = None
    error: str | None = None


class ReplayManager:
    def __init__(self) -> None:
        self.runs: dict[int, ReplayState] = {}
        self._task: asyncio.Task | None = None

    def next_id(self) -> int:
        n = db.kv_get("replay_counter") or 0
        db.kv_set("replay_counter", n + 1)
        return n + 1

    def start(self, tf: str = "1h", bars: int = 2400, calib_frac: float = 0.6, assets: list | None = None,
              settings_patch: dict | None = None) -> ReplayState:
        if self._task and not self._task.done():
            raise RuntimeError("a replay is already running")
        rid = self.next_id()
        st = ReplayState(rid, f"replay-{rid}", started_at=time.time())
        self.runs[rid] = st
        self._task = asyncio.create_task(self._run(st, tf, bars, calib_frac, assets or DEFAULT_ASSETS, settings_patch or {}),
                                         name=f"replay-{rid}")
        return st

    async def _run(self, st: ReplayState, tf, bars, calib_frac, assets, patch) -> None:
        try:
            st.result = await run_replay(st, tf, bars, calib_frac, assets, patch)
            st.status = "finished"
        except Exception as e:  # report, never crash the server
            log.exception("replay failed")
            st.status, st.error = "error", f"{type(e).__name__}: {e}"
        finally:
            st.finished_at = time.time()
            clock.set_sim_time(None)
            db.kv_set(f"replay:{st.id}", {"id": st.id, "account": st.account, "status": st.status, "error": st.error,
                                          "started_at": st.started_at, "finished_at": st.finished_at, "result": st.result})


async def _history(sym: str, source: str, tf: str, bars: int) -> list:
    if source == "kraken":
        return await feeds.kraken_candles(sym, tf)
    return await feeds.coinbase_history(sym, tf, bars)


def _path(o: float, h: float, l: float, c: float, long_bias: bool) -> list[float]:
    """Adverse-first intrabar path: for a bullish candle assume the low came first, for a bearish one the high."""
    return [o, l, h, c] if c >= o else [o, h, l, c]


async def run_replay(st: ReplayState, tf: str, bars: int, calib_frac: float, assets: list, patch: dict) -> dict:
    account = st.account
    settings = {**get_settings(), **patch}
    st.status, st.message = "downloading", "downloading real historical candles"
    series: dict[str, Series] = {}
    ctx: dict[str, AssetCtx] = {}
    for sym, display, cls, src in assets:
        try:
            cs = await _history(sym, src, tf, bars)
        except Exception as e:
            st.message = f"{display}: history unavailable ({e})"
            continue
        s = Series.from_candles(sym, cls, tf, src, cs, now=time.time())
        if len(s) < 300:
            continue
        series[sym] = s
        ctx[sym] = AssetCtx(sym, display, cls)
    if not series:
        raise RuntimeError("no historical data could be downloaded")
    t_start = min(int(s.t[0]) for s in series.values())
    t_end = max(int(s.t[-1]) for s in series.values())
    split = int(t_start + calib_frac * (t_end - t_start))
    # ---- calibration on the first window only
    st.status, st.message = "calibrating", "walk-forward calibration on the first window (no look-ahead)"
    fees_by = settings["fees"]
    trades = []
    for sym, s in series.items():
        cut = int(np.searchsorted(s.t, split))
        if cut < 200:
            continue
        cal = s.upto(cut - 1)
        tr, _ = await asyncio.to_thread(backtest.run, cal, STRATEGIES, MAX_HOLD.get(tf, 48), 1000.0, None,
                                        settings["targets_pct"], 60, None, fees_by.get(cal.asset_class, fees_by["crypto"]),
                                        settings["entry_order"], settings["entry_limit_bars"])
        trades += [x for x in tr if x.exit_time < split]
        await asyncio.sleep(0)
    selector = Selector(account)
    selector.fit(trades)
    broker = Broker(account, settings)
    broker.ensure_account()
    events_log: list[dict] = []

    async def emit(message, kind="info", level="info", asset=None, strategy=None, latency_ms=None, data=None, account=None):
        ev = {"ts": clock.now(), "level": level, "kind": kind, "asset": asset, "strategy": strategy, "message": message,
              "latency_ms": latency_ms, "data": data, "account": st.account}
        events_log.append(ev)
        if kind in ("trade", "report", "learning", "decision"):
            with db.engine().begin() as c:
                c.execute(db.insert(db.activity).values(**{k: v for k, v in ev.items()}))

    desk = Desk(account, broker, selector, emit)
    # ---- replay window
    st.status, st.message = "replaying", "replaying candles through the trading desk"
    timeline = sorted({int(t) for s in series.values() for t in s.t if t >= split})
    idx = {sym: int(np.searchsorted(s.t, split)) for sym, s in series.items()}
    step = TF_SECONDS[tf]
    last_day = None
    clock.set_sim_time(float(split))
    broker.snapshot_equity(float(split))
    for n, bar_t in enumerate(timeline):
        for sym, s in series.items():
            i = idx[sym]
            if i >= len(s) or int(s.t[i]) != bar_t:
                continue
            hs = HALF_SPREAD.get(s.asset_class, 0.0002)
            path = _path(s.o[i], s.h[i], s.l[i], s.c[i], True)
            live = broker.positions("open", sym) or broker.open_orders(sym)
            if live:
                # continuous markets: price passes THROUGH levels, so walk each leg in small steps
                # (otherwise a stop between open and high would look like a gap and fill at the high)
                fine = [path[0]]
                for a_, b_ in zip(path, path[1:]):
                    fine += list(np.linspace(a_, b_, 25)[1:])
                path = fine
            for k, px in enumerate(path):
                ts = bar_t + (k / max(1, len(path) - 1)) * (step - 1)
                clock.set_sim_time(ts)
                await desk.on_quote(sym, Quote(px * (1 - hs), px * (1 + hs), px, ts))
            close_t = bar_t + step
            clock.set_sim_time(close_t)
            w = s.upto(i).tail(400)
            an = await asyncio.to_thread(analyse, w)
            q = Quote(s.c[i] * (1 - hs), s.c[i] * (1 + hs), s.c[i], close_t)
            closes = {k: v.c[max(0, idx[k] - 200):idx[k] + 1] for k, v in series.items()}
            await desk.on_candle(ctx[sym], w, an, q, closes)
            idx[sym] = i + 1
        clock.set_sim_time(bar_t + step)
        broker.snapshot_equity(bar_t + step)
        day = (bar_t + step) // 86400
        if last_day is not None and day != last_day:
            reports.period_report(account, "daily", (day - 1) * 86400 + 1)
        last_day = day
        if n % 25 == 0:
            st.progress = n / max(1, len(timeline))
            st.message = f"replaying {time.strftime('%Y-%m-%d %H:%M', time.gmtime(bar_t))} UTC — {desk.counters.get('trades_executed', 0)} trades executed"
            await asyncio.sleep(0)
    # close the books at the last price: open positions are marked, not force-closed
    end_t = timeline[-1] + step if timeline else split
    clock.set_sim_time(end_t)
    reports.period_report(account, "daily", end_t - 1)
    reports.period_report(account, "weekly", end_t - 1)
    reports.period_report(account, "monthly", end_t - 1)
    state = broker.state()
    closed = broker.positions("closed")
    net = [p["realized_pnl"] for p in closed]
    bh = {ctx[k].display: float(v.c[-1] / v.c[int(np.searchsorted(v.t, split))] - 1) * 100 for k, v in series.items()}
    st.progress = 1.0
    return {
        "account": account, "timeframe": tf, "assets": [c.display for c in ctx.values()],
        "calibration_window": [t_start, split], "replay_window": [split, end_t],
        "calibration_trades": len(trades), "tiers": selector.summary()["counts"],
        "active_or_exploration": {k: v for k, v in selector.tiers.items() if v["tier"] != "disabled"},
        "selection_validation": selector.validation,
        "final": state, "closed_trades": len(closed), "winning": sum(1 for x in net if x > 0),
        "losing": sum(1 for x in net if x <= 0), "net_realized": sum(net), "counters": desk.counters,
        "benchmark_buy_hold_pct": bh, "events": len(events_log),
    }


manager = ReplayManager()
