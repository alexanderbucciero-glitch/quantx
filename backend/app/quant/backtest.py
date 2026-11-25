"""Event-driven historical simulation of strategy setups.

Rules (conservative, documented):
- Signal on the CLOSE of bar t. Fill at the OPEN of bar t+1 (no same-bar fills, no look-ahead).
- Stop / TP levels are absolute prices from the plan; returns are measured from the actual fill.
- If a bar touches both stop and a target, the STOP is assumed to fill first.
- 50% exits at TP1, stop moves to break-even, remainder exits at TP2, the stop, or the time limit.
- Round-trip fees and slippage from the cost model are deducted from every trade.
- One open trade per strategy per asset at a time (no overlapping duplicate setups).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from ..analysis.structure import Analysis, analyse
from ..market.candles import Series
from ..strategies.base import Signal, Strategy
from . import costs as cost_model
from .targets import PlanRejection, TradePlan, build_plan


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

    def to_json(self) -> dict:
        return asdict(self)


def regime_of(s: Series, a: Analysis, t: int, direction: str) -> str:
    """Causal 2×2 regime: with/counter trend (EMA20 vs EMA50) × high/low volatility (ATR% vs its median)."""
    e20, e50 = a.ema20[t], a.ema50[t]
    trend_up = bool(e20 > e50) if not (np.isnan(e20) or np.isnan(e50)) else None
    with_trend = "with-trend" if trend_up is None or (trend_up == (direction == "long")) else "counter-trend"
    atr_pct = a.atr[: t + 1] / s.c[: t + 1]
    hist = atr_pct[max(0, t - 300):t + 1]
    hist = hist[~np.isnan(hist)]
    vol = "high-vol" if hist.size and atr_pct[t] > np.median(hist) else "low-vol"
    return f"{with_trend}/{vol}"


def simulate(s: Series, sig: Signal, plan: TradePlan, costs_pct: float) -> SimTrade | None:
    t = sig.t
    if t + 1 >= len(s):
        return None
    fill = float(s.o[t + 1])
    long = plan.direction == "long"
    stop, tp1, tp2 = plan.stop, plan.tp1, plan.tp2
    # gap through the stop at the open -> immediate stopped-out fill
    remaining, realised, hit1, hit2, stopped, timed = 1.0, 0.0, False, False, False, False
    last = min(len(s) - 1, t + plan.max_hold_bars)
    exit_time, j = int(s.t[last]), last

    def ret(px: float) -> float:
        return (px / fill - 1) if long else (1 - px / fill)

    for j in range(t + 1, last + 1):
        lo, hi = s.l[j], s.h[j]
        stop_hit = lo <= stop if long else hi >= stop
        if stop_hit:
            px = min(stop, s.o[j]) if long else max(stop, s.o[j])
            realised += remaining * ret(px)
            remaining, stopped, exit_time = 0.0, True, int(s.t[j])
            break
        if not hit1 and (hi >= tp1 if long else lo <= tp1):
            hit1 = True
            realised += 0.5 * ret(tp1)
            remaining = 0.5
            stop = fill                              # break-even on the runner
            if tp2 == tp1:
                realised += remaining * ret(tp1)
                remaining, hit2, exit_time = 0.0, True, int(s.t[j])
                break
            continue                                 # TP2 can only be checked from the next bar
        if hit1 and (hi >= tp2 if long else lo <= tp2):
            realised += remaining * ret(tp2)
            remaining, hit2, exit_time = 0.0, True, int(s.t[j])
            break
    if remaining > 0:
        realised += remaining * ret(float(s.c[j]))
        timed, exit_time = True, int(s.t[j])
    gross = 100 * realised
    exit_avg = fill * (1 + realised) if long else fill * (1 - realised)
    return SimTrade(s.symbol, s.asset_class, sig.strategy, plan.direction, t, int(s.t[t + 1]), exit_time,
                    fill, exit_avg, j - t, hit1, hit2, stopped, timed, gross, gross - costs_pct,
                    "", sig.score, plan.tp1_pct, plan.stop_pct)


def plan_for(s: Series, a: Analysis, sig: Signal, notional: float, max_hold: int,
             liquidity_usd: float | None = None, spread_pct: float | None = None,
             targets: list[float] | None = None, cost_overrides: dict | None = None,
             vol_24h_usd: float | None = None) -> tuple[TradePlan | PlanRejection, cost_model.CostEstimate]:
    t = sig.t
    if vol_24h_usd is None and s.asset_class != "memecoin":
        bars_day = max(1, 86400 // s.step)
        vol_24h_usd = float(np.sum(s.v[max(0, t - bars_day + 1):t + 1] * s.c[max(0, t - bars_day + 1):t + 1]))
    ce = cost_model.estimate(s.asset_class, notional, spread_pct, vol_24h_usd, liquidity_usd, cost_overrides)
    plan = build_plan(sig.direction, float(s.c[t]), float(a.atr[t]), ce, sig.invalidation, targets, max_hold)
    return plan, ce


def run(s: Series, strategies: list[Strategy], max_hold: int, notional: float = 1000.0,
        liquidity_usd: float | None = None, targets: list[float] | None = None,
        start: int = 60, cost_overrides: dict | None = None) -> tuple[list[SimTrade], dict]:
    a = analyse(s)
    trades: list[SimTrade] = []
    rejected: dict[str, int] = {}
    busy_until = {st.name: -1 for st in strategies}
    for t in range(start, len(s) - 1):
        for st in strategies:
            if t <= busy_until[st.name]:
                continue
            sig = st.evaluate(s, a, t)
            if sig is None:
                continue
            plan, ce = plan_for(s, a, sig, notional, max_hold, liquidity_usd, None, targets, cost_overrides)
            if isinstance(plan, PlanRejection):
                rejected[plan.reason] = rejected.get(plan.reason, 0) + 1
                continue
            tr = simulate(s, sig, plan, ce.total_pct)
            if tr is None:
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
    return {
        "trades": len(trades),
        "tp1_hit_rate": float(np.mean([x.hit_tp1 for x in trades])),
        "win_rate": float(np.mean(net > 0)),
        "avg_net_pct": float(net.mean()),
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
