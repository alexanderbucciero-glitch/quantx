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
from ..paper import engine as paper
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
async def agent_opportunities(limit: int = Query(100, le=500), status: str | None = None):
    q = db.select(db.opportunities.c.id, db.opportunities.c.ts, db.opportunities.c.asset, db.opportunities.c.asset_class,
                  db.opportunities.c.strategy, db.opportunities.c.direction, db.opportunities.c.status,
                  db.opportunities.c.reason).order_by(db.opportunities.c.id.desc()).limit(limit)
    if status:
        q = q.where(db.opportunities.c.status == status)
    with db.engine().connect() as c:
        rows = [dict(r._mapping) for r in c.execute(q)]
        counts = dict(c.execute(db.select(db.opportunities.c.status, db.func.count()).group_by(db.opportunities.c.status)).all())
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
    st = paper.get_settings()
    for strat in STRATEGIES:
        sig = strat.evaluate(s, a, t)
        if not sig:
            continue
        plan, ce = backtest.plan_for(s, a, sig, 1000.0, cfg["max_hold"], targets=st["targets_pct"],
                                     cost_overrides={"fee_pct": st["fees"].get(asset_class, 0.4)})
        setups.append({"signal": sig.to_json(), "plan": None if hasattr(plan, "reason") else plan.to_json(),
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
    import app.quant.costs as cm
    fee = req.fee_pct if req.fee_pct is not None else cm.DEFAULTS[req.asset_class]["fee_pct"]
    t0 = time.perf_counter()
    trades, rejected = await asyncio.to_thread(backtest.run, s, strats, max_hold, 1000.0, None, req.targets_pct, 60, {"fee_pct": fee})
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
                        "partial": "50% at TP1, stop to break-even", "fees_pct_per_side": fee,
                        "survivorship": "single asset chosen today; delisted assets not included"},
    }


# ------------------------------------------------------------------ paper trading
@router.get("/paper/portfolio")
async def paper_portfolio():
    return paper.metrics()


@router.get("/paper/history")
async def paper_history(limit: int = Query(200, le=1000)):
    return paper.history(limit)


@router.get("/paper/settings")
async def paper_settings():
    return paper.get_settings()


@router.put("/paper/settings")
async def paper_settings_update(patch: dict):
    try:
        return paper.update_settings(patch)
    except ValueError as e:
        raise _bad(e)


@router.post("/paper/positions/{pid}/close")
async def paper_close(pid: int):
    with db.engine().connect() as c:
        r = c.execute(db.select(db.positions).where(db.positions.c.id == pid)).first()
    if not r:
        raise HTTPException(404, {"error": "not found"})
    p = dict(r._mapping)
    a = agent.assets.get(p["asset"])
    q = agent._quote(a) if a else None
    if q is None:
        try:
            if p["asset_class"] == "crypto":
                tk = await feeds.coinbase_ticker(p["asset"])
                q = paper.Quote(tk["bid"], tk["ask"], tk["price"], time.time())
            else:
                cs = await feeds.gecko_pool_candles(p["asset"], "1m", 2)
                q = paper.Quote(cs[-1].c, cs[-1].c, cs[-1].c, time.time())
        except (UpstreamError, IndexError) as e:
            raise HTTPException(502, {"error": f"no live price to close at: {e}"})
    try:
        ev = paper.close_manually(pid, q)
    except ValueError as e:
        raise _bad(e)
    await agent.emit(f"SIMULATED manual close: {p['asset']} @ {ev['price']:.6g}, P&L {ev['pnl']:+.2f} USD", "trade", asset=p["asset"], data=ev)
    return ev


class ResetReq(BaseModel):
    confirm: str


@router.post("/paper/reset")
async def paper_reset(req: ResetReq):
    if req.confirm != "RESET":
        raise HTTPException(400, {"error": "send {\"confirm\": \"RESET\"} to delete all simulated positions"})
    if paper.open_positions():
        raise HTTPException(400, {"error": "close open simulated positions first"})
    paper.reset_account()
    await agent.emit("Simulated account reset by user", "system")
    return {"ok": True}
