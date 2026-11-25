"""Time source for the trading core.

Live trading uses wall-clock time. Historical replay runs the SAME decision and execution code
with a simulated clock, set per asyncio task via a ContextVar, so a replay never disturbs the
live agent running in the same process.
"""
from __future__ import annotations

import time
from contextvars import ContextVar

_sim_now: ContextVar[float | None] = ContextVar("quantx_sim_now", default=None)


def now() -> float:
    v = _sim_now.get()
    return time.time() if v is None else v


def set_sim_time(t: float | None) -> None:
    _sim_now.set(t)


def simulated() -> bool:
    return _sim_now.get() is not None
