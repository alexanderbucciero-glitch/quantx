"""Event-driven historical simulation of strategy setups, using the SAME execution rules as the
paper broker (app/paper/broker.py):

- Signal on the CLOSE of bar t. Entry:
    limit  (default): a limit at the signal close, valid for `limit_bars` bars; fills only if a later
                      bar trades through it (low ≤ limit for buys), at the limit price, maker fee.
    market          : fill at the OPEN of bar t+1 plus slippage, taker fee.
- Exits:
    stop   : touched → fill at the worse of stop and the bar open (gaps not given the stop), taker fee + slippage.
    TP1/TP2: limit fills at the level, maker fee. 50% at TP1, then stop to break-even.
    trail  : trend strategies — stop ratchets to (best close since entry − k·ATR); signal exit on close, taker.
    time   : exit at the close of the last allowed bar, taker fee.
- If a bar touches both the stop and a target, the STOP is assumed to fill first (conservative).
- One open trade per strategy per asset at a time.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np

from ..analysis.structure import Analysis, analyse
from ..market.candles import Series
from ..strategies.base import Signal, Strategy
from . import costs as cost_model
from .targets import PlanRejection, TradePlan, build_plan

DEFAULT_FEES = {"maker": 0.25, "taker": 0.40}


@dataclass
class SimTrade:
    symbol: str
    asset_class: str
    strategy: str
    direction: str
    signal_bar: int
    entry_time: int
    exit_time: int
    entry: float
    exit_avg: float
    bars_held: int
    hit_tp1: bool
    hit_tp2: bool
    stopped: bool
    timed_out: bool
    gross_pct: float
    net_pct: float
    regime: str
    score: float
    tp1_pct: float
    stop_pct: float
    tf: str = ""
    exit_reason: str = ""
    fees_pct: float = 0.0

    def to_json(self) -> dict:
        return asdict(self)

    @property
    def combo(self) -> str:
        return combo_key(self.strategy, self.asset_class, self.tf, self.direction)


def combo_key(strategy: str, asset_class: str, tf: str, direction: str) -> str:
    return f"{strategy}|{asset_class}|{tf}|{direction}"


def regime_of(s: Series, a: Analysis, t: int, direction: str) -> str:
    """Causal 2×2 regime: with/counter trend (EMA20 vs EMA50) × high/low volatility (ATR% vs its median)."""
    e20, e50 = a.ema20[t], a.ema50[t]
    trend_up = bool(e20 > e50) if not (np.isnan(e20) or np.isnan(e50)) else None
    with_trend = "with-trend" if trend_up is None or (trend_up == (direction == "long")) else "counter-trend"
    atr_pct = a.atr[max(0, t - 300):t + 1] / s.c[max(0, t - 300):t + 1]
    hist = atr_pct[~np.isnan(atr_pct)]
    vol = "high-vol" if hist.size and atr_pct[-1] > np.median(hist) else "low-vol"
    return f"{with_trend}/{vol}"


def plan_for(s: Series, a: Analysis, sig: Signal, notional: float, max_hold: int,
             liquidity_usd: float | None = None, spread_pct: float | None = None,
             targets: list[float] | None = None, cost_overrides: dict | None = None,
             vol_24h_usd: float | None = None, fees: dict | None = None, trail_atr: float | None = None,
             min_rr: float = 1.2) -> tuple[TradePlan | PlanRejection | dict, cost_model.CostEstimate]:
    """Plan for a signal. Target strategies → TradePlan (TP1/TP2/stop). Trend strategies → trailing dict."""
    t = sig.t
    fees = fees or DEFAULT_FEES
    if vol_24h_usd is None and s.asset_class not in ("memecoin",):
        bars_day = max(1, 86400 // s.step)
        lo = max(0, t - bars_day + 1)
        vol_24h_usd = float(np.sum(s.v[lo:t + 1] * s.c[lo:t + 1])) or None
    over = {"fee_pct": (fees["maker"] + fees["taker"]) / 2, **(cost_overrides or {})}
    ce = cost_model.estimate(s.asset_class, notional, spread_pct, vol_24h_usd, liquidity_usd, over)
    atr_v = float(a.atr[t])
    if sig.exit_mode == "trail":
        if not atr_v or math.isnan(atr_v):
            return PlanRejection("no_volatility_estimate", {}), ce
        k = trail_atr or 3.0
        entry = float(s.c[t])
        stop = entry - k * atr_v if sig.direction == "long" else entry + k * atr_v
        return {"direction": sig.direction, "entry": entry, "stop": stop, "tp1": None, "tp2": None, "trail_atr": k,
                "atr": atr_v, "stop_pct": 100 * k * atr_v / entry, "costs_pct": ce.total_pct, "fee_pct": ce.fee_pct_round_trip,
                "slippage_pct": ce.slippage_pct_round_trip, "max_hold_bars": max_hold, "exit_mode": "trail"}, ce
    plan = build_plan(sig.direction, float(s.c[t]), atr_v, ce, sig.invalidation, targets, max_hold, min_rr=min_rr)
    return plan, ce


def _slip(s: Series, j: int, notional: float = 1000.0) -> float:
    lo = max(0, j - max(1, 86400 // s.step) + 1)
    vol = float(np.sum(s.v[lo:j + 1] * s.c[lo:j + 1])) or None
    return cost_model.estimate(s.asset_class, notional, 0.0, vol, None).slippage_pct_round_trip / 2


def simulate(s: Series, sig: Signal, plan, fees: dict | float = 0.0, entry_mode: str = "market", limit_bars: int = 2,
             strategy: Strategy | None = None, a: Analysis | None = None, tf: str = "") -> SimTrade | None:
    """Simulate one setup. `fees` may be {"maker","taker"} (% per side) or a single round-trip % (legacy)."""
    t = sig.t
    if t + 1 >= len(s):
        return None
    legacy_rt = fees if isinstance(fees, (int, float)) else None
    fm = fees if isinstance(fees, dict) else {"maker": 0.0, "taker": 0.0}
    pd = plan if isinstance(plan, dict) else plan.to_json()
    long = sig.direction == "long"
    stop, tp1, tp2 = pd["stop"], pd.get("tp1"), pd.get("tp2")
    trail = pd.get("trail_atr")
    max_hold = pd["max_hold_bars"]
    # ---- entry
    if entry_mode == "limit":
        lim = float(s.c[t])
        j0 = next((j for j in range(t + 1, min(len(s), t + 1 + limit_bars)) if (s.l[j] <= lim if long else s.h[j] >= lim)), None)
        if j0 is None:
            return None
        fill = min(lim, s.o[j0]) if long else max(lim, s.o[j0])   # a gap through the limit fills at the better open
        entry_fee = fm["maker"]
        first = j0
    else:
        j0 = t + 1
        sl = _slip(s, j0) if legacy_rt is None else 0.0
        fill = s.o[j0] * (1 + sl / 100) if long else s.o[j0] * (1 - sl / 100)
        entry_fee = fm["taker"]
        first = j0
    remaining, realised, fee_pct = 1.0, 0.0, entry_fee
    hit1 = hit2 = stopped = timed = False
    reason = ""
    last = min(len(s) - 1, first + max_hold)
    best = fill

    def ret(px: float) -> float:
        return (px / fill - 1) if long else (1 - px / fill)

    j = first
    for j in range(first, last + 1):
        lo, hi, op = s.l[j], s.h[j], s.o[j]
        if True:                                   # stop checked first on every bar, incl. the fill bar (conservative)
            if (lo <= stop) if long else (hi >= stop):
                px = min(stop, op) if long else max(stop, op)
                sl = _slip(s, j) if legacy_rt is None else 0.0
                px = px * (1 - sl / 100) if long else px * (1 + sl / 100)
                realised += remaining * ret(px)
                fee_pct += remaining * fm["taker"]
                reason = ("trailing_stop" if trail and ((stop > fill) if long else (stop < fill))
                          else ("breakeven" if hit1 else "stop"))
                remaining, stopped = 0.0, True
                break
        if tp1 is not None:
            # on a limit-entry fill bar the intrabar order is unknown, so targets count only from the next bar
            if not hit1 and (hi >= tp1 if long else lo <= tp1) and (entry_mode == "market" or j > first):
                hit1 = True
                part = 0.5 if tp2 and tp2 != tp1 else 1.0
                realised += part * ret(tp1)
                fee_pct += part * fm["maker"]
                remaining = 1.0 - part
                stop = fill
                if remaining <= 0:
                    hit2, reason = True, "tp1"
                    break
                continue
            if hit1 and tp2 and (hi >= tp2 if long else lo <= tp2):
                realised += remaining * ret(tp2)
                fee_pct += remaining * fm["maker"]
                remaining, hit2, reason = 0.0, True, "tp2"
                break
        if trail:
            best = max(best, s.c[j]) if long else min(best, s.c[j])
            new = best - trail * pd["atr"] if long else best + trail * pd["atr"]
            stop = max(stop, new) if long else min(stop, new)
            if strategy is not None and a is not None:
                why = strategy.should_exit(s, a, j, sig.direction)
                if why and j < last:
                    sl = _slip(s, j)
                    px = s.c[j] * (1 - sl / 100) if long else s.c[j] * (1 + sl / 100)
                    realised += remaining * ret(px)
                    fee_pct += remaining * fm["taker"]
                    remaining, reason = 0.0, "signal_exit"
                    break
    if remaining > 0:
        realised += remaining * ret(float(s.c[j]))
        fee_pct += remaining * fm["taker"]
        timed, reason = True, reason or "time_exit"
    gross = 100 * realised
    net = gross - legacy_rt if legacy_rt is not None else gross - fee_pct
    exit_avg = fill * (1 + realised) if long else fill * (1 - realised)
    return SimTrade(s.symbol, s.asset_class, sig.strategy, sig.direction, t, int(s.t[first]), int(s.t[j]),
                    float(fill), float(exit_avg), j - first + 1, hit1, hit2, stopped, timed, gross, net,
                    "", sig.score, float(pd.get("tp1_pct") or 0.0), float(pd.get("stop_pct") or 0.0),
                    tf or s.tf, reason, fee_pct if legacy_rt is None else legacy_rt)


def run(s: Series, strategies: list[Strategy], max_hold: int, notional: float = 1000.0,
        liquidity_usd: float | None = None, targets: list[float] | None = None,
        start: int = 60, cost_overrides: dict | None = None, fees: dict | None = None,
        entry_mode: str = "limit", limit_bars: int = 2, analysis: Analysis | None = None,
        until: int | None = None) -> tuple[list[SimTrade], dict]:
    """Backtest `strategies` on `s`. Bars after `until` (exclusive) are never used for signals."""
    a = analysis or analyse(s)
    fees = fees or DEFAULT_FEES
    if cost_overrides and "fee_pct" in cost_overrides and fees is DEFAULT_FEES:
        fees = {"maker": cost_overrides["fee_pct"], "taker": cost_overrides["fee_pct"]}
    trades: list[SimTrade] = []
    rejected: dict[str, int] = {}
    busy_until = {st.name: -1 for st in strategies}
    end = min(len(s) - 1, until if until is not None else len(s) - 1)
    for t in range(start, end):
        for st in strategies:
            if t <= busy_until[st.name]:
                continue
            sig = st.evaluate(s, a, t)
            if sig is None:
                continue
            hold = max_hold * (2 if sig.exit_mode == "trail" else 1)
            plan, ce = plan_for(s, a, sig, notional, hold, liquidity_usd, None, targets, None, None, fees, st.trail_atr)
            if isinstance(plan, PlanRejection):
                rejected[plan.reason] = rejected.get(plan.reason, 0) + 1
                continue
            tr = simulate(s, sig, plan, fees, entry_mode, limit_bars, st, a)
            if tr is None:
                rejected["entry_limit_not_filled"] = rejected.get("entry_limit_not_filled", 0) + 1
                continue
            tr.regime = regime_of(s, a, t, sig.direction)
            trades.append(tr)
            busy_until[st.name] = t + tr.bars_held
    return trades, rejected


def summarise(trades: list[SimTrade]) -> dict:
    if not trades:
        return {"trades": 0}
    net = np.array([x.net_pct for x in trades])
    wins, losses = net[net > 0], net[net <= 0]
    eq = np.cumprod(1 + net / 100 * 0.1)        # illustrative: 10% of equity per trade
    peak = np.maximum.accumulate(eq)
    se = float(net.std(ddof=1) / np.sqrt(net.size)) if net.size > 1 else None
    return {
        "trades": len(trades),
        "tp1_hit_rate": float(np.mean([x.hit_tp1 for x in trades])),
        "win_rate": float(np.mean(net > 0)),
        "avg_net_pct": float(net.mean()),
        "se_net_pct": se,
        "ci90_net_pct": [float(net.mean() - 1.645 * se), float(net.mean() + 1.645 * se)] if se is not None else None,
        "median_net_pct": float(np.median(net)),
        "avg_win_pct": float(wins.mean()) if wins.size else None,
        "avg_loss_pct": float(losses.mean()) if losses.size else None,
        "profit_factor": float(wins.sum() / -losses.sum()) if losses.size and losses.sum() < 0 else None,
        "avg_bars_held": float(np.mean([x.bars_held for x in trades])),
        "stopped_rate": float(np.mean([x.stopped for x in trades])),
        "timed_out_rate": float(np.mean([x.timed_out for x in trades])),
        "max_drawdown_pct_at_10pct_size": float(100 * (eq / peak - 1).min()),
        "sum_net_pct": float(net.sum()),
    }
