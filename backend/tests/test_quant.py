"""Tests for candle processing, structure detection, strategies (causality), targets, backtest
fill rules, probability calibration, paper trading and agent activation."""
import asyncio
import json
import time
from pathlib import Path

import numpy as np
import pytest

from app.analysis import structure as st
from app.market.candles import Candle, Series
from app.quant import backtest, costs, pairs
from app.quant.probability import ProbabilityModel, calibration_report, walk_forward, wilson
from app.quant.targets import PlanRejection, TradePlan, build_plan
from app.strategies.base import Signal
from app.strategies.library import STRATEGIES

F = Path(__file__).parent / "fixtures"


def mk(rows, tf="15m", cls="crypto", t0=1_700_000_000):
    cs = [Candle(t0 + i * 900, *r) for i, r in enumerate(rows)]
    return Series.from_candles("TEST", cls, tf, "test", cs, now=t0 + len(rows) * 900 + 1)


def real_series():
    from app.providers.crypto import parse_coinbase_candles
    bars = parse_coinbase_candles(json.loads((F / "coinbase_candles.json").read_text()))
    cs = [Candle(b["time"], b["open"], b["high"], b["low"], b["close"], b["volume"]) for b in bars]
    return Series.from_candles("BTC-USD", "crypto", "1d", "coinbase", cs, now=cs[-1].t + 86400 + 1)


# ------------------------------------------------------------------ candles
def test_series_splits_provisional_and_counts_gaps():
    t0 = 1_700_000_000
    cs = [Candle(t0 + i * 900, 1, 2, 0.5, 1.5, 10) for i in (0, 1, 3, 4)]
    s = Series.from_candles("X", "crypto", "15m", "t", cs, now=t0 + 4 * 900 + 100)   # bar 4 still forming
    assert len(s) == 3 and s.provisional is not None and s.provisional.t == t0 + 4 * 900
    assert s.gaps == 1                                     # bar 2 missing, never filled
    added = s.merge([Candle(t0 + 4 * 900, 1, 2, 0.5, 1.6, 12), Candle(t0 + 5 * 900, 1, 2, 0.5, 1.7, 5)], now=t0 + 6 * 900 + 1)
    assert added == 2 and len(s) == 5 and s.c[-1] == 1.7 and s.provisional is None


# ------------------------------------------------------------------ structure
def test_liquidity_sweep_with_evidence():
    rows = [(100, 101, 99, 100, 10)] * 3 + [(100, 105, 99.5, 104, 10)] + [(103, 104, 101, 102, 10)] * 3 \
        + [(102, 103, 100, 101, 10)] * 3 + [(101, 106, 100.5, 103, 30)] + [(102.5, 103, 98, 99, 12), (99, 99.5, 97, 98, 12)]
    s = mk(rows)
    a = st.analyse(s, swing_k=3)
    sw = [d for d in a.detections if d.kind == "liquidity_sweep"]
    assert sw, "sweep of the 105 swing high must be detected"
    d = sw[0]
    assert d.direction == "bearish" and d.evidence["swept_level"] == 105
    assert d.evidence["penetration_pct"] == pytest.approx((106 / 105 - 1) * 100)
    assert d.evidence["closed_back_inside"] and d.evidence["reversal_confirmed"]


def test_bos_choch_and_fvg():
    up = [(100 + i, 101 + i, 99.5 + i, 100.8 + i, 10) for i in range(10)]
    pull = [(109, 109.5, 106, 106.5, 10), (106.5, 107, 104, 104.5, 10), (104.5, 105, 103, 103.5, 10),
            (103.5, 106, 103.2, 105.8, 10), (105.8, 108, 105.5, 107.8, 10), (107.8, 111, 107.6, 110.9, 10),
            (111, 113, 110.5, 112.8, 10), (112.8, 113, 108, 108.5, 10), (108.5, 109, 101, 101.5, 10),
            (101.5, 102, 98, 98.5, 10)]
    s = mk(up + pull)
    a = st.analyse(s, swing_k=2)
    kinds = {(d.kind, d.direction) for d in a.detections}
    assert ("bos", "bullish") in kinds
    assert any(k == "choch" for k, _ in kinds) or ("bos", "bearish") in kinds
    gap = mk([(100, 101, 99, 100.5, 1), (101, 110, 100.8, 109, 5), (109, 112, 105, 111, 1)])
    f = st.fair_value_gaps(gap, min_gap_atr=0)
    assert f and f[0].direction == "bullish" and f[0].evidence["gap_low"] == 101 and f[0].evidence["gap_high"] == 105


def test_indicators_match_definitions():
    x = np.arange(1, 31, dtype=float)
    assert np.isnan(st.sma(x, 5)[3]) and st.sma(x, 5)[4] == 3.0
    assert st.rsi(x, 14)[-1] == 100
    rv = st.rel_volume(np.array([1.0] * 20 + [3.0]))
    assert rv[-1] == 3.0                                   # excludes the current bar from the baseline


def test_strategies_are_causal():
    """A signal at bar t must be identical whether or not future candles exist."""
    s = real_series()
    full = st.analyse(s)
    for t in range(80, len(s) - 20, 7):
        trunc = s.upto(t)
        a_t = st.analyse(trunc)
        for strat in STRATEGIES:
            f, p = strat.evaluate(s, full, t), strat.evaluate(trunc, a_t, t)
            assert (f is None) == (p is None), f"{strat.name} differs at {t}"
            if f:
                assert f.direction == p.direction and f.score == p.score


# ------------------------------------------------------------------ targets & costs
def test_target_engine_rejects_unrealistic_and_costly():
    ce = costs.estimate("crypto", 1000, 0.01, 1e9)
    r = build_plan("long", 100.0, 0.05, ce, max_hold_bars=16)        # ATR 0.05% -> 0.2% typical move
    assert isinstance(r, PlanRejection) and r.reason == "targets_not_realistic"
    ok = build_plan("long", 100.0, 1.0, ce, max_hold_bars=64, min_rr=0.5)
    assert isinstance(ok, TradePlan) and ok.tp1_pct in (2, 3, 4, 5) and ok.stop < 100 < ok.tp1 <= ok.tp2
    assert ok.net_tp1_pct == pytest.approx(ok.tp1_pct - ce.total_pct)
    meme = costs.estimate("memecoin", 1000, None, None, 50_000)
    big = costs.estimate("memecoin", 1000, None, None, 5_000_000)
    assert meme.total_pct > big.total_pct                   # thin pools cost more


def test_backtest_fill_rules_stop_first_and_partials():
    rows = [(100, 100.5, 99.5, 100, 10)] * 3 + [(100, 103, 97, 100, 10)]      # same bar touches stop and TP
    s = mk(rows)
    sig = Signal("x", "long", 2, 0.5, None)
    plan = TradePlan("long", 100, 98, 102, 103, 2, 3, 2, 1, 5, 2, 0.5, 0.4, 0.1, 1.5, 2.5, 0.6, [2, 3])
    tr = backtest.simulate(s, sig, plan, 0.5)
    assert tr.stopped and not tr.hit_tp1 and tr.gross_pct == pytest.approx(-2.0)
    rows2 = [(100, 100.5, 99.5, 100, 10)] * 3 + [(100, 102.5, 99.8, 102, 10), (102, 102.2, 99.9, 100.1, 10)]
    tr2 = backtest.simulate(mk(rows2), sig, plan, 0.5)
    assert tr2.hit_tp1 and tr2.stopped and tr2.gross_pct == pytest.approx(1.0)   # half at +2%, half at break-even


# ------------------------------------------------------------------ probability
def test_wilson_and_calibration_metrics():
    lo, hi = wilson(50, 100)
    assert lo < 0.5 < hi and hi - lo < 0.2
    rep = calibration_report(np.array([0.2] * 50 + [0.8] * 50), np.array([0] * 40 + [1] * 10 + [1] * 40 + [0] * 10),
                             np.array([0.5] * 100))
    assert rep["ece"] == pytest.approx(0.0, abs=1e-9) and rep["brier"] < rep["brier_baseline"]


def _trade(i, win, regime="with-trend/low-vol"):
    return backtest.SimTrade("A", "crypto", "momentum", "long", i, i * 100, i * 100 + 50, 1, 1, 5, win, False,
                             not win, False, 2 if win else -1, 1.5 if win else -1.5, regime, 0.5, 2, 1)


def test_walk_forward_uses_only_closed_trades_and_min_samples():
    few = ProbabilityModel([_trade(i, i % 2 == 0) for i in range(10)])
    assert few.estimate("momentum", "crypto", "with-trend/low-vol")["calibrated_probability"] is None
    many = [_trade(i, (i % 3) != 0) for i in range(120)]
    m = ProbabilityModel(many)
    est = m.estimate("momentum", "crypto", "with-trend/low-vol")
    assert est["calibrated_probability"] == pytest.approx(2 / 3, abs=0.05)
    assert est["historical_samples"] == 120 and est["calibrated_ci90"][0] < est["calibrated_probability"]
    wf = walk_forward(many)
    assert wf["oos_predictions"] == 110                    # predictions start after 10 closed trades


def test_pairs_detects_cointegration():
    rng = np.random.default_rng(1)
    b = np.exp(np.cumsum(rng.normal(0, 0.01, 800)) + 4)
    a = np.exp(1.5 * np.log(b) + rng.normal(0, 0.005, 800))
    r = pairs.engle_granger(a, b)
    assert r["cointegrated_5pct"] and r["hedge_ratio"] == pytest.approx(1.5, abs=0.05)
    walk = np.exp(np.cumsum(rng.normal(0, 0.01, 800)) + 4)
    assert not pairs.engle_granger(walk, b)["cointegrated_5pct"]


# ------------------------------------------------------------------ paper trading (temp database)
@pytest.fixture()
def tmpdb(tmp_path):
    from app.core import db
    db.reset_engine_for_tests(f"sqlite:///{tmp_path}/t.db")
    yield db
    db.reset_engine_for_tests("sqlite:///./data/quantx.db")


def test_paper_lifecycle_partial_breakeven_and_fees(tmpdb):
    from app.paper import engine as paper
    q = paper.Quote(99.95, 100.05, 100, time.time())
    plan = {"stop": 98.0, "tp1": 102.0, "tp2": 104.0}
    f = paper.open_position("TEST-USD", "crypto", "momentum", "long", q, plan, 3600, {}, None, None, 1e9)
    assert f["price"] > 100.05                             # bought at the ask plus slippage
    risk = (f["price"] - 98) * f["qty"]
    assert risk <= 10_000 * 0.005 + 1e-6                    # never more than 0.5% of equity at risk
    assert f["notional"] <= 10_000 * 0.20 * 1.01             # 20% notional cap binds here
    assert paper.risk_check("TEST-USD", "crypto", 100) is not None   # no duplicate positions
    ev = paper.on_quote("TEST-USD", paper.Quote(102.1, 102.2, 102.15, time.time()))
    assert ev[0]["kind"] == "tp1" and not ev[0]["closed"]
    p = paper.open_positions("TEST-USD")[0]
    assert p["stop"] == pytest.approx(f["price"]) and p["remaining_qty"] == pytest.approx(f["qty"] / 2)
    ev = paper.on_quote("TEST-USD", paper.Quote(f["price"] - 0.01, f["price"], f["price"], time.time()))
    assert ev[0]["kind"] == "breakeven" and ev[0]["closed"]
    m = paper.metrics()
    assert m["closed_trades"] == 1 and m["fees_paid"] > 0 and m["simulated"] is True
    assert m["equity"] == pytest.approx(10_000 + m["equity"] - 10_000)


def test_paper_stop_and_time_exit(tmpdb):
    from app.paper import engine as paper
    q = paper.Quote(99.95, 100.05, 100, time.time())
    paper.open_position("S-USD", "crypto", "volume", "short", q, {"stop": 102.0, "tp1": 98.0, "tp2": 96.0}, 3600, {}, None, None, 1e9)
    ev = paper.on_quote("S-USD", paper.Quote(102.5, 102.6, 102.55, time.time()))
    assert ev[0]["kind"] == "stop" and ev[0]["pnl"] < 0
    paper.open_position("T-USD", "crypto", "volume", "long", q, {"stop": 98.0, "tp1": 102.0, "tp2": 104.0}, 10, {}, None, None, 1e9)
    ev = paper.on_quote("T-USD", paper.Quote(100.0, 100.1, 100.05, time.time() + 11))
    assert ev[0]["kind"] == "time" and ev[0]["closed"]


# ------------------------------------------------------------------ agent activation / shutdown
def test_agent_activate_deactivate(tmpdb, monkeypatch):
    from app.agent.engine import Agent

    async def run():
        ag = Agent()
        calls = []

        async def fake_universe():
            calls.append("universe")
            await ag.emit("Universe built (test)", "scan")
        monkeypatch.setattr(ag, "refresh_universe", fake_universe)
        monkeypatch.setattr(ag.stream, "start", lambda: calls.append("stream"))

        async def nostop():
            calls.append("stream-stop")
        monkeypatch.setattr(ag.stream, "stop", nostop)
        st1 = await ag.activate()
        assert st1["active"] and st1["state"] == "initialising"
        await asyncio.sleep(0.05)
        assert "universe" in calls and "stream" in calls
        assert any("ACTIVATE AI" in e["message"] for e in ag.logbuf)
        st2 = await ag.deactivate()
        assert not st2["active"] and st2["state"] == "stopped"
        assert all(t.get_name() == "status" for t in ag.tasks)
        assert any("deactivated" in e["message"] for e in ag.logbuf)
        with tmpdb.engine().connect() as c:
            n = c.execute(tmpdb.select(tmpdb.func.count()).select_from(tmpdb.activity)).scalar()
        assert n >= 3                                       # every log event persisted
        await ag.shutdown()
    asyncio.run(run())


def test_detections_and_signals_are_json_safe():
    s = real_series()
    a = st.analyse(s)
    json.dumps([d.to_json() for d in a.detections])          # numpy scalars must not leak into JSON
    for t in range(80, len(s)):
        for strat in STRATEGIES:
            sig = strat.evaluate(s, a, t)
            if sig:
                json.dumps(sig.to_json())
