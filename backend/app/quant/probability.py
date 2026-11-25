"""Statistical probability engine.

Success event: TP1 is reached before the stop (the setup "worked").
Model: hierarchical Beta-binomial shrinkage
    p(strategy, class, regime) = (wins_regime + m · p_strategy) / (n_regime + m)
    p_strategy               = (wins_strategy + 1) / (n_strategy + 2)
fitted WALK-FORWARD: each historical trade is predicted using only trades that had already
closed before it was entered. Those out-of-sample predictions are scored (Brier, log-loss,
expected calibration error, reliability table) and compared with a no-skill baseline.

Distinctions shown to the user:
- AI confidence      : heuristic evidence score of the setup (not a probability).
- Historical win rate: raw fraction of past setups that reached TP1 (in-sample, all data).
- Calibrated prob.   : walk-forward shrinkage estimate for this regime, with a confidence interval,
                       reported only when the sample is large enough.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

import numpy as np

from .backtest import SimTrade

MIN_SAMPLES = 30        # below this: "insufficient evidence"
SHRINK_M = 20.0


def wilson(k: int, n: int, z: float = 1.645) -> tuple[float, float] | None:
    """Wilson score interval (90% by default)."""
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


@dataclass
class Counts:
    n: int = 0
    k: int = 0
    net_sum: float = 0.0

    def add(self, success: bool, net: float) -> None:
        self.n += 1
        self.k += int(success)
        self.net_sum += net


def _predict(strat: Counts, reg: Counts) -> float:
    p_s = (strat.k + 1) / (strat.n + 2)
    return (reg.k + SHRINK_M * p_s) / (reg.n + SHRINK_M)


def walk_forward(trades: list[SimTrade]) -> dict:
    """Out-of-sample evaluation of the probability model over chronologically ordered trades."""
    by_entry = sorted(trades, key=lambda x: x.entry_time)
    by_exit = sorted(trades, key=lambda x: x.exit_time)
    strat: dict[str, Counts] = defaultdict(Counts)
    reg: dict[tuple, Counts] = defaultdict(Counts)
    base = Counts()
    preds, outs, base_preds = [], [], []
    xi = 0
    for tr in by_entry:
        while xi < len(by_exit) and by_exit[xi].exit_time <= tr.entry_time:   # only CLOSED trades are known
            done = by_exit[xi]
            key_s = f"{done.strategy}|{done.asset_class}"
            strat[key_s].add(done.hit_tp1, done.net_pct)
            reg[(key_s, done.regime)].add(done.hit_tp1, done.net_pct)
            base.add(done.hit_tp1, done.net_pct)
            xi += 1
        key_s = f"{tr.strategy}|{tr.asset_class}"
        if strat[key_s].n >= 10:                       # need a minimal history before predicting
            preds.append(_predict(strat[key_s], reg[(key_s, tr.regime)]))
            base_preds.append((base.k + 1) / (base.n + 2))
            outs.append(1.0 if tr.hit_tp1 else 0.0)
    return calibration_report(np.array(preds), np.array(outs), np.array(base_preds))


def calibration_report(p: np.ndarray, y: np.ndarray, base: np.ndarray) -> dict:
    if p.size == 0:
        return {"oos_predictions": 0, "insufficient": True}
    eps = 1e-6
    brier = float(np.mean((p - y) ** 2))
    brier_base = float(np.mean((base - y) ** 2))
    ll = float(-np.mean(y * np.log(np.clip(p, eps, 1)) + (1 - y) * np.log(np.clip(1 - p, eps, 1))))
    bins = np.linspace(0, 1, 11)
    table, ece = [], 0.0
    for lo, hi in zip(bins[:-1], bins[1:]):
        m = (p >= lo) & (p < hi if hi < 1 else p <= hi)
        if m.sum() == 0:
            continue
        mp, my = float(p[m].mean()), float(y[m].mean())
        ece += m.sum() / p.size * abs(mp - my)
        table.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": int(m.sum()), "predicted": mp, "observed": my})
    return {"oos_predictions": int(p.size), "brier": brier, "brier_baseline": brier_base,
            "brier_skill": (1 - brier / brier_base) if brier_base > 0 else None,
            "log_loss": ll, "ece": float(ece), "reliability": table,
            "observed_rate": float(y.mean()), "insufficient": p.size < MIN_SAMPLES}


class ProbabilityModel:
    """Fitted on all historical simulated trades; queried for live setups."""

    def __init__(self, trades: list[SimTrade]):
        self.strat: dict[str, Counts] = defaultdict(Counts)
        self.reg: dict[tuple, Counts] = defaultdict(Counts)
        self.trades = trades
        for tr in trades:
            k = f"{tr.strategy}|{tr.asset_class}"
            self.strat[k].add(tr.hit_tp1, tr.net_pct)
            self.reg[(k, tr.regime)].add(tr.hit_tp1, tr.net_pct)
        self.validation: dict[str, dict] = {}
        groups: dict[str, list[SimTrade]] = defaultdict(list)
        for tr in trades:
            groups[f"{tr.strategy}|{tr.asset_class}"].append(tr)
        for k, g in groups.items():
            self.validation[k] = walk_forward(g)

    def estimate(self, strategy: str, asset_class: str, regime: str) -> dict:
        k = f"{strategy}|{asset_class}"
        s, r = self.strat.get(k, Counts()), self.reg.get((k, regime), Counts())
        val = self.validation.get(k, {"oos_predictions": 0, "insufficient": True})
        out = {
            "historical_samples": s.n, "regime_samples": r.n, "regime": regime,
            "historical_win_rate": (s.k / s.n) if s.n else None,
            "historical_win_rate_ci90": wilson(s.k, s.n),
            "regime_win_rate": (r.k / r.n) if r.n else None,
            "expected_net_pct": None, "calibrated_probability": None, "calibrated_ci90": None,
            "validation": {kk: val.get(kk) for kk in ("oos_predictions", "brier", "brier_baseline", "brier_skill", "ece", "observed_rate")},
            "insufficient": True, "reason": None,
        }
        if s.n < MIN_SAMPLES:
            out["reason"] = f"only {s.n} historical setups for {strategy} on {asset_class} (minimum {MIN_SAMPLES})"
            return out
        p = _predict(s, r)
        n_eff = r.n + SHRINK_M
        out["calibrated_probability"] = p
        out["calibrated_ci90"] = wilson(round(p * n_eff), round(n_eff))
        # shrink expected net return toward the strategy mean in the same way
        mean_s = s.net_sum / s.n
        out["expected_net_pct"] = (r.net_sum + SHRINK_M * mean_s) / (r.n + SHRINK_M)
        out["insufficient"] = bool(val.get("insufficient", True))
        if out["insufficient"]:
            out["reason"] = "too few out-of-sample predictions to verify calibration"
        elif (val.get("brier_skill") or 0) < 0:
            out["reason"] = "walk-forward calibration is worse than the no-skill baseline"
        return out
