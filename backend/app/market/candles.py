"""Standardised candle schema shared by every feed, detector, strategy and backtest.

A `Series` holds CLOSED candles only, as numpy arrays (fast vectorised maths).
A still-forming candle is kept separately in `provisional` and is never used to
generate signals; the UI shows it marked as provisional.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

TF_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "6h": 21600, "1d": 86400}


@dataclass
class Candle:
    t: int          # open time, epoch seconds UTC
    o: float
    h: float
    l: float
    c: float
    v: float        # volume in base units (crypto) or USD (DEX pools; see Series.volume_unit)


@dataclass
class Series:
    symbol: str
    asset_class: str            # 'crypto' | 'memecoin' | 'equity'
    tf: str
    source: str
    t: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int64))
    o: np.ndarray = field(default_factory=lambda: np.zeros(0))
    h: np.ndarray = field(default_factory=lambda: np.zeros(0))
    l: np.ndarray = field(default_factory=lambda: np.zeros(0))
    c: np.ndarray = field(default_factory=lambda: np.zeros(0))
    v: np.ndarray = field(default_factory=lambda: np.zeros(0))
    provisional: Candle | None = None
    volume_unit: str = "base"
    gaps: int = 0               # missing bars detected (never filled)

    def __len__(self) -> int:
        return int(self.t.size)

    @property
    def step(self) -> int:
        return TF_SECONDS[self.tf]

    @classmethod
    def from_candles(cls, symbol: str, asset_class: str, tf: str, source: str, candles: list[Candle],
                     now: float | None = None, volume_unit: str = "base") -> "Series":
        """Split closed vs still-forming candles, de-duplicate, sort, and count gaps."""
        now = time.time() if now is None else now
        step = TF_SECONDS[tf]
        by_t = {c.t: c for c in candles}
        ordered = [by_t[k] for k in sorted(by_t)]
        closed = [c for c in ordered if c.t + step <= now]
        forming = [c for c in ordered if c.t + step > now]
        s = cls(symbol, asset_class, tf, source, volume_unit=volume_unit)
        if closed:
            s.t = np.array([c.t for c in closed], dtype=np.int64)
            s.o = np.array([c.o for c in closed], dtype=float)
            s.h = np.array([c.h for c in closed], dtype=float)
            s.l = np.array([c.l for c in closed], dtype=float)
            s.c = np.array([c.c for c in closed], dtype=float)
            s.v = np.array([c.v for c in closed], dtype=float)
            s.gaps = int(np.sum(np.diff(s.t) // step - 1)) if len(closed) > 1 else 0
        s.provisional = forming[-1] if forming else None
        return s

    def merge(self, new: list[Candle], now: float | None = None) -> int:
        """Incrementally add candles. Returns number of newly CLOSED candles (0 = nothing to recompute)."""
        before = len(self)
        last_t = int(self.t[-1]) if before else -1
        existing = [Candle(int(t), o, h, l, c, v) for t, o, h, l, c, v in
                    zip(self.t[-5:], self.o[-5:], self.h[-5:], self.l[-5:], self.c[-5:], self.v[-5:])] if before else []
        merged = Series.from_candles(self.symbol, self.asset_class, self.tf, self.source,
                                     existing + new, now, self.volume_unit)
        add = merged.t > last_t
        if np.any(add):
            for name in ("t", "o", "h", "l", "c", "v"):
                setattr(self, name, np.concatenate([getattr(self, name), getattr(merged, name)[add]]))
            if before:
                self.gaps += int(max(0, (merged.t[add][0] - last_t) // self.step - 1))
        self.provisional = merged.provisional
        return len(self) - before

    def tail(self, n: int) -> "Series":
        s = Series(self.symbol, self.asset_class, self.tf, self.source, volume_unit=self.volume_unit)
        for name in ("t", "o", "h", "l", "c", "v"):
            setattr(s, name, getattr(self, name)[-n:])
        return s

    def upto(self, i: int) -> "Series":
        """Closed candles 0..i inclusive — used by backtests to avoid look-ahead."""
        s = Series(self.symbol, self.asset_class, self.tf, self.source, volume_unit=self.volume_unit)
        for name in ("t", "o", "h", "l", "c", "v"):
            setattr(s, name, getattr(self, name)[: i + 1])
        return s

    def to_json(self, limit: int = 500) -> list[dict]:
        n = len(self)
        out = [{"time": int(self.t[i]), "open": float(self.o[i]), "high": float(self.h[i]),
                "low": float(self.l[i]), "close": float(self.c[i]), "volume": float(self.v[i])}
               for i in range(max(0, n - limit), n)]
        return out
