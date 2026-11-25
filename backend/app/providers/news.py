"""Free news sources: central-bank RSS feeds and the GDELT DOC 2.0 API.

GDELT: https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/  (max 1 request / 5 s)
Fed RSS: https://www.federalreserve.gov/feeds/feeds.htm
ECB RSS: https://www.ecb.europa.eu/home/html/rss.en.html
"""
from __future__ import annotations

import hashlib
from email.utils import parsedate_to_datetime

from defusedxml import ElementTree as ET

from ..core.http import UpstreamError, hub

RSS_FEEDS = {
    "fed": {"name": "Federal Reserve press releases", "url": "https://www.federalreserve.gov/feeds/press_all.xml",
            "primary": True, "category": "central_bank"},
    "ecb": {"name": "ECB press releases", "url": "https://www.ecb.europa.eu/rss/press.html",
            "primary": True, "category": "central_bank"},
}


def _iso(pub: str | None) -> str | None:
    if not pub:
        return None
    try:
        return parsedate_to_datetime(pub).isoformat()
    except (TypeError, ValueError):
        return None


def parse_rss(xml_text: str, source: str) -> list[dict]:
    root = ET.fromstring(xml_text.lstrip("﻿").encode("utf-8"))
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        items.append({
            "id": hashlib.sha1((link or title).encode()).hexdigest()[:16],
            "title": title, "url": link, "source": source,
            "published_at": _iso(it.findtext("pubDate")),
            "summary": (it.findtext("description") or "").strip()[:400],
        })
    return items


async def rss(feed_key: str) -> dict:
    feed = RSS_FEEDS[feed_key]
    text, fetched = await hub.get(feed_key, feed["url"], ttl=300, kind="text")
    try:
        items = parse_rss(text, feed["name"])
    except ET.ParseError as e:
        raise UpstreamError(feed_key, "could not parse RSS") from e
    for i in items:
        i["primary_source"] = feed["primary"]
        i["category"] = feed["category"]
    return {"source": feed["name"], "fetched_at": fetched, "items": items}


def parse_gdelt(raw: dict) -> list[dict]:
    out = []
    for a in raw.get("articles", []) or []:
        seen = a.get("seendate")  # e.g. 20261125T143000Z
        iso = f"{seen[0:4]}-{seen[4:6]}-{seen[6:8]}T{seen[9:11]}:{seen[11:13]}:{seen[13:15]}Z" if seen and len(seen) >= 15 else None
        out.append({
            "id": hashlib.sha1(a.get("url", "").encode()).hexdigest()[:16],
            "title": a.get("title", "").strip(), "url": a.get("url"),
            "source": a.get("domain"), "published_at": iso, "language": a.get("language"),
            "country": a.get("sourcecountry"), "primary_source": False, "category": "media",
        })
    return out


async def gdelt(query: str, max_records: int = 50, timespan: str = "1d") -> dict:
    params = {"query": query, "mode": "artlist", "format": "json", "maxrecords": max_records,
              "sort": "datedesc", "timespan": timespan}
    raw, fetched = await hub.get("gdelt", "https://api.gdeltproject.org/api/v2/doc/doc",
                                 params=params, ttl=600)
    if not isinstance(raw, dict):
        raise UpstreamError("gdelt", "unexpected payload")
    return {"source": "GDELT DOC 2.0", "fetched_at": fetched, "query": query, "items": parse_gdelt(raw)}


def title_key(title: str) -> str:
    """Normalised key for near-duplicate headline detection."""
    words = [w for w in "".join(ch.lower() if ch.isalnum() else " " for ch in title).split() if len(w) > 2]
    return " ".join(sorted(set(words))[:12])


def dedupe(items: list[dict]) -> list[dict]:
    seen_url, seen_title, out = set(), set(), []
    for i in items:
        k = title_key(i.get("title", ""))
        if i.get("url") in seen_url or (k and k in seen_title):
            continue
        seen_url.add(i.get("url"))
        seen_title.add(k)
        out.append(i)
    return out
