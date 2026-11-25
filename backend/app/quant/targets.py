"""The 2–5% target system.

For a setup we pick take-profits from the configured gross targets (default 2/3/4/5 %), but only
targets that are REALISTIC for this asset right now:

  expected typical move over the holding horizon ≈ ATR% × sqrt(bars)   (random-walk scaling)

A target is feasible if it is ≤ `reach` × ATR% × sqrt(max_hold_bars). The stop is volatility
adjusted (k × ATR beyond entry, or beyond the structural invalidation level if that is wider).
The trade is rejected when costs eat the edge or reward/risk after costs is below the minimum.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from .costs import CostEstimate

DEFAULT_TARGETS = [2.0, 3.0, 4.0, 5.0]


@dataclass
class TradePlan:
    direction: str            # long | short
    entry: float
    stop: float
    tp1: float
    tp2: float
    tp1_pct: float
    tp2_pct: float
    stop_pct: float
    expected_hold_bars: int
    max_hold_bars: int
    gross_tp1_pct: float
    costs_pct: float
    fee_pct: float
    slippage_pct: float
    net_tp1_pct: float
    net_loss_pct: float
    rr_net: float
    feasible_targets: list[float]

    def to_json(self) -> dict:
        return asdict(self)


@dataclass
class PlanRejection:
    reason: str
    detail: dict


def build_plan(direction: str, entry: float, atr_value: float, costs: CostEstimate,
               invalidation: float | None = None, targets: list[float] | None = None,
               max_hold_bars: int = 48, stop_atr: float = 1.5, reach: float = 1.0,
               min_rr: float = 1.2) -> TradePlan | PlanRejection:
    targets = sorted(targets or DEFAULT_TARGETS)
    if entry <= 0 or not atr_value or math.isnan(atr_value) or atr_value <= 0:
        return PlanRejection("no_volatility_estimate", {"atr": atr_value})
    atr_pct = 100 * atr_value / entry
    horizon_move = reach * atr_pct * math.sqrt(max_hold_bars)
    feasible = [t for t in targets if t <= horizon_move and t > 2 * costs.total_pct]
    if not feasible:
        return PlanRejection("targets_not_realistic", {
            "atr_pct": round(atr_pct, 4), "typical_move_over_horizon_pct": round(horizon_move, 3),
            "round_trip_costs_pct": round(costs.total_pct, 3), "targets": targets})
    sign = 1 if direction == "long" else -1
    stop_dist = stop_atr * atr_value
    if invalidation is not None:
        struct_dist = (entry - invalidation) * sign + 0.2 * atr_value
        if struct_dist > 0:
            stop_dist = max(stop_dist, struct_dist)
    stop_pct = 100 * stop_dist / entry
    net_loss = stop_pct + costs.total_pct
    plan = None
    # smallest realistic target whose reward/risk AFTER costs clears the minimum
    for k, tp1_pct in enumerate(feasible):
        tp2_pct = feasible[k + 1] if k + 1 < len(feasible) else tp1_pct
        net_win = tp1_pct - costs.total_pct
        rr = net_win / net_loss if net_loss > 0 else 0.0
        exp_bars = int(min(max_hold_bars, max(1, round((tp1_pct / max(atr_pct, 1e-9)) ** 2))))
        plan = TradePlan(direction, entry, entry - sign * stop_dist, entry * (1 + sign * tp1_pct / 100),
                         entry * (1 + sign * tp2_pct / 100), tp1_pct, tp2_pct, stop_pct, exp_bars, max_hold_bars,
                         tp1_pct, costs.total_pct, costs.fee_pct_round_trip, costs.slippage_pct_round_trip,
                         net_win, net_loss, rr, feasible)
        if net_win > 0 and rr >= min_rr:
            return plan
    if plan.net_tp1_pct <= 0:
        return PlanRejection("costs_exceed_target", plan.to_json())
    return PlanRejection("insufficient_risk_reward", {**plan.to_json(), "min_rr": min_rr})
