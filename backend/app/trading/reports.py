"""Automatic reports, built ONLY from recorded fills, positions and equity snapshots.

trade report   : written the moment a simulated position closes
daily report   : UTC day; generated automatically after midnight and on demand
weekly/monthly : ISO week / calendar month, same contents aggregated
"""
from __future__ import annotations

import calendar
import datetime as dt
import time
from collections import defaultdict

from sqlalchemy import text

from ..core import clock, db

EXIT_LABEL = {"tp1": "take-profit 1 filled (whole position)", "tp2": "take-profit 2 filled", "stop": "stop-loss hit",
              "breakeven": "break-even stop hit after TP1", "trailing_stop": "trailing stop hit", "time_exit": "maximum holding time reached",
              "signal_exit": "strategy exit signal", "manual": "closed manually", "emergency_stop": "emergency stop",
              "deactivate_flatten": "closed on deactivation (flatten mode)"}


def _dur(s: float) -> str:
    if s < 3600:
        return f"{s / 60:.0f} min"
    if s < 86400:
        return f"{s / 3600:.1f} h"
    return f"{s / 86400:.1f} days"


def trade_report(position_id: int) -> dict:
    p = db.rows(db.select(db.positions).where(db.positions.c.id == position_id))[0]
    fl = db.rows(db.select(db.fills).where(db.fills.c.position_id == position_id).order_by(db.fills.c.ts))
    entry_rep = p["report"] or {}
    notional = p["entry"] * p["qty"]
    gross, fees, net = p["gross_pnl"] or 0.0, p["fees"] or 0.0, p["realized_pnl"] or 0.0
    net_pct = 100 * net / notional if notional else 0.0
    hold = (p["closed_at"] or clock.now()) - (p["opened_at"] or 0)
    exits = [f for f in fl if f["kind"] != "entry"]
    long = p["direction"] == "long"
    mfe, mae = p["mfe_pct"] or 0.0, p["mae_pct"] or 0.0
    target_pct = (abs(p["tp1"] / p["entry"] - 1) * 100) if p["tp1"] else None
    stop_pct = abs(p["initial_stop"] / p["entry"] - 1) * 100 if p["initial_stop"] else None
    # was the original analysis correct? = did price move the predicted way enough to reach the first objective
    if p["tp1"]:
        correct = bool(p["tp1_done"]) or mfe >= target_pct
        verdict = ("Correct: price reached the first target." if correct else
                   f"Not correct: best move in favour was {mfe:+.2f}% versus a {target_pct:.2f}% target.")
    else:
        correct = (gross > 0)
        verdict = ("Correct: the trend move exceeded costs." if net > 0 else
                   "Direction right but gain smaller than costs." if gross > 0 else
                   f"Not correct: the trend did not follow through (best {mfe:+.2f}%, worst {mae:+.2f}%).")
    lessons = []
    if p["exit_reason"] == "stop" and hold < 3 * 3600 and mfe < 0.3 * (target_pct or 1):
        lessons.append("Stopped quickly with almost no favourable move: entry timing or the trigger was weak in this regime.")
    if p["exit_reason"] in ("stop", "trailing_stop") and stop_pct and mae < -stop_pct * 0.95 and mfe >= 0.8 * (target_pct or 99):
        lessons.append("Price came close to the target before reversing: a smaller first target would have locked in a gain.")
    if p["exit_reason"] == "time_exit":
        lessons.append("The move never developed within the holding limit: the target may be too far for current volatility.")
    if p["exit_reason"] == "breakeven":
        lessons.append("TP1 was banked and the runner stopped at break-even: risk management worked as designed.")
    if gross > 0 and net <= 0:
        lessons.append(f"Gross profit {gross:+.2f} was consumed by {fees:.2f} in fees: the edge is smaller than the costs for this setup.")
    if p["tier"] == "exploration":
        lessons.append("Exploration trade (small size): its result feeds the live evidence for this strategy combination.")
    if not lessons:
        lessons.append("No specific execution issue identified; the result is consistent with the strategy's historical distribution.")
    exp = p["expected_net_pct"]
    body = {
        "position_id": p["id"], "account": p["account"], "asset": p["display"] or p["asset"], "asset_class": p["asset_class"],
        "strategy": p["strategy"], "timeframe": p["tf"], "direction": p["direction"], "evidence_tier": p["tier"],
        "entry_reason": entry_rep.get("explanation") or "; ".join(entry_rep.get("bullish_evidence", []) if long else entry_rep.get("bearish_evidence", [])),
        "entry_time": p["opened_at"], "exit_time": p["closed_at"], "entry_price": p["entry"], "exit_price": p["exit_avg"],
        "position_size": p["qty"], "notional_usd": notional, "holding_period": _dur(hold), "holding_seconds": hold,
        "gross_pnl": gross, "trading_costs": fees, "slippage_cost": p["slippage_cost"], "net_pnl": net, "net_pct": net_pct,
        "exit_reason": EXIT_LABEL.get(p["exit_reason"], p["exit_reason"]), "fills": exits,
        "max_favourable_pct": mfe, "max_adverse_pct": mae,
        "expected_net_pct": exp, "expected_vs_actual": None if exp is None else f"expected {exp:+.2f}%, actual {net_pct:+.2f}%",
        "analysis_correct": correct, "verdict": verdict, "improvements": lessons,
        "simulated": True,
    }
    with db.engine().begin() as c:
        c.execute(db.update(db.positions).where(db.positions.c.id == p["id"]).values(trade_report=body))
        c.execute(db.insert(db.reports).values(account=p["account"], kind="trade", period_start=p["opened_at"],
                                               period_end=p["closed_at"], position_id=p["id"], created_at=clock.now(), body=body))
    return body


def _period(kind: str, ref: float) -> tuple[float, float, str]:
    d = dt.datetime.fromtimestamp(ref, dt.timezone.utc)
    if kind == "daily":
        start = dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc)
        end = start + dt.timedelta(days=1)
        label = start.strftime("%Y-%m-%d")
    elif kind == "weekly":
        start = dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc) - dt.timedelta(days=d.weekday())
        end = start + dt.timedelta(days=7)
        label = f"week {start.isocalendar()[1]} {start.year}"
    else:
        start = dt.datetime(d.year, d.month, 1, tzinfo=dt.timezone.utc)
        end = start + dt.timedelta(days=calendar.monthrange(d.year, d.month)[1])
        label = start.strftime("%B %Y")
    return start.timestamp(), end.timestamp(), label


def period_report(account: str, kind: str, ref: float | None = None, events: list[dict] | None = None,
                  store: bool = True) -> dict:
    ref = ref or clock.now()
    start, end, label = _period(kind, ref)
    end_eff = min(end, clock.now())
    with db.engine().connect() as c:
        eq_start = c.execute(text("SELECT equity FROM equity WHERE account=:a AND ts <= :s ORDER BY ts DESC LIMIT 1"), {"a": account, "s": start}).scalar()
        if eq_start is None:
            eq_start = c.execute(text("SELECT equity FROM equity WHERE account=:a AND ts >= :s ORDER BY ts LIMIT 1"), {"a": account, "s": start}).scalar()
        if eq_start is None:
            eq_start = c.execute(text("SELECT starting FROM cash WHERE account=:a"), {"a": account}).scalar()
        eq_end = c.execute(text("SELECT equity FROM equity WHERE account=:a AND ts <= :e ORDER BY ts DESC LIMIT 1"), {"a": account, "e": end_eff}).scalar()
        curve = [r[0] for r in c.execute(text("SELECT equity FROM equity WHERE account=:a AND ts >= :s AND ts <= :e ORDER BY ts"),
                                         {"a": account, "s": start, "e": end_eff})]
        unreal = c.execute(text("SELECT unrealized FROM equity WHERE account=:a AND ts <= :e ORDER BY ts DESC LIMIT 1"), {"a": account, "e": end_eff}).scalar()
    closed = db.rows(text("SELECT * FROM positions WHERE account=:a AND status='closed' AND closed_at >= :s AND closed_at < :e ORDER BY closed_at"),
                     {"a": account, "s": start, "e": end})
    opened = db.rows(text("SELECT COUNT(*) AS n FROM positions WHERE account=:a AND opened_at >= :s AND opened_at < :e"),
                     {"a": account, "s": start, "e": end})[0]["n"]
    open_now = db.rows(text("SELECT id, display, asset, direction, strategy, entry, last_price, remaining_qty, stop, tp1, opened_at FROM positions "
                            "WHERE account=:a AND status='open'"), {"a": account})
    fills_n = db.rows(text("SELECT COUNT(*) AS n FROM fills WHERE account=:a AND ts >= :s AND ts < :e"), {"a": account, "s": start, "e": end})[0]["n"]
    pnl = [p["realized_pnl"] for p in closed]
    wins, losses = [x for x in pnl if x > 0], [x for x in pnl if x <= 0]
    by = defaultdict(lambda: {"trades": 0, "net": 0.0, "wins": 0})
    for p in closed:
        b = by[p["strategy"]]
        b["trades"] += 1
        b["net"] += p["realized_pnl"]
        b["wins"] += int(p["realized_pnl"] > 0)
    peak, mdd = None, 0.0
    for e in curve:
        peak = e if peak is None else max(peak, e)
        mdd = min(mdd, e / peak - 1)
    best = max(by.items(), key=lambda kv: kv[1]["net"])[0] if by else None
    worst = min(by.items(), key=lambda kv: kv[1]["net"])[0] if by else None
    eq_end = eq_end if eq_end is not None else eq_start
    total_ret = 100 * (eq_end / eq_start - 1) if eq_start else 0.0
    realized = sum(pnl)
    pf = (sum(wins) / -sum(losses)) if losses and sum(losses) < 0 else None
    if not closed and not opened:
        assess = ("No trades this period: the agent found no setup that passed its evidence and risk gates. "
                  "Standing aside is a decision, not a failure.")
    else:
        parts = [f"{len(closed)} trade(s) closed with net realised P&L {realized:+.2f} USD ({total_ret:+.2f}% portfolio change)."]
        if closed:
            parts.append(f"Win rate {100 * len(wins) / len(closed):.0f}%" + (f", profit factor {pf:.2f}." if pf is not None else "."))
        if len(closed) < 20:
            parts.append("The sample is too small to judge skill; results are dominated by chance at this size.")
        parts.append(f"Best strategy: {best}; worst: {worst}." if best else "")
        assess = " ".join(x for x in parts if x)
    body = {
        "kind": kind, "label": label, "account": account, "period_start": start, "period_end": end, "complete": clock.now() >= end,
        "starting_value": eq_start, "ending_value": eq_end, "realized_pnl": realized, "unrealized_pnl": unreal or 0.0,
        "total_return_pct": total_ret, "trades_opened": opened, "trades_closed": len(closed), "fills": fills_n,
        "winning_trades": len(wins), "losing_trades": len(losses), "win_rate": (len(wins) / len(closed)) if closed else None,
        "avg_win": (sum(wins) / len(wins)) if wins else None, "avg_loss": (sum(losses) / len(losses)) if losses else None,
        "profit_factor": pf, "max_drawdown_pct": 100 * mdd, "by_strategy": dict(by),
        "best_strategy": best, "worst_strategy": worst, "open_positions": open_now,
        "market_events": (events or [])[:10], "assessment": assess, "simulated": True,
        "trade_ids": [p["id"] for p in closed],
    }
    if store:
        with db.engine().begin() as c:
            c.execute(db.delete(db.reports).where(db.reports.c.account == account, db.reports.c.kind == kind,
                                                  db.reports.c.period_start == start))
            c.execute(db.insert(db.reports).values(account=account, kind=kind, period_start=start, period_end=end,
                                                   created_at=clock.now(), body=db.jsonable(body)))
    return body


def list_reports(account: str, kind: str | None = None, limit: int = 100) -> list[dict]:
    q = db.select(db.reports.c.id, db.reports.c.kind, db.reports.c.period_start, db.reports.c.period_end,
                  db.reports.c.position_id, db.reports.c.created_at, db.reports.c.body).where(db.reports.c.account == account)
    if kind:
        q = q.where(db.reports.c.kind == kind)
    out = []
    for r in db.rows(q.order_by(db.reports.c.period_end.desc(), db.reports.c.id.desc()).limit(limit)):
        b = r.pop("body") or {}
        r["summary"] = ({"asset": b.get("asset"), "strategy": b.get("strategy"), "net_pnl": b.get("net_pnl"),
                         "exit_reason": b.get("exit_reason"), "analysis_correct": b.get("analysis_correct")} if r["kind"] == "trade"
                        else {"label": b.get("label"), "total_return_pct": b.get("total_return_pct"), "trades_closed": b.get("trades_closed"),
                              "realized_pnl": b.get("realized_pnl")})
        out.append(r)
    return out


def get_report(rid: int) -> dict | None:
    r = db.rows(db.select(db.reports).where(db.reports.c.id == rid))
    return r[0] if r else None


def last_completed_day(now: float | None = None) -> float:
    now = now or time.time()
    return now - 86400
