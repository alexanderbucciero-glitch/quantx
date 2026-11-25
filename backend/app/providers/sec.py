"""SEC EDGAR — free primary-source company data.

Docs: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
Fair access: max 10 requests/second, descriptive User-Agent required.
"""
from __future__ import annotations

import html
import re
from datetime import date

from defusedxml import ElementTree as ET

from ..core.http import UpstreamError, hub

ATOM = "{http://www.w3.org/2005/Atom}"

# 8-K item codes -> QUANTX event taxonomy (from SEC Form 8-K General Instructions)
ITEM_EVENTS: dict[str, tuple[str, str]] = {
    "1.01": ("material_agreement", "Entry into a material definitive agreement"),
    "1.02": ("agreement_terminated", "Termination of a material definitive agreement"),
    "1.03": ("bankruptcy", "Bankruptcy or receivership"),
    "1.05": ("cybersecurity_incident", "Material cybersecurity incident"),
    "2.01": ("acquisition_completed", "Completion of acquisition or disposition of assets"),
    "2.02": ("earnings_release", "Results of operations and financial condition"),
    "2.03": ("new_debt", "Creation of a direct financial obligation"),
    "2.04": ("debt_acceleration", "Triggering events that accelerate a financial obligation"),
    "2.05": ("restructuring", "Costs associated with exit or disposal activities"),
    "2.06": ("impairment", "Material impairments"),
    "3.01": ("delisting_notice", "Notice of delisting or failure to satisfy listing rule"),
    "3.02": ("unregistered_equity_sale", "Unregistered sales of equity securities"),
    "3.03": ("rights_modified", "Material modification to rights of security holders"),
    "4.01": ("auditor_change", "Changes in registrant's certifying accountant"),
    "4.02": ("restatement", "Non-reliance on previously issued financial statements"),
    "5.01": ("control_change", "Changes in control of registrant"),
    "5.02": ("executive_change", "Departure/election of directors or officers"),
    "5.03": ("bylaw_change", "Amendments to articles or bylaws"),
    "5.07": ("shareholder_vote", "Submission of matters to a vote of security holders"),
    "7.01": ("reg_fd_disclosure", "Regulation FD disclosure"),
    "8.01": ("other_event", "Other events"),
    "9.01": ("exhibits", "Financial statements and exhibits"),
}
# Items that are usually price-relevant on their own (importance 0..1, rule-based prior).
ITEM_IMPORTANCE = {"1.03": 1.0, "4.02": 0.95, "2.02": 0.9, "5.01": 0.9, "2.01": 0.8, "1.05": 0.8,
                   "3.01": 0.8, "2.06": 0.7, "2.04": 0.7, "5.02": 0.6, "1.01": 0.6, "1.02": 0.6,
                   "2.05": 0.6, "4.01": 0.5, "3.02": 0.4, "2.03": 0.4, "8.01": 0.3, "7.01": 0.3,
                   "5.07": 0.2, "5.03": 0.2, "3.03": 0.4, "9.01": 0.0}

_TITLE = re.compile(r"^(?P<form>\S+) - (?P<name>.+?) \((?P<cik>\d{10})\) \((?P<role>[^)]+)\)$")
_ITEM = re.compile(r"Item (\d\.\d\d)")


def parse_current_filings(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text.encode("latin-1", "replace"))
    out = []
    for e in root.findall(f"{ATOM}entry"):
        title = (e.findtext(f"{ATOM}title") or "").strip()
        m = _TITLE.match(title)
        summary = html.unescape(e.findtext(f"{ATOM}summary") or "")
        items = sorted(set(_ITEM.findall(summary)))
        link = e.find(f"{ATOM}link")
        events = [{"item": i, "event_type": ITEM_EVENTS[i][0], "label": ITEM_EVENTS[i][1]}
                  for i in items if i in ITEM_EVENTS]
        out.append({
            "form": m["form"] if m else None,
            "company": m["name"] if m else title,
            "cik": m["cik"] if m else None,
            "role": m["role"] if m else None,
            "updated": e.findtext(f"{ATOM}updated"),
            "accession": (e.findtext(f"{ATOM}id") or "").split("accession-number=")[-1],
            "url": link.get("href") if link is not None else None,
            "items": items,
            "events": events,
            "importance": max((ITEM_IMPORTANCE.get(i, 0.3) for i in items), default=0.0),
        })
    # The feed lists every party to a filing separately (co-registrants, reporting owners).
    # Keep one row per accession number, preferring the Filer/Subject entry.
    by_acc: dict[str, dict] = {}
    for f in out:
        prev = by_acc.get(f["accession"])
        if prev is None or (prev["role"] not in ("Filer", "Subject") and f["role"] in ("Filer", "Subject")):
            by_acc[f["accession"]] = f
    return list(by_acc.values())


async def current_filings(form: str = "8-K", count: int = 40) -> dict:
    xml_text, fetched = await hub.get(
        "sec", "https://www.sec.gov/cgi-bin/browse-edgar",
        params={"action": "getcurrent", "type": form, "count": count, "output": "atom"},
        ttl=60, kind="text")
    try:
        filings = parse_current_filings(xml_text)
    except ET.ParseError as e:
        raise UpstreamError("sec", "could not parse EDGAR feed") from e
    return {"source": "SEC EDGAR current filings", "fetched_at": fetched, "form": form,
            "filings": filings, "verification": "primary_source_confirmed"}


async def ticker_map() -> dict[str, dict]:
    raw, _ = await hub.get("sec", "https://www.sec.gov/files/company_tickers.json", ttl=86400)
    return {v["ticker"].upper(): {"cik": f'{int(v["cik_str"]):010d}', "name": v["title"],
                                  "ticker": v["ticker"].upper()} for v in raw.values()}


async def search_companies(q: str, limit: int = 12) -> list[dict]:
    q = q.strip().upper()
    if not q:
        return []
    m = await ticker_map()
    exact = [m[q]] if q in m else []
    rest = [v for k, v in m.items() if k != q and (k.startswith(q) or q in v["name"].upper())]
    return (exact + rest)[:limit]


async def company_profile(ticker: str) -> dict:
    m = await ticker_map()
    info = m.get(ticker.upper())
    if not info:
        raise LookupError(f"{ticker} not found in SEC ticker list")
    sub, fetched = await hub.get("sec", f"https://data.sec.gov/submissions/CIK{info['cik']}.json", ttl=600)
    recent = sub.get("filings", {}).get("recent", {})
    filings = []
    for i in range(min(40, len(recent.get("form", [])))):
        acc = recent["accessionNumber"][i]
        filings.append({
            "form": recent["form"][i], "filed": recent["filingDate"][i],
            "accepted": recent.get("acceptanceDateTime", [None] * (i + 1))[i],
            "items": recent.get("items", [""] * (i + 1))[i],
            "description": recent.get("primaryDocDescription", [""] * (i + 1))[i],
            "url": f"https://www.sec.gov/Archives/edgar/data/{int(info['cik'])}/{acc.replace('-', '')}/{recent['primaryDocument'][i]}",
        })
    return {"ticker": info["ticker"], "cik": info["cik"], "name": sub.get("name", info["name"]),
            "sic": sub.get("sic"), "industry": sub.get("sicDescription"),
            "exchanges": sub.get("exchanges"), "fiscal_year_end": sub.get("fiscalYearEnd"),
            "state": sub.get("stateOfIncorporation"), "filings": filings,
            "source": "SEC EDGAR submissions API", "fetched_at": fetched}


FACT_CANDIDATES = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"],
    "net_income": ["NetIncomeLoss"],
    "eps_diluted": ["EarningsPerShareDiluted"],
    "operating_income": ["OperatingIncomeLoss"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue"],
    "total_debt": ["LongTermDebt", "LongTermDebtNoncurrent"],
    "shares_outstanding": ["dei:EntityCommonStockSharesOutstanding"],
}


def extract_fact_series(facts: dict, metric: str) -> list[dict]:
    """Return de-duplicated reported values with their FILING date (point-in-time)."""
    for tag in FACT_CANDIDATES[metric]:
        ns, _, name = tag.rpartition(":")
        block = facts.get("facts", {}).get(ns or "us-gaap", {}).get(name)
        if not block:
            continue
        unit_vals = next(iter(block["units"].values()))
        rows, seen = [], set()
        # earliest filing first, so each period keeps the value as ORIGINALLY reported
        # (point-in-time; later restatements and split-adjusted comparatives are ignored)
        for v in sorted(unit_vals, key=lambda x: x.get("filed", "")):
            if v.get("form") not in ("10-K", "10-Q", "20-F", "40-F", "10-K/A", "10-Q/A"):
                continue
            k = (v.get("start"), v["end"], v.get("fp"))
            if k in seen:
                continue
            seen.add(k)
            days = None
            if v.get("start"):
                days = (date.fromisoformat(v["end"]) - date.fromisoformat(v["start"])).days
            rows.append({"start": v.get("start"), "end": v["end"], "value": v["val"], "period_days": days,
                         "period": None if days is None else "quarter" if days < 120 else "annual" if days > 340 else "ytd",
                         "fy": v.get("fy"), "fp": v.get("fp"), "form": v["form"], "filed": v["filed"],
                         "tag": tag})
        rows.sort(key=lambda r: (r["end"], r["filed"]))
        if rows:
            return rows
    return []


async def fundamentals(ticker: str) -> dict:
    m = await ticker_map()
    info = m.get(ticker.upper())
    if not info:
        raise LookupError(f"{ticker} not found in SEC ticker list")
    facts, fetched = await hub.get("sec", f"https://data.sec.gov/api/xbrl/companyfacts/CIK{info['cik']}.json",
                                   ttl=3600)
    out = {metric: extract_fact_series(facts, metric)[-60:] for metric in FACT_CANDIDATES}
    return {"ticker": info["ticker"], "source": "SEC EDGAR XBRL company facts (as filed)",
            "fetched_at": fetched, "metrics": out}
