"""Shared HTTP client: polite rate limiting per host, TTL caching, honest errors.

Nothing here ever invents a response. If an upstream call fails, the caller gets
an UpstreamError and the API reports the source as unavailable.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from .config import settings


class UpstreamError(Exception):
    def __init__(self, source: str, message: str, status: int | None = None):
        super().__init__(f"{source}: {message}")
        self.source = source
        self.status = status
        self.message = message


# Minimum seconds between requests to the same host (provider etiquette / ToS limits)
HOST_MIN_INTERVAL: dict[str, float] = {
    "api.gdeltproject.org": 5.5,      # GDELT asks for max 1 request per 5 s
    "data.sec.gov": 0.15,             # SEC fair-access: <= 10 req/s
    "www.sec.gov": 0.15,
    "api.coingecko.com": 2.5,         # free/public tier is ~5-30 calls/min
    "api.dexscreener.com": 0.25,      # documented 60-300 req/min depending on endpoint
    "api.exchange.coinbase.com": 0.2,
    "api.kraken.com": 1.0,
    "fred.stlouisfed.org": 0.5,
}


@dataclass
class _Entry:
    expires: float
    value: Any
    fetched_at: float


class HttpHub:
    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._cache: dict[str, _Entry] = {}
        self._host_locks: dict[str, asyncio.Lock] = {}
        self._host_last: dict[str, float] = {}
        self._inflight: dict[str, asyncio.Future] = {}

    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(20.0, connect=10.0),
                headers={"User-Agent": settings.user_agent, "Accept-Encoding": "gzip, deflate"},
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _throttle(self, host: str) -> None:
        lock = self._host_locks.setdefault(host, asyncio.Lock())
        async with lock:
            gap = HOST_MIN_INTERVAL.get(host, 0.2)
            wait = self._host_last.get(host, 0.0) + gap - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._host_last[host] = time.monotonic()

    async def get(self, source: str, url: str, *, params: dict | None = None, ttl: float = 60.0,
                  kind: str = "json") -> tuple[Any, float]:
        """Returns (payload, fetched_at_epoch). kind: 'json' | 'text'."""
        key = url + "?" + "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()))
        now = time.time()
        hit = self._cache.get(key)
        if hit and hit.expires > now:
            return hit.value, hit.fetched_at
        if key in self._inflight:              # coalesce identical concurrent requests
            return await self._inflight[key]

        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._inflight[key] = fut
        try:
            host = urlparse(url).hostname or ""
            await self._throttle(host)
            try:
                r = await self.client().get(url, params=params)
            except httpx.HTTPError as e:
                raise UpstreamError(source, f"network error: {type(e).__name__}") from e
            if r.status_code == 429:
                raise UpstreamError(source, "rate limited by provider", 429)
            if r.status_code >= 400:
                raise UpstreamError(source, f"HTTP {r.status_code}", r.status_code)
            try:
                value = r.json() if kind == "json" else r.text
            except ValueError as e:
                raise UpstreamError(source, "invalid response format") from e
            fetched = time.time()
            self._cache[key] = _Entry(expires=fetched + ttl, value=value, fetched_at=fetched)
            fut.set_result((value, fetched))
            return value, fetched
        except BaseException as e:
            # serve stale cache on failure, flagged by the caller via fetched_at age
            if hit is not None:
                fut.set_result((hit.value, hit.fetched_at))
                return hit.value, hit.fetched_at
            fut.set_exception(e)
            fut.exception()  # mark retrieved
            raise
        finally:
            self._inflight.pop(key, None)


hub = HttpHub()
