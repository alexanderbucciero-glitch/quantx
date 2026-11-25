"""Evidence-based strategy selection (the self-improvement loop).

Unit of decision: a combo = strategy × asset class × timeframe × direction.

Tiers
  active      : backtest mean net return per trade has a 90% CI entirely above 0 (n ≥ 30), and the most
                recent half of the history is also positive.                         → normal risk
  exploration : not proven but not disproven: mean ≥ 0, the 90% CI upper bound is above 0, n ≥ 10, and the recent
                half is not negative. Traded autonomously with small risk to gather live evidence.
  disabled    : the 90% CI is entirely below 0, the recent half is negative, or too few samples.

Live paper results are tracked SEPARATELY from backtests and can only change a tier with enough
samples (never because of one trade):
  demote to disabled  : ≥ 10 live trades and the live 90% CI upper bound < 0
  promote to active   : ≥ 20 live trades, live 90% CI lower bound > 0 and the backtest tier is not disabled
Every tier change is stored with its evidence; later reports compare performance before and after it.

The selection rule itself is validated walk-forward: history is cut into chronological folds; for
each fold the tier is decided from EARLIER folds only, and the result of trading that fold with those
tiers is recorded. This measures whether evidence-based selection works out of sample.
"""
from __future__ import annotations

import time
from collections import defaultdict

import numpy as np

from ..core import db
from .backtest import SimTrade

MIN_EXPLORE, MIN_ACTIVE = 10, 30
LIVE_DEMOTE_N, LIVE_PROMOTE_N = 10, 20
Z90 = 1.645


def stats(net: list[float]) -> dict:
    n = len(net)
    if n == 0:
        return {"n": 0, "mean": None, "lo90": None, "hi90": None, "win_rate": None}
    a = np.asarray(net, dtype=float)
    if n == 1:   # a single observation has no measurable uncertainty: no confidence interval
        return {"n": 1, "mean": float(a[0]), "se": None, "lo90": None, "hi90": None, "win_rate": float(a[0] > 0)}
    se = float(a.std(ddof=1) / np.sqrt(n))
    return {"n": n, "mean": float(a.mean()), "se": se, "lo90": float(a.mean() - Z90 * se),
            "hi90": float(a.mean() + Z90 * se), "win_rate": float((a > 0).mean())}


def tier_from(net_chrono: list[float]) -> tuple[str, str, dict]:
    st = stats(net_chrono)
    n = st["n"]
    if n < MIN_EXPLORE:
        return "disabled", f"only {n} historical setups (need {MIN_EXPLORE})", st
    recent = stats(net_chrono[n // 2:])
    st["recent_mean"] = recent["mean"]
    if n >= MIN_ACTIVE and st["lo90"] > 0 and recent["mean"] > 0:
        return "active", f"mean {st['mean']:+.2f}%/trade, 90% CI [{st['lo90']:+.2f}, {st['hi90']:+.2f}] above zero", st
    if st["hi90"] > 0 and st["mean"] >= 0 and recent["mean"] >= 0:   # never explore a setup whose best estimate is a loss
        return "exploration", (f"unproven: mean {st['mean']:+.2f}%/trade, 90% CI [{st['lo90']:+.2f}, {st['hi90']:+.2f}] "
                               f"includes zero; recent half {recent['mean']:+.2f}%"), st
    why = (f"90% CI [{st['lo90']:+.2f}, {st['hi90']:+.2f}] below zero" if st["hi90"] <= 0
           else f"recent half negative ({recent['mean']:+.2f}%/trade)")
    return "disabled", f"mean {st['mean']:+.2f}%/trade; {why}", st


def walk_forward_selection(trades: list[SimTrade], folds: int = 4) -> dict:
    """Out-of-sample test of the selection rule itself."""
    if len(trades) < 40:
        return {"folds": 0, "insufficient": True}
    tr = sorted(trades, key=lambda x: x.entry_time)
    t0, t1 = tr[0].entry_time, tr[-1].entry_time
    edges = np.linspace(t0, t1 + 1, folds + 1)
    out, picked_all, all_oos = [], [], []
    for k in range(1, folds):
        train = [x for x in tr if x.exit_time < edges[k]]                 # only trades CLOSED before the fold
        test = [x for x in tr if edges[k] <= x.entry_time < edges[k + 1]]
        by = defaultdict(list)
        for x in train:
            by[x.combo].append(x.net_pct)
        tiers = {c: tier_from(v)[0] for c, v in by.items()}
        picked = [x.net_pct for x in test if tiers.get(x.combo) in ("active", "exploration")]
        out.append({"fold": k, "train_trades": len(train), "test_trades": len(test),
                    "selected_trades": len(picked), "selected_mean_net_pct": float(np.mean(picked)) if picked else None,
                    "all_mean_net_pct": float(np.mean([x.net_pct for x in test])) if test else None})
        picked_all += picked
        all_oos += [x.net_pct for x in test]
    return {"folds": folds - 1, "by_fold": out, "selected": stats(picked_all), "unselected_baseline": stats(all_oos),
            "insufficient": len(picked_all) < MIN_EXPLORE}


class Selector:
    def __init__(self, account: str = "live"):
        self.account = account
        self.backtest: dict[str, dict] = {}       # combo -> {tier, reason, stats}
        self.validation: dict = db.kv_get(f"selection_validation:{account}") or {}
        self.tiers: dict[str, dict] = db.kv_get(f"tiers:{account}") or {}

    def fit(self, trades: list[SimTrade]) -> None:
        by = defaultdict(list)
        for x in sorted(trades, key=lambda x: x.entry_time):
            by[x.combo].append(x.net_pct)
        self.backtest = {}
        for c, v in by.items():
            tier, reason, st = tier_from(v)
            self.backtest[c] = {"tier": tier, "reason": reason, "stats": st}
        self.validation = walk_forward_selection(trades)
        db.kv_set(f"selection_validation:{self.account}", self.validation)
        self.refresh()

    def live_stats(self) -> dict[str, dict]:
        rows = db.rows(db.text("SELECT strategy, asset_class, tf, direction, realized_pnl, entry, qty FROM positions "
                               "WHERE account=:a AND status='closed' ORDER BY closed_at"), {"a": self.account})
        by = defaultdict(list)
        for r in rows:
            if r["entry"] and r["qty"]:
                by[f"{r['strategy']}|{r['asset_class']}|{r['tf']}|{r['direction']}"].append(100 * r["realized_pnl"] / (r["entry"] * r["qty"]))
        return {c: stats(v) for c, v in by.items()}

    def refresh(self, now: float | None = None) -> list[dict]:
        """Recompute tiers from backtest + live evidence; persist and log every change."""
        live = self.live_stats()
        changes = []
        combos = set(self.backtest) | set(live)
        new: dict[str, dict] = {}
        for c in combos:
            bt = self.backtest.get(c, {"tier": "disabled", "reason": "no backtest evidence", "stats": stats([])})
            tier, reason = bt["tier"], f"backtest: {bt['reason']}"
            lv = live.get(c, stats([]))
            if lv["n"] >= LIVE_DEMOTE_N and (lv["hi90"] or 0) < 0 and tier != "disabled":
                tier, reason = "disabled", f"live paper results negative: {lv['n']} trades, mean {lv['mean']:+.2f}%, 90% CI upper {lv['hi90']:+.2f}%"
            elif lv["n"] >= LIVE_PROMOTE_N and (lv["lo90"] or 0) > 0 and bt["tier"] != "disabled":
                tier, reason = "active", f"live paper results positive: {lv['n']} trades, 90% CI [{lv['lo90']:+.2f}, {lv['hi90']:+.2f}]%"
            new[c] = {"tier": tier, "reason": reason, "backtest": bt["stats"], "backtest_tier": bt["tier"], "live": lv}
            old = self.tiers.get(c, {}).get("tier")
            if old is not None and old != tier:
                changes.append({"combo": c, "old_tier": old, "new_tier": tier, "reason": reason,
                                "evidence": {"backtest": bt["stats"], "live": lv}})
        self.tiers = new
        db.kv_set(f"tiers:{self.account}", new)
        if changes:
            with db.engine().begin() as conn:
                for ch in changes:
                    conn.execute(db.insert(db.strategy_changes).values(account=self.account, ts=now or time.time(), **ch))
        return changes

    def tier(self, strategy: str, asset_class: str, tf: str, direction: str) -> tuple[str, str, dict]:
        c = f"{strategy}|{asset_class}|{tf}|{direction}"
        t = self.tiers.get(c)
        if not t:
            return "disabled", "no historical evidence for this strategy/market/timeframe/direction yet", {}
        return t["tier"], t["reason"], t

    def failing(self) -> bool:
        """True when trades picked by the selection rule lost money out of sample (90% CI below zero)."""
        sel = (self.validation or {}).get("selected") or {}
        return bool(sel.get("n", 0) >= MIN_EXPLORE and sel.get("hi90") is not None and sel["hi90"] < 0)

    def warning(self) -> str | None:
        if not self.failing():
            return None
        sel = self.validation["selected"]
        return (f"Selection rule failed its own out-of-sample test: setups picked by past evidence averaged "
                f"{sel['mean']:+.2f}%/trade (n={sel['n']}). Exploration trades are forward tests, not expected winners.")

    def summary(self) -> dict:
        by = defaultdict(int)
        for v in self.tiers.values():
            by[v["tier"]] += 1
        return {"counts": dict(by), "combos": self.tiers, "selection_validation": self.validation}


