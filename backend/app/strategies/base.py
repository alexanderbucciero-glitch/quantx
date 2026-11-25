"""Strategy interface. Every strategy is causal: `evaluate(series, analysis, t)` may only use
candle data and detections known at bar `t` (closed). This is enforced by `known(...)` and by
tests that compare results on truncated vs full series."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np

from ..analysis.structure import Analysis, Detection, ema, plain
from ..market.candles import Series


@dataclass
class Signal:
    strategy: str
    direction: str                 # long | short
    t: int                         # bar index of the closed candle that triggered it
    score: float                   # 0..1 heuristic evidence strength (NOT a probability)
    invalidation: float | None     # structural level that would prove the idea wrong
    bullish: list[str] = field(default_factory=list)
    bearish: list[str] = field(default_factory=list)
    features: dict = field(default_factory=dict)
    trigger: dict | None = None    # detection that triggered it

    def to_json(self) -> dict:
        return plain(asdict(self))


def known(a: Analysis, t: int, kinds: tuple[str, ...], within: int) -> list[Detection]:
    """Detections of the given kinds created at bars (t-within, t] — never later than t."""
    out: list[Detection] = []
    for i in range(max(0, t - within + 1), t + 1):
        out.extend(d for d in a.at(i) if d.kind in kinds)
    return out


class Strategy:
    name = "base"
    label = "Base"
    description = ""
    asset_classes = ("crypto", "memecoin", "equity")
    shortable = ("crypto", "equity")   # memecoins: spot DEX, long only

    def evaluate(self, s: Series, a: Analysis, t: int) -> Signal | None:  # pragma: no cover
        raise NotImplementedError

    # ----- shared helpers (all causal)
    @staticmethod
    def trend_context(s: Series, t: int) -> dict:
        c = s.c[: t + 1]
        e20, e50 = ema(c, 20), ema(c, 50)
        return {"ema20": float(e20[-1]) if c.size >= 20 else None, "ema50": float(e50[-1]) if c.size >= 50 else None}

    def allowed(self, s: Series, direction: str) -> bool:
        return s.asset_class in self.asset_classes and (direction == "long" or s.asset_class in self.shortable)


def nz(x) -> float | None:
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else float(x)
