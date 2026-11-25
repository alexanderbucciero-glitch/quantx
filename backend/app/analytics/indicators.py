"""Technical indicators and descriptive statistics computed from real bars.

These are DESCRIPTIVE measurements of history, not forecasts. Forecast models
(with calibration and out-of-sample validation) arrive in Stage 3.
"""
from __future__ import annotations

import math
from statistics import mean, pstdev


def sma(values: list[float], n: int) -> list[float | None]:
    out: list[float | None] = []
    s = 0.0
    for i, v in enumerate(values):
        s += v
        if i >= n:
            s -= values[i - n]
        out.append(s / n if i >= n - 1 else None)
    return out


def ema(values: list[float], n: int) -> list[float | None]:
    out: list[float | None] = []
    k = 2 / (n + 1)
    prev = None
    for i, v in enumerate(values):
        if i < n - 1:
            out.append(None)
            continue
        prev = mean(values[i - n + 1:i + 1]) if prev is None else v * k + prev * (1 - k)
        out.append(prev)
    return out


def rsi(values: list[float], n: int = 14) -> list[float | None]:
    """Wilder's RSI."""
    out: list[float | None] = [None] * len(values)
    if len(values) <= n:
        return out
    gains = [max(values[i] - values[i - 1], 0) for i in range(1, len(values))]
    losses = [max(values[i - 1] - values[i], 0) for i in range(1, len(values))]
    ag, al = mean(gains[:n]), mean(losses[:n])
    out[n] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(n + 1, len(values)):
        ag = (ag * (n - 1) + gains[i - 1]) / n
        al = (al * (n - 1) + losses[i - 1]) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def log_returns(values: list[float]) -> list[float]:
    return [math.log(values[i] / values[i - 1]) for i in range(1, len(values)) if values[i - 1] > 0 and values[i] > 0]


def realized_vol(values: list[float], n: int, periods_per_year: float) -> float | None:
    r = log_returns(values[-(n + 1):])
    if len(r) < max(5, n // 2):
        return None
    return pstdev(r) * math.sqrt(periods_per_year)


def max_drawdown(values: list[float]) -> float | None:
    if not values:
        return None
    peak, mdd = values[0], 0.0
    for v in values:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    return mdd


def summary(closes: list[float], volumes: list[float] | None, periods_per_year: float) -> dict:
    """Snapshot of descriptive measures at the last bar."""
    if len(closes) < 2:
        return {"bars": len(closes), "insufficient_data": True}
    last = closes[-1]

    def ret(k: int) -> float | None:
        return last / closes[-1 - k] - 1 if len(closes) > k and closes[-1 - k] > 0 else None

    s20, s50, s200 = (sma(closes, n)[-1] for n in (20, 50, 200))
    r14 = rsi(closes, 14)[-1]
    vol20 = realized_vol(closes, 20, periods_per_year)
    vol_long = realized_vol(closes, min(250, len(closes) - 1), periods_per_year)
    high = max(closes)
    vol_z = None
    if volumes and len(volumes) >= 30:
        base = volumes[-31:-1]
        sd = pstdev(base)
        vol_z = (volumes[-1] - mean(base)) / sd if sd > 0 else None
    # descriptive regime label from trend + volatility (not a model output)
    if s200 is None or vol20 is None or vol_long is None:
        regime = "unclassified (insufficient history)"
    else:
        trend = "uptrend" if last > s200 else "downtrend"
        vol_state = "high-vol" if vol20 > 1.25 * vol_long else "low-vol" if vol20 < 0.75 * vol_long else "normal-vol"
        regime = f"{trend} / {vol_state}"
    return {
        "bars": len(closes), "last": last,
        "return_1": ret(1), "return_5": ret(5), "return_20": ret(20),
        "sma20": s20, "sma50": s50, "sma200": s200, "rsi14": r14,
        "realized_vol_20": vol20, "realized_vol_long": vol_long,
        "drawdown_from_high": last / high - 1, "max_drawdown": max_drawdown(closes),
        "volume_zscore": vol_z, "regime": regime,
        "note": "Descriptive statistics of past prices. Not a forecast.",
    }
