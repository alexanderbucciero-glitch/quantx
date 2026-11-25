"""Market-structure and candlestick analysis on CLOSED candles.

Every detection carries the exact bars and price levels that justify it, so the UI
and the AI report can quote the evidence. Nothing here predicts; it describes.

Definitions used (documented so results are reproducible):
- Swing high at i: high[i] is the strict max of highs[i-k .. i+k] (k = `swing_k`). A swing is only
  CONFIRMED k bars later, and every detection uses a swing only after its confirmation bar.
- Buy-side liquidity: unswept swing highs (stops of shorts / breakout buy orders rest above them).
  Sell-side liquidity: unswept swing lows.
- Liquidity sweep: a candle trades beyond an unswept swing level and CLOSES back on the other side.
- BOS (break of structure): close beyond the latest confirmed swing in the direction of the current trend.
- CHoCH (change of character): close beyond the latest confirmed swing AGAINST the current trend.
- FVG (fair value gap): 3-candle pattern where low[i] > high[i-2] (bullish) or high[i] < low[i-2] (bearish).
- Order block: last opposite-direction candle before the displacement leg that produced a BOS/CHoCH.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np

from ..market.candles import Series


# ------------------------------------------------------------------ indicators (vectorised)
def ema(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(x.size, np.nan)
    if x.size < n:
        return out
    k = 2 / (n + 1)
    out[n - 1] = x[:n].mean()
    for i in range(n, x.size):
        out[i] = x[i] * k + out[i - 1] * (1 - k)
    return out


def sma(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(x.size, np.nan)
    if x.size >= n:
        cs = np.cumsum(np.insert(x, 0, 0.0))
        out[n - 1:] = (cs[n:] - cs[:-n]) / n
    return out


def rolling_std(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(x.size, np.nan)
    if x.size >= n:
        w = np.lib.stride_tricks.sliding_window_view(x, n)
        out[n - 1:] = w.std(axis=1)
    return out


def atr(s: Series, n: int = 14) -> np.ndarray:
    if len(s) == 0:
        return np.zeros(0)
    prev = np.concatenate([[s.c[0]], s.c[:-1]])
    tr = np.maximum(s.h - s.l, np.maximum(np.abs(s.h - prev), np.abs(s.l - prev)))
    out = np.full(tr.size, np.nan)
    if tr.size >= n:
        out[n - 1] = tr[:n].mean()
        for i in range(n, tr.size):
            out[i] = (out[i - 1] * (n - 1) + tr[i]) / n
    return out


def rsi(x: np.ndarray, n: int = 14) -> np.ndarray:
    out = np.full(x.size, np.nan)
    if x.size <= n:
        return out
    d = np.diff(x)
    g, l_ = np.maximum(d, 0), np.maximum(-d, 0)
    ag, al = g[:n].mean(), l_[:n].mean()
    out[n] = 100 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(n + 1, x.size):
        ag = (ag * (n - 1) + g[i - 1]) / n
        al = (al * (n - 1) + l_[i - 1]) / n
        out[i] = 100 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def rel_volume(v: np.ndarray, n: int = 20) -> np.ndarray:
    """volume / mean of the PREVIOUS n bars (excludes the current bar)."""
    out = np.full(v.size, np.nan)
    if v.size > n:
        base = sma(v, n)
        out[n:] = v[n:] / np.where(base[n - 1:-1] > 0, base[n - 1:-1], np.nan)
    return out


# ------------------------------------------------------------------ data classes
@dataclass
class Swing:
    i: int
    t: int
    kind: str           # 'high' | 'low'
    price: float
    confirmed_at: int   # bar index at which the swing became known
    label: str = ""     # HH / LH / HL / LL
    swept_at: int | None = None


@dataclass
class Detection:
    kind: str           # liquidity_sweep | bos | choch | fvg | order_block | sr_level | pattern | ...
    direction: str      # bullish | bearish | neutral
    i: int              # bar index where it was detected
    t: int
    price: float
    title: str
    evidence: dict = field(default_factory=dict)
    provisional: bool = False

    def to_json(self) -> dict:
        return plain(asdict(self))


def plain(v):
    """Convert numpy scalars/arrays (and NaN) inside nested structures to JSON-safe Python values."""
    if isinstance(v, dict):
        return {k: plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [plain(x) for x in v]
    if isinstance(v, np.generic):
        v = v.item()
    if isinstance(v, np.ndarray):
        return [plain(x) for x in v.tolist()]
    if isinstance(v, float) and v != v:
        return None
    return v


# ------------------------------------------------------------------ swings & structure
def find_swings(s: Series, k: int = 3) -> list[Swing]:
    h, l_, n = s.h, s.l, len(s)
    out: list[Swing] = []
    for i in range(k, n - k):
        wh, wl = h[i - k:i + k + 1], l_[i - k:i + k + 1]
        if h[i] == wh.max() and np.sum(wh == h[i]) == 1:
            out.append(Swing(i, int(s.t[i]), "high", float(h[i]), i + k))
        if l_[i] == wl.min() and np.sum(wl == l_[i]) == 1:
            out.append(Swing(i, int(s.t[i]), "low", float(l_[i]), i + k))
    out.sort(key=lambda x: (x.i, x.kind))
    last = {"high": None, "low": None}
    for sw in out:
        prev = last[sw.kind]
        if prev is not None:
            if sw.kind == "high":
                sw.label = "HH" if sw.price > prev.price else "LH"
            else:
                sw.label = "HL" if sw.price > prev.price else "LL"
        last[sw.kind] = sw
    return out


def _vol_ratio(s: Series, i: int, n: int = 20) -> float | None:
    if i < n:
        return None
    base = s.v[i - n:i].mean()
    return float(s.v[i] / base) if base > 0 else None


def structure_events(s: Series, swings: list[Swing]) -> tuple[list[Detection], str]:
    """Walk bars forward; emit BOS / CHoCH when a close breaks the latest confirmed swing."""
    events: list[Detection] = []
    trend = "neutral"
    last_high: Swing | None = None
    last_low: Swing | None = None
    by_conf: dict[int, list[Swing]] = {}
    for sw in swings:
        by_conf.setdefault(sw.confirmed_at, []).append(sw)
    broken_high = broken_low = None
    for i in range(len(s)):
        for sw in by_conf.get(i, []):
            if sw.kind == "high":
                last_high = sw
            else:
                last_low = sw
        c = s.c[i]
        if last_high and broken_high is not last_high and c > last_high.price:
            kind = "bos" if trend in ("bullish", "neutral") else "choch"
            events.append(Detection(kind, "bullish", i, int(s.t[i]), last_high.price,
                                    f"{'Break of structure' if kind == 'bos' else 'Change of character'} (bullish)",
                                    {"broken_level": last_high.price, "level_time": last_high.t, "close": float(c),
                                     "close_beyond_pct": float(c / last_high.price - 1) * 100,
                                     "prior_trend": trend, "volume_ratio": _vol_ratio(s, i)}))
            trend, broken_high = "bullish", last_high
        if last_low and broken_low is not last_low and c < last_low.price:
            kind = "bos" if trend in ("bearish", "neutral") else "choch"
            events.append(Detection(kind, "bearish", i, int(s.t[i]), last_low.price,
                                    f"{'Break of structure' if kind == 'bos' else 'Change of character'} (bearish)",
                                    {"broken_level": last_low.price, "level_time": last_low.t, "close": float(c),
                                     "close_beyond_pct": float(1 - c / last_low.price) * 100,
                                     "prior_trend": trend, "volume_ratio": _vol_ratio(s, i)}))
            trend, broken_low = "bearish", last_low
    return events, trend


def liquidity_sweeps(s: Series, swings: list[Swing], confirm_bars: int = 2, max_age: int = 120) -> list[Detection]:
    """Sweep = wick beyond an unswept, confirmed swing level with a close back inside the range."""
    out: list[Detection] = []
    n = len(s)
    for sw in swings:
        start = sw.confirmed_at + 1
        for i in range(start, min(n, sw.i + max_age)):
            if sw.kind == "high" and s.h[i] > sw.price:
                sw.swept_at = i
                if s.c[i] < sw.price:   # took buy-side liquidity, closed back below -> bearish sweep
                    conf = [j for j in range(i + 1, min(n, i + 1 + confirm_bars)) if s.c[j] < s.l[i]]
                    pending = i + confirm_bars >= n
                    out.append(Detection("liquidity_sweep", "bearish", i, int(s.t[i]), sw.price,
                        "Buy-side liquidity sweep",
                        {"swept_level": sw.price, "level_type": "swing high", "level_time": sw.t,
                         "penetration_pct": float(s.h[i] / sw.price - 1) * 100, "sweep_high": float(s.h[i]),
                         "closed_back_inside": True, "close": float(s.c[i]),
                         "volume_ratio": _vol_ratio(s, i), "volume_confirmed": (_vol_ratio(s, i) or 0) >= 1.2,
                         "reversal_confirmed": bool(conf), "confirmation_bar": conf[0] if conf else None,
                         "confirmation_pending": pending and not conf}))
                break
            if sw.kind == "low" and s.l[i] < sw.price:
                sw.swept_at = i
                if s.c[i] > sw.price:
                    conf = [j for j in range(i + 1, min(n, i + 1 + confirm_bars)) if s.c[j] > s.h[i]]
                    pending = i + confirm_bars >= n
                    out.append(Detection("liquidity_sweep", "bullish", i, int(s.t[i]), sw.price,
                        "Sell-side liquidity sweep",
                        {"swept_level": sw.price, "level_type": "swing low", "level_time": sw.t,
                         "penetration_pct": float(1 - s.l[i] / sw.price) * 100, "sweep_low": float(s.l[i]),
                         "closed_back_inside": True, "close": float(s.c[i]),
                         "volume_ratio": _vol_ratio(s, i), "volume_confirmed": (_vol_ratio(s, i) or 0) >= 1.2,
                         "reversal_confirmed": bool(conf), "confirmation_bar": conf[0] if conf else None,
                         "confirmation_pending": pending and not conf}))
                break
    return out


def failed_breakouts(s: Series, swings: list[Swing], within: int = 3) -> list[Detection]:
    """Close beyond a swing level, then a close back inside within `within` bars."""
    out = []
    n = len(s)
    for sw in swings:
        for i in range(sw.confirmed_at + 1, min(n, sw.i + 120)):
            if sw.kind == "high" and s.c[i] > sw.price:
                back = [j for j in range(i + 1, min(n, i + 1 + within)) if s.c[j] < sw.price]
                if back:
                    out.append(Detection("failed_breakout", "bearish", back[0], int(s.t[back[0]]), sw.price,
                        "Failed breakout above swing high",
                        {"level": sw.price, "breakout_bar": i, "breakout_close": float(s.c[i]),
                         "failed_bar": back[0], "failed_close": float(s.c[back[0]])}))
                break
            if sw.kind == "low" and s.c[i] < sw.price:
                back = [j for j in range(i + 1, min(n, i + 1 + within)) if s.c[j] > sw.price]
                if back:
                    out.append(Detection("failed_breakout", "bullish", back[0], int(s.t[back[0]]), sw.price,
                        "Failed breakdown below swing low",
                        {"level": sw.price, "breakdown_bar": i, "breakdown_close": float(s.c[i]),
                         "failed_bar": back[0], "failed_close": float(s.c[back[0]])}))
                break
    return out


def fair_value_gaps(s: Series, min_gap_atr: float = 0.15) -> list[Detection]:
    out = []
    a = atr(s)
    for i in range(2, len(s)):
        ref = a[i] if not np.isnan(a[i]) else (s.h[i] - s.l[i])
        if s.l[i] > s.h[i - 2] and (s.l[i] - s.h[i - 2]) >= min_gap_atr * ref:
            lo, hi = float(s.h[i - 2]), float(s.l[i])
            filled = next((j for j in range(i + 1, len(s)) if s.l[j] <= lo), None)
            out.append(Detection("fvg", "bullish", i, int(s.t[i]), (lo + hi) / 2, "Bullish fair value gap",
                                 {"gap_low": lo, "gap_high": hi, "gap_pct": (hi / lo - 1) * 100,
                                  "filled_at": filled, "open": filled is None}))
        if s.h[i] < s.l[i - 2] and (s.l[i - 2] - s.h[i]) >= min_gap_atr * ref:
            lo, hi = float(s.h[i]), float(s.l[i - 2])
            filled = next((j for j in range(i + 1, len(s)) if s.h[j] >= hi), None)
            out.append(Detection("fvg", "bearish", i, int(s.t[i]), (lo + hi) / 2, "Bearish fair value gap",
                                 {"gap_low": lo, "gap_high": hi, "gap_pct": (hi / lo - 1) * 100,
                                  "filled_at": filled, "open": filled is None}))
    return out


def order_blocks(s: Series, events: list[Detection], lookback: int = 12) -> list[Detection]:
    out = []
    for e in events:
        if e.kind not in ("bos", "choch"):
            continue
        rng = range(e.i - 1, max(-1, e.i - lookback), -1)
        want_bear = e.direction == "bullish"   # bullish OB = last bearish candle before up-move
        j = next((k for k in rng if (s.c[k] < s.o[k]) == want_bear and s.c[k] != s.o[k]), None)
        if j is None:
            continue
        lo, hi = float(s.l[j]), float(s.h[j])
        mitigated = next((m for m in range(e.i + 1, len(s)) if (s.l[m] <= hi if want_bear else s.h[m] >= lo)), None)
        out.append(Detection("order_block", e.direction, j, int(s.t[j]), (lo + hi) / 2,
                             f"{'Bullish' if want_bear else 'Bearish'} order block",
                             {"zone_low": lo, "zone_high": hi, "caused": e.kind, "break_bar": e.i,
                              "mitigated_at": mitigated}))
    return out


def sr_levels(s: Series, swings: list[Swing], tol_atr: float = 0.35, max_levels: int = 8) -> list[Detection]:
    if not swings or len(s) < 20:
        return []
    a = np.nanmedian(atr(s)[-100:]) if len(s) >= 15 else float(np.mean(s.h - s.l))
    tol = tol_atr * a
    clusters: list[list[Swing]] = []
    for sw in sorted(swings, key=lambda x: x.price):
        if clusters and abs(sw.price - np.mean([x.price for x in clusters[-1]])) <= tol:
            clusters[-1].append(sw)
        else:
            clusters.append([sw])
    last = float(s.c[-1])
    out = []
    for cl in clusters:
        if len(cl) < 2:
            continue
        price = float(np.mean([x.price for x in cl]))
        out.append(Detection("sr_level", "bullish" if price < last else "bearish", cl[-1].i, cl[-1].t, price,
                             "Support" if price < last else "Resistance",
                             {"touches": len(cl), "first_touch": cl[0].t, "last_touch": cl[-1].t,
                              "distance_pct": (price / last - 1) * 100,
                              "equal_levels": len(cl) >= 2 and (max(x.price for x in cl) - min(x.price for x in cl)) <= 0.1 * a}))
    out.sort(key=lambda d: (-d.evidence["touches"], abs(d.evidence["distance_pct"])))
    return out[:max_levels]


def candle_patterns(s: Series, start: int = 0) -> list[Detection]:
    out = []
    o, h, l_, c = s.o, s.h, s.l, s.c
    for i in range(max(1, start), len(s)):
        rng = h[i] - l_[i]
        if rng <= 0:
            continue
        body = abs(c[i] - o[i])
        upper, lower = h[i] - max(o[i], c[i]), min(o[i], c[i]) - l_[i]
        pb, pc, po = abs(c[i - 1] - o[i - 1]), c[i - 1], o[i - 1]
        if c[i] > o[i] and pc < po and c[i] >= po and o[i] <= pc and body > pb:
            out.append(Detection("pattern", "bullish", i, int(s.t[i]), float(c[i]), "Bullish engulfing", {"body_ratio": body / max(pb, 1e-12)}))
        elif c[i] < o[i] and pc > po and c[i] <= po and o[i] >= pc and body > pb:
            out.append(Detection("pattern", "bearish", i, int(s.t[i]), float(c[i]), "Bearish engulfing", {"body_ratio": body / max(pb, 1e-12)}))
        if lower >= 2 * body and upper <= 0.3 * rng and body > 0.05 * rng:
            out.append(Detection("pattern", "bullish", i, int(s.t[i]), float(c[i]), "Hammer", {"lower_wick_to_body": lower / body}))
        if upper >= 2 * body and lower <= 0.3 * rng and body > 0.05 * rng:
            out.append(Detection("pattern", "bearish", i, int(s.t[i]), float(c[i]), "Shooting star", {"upper_wick_to_body": upper / body}))
        if body <= 0.08 * rng:
            out.append(Detection("pattern", "neutral", i, int(s.t[i]), float(c[i]), "Doji", {"body_to_range": body / rng}))
    return out


def volume_and_volatility(s: Series, start: int = 0) -> list[Detection]:
    out = []
    if len(s) < 55:
        return out
    a5, a50 = atr(s, 5), atr(s, 50)
    rv = rel_volume(s.v)
    for i in range(max(50, start), len(s)):
        rng = s.h[i] - s.l[i]
        body = abs(s.c[i] - s.o[i])
        if not np.isnan(rv[i]) and rv[i] >= 2.5 and rng > 0 and body / rng >= 0.6:
            d = "bullish" if s.c[i] > s.o[i] else "bearish"
            out.append(Detection("volume_imbalance", d, i, int(s.t[i]), float(s.c[i]), "Volume imbalance candle",
                                 {"relative_volume": float(rv[i]), "body_to_range": float(body / rng)}))
        if not np.isnan(a5[i]) and not np.isnan(a50[i]) and a5[i] / a50[i] >= 1.6 and (np.isnan(a5[i - 1]) or a5[i - 1] / a50[i - 1] < 1.6):
            out.append(Detection("volatility_expansion", "neutral", i, int(s.t[i]), float(s.c[i]), "Volatility expansion",
                                 {"atr5_over_atr50": float(a5[i] / a50[i])}))
    return out


def divergences(s: Series, swings: list[Swing]) -> list[Detection]:
    r = rsi(s.c)
    out = []
    highs = [x for x in swings if x.kind == "high"]
    lows = [x for x in swings if x.kind == "low"]
    for a, b in zip(highs, highs[1:]):
        if b.price > a.price and not np.isnan(r[a.i]) and not np.isnan(r[b.i]) and r[b.i] < r[a.i] - 2:
            out.append(Detection("divergence", "bearish", b.confirmed_at, int(s.t[min(b.confirmed_at, len(s) - 1)]), b.price,
                                 "Bearish RSI divergence", {"price_1": a.price, "price_2": b.price,
                                                            "rsi_1": float(r[a.i]), "rsi_2": float(r[b.i])}))
    for a, b in zip(lows, lows[1:]):
        if b.price < a.price and not np.isnan(r[a.i]) and not np.isnan(r[b.i]) and r[b.i] > r[a.i] + 2:
            out.append(Detection("divergence", "bullish", b.confirmed_at, int(s.t[min(b.confirmed_at, len(s) - 1)]), b.price,
                                 "Bullish RSI divergence", {"price_1": a.price, "price_2": b.price,
                                                            "rsi_1": float(r[a.i]), "rsi_2": float(r[b.i])}))
    return out


def liquidity_pools(s: Series, swings: list[Swing]) -> dict:
    """Unswept confirmed swing levels = resting liquidity."""
    n = len(s)
    buy, sell = [], []
    for sw in swings:
        if sw.confirmed_at >= n:
            continue
        after = slice(sw.i + 1, n)
        if sw.kind == "high" and (n == sw.i + 1 or s.h[after].max() <= sw.price):
            buy.append({"price": sw.price, "time": sw.t, "label": sw.label})
        if sw.kind == "low" and (n == sw.i + 1 or s.l[after].min() >= sw.price):
            sell.append({"price": sw.price, "time": sw.t, "label": sw.label})
    last = float(s.c[-1]) if n else 0
    buy = sorted([b for b in buy if b["price"] > last], key=lambda x: x["price"])[:5]
    sell = sorted([b for b in sell if b["price"] < last], key=lambda x: -x["price"])[:5]
    return {"buy_side": buy, "sell_side": sell}


# ------------------------------------------------------------------ full analysis
@dataclass
class Analysis:
    swings: list[Swing]
    events: list[Detection]
    trend: str
    detections: list[Detection]
    pools: dict
    atr: np.ndarray
    rsi: np.ndarray
    rel_volume: np.ndarray
    # causal indicator arrays (value at i uses bars <= i only)
    ema20: np.ndarray = None
    ema50: np.ndarray = None
    sma20: np.ndarray = None
    std20: np.ndarray = None

    _by_i: dict | None = None

    def at(self, i: int) -> list[Detection]:
        """Detections created at bar i (index built once)."""
        if self._by_i is None:
            self._by_i = {}
            for d in self.detections:
                self._by_i.setdefault(d.i, []).append(d)
        return self._by_i.get(i, [])

    def recent(self, bars: int, n: int) -> list[Detection]:
        return [d for d in self.detections if d.i >= n - bars]


def analyse(s: Series, swing_k: int = 3) -> Analysis:
    swings = find_swings(s, swing_k)
    events, trend = structure_events(s, swings)
    det: list[Detection] = list(events)
    det += liquidity_sweeps(s, swings)
    det += failed_breakouts(s, swings)
    det += fair_value_gaps(s)
    det += order_blocks(s, events)
    det += sr_levels(s, swings)
    det += candle_patterns(s, start=max(1, len(s) - 200))
    det += volume_and_volatility(s, start=max(0, len(s) - 200))
    det += divergences(s, swings)
    det.sort(key=lambda d: d.i)
    return Analysis(swings, events, trend, det, liquidity_pools(s, swings), atr(s), rsi(s.c), rel_volume(s.v),
                    ema(s.c, 20), ema(s.c, 50), sma(s.c, 20), rolling_std(s.c, 20))


def market_structure_label(swings: list[Swing], last: int = 4) -> str:
    labels = [x.label for x in swings if x.label][-last:]
    if not labels:
        return "insufficient swings"
    if all(lbl in ("HH", "HL") for lbl in labels):
        return "higher highs & higher lows"
    if all(lbl in ("LH", "LL") for lbl in labels):
        return "lower highs & lower lows"
    return "mixed / ranging"
