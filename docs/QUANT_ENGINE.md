# QUANTX quantitative engine (v0.2)

Everything here runs on free public data. All trading is simulated: there is no broker, exchange-account or wallet integration, and no code path that can place a real order.

## Event flow

```
Coinbase WebSocket tick ─► quote update ─► simulated position checks (stop / TP1 / TP2 / time)    ~0.02 ms
Candle-close scheduler  ─► fetch only the new candle(s) of assets whose bar just closed (concurrent)
                        ─► re-analyse that asset (last 600 bars) ─► 5 strategies ─► decision gates
                        ─► research report ─► (all gates pass) open simulated position
Universe 30 min · news 5 min · walk-forward validation at start + every 6 h · equity snapshot 60 s
```

The agent runs inside the FastAPI process as asyncio workers (`app/agent/engine.py`). Every activity-log line is emitted by the code that did the work, with its measured latency, and is pushed to the browser over a WebSocket (`/api/ws/agent`).

## Universe and ranking

- **Crypto:** the 25 highest-volume CoinGecko coins that trade against USD on Coinbase, excluding stablecoins. 15-minute candles.
- **Solana / Pump.fun:** up to 8 trending GeckoTerminal pools with at least $50k liquidity. 5-minute candles. Paper trading needs at least $100k liquidity.
- **Stocks:** inactive. Intraday equity data needs a key (a free Tiingo key, not wired into the agent yet).

The rank score combines liquidity, relative volume, spread, volatility fit (are 2–5% targets reachable?), momentum, recent structure events, historical strategy results, news mentions and data quality. Liquidity and data quality are multiplicative, so a large % volume spike on a thin market cannot rank highly. An asset is excluded when:
- 24h volume is under $5M (crypto)
- pool liquidity is under $100k (memecoins)
- the spread exceeds 0.25% (crypto) or 1% (memecoins)
- none of the 2–5% targets is reachable within the holding limit

## Market structure (`app/analysis/structure.py`)

The engine detects swings (k-bar fractals, confirmed k bars later), HH/HL/LH/LL labels, BOS and CHoCH, buy-side and sell-side liquidity pools, liquidity sweeps, failed breakouts, fair value gaps, order blocks, support/resistance clusters, engulfing/hammer/shooting-star/doji candles, volume imbalances, volatility expansion and RSI divergence. Each detection stores the exact levels, penetration, close location, volume ratio and confirmation bar. A still-forming candle is kept as `provisional` and never used for signals.

## Strategies (`app/strategies/library.py`)

| Strategy | Rule (fixed in advance, no parameter fitting) |
|---|---|
| Momentum | Close > EMA20 > EMA50 (mirror for shorts), accelerating 3/6-bar return, relative volume ≥ 1.5×, fresh BOS/CHoCH |
| Mean reversion | z-score vs 20-bar mean ≤ −2.5 with RSI < 28 and a reversal candle (mirror) |
| Liquidity sweep | Sweep of a confirmed swing level, close back inside, entered on the confirming close |
| Breakout | Bollinger bandwidth in the lowest 20% of 120 bars, then a close beyond the 20-bar range on ≥ 1.8× volume |
| Volume surge | ≥ 3× relative volume, close in the top/bottom quarter of the bar, no opposing divergence |
| Statistical (research only) | Engle–Granger cointegration of the top-10 pairs. Two-leg positions are not simulated yet. |
| Event-driven (inactive) | Needs intraday equity prices to measure reactions to SEC 8-K events |

Memecoins are long-only (spot DEX). A test checks that every strategy is causal: signals at bar *t* are identical with and without future candles.

## The 2–5% target system (`app/quant/targets.py`)

- **Feasibility:** a target is realistic only if it is ≤ ATR% × √(max holding bars). That is the typical move over the horizon under random-walk scaling.
- **Stop:** max(1.5 × ATR, structural invalidation + 0.2 × ATR).
- **Target choice:** TP1 is the smallest feasible target whose reward/risk after costs is ≥ 1.2. TP2 is the next one.
- **Costs** (`app/quant/costs.py`):
  - crypto: 0.40% taker fee per side, plus half-spread and square-root impact
  - memecoins: 0.30% DEX fee, plus constant-product price impact from pool liquidity

## Probability engine (`app/quant/probability.py`)

The event being predicted is "TP1 is reached before the stop". The model uses hierarchical Beta-binomial shrinkage by strategy × asset class × regime. Regime is with/counter trend × high/low volatility, computed causally.

The model is fitted **walk-forward**: each historical trade is predicted only from trades that had already closed. Out-of-sample predictions are scored with the Brier score, log-loss, expected calibration error and a reliability table, against a no-skill baseline. No probability is shown when:
- there are fewer than 30 samples, or
- there are too few out-of-sample predictions.

The UI keeps three numbers separate: AI confidence (heuristic), historical win rate (in-sample, with a Wilson CI), and the calibrated probability.

## Decision gates (all must pass for a simulated position)

1. The signal is on a closed candle.
2. The market is tradeable (liquidity, spread, data quality).
3. A realistic 2–5% target exists with net reward/risk ≥ the minimum.
4. There is enough historical evidence, and calibration is verified.
5. Expected net return after costs is ≥ the minimum (default +0.05% per trade).
6. Portfolio risk limits allow it:
   - 0.5% risk per trade
   - 20% notional cap per position (2% for memecoins)
   - 60% gross exposure
   - 5 open positions
   - 2% daily loss limit

## Paper trading (`app/paper/engine.py`)

- **Entries:** longs buy at the live ask and shorts sell at the bid, plus modelled slippage and a taker fee on every fill.
- **Exits:** stops fill at the first price beyond the stop (gaps are not given the stop price). 50% is closed at TP1, then the stop moves to break-even. The rest closes at TP2, the stop, or the time limit.
- **Records:** every fill is kept, and every decision is logged with its full report.

## Backtesting (`app/quant/backtest.py`, `/api/backtest`)

- **Fills:** at the next bar's open after the signal. If a bar touches both the stop and a target, the stop is assumed to fill first.
- **Costs:** the same cost model as paper trading.
- **Reports:**
  - total return; annualised return (only for 3+ months of data)
  - max drawdown, per-trade Sharpe/Sortino, win rate, profit factor, average win/loss
  - exposure, number of fills, and buy-and-hold as the benchmark
  - a 70/30 chronological split, breakdowns by strategy and by regime, and walk-forward calibration

## Measured performance (cloud container, Python 3.13)

| Operation | Time |
|---|---|
| Full structure analysis, 600 candles | 7.6 ms (live average ≈ 11 ms) |
| Backtest, 3,000 candles × 5 strategies | ≈ 0.3 s |
| Tick → simulated position check | ≈ 0.02 ms |
| Candle close → decision (25 crypto assets, network included) | a few seconds, dominated by REST round-trips |

**Rust/C++ evaluation:** not justified yet. Computation takes milliseconds while data-provider round-trips take 100–1,000 ms. The next real speed-up would be building candles locally from the WebSocket trade stream instead of fetching them over REST.

## First live results (25 Nov 2026, ≈25 days of 15m history, 33 assets)

The walk-forward validation produced 1,452 historical setups. After 0.40%-per-side fees and slippage, **every strategy had a negative average net return**: for example, momentum on crypto reached TP1 in 22.7% of 326 setups, averaging −0.80% per trade. The probability model showed no skill over the baseline. The agent therefore rejects these setups and logs why. This is the intended behaviour, not a malfunction. Lower fees (maker orders), longer histories, or different rules would be needed before any strategy qualifies.
