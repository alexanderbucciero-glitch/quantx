"""Crypto market data from public, keyless exchange/aggregator APIs.

Coinbase Exchange: https://docs.cdp.coinbase.com/exchange/reference/exchangerestapi_getproductcandles
Kraken:            https://docs.kraken.com/api/docs/rest-api/get-ticker-information
CoinGecko:         https://docs.coingecko.com/reference/coins-markets
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..core.http import UpstreamError, hub

GRANULARITY = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "6h": 21600, "1d": 86400}


def parse_coinbase_candles(raw: list) -> list[dict]:
    """Coinbase returns [time, low, high, open, close, volume], newest first."""
    out = []
    for row in raw:
        t, low, high, open_, close, vol = row[:6]
        out.append({"time": int(t), "open": float(open_), "high": float(high),
                    "low": float(low), "close": float(close), "volume": float(vol)})
    out.sort(key=lambda b: b["time"])
    return out


async def coinbase_candles(product: str, interval: str = "1d") -> dict:
    if interval not in GRANULARITY:
        raise ValueError(f"interval must be one of {list(GRANULARITY)}")
    url = f"https://api.exchange.coinbase.com/products/{product}/candles"
    raw, fetched = await hub.get("coinbase", url, params={"granularity": GRANULARITY[interval]},
                                 ttl=30 if interval != "1d" else 300)
    if not isinstance(raw, list):
        raise UpstreamError("coinbase", "unexpected payload")
    return {"product": product, "interval": interval, "source": "Coinbase Exchange public API",
            "fetched_at": fetched, "bars": parse_coinbase_candles(raw)}


def parse_coingecko_markets(raw: list) -> list[dict]:
    keep = ["id", "symbol", "name", "current_price", "market_cap", "market_cap_rank",
            "total_volume", "price_change_percentage_24h", "high_24h", "low_24h",
            "circulating_supply", "ath", "ath_change_percentage", "last_updated"]
    return [{k: c.get(k) for k in keep} for c in raw]


async def coingecko_markets(per_page: int = 50, category: str | None = None) -> dict:
    # Always request the same 250-row page so every caller shares one cached response
    # (the keyless CoinGecko tier allows only a few calls per minute).
    params = {"vs_currency": "usd", "order": "market_cap_desc", "per_page": 250, "page": 1,
              "price_change_percentage": "1h,24h,7d"}
    if category:
        params["category"] = category
    raw, fetched = await hub.get("coingecko", "https://api.coingecko.com/api/v3/coins/markets",
                                 params=params, ttl=180)
    if not isinstance(raw, list):
        raise UpstreamError("coingecko", "unexpected payload")
    raw = raw[:per_page]
    rows = parse_coingecko_markets(raw)
    for r, c in zip(rows, raw):
        r["change_1h"] = c.get("price_change_percentage_1h_in_currency")
        r["change_7d"] = c.get("price_change_percentage_7d_in_currency")
    return {"source": "CoinGecko public API", "fetched_at": fetched, "coins": rows}


async def kraken_ticker(pair: str = "XBTUSD") -> dict:
    raw, fetched = await hub.get("kraken", "https://api.kraken.com/0/public/Ticker",
                                 params={"pair": pair}, ttl=15)
    if raw.get("error"):
        raise UpstreamError("kraken", "; ".join(raw["error"]))
    (key, t), = raw["result"].items()
    return {"pair": key, "last": float(t["c"][0]), "bid": float(t["b"][0]), "ask": float(t["a"][0]),
            "volume_24h": float(t["v"][1]), "vwap_24h": float(t["p"][1]),
            "source": "Kraken public API", "fetched_at": fetched}


def utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
