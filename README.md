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

## Autonomous paper-trading desk (v0.3)

Press **ACTIVATE**. With no further input, the backend agent:
- scans crypto, gold, forex and Solana pools
- decides on every closed candle
- executes simulated market, limit and stop orders
- manages stops, targets and trailing exits
- closes positions on its own
- writes a report for every closed trade, plus daily, weekly and monthly reports

**DEACTIVATE** stops new entries but keeps managing open positions. **EMERGENCY STOP** closes everything at market.

Everything is simulated, and nothing can trade real money. See `docs/AUTONOMOUS_DESK.md` for how it decides, the risk rules, and the honest results so far.

## AI agent, strategies and paper trading (v0.2)

Press **ACTIVATE AI** in the top bar. The agent then does the following:
- builds a universe of the 25 highest-volume Coinbase crypto pairs and the top trending Solana / Pump.fun pools
- streams live prices over WebSocket
- analyses every newly closed candle (liquidity sweeps, BOS/CHoCH, fair value gaps, order blocks and more)
- runs five strategies against configurable 2–5% targets after fees and slippage
- validates every strategy walk-forward and opens **simulated** positions only when all decision gates pass

Everything it does appears in the live activity log on the **AI Agent** page.

| New / upgraded page | What it shows |
|---|---|
| AI Agent | Activate/deactivate, live activity log, counters, latency, data-source status, opportunity ranking, research reports |
| Market Structure | Annotated candlestick chart (BOS/CHoCH, sweeps, liquidity pools, FVGs, order blocks, S/R, entry/stop/TP lines) with the evidence for each detection |
| Paper Portfolio | Simulated balance, open/closed positions, return, win rate, profit factor, drawdown, equity curve, exposure, settings |
| Long / Short Signals | Every setup, accepted or rejected, with the deciding gate |
| AI Predictions | AI confidence vs historical win rate vs walk-forward calibrated probability |
| Backtesting Lab | Bar-by-bar replay with costs, metrics, regime breakdown and trades on the chart |
| Model Performance | Strategy × regime results and cointegrated pairs |

How it works, and the first honest results: `docs/QUANT_ENGINE.md`.
Social Intelligence still needs a permitted API (Reddit approval or a YouTube key).
Simulated records are stored in `backend/data/quantx.db` (SQLite, local).

### Docker

```bash
docker compose up --build
```

## Optional free keys

| Key | Unlocks | Where |
|---|---|---|
| `QUANTX_TIINGO_API_KEY` | Stock & ETF daily price history (decades) | tiingo.com → Account → API |
| `QUANTX_DATABASE_URL` | Move from local SQLite to PostgreSQL/TimescaleDB | see `backend/.env.example` |

The Tiingo adapter is written but has not yet been tested against the live API (no key in the build environment).

## Tests

```bash
cd backend && .venv/bin/python -m pytest -q
```

Parser tests run against real responses captured from each provider (`backend/tests/fixtures`).
