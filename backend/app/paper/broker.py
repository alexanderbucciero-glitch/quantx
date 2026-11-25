"""Simulated broker: orders, fills, positions and cash for one paper account.

SIMULATION ONLY — QUANTX has no brokerage, exchange account or wallet connection and no code path
that can send a real order.

Order types
  market : fills immediately against the live quote (buy at ASK, sell at BID) + modelled slippage, taker fee
  limit  : rests until the market trades through the limit (buy when ASK <= limit, sell when BID >= limit),
           fills AT the limit price, maker fee. Unfilled entry limits expire.
  stop   : triggers when BID (long exit) / ASK (short exit) crosses the stop; fills at the worse of the stop
           and the current price (gaps are not given the stop price) + slippage, taker fee.

Cash accounting (1x, no leverage)
  long  open : cash -= qty·price + fee         long  close : cash += qty·price − fee
  short open : cash += qty·price − fee         short close : cash -= qty·price + fee
  equity = cash + Σ long qty·price − Σ short qty·price        available = equity − gross exposure
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import text

from ..core import clock, db
from ..quant import costs as cost_model
from .settings import get_settings

log = logging.getLogger("quantx.broker")


@dataclass
class Quote:
    bid: float
    ask: float
    last: float
    ts: float

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2


class BrokerError(Exception):
    pass


class Broker:
    def __init__(self, account: str = "live", settings: dict | None = None):
        self.account = account
        self._settings = settings

    # ------------------------------------------------------------------ account
    @property
    def settings(self) -> dict:
        return self._settings if self._settings is not None else get_settings()

    def ensure_account(self) -> dict:
        with db.engine().begin() as c:
            r = c.execute(db.select(db.cash).where(db.cash.c.account == self.account)).first()
            if r is None:
                start = float(self.settings["starting_balance"])
                c.execute(db.insert(db.cash).values(account=self.account, starting=start, cash=start,
                                                    peak_equity=start, created_at=clock.now()))
                return {"starting": start, "cash": start, "peak_equity": start}
            return dict(r._mapping)

    def _cash_add(self, c, delta: float) -> None:
        c.execute(db.update(db.cash).where(db.cash.c.account == self.account).values(cash=db.cash.c.cash + delta))

    def positions(self, status: str = "open", asset: str | None = None) -> list[dict]:
        q = db.select(db.positions).where(db.positions.c.account == self.account, db.positions.c.status == status)
        if asset:
            q = q.where(db.positions.c.asset == asset)
        return db.rows(q.order_by(db.positions.c.id))

    def open_orders(self, asset: str | None = None, purpose: str | None = None) -> list[dict]:
        q = db.select(db.orders).where(db.orders.c.account == self.account, db.orders.c.status == "open")
        if asset:
            q = q.where(db.orders.c.asset == asset)
        if purpose:
            q = q.where(db.orders.c.purpose == purpose)
        return db.rows(q.order_by(db.orders.c.id))

    def state(self) -> dict:
        acct = self.ensure_account()
        opens = self.positions()
        long_val = short_val = unreal = 0.0
        for p in opens:
            px = p["last_price"] or p["entry"]
            if p["direction"] == "long":
                long_val += px * p["remaining_qty"]
                unreal += (px - p["entry"]) * p["remaining_qty"]
            else:
                short_val += px * p["remaining_qty"]
                unreal += (p["entry"] - px) * p["remaining_qty"]
        equity = acct["cash"] + long_val - short_val
        gross = long_val + short_val
        with db.engine().connect() as c:
            realized = c.execute(text("SELECT COALESCE(SUM(realized_pnl),0) FROM positions WHERE account=:a"), {"a": self.account}).scalar() or 0.0
            fees = c.execute(text("SELECT COALESCE(SUM(fee),0) FROM fills WHERE account=:a"), {"a": self.account}).scalar() or 0.0
            pending = c.execute(text("SELECT COALESCE(SUM(qty*limit_price),0) FROM orders WHERE account=:a AND status='open' AND purpose='entry'"),
                                {"a": self.account}).scalar() or 0.0
        # realized_pnl on open positions includes the entry fee and any partial exits already booked
        return {"account": self.account, "starting_balance": acct["starting"], "cash": acct["cash"], "equity": equity,
                "unrealized": unreal, "realized": realized, "fees_paid": fees, "exposure": gross,
                "long_exposure": long_val, "short_exposure": short_val,
                "available": max(0.0, equity - gross - pending), "reserved_for_orders": pending,
                "peak_equity": max(acct["peak_equity"] or equity, equity), "open_positions": len(opens), "simulated": True}

    # ------------------------------------------------------------------ fills
    def _fee_pct(self, asset_class: str, liquidity: str) -> float:
        f = self.settings["fees"].get(asset_class) or self.settings["fees"]["crypto"]
        return float(f["maker"] if liquidity == "maker" else f["taker"])

    def _slip_pct(self, p_or_meta: dict, notional: float) -> float:
        meta = p_or_meta.get("report") or p_or_meta.get("meta") or {}
        ce = cost_model.estimate(p_or_meta["asset_class"], notional, 0.0, meta.get("volume_24h_usd"), meta.get("liquidity_usd"))
        return ce.slippage_pct_round_trip / 2

    def _record_fill(self, c, *, position: dict, order_id: int | None, kind: str, side: str, price: float, ref: float,
                     qty: float, fee: float, liquidity: str, reason: str, ts: float) -> int:
        return c.execute(db.insert(db.fills).values(
            account=self.account, position_id=position["id"], order_id=order_id, ts=ts, asset=position["asset"],
            display=position.get("display") or position["asset"], strategy=position["strategy"], kind=kind, side=side,
            price=price, ref_price=ref, qty=qty, fee=fee, slippage=abs(price - ref) * qty, liquidity=liquidity,
            reason=reason)).inserted_primary_key[0]

    # ------------------------------------------------------------------ entries
    def submit_entry(self, *, asset: str, display: str, asset_class: str, strategy: str, tf: str, direction: str,
                     qty: float, order_type: str, quote: Quote, limit_price: float | None, plan: dict, max_hold_s: float,
                     tier: str, client_key: str, report: dict, opportunity_id: int | None, expires_s: float,
                     expected_net_pct: float | None = None, p_success: float | None = None) -> dict:
        """Create the position record (status 'pending' until filled) and its entry order."""
        if qty <= 0:
            raise BrokerError("position size is zero")
        self.ensure_account()                       # the cash ledger must exist before the first fill
        side = "buy" if direction == "long" else "sell"
        now = quote.ts
        meta = {"volume_24h_usd": report.get("volume_24h_usd"), "liquidity_usd": report.get("liquidity_usd")}
        with db.engine().begin() as c:
            if c.execute(db.select(db.orders.c.id).where(db.orders.c.client_key == client_key)).first():
                raise BrokerError(f"duplicate order rejected ({client_key})")
            pid = c.execute(db.insert(db.positions).values(
                account=self.account, asset=asset, display=display, asset_class=asset_class, strategy=strategy, tf=tf,
                direction=direction, tier=tier, status="pending", opened_at=None, entry=None, qty=qty, remaining_qty=0.0,
                stop=plan["stop"], initial_stop=plan["stop"], tp1=plan.get("tp1"), tp2=plan.get("tp2"), tp1_done=0,
                trail_atr=plan.get("trail_atr"), atr=plan.get("atr"), max_hold_s=max_hold_s, fees=0.0, slippage_cost=0.0,
                realized_pnl=0.0, gross_pnl=0.0, expected_net_pct=expected_net_pct, p_success=p_success,
                opportunity_id=opportunity_id, report={**report, **meta})).inserted_primary_key[0]
            oid = c.execute(db.insert(db.orders).values(
                account=self.account, client_key=client_key, asset=asset, asset_class=asset_class, position_id=pid,
                purpose="entry", side=side, type=order_type, qty=qty, limit_price=limit_price, status="open",
                created_at=now, expires_at=now + expires_s if order_type == "limit" else None,
                reason=f"{strategy} {direction} entry", meta=meta)).inserted_primary_key[0]
        if order_type == "market":
            return self._fill_entry(oid, quote, liquidity="taker")
        res = self._try_fill_entry_limit(oid, quote)
        return res or {"order_id": oid, "position_id": pid, "status": "open", "limit_price": limit_price}

    def _fill_entry(self, order_id: int, q: Quote, liquidity: str, price: float | None = None) -> dict:
        with db.engine().begin() as c:
            o = dict(c.execute(db.select(db.orders).where(db.orders.c.id == order_id)).first()._mapping)
            p = dict(c.execute(db.select(db.positions).where(db.positions.c.id == o["position_id"])).first()._mapping)
            ref = q.ask if o["side"] == "buy" else q.bid
            if price is None:
                slip = self._slip_pct({"asset_class": o["asset_class"], "meta": o["meta"]}, ref * o["qty"])
                price = ref * (1 + slip / 100) if o["side"] == "buy" else ref * (1 - slip / 100)
            qty = o["qty"]
            fee = price * qty * self._fee_pct(o["asset_class"], liquidity) / 100
            self._cash_add(c, -(price * qty + fee) if o["side"] == "buy" else (price * qty - fee))
            c.execute(db.update(db.orders).where(db.orders.c.id == order_id).values(
                status="filled", filled_at=q.ts, fill_price=price, ref_price=ref, fee=fee, liquidity=liquidity))
            c.execute(db.update(db.positions).where(db.positions.c.id == p["id"]).values(
                status="open", opened_at=q.ts, entry=price, remaining_qty=qty, fees=fee, realized_pnl=-fee,
                slippage_cost=abs(price - ref) * qty, last_price=q.last, last_quote_ts=q.ts, extreme=price))
            p.update(entry=price, remaining_qty=qty, opened_at=q.ts)
            self._record_fill(c, position=p, order_id=order_id, kind="entry", side=o["side"], price=price, ref=ref, qty=qty,
                              fee=fee, liquidity=liquidity, reason=o["reason"], ts=q.ts)
            exit_side = "sell" if o["side"] == "buy" else "buy"
            c.execute(db.insert(db.orders).values(
                account=self.account, client_key=f"{o['client_key']}:stop", asset=o["asset"], asset_class=o["asset_class"],
                position_id=p["id"], purpose="stop", side=exit_side, type="stop", qty=qty, stop_price=p["stop"],
                status="open", created_at=q.ts, reason="protective stop", meta=o["meta"]))
            if p["tp1"]:
                tp1_qty = qty * (0.5 if p["tp2"] and p["tp2"] != p["tp1"] else 1.0)
                c.execute(db.insert(db.orders).values(
                    account=self.account, client_key=f"{o['client_key']}:tp1", asset=o["asset"], asset_class=o["asset_class"],
                    position_id=p["id"], purpose="tp1", side=exit_side, type="limit", qty=tp1_qty, limit_price=p["tp1"],
                    status="open", created_at=q.ts, reason="take profit 1", meta=o["meta"]))
        return {"order_id": order_id, "position_id": p["id"], "status": "filled", "price": price, "qty": qty, "fee": fee,
                "liquidity": liquidity, "notional": price * qty}

    def _try_fill_entry_limit(self, order_id: int, q: Quote) -> dict | None:
        o = db.rows(db.select(db.orders).where(db.orders.c.id == order_id))[0]
        lim = o["limit_price"]
        if (o["side"] == "buy" and q.ask <= lim) or (o["side"] == "sell" and q.bid >= lim):
            return self._fill_entry(order_id, q, liquidity="maker", price=lim)
        return None

    def cancel_order(self, order_id: int, reason: str, ts: float | None = None) -> None:
        with db.engine().begin() as c:
            o = c.execute(db.select(db.orders).where(db.orders.c.id == order_id)).first()
            if not o or o.status != "open":
                return
            c.execute(db.update(db.orders).where(db.orders.c.id == order_id).values(status="cancelled", reason=f"{o.reason}; cancelled: {reason}"))
            if o.purpose == "entry":
                c.execute(db.update(db.positions).where(db.positions.c.id == o.position_id).values(
                    status="cancelled", closed_at=ts or clock.now(), exit_reason=reason))

    # ------------------------------------------------------------------ exits
    def _exit(self, c, p: dict, qty: float, price: float, ref: float, kind: str, liquidity: str, ts: float,
              order_id: int | None, reason: str) -> dict:
        side = "sell" if p["direction"] == "long" else "buy"
        fee = price * qty * self._fee_pct(p["asset_class"], liquidity) / 100
        self._cash_add(c, (price * qty - fee) if side == "sell" else -(price * qty + fee))
        sign = 1 if p["direction"] == "long" else -1
        gross = sign * (price - p["entry"]) * qty
        remaining = p["remaining_qty"] - qty
        vals = {"remaining_qty": max(0.0, remaining), "realized_pnl": p["realized_pnl"] + gross - fee,
                "gross_pnl": (p["gross_pnl"] or 0) + gross, "fees": p["fees"] + fee,
                "slippage_cost": (p["slippage_cost"] or 0) + abs(price - ref) * qty, "last_price": ref, "last_quote_ts": ts}
        self._record_fill(c, position=p, order_id=order_id, kind=kind, side=side, price=price, ref=ref, qty=qty, fee=fee,
                          liquidity=liquidity, reason=reason, ts=ts)
        closed = remaining <= 1e-12 * max(1.0, p["qty"])
        if closed:
            avg = c.execute(text("SELECT SUM(price*qty)/SUM(qty) FROM fills WHERE position_id=:p AND kind!='entry'"),
                            {"p": p["id"]}).scalar()
            vals.update(status="closed", closed_at=ts, exit_avg=avg, remaining_qty=0.0, exit_reason=kind)
            c.execute(db.update(db.orders).where(db.orders.c.position_id == p["id"], db.orders.c.status == "open").values(status="cancelled"))
        elif kind == "tp1":
            vals.update(tp1_done=1, stop=p["entry"])            # runner: stop to break-even
            c.execute(db.update(db.orders).where(db.orders.c.position_id == p["id"], db.orders.c.purpose == "stop",
                                                 db.orders.c.status == "open").values(qty=remaining, stop_price=p["entry"]))
            if p["tp2"] and p["tp2"] != p["tp1"]:
                o = c.execute(db.select(db.orders.c.client_key).where(db.orders.c.position_id == p["id"], db.orders.c.purpose == "entry")).first()
                c.execute(db.insert(db.orders).values(
                    account=self.account, client_key=f"{o.client_key}:tp2", asset=p["asset"], asset_class=p["asset_class"],
                    position_id=p["id"], purpose="tp2", side=side, type="limit", qty=remaining, limit_price=p["tp2"],
                    status="open", created_at=ts, reason="take profit 2", meta={}))
        c.execute(db.update(db.positions).where(db.positions.c.id == p["id"]).values(**vals))
        p.update(vals)
        return {"position_id": p["id"], "asset": p["asset"], "display": p.get("display"), "kind": kind, "side": side,
                "price": price, "qty": qty, "gross": gross, "fee": fee, "net": gross - fee, "closed": closed,
                "realized_pnl": vals["realized_pnl"], "reason": reason}

    def _mark_order(self, c, p: dict, purpose: str, ts: float, price: float, fee_liq: str) -> int | None:
        r = c.execute(db.select(db.orders.c.id).where(db.orders.c.position_id == p["id"], db.orders.c.purpose == purpose,
                                                      db.orders.c.status == "open")).first()
        if r:
            c.execute(db.update(db.orders).where(db.orders.c.id == r.id).values(status="filled", filled_at=ts, fill_price=price, liquidity=fee_liq))
            return r.id
        return None

    def on_quote(self, asset: str, q: Quote) -> list[dict]:
        """Process every resting order and open position in `asset` against a new quote."""
        events: list[dict] = []
        for o in self.open_orders(asset, "entry"):
            if o["expires_at"] and q.ts >= o["expires_at"]:
                self.cancel_order(o["id"], "entry limit expired unfilled", q.ts)
                events.append({"kind": "entry_expired", "order_id": o["id"], "asset": asset, "position_id": o["position_id"]})
                continue
            r = self._try_fill_entry_limit(o["id"], q)
            if r:
                events.append({"kind": "entry", **r, "asset": asset})
        opens = self.positions("open", asset)
        if not opens:
            return events
        with db.engine().begin() as c:
            for p in opens:
                long = p["direction"] == "long"
                exit_ref = q.bid if long else q.ask
                fav = (q.bid / p["entry"] - 1) if long else (1 - q.ask / p["entry"])
                mfe, mae = max(p["mfe_pct"] or 0, 100 * fav), min(p["mae_pct"] or 0, 100 * fav)
                extreme = max(p["extreme"] or p["entry"], q.bid) if long else min(p["extreme"] or p["entry"], q.ask)
                upd = {"last_price": q.last, "last_quote_ts": q.ts, "mfe_pct": mfe, "mae_pct": mae, "extreme": extreme}
                # trailing stop (trend strategies): ratchet only in the favourable direction
                if p["trail_atr"] and p["atr"]:
                    trail = extreme - p["trail_atr"] * p["atr"] if long else extreme + p["trail_atr"] * p["atr"]
                    if (long and trail > p["stop"]) or (not long and trail < p["stop"]):
                        upd["stop"] = trail
                        c.execute(db.update(db.orders).where(db.orders.c.position_id == p["id"], db.orders.c.purpose == "stop",
                                                             db.orders.c.status == "open").values(stop_price=trail))
                c.execute(db.update(db.positions).where(db.positions.c.id == p["id"]).values(**upd))
                p.update(upd)
                stop_hit = exit_ref <= p["stop"] if long else exit_ref >= p["stop"]
                if stop_hit:
                    ref = min(p["stop"], exit_ref) if long else max(p["stop"], exit_ref)
                    slip = self._slip_pct(p, ref * p["remaining_qty"])
                    px = ref * (1 - slip / 100) if long else ref * (1 + slip / 100)
                    kind = "stop" if not p["tp1_done"] else "breakeven"
                    if p["trail_atr"] and ((p["stop"] > p["entry"]) if long else (p["stop"] < p["entry"])):
                        kind = "trailing_stop"            # the stop had trailed into profit
                    oid = self._mark_order(c, p, "stop", q.ts, px, "taker")
                    events.append(self._exit(c, p, p["remaining_qty"], px, exit_ref, kind, "taker", q.ts, oid,
                                             f"stop order triggered at {p['stop']:.6g}"))
                    continue
                if p["tp1"] and not p["tp1_done"] and ((q.bid >= p["tp1"]) if long else (q.ask <= p["tp1"])):
                    qty = p["remaining_qty"] * (0.5 if p["tp2"] and p["tp2"] != p["tp1"] else 1.0)
                    oid = self._mark_order(c, p, "tp1", q.ts, p["tp1"], "maker")
                    events.append(self._exit(c, p, qty, p["tp1"], exit_ref, "tp1", "maker", q.ts, oid, f"take-profit limit {p['tp1']:.6g} filled"))
                    if p["status"] == "closed":
                        continue
                elif p["tp1_done"] and p["tp2"] and ((q.bid >= p["tp2"]) if long else (q.ask <= p["tp2"])):
                    oid = self._mark_order(c, p, "tp2", q.ts, p["tp2"], "maker")
                    events.append(self._exit(c, p, p["remaining_qty"], p["tp2"], exit_ref, "tp2", "maker", q.ts, oid, f"take-profit limit {p['tp2']:.6g} filled"))
                    continue
                if p["status"] == "open" and p["max_hold_s"] and q.ts - p["opened_at"] >= p["max_hold_s"]:
                    events.append(self._market_exit(c, p, q, "time_exit", "maximum holding time reached"))
        return events

    def _market_exit(self, c, p: dict, q: Quote, kind: str, reason: str) -> dict:
        long = p["direction"] == "long"
        ref = q.bid if long else q.ask
        slip = self._slip_pct(p, ref * p["remaining_qty"])
        px = ref * (1 - slip / 100) if long else ref * (1 + slip / 100)
        c.execute(db.insert(db.orders).values(
            account=self.account, client_key=f"pos{p['id']}:{kind}:{q.ts}", asset=p["asset"], asset_class=p["asset_class"],
            position_id=p["id"], purpose="exit", side="sell" if long else "buy", type="market", qty=p["remaining_qty"],
            status="filled", created_at=q.ts, filled_at=q.ts, fill_price=px, ref_price=ref, liquidity="taker", reason=reason, meta={}))
        return self._exit(c, p, p["remaining_qty"], px, ref, kind, "taker", q.ts, None, reason)

    def close_position(self, position_id: int, q: Quote, kind: str, reason: str) -> dict:
        with db.engine().begin() as c:
            r = c.execute(db.select(db.positions).where(db.positions.c.id == position_id, db.positions.c.account == self.account)).first()
            if not r or r.status != "open":
                raise BrokerError("position is not open")
            return self._market_exit(c, dict(r._mapping), q, kind, reason)

    def snapshot_equity(self, ts: float | None = None) -> dict:
        s = self.state()
        ts = ts or clock.now()
        with db.engine().begin() as c:
            c.execute(db.insert(db.equity).values(account=self.account, ts=ts, equity=s["equity"], cash=s["cash"],
                                                  unrealized=s["unrealized"], exposure=s["exposure"]))
            c.execute(db.update(db.cash).where(db.cash.c.account == self.account).values(peak_equity=s["peak_equity"]))
        return s
