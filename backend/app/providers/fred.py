"""FRED (Federal Reserve Bank of St. Louis) public CSV download.

The fredgraph.csv endpoint is the same download offered on every FRED series page.
Missing observations appear as '.' or blank and are returned as None, never filled.
Note: some series (e.g. SP500, NASDAQCOM, DJIA) are licensed to FRED and limited
to roughly the last 10 years.
"""
from __future__ import annotations

import csv
import io

from ..core.http import UpstreamError, hub

# Curated catalogue (id -> metadata). All ids are real FRED series ids.
CATALOGUE: dict[str, dict] = {
    # equity indices & volatility
    "SP500":       {"name": "S&P 500", "group": "Equity indices", "units": "index"},
    "NASDAQCOM":   {"name": "NASDAQ Composite", "group": "Equity indices", "units": "index"},
    "DJIA":        {"name": "Dow Jones Industrial Average", "group": "Equity indices", "units": "index"},
    "VIXCLS":      {"name": "CBOE Volatility Index (VIX)", "group": "Equity indices", "units": "index"},
    # rates
    "DFF":         {"name": "Effective Federal Funds Rate", "group": "Rates", "units": "%"},
    "DGS2":        {"name": "US Treasury 2Y yield", "group": "Rates", "units": "%"},
    "DGS10":       {"name": "US Treasury 10Y yield", "group": "Rates", "units": "%"},
    "T10Y2Y":      {"name": "10Y minus 2Y spread", "group": "Rates", "units": "pp"},
    "BAMLH0A0HYM2": {"name": "US High Yield OAS", "group": "Rates", "units": "%"},
    # FX & commodities
    "DEXUSEU":     {"name": "USD per EUR", "group": "FX & commodities", "units": "USD"},
    "DEXUSUK":     {"name": "USD per GBP", "group": "FX & commodities", "units": "USD"},
    "DEXJPUS":     {"name": "JPY per USD", "group": "FX & commodities", "units": "JPY"},
    "DTWEXBGS":    {"name": "Broad USD index", "group": "FX & commodities", "units": "index"},
    "DCOILWTICO":  {"name": "WTI crude oil", "group": "FX & commodities", "units": "USD/bbl"},
    "DCOILBRENTEU": {"name": "Brent crude oil", "group": "FX & commodities", "units": "USD/bbl"},
    # macro (long history)
    "CPIAUCSL":    {"name": "US CPI, all items", "group": "Macro", "units": "index 1982-84=100"},
    "PCEPILFE":    {"name": "Core PCE price index", "group": "Macro", "units": "index"},
    "UNRATE":      {"name": "US unemployment rate", "group": "Macro", "units": "%"},
    "PAYEMS":      {"name": "Nonfarm payrolls", "group": "Macro", "units": "thousands"},
    "GDPC1":       {"name": "US real GDP", "group": "Macro", "units": "bn chained 2017 USD"},
    "INDPRO":      {"name": "Industrial production", "group": "Macro", "units": "index"},
    "FEDFUNDS":    {"name": "Fed funds rate (monthly)", "group": "Macro", "units": "%"},
    "M2SL":        {"name": "M2 money supply", "group": "Macro", "units": "bn USD"},
    "UMCSENT":     {"name": "UMich consumer sentiment", "group": "Macro", "units": "index"},
    "CP0000EZ19M086NEST": {"name": "Euro area HICP", "group": "Macro", "units": "index"},
    "IRLTLT01DEM156N": {"name": "Germany 10Y yield (monthly)", "group": "Macro", "units": "%"},
}


def parse_fred_csv(text: str) -> list[dict]:
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header or len(header) < 2:
        raise ValueError("not a FRED CSV")
    out = []
    for row in reader:
        if len(row) < 2 or not row[0]:
            continue
        v = row[1].strip()
        out.append({"date": row[0], "value": None if v in ("", ".") else float(v)})
    return out


async def series(series_id: str, start: str | None = None) -> dict:
    params = {"id": series_id}
    if start:
        params["cosd"] = start
    text, fetched = await hub.get("fred", "https://fred.stlouisfed.org/graph/fredgraph.csv",
                                  params=params, ttl=3600, kind="text")
    try:
        obs = parse_fred_csv(text)
    except ValueError as e:
        raise UpstreamError("fred", f"could not parse series {series_id}") from e
    valid = [o for o in obs if o["value"] is not None]
    meta = CATALOGUE.get(series_id, {"name": series_id, "group": "Other", "units": ""})
    return {
        "id": series_id, **meta,
        "source": "FRED, Federal Reserve Bank of St. Louis",
        "source_url": f"https://fred.stlouisfed.org/series/{series_id}",
        "fetched_at": fetched,
        "first_date": valid[0]["date"] if valid else None,
        "last_date": valid[-1]["date"] if valid else None,
        "observations": len(obs),
        "missing": len(obs) - len(valid),
        "data": obs,
    }
