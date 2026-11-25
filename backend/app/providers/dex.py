"""DexScreener public API — Solana / Pump.fun token market data.

Docs: https://docs.dexscreener.com/api/reference
Pump.fun tokens are read from public market data (their mint addresses end in 'pump');
QUANTX does not log in to or scrape pump.fun.
"""
from __future__ import annotations

import time

from ..core.http import UpstreamError, hub

MIN_LIQUIDITY_USD = 50_000


def risk_flags(pair: dict, now_ms: float | None = None) -> list[dict]:
    """Rule-based flags from observable market data. Not a prediction."""
    now_ms = now_ms or time.time() * 1000
    flags = []
    liq = (pair.get("liquidity") or {}).get("usd")
    if liq is None:
        flags.append({"flag": "unreliable_data", "severity": 2, "detail": "no liquidity figure reported"})
    elif liq < MIN_LIQUIDITY_USD:
        flags.append({"flag": "low_liquidity", "severity": 3 if liq < 10_000 else 2,
                      "detail": f"liquidity ${liq:,.0f} < ${MIN_LIQUIDITY_USD:,}"})
    created = pair.get("pairCreatedAt")
    if created:
        age_h = (now_ms - created) / 3_600_000
        if age_h < 24:
            flags.append({"flag": "very_new", "severity": 2, "detail": f"pair is {age_h:.1f} h old"})
    else:
        flags.append({"flag": "unknown_age", "severity": 1, "detail": "creation time not reported"})
    fdv, mcap = pair.get("fdv"), pair.get("marketCap")
    if liq and (mcap or fdv) and (mcap or fdv) / liq > 50:
        flags.append({"flag": "thin_liquidity_vs_valuation", "severity": 2,
                      "detail": f"valuation is {(mcap or fdv) / liq:,.0f}x liquidity"})
    tx = (pair.get("txns") or {}).get("h1") or {}
    b, s = tx.get("buys", 0), tx.get("sells", 0)
    vol_h1 = (pair.get("volume") or {}).get("h1") or 0
    if liq and vol_h1 > 5 * liq:
        flags.append({"flag": "possible_wash_trading", "severity": 2,
                      "detail": f"1h volume is {vol_h1 / liq:.1f}x liquidity"})
    if b + s >= 20 and min(b, s) / max(b, s) < 0.15:
        flags.append({"flag": "one_sided_flow", "severity": 1, "detail": f"1h buys {b} vs sells {s}"})
    # Holder concentration requires on-chain holder data (Helius/RPC) — not available here.
    flags.append({"flag": "holder_data_unavailable", "severity": 1,
                  "detail": "holder concentration needs an on-chain data source (Stage 8)"})
    return flags


def summarise_pair(p: dict, now_ms: float | None = None) -> dict:
    now_ms = now_ms or time.time() * 1000
    created = p.get("pairCreatedAt")
    base = p.get("baseToken") or {}
    return {
        "chain": p.get("chainId"), "dex": p.get("dexId"), "pair_address": p.get("pairAddress"),
        "url": p.get("url"), "token_address": base.get("address"), "symbol": base.get("symbol"),
        "name": base.get("name"), "quote": (p.get("quoteToken") or {}).get("symbol"),
        "price_usd": float(p["priceUsd"]) if p.get("priceUsd") else None,
        "liquidity_usd": (p.get("liquidity") or {}).get("usd"),
        "fdv": p.get("fdv"), "market_cap": p.get("marketCap"),
        "volume": p.get("volume") or {}, "txns": p.get("txns") or {},
        "price_change": p.get("priceChange") or {},
        "age_hours": round((now_ms - created) / 3_600_000, 2) if created else None,
        "is_pump_fun": bool(base.get("address", "").endswith("pump")),
        "flags": risk_flags(p, now_ms),
        # Explicitly no probability: QUANTX has not yet built the historical
        # comparable-launch database needed to estimate one (Stage 8).
        "probability": None,
        "probability_reason": "insufficient historical samples — comparable-launch database not built yet",
    }


async def latest_solana_tokens(limit: int = 30) -> dict:
    """Newest token profiles on DexScreener, filtered to Solana, enriched with pair data."""
    profiles, fetched = await hub.get("dexscreener", "https://api.dexscreener.com/token-profiles/latest/v1", ttl=60)
    if not isinstance(profiles, list):
        raise UpstreamError("dexscreener", "unexpected payload")
    addrs = []
    for p in profiles:
        if p.get("chainId") == "solana" and p.get("tokenAddress") not in addrs:
            addrs.append(p["tokenAddress"])
    addrs = addrs[:limit]
    pairs: list[dict] = []
    for i in range(0, len(addrs), 30):  # endpoint accepts up to 30 addresses
        chunk = ",".join(addrs[i:i + 30])
        raw, _ = await hub.get("dexscreener", f"https://api.dexscreener.com/tokens/v1/solana/{chunk}", ttl=60)
        if isinstance(raw, list):
            pairs.extend(raw)
    # keep the most liquid pair per token
    best: dict[str, dict] = {}
    for p in pairs:
        a = (p.get("baseToken") or {}).get("address")
        if a and ((p.get("liquidity") or {}).get("usd") or 0) >= ((best.get(a, {}).get("liquidity") or {}).get("usd") or -1):
            best[a] = p
    now_ms = time.time() * 1000
    rows = [summarise_pair(best[a], now_ms) for a in addrs if a in best]
    missing = [a for a in addrs if a not in best]
    return {"source": "DexScreener public API", "fetched_at": fetched, "tokens": rows,
            "without_pair_data": missing}


async def search_pairs(q: str) -> dict:
    raw, fetched = await hub.get("dexscreener", "https://api.dexscreener.com/latest/dex/search",
                                 params={"q": q}, ttl=60)
    now_ms = time.time() * 1000
    rows = [summarise_pair(p, now_ms) for p in (raw.get("pairs") or [])[:40]]
    return {"source": "DexScreener public API", "fetched_at": fetched, "pairs": rows}
