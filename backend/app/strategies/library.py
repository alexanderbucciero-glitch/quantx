"""Independent quantitative strategies. Fixed, documented rules (no curve-fitted parameters),
so walk-forward results are genuinely out-of-sample for every fold."""
from __future__ import annotations

import numpy as np

from ..analysis.structure import Analysis
from ..market.candles import Series
from .base import Signal, Strategy, known, nz


class Momentum(Strategy):
    name, label = "momentum", "Momentum"
    description = "Accelerating move in trend direction with unusual relative volume and a fresh break of structure."

    def evaluate(self, s: Series, a: Analysis, t: int) -> Signal | None:
        if t < 60:
            return None
        c = s.c
        e20, e50 = a.ema20[t], a.ema50[t]
        roc6, roc3 = c[t] / c[t - 6] - 1, c[t] / c[t - 3] - 1
        rv = nz(a.rel_volume[t]) or 0
        bos = known(a, t, ("bos", "choch"), 6)
        for direction, sign in (("long", 1), ("short", -1)):
            if not self.allowed(s, direction):
                continue
            trend_ok = (c[t] > e20 > e50) if sign > 0 else (c[t] < e20 < e50)
            accel = sign * roc6 > 0 and sign * roc3 > 0.6 * sign * roc6
            brk = [d for d in bos if d.direction == ("bullish" if sign > 0 else "bearish")]
            if trend_ok and accel and rv >= 1.5 and brk:
                score = min(1.0, 0.35 + 0.15 * min(rv, 4) / 4 * 2 + 0.2 * (len(brk) > 0) + 0.1 * (abs(roc6) > 0.01))
                return Signal(self.name, direction, t, round(score, 3),
                              invalidation=float(np.min(s.l[t - 5:t + 1]) if sign > 0 else np.max(s.h[t - 5:t + 1])),
                              bullish=[f"Close above EMA20 > EMA50", f"6-bar return {roc6 * 100:+.2f}%, accelerating",
                                       f"Relative volume {rv:.1f}×", brk[-1].title] if sign > 0 else [],
                              bearish=[f"Close below EMA20 < EMA50", f"6-bar return {roc6 * 100:+.2f}%, accelerating",
                                       f"Relative volume {rv:.1f}×", brk[-1].title] if sign < 0 else [],
                              features={"roc6": roc6, "roc3": roc3, "rel_volume": rv}, trigger=brk[-1].to_json())
        return None


class MeanReversion(Strategy):
    name, label = "mean_reversion", "Mean reversion"
    description = "Statistically stretched move (z-score vs 20-bar mean ≤ −2.5 with RSI extreme) showing a reversal candle."

    def evaluate(self, s: Series, a: Analysis, t: int) -> Signal | None:
        if t < 60:
            return None
        m, sd = a.sma20[t], a.std20[t]
        if not sd or np.isnan(sd):
            return None
        z = (s.c[t] - m) / sd
        r = nz(a.rsi[t])
        if r is None:
            return None
        rev_up = s.c[t] > s.o[t] and s.l[t] < s.l[t - 1]
        rev_dn = s.c[t] < s.o[t] and s.h[t] > s.h[t - 1]
        if z <= -2.5 and r < 28 and rev_up and self.allowed(s, "long"):
            return Signal(self.name, "long", t, round(min(1.0, 0.4 + 0.1 * (abs(z) - 2.5) + 0.2 * (r < 20)), 3),
                          float(s.l[t]), bullish=[f"z-score {z:.2f} vs 20-bar mean", f"RSI {r:.1f}", "Reversal candle (higher close after new low)"],
                          bearish=["Trading against short-term momentum"], features={"z": z, "rsi": r, "mean": m})
        if z >= 2.5 and r > 72 and rev_dn and self.allowed(s, "short"):
            return Signal(self.name, "short", t, round(min(1.0, 0.4 + 0.1 * (z - 2.5) + 0.2 * (r > 80)), 3),
                          float(s.h[t]), bullish=["Trading against short-term momentum"],
                          bearish=[f"z-score {z:.2f} vs 20-bar mean", f"RSI {r:.1f}", "Reversal candle (lower close after new high)"],
                          features={"z": z, "rsi": r, "mean": m})
        return None


class LiquiditySweep(Strategy):
    name, label = "liquidity", "Liquidity sweep"
    description = "Sweep of resting liquidity (swing high/low) that closes back inside, with volume and a confirming close."

    def evaluate(self, s: Series, a: Analysis, t: int) -> Signal | None:
        for d in reversed(known(a, t, ("liquidity_sweep",), 4)):
            cb = d.evidence.get("confirmation_bar")
            if cb is None or cb != t:          # trigger exactly on the confirming close (known at t)
                continue
            direction = "long" if d.direction == "bullish" else "short"
            if not self.allowed(s, direction):
                continue
            vr = d.evidence.get("volume_ratio") or 0
            score = 0.4 + 0.2 * (vr >= 1.2) + 0.1 * (vr >= 2) + 0.1 * (d.evidence["penetration_pct"] < 1.0)
            inval = d.evidence.get("sweep_low") if direction == "long" else d.evidence.get("sweep_high")
            ev = [f"{d.title} of {d.evidence['level_type']} {d.evidence['swept_level']:.6g}",
                  f"Penetration {d.evidence['penetration_pct']:.2f}% then close back inside",
                  f"Volume {vr:.1f}× 20-bar average" if vr else "Volume unavailable",
                  "Next candle closed beyond the sweep candle (reversal confirmed)"]
            return Signal(self.name, direction, t, round(min(score, 1.0), 3), inval,
                          bullish=ev if direction == "long" else [], bearish=ev if direction == "short" else [],
                          features={"penetration_pct": d.evidence["penetration_pct"], "volume_ratio": vr},
                          trigger=d.to_json())
        return None


class Breakout(Strategy):
    name, label = "breakout", "Breakout"
    description = "Tight consolidation (Bollinger bandwidth in lowest 20% of last 120 bars) then a close beyond the range on ≥1.8× volume."

    def evaluate(self, s: Series, a: Analysis, t: int) -> Signal | None:
        if t < 140:
            return None
        bw = (4 * a.std20[: t + 1]) / a.sma20[: t + 1]
        prev = bw[t - 1]
        hist = bw[t - 121:t - 1]
        hist = hist[~np.isnan(hist)]
        if hist.size < 60 or np.isnan(prev) or prev > np.percentile(hist, 20):
            return None
        hi, lo = s.h[t - 20:t].max(), s.l[t - 20:t].min()
        rv = nz(a.rel_volume[t]) or 0
        if rv < 1.8:
            return None
        if s.c[t] > hi and self.allowed(s, "long"):
            return Signal(self.name, "long", t, round(min(1.0, 0.45 + 0.1 * min(rv - 1.8, 3)), 3), float(lo),
                          bullish=[f"20-bar range {lo:.6g}–{hi:.6g} after volatility contraction", f"Close {s.c[t]:.6g} above range high", f"Relative volume {rv:.1f}×"],
                          features={"range_high": hi, "range_low": lo, "rel_volume": rv, "bandwidth": prev})
        if s.c[t] < lo and self.allowed(s, "short"):
            return Signal(self.name, "short", t, round(min(1.0, 0.45 + 0.1 * min(rv - 1.8, 3)), 3), float(hi),
                          bearish=[f"20-bar range {lo:.6g}–{hi:.6g} after volatility contraction", f"Close {s.c[t]:.6g} below range low", f"Relative volume {rv:.1f}×"],
                          features={"range_high": hi, "range_low": lo, "rel_volume": rv, "bandwidth": prev})
        return None


class VolumeSurge(Strategy):
    name, label = "volume", "Volume surge"
    description = "Volume ≥3× normal on a decisive candle closing in its top/bottom quarter, without bearish/bullish divergence."

    def evaluate(self, s: Series, a: Analysis, t: int) -> Signal | None:
        if t < 60:
            return None
        rv = nz(a.rel_volume[t]) or 0
        rng = s.h[t] - s.l[t]
        if rv < 3 or rng <= 0:
            return None
        pos = (s.c[t] - s.l[t]) / rng
        div = known(a, t, ("divergence",), 15)
        if pos >= 0.75 and s.c[t] > s.o[t] and self.allowed(s, "long") and not any(d.direction == "bearish" for d in div):
            return Signal(self.name, "long", t, round(min(1.0, 0.4 + 0.05 * min(rv, 10)), 3), float(s.l[t]),
                          bullish=[f"Relative volume {rv:.1f}×", f"Closed in top {100 - pos * 100:.0f}% of range"],
                          features={"rel_volume": rv, "close_position": pos})
        if pos <= 0.25 and s.c[t] < s.o[t] and self.allowed(s, "short") and not any(d.direction == "bullish" for d in div):
            return Signal(self.name, "short", t, round(min(1.0, 0.4 + 0.05 * min(rv, 10)), 3), float(s.h[t]),
                          bearish=[f"Relative volume {rv:.1f}×", f"Closed in bottom {pos * 100:.0f}% of range"],
                          features={"rel_volume": rv, "close_position": pos})
        return None


class TrendFollow(Strategy):
    name, label = "trend", "Trend following"
    description = ("Enters when a trend newly forms (close > EMA20 > EMA50, 20-bar return > 0, MACD histogram > 0); "
                   "no fixed target — a 3×ATR trailing stop and a close back through EMA20 end the trade.")
    exit_mode = "trail"
    trail_atr = 3.0

    @staticmethod
    def _state(s: Series, a: Analysis, t: int) -> int:
        e20, e50, h = a.ema20[t], a.ema50[t], a.macd_hist[t]
        if t < 60 or np.isnan(e50) or np.isnan(h):
            return 0
        if s.c[t] > e20 > e50 and s.c[t] > s.c[t - 20] and h > 0:
            return 1
        if s.c[t] < e20 < e50 and s.c[t] < s.c[t - 20] and h < 0:
            return -1
        return 0

    def evaluate(self, s: Series, a: Analysis, t: int) -> Signal | None:
        st, prev = self._state(s, a, t), self._state(s, a, t - 1)
        if st == 0 or st == prev:          # only a NEW trend state triggers an entry
            return None
        direction = "long" if st > 0 else "short"
        if not self.allowed(s, direction):
            return None
        roc = s.c[t] / s.c[t - 20] - 1
        ev = [f"Close {'above' if st > 0 else 'below'} EMA20 {'>' if st > 0 else '<'} EMA50",
              f"20-bar return {roc * 100:+.2f}%", f"MACD histogram {a.macd_hist[t]:+.4g}"]
        return Signal(self.name, direction, t, round(min(1.0, 0.4 + min(abs(roc) * 5, 0.4)), 3),
                      float(s.c[t] - st * 3 * a.atr[t]), bullish=ev if st > 0 else [], bearish=ev if st < 0 else [],
                      features={"roc20": roc, "macd_hist": float(a.macd_hist[t])}, exit_mode="trail")

    def should_exit(self, s: Series, a: Analysis, t: int, direction: str) -> str | None:
        e20 = a.ema20[t]
        if np.isnan(e20):
            return None
        if direction == "long" and s.c[t] < e20:
            return f"close {s.c[t]:.6g} fell below EMA20 {e20:.6g} (trend exit)"
        if direction == "short" and s.c[t] > e20:
            return f"close {s.c[t]:.6g} rose above EMA20 {e20:.6g} (trend exit)"
        return None


class BollingerReversion(Strategy):
    name, label = "bollinger", "Bollinger reversion"
    description = "Close back inside the Bollinger band after a close outside it, with RSI confirming, in a non-trending market."

    def evaluate(self, s: Series, a: Analysis, t: int) -> Signal | None:
        if t < 60 or np.isnan(a.std20[t]) or np.isnan(a.rsi[t]):
            return None
        up, lo = a.bb_upper, a.bb_lower
        flat = abs(a.ema20[t] / a.ema50[t] - 1) < 0.5 * a.atr[t] / s.c[t]
        if not flat:
            return None
        if s.c[t - 1] < lo[t - 1] and s.c[t] > lo[t] and a.rsi[t] < 40 and self.allowed(s, "long"):
            return Signal(self.name, "long", t, 0.45, float(min(s.l[t - 1], s.l[t])),
                          bullish=["Close back inside lower Bollinger band", f"RSI {a.rsi[t]:.1f}", "EMA20≈EMA50 (range)"],
                          features={"rsi": float(a.rsi[t])})
        if s.c[t - 1] > up[t - 1] and s.c[t] < up[t] and a.rsi[t] > 60 and self.allowed(s, "short"):
            return Signal(self.name, "short", t, 0.45, float(max(s.h[t - 1], s.h[t])),
                          bearish=["Close back inside upper Bollinger band", f"RSI {a.rsi[t]:.1f}", "EMA20≈EMA50 (range)"],
                          features={"rsi": float(a.rsi[t])})
        return None


STRATEGIES: list[Strategy] = [Momentum(), MeanReversion(), LiquiditySweep(), Breakout(), VolumeSurge(), TrendFollow(), BollingerReversion()]
BY_NAME = {x.name: x for x in STRATEGIES}

# Not paper-traded yet, listed so the UI states their status honestly.
RESEARCH_ONLY = {
    "statistical": "Pairs / cointegration relative value — shown on the Model Performance page; two-leg positions are not simulated yet.",
    "event": "Event-driven — needs intraday equity prices (free Tiingo key) to measure reactions to SEC 8-K events.",
}
