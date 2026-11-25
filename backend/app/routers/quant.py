"""Agent, market-structure, strategy, backtest and paper-trading endpoints (+ live WebSocket)."""
from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from ..agent.engine import CLASS_CFG, agent
from ..analysis.structure import analyse, market_structure_label, plain
from ..core import db
from ..core.config import settings
from ..core.http import UpstreamError
from ..market import feeds
from ..market.candles import TF_SECONDS, Series
from ..paper import settings as paper_settings
from ..paper.broker import BrokerError, Quote
from ..trading import reports
from ..trading.replay import manager as replay_manager
from ..quant import backtest
from ..quant.probability import ProbabilityModel
from ..strategies.library import BY_NAME, RESEARCH_ONLY, STRATEGIES

router = APIRouter(prefix="/api")


def _bad(e: Exception) -> HTTPException:
    if isinstance(e, UpstreamError):
        return HTTPException(502, {"source": e.source, "error": e.message})
    return HTTPException(400, {"error": str(e)})


# ------------------------------------------------------------------ agent
@router.post("/agent/activate")
async def agent_activate():
    return await agent.activate()


@router.post("/agent/deactivate")
async def agent_deactivate():
    return await agent.deactivate()


@router.get("/agent/status")
async def agent_status():
    return agent.status()


@router.get("/agent/log")
async def agent_log(limit: int = Query(200, le=600)):
    return {"events": list(agent.logbuf)[-limit:]}


@router.get("/agent/news")
async def agent_news():
    """Headlines checked by the agent, linked to scanned assets, with a rule-based relevance note."""
    out = []
    for n in agent.news[:150]:
        if n.get("category") == "central_bank":
            why = "Central-bank communication can move rates, the dollar and risk assets broadly (primary source)."
        elif n.get("assets"):
            why = (f"Mentions {', '.join(n['assets'][:3])}. A possible catalyst; it is unverified until a primary "
                   "source (exchange, project or regulator) confirms it.")
        else:
            why = None
        out.append({**n, "relevance": why})
    return {"items": out, "linked": sum(1 for n in out if n.get("assets")),
            "note": "Media and social claims are never treated as confirmed information."}


@router.get("/agent/assets")
async def agent_assets():
    rows = sorted((a.public() for a in agent.assets.values()), key=lambda r: (r["excluded"] is not None, -r["rank_score"]))
    return {"assets": rows, "updated": time.time()}


@router.get("/agent/opportunities")
async def agent_opportunities(limit: int = Query(100, le=500), status: str | None = None, account: str = "live"):
    q = db.select(db.opportunities.c.id, db.opportunities.c.ts, db.opportunities.c.asset, db.opportunities.c.asset_class,
                  db.opportunities.c.strategy, db.opportunities.c.direction, db.opportunities.c.status,
                  db.opportunities.c.reason).where(db.opportunities.c.account == account).order_by(db.opportunities.c.id.desc()).limit(limit)
    if status:
        q = q.where(db.opportunities.c.status == status)
    with db.engine().connect() as c:
        rows = [dict(r._mapping) for r in c.execute(q)]
        counts = dict(c.execute(db.select(db.opportunities.c.status, db.func.count()).where(db.opportunities.c.account == account)
                                .group_by(db.opportunities.c.status)).all())
    return {"opportunities": rows, "counts": counts}


@router.get("/agent/opportunities/{oid}")
async def agent_opportunity(oid: int):
    with db.engine().connect() as c:
        r = c.execute(db.select(db.opportunities).where(db.opportunities.c.id == oid)).first()
    if not r:
        raise HTTPException(404, {"error": "not found"})
    return dict(r._mapping)


@router.websocket("/ws/agent")
async def agent_ws(ws: WebSocket):
    origin = ws.headers.get("origin")
    allowed = [o.strip() for o in settings.cors_origins.split(",")]
    if origin and origin not in allowed and not origin.startswith(("http://localhost", "http://127.0.0.1")):
        await ws.close(code=1008)
        return
    await ws.accept()
    q: asyncio.Queue = asyncio.Queue()
    agent.subscribers.add(q)
    try:
        await ws.send_json({"type": "snapshot", "status": agent.status(), "events": list(agent.logbuf)[-150:]})
        while True:
            msg = await q.get()
            await ws.send_json(msg)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        agent.subscribers.discard(q)


# ------------------------------------------------------------------ market structure
@router.get("/structure")
async def structure(symbol: str = "BTC-USD", tf: str = "15m", asset_class: str = "crypto", bars: int = Query(500, le=1500)):
    """Fetch candles, analyse structure, and evaluate strategies on the latest CLOSED candle."""
    if tf not in TF_SECONDS:
        raise HTTPException(400, {"error": f"timeframe must be one of {list(TF_SECONDS)}"})
    t0 = time.perf_counter()
    try:
        if asset_class == "memecoin":
            cs = await feeds.gecko_pool_candles(symbol, tf, bars)
            src, unit = "GeckoTerminal", "usd"
        else:
            cs = await feeds.coinbase_history(symbol.upper(), tf, bars)
            src, unit = "Coinbase Exchange", "base"
    except UpstreamError as e:
        raise _bad(e)
    fetch_ms = (time.perf_counter() - t0) * 1000
    s = Series.from_candles(symbol, asset_class, tf, src, cs, volume_unit=unit)
    if len(s) < 60:
        raise HTTPException(422, {"error": f"only {len(s)} closed candles available — not enough for analysis"})
    t1 = time.perf_counter()
    a = analyse(s)
    analysis_ms = (time.perf_counter() - t1) * 1000
    t = len(s) - 1
    setups = []
    cfg = CLASS_CFG.get(asset_class, CLASS_CFG["crypto"])
    st = paper_settings.get_settings()
    for strat in STRATEGIES:
        sig = strat.evaluate(s, a, t)
        if not sig:
            continue
        plan, ce = backtest.plan_for(s, a, sig, 1000.0, cfg["max_hold"], targets=st["targets_pct"],
                                     fees=st["fees"].get(asset_class, st["fees"]["crypto"]), trail_atr=strat.trail_atr,
                                     min_rr=st["min_rr"])
        tier = agent.selector.tier(sig.strategy, asset_class, tf, sig.direction)
        setups.append({"signal": sig.to_json(), "tier": {"tier": tier[0], "reason": tier[1]},
                       "plan": None if hasattr(plan, "reason") else (plan if isinstance(plan, dict) else plan.to_json()),
                       "rejection": {"reason": plan.reason, "detail": plan.detail} if hasattr(plan, "reason") else None,
                       "regime": backtest.regime_of(s, a, t, sig.direction),
                       "probability": agent.model.estimate(sig.strategy, asset_class, backtest.regime_of(s, a, t, sig.direction)) if agent.model else None})
    return plain({
        "symbol": symbol, "tf": tf, "asset_class": asset_class, "source": src, "fetched_at": time.time(),
        "candles": s.to_json(len(s)), "provisional": None if s.provisional is None else s.provisional.__dict__,
        "gaps": s.gaps, "trend": a.trend, "structure_label": market_structure_label(a.swings),
        "swings": [{"i": x.i, "t": x.t, "kind": x.kind, "price": x.price, "label": x.label} for x in a.swings],
        "detections": [d.to_json() for d in a.detections if d.kind != "pattern" or d.i >= t - 60],
        "pools": a.pools, "atr_pct": float(100 * a.atr[t] / s.c[t]), "rsi": float(a.rsi[t]),
        "rel_volume": None if a.rel_volume[t] != a.rel_volume[t] else float(a.rel_volume[t]),
        "ema20": [None if x != x else float(x) for x in a.ema20], "ema50": [None if x != x else float(x) for x in a.ema50],
        "setups": setups, "latency_ms": {"fetch": round(fetch_ms, 1), "analysis": round(analysis_ms, 1)},
    })


# ------------------------------------------------------------------ memecoin pools
def pool_flags(p: dict) -> list[dict]:
    flags = []
    liq = p.get("liquidity_usd")
    if liq is None:
        flags.append({"flag": "unreliable_data", "severity": 2, "detail": "no liquidity reported"})
    elif liq < 100_000:
        flags.append({"flag": "low_liquidity", "severity": 3 if liq < 25_000 else 2, "detail": f"liquidity ${liq:,.0f}"})
    vol_h1 = p["volume_usd"].get("h1") or 0
    if liq and vol_h1 > 5 * liq:
        flags.append({"flag": "possible_wash_trading", "severity": 2, "detail": f"1h volume {vol_h1 / liq:.1f}× liquidity"})
    tx = (p.get("transactions") or {}).get("h1") or {}
    b, s = tx.get("buys", 0), tx.get("sells", 0)
    if b + s >= 20 and min(b, s) / max(b, s) < 0.15:
        flags.append({"flag": "one_sided_flow", "severity": 1, "detail": f"1h buys {b} / sells {s}"})
    traders = tx.get("unique_traders") or (tx.get("buyers", 0) + tx.get("sellers", 0))
    if b + s >= 50 and traders and (b + s) / traders > 8:
        flags.append({"flag": "few_wallets_many_trades", "severity": 2, "detail": f"{b + s} trades from {traders} wallets in 1h"})
    mc = p.get("market_cap_usd") or p.get("fdv_usd")
    if liq and mc and mc / liq > 50:
        flags.append({"flag": "thin_liquidity_vs_valuation", "severity": 2, "detail": f"valuation {mc / liq:,.0f}× liquidity"})
    if p.get("created_at"):
        import datetime as dt
        age_h = (time.time() - dt.datetime.fromisoformat(p["created_at"].replace("Z", "+00:00")).timestamp()) / 3600
        p["age_hours"] = round(age_h, 2)
        if age_h < 24:
            flags.append({"flag": "very_new", "severity": 2, "detail": f"{age_h:.1f} h old"})
    return flags


@router.get("/memecoins/pools")
async def memecoin_pools(kind: str = Query("trending_pools", pattern="^(trending_pools|new_pools)$")):
    try:
        pools = await feeds.gecko_pools(kind)
    except UpstreamError as e:
        raise _bad(e)
    for p in pools:
        p["flags"] = pool_flags(p)
        p["is_pump_fun"] = bool((p.get("token_address") or "").endswith("pump")) or "pump" in (p.get("dex") or "")
        p["holder_data"] = None
        p["probability"] = None
    return {"source": "GeckoTerminal public API", "fetched_at": time.time(), "kind": kind, "pools": pools,
            "holder_note": "Holder concentration needs on-chain data (free Helius key); not available yet."}


# ------------------------------------------------------------------ strategies & backtests
@router.get("/strategies")
async def strategies():
    return {"strategies": [{"name": s.name, "label": s.label, "description": s.description} for s in STRATEGIES],
            "research_only": RESEARCH_ONLY, "calibration": agent.calibration or db.kv_get("calibration_summary") or {}}


class BacktestReq(BaseModel):
    symbol: str = "BTC-USD"
    asset_class: str = "crypto"
    tf: str = "15m"
    bars: int = 2400
    strategies: list[str] | None = None
    targets_pct: list[float] = [2.0, 3.0, 4.0, 5.0]
    fee_pct: float | None = None
    max_hold_bars: int | None = None
    starting_balance: float = 10_000.0
    risk_per_trade_pct: float = 0.5
    entry_order: str = "limit"


@router.post("/backtest")
async def run_backtest(req: BacktestReq):
    if req.bars > 6000:
        raise HTTPException(400, {"error": "bars must be ≤ 6000"})
    try:
        if req.asset_class == "memecoin":
            cs = await feeds.gecko_pool_candles(req.symbol, req.tf, min(req.bars, 1000))
        else:
            cs = await feeds.coinbase_history(req.symbol.upper(), req.tf, req.bars)
    except UpstreamError as e:
        raise _bad(e)
    s = Series.from_candles(req.symbol, req.asset_class, req.tf, "backtest", cs)
    strats = [BY_NAME[n] for n in (req.strategies or [x.name for x in STRATEGIES]) if n in BY_NAME]
    max_hold = req.max_hold_bars or CLASS_CFG.get(req.asset_class, CLASS_CFG["crypto"])["max_hold"]
    st = paper_settings.get_settings()
    fees = st["fees"].get(req.asset_class, st["fees"]["crypto"])
    if req.fee_pct is not None:
        fees = {"maker": req.fee_pct, "taker": req.fee_pct}
    fee = fees
    t0 = time.perf_counter()
    trades, rejected = await asyncio.to_thread(backtest.run, s, strats, max_hold, 1000.0, None, req.targets_pct, 60, None, fees,
                                               req.entry_order, 2)
    ms = (time.perf_counter() - t0) * 1000
    trades.sort(key=lambda x: x.entry_time)
    # equity simulation with fixed fractional risk
    eq, curve, peak, mdd = req.starting_balance, [], req.starting_balance, 0.0
    for tr in trades:
        notional = eq * req.risk_per_trade_pct / 100 / (tr.stop_pct / 100) if tr.stop_pct > 0 else 0
        notional = min(notional, eq * 0.2)
        eq += notional * tr.net_pct / 100
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1)
        curve.append({"time": tr.exit_time, "equity": eq})
    rets = [tr.net_pct for tr in trades]
    import numpy as np
    years = (s.t[-1] - s.t[0]) / (365 * 86400) if len(s) > 1 else 0
    neg = [r for r in rets if r < 0]
    bh = float(s.c[-1] / s.c[0] - 1) * 100 if len(s) > 1 else None
    exposure = sum(tr.bars_held for tr in trades) / max(len(s), 1)
    model = ProbabilityModel(trades)
    split = int(len(trades) * 0.7)
    return {
        "symbol": req.symbol, "tf": req.tf, "bars": len(s), "gaps": s.gaps,
        "period": {"start": int(s.t[0]) if len(s) else None, "end": int(s.t[-1]) if len(s) else None},
        "summary": backtest.summarise(trades), "rejected_setups": rejected, "runtime_ms": round(ms, 1),
        "portfolio": {
            "starting_balance": req.starting_balance, "final_equity": eq,
            "total_return_pct": 100 * (eq / req.starting_balance - 1),
            # annualising fewer than ~90 days of results is meaningless, so it is withheld
            "annualised_return_pct": (100 * ((eq / req.starting_balance) ** (1 / years) - 1)) if years >= 0.25 and eq > 0 else None,
            "max_drawdown_pct": 100 * mdd,
            "sharpe_per_trade": float(np.mean(rets) / np.std(rets)) if len(rets) > 1 and np.std(rets) > 0 else None,
            "sortino_per_trade": float(np.mean(rets) / np.std(neg)) if len(neg) > 1 and np.std(neg) > 0 else None,
            "exposure_pct": 100 * exposure, "benchmark_buy_hold_pct": bh, "transactions": 2 * len(trades),
        },
        "out_of_sample": {"in_sample": backtest.summarise(trades[:split]), "out_of_sample": backtest.summarise(trades[split:]),
                          "note": "Rules are fixed in advance (no parameter fitting), so both segments are untouched by optimisation; "
                                  "the split checks stability over time."},
        "by_strategy": {st.name: backtest.summarise([x for x in trades if x.strategy == st.name]) for st in strats},
        "by_regime": {r: backtest.summarise([x for x in trades if x.regime == r]) for r in sorted({x.regime for x in trades})},
        "walk_forward_calibration": model.validation,
        "equity_curve": curve, "trades": [x.to_json() for x in trades[-300:]],
        "candles": s.to_json(len(s)),
        "assumptions": {"fill": "next bar open after signal close", "same_bar_stop_and_target": "stop assumed first",
                        "partial": "50% at TP1, stop to break-even", "fees_pct_per_side": fee, "entry_order": req.entry_order,
                        "survivorship": "single asset chosen today; delisted assets not included"},
    }


# ------------------------------------------------------------------ agent controls
@router.post("/agent/emergency-stop")
async def agent_emergency_stop():
    return await agent.emergency_stop()


# ------------------------------------------------------------------ paper trading (broker-backed)
def _broker(account: str):
    from ..paper.broker import Broker
    if account == "live":
        return agent.broker
    if not db.rows(db.select(db.cash.c.account).where(db.cash.c.account == account)):
        raise HTTPException(404, {"error": f"unknown account {account}"})
    return Broker(account)


@router.get("/paper/accounts")
async def paper_accounts():
    rows = db.rows(db.select(db.cash))
    return {"accounts": [{**r, "kind": "live paper trading" if r["account"] == "live" else "historical replay"} for r in rows]}


@router.get("/paper/portfolio")
async def paper_portfolio(account: str = "live"):
    b = _broker(account)
    s = b.state()
    closed = b.positions("closed")
    pnl = [p["realized_pnl"] for p in closed]
    wins, losses = [x for x in pnl if x > 0], [x for x in pnl if x <= 0]
    eq = db.rows(db.text("SELECT ts, equity FROM equity WHERE account=:a ORDER BY ts"), {"a": account})
    peak, mdd = None, 0.0
    for e in eq:
        peak = e["equity"] if peak is None else max(peak, e["equity"])
        mdd = min(mdd, e["equity"] / peak - 1)
    now = eq[-1]["ts"] if eq and account != "live" else time.time()
    day_ref = next((e["equity"] for e in eq if e["ts"] >= now - 86400), None)
    by = {}
    for p in closed:
        b_ = by.setdefault(p["strategy"], {"trades": 0, "wins": 0, "pnl": 0.0})
        b_["trades"] += 1
        b_["wins"] += int(p["realized_pnl"] > 0)
        b_["pnl"] += p["realized_pnl"]
    stride = max(1, len(eq) // 1500)
    return plain({**s, "total_return_pct": 100 * (s["equity"] / s["starting_balance"] - 1),
                  "daily_return_pct": 100 * (s["equity"] / day_ref - 1) if day_ref else None,
                  "realized_closed": sum(pnl), "closed_trades": len(closed),
                  "win_rate": len(wins) / len(closed) if closed else None,
                  "profit_factor": (sum(wins) / -sum(losses)) if losses and sum(losses) < 0 else None,
                  "avg_win": sum(wins) / len(wins) if wins else None, "avg_loss": sum(losses) / len(losses) if losses else None,
                  "max_drawdown_pct": 100 * mdd if eq else None,
                  "avg_holding_s": (sum(p["closed_at"] - p["opened_at"] for p in closed) / len(closed)) if closed else None,
                  "by_strategy": by, "equity_curve": eq[::stride], "settings": paper_settings.get_settings(),
                  "entries_suspended": None if account != "live" else agent.status()["entries_suspended"]})


@router.get("/paper/history")
async def paper_history(account: str = "live", limit: int = Query(200, le=2000)):
    b = _broker(account)
    closed = db.rows(db.select(db.positions).where(db.positions.c.account == account, db.positions.c.status == "closed")
                     .order_by(db.positions.c.closed_at.desc()).limit(limit))
    return plain({"open": b.positions("open"), "pending": b.positions("pending"), "closed": closed,
                  "orders": db.rows(db.select(db.orders).where(db.orders.c.account == account).order_by(db.orders.c.id.desc()).limit(limit))})


@router.get("/paper/activity")
async def paper_activity(account: str = "live", limit: int = Query(200, le=2000)):
    """Every executed BUY / SELL with price, quantity, strategy and reason (from the fills ledger)."""
    return plain({"fills": db.rows(db.select(db.fills).where(db.fills.c.account == account).order_by(db.fills.c.ts.desc()).limit(limit))})


@router.get("/paper/settings")
async def paper_settings_get():
    return paper_settings.get_settings()


@router.put("/paper/settings")
async def paper_settings_update(patch: dict):
    try:
        return paper_settings.update_settings(patch)
    except ValueError as e:
        raise _bad(e)


@router.post("/paper/positions/{pid}/close")
async def paper_close(pid: int):
    rows = db.rows(db.select(db.positions).where(db.positions.c.id == pid, db.positions.c.account == "live"))
    if not rows:
        raise HTTPException(404, {"error": "not found"})
    p = rows[0]
    q = (await agent._fresh_quotes()).get(p["asset"])
    if q is None:
        raise HTTPException(502, {"error": "no live price available to close at"})
    try:
        ev = agent.broker.close_position(pid, q, "manual", "closed manually by user")
    except BrokerError as e:
        raise _bad(e)
    await agent.desk._after_fill(ev)
    return plain(ev)


class ResetReq(BaseModel):
    confirm: str


@router.post("/paper/reset")
async def paper_reset(req: ResetReq):
    if req.confirm != "RESET":
        raise HTTPException(400, {"error": "send {\"confirm\": \"RESET\"} to delete the live simulated account"})
    if agent.broker.positions("open") or agent.active:
        raise HTTPException(400, {"error": "deactivate the agent and close open simulated positions first"})
    with db.engine().begin() as c:
        for t in (db.fills, db.orders, db.positions, db.equity, db.reports):
            c.execute(db.delete(t).where(t.c.account == "live"))
        c.execute(db.delete(db.cash).where(db.cash.c.account == "live"))
    agent.broker.ensure_account()
    await agent.emit("Live simulated account reset by user", "system")
    return {"ok": True}


# ------------------------------------------------------------------ reports
@router.get("/reports")
async def reports_list(account: str = "live", kind: str | None = None, limit: int = Query(100, le=1000)):
    return plain({"reports": reports.list_reports(account, kind, limit)})


@router.get("/reports/{rid}")
async def report_get(rid: int):
    r = reports.get_report(rid)
    if not r:
        raise HTTPException(404, {"error": "not found"})
    return plain(r)


class GenReq(BaseModel):
    kind: str = "daily"
    account: str = "live"


@router.post("/reports/generate")
async def report_generate(req: GenReq):
    if req.kind not in ("daily", "weekly", "monthly"):
        raise HTTPException(400, {"error": "kind must be daily, weekly or monthly"})
    events = [{"title": n["title"], "source": n["source"], "url": n["url"]} for n in agent.news if n.get("primary_source") or n.get("assets")][:10]
    ref = None
    if req.account != "live":
        last = db.rows(db.text("SELECT MAX(ts) AS t FROM equity WHERE account=:a"), {"a": req.account})[0]["t"]
        ref = (last or time.time()) - 1
    return plain(reports.period_report(req.account, req.kind, ref, events if req.account == "live" else None))


# ------------------------------------------------------------------ evidence / self-improvement
@router.get("/strategies/evidence")
async def strategies_evidence(account: str = "live"):
    from ..quant.selector import Selector
    sel = agent.selector if account == "live" else Selector(account)
    changes = db.rows(db.select(db.strategy_changes).where(db.strategy_changes.c.account == account)
                      .order_by(db.strategy_changes.c.ts.desc()).limit(100))
    return plain({**sel.summary(), "changes": changes,
                  "rules": {"active": "backtest 90% CI of mean net return entirely above 0 (n ≥ 30) and recent half positive",
                            "exploration": "unproven: mean ≥ 0 but 90% CI includes 0, n ≥ 10, recent half not negative — traded at small size",
                            "disabled": "90% CI below 0, recent half negative, or too few samples",
                            "live_demote": "≥ 10 live trades with 90% CI upper bound < 0",
                            "live_promote": "≥ 20 live trades with 90% CI lower bound > 0"}})


# ------------------------------------------------------------------ historical replay
class ReplayReq(BaseModel):
    tf: str = "1h"
    bars: int = 2400
    calib_frac: float = 0.6


@router.post("/replay")
async def replay_start(req: ReplayReq):
    if req.tf not in ("15m", "1h", "1d"):
        raise HTTPException(400, {"error": "tf must be 15m, 1h or 1d"})
    if not 0.3 <= req.calib_frac <= 0.8:
        raise HTTPException(400, {"error": "calib_frac must be between 0.3 and 0.8"})
    try:
        st = replay_manager.start(req.tf, min(req.bars, 3000), req.calib_frac)
    except RuntimeError as e:
        raise HTTPException(409, {"error": str(e)})
    return {"id": st.id, "account": st.account, "status": st.status}


@router.get("/replay")
async def replay_list():
    runs = {r.id: {"id": r.id, "account": r.account, "status": r.status, "progress": r.progress, "message": r.message,
                   "started_at": r.started_at, "finished_at": r.finished_at, "error": r.error, "result": r.result}
            for r in replay_manager.runs.values()}
    n = db.kv_get("replay_counter") or 0
    for i in range(1, n + 1):
        if i not in runs:
            saved = db.kv_get(f"replay:{i}")
            if saved:
                runs[i] = {**saved, "progress": 1.0, "message": ""}
    return plain({"runs": sorted(runs.values(), key=lambda r: -r["id"])})
