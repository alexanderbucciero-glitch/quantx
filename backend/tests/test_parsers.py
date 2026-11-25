"""Parser tests against real responses captured from each provider (tests/fixtures)."""
import json
from pathlib import Path

from app.analytics import indicators
from app.providers import crypto, dex, fred, news, sec

F = Path(__file__).parent / "fixtures"


def test_coinbase_candles_sorted_and_typed():
    bars = crypto.parse_coinbase_candles(json.loads((F / "coinbase_candles.json").read_text()))
    assert len(bars) > 100
    assert all(a["time"] < b["time"] for a, b in zip(bars, bars[1:]))
    assert all(b["low"] <= min(b["open"], b["close"]) and b["high"] >= max(b["open"], b["close"]) for b in bars)


def test_fred_csv_keeps_missing_as_none():
    obs = fred.parse_fred_csv((F / "fred_sp500.csv").read_text().rsplit("\n", 1)[0])
    assert obs[0]["date"].startswith("20")
    # synthetic check of the missing-value rule
    assert fred.parse_fred_csv("observation_date,X\n2020-01-01,.\n2020-01-02,1.5\n") == [
        {"date": "2020-01-01", "value": None}, {"date": "2020-01-02", "value": 1.5}]


def test_sec_atom_items_and_dedup():
    filings = sec.parse_current_filings((F / "sec_8k.atom").read_text(encoding="latin-1"))
    assert filings and all(f["form"].startswith("8-K") for f in filings)
    assert len({f["accession"] for f in filings}) == len(filings)
    carvana = [f for f in filings if "Carvana" in f["company"]][0]
    assert carvana["items"] == ["1.01", "8.01", "9.01"]
    assert carvana["events"][0]["event_type"] == "material_agreement"


def test_rss_parsing():
    fed = news.parse_rss((F / "fed_press.xml").read_text(encoding="utf-8-sig"), "fed")
    ecb = news.parse_rss((F / "ecb_press.xml").read_text(), "ecb")
    assert fed and ecb
    assert all(i["title"] and i["url"].startswith("https://") for i in fed + ecb)


def test_dex_flags_never_give_probability():
    pairs = json.loads((F / "dex_search.json").read_text())["pairs"]
    rows = [dex.summarise_pair(p) for p in pairs]
    assert all(r["probability"] is None for r in rows)
    low = dex.risk_flags({"liquidity": {"usd": 5000}, "pairCreatedAt": 0})
    assert {"low_liquidity", "holder_data_unavailable"} <= {f["flag"] for f in low}


def test_headline_dedupe():
    items = [{"title": "Fed raises rates by 25bp", "url": "a"}, {"title": "Fed Raises Rates by 25bp!", "url": "b"},
             {"title": "ECB holds", "url": "c"}]
    assert [i["url"] for i in news.dedupe(items)] == ["a", "c"]


def test_indicators():
    xs = [float(i) for i in range(1, 31)]
    assert indicators.sma(xs, 5)[4] == 3.0 and indicators.sma(xs, 5)[3] is None
    assert indicators.rsi(xs, 14)[-1] == 100.0          # only gains
    assert indicators.max_drawdown([10, 12, 6, 8]) == -0.5
    s = indicators.summary([100.0], None, 252)
    assert s["insufficient_data"] is True


def test_fundamentals_keep_original_filing():
    facts = {"facts": {"us-gaap": {"EarningsPerShareDiluted": {"units": {"USD/shares": [
        {"start": "2017-10-01", "end": "2018-09-29", "val": 2.98, "fp": "FY", "fy": 2020, "form": "10-K", "filed": "2020-10-30"},
        {"start": "2017-10-01", "end": "2018-09-29", "val": 11.91, "fp": "FY", "fy": 2018, "form": "10-K", "filed": "2018-11-05"},
    ]}}}}}
    rows = sec.extract_fact_series(facts, "eps_diluted")
    assert len(rows) == 1 and rows[0]["value"] == 11.91 and rows[0]["filed"] == "2018-11-05"
    assert rows[0]["period"] == "annual"
