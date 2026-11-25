# QUANTX autonomous paper-trading desk (v0.3)

**Simulation only.** QUANTX has no brokerage, exchange-account or wallet integration, and no code path that can send a real order. It uses real market data with simulated capital.

## What happens after ACTIVATE

The agent is a persistent backend process inside the FastAPI server (`app/agent/engine.py`). Closing the website does not stop it. After a server restart it resumes from the database: positions, resting orders, evidence tiers and settings all come back.

```
universe (30 min) ─► 20 high-volume crypto (Coinbase) · gold: PAXG (Coinbase), XAUT (Kraken)
                     8 forex pairs (Kraken) · up to 6 Solana/Pump.fun pools (GeckoTerminal, liquidity ≥ $100k)
history  ─► 15m / 1h / 1d candles per market (5m for pools); missing bars are counted, never filled
calibration (start + every 6 h) ─► backtest every strategy × market class × timeframe × direction with the
                     paper broker's own execution rules ─► evidence tiers (selector)
live prices ─► Coinbase WebSocket · Kraken ticker every ~10 s · DEX pools every ~30 s when an order is live
             ─► desk.on_quote ─► broker fills limit entries, stops, take-profits, trailing stops, time exits
candle close ─► analyse (structure, RSI, MACD, Bollinger, ATR, volume, liquidity, FVG, OB, BOS/CHoCH)
             ─► desk.on_candle: HOLD or SELL/COVER each open position (strategy exit rules), then evaluate
                every strategy ─► gates ─► simulated order ─► fill ─► position
closed position ─► trade report ─► live evidence updated ─► tiers may change (with minimum samples)
UTC midnight ─► daily report (+ weekly on Mondays, monthly on the 1st)
```

## Decision gates (each logged with every decision)

1. Closed candle (forming candles are never traded).
2. Tradeable market: volume, liquidity, spread and data quality.
3. **Strategy evidence tier** (`app/quant/selector.py`):
   - **active:** backtest 90% CI of the mean net return per trade is entirely above zero (n ≥ 30) and the recent half is positive. Trades at normal risk (0.5% of equity).
   - **exploration:** unproven, with a CI that includes zero, n ≥ 10 and a recent half that is not negative. Trades autonomously at small risk (0.1%) to gather live evidence.
   - **disabled:** the CI is below zero, the recent half is negative, or there are too few samples. Never traded.
4. A viable plan:
   - target strategies: a stop and the smallest realistic target from the ladder (0.75–5%) with reward/risk after costs ≥ 1.2
   - trend strategy: a 3×ATR trailing stop plus an EMA20 exit
5. A live quote is available.
6. Risk and sizing (`app/paper/risk.py`):
   - risk per trade includes fees and slippage
   - caps on position size, gross exposure and number of positions
   - daily loss limit and maximum drawdown (both suspend new entries only)
   - correlation limit, re-entry cooldown, entries per hour and per day
   - duplicate prevention: one position per asset, plus unique order keys

## Paper broker (`app/paper/broker.py`)

Market, limit and stop orders. Longs buy at the ask and sell at the bid. Limit orders fill only when the market trades through, at the limit price, with a maker fee. Stops fill at the worse of the stop and the current price, with a taker fee and slippage.

Every fill updates the cash ledger: shorts credit their proceeds, and equity = cash + longs − shorts. Positions, orders, fills, equity snapshots and reports are all persisted. Each closed trade is reconciled exactly: net = gross − fees, and the account's cash change equals the sum of net results.

## Self-improvement

- Live paper results are tracked separately from backtests.
- **Demotion:** a combination is demoted after at least 10 live trades with a 90% CI upper bound below zero.
- **Promotion:** a combination is promoted to active after at least 20 live trades with a lower bound above zero.
- Every tier change is stored with its evidence and shown on the Model Performance page.
- **Testing the selection rule itself:** it is walk-forward validated. Tiers are decided using earlier folds only, then measured on the next fold.

## Historical replay (`app/trading/replay.py`)

Replay runs the same desk and broker over real history with a simulated clock:
- It calibrates on the first 60% of the history, then replays the rest candle by candle.
- Each candle is fed as a price path in adverse order (low first on green candles, high first on red). The path is interpolated when an order is live, so stops are not mistaken for gaps.
- Results go into their own `replay-<n>` account, separate from live trading.

## Results so far (real data, 25 Nov 2026)

| Run | Market move (buy & hold) | Desk result |
|---|---|---|
| Replay, 11 assets, 1h, last ~40% of 100 days | −15% to −35% | **−2.2%**: 22 trades, 2 wins / 20 losses; worst trade −0.13% of equity |
| Walk-forward test of the selection rule | — | selected setups −1.07%/trade out of sample vs −0.55% for all setups |

The machinery works end to end: decisions, orders, fills, exits, reports and learning.

**Profitability is not demonstrated.** No strategy on these free data sources has shown a statistically significant positive edge after realistic retail costs. The desk therefore trades only small exploration positions, and it demotes combinations when live results are negative. These are the honest numbers.

## Not available

- **US and international stocks:** need a market-data key, which was declined.
- **Exchange-traded indices:** FRED data is daily and delayed, so indices are display-only.
- **Social-media sentiment:** needs approved API access.
- **Holder concentration for memecoins:** needs a Solana RPC key.
- **Pairs trading:** research only, because two-leg positions are not simulated.
