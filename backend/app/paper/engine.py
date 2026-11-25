"""Paper-trading engine. Everything here is SIMULATED: no broker, exchange account or wallet exists
anywhere in QUANTX, and there is no code path that could send a real order.

Fill model: longs buy at the live ASK and sell at the BID (shorts the reverse), plus modelled
slippage/impact, plus a taker fee on every fill. Stops fill at the first price beyond the stop
(gaps are not given the stop price). 50% is closed at TP1, the stop then moves to break-even,
the rest closes at TP2, the stop, or the time limit.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy import text

from ..core import db
from ..quant import costs as cost_model

DEFAULT_SETTINGS = {
    "starting_balance": 10_000.0,
    "risk_per_trade_pct": 0.5,          # equity lost if the stop is hit
    "max_open_positions": 5,
    "max_position_pct": 20.0,           # notional cap per position, % of equity
    "max_memecoin_position_pct": 2.0,
    "max_gross_exposure_pct": 60.0,
    "daily_loss_limit_pct": 2.0,        # stop opening positions after this daily loss
    "allow_short": True,
    "targets_pct": [2.0, 3.0, 4.0, 5.0],
    "min_rr": 1.2,
    "min_expected_net_pct": 0.05,
    "require_calibration": True,
    "fees": {"crypto": 0.40, "memecoin": 0.30, "equity": 0.01},
}


def get_settings() -> dict:
    s = db.kv_get("paper_settings") or {}
    return {**DEFAULT_SETTINGS, **s}


def update_settings(patch: dict) -> dict:
    cur = get_settings()
    allowed = set(DEFAULT_SETTINGS)
    for k, v in patch.items():
        if k not in allowed:
            raise ValueError(f"unknown setting {k}")
        cur[k] = v
    if not (0 < float(cur["risk_per_trade_pct"]) <= 2):
        raise ValueError("risk_per_trade_pct must be between 0 and 2")
    db.kv_set("paper_settings", cur)
    return cur


@dataclass
class Quote:
    bid: float
    ask: float
    last: float
    ts: float


def _rows(q) -> list[dict]:
    with db.engine().connect() as c:
        return [dict(r._mapping) for r in c.execute(q)]


def open_positions(asset: str | None = None) -> list[dict]:
    q = db.select(db.positions).where(db.positions.c.status == "open")
    if asset:
        q = q.where(db.positions.c.asset == asset)
    return _rows(q)


def account() -> dict:
    st = get_settings()
    with db.engine().connect() as c:
        realized = c.execute(text("SELECT COALESCE(SUM(realized_pnl),0) FROM positions")).scalar() or 0.0
    opens = open_positions()
    unreal, exposure = 0.0, 0.0
    for p in opens:
        px = p["last_price"] or p["entry"]
        sign = 1 if p["direction"] == "long" else -1
        unreal += sign * (px - p["entry"]) * p["remaining_qty"]
        exposure += px * p["remaining_qty"]
    balance = st["starting_balance"] + realized
    return {"starting_balance": st["starting_balance"], "balance": balance, "unrealized": unreal,
            "equity": balance + unreal, "exposure": exposure, "open_positions": len(opens), "simulated": True}


def _fill_price(ref: float, side: str, slip_pct: float) -> float:
    return ref * (1 + slip_pct / 100) if side == "buy" else ref * (1 - slip_pct / 100)


def _slip_pct(asset_class: str, notional: float, liquidity_usd: float | None, vol_24h: float | None) -> float:
    ce = cost_model.estimate(asset_class, notional, 0.0, vol_24h, liquidity_usd)
    return ce.slippage_pct_round_trip / 2          # one side; spread is already in bid/ask


def risk_check(asset: str, asset_class: str, notional: float) -> str | None:
    st = get_settings()
    acc = account()
    if open_positions(asset):
        return "already holding a simulated position in this asset"
    if acc["open_positions"] >= st["max_open_positions"]:
        return f"max open positions reached ({st['max_open_positions']})"
    if acc["exposure"] + notional > acc["equity"] * st["max_gross_exposure_pct"] / 100:
        return f"gross exposure limit {st['max_gross_exposure_pct']}% would be exceeded"
    day0 = time.time() - (time.time() % 86400)
    with db.engine().connect() as c:
        today = c.execute(text("SELECT COALESCE(SUM(realized_pnl),0) FROM positions WHERE closed_at >= :d"), {"d": day0}).scalar() or 0.0
    if today < -acc["equity"] * st["daily_loss_limit_pct"] / 100:
        return f"daily loss limit {st['daily_loss_limit_pct']}% reached"
    return None


def size_position(asset_class: str, entry: float, stop: float) -> tuple[float, float]:
    st = get_settings()
    eq = account()["equity"]
    risk_usd = eq * st["risk_per_trade_pct"] / 100
    per_unit = abs(entry - stop)
    qty = risk_usd / per_unit if per_unit > 0 else 0.0
    cap_pct = st["max_memecoin_position_pct"] if asset_class == "memecoin" else st["max_position_pct"]
    qty = min(qty, eq * cap_pct / 100 / entry)
    return qty, qty * entry


def open_position(asset: str, asset_class: str, strategy: str, direction: str, quote: Quote, plan: dict,
                  max_hold_s: float, report: dict, opportunity_id: int | None,
                  liquidity_usd: float | None = None, vol_24h: float | None = None) -> dict:
    st = get_settings()
    ref = quote.ask if direction == "long" else quote.bid
    qty, notional = size_position(asset_class, ref, plan["stop"])
    if qty <= 0:
        raise ValueError("position size is zero")
    slip = _slip_pct(asset_class, notional, liquidity_usd, vol_24h)
    side = "buy" if direction == "long" else "sell"
    px = _fill_price(ref, side, slip)
    fee = px * qty * st["fees"][asset_class] / 100
    now = time.time()
    report = {**(report or {}), "liquidity_usd": liquidity_usd, "volume_24h_usd": vol_24h}
    with db.engine().begin() as c:
        pid = c.execute(db.insert(db.positions).values(
            asset=asset, asset_class=asset_class, strategy=strategy, direction=direction, status="open",
            opened_at=now, entry=px, qty=qty, remaining_qty=qty, stop=plan["stop"], tp1=plan["tp1"], tp2=plan["tp2"],
            tp1_done=0, max_hold_s=max_hold_s, fees=fee, realized_pnl=-fee, last_price=quote.last,
            opportunity_id=opportunity_id, report=report)).inserted_primary_key[0]
        c.execute(db.insert(db.fills).values(position_id=pid, ts=now, kind="entry", side=side, price=px,
                                             ref_price=ref, qty=qty, fee=fee, slippage=abs(px - ref) * qty))
    return {"id": pid, "price": px, "qty": qty, "notional": px * qty, "fee": fee, "slippage_pct": slip}


def _close(c, p: dict, qty: float, ref: float, kind: str, now: float) -> dict:
    st = get_settings()
    side = "sell" if p["direction"] == "long" else "buy"
    slip = _slip_pct(p["asset_class"], ref * qty, (p["report"] or {}).get("liquidity_usd"), (p["report"] or {}).get("volume_24h_usd"))
    px = _fill_price(ref, side, slip)
    fee = px * qty * st["fees"][p["asset_class"]] / 100
    sign = 1 if p["direction"] == "long" else -1
    pnl = sign * (px - p["entry"]) * qty - fee
    c.execute(db.insert(db.fills).values(position_id=p["id"], ts=now, kind=kind, side=side, price=px, ref_price=ref,
                                         qty=qty, fee=fee, slippage=abs(px - ref) * qty))
    remaining = p["remaining_qty"] - qty
    vals = {"remaining_qty": remaining, "realized_pnl": p["realized_pnl"] + pnl, "fees": p["fees"] + fee, "last_price": ref}
    if remaining <= 1e-12:
        with_exit = c.execute(text("SELECT SUM(price*qty)/SUM(qty) FROM fills WHERE position_id=:p AND kind!='entry'"), {"p": p["id"]}).scalar()
        vals.update(status="closed", closed_at=now, exit_avg=with_exit, remaining_qty=0.0)
    if kind == "tp1":
        vals.update(tp1_done=1, stop=p["entry"])
    c.execute(db.update(db.positions).where(db.positions.c.id == p["id"]).values(**vals))
    p.update(vals)
    return {"position_id": p["id"], "asset": p["asset"], "kind": kind, "price": px, "qty": qty, "pnl": pnl,
            "closed": vals.get("status") == "closed"}


def on_quote(asset: str, q: Quote) -> list[dict]:
    """Check stops/targets/time limits for every open simulated position in `asset`."""
    events = []
    opens = open_positions(asset)
    if not opens:
        return events
    now = q.ts
    with db.engine().begin() as c:
        for p in opens:
            long = p["direction"] == "long"
            exit_ref = q.bid if long else q.ask          # price we could actually exit at
            p["last_price"] = q.last
            stop_hit = exit_ref <= p["stop"] if long else exit_ref >= p["stop"]
            if stop_hit:
                events.append(_close(c, p, p["remaining_qty"], exit_ref, "stop" if not p["tp1_done"] else "breakeven", now))
                continue
            if not p["tp1_done"] and (exit_ref >= p["tp1"] if long else exit_ref <= p["tp1"]):
                half = p["remaining_qty"] * (0.5 if p["tp2"] != p["tp1"] else 1.0)
                events.append(_close(c, p, half, exit_ref, "tp1", now))
                if p["status"] == "closed":
                    continue
            elif p["tp1_done"] and (exit_ref >= p["tp2"] if long else exit_ref <= p["tp2"]):
                events.append(_close(c, p, p["remaining_qty"], exit_ref, "tp2", now))
                continue
            if p["status"] == "open" and now - p["opened_at"] >= p["max_hold_s"]:
                events.append(_close(c, p, p["remaining_qty"], exit_ref, "time", now))
                continue
            c.execute(db.update(db.positions).where(db.positions.c.id == p["id"]).values(last_price=q.last))
    return events


def close_manually(position_id: int, q: Quote) -> dict:
    with db.engine().begin() as c:
        p = dict(c.execute(db.select(db.positions).where(db.positions.c.id == position_id)).first()._mapping)
        if p["status"] != "open":
            raise ValueError("position is not open")
        return _close(c, p, p["remaining_qty"], q.bid if p["direction"] == "long" else q.ask, "manual", q.ts)


def snapshot_equity() -> dict:
    a = account()
    with db.engine().begin() as c:
        c.execute(db.insert(db.equity).values(ts=time.time(), equity=a["equity"], cash=a["balance"],
                                              unrealized=a["unrealized"], exposure=a["exposure"]))
    return a


def metrics() -> dict:
    a = account()
    closed = _rows(db.select(db.positions).where(db.positions.c.status == "closed").order_by(db.positions.c.closed_at))
    eq = _rows(db.select(db.equity).order_by(db.equity.c.ts))
    pnl = [p["realized_pnl"] for p in closed]
    wins = [x for x in pnl if x > 0]
    losses = [x for x in pnl if x <= 0]
    peak, mdd = None, 0.0
    for e in eq:
        peak = e["equity"] if peak is None else max(peak, e["equity"])
        mdd = min(mdd, e["equity"] / peak - 1)
    day_ago = time.time() - 86400
    ref = next((e["equity"] for e in eq if e["ts"] >= day_ago), None)
    by_strategy: dict[str, dict] = {}
    for p in closed:
        s = by_strategy.setdefault(p["strategy"], {"trades": 0, "wins": 0, "pnl": 0.0})
        s["trades"] += 1
        s["wins"] += int(p["realized_pnl"] > 0)
        s["pnl"] += p["realized_pnl"]
    by_class: dict[str, float] = {}
    for p in open_positions():
        by_class[p["asset_class"]] = by_class.get(p["asset_class"], 0) + (p["last_price"] or p["entry"]) * p["remaining_qty"]
    return {
        **a,
        "total_return_pct": 100 * (a["equity"] / a["starting_balance"] - 1),
        "daily_return_pct": 100 * (a["equity"] / ref - 1) if ref else None,
        "closed_trades": len(closed),
        "win_rate": len(wins) / len(closed) if closed else None,
        "profit_factor": (sum(wins) / -sum(losses)) if losses and sum(losses) < 0 else None,
        "max_drawdown_pct": 100 * mdd if eq else None,
        "avg_holding_s": (sum(p["closed_at"] - p["opened_at"] for p in closed) / len(closed)) if closed else None,
        "fees_paid": sum(p["fees"] for p in closed) + sum(p["fees"] for p in open_positions()),
        "by_strategy": by_strategy,
        "exposure_by_class": by_class,
        "equity_curve": [{"ts": e["ts"], "equity": e["equity"]} for e in eq[-2000:]],
        "settings": get_settings(),
    }


def history(limit: int = 200) -> dict:
    closed = _rows(db.select(db.positions).where(db.positions.c.status == "closed").order_by(db.positions.c.closed_at.desc()).limit(limit))
    fl = _rows(db.select(db.fills).order_by(db.fills.c.ts.desc()).limit(limit * 3))
    return {"open": open_positions(), "closed": closed, "fills": fl}


def reset_account() -> None:
    """Archive nothing, delete simulated records. Only called on an explicit user request."""
    with db.engine().begin() as c:
        c.execute(db.delete(db.fills))
        c.execute(db.delete(db.positions))
        c.execute(db.delete(db.equity))
