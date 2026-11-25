"""Market-data feeds producing standardised Candles.

Coinbase Exchange REST  — crypto candles (max 300 per request, paginated for history)
Coinbase Exchange WS    — real-time ticker (price, best bid/ask) for scanning and position monitoring
GeckoTerminal           — Solana DEX pool candles (Pump.fun / memecoins). Strict free rate limit.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable

from ..core.http import UpstreamError, hub
from .candles import TF_SECONDS, Candle

log = logging.getLogger("quantx.feeds")

CB = "https://api.exchange.coinbase.com"
GT = "https://api.geckoterminal.com/api/v2"


# ------------------------------------------------------------------ Coinbase REST
async def coinbase_candles(product: str, tf: str, start: int | None = None, end: int | None = None,
                           ttl: float = 5) -> list[Candle]:
    params: dict = {"granularity": TF_SECONDS[tf]}
    if start is not None and end is not None:
        params["start"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(start))
        params["end"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(end))
    raw, _ = await hub.get("coinbase", f"{CB}/products/{product}/candles", params=params, ttl=ttl)
    if not isinstance(raw, list):
        raise UpstreamError("coinbase", "unexpected candle payload")
    # [time, low, high, open, close, volume]
    return [Candle(int(r[0]), float(r[3]), float(r[2]), float(r[1]), float(r[4]), float(r[5])) for r in raw]


async def coinbase_history(product: str, tf: str, bars: int) -> list[Candle]:
    """Page backwards 300 bars at a time. Missing bars stay missing."""
    step = TF_SECONDS[tf]
    end = (int(time.time()) // step) * step + step
    out: dict[int, Candle] = {}
    while len(out) < bars:
        start = end - 300 * step
        chunk = await coinbase_candles(product, tf, start, end, ttl=3600)
        if not chunk:
            break
        for c in chunk:
            out[c.t] = c
        end = start
    return sorted(out.values(), key=lambda c: c.t)[-bars:]


async def coinbase_products() -> list[dict]:
    raw, _ = await hub.get("coinbase", f"{CB}/products", ttl=3600)
    return [p for p in raw if p.get("quote_currency") == "USD" and p.get("status") == "online"
            and not p.get("trading_disabled") and not p.get("cancel_only") and not p.get("auction_mode")]


async def coinbase_ticker(product: str) -> dict:
    raw, fetched = await hub.get("coinbase", f"{CB}/products/{product}/ticker", ttl=2)
    return {"price": float(raw["price"]), "bid": float(raw["bid"]), "ask": float(raw["ask"]),
            "volume": float(raw["volume"]), "time": raw.get("time"), "fetched_at": fetched}


# ------------------------------------------------------------------ Coinbase WebSocket
class CoinbaseTickerStream:
    """Maintains a live ticker subscription. Calls `on_tick(product, tick)` for every update."""

    URL = "wss://ws-feed.exchange.coinbase.com"

    def __init__(self, on_tick: Callable[[str, dict], Awaitable[None]], on_status: Callable[[str, str], Awaitable[None]]):
        self.on_tick = on_tick
        self.on_status = on_status
        self.products: set[str] = set()
        self._task: asyncio.Task | None = None
        self._ws = None
        self.connected = False
        self.last_msg_at: float | None = None
        self.messages = 0

    async def set_products(self, products: set[str]) -> None:
        added, removed = products - self.products, self.products - products
        self.products = set(products)
        if self._ws is not None and self.connected:
            try:
                if added:
                    await self._ws.send(json.dumps({"type": "subscribe", "product_ids": sorted(added), "channels": ["ticker"]}))
                if removed:
                    await self._ws.send(json.dumps({"type": "unsubscribe", "product_ids": sorted(removed), "channels": ["ticker"]}))
            except Exception:  # reconnect loop will resubscribe
                pass

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="coinbase-ws")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
        self.connected = False

    async def _run(self) -> None:
        import websockets
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(self.URL, open_timeout=15, ping_interval=20, max_size=2**22) as ws:
                    self._ws = ws
                    if self.products:
                        await ws.send(json.dumps({"type": "subscribe", "product_ids": sorted(self.products), "channels": ["ticker"]}))
                    self.connected = True
                    backoff = 1.0
                    await self.on_status("connected", f"Coinbase live feed connected ({len(self.products)} products)")
                    async for raw in ws:
                        m = json.loads(raw)
                        if m.get("type") != "ticker":
                            if m.get("type") == "error":
                                await self.on_status("error", f"Coinbase feed: {m.get('message')} {m.get('reason', '')}")
                            continue
                        self.messages += 1
                        self.last_msg_at = time.time()
                        tick = {"price": float(m["price"]), "bid": float(m.get("best_bid") or m["price"]),
                                "ask": float(m.get("best_ask") or m["price"]), "time": m.get("time"),
                                "volume_24h": float(m.get("volume_24h") or 0), "recv": self.last_msg_at}
                        await self.on_tick(m["product_id"], tick)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # network drop: report and reconnect with backoff
                self.connected = False
                await self.on_status("error", f"Coinbase feed disconnected: {type(e).__name__}. Reconnecting in {backoff:.0f}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
            finally:
                self._ws = None
                self.connected = False


# ------------------------------------------------------------------ GeckoTerminal (Solana pools)
GT_TF = {"1m": ("minute", 1), "5m": ("minute", 5), "15m": ("minute", 15), "1h": ("hour", 1), "1d": ("day", 1)}


async def gecko_pool_candles(pool: str, tf: str, limit: int = 500) -> list[Candle]:
    unit, agg = GT_TF[tf]
    raw, _ = await hub.get("geckoterminal", f"{GT}/networks/solana/pools/{pool}/ohlcv/{unit}",
                           params={"aggregate": agg, "limit": min(limit, 1000), "currency": "usd"}, ttl=30)
    rows = (raw.get("data") or {}).get("attributes", {}).get("ohlcv_list") or []
    # [ts, o, h, l, c, volume_usd]
    return [Candle(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in rows]


async def gecko_pools(kind: str = "trending_pools") -> list[dict]:
    raw, _ = await hub.get("geckoterminal", f"{GT}/networks/solana/{kind}",
                           params={"page": 1, "include": "base_token,dex"}, ttl=120)
    out = []
    for p in raw.get("data") or []:
        a = p["attributes"]
        rel = p.get("relationships", {})
        base_id = (rel.get("base_token", {}).get("data") or {}).get("id", "")
        dex = (rel.get("dex", {}).get("data") or {}).get("id", "")
        out.append({
            "pool": a["address"], "name": a.get("name"), "dex": dex,
            "token_address": base_id.split("_", 1)[-1] if base_id else None,
            "price_usd": float(a["base_token_price_usd"]) if a.get("base_token_price_usd") else None,
            "liquidity_usd": float(a["reserve_in_usd"]) if a.get("reserve_in_usd") else None,
            "market_cap_usd": float(a["market_cap_usd"]) if a.get("market_cap_usd") else None,
            "fdv_usd": float(a["fdv_usd"]) if a.get("fdv_usd") else None,
            "volume_usd": {k: float(v) for k, v in (a.get("volume_usd") or {}).items() if v is not None},
            "price_change": {k: float(v) for k, v in (a.get("price_change_percentage") or {}).items() if v is not None},
            "transactions": a.get("transactions") or {},
            "created_at": a.get("pool_created_at"),
        })
    return out
