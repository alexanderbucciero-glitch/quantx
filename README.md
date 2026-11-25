# QUANTX

A personal quantitative market research terminal built on **free data sources only**.
It is for research and paper simulation. It has no broker or wallet connection.

## Run it on your computer

You need [Python 3.11+](https://www.python.org/downloads/) and [Node.js 20+](https://nodejs.org/).

- macOS / Linux: `./start.sh`
- Windows: double-click `start.bat`

Then open **http://localhost:3000**.

On the first run, open `backend/.env` and replace the email in `QUANTX_USER_AGENT` with your own.
The SEC requires automated clients to identify themselves this way.

## What works today (all free, no accounts)

| Page | Data | Source |
|---|---|---|
| Dashboard | Summary of everything below | — |
| Global Markets | S&P 500, Nasdaq, Dow, VIX, Treasury curve, FX, oil, credit spreads | FRED |
| Stock Intelligence | Company profile, filings, quarterly/annual results as originally filed | SEC EDGAR |
| Crypto Intelligence | Candlestick charts (15m–1d), SMA/RSI/volatility, top 100 by market cap | Coinbase, CoinGecko |
| Memecoin Scanner | New Solana / Pump.fun tokens with liquidity and manipulation flags | DexScreener |
| Breaking News | Fed + ECB releases (primary), global headlines (unverified) | Fed/ECB RSS, GDELT |
| Event-Driven Analysis | Company events classified from 8-K item codes | SEC EDGAR |
| Historical Database | Macro series back to 1947 with coverage/missing-data stats | FRED |
| Integrations | Live health check of every source | — |

Charts use TradingView **Lightweight Charts** (open source). They need no TradingView account.

## What is deliberately empty

AI Predictions, Signals, Backtesting, Paper Portfolio, Model Performance and Social Intelligence
explain what they need and show **no placeholder numbers**. They are built in Stages 3–10
(see `docs/ARCHITECTURE.md`).

## Optional free keys

| Key | Unlocks | Where |
|---|---|---|
| `QUANTX_TIINGO_API_KEY` | Stock & ETF daily price history (decades) | tiingo.com → Account → API |

The Tiingo adapter is written but has not yet been tested against the live API (no key in the build environment).

## Tests

```bash
cd backend && .venv/bin/python -m pytest -q
```

Parser tests run against real responses captured from each provider (`backend/tests/fixtures`).
