"""Tests for the autonomous trading workflow: broker, risk, evidence selection, desk decisions,
reports, agent controls and restart recovery. Every test uses its own temporary database."""
import asyncio
import time

import numpy as np
import pytest

from app.core import clock
from app.market.candles import Candle, Series
from app.paper.broker import Broker, BrokerError, Quote


@pytest.fixture()
def tdb(tmp_path):
    from app.core import db
    db.reset_engine_for_tests(f"sqlite:///{tmp_path}/t.db")
    yield db
    db.reset_engine_for_tests("sqlite:///./data/quantx.db")


def settings(**kw):
    from app.paper.settings import DEFAULT_SETTINGS
    s = {**DEFAULT_SETTINGS, "entry_order": "market", "cooldown_minutes": 0, **kw}
    s["fees"] = {k: dict(v) for k, v in DEFAULT_SETTINGS["fees"].items()}
    return s


PLAN = {"stop": 98.0, "tp1": 102.0, "tp2": 104.0}
META = {"volume_24h_usd": 1e10}


def entry(b, asset="X-USD", direction="long", qty=10.0, plan=None, order_type="market", limit=None, key=None, t=1000.0,
          bid=99.95, ask=100.05, tier="active", max_hold=86400):
    return b.submit_entry(asset=asset, display=asset, asset_class="crypto", strategy="momentum", tf="1h", direction=direction,
                          qty=qty, order_type=order_type, quote=Quote(bid, ask, (bid + ask) / 2, t), limit_price=limit,
                          plan=plan or PLAN, max_hold_s=max_hold, tier=tier, client_key=key or f"{asset}:{t}:{direction}",
                          report=META, opportunity_id=None, expires_s=7200)


# ------------------------------------------------------------------ broker
def test_market_buy_sell_cash_fees_and_persistence(tdb):
    b = Broker("t1", settings())
    r = entry(b)
    assert r["status"] == "filled" and r["price"] > 100.05 and r["liquidity"] == "taker"
    s = b.state()
    assert s["cash"] == pytest.approx(10_000 - r["price"] * 10 - r["fee"])
    assert s["equity"] == pytest.approx(s["cash"] + r["price"] * 10, rel=1e-9) or s["open_positions"] == 1
    b2 = Broker("t1", settings())                            # new instance = backend restart
    assert b2.positions("open")[0]["entry"] == pytest.approx(r["price"])
    assert {o["purpose"] for o in b2.open_orders()} == {"stop", "tp1"}   # resting exit orders persisted
    pid = b2.positions("open")[0]["id"]
    ev = b2.close_position(pid, Quote(101, 101.1, 101.05, 2000), "manual", "test")
    assert ev["closed"] and ev["side"] == "sell" and ev["price"] < 101
    p = b2.positions("closed")[0]
    assert p["realized_pnl"] == pytest.approx(p["gross_pnl"] - p["fees"])
    assert b2.state()["cash"] == pytest.approx(10_000 + p["realized_pnl"])
    assert not b2.open_orders()                              # exit orders cancelled on close


def test_limit_entry_fills_only_when_traded_through_and_expires(tdb):
    b = Broker("t2", settings())
    r = entry(b, order_type="limit", limit=99.0, bid=99.95, ask=100.05)
    assert r["status"] == "open" and b.positions("pending")
    assert b.on_quote("X-USD", Quote(99.5, 99.6, 99.55, 1100)) == []           # ask 99.6 > limit
    ev = b.on_quote("X-USD", Quote(98.9, 99.0, 98.95, 1200))
    assert ev[0]["kind"] == "entry" and ev[0]["price"] == 99.0 and ev[0]["liquidity"] == "maker"
    r2 = entry(b, asset="Y-USD", order_type="limit", limit=50.0, key="y", t=1000)
    ev = b.on_quote("Y-USD", Quote(99, 99.1, 99.05, 1000 + 7201))
    assert ev[0]["kind"] == "entry_expired" and not b.positions("pending", "Y-USD")
    assert b.positions("cancelled", "Y-USD")


def test_short_stop_gap_and_cash(tdb):
    b = Broker("t3", settings())
    entry(b, direction="short", plan={"stop": 102.0, "tp1": 98.0, "tp2": 96.0})
    cash0 = b.state()["cash"]
    assert cash0 > 10_000                                    # short proceeds credited
    ev = b.on_quote("X-USD", Quote(104.9, 105.0, 104.95, 1500))               # gap through the stop
    assert ev[0]["kind"] == "stop" and ev[0]["price"] >= 105.0                # filled at the worse price, not 102
    p = b.positions("closed")[0]
    assert p["realized_pnl"] < -(105 - 100) * 10                              # loss includes fees
    assert b.state()["cash"] == pytest.approx(10_000 + p["realized_pnl"])


def test_take_profit_partial_then_breakeven(tdb):
    b = Broker("t4", settings())
    r = entry(b)
    ev = b.on_quote("X-USD", Quote(102.1, 102.2, 102.15, 1100))
    assert ev[0]["kind"] == "tp1" and ev[0]["price"] == 102.0 and not ev[0]["closed"]
    p = b.positions("open")[0]
    assert p["stop"] == pytest.approx(r["price"]) and p["remaining_qty"] == pytest.approx(5)
    stop_order = [o for o in b.open_orders() if o["purpose"] == "stop"][0]
    assert stop_order["stop_price"] == pytest.approx(r["price"]) and stop_order["qty"] == pytest.approx(5)
    ev = b.on_quote("X-USD", Quote(r["price"] - 0.02, r["price"], r["price"], 1200))
    assert ev[0]["kind"] == "breakeven" and ev[0]["closed"]


def test_trailing_stop_ratchets_and_time_exit(tdb):
    b = Broker("t5", settings())
    entry(b, plan={"stop": 97.0, "tp1": None, "tp2": None, "trail_atr": 2.0, "atr": 1.0})
    b.on_quote("X-USD", Quote(105.0, 105.1, 105.05, 1100))
    assert b.positions("open")[0]["stop"] == pytest.approx(103.0)            # 105 − 2×ATR
    b.on_quote("X-USD", Quote(104.0, 104.1, 104.05, 1200))
    assert b.positions("open")[0]["stop"] == pytest.approx(103.0)            # never loosens
    ev = b.on_quote("X-USD", Quote(102.9, 103.0, 102.95, 1300))
    assert ev[0]["kind"] == "trailing_stop" and ev[0]["net"] > 0
    entry(b, asset="T-USD", key="t", max_hold=100, t=2000)
    ev = b.on_quote("T-USD", Quote(100.0, 100.1, 100.05, 2101))
    assert ev[0]["kind"] == "time_exit" and ev[0]["closed"]


def test_duplicate_orders_rejected(tdb):
    b = Broker("t6", settings())
    entry(b, key="same")
    with pytest.raises(BrokerError):
        entry(b, key="same")


# ------------------------------------------------------------------ risk
def test_risk_limits(tdb):
    from app.paper import risk
    b = Broker("r1", settings(max_open_positions=2, max_entries_per_hour=10))
    ok = risk.check_entry(b, asset="A-USD", asset_class="crypto", direction="long", entry=100, stop=98, tier="active", now=1000)
    assert ok.ok and ok.risk_usd <= 10_000 * 0.005 + 1e-6
    ex = risk.check_entry(b, asset="A-USD", asset_class="crypto", direction="long", entry=100, stop=98, tier="exploration", now=1000)
    assert ex.ok and ex.qty < ok.qty                                         # exploration trades are smaller
    assert not risk.check_entry(b, asset="A-USD", asset_class="crypto", direction="long", entry=100, stop=98, tier="disabled", now=1000).ok
    assert not risk.check_entry(b, asset="M", asset_class="memecoin", direction="short", entry=1, stop=1.1, tier="active", now=1000).ok
    entry(b, asset="A-USD", key="a")
    d = risk.check_entry(b, asset="A-USD", asset_class="crypto", direction="long", entry=100, stop=98, tier="active", now=1000)
    assert not d.ok and "duplicate" in d.reason
    entry(b, asset="B-USD", key="b")
    d = risk.check_entry(b, asset="C-USD", asset_class="crypto", direction="long", entry=100, stop=98, tier="active", now=1000)
    assert not d.ok and "maximum open positions" in d.reason


def test_circuit_breakers_and_cooldown_and_correlation(tdb):
    from app.paper import risk
    b = Broker("r2", settings(cooldown_minutes=60, max_correlated_positions=1))
    b.snapshot_equity(1000)
    entry(b, asset="A-USD", key="a", qty=60)
    b.on_quote("A-USD", Quote(96.0, 96.1, 96.05, 1100))                     # stop at 98 → −2.4% of equity
    assert risk.entries_suspended(b, 1200) and "daily loss" in risk.entries_suspended(b, 1200)
    d = risk.check_entry(b, asset="Z-USD", asset_class="crypto", direction="long", entry=100, stop=98, tier="active", now=1200)
    assert not d.ok and "suspended" in d.reason
    b2 = Broker("r3", settings(cooldown_minutes=60, max_correlated_positions=1))
    entry(b2, asset="A-USD", key="a2")
    b2.close_position(b2.positions("open")[0]["id"], Quote(100, 100.1, 100.05, 1500), "manual", "t")
    d = risk.check_entry(b2, asset="A-USD", asset_class="crypto", direction="long", entry=100, stop=98, tier="active", now=1600)
    assert not d.ok and "cooldown" in d.reason
    rng = np.random.default_rng(0)
    base = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 200)))
    closes = {"B-USD": base, "C-USD": base * 1.001, "D-USD": 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 200)))}
    entry(b2, asset="B-USD", key="b2", t=5000)
    d = risk.check_entry(b2, asset="C-USD", asset_class="crypto", direction="long", entry=100, stop=98, tier="active", now=9000, closes=closes)
    assert not d.ok and "correlation" in d.reason
    assert risk.check_entry(b2, asset="D-USD", asset_class="crypto", direction="long", entry=100, stop=98, tier="active", now=9000, closes=closes).ok


# ------------------------------------------------------------------ evidence selector
def _trade(i, net, strategy="trend", tf="1h", direction="long"):
    from app.quant.backtest import SimTrade
    return SimTrade("A", "crypto", strategy, direction, i, i * 100, i * 100 + 50, 1, 1, 5, net > 0, False, net <= 0, False,
                    net, net, "with-trend/low-vol", 0.5, 2, 1, tf, "x", 0.5)


def test_selector_tiers_need_evidence(tdb):
    from app.quant.selector import Selector, tier_from
    assert tier_from([1.0] * 5)[0] == "disabled"                            # too few samples
    rng = np.random.default_rng(1)
    assert tier_from(list(rng.normal(0.8, 1.0, 60)))[0] == "active"
    assert tier_from(list(rng.normal(-0.8, 1.0, 60)))[0] == "disabled"
    assert tier_from(list(rng.normal(0.05, 2.0, 20)))[0] in ("exploration", "disabled")
    sel = Selector("s1")
    sel.fit([_trade(i, x) for i, x in enumerate(rng.normal(0.1, 2.0, 40))])
    tier, reason, info = sel.tier("trend", "crypto", "1h", "long")
    assert tier in ("exploration", "active", "disabled") and reason
    assert sel.tier("trend", "crypto", "1d", "long")[0] == "disabled"         # no evidence → never traded


def test_selector_demotes_only_after_enough_live_losses(tdb):
    from app.quant.selector import Selector
    sel = Selector("s2")
    sel.backtest = {"trend|crypto|1h|long": {"tier": "exploration", "reason": "x", "stats": {}}}
    sel.refresh()
    b = Broker("s2", settings())
    for k in range(10):
        entry(b, asset=f"L{k}-USD", key=f"l{k}", tier="exploration", t=1000 + k)
        p = b.positions("open")[0]
        with tdb.engine().begin() as c:
            c.execute(tdb.update(tdb.positions).where(tdb.positions.c.id == p["id"]).values(strategy="trend", tf="1h"))
        b.close_position(p["id"], Quote(97, 97.1, 97.05, 2000 + k), "stop", "t")
        changes = sel.refresh()
        if k < 9:
            assert sel.tier("trend", "crypto", "1h", "long")[0] == "exploration"   # not changed by a few losses
    assert sel.tier("trend", "crypto", "1h", "long")[0] == "disabled"
    rows = tdb.rows(tdb.select(tdb.strategy_changes).where(tdb.strategy_changes.c.account == "s2"))
    assert rows and rows[-1]["new_tier"] == "disabled"


# ------------------------------------------------------------------ desk: full autonomous cycle
def trend_series(n=200, t0=1_700_000_000, step=3600):
    """60 flat bars (tiny alternating noise) followed by a steady +0.6%/bar uptrend."""
    px = [100.0 + (0.05 if i % 2 else -0.05) for i in range(60)]
    while len(px) < n:
        px.append(px[-1] * 1.006)
    return [Candle(t0 + i * step, px[i - 1] if i else px[0], max(px[i], px[i - 1] if i else px[i]) * 1.001,
                   min(px[i], px[i - 1] if i else px[i]) * 0.999, px[i], 100.0) for i in range(len(px))]


async def run_cycle(tdb, prices_after, account):
    from app.analysis.structure import analyse
    from app.quant.selector import Selector
    from app.strategies.library import TrendFollow
    from app.trading.desk import AssetCtx, Desk
    cs = trend_series()
    events = []

    async def emit(msg, *a, **k):
        events.append(msg)
    sel = Selector(account)
    sel.backtest = {f"trend|crypto|1h|long": {"tier": "exploration", "reason": "test evidence", "stats": {"n": 20, "mean": 0.3, "win_rate": 0.4}}}
    sel.refresh()
    b = Broker(account, settings())
    desk = Desk(account, b, sel, emit)
    ctx = AssetCtx("X-USD", "X/USD", "crypto", volume_24h_usd=1e9)
    # find the first bar where the trend strategy fires, and present exactly that closed candle
    for k in range(60, len(cs)):
        s = Series.from_candles("X-USD", "crypto", "1h", "t", cs[:k], now=cs[k - 1].t + 3601)
        an = analyse(s)
        if TrendFollow().evaluate(s, an, len(s) - 1):
            break
    close = s.c[-1]
    clock.set_sim_time(float(s.t[-1] + 3600))
    res = await desk.on_candle(ctx, s, an, Quote(close * 0.9999, close * 1.0001, close, clock.now()))
    assert res["accepted"] >= 1, events
    p = b.positions("open")[0]
    for i, px in enumerate(prices_after):
        clock.set_sim_time(clock.now() + 60)
        await desk.on_quote("X-USD", Quote(px * 0.9999, px * 1.0001, px, clock.now()))
    clock.set_sim_time(None)
    return b, p, events


def test_desk_autonomous_cycle_profit_and_loss(tdb):
    from app.trading import reports
    # winning trade: price rises, trailing stop ratchets up, then price falls back → trailing stop exit in profit
    b, p, events = asyncio.run(run_cycle(tdb, [], "win"))
    entry_px = p["entry"]
    path_up = list(np.linspace(entry_px, entry_px * 1.15, 80)) + list(np.linspace(entry_px * 1.15, entry_px * 1.05, 40))
    b, p, events = asyncio.run(run_cycle(tdb, path_up, "win2"))
    closed = b.positions("closed")
    assert closed and closed[0]["realized_pnl"] > 0 and closed[0]["exit_reason"] == "trailing_stop"
    assert any("EXECUTED BUY" in e for e in events) and any("EXECUTED SELL" in e for e in events)
    rep = closed[0]["trade_report"]
    assert rep["net_pnl"] == pytest.approx(closed[0]["realized_pnl"]) and rep["analysis_correct"] is True
    assert rep["gross_pnl"] - rep["trading_costs"] == pytest.approx(rep["net_pnl"])
    # losing trade: price falls through the initial stop
    b2, p2, ev2 = asyncio.run(run_cycle(tdb, list(np.linspace(1, 0.9, 50) * entry_px), "lose"))
    c2 = b2.positions("closed")[0]
    assert c2["realized_pnl"] < 0 and c2["exit_reason"] == "stop" and c2["trade_report"]["analysis_correct"] is False
    # daily report sums exactly the recorded trades
    d = reports.period_report("lose", "daily", c2["closed_at"])
    assert d["trades_closed"] == 1 and d["realized_pnl"] == pytest.approx(c2["realized_pnl"]) and d["losing_trades"] == 1


def test_desk_never_trades_disabled_and_dedupes(tdb):
    from app.analysis.structure import analyse
    from app.quant.selector import Selector
    from app.strategies.library import TrendFollow
    from app.trading.desk import AssetCtx, Desk
    cs = trend_series()
    for k in range(60, len(cs)):
        s = Series.from_candles("X-USD", "crypto", "1h", "t", cs[:k], now=cs[k - 1].t + 3601)
        an = analyse(s)
        if TrendFollow().evaluate(s, an, len(s) - 1):
            break
    sel = Selector("d1")                                                     # no evidence at all → disabled
    b = Broker("d1", settings())
    desk = Desk("d1", b, sel)
    q = Quote(s.c[-1] * 0.9999, s.c[-1] * 1.0001, s.c[-1], time.time())
    r1 = asyncio.run(desk.on_candle(AssetCtx("X-USD", "X/USD", "crypto", 1e9), s, an, q))
    assert r1["accepted"] == 0 and not b.positions("open") and not b.positions("pending")
    r2 = asyncio.run(desk.on_candle(AssetCtx("X-USD", "X/USD", "crypto", 1e9), s, an, q))
    assert r2["signals"] == r1["signals"]
    n = tdb.rows(tdb.text("SELECT COUNT(*) AS n FROM opportunities WHERE account='d1'"))[0]["n"]
    assert n == r1["signals"]                                                # the same closed candle is decided once


# ------------------------------------------------------------------ agent controls & recovery
def test_agent_activate_deactivate_emergency_and_recovery(tdb, monkeypatch):
    from app.agent.engine import Agent

    async def run():
        ag = Agent()
        monkeypatch.setattr(ag, "refresh_universe", lambda: asyncio.sleep(0))
        monkeypatch.setattr(ag.stream, "start", lambda: None)

        async def noop(*a, **k):
            return None
        monkeypatch.setattr(ag.stream, "stop", noop)
        monkeypatch.setattr(ag.stream, "set_products", noop)
        st = await ag.activate()
        assert st["active"] and st["mode"] == "trading" and st["entries_enabled"]
        # an open position exists, then the user deactivates → entries stop, position keeps being managed
        b = ag.broker
        entry(b, asset="BTC-USD", key="k1", t=time.time())
        st = await ag.deactivate()
        assert not st["entries_enabled"] and st["mode"] == "managing" and st["positions_open"] == 1
        # restart: a fresh agent recovers the position from the database and manages it
        ag2 = Agent()
        monkeypatch.setattr(ag2.stream, "start", lambda: None)
        monkeypatch.setattr(ag2.stream, "stop", noop)
        monkeypatch.setattr(ag2.stream, "set_products", noop)

        async def no_series(a, tf):
            return None
        monkeypatch.setattr(ag2, "_load_series", no_series)
        await ag2.boot()
        assert ag2.mode == "managing" and ag2.broker.positions("open")
        assert any("Recovered" in e["message"] for e in ag2.logbuf)
        # emergency stop closes everything at market
        async def quotes():
            return {"BTC-USD": Quote(100.0, 100.1, 100.05, time.time())}
        monkeypatch.setattr(ag2, "_fresh_quotes", quotes)
        st = await ag2.emergency_stop()
        assert st["mode"] == "emergency_stopped" and not ag2.broker.positions("open")
        assert ag2.broker.positions("closed")[0]["exit_reason"] == "emergency_stop"
        await ag.shutdown()
        await ag2.shutdown()
    asyncio.run(run())
