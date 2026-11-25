"""Integration health checks and build-stage status. Nothing is reported as working
unless a real request just succeeded."""
from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter

from ..core.config import settings
from ..core.http import UpstreamError
from ..providers import crypto, dex, fred, news, sec, tiingo

router = APIRouter(prefix="/api")

INTEGRATIONS = [
    # key, name, category, needs, check coroutine factory
    ("fred", "FRED (St. Louis Fed)", "Macro, rates, indices", "No key", lambda: fred.series("DFF", "2026-01-01")),
    ("sec", "SEC EDGAR", "Filings, fundamentals, events", "No key (User-Agent)", lambda: sec.current_filings("8-K", 10)),
    ("coinbase", "Coinbase Exchange", "Crypto OHLCV", "No key", lambda: crypto.coinbase_candles("BTC-USD", "1h")),
    ("kraken", "Kraken", "Crypto ticker", "No key", lambda: crypto.kraken_ticker("XBTUSD")),
    ("coingecko", "CoinGecko", "Crypto market caps", "No key (public tier)", lambda: crypto.coingecko_markets(5)),
    ("dexscreener", "DexScreener", "Solana / Pump.fun tokens", "No key", lambda: dex.search_pairs("SOL")),
    ("fed", "Federal Reserve RSS", "Central bank news", "No key", lambda: news.rss("fed")),
    ("ecb", "ECB RSS", "Central bank news", "No key", lambda: news.rss("ecb")),
    ("gdelt", "GDELT DOC 2.0", "Global news", "No key (1 req / 5 s)", lambda: news.gdelt("markets", 5)),
]

NOT_AVAILABLE = [
    {"key": "tiingo", "name": "Tiingo", "category": "Stock & ETF daily prices (decades)",
     "status": "configured" if tiingo.configured() else "not_configured",
     "needs": "Free account + API key", "how": "tiingo.com → Account → API → set QUANTX_TIINGO_API_KEY"},
    {"key": "reddit", "name": "Reddit", "category": "Social sentiment", "status": "not_configured",
     "needs": "Free app approval (non-commercial)", "how": "Register an app at reddit.com/prefs/apps and request Data API access"},
    {"key": "youtube", "name": "YouTube Data API", "category": "Social / video", "status": "not_configured",
     "needs": "Free Google Cloud API key", "how": "console.cloud.google.com → enable YouTube Data API v3"},
    {"key": "x", "name": "X (Twitter)", "category": "Social sentiment", "status": "unavailable_free",
     "needs": "Paid (pay-per-use credits)", "how": "No free read access. Skipped to keep the platform free."},
    {"key": "stocktwits", "name": "StockTwits", "category": "Social sentiment", "status": "unavailable_free",
     "needs": "API closed to new developers", "how": "Not available."},
    {"key": "bloomberg", "name": "Bloomberg", "category": "News / data", "status": "unavailable_free",
     "needs": "Terminal / B-PIPE licence", "how": "A consumer bloomberg.com subscription does not include data API access. Articles are linked, not ingested."},
    {"key": "economist", "name": "The Economist", "category": "News", "status": "unavailable_free",
     "needs": "No public API", "how": "Subscription content cannot be ingested automatically. Read it via links."},
    {"key": "helius", "name": "Helius (Solana RPC)", "category": "On-chain holders, launches", "status": "not_configured",
     "needs": "Free developer key", "how": "helius.dev → create key (Stage 8: holder concentration)"},
]


async def _check(key, name, category, needs, factory):
    t0, started = time.perf_counter(), time.time()
    try:
        res = await asyncio.wait_for(factory(), timeout=25)
        fetched = res.get("fetched_at", started)
        if fetched < started:  # answered from cache: report when the source last really answered
            return {"key": key, "name": name, "category": category, "needs": needs, "status": "live",
                    "latency_ms": None, "checked_at": fetched, "cached": True}
        return {"key": key, "name": name, "category": category, "needs": needs, "status": "live",
                "latency_ms": round((time.perf_counter() - t0) * 1000), "checked_at": fetched}
    except (UpstreamError, asyncio.TimeoutError) as e:
        msg = e.message if isinstance(e, UpstreamError) else "timed out"
        return {"key": key, "name": name, "category": category, "needs": needs, "status": "error",
                "error": msg, "checked_at": time.time()}


@router.get("/integrations")
async def integrations():
    checks = await asyncio.gather(*[_check(*i) for i in INTEGRATIONS])
    return {"live_checks": checks, "other": NOT_AVAILABLE,
            "user_agent_set": "set QUANTX_USER_AGENT" not in settings.user_agent}


STAGES = [
    (1, "Architecture, database, frontend", "in_progress"),
    (2, "Historical market data collection", "partial"),
    (3, "Quantitative analysis engine", "not_started"),
    (4, "Historical backtesting", "not_started"),
    (5, "Social media intelligence", "not_started"),
    (6, "Breaking news & event detection", "partial"),
    (7, "Event-driven quantitative models", "not_started"),
    (8, "Memecoin intelligence", "partial"),
    (9, "Automated paper trading", "not_started"),
    (10, "Performance monitoring", "not_started"),
]


@router.get("/stages")
async def stages():
    return {"stages": [{"stage": s, "name": n, "status": st} for s, n, st in STAGES]}
