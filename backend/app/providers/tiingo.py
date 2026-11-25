"""Tiingo end-of-day stock prices (free account, personal use).

Docs: https://www.tiingo.com/documentation/end-of-day
Requires QUANTX_TIINGO_API_KEY. This adapter has NOT been exercised against the
live API from the development environment (no key available there); the
Integrations page reports it as unverified until a real request succeeds.
"""
from __future__ import annotations

from ..core.config import settings
from ..core.http import UpstreamError, hub


def configured() -> bool:
    return bool(settings.tiingo_api_key)


def parse_prices(raw: list) -> list[dict]:
    out = []
    for r in raw:
        out.append({
            "date": r["date"][:10],
            "open": r.get("open"), "high": r.get("high"), "low": r.get("low"), "close": r.get("close"),
            "volume": r.get("volume"),
            "adj_close": r.get("adjClose"), "div_cash": r.get("divCash"), "split_factor": r.get("splitFactor"),
        })
    return out


async def daily(ticker: str, start: str = "1970-01-01") -> dict:
    if not configured():
        raise UpstreamError("tiingo", "not configured: add a free Tiingo API key in backend/.env")
    raw, fetched = await hub.get(
        "tiingo", f"https://api.tiingo.com/tiingo/daily/{ticker.lower()}/prices",
        params={"startDate": start, "token": settings.tiingo_api_key, "format": "json"}, ttl=3600)
    if not isinstance(raw, list):
        raise UpstreamError("tiingo", "unexpected payload")
    return {"ticker": ticker.upper(), "source": "Tiingo EOD", "fetched_at": fetched, "bars": parse_prices(raw)}
