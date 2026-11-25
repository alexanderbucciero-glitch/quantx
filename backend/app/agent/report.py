"""Research report for a setup. Every sentence is derived from computed values; nothing is invented.
The 'AI explanation' is generated deterministically from the evidence (no language model needed,
so it cannot hallucinate numbers)."""
from __future__ import annotations

import time

from ..analysis.structure import Analysis, market_structure_label, plain
from ..market.candles import Series
from ..strategies.base import Signal
from ..strategies.library import BY_NAME


def _fmt(x: float | None, d: int = 2, pct: bool = False) -> str:
    if x is None:
        return "n/a"
    return f"{x * 100:.{d}f}%" if pct else f"{x:,.{d}f}"


def build(asset: dict, s: Series, a: Analysis, sig: Signal, plan: dict | None, plan_rejection: dict | None,
          prob: dict, news: list[dict], gates: list[dict], price: float) -> dict:
    t = sig.t
    recent = a.recent(24, len(s))
    structure = [d.to_json() for d in recent if d.kind in ("bos", "choch", "liquidity_sweep", "failed_breakout", "order_block", "fvg")][-8:]
    bull = list(sig.bullish)
    bear = list(sig.bearish)
    for d in recent:
        line = f"{d.title} at {d.price:.6g}"
        if d.kind in ("pattern", "divergence", "volume_imbalance") or d.kind in ("bos", "choch"):
            (bull if d.direction == "bullish" else bear if d.direction == "bearish" else []).append(line)
    if a.trend == "bullish":
        bull.append("Last structure break was bullish")
    elif a.trend == "bearish":
        bear.append("Last structure break was bearish")
    rv = a.rel_volume[t]
    atr_pct = 100 * a.atr[t] / s.c[t] if s.c[t] else None
    invalid = []
    if plan:
        invalid.append(f"A traded price beyond the stop at {plan['stop']:.6g} ({plan['stop_pct']:.2f}% from entry)")
    if sig.invalidation is not None:
        invalid.append(f"Structural invalidation level {sig.invalidation:.6g}")
    invalid.append(f"No progress toward TP1 within {plan['max_hold_bars'] if plan else '?'} bars ({s.tf} candles)")

    p_cal = prob.get("calibrated_probability")
    accepted = all(g["passed"] for g in gates)
    verdict_reason = next((g["detail"] for g in gates if not g["passed"]), None)
    strat = BY_NAME.get(sig.strategy)
    lines = [
        f"{asset['symbol']} ({asset['asset_class']}) — {strat.label if strat else sig.strategy} setup, {sig.direction.upper()} on the {s.tf} chart.",
        f"Trigger: {(sig.bullish if sig.direction == 'long' else sig.bearish)[0] if (sig.bullish or sig.bearish) else 'rule conditions met'}.",
        f"Market structure: {market_structure_label(a.swings)}; ATR {atr_pct:.2f}% per bar; relative volume {rv:.1f}×." if atr_pct is not None and rv == rv else
        f"Market structure: {market_structure_label(a.swings)}.",
    ]
    if plan:
        lines.append(f"Plan: entry ≈ {plan['entry']:.6g}, stop {plan['stop']:.6g}, TP1 {plan['tp1']:.6g} (+{plan['tp1_pct']:.0f}% gross), "
                     f"TP2 {plan['tp2']:.6g}; round-trip costs ≈ {plan['costs_pct']:.2f}% → reward/risk after costs {plan['rr_net']:.2f}.")
    elif plan_rejection:
        lines.append(f"No viable plan: {plan_rejection['reason'].replace('_', ' ')}.")
    if prob.get("historical_samples"):
        lines.append(f"History: {prob['historical_samples']} comparable simulated setups, TP1 reached in "
                     f"{_fmt(prob.get('historical_win_rate'), 1, True)} (90% CI {_fmt((prob.get('historical_win_rate_ci90') or [None])[0], 1, True)}–"
                     f"{_fmt((prob.get('historical_win_rate_ci90') or [None, None])[1], 1, True)}).")
    if p_cal is not None:
        lines.append(f"Walk-forward calibrated probability in regime '{prob['regime']}': {_fmt(p_cal, 1, True)}; "
                     f"expected net result {prob['expected_net_pct']:+.2f}% per trade after costs.")
    else:
        lines.append(f"No calibrated probability: {prob.get('reason') or 'insufficient evidence'}.")
    if news:
        lines.append(f"{len(news)} recent headline(s) mention this asset; media headlines are unverified.")
    lines.append("Decision: " + ("simulated position opened." if accepted else f"rejected — {verdict_reason}."))

    return plain({
        "asset": asset["symbol"], "asset_class": asset["asset_class"], "price": price, "timestamp": time.time(),
        "candle_time": int(s.t[t]), "timeframe": s.tf, "direction": sig.direction, "strategy": sig.strategy,
        "strategy_label": strat.label if strat else sig.strategy,
        "plan": plan, "plan_rejection": plan_rejection,
        "liquidity": {"volume_24h_usd": asset.get("volume_24h_usd"), "liquidity_usd": asset.get("liquidity_usd"),
                      "spread_pct": asset.get("spread_pct"), "pools": a.pools},
        "volume": {"relative_volume": None if rv != rv else float(rv)},
        "market_structure": {"trend": a.trend, "label": market_structure_label(a.swings), "recent": structure},
        "news": news[:5], "bullish_evidence": bull[:10], "bearish_evidence": bear[:10],
        "invalidation": invalid,
        "confidence": {
            "ai_confidence": sig.score,
            "ai_confidence_note": "Heuristic strength of the rule evidence (0–1). It is NOT a probability of profit.",
            "historical_win_rate": prob.get("historical_win_rate"),
            "historical_samples": prob.get("historical_samples"),
            "calibrated_probability": p_cal,
            "calibrated_ci90": prob.get("calibrated_ci90"),
            "expected_net_pct": prob.get("expected_net_pct"),
            "validation": prob.get("validation"),
            "insufficient_evidence": prob.get("insufficient", True),
            "reason": prob.get("reason"),
            "disclaimer": "Historical simulated results do not guarantee future outcomes.",
        },
        "gates": gates, "accepted": accepted, "explanation": " ".join(lines),
        "volume_24h_usd": asset.get("volume_24h_usd"), "liquidity_usd": asset.get("liquidity_usd"),
    })
