"""Trading desk: the autonomous decision core shared by the live agent and historical replay.

on_candle (a bar closed for one asset/timeframe):
  1. manage open positions in that asset: strategy exit signal → SELL/COVER at market, otherwise HOLD
  2. evaluate every strategy on the closed bar
  3. for each signal: evidence tier → plan (targets or trailing) → risk/sizing → decision
  4. accepted → submit a simulated order to the broker (limit or market); every decision is persisted
on_quote (live price):
  broker fills resting orders, stops, targets, trailing stops and time exits; each closed position
  gets a trade report and the live evidence for its strategy combo is refreshed.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import numpy as np

from ..analysis.structure import Analysis, market_structure_label, plain
from ..core import clock, db
from ..market.candles import TF_SECONDS, Series
from ..paper import risk
from ..paper.broker import Broker, BrokerError, Quote
from ..quant import backtest
from ..quant.selector import Selector
from ..quant.targets import PlanRejection
from ..strategies.library import BY_NAME, STRATEGIES
from . import reports

log = logging.getLogger("quantx.desk")

Emit = Callable[..., Awaitable[None]]


@dataclass
class AssetCtx:
    symbol: str
    display: str
    asset_class: str
    volume_24h_usd: float | None = None
    liquidity_usd: float | None = None
    spread_pct: float | None = None
    excluded: str | None = None
    names: list[str] = field(default_factory=list)


MAX_HOLD = {"5m": 48, "15m": 64, "1h": 48, "6h": 40, "1d": 30}


async def _noop(*a, **k) -> None:
    return None


class Desk:
    def __init__(self, account: str, broker: Broker, selector: Selector, emit: Emit | None = None):
        self.account = account
        self.broker = broker
        self.selector = selector
        self.emit = emit or _noop
        self.entries_enabled = True
        self.counters: dict[str, int] = {}
        self.last_decision: dict | None = None

    def _count(self, k: str, n: int = 1) -> None:
        self.counters[k] = self.counters.get(k, 0) + n

    # ------------------------------------------------------------------ quotes → fills
    async def on_quote(self, asset: str, q: Quote) -> list[dict]:
        events = self.broker.on_quote(asset, q)
        for ev in events:
            await self._after_fill(ev)
        return events

    async def _after_fill(self, ev: dict) -> None:
        k = ev["kind"]
        if k == "entry_expired":
            await self.emit(f"Entry limit for {ev['asset']} expired unfilled — no position opened", "decision", asset=ev["asset"],
                            account=self.account)
            return
        if k == "entry":
            p = db.rows(db.select(db.positions).where(db.positions.c.id == ev["position_id"]))[0]
            self._count("trades_executed")
            await self.emit(f"EXECUTED {'BUY' if p['direction'] == 'long' else 'SELL SHORT'} {p['display']}: "
                            f"{ev['qty']:.6g} @ {ev['price']:.6g} ({ev['liquidity']}, fee {ev['fee']:.2f} USD) — "
                            f"stop {p['stop']:.6g}" + (f", TP1 {p['tp1']:.6g}" if p['tp1'] else ", trailing exit"),
                            "trade", asset=p["display"], strategy=p["strategy"], account=self.account, data=plain(ev))
            return
        verb = "SELL" if ev["side"] == "sell" else "BUY TO COVER"
        self._count("exits")
        await self.emit(f"EXECUTED {verb} {ev.get('display') or ev['asset']} ({ev['kind'].replace('_', ' ')}): {ev['qty']:.6g} @ {ev['price']:.6g}, "
                        f"net {ev['net']:+.2f} USD" + (" — position closed" if ev["closed"] else ""), "trade",
                        asset=ev.get("display") or ev["asset"], account=self.account, data=plain(ev))
        if ev["closed"]:
            rep = reports.trade_report(ev["position_id"])
            self._count("closed_trades")
            await self.emit(f"Trade report: {rep['asset']} {rep['strategy']} {rep['direction']} → net {rep['net_pnl']:+.2f} USD "
                            f"({rep['net_pct']:+.2f}%), {rep['exit_reason']}. {rep['verdict']}", "report",
                            asset=rep["asset"], strategy=rep["strategy"], account=self.account,
                            data={"position_id": ev["position_id"]})
            changes = self.selector.refresh(clock.now())
            for ch in changes:
                await self.emit(f"Strategy selection updated: {ch['combo']} {ch['old_tier']} → {ch['new_tier']} ({ch['reason']})",
                                "learning", account=self.account, data=plain(ch))

    # ------------------------------------------------------------------ candle close → decisions
    async def on_candle(self, a: AssetCtx, s: Series, an: Analysis, q: Quote | None,
                        closes: dict[str, np.ndarray] | None = None, news: list[dict] | None = None) -> dict:
        t = len(s) - 1
        out = {"signals": 0, "accepted": 0, "rejected": 0, "exits": 0, "holds": 0}
        now = clock.now()
        # 1. manage open positions opened on this timeframe
        for p in self.broker.positions("open", a.symbol):
            strat = BY_NAME.get(p["strategy"])
            if p["tf"] != s.tf or strat is None:
                continue
            why = strat.should_exit(s, an, t, p["direction"])
            if why and q is not None:
                ev = self.broker.close_position(p["id"], q, "signal_exit", why)
                out["exits"] += 1
                await self._after_fill(ev)
                self.last_decision = {"ts": now, "asset": a.display, "decision": "SELL" if p["direction"] == "long" else "COVER", "reason": why}
            else:
                out["holds"] += 1
                upnl = ((q.bid if p["direction"] == "long" else q.ask) - p["entry"]) * p["remaining_qty"] * (1 if p["direction"] == "long" else -1) if q else None
                await self.emit(f"HOLD {a.display} {p['direction']} ({p['strategy']}): exit conditions not met"
                                + (f"; unrealised {upnl:+.2f} USD, stop {p['stop']:.6g}" if upnl is not None else ""),
                                "decision", asset=a.display, strategy=p["strategy"], account=self.account)
        # 2. entries
        for strat in STRATEGIES:
            sig = strat.evaluate(s, an, t)
            if sig is None:
                continue
            out["signals"] += 1
            self._count("opportunities_identified")
            accepted = await self._decide(a, s, an, sig, strat, q, closes, news or [], now)
            out["accepted" if accepted else "rejected"] += 1
        return out

    async def _decide(self, a: AssetCtx, s: Series, an: Analysis, sig, strat, q: Quote | None,
                      closes, news: list[dict], now: float) -> bool:
        st = self.broker.settings
        t = sig.t
        tf = s.tf
        client_key = f"{self.account}:{a.symbol}:{tf}:{sig.strategy}:{sig.direction}:{int(s.t[t])}"
        if db.rows(db.select(db.opportunities.c.id).where(db.opportunities.c.client_key == client_key)):
            return False                                   # this exact setup was already decided (restart-safe)
        tier, tier_reason, tier_info = self.selector.tier(sig.strategy, a.asset_class, tf, sig.direction)
        bt = (tier_info or {}).get("backtest") or {}
        fees = st["fees"].get(a.asset_class, st["fees"]["crypto"])
        max_hold = MAX_HOLD.get(tf, 48) * (2 if sig.exit_mode == "trail" else 1)
        plan_obj, ce = backtest.plan_for(s, an, sig, 1000.0, max_hold, a.liquidity_usd, a.spread_pct, st["targets_pct"],
                                         None, a.volume_24h_usd, fees, strat.trail_atr, st["min_rr"])
        rej = None
        if isinstance(plan_obj, PlanRejection):
            plan, rej = None, {"reason": plan_obj.reason, "detail": plan_obj.detail}
        else:
            plan = plan_obj if isinstance(plan_obj, dict) else {**plan_obj.to_json(), "exit_mode": "targets"}
            plan.setdefault("atr", float(an.atr[t]))
        price = q.last if q else float(s.c[t])
        gates = [
            {"gate": "Closed candle", "passed": True, "detail": f"{tf} candle closed {_hm(int(s.t[t]) + s.step)} UTC"},
            {"gate": "Tradeable market", "passed": a.excluded is None, "detail": a.excluded or "liquidity, spread and data quality OK"},
            {"gate": "Strategy evidence", "passed": tier in ("active", "exploration"), "detail": f"tier {tier.upper()}: {tier_reason}"},
            {"gate": "Viable plan", "passed": plan is not None,
             "detail": (f"stop {plan['stop']:.6g} ({plan['stop_pct']:.2f}%), " +
                        (f"TP1 +{plan['tp1_pct']:.2f}% / R:R {plan['rr_net']:.2f} after costs" if plan.get("tp1") else f"trailing {plan['trail_atr']}×ATR exit")
                        if plan else rej["reason"].replace("_", " "))},
            {"gate": "Live quote", "passed": q is not None, "detail": "bid/ask available" if q else "no live quote"},
        ]
        rd = None
        if all(g["passed"] for g in gates):
            if not self.entries_enabled:
                gates.append({"gate": "Agent accepting entries", "passed": False, "detail": "new entries are switched off (agent deactivated)"})
            else:
                rd = risk.check_entry(self.broker, asset=a.symbol, asset_class=a.asset_class, direction=sig.direction,
                                      entry=q.ask if sig.direction == "long" else q.bid, stop=plan["stop"], tier=tier,
                                      now=now, closes=closes)
                gates.append({"gate": "Risk & position sizing", "passed": rd.ok,
                              "detail": (f"{rd.qty:.6g} units ≈ ${rd.notional:,.2f}, risking ${rd.risk_usd:,.2f} ({rd.risk_pct:.2f}% of equity)"
                                         if rd.ok else rd.reason)})
        accepted = all(g["passed"] for g in gates)
        exp_net = bt.get("mean")
        p_win = bt.get("win_rate")
        ev_text = "no backtest estimate" if exp_net is None else f"{exp_net:+.2f}% per trade after costs (backtest, n={bt.get('n')})"
        report = _report(a, s, an, sig, strat, plan, rej, tier, tier_reason, bt, gates, price, news, accepted, ev_text, rd)
        failed = next((g for g in gates if not g["passed"]), None)
        with db.engine().begin() as c:
            oid = c.execute(db.insert(db.opportunities).values(
                ts=now, account=self.account, asset=a.display, asset_class=a.asset_class, strategy=sig.strategy,
                direction=sig.direction, status="accepted" if accepted else "rejected",
                reason=None if accepted else f"{failed['gate']}: {failed['detail']}", report=report,
                client_key=client_key)).inserted_primary_key[0]
        self.last_decision = {"ts": now, "asset": a.display, "strategy": sig.strategy,
                              "decision": ("BUY" if sig.direction == "long" else "SELL SHORT") if accepted else "NO TRADE",
                              "reason": "all gates passed" if accepted else f"{failed['gate']}: {failed['detail']}"}
        if not accepted:
            self._count("opportunities_rejected")
            await self.emit(f"NO TRADE {a.display} {strat.label} {sig.direction} ({tf}): {failed['gate']} — {failed['detail']}",
                            "decision", asset=a.display, strategy=sig.strategy, account=self.account, data={"opportunity_id": oid})
            return False
        order_type = st["entry_order"]
        limit_px = float(s.c[t]) if order_type == "limit" else None
        try:
            res = self.broker.submit_entry(
                asset=a.symbol, display=a.display, asset_class=a.asset_class, strategy=sig.strategy, tf=tf, direction=sig.direction,
                qty=rd.qty, order_type=order_type, quote=q, limit_price=limit_px, plan=plan,
                max_hold_s=max_hold * TF_SECONDS[tf], tier=tier, client_key=client_key, report=report, opportunity_id=oid,
                expires_s=st["entry_limit_bars"] * TF_SECONDS[tf], expected_net_pct=exp_net, p_success=p_win)
        except BrokerError as e:
            await self.emit(f"Order rejected by broker for {a.display}: {e}", "decision", "warn", asset=a.display, account=self.account)
            return False
        self._count("orders_submitted")
        await self.emit(f"DECISION {'BUY' if sig.direction == 'long' else 'SELL SHORT'} {a.display} — {strat.label} ({tf}, {tier} tier): "
                        f"{order_type} order for {rd.qty:.6g} " + (f"@ {limit_px:.6g}" if limit_px else "at market")
                        + f", risking {rd.risk_pct:.2f}% of equity", "decision", asset=a.display, strategy=sig.strategy,
                        account=self.account, data={"opportunity_id": oid, "order": plain(res)})
        if res.get("status") == "filled":
            await self._after_fill({"kind": "entry", "asset": a.symbol, **res})
        return True

    # ------------------------------------------------------------------ controls
    async def flatten(self, quotes: dict[str, Quote], kind: str, reason: str) -> list[dict]:
        out = []
        for o in self.broker.open_orders(purpose="entry"):
            self.broker.cancel_order(o["id"], reason)
        for p in self.broker.positions("open"):
            q = quotes.get(p["asset"])
            if q is None:
                await self.emit(f"{p['display']}: no live price — position stays protected by its stop order", "system", "warn",
                                asset=p["display"], account=self.account)
                continue
            ev = self.broker.close_position(p["id"], q, kind, reason)
            out.append(ev)
            await self._after_fill(ev)
        return out


def _hm(ts: int) -> str:
    import time as _t
    return _t.strftime("%Y-%m-%d %H:%M", _t.gmtime(ts))


def _report(a: AssetCtx, s: Series, an: Analysis, sig, strat, plan, rej, tier, tier_reason, bt, gates, price, news,
            accepted, ev_text, rd) -> dict:
    t = sig.t
    long = sig.direction == "long"
    atr_pct = 100 * an.atr[t] / s.c[t] if not math.isnan(an.atr[t]) else None
    rv = an.rel_volume[t]
    recent = [d for d in an.recent(24, len(s)) if d.kind in ("bos", "choch", "liquidity_sweep", "fvg", "order_block", "failed_breakout")]
    tech = {"rsi": _f(an.rsi[t]), "macd_hist": _f(an.macd_hist[t]), "ema20": _f(an.ema20[t]), "ema50": _f(an.ema50[t]),
            "bb_upper": _f(an.bb_upper[t]), "bb_lower": _f(an.bb_lower[t]), "atr_pct": _f(atr_pct), "rel_volume": _f(rv)}
    failed = next((g for g in gates if not g["passed"]), None)
    lines = [f"{a.display} ({a.asset_class}, {s.tf}) — {strat.label} {sig.direction.upper()} setup.",
             f"Trigger: {(sig.bullish if long else sig.bearish or ['rule conditions met'])[0] if (sig.bullish or sig.bearish) else 'rule conditions met'}.",
             f"Structure: {market_structure_label(an.swings)}; last break {an.trend}; RSI {tech['rsi'] or 0:.1f}; "
             f"MACD hist {tech['macd_hist'] or 0:+.3g}; ATR {tech['atr_pct'] or 0:.2f}%/bar.",
             f"Evidence tier {tier.upper()} ({tier_reason}). Expected {ev_text}."]
    if plan:
        lines.append(f"Plan: entry ≈ {plan['entry']:.6g}, stop {plan['stop']:.6g}" +
                     (f", TP1 {plan['tp1']:.6g}, TP2 {plan['tp2']:.6g}" if plan.get("tp1") else f", trailing stop {plan['trail_atr']}×ATR + EMA20 exit") + ".")
    lines.append("Decision: " + ("execute simulated order." if accepted else f"no trade — {failed['gate']}: {failed['detail']}."))
    return plain({
        "asset": a.display, "symbol": a.symbol, "asset_class": a.asset_class, "timeframe": s.tf, "price": price,
        "timestamp": clock.now(), "candle_time": int(s.t[t]), "direction": sig.direction, "strategy": sig.strategy,
        "strategy_label": strat.label, "plan": plan, "plan_rejection": rej, "tier": tier, "tier_reason": tier_reason,
        "technical": tech, "market_structure": {"trend": an.trend, "label": market_structure_label(an.swings),
                                                "recent": [d.to_json() for d in recent[-8:]], "pools": an.pools},
        "liquidity": {"volume_24h_usd": a.volume_24h_usd, "liquidity_usd": a.liquidity_usd, "spread_pct": a.spread_pct},
        "news": news[:5], "bullish_evidence": sig.bullish[:10], "bearish_evidence": sig.bearish[:10],
        "invalidation": [f"stop at {plan['stop']:.6g}" if plan else "n/a", f"structural level {sig.invalidation:.6g}" if sig.invalidation else "—"],
        "confidence": {"ai_confidence": sig.score, "ai_confidence_note": "Heuristic strength of the rule evidence (0–1), not a probability.",
                       "historical_win_rate": bt.get("win_rate"), "historical_samples": bt.get("n"),
                       "expected_net_pct": bt.get("mean"), "expected_net_ci90": [bt.get("lo90"), bt.get("hi90")] if bt.get("n") else None,
                       "calibrated_probability": None,
                       "reason": "Probability shown as the historical win rate of this exact strategy/market/timeframe/direction (backtest).",
                       "disclaimer": "Historical simulated results do not guarantee future outcomes."},
        "sizing": None if rd is None or not rd.ok else {"qty": rd.qty, "notional": rd.notional, "risk_usd": rd.risk_usd, "risk_pct": rd.risk_pct},
        "gates": gates, "accepted": accepted, "explanation": " ".join(lines),
        "volume_24h_usd": a.volume_24h_usd, "liquidity_usd": a.liquidity_usd,
    })


def _f(x) -> float | None:
    try:
        return None if x is None or math.isnan(float(x)) else float(x)
    except (TypeError, ValueError):
        return None
