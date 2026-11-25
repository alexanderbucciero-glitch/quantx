"""Execution-cost model per asset class (all simulated). Conservative defaults, configurable.

crypto   : centralised-exchange taker fee per side + half the observed bid/ask spread + size impact.
memecoin : DEX swap fee + network/priority cost + constant-product price impact from pool liquidity.
equity   : commission-free broker assumed, but SEC/FINRA fees + half spread + impact.
"""
from __future__ import annotations

from dataclasses import dataclass

DEFAULTS = {
    "crypto":   {"fee_pct": 0.40, "base_slippage_pct": 0.02, "min_volume_24h_usd": 5_000_000, "max_spread_pct": 0.25},
    "memecoin": {"fee_pct": 0.30, "base_slippage_pct": 0.10, "min_liquidity_usd": 100_000, "max_spread_pct": 1.0},
    "equity":   {"fee_pct": 0.01, "base_slippage_pct": 0.02, "min_volume_24h_usd": 20_000_000, "max_spread_pct": 0.10},
    "commodity": {"fee_pct": 0.40, "base_slippage_pct": 0.03, "min_volume_24h_usd": 2_000_000, "max_spread_pct": 0.25},
    "fx":       {"fee_pct": 0.40, "base_slippage_pct": 0.005, "min_volume_24h_usd": 0, "max_spread_pct": 0.05},
}


@dataclass
class CostEstimate:
    fee_pct_round_trip: float
    slippage_pct_round_trip: float

    @property
    def total_pct(self) -> float:
        return self.fee_pct_round_trip + self.slippage_pct_round_trip


def estimate(asset_class: str, notional_usd: float, spread_pct: float | None = None,
             volume_24h_usd: float | None = None, liquidity_usd: float | None = None,
             overrides: dict | None = None) -> CostEstimate:
    p = {**DEFAULTS[asset_class], **(overrides or {})}
    fee_rt = 2 * p["fee_pct"]
    half_spread = (spread_pct or 0) / 2
    if asset_class == "memecoin":
        if not liquidity_usd or liquidity_usd <= 0:
            impact = 2.0                       # unknown pool depth: assume a conservative 2% per side
        else:
            # constant-product AMM: impact ≈ trade / (pool side); pool side ≈ liquidity / 2
            impact = 100 * notional_usd / (liquidity_usd / 2)
    else:
        if not volume_24h_usd or volume_24h_usd <= 0:
            impact = 0.5                       # unknown volume: conservative fixed impact
        else:
            # square-root impact model with a small coefficient; tiny for liquid majors
            impact = 10 * (notional_usd / volume_24h_usd) ** 0.5
    impact = min(impact, 10.0)                 # beyond this the market is untradeable anyway
    slip_rt = 2 * (p["base_slippage_pct"] + half_spread + impact)
    return CostEstimate(fee_rt, slip_rt)
