"""Paper-account and risk settings (persisted). Conservative defaults."""
from __future__ import annotations

from ..core import db

DEFAULT_SETTINGS = {
    "starting_balance": 10_000.0,
    # risk per trade, % of equity lost if the stop is hit
    "risk_per_trade_pct": 0.5,            # strategies in the evidence tier "active"
    "exploration_risk_pct": 0.1,          # strategies in "exploration" (not yet proven, not disproven)
    "exploration_enabled": True,
    "max_open_positions": 6,
    "max_position_pct": 20.0,             # notional cap per position, % of equity
    "max_memecoin_position_pct": 2.0,
    "max_gross_exposure_pct": 80.0,
    "daily_loss_limit_pct": 2.0,          # suspend NEW entries after this loss today (UTC)
    "max_drawdown_pct": 10.0,             # suspend NEW entries below this drawdown from peak equity
    "max_correlated_positions": 2,        # same-direction positions with return correlation > 0.7
    "max_entries_per_hour": 6,
    "max_entries_per_day": 30,
    "cooldown_minutes": 60,               # no re-entry in the same asset right after an exit
    "allow_short": True,
    "entry_order": "limit",               # limit (maker, may not fill) | market (taker)
    "entry_limit_bars": 2,                # unfilled entry limits expire after this many bars
    "min_rr": 1.2,
    "deactivate_mode": "manage",          # manage: keep managing open positions | flatten: close them at market
    "targets_pct": [0.75, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0],
    "fees": {                             # % per side; Kraken Pro base tier ≈ 0.25 maker / 0.40 taker
        "crypto": {"maker": 0.25, "taker": 0.40},
        "memecoin": {"maker": 0.30, "taker": 0.30},
        "fx": {"maker": 0.25, "taker": 0.40},
        "commodity": {"maker": 0.25, "taker": 0.40},
        "equity": {"maker": 0.0, "taker": 0.01},
    },
}


def get_settings() -> dict:
    s = db.kv_get("paper_settings") or {}
    out = {**DEFAULT_SETTINGS, **s}
    # older saved format: fees as a single number per class
    if any(not isinstance(v, dict) for v in out["fees"].values()):
        out["fees"] = DEFAULT_SETTINGS["fees"]
    for k, v in DEFAULT_SETTINGS["fees"].items():
        out["fees"].setdefault(k, v)
    return out


def update_settings(patch: dict) -> dict:
    cur = get_settings()
    for k, v in patch.items():
        if k not in DEFAULT_SETTINGS:
            raise ValueError(f"unknown setting {k}")
        cur[k] = v
    if not (0 < float(cur["risk_per_trade_pct"]) <= 2):
        raise ValueError("risk_per_trade_pct must be between 0 and 2")
    if not (0 <= float(cur["exploration_risk_pct"]) <= float(cur["risk_per_trade_pct"])):
        raise ValueError("exploration_risk_pct must be between 0 and risk_per_trade_pct")
    if cur["entry_order"] not in ("limit", "market"):
        raise ValueError("entry_order must be 'limit' or 'market'")
    if cur["deactivate_mode"] not in ("manage", "flatten"):
        raise ValueError("deactivate_mode must be 'manage' or 'flatten'")
    db.kv_set("paper_settings", cur)
    return cur
