"""Read endpoints used by the web UI. Every response names its source and fetch time."""
from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, HTTPException, Query

from ..analytics import indicators
from ..core.http import UpstreamError
from ..providers import crypto, dex, fred, news, sec, tiingo

router = APIRouter(prefix="/api")


def _fail(e: Exception) -> HTTPException:
    if isinstance(e, UpstreamError):
        return HTTPException(status_code=502, detail={"source": e.source, "error": e.message})
    if isinstance(e, LookupError):
        return HTTPException(status_code=404, detail={"error": str(e)})
    if isinstance(e, ValueError):
        return HTTPException(status_code=400, detail={"error": str(e)})
    raise e


async def _safe(coro):
    """Run a source call; return {'ok':False,...} instead of raising so one dead source
    does not blank a whole page."""
    try:
        return {"ok": True, **(await coro)}
    except UpstreamError as e:
        return {"ok": False, "source": e.source, "error": e.message}


# ---------------------------------------------------------------- markets
@router.get("/markets/catalogue")
async def markets_catalogue():
    return {"series": [{"id": k, **v} for k, v in fred.CATALOGUE.items()]}


@router.get("/markets/series/{series_id}")
async def market_series(series_id: str, start: str | None = None):
    try:
        return await fred.series(series_id, start)
    except Exception as e:
        raise _fail(e)


@router.get("/markets/overview")
async def markets_overview():
    ids = ["SP500", "NASDAQCOM", "DJIA", "VIXCLS", "DGS2", "DGS10", "T10Y2Y", "DFF", "DEXUSEU",
           "DEXUSUK", "DEXJPUS", "DCOILWTICO", "DCOILBRENTEU", "BAMLH0A0HYM2"]
    start = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 400 * 86400))
    results = await asyncio.gather(*[_safe(fred.series(i, start)) for i in ids])
    rows = []
    for i, r in zip(ids, results):
        meta = fred.CATALOGUE[i]
        if not r["ok"]:
            rows.append({"id": i, **meta, "ok": False, "error": r["error"]})
            continue
        vals = [o for o in r["data"] if o["value"] is not None]
        last = vals[-1] if vals else None
        prev = vals[-2] if len(vals) > 1 else None
        year_ago = next((o for o in vals if o["date"] >= time.strftime("%Y-%m-%d", time.gmtime(time.time() - 365 * 86400))), None)
        rows.append({
            "id": i, **meta, "ok": True, "last": last, "prev": prev,
            "change": (last["value"] - prev["value"]) if last and prev else None,
            "change_pct": (last["value"] / prev["value"] - 1) if last and prev and prev["value"] else None,
            "change_1y_pct": (last["value"] / year_ago["value"] - 1) if last and year_ago and year_ago["value"] else None,
            "spark": [o["value"] for o in vals[-90:]],
            "fetched_at": r["fetched_at"], "source_url": r["source_url"],
        })
    return {"source": "FRED", "rows": rows}


# ---------------------------------------------------------------- crypto
@router.get("/crypto/markets")
async def crypto_markets(per_page: int = Query(50, le=250), category: str | None = None):
    try:
        return await crypto.coingecko_markets(per_page, category)
    except Exception as e:
        raise _fail(e)


@router.get("/crypto/candles/{product}")
async def crypto_candles(product: str, interval: str = "1d"):
    try:
        data = await crypto.coinbase_candles(product.upper(), interval)
    except Exception as e:
        raise _fail(e)
    closes = [b["close"] for b in data["bars"]]
    vols = [b["volume"] for b in data["bars"]]
    ppy = {"1m": 525600, "5m": 105120, "15m": 35040, "1h": 8760, "6h": 1460, "1d": 365}[interval]
    data["stats"] = indicators.summary(closes, vols, ppy)
    data["overlays"] = {"sma20": indicators.sma(closes, 20), "sma50": indicators.sma(closes, 50),
                        "rsi14": indicators.rsi(closes, 14)}
    return data


@router.get("/crypto/ticker/{pair}")
async def crypto_ticker(pair: str):
    try:
        return await crypto.kraken_ticker(pair.upper())
    except Exception as e:
        raise _fail(e)


# ---------------------------------------------------------------- memecoins
@router.get("/memecoins/latest")
async def memecoins_latest(limit: int = Query(30, le=60)):
    try:
        return await dex.latest_solana_tokens(limit)
    except Exception as e:
        raise _fail(e)


@router.get("/memecoins/search")
async def memecoins_search(q: str = Query(..., min_length=2)):
    try:
        return await dex.search_pairs(q)
    except Exception as e:
        raise _fail(e)


# ---------------------------------------------------------------- news & events
@router.get("/news/feed")
async def news_feed(query: str = "(stocks OR markets OR earnings OR inflation OR crypto) sourcelang:english"):
    fed, ecb, gd = await asyncio.gather(_safe(news.rss("fed")), _safe(news.rss("ecb")),
                                        _safe(news.gdelt(query, 75)))
    items, sources = [], []
    for r, name in ((fed, "Federal Reserve"), (ecb, "ECB"), (gd, "GDELT")):
        sources.append({"name": name, "ok": r["ok"], "error": r.get("error"), "fetched_at": r.get("fetched_at"),
                        "count": len(r.get("items", []))})
        items.extend(r.get("items", []))
    items = news.dedupe(items)
    items.sort(key=lambda i: i.get("published_at") or "", reverse=True)
    return {"sources": sources, "items": items[:150],
            "note": "Media headlines are unverified. Central-bank items are primary sources."}


@router.get("/events/sec")
async def sec_events(form: str = "8-K", count: int = Query(40, le=100)):
    try:
        return await sec.current_filings(form, count)
    except Exception as e:
        raise _fail(e)


# ---------------------------------------------------------------- stocks
@router.get("/stocks/search")
async def stock_search(q: str = Query(..., min_length=1)):
    try:
        return {"results": await sec.search_companies(q)}
    except Exception as e:
        raise _fail(e)


@router.get("/stocks/{ticker}/profile")
async def stock_profile(ticker: str):
    try:
        return await sec.company_profile(ticker)
    except Exception as e:
        raise _fail(e)


@router.get("/stocks/{ticker}/fundamentals")
async def stock_fundamentals(ticker: str):
    try:
        return await sec.fundamentals(ticker)
    except Exception as e:
        raise _fail(e)


@router.get("/stocks/{ticker}/prices")
async def stock_prices(ticker: str, start: str = "1970-01-01"):
    if not tiingo.configured():
        raise HTTPException(status_code=503, detail={
            "source": "tiingo", "error": "not configured",
            "how_to_fix": "Create a free account at tiingo.com, then set QUANTX_TIINGO_API_KEY in backend/.env"})
    try:
        data = await tiingo.daily(ticker, start)
    except Exception as e:
        raise _fail(e)
    closes = [b["adj_close"] for b in data["bars"] if b["adj_close"] is not None]
    data["stats"] = indicators.summary(closes, [b["volume"] or 0 for b in data["bars"]], 252)
    return data
