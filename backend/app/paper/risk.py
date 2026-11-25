"""Pre-trade risk checks and position sizing. Every rejection carries a plain-English reason that is
logged with the decision. Exits are never blocked by risk limits."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sqlalchemy import text

from ..core import db
from .broker import Broker


@dataclass
class RiskDecision:
    ok: bool
    reason: str
    qty: float = 0.0
    notional: float = 0.0
    risk_usd: float = 0.0
    risk_pct: float = 0.0


def entries_suspended(b: Broker, now: float) -> str | None:
    """Portfolio-level circuit breakers (daily loss, drawdown). Existing positions are still managed."""
    st, s = b.settings, b.state()
    day0 = now - (now % 86400)
    with db.engine().connect() as c:
        day_start = c.execute(text("SELECT equity FROM equity WHERE account=:a AND ts >= :d ORDER BY ts LIMIT 1"),
                              {"a": b.account, "d": day0}).scalar()
    ref = day_start or s["starting_balance"]
    if s["equity"] < ref * (1 - st["daily_loss_limit_pct"] / 100):
        return f"daily loss limit reached ({100 * (s['equity'] / ref - 1):.2f}% today, limit −{st['daily_loss_limit_pct']}%)"
    dd = 100 * (s["equity"] / s["peak_equity"] - 1) if s["peak_equity"] else 0.0
    if dd < -st["max_drawdown_pct"]:
        return f"maximum drawdown reached ({dd:.2f}% from peak, limit −{st['max_drawdown_pct']}%)"
    return None


def correlation(a: np.ndarray, b: np.ndarray) -> float | None:
    n = min(a.size, b.size)
    if n < 30:
        return None
    ra, rb = np.diff(np.log(a[-n:])), np.diff(np.log(b[-n:]))
    if ra.std() == 0 or rb.std() == 0:
        return None
    return float(np.corrcoef(ra, rb)[0, 1])


def check_entry(b: Broker, *, asset: str, asset_class: str, direction: str, entry: float, stop: float, tier: str,
                now: float, closes: dict[str, np.ndarray] | None = None) -> RiskDecision:
    st = b.settings
    s = b.state()
    if tier not in ("active", "exploration"):
        return RiskDecision(False, f"strategy evidence tier is '{tier}'")
    if tier == "exploration" and not st["exploration_enabled"]:
        return RiskDecision(False, "exploration trading is switched off in settings")
    if direction == "short" and (not st["allow_short"] or asset_class == "memecoin"):
        return RiskDecision(False, "short selling not allowed for this asset/settings")
    halted = entries_suspended(b, now)
    if halted:
        return RiskDecision(False, f"new entries suspended: {halted}")
    if b.positions("open", asset) or b.positions("pending", asset):
        return RiskDecision(False, "duplicate prevented: a position or pending entry already exists in this asset")
    if s["open_positions"] + len(b.positions("pending")) >= st["max_open_positions"]:
        return RiskDecision(False, f"maximum open positions ({st['max_open_positions']}) reached")
    with db.engine().connect() as c:
        last_exit = c.execute(text("SELECT MAX(closed_at) FROM positions WHERE account=:a AND asset=:s AND status='closed'"),
                              {"a": b.account, "s": asset}).scalar()
        n_hour = c.execute(text("SELECT COUNT(*) FROM orders WHERE account=:a AND purpose='entry' AND created_at >= :t"),
                           {"a": b.account, "t": now - 3600}).scalar()
        n_day = c.execute(text("SELECT COUNT(*) FROM orders WHERE account=:a AND purpose='entry' AND created_at >= :t"),
                          {"a": b.account, "t": now - 86400}).scalar()
    if last_exit and now - last_exit < st["cooldown_minutes"] * 60:
        return RiskDecision(False, f"cooldown: exited this asset {int((now - last_exit) / 60)} min ago (minimum {st['cooldown_minutes']})")
    if n_hour >= st["max_entries_per_hour"]:
        return RiskDecision(False, f"overtrading protection: {n_hour} entries in the last hour")
    if n_day >= st["max_entries_per_day"]:
        return RiskDecision(False, f"overtrading protection: {n_day} entries in the last 24 h")
    if closes and asset in closes:
        same = [p for p in b.positions("open") if p["direction"] == direction and p["asset"] in closes]
        corr = [(p["display"] or p["asset"], correlation(closes[asset], closes[p["asset"]])) for p in same]
        high = [(n, r) for n, r in corr if r is not None and r > 0.7]
        if len(high) >= st["max_correlated_positions"]:
            return RiskDecision(False, "correlation limit: already " + ", ".join(f"{n} (ρ={r:.2f})" for n, r in high)
                                + f" in the same direction")
    risk_pct = st["risk_per_trade_pct"] if tier == "active" else st["exploration_risk_pct"]
    risk_usd = s["equity"] * risk_pct / 100
    if abs(entry - stop) <= 0:
        return RiskDecision(False, "invalid stop distance")
    # risk = distance to stop PLUS round-trip fees and slippage, so a stopped-out trade loses ≈ the budget
    f = st["fees"].get(asset_class) or st["fees"]["crypto"]
    cost_frac = (f["maker"] if st["entry_order"] == "limit" else f["taker"]) / 100 + f["taker"] / 100 + 0.001
    per_unit = abs(entry - stop) + entry * cost_frac
    qty = risk_usd / per_unit
    cap_pct = st["max_memecoin_position_pct"] if asset_class == "memecoin" else st["max_position_pct"]
    qty = min(qty, s["equity"] * cap_pct / 100 / entry)
    notional = qty * entry
    if s["exposure"] + notional > s["equity"] * st["max_gross_exposure_pct"] / 100:
        room = s["equity"] * st["max_gross_exposure_pct"] / 100 - s["exposure"]
        if room <= entry * qty * 0.25:
            return RiskDecision(False, f"gross exposure limit {st['max_gross_exposure_pct']}% reached")
        qty = room / entry
        notional = room
    if direction == "long" and notional > s["available"]:
        return RiskDecision(False, f"insufficient available capital (${s['available']:.2f})")
    return RiskDecision(True, "within limits", qty, notional, qty * per_unit, 100 * qty * per_unit / s["equity"])
