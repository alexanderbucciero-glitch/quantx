# QUANTX — Technical Architecture (Stage 0 design)

**Decision (Nov 2026): personal research only, free data sources only.** Tier A paid feeds are replaced by FRED, SEC EDGAR, Coinbase/Kraken/CoinGecko, DexScreener, Fed/ECB RSS and GDELT, plus an optional free Tiingo key for stock prices. See README.md for what is live.

Status (original design): Nothing below is implemented yet, and no integration is live.
Prices were checked in November 2026 and move often. Confirm each one on the vendor's own pricing page before you sign up.

---

## 0. Guiding principles (these override everything else)

| # | Principle | Concrete mechanism |
|---|-----------|--------------------|
| P1 | **Point-in-time correctness** | Every fact has an `available_at` timestamp. Feature and backtest queries take an `as_of` argument and use only rows where `available_at <= as_of`. A CI test seeds "future" rows and checks they never leak. |
| P2 | **No fabricated data** | Missing observations go into `ops.data_gap`. There is no forward-fill in storage. Any fill done in a feature pipeline is explicit and logged. The UI shows "no data", never a placeholder number. |
| P3 | **Survivorship-bias control** | A permanent `instrument_id`, delisted securities kept in the master, and time-ranged tickers and index membership. Universes are rebuilt for each historical date. |
| P4 | **Probability ≠ confidence ≠ profit** | A forecast stores the calibrated P(up), the expected return *after costs*, the sample size and the calibration error as separate fields. A signal needs an edge after costs, not just a high probability. |
| P5 | **Honest "I don't know"** | `insufficient_data=true` when the sample size or calibration falls below a threshold. Memecoins under N comparable samples get no probability at all. |
| P6 | **Two market families** | `traditional` and `crypto` get separate feature sets, models, trading calendars, cost models and risk limits. |
| P7 | **No real money** | No brokerage or wallet keys ever enter the system. The paper-trading engine has no order-routing code path. |

---

## 1. System architecture

```
                         ┌───────────────────────────────────────────────┐
                         │   Next.js 15 / React / TS / Tailwind (web)    │
                         │   TradingView Lightweight Charts              │
                         └──────────────┬──────────────▲─────────────────┘
                                        │ REST/JSON     │ WebSocket (live updates)
                         ┌──────────────▼──────────────┴─────────────────┐
                         │  api-gateway  (FastAPI, auth, read models)    │
                         └──────────────┬────────────────────────────────┘
                                        │ reads
  ┌─────────────────────────────────────▼────────────────────────────────────────┐
  │        PostgreSQL 16 + TimescaleDB   (system of record, point-in-time)       │
  │        Object store (S3/MinIO): raw payload archive (Parquet/JSONL), models  │
  └───▲──────────▲──────────▲───────────▲──────────▲──────────▲──────────▲───────┘
      │          │          │           │          │          │          │
 ┌────┴────┐┌────┴────┐┌────┴─────┐┌────┴─────┐┌───┴────┐┌────┴────┐┌────┴─────┐
 │ingest-  ││ingest-  ││ingest-   ││nlp-      ││quant-  ││backtest ││paper-    │
 │market   ││news     ││social    ││events    ││engine  ││lab      ││trader    │
 │(eq+cry) ││(wires,  ││(X,Reddit,││(NER, sent││features││(batch)  ││(live sim)│
 │         ││SEC,CBs) ││YouTube)  ││ dedup,   ││models  ││         ││          │
 │ingest-  ││         ││          ││ classify)││forecast││         ││          │
 │chain    ││         ││          ││          ││        ││         ││          │
 └────┬────┘└────┬────┘└────┬─────┘└──▲───┬───┘└──▲──┬──┘└─────────┘└────▲─────┘
      │          │          │         │   │       │  │                    │
      └──────────┴──────────┴─────────┴───▼───────┴──▼────────────────────┴───
                     Redis Streams  (event bus: bars.*, news.raw, social.raw,
                     events.classified, forecasts.new, signals.new)
                     Redis also: rate-limit token buckets, cache, locks
      ───────────────────────────────────────────────────────────────────────
                     Dagster (orchestrator): scheduled backfills, nightly
                     retraining, outcome resolution, data-quality checks
                     MLflow: experiment tracking + model registry
                     Prometheus + Grafana + Loki: metrics, dashboards, logs
```

### Services (each one is an independent Python package and container)

| Service | Responsibility | Runs as |
|---|---|---|
| `ingest-market` | Equity, ETF, index and FX bars, corporate actions, fundamentals, earnings, macro (FRED/ALFRED) | Dagster jobs (history/EOD) + async stream workers (intraday) |
| `ingest-chain` | CEX OHLCV, Solana DEX pools, token launches, holder snapshots | Stream workers (WebSocket/RPC subscriptions) |
| `ingest-news` | Newswire APIs, SEC EDGAR, central-bank and government RSS/APIs, IR pages (where the terms allow) | Pollers + push consumers |
| `ingest-social` | X, Reddit, YouTube (only sources with licensed API access) | Pollers within quota budgets |
| `nlp-events` | Dedup (hash + SimHash), entity linking to `instrument_id`, sentiment (FinBERT-class local model), event classification, verification state machine, bot/coordination scoring | Stream consumers, GPU optional |
| `quant-engine` | Point-in-time feature store, model training (walk-forward), calibration, forecast generation, event studies, regime detection | Dagster (train) + workers (infer) |
| `backtest-lab` | Event-driven simulator shared with paper-trader. Cost, slippage and impact models. Metrics. | Job queue (user-submitted runs) |
| `paper-trader` | Consumes signals, applies risk rules, simulates fills on live prices, writes an append-only audit log | Long-running worker |
| `memecoin-intel` | Launch-condition features, similarity search (k-NN over launch vectors), risk flags | Stream consumer + batch |
| `api-gateway` | Auth, REST and WebSocket endpoints, read-optimised queries for the 15 pages | FastAPI (uvicorn) |
| `web` | UI | Next.js |

**Why Redis Streams + Dagster rather than Kafka + Airflow?** At this scale (one researcher, some thousands of messages per minute), Redis Streams gives durable consumer groups with almost no operating overhead. Dagster's asset model fits "this dataset depends on that one" lineage, and its backfill tooling is good. The bus sits behind an interface, so Kafka/Redpanda can replace it later without rewriting the services.

### The point-in-time contract (the most important design decision)

```python
# Every feature reader in quant-engine has this signature:
def get_features(instrument_ids, as_of: datetime, feature_set: str) -> pd.DataFrame: ...
# and is implemented with  WHERE available_at <= :as_of  (plus vintage selection for macro).
```

* `available_at` for a daily bar = session close + the provider's publication lag, not midnight.
* Macro uses ALFRED vintages. The CPI value a backtest sees for 2008-03 is the one published at that time, not today's revised number.
* Fundamentals are keyed on `filed_at`, so restatements never leak backwards.
* News and social use `first_seen_at`, meaning when we received the item. For archived history from a vendor we use the vendor's documented timestamp and flag `archival=true`. Results built on archival timestamps carry a warning in the UI.
* The backtest engine and the paper trader call **the same** `get_features` and `decide()` code. The only difference is the clock source.

---

## 2. Database schema

The full runnable DDL is in [`db/schema.sql`](../db/schema.sql). It has been tested and loads cleanly on PostgreSQL 16. TimescaleDB calls are guarded and enable themselves when the extension is present.

| Schema | Key tables | Notes |
|---|---|---|
| `ref` | `provider`, `dataset`, `exchange`, `instrument`, `symbol_history`, `provider_symbol`, `index_membership` | Permanent ids, time-ranged tickers, licensing flags per provider |
| `mkt` | `bar` (hypertable, **unadjusted**), `corporate_action`, `fundamental`, `earnings_event`, `market_cap` | Adjusted prices are computed from actions as-of a date. Vendor-adjusted close is kept for reconciliation. |
| `macro` | `series`, `observation` (with `vintage_date`), `release_calendar` | Consensus vs actual for macro surprise |
| `intel` | `source`, `news_item`, `social_post`, `author_profile`, `mention`, `social_metric`, `event`, `event_instrument`, `event_reaction` | Social author ids are HMAC-pseudonymised. Events carry a verification state machine and surprise/priced-in fields. |
| `chain` | `token_launch`, `token_snapshot`, `risk_flag` | Memecoin engine |
| `ml` | `model_version`, `forecast`, `forecast_outcome`, `signal` | Forecast fields map 1:1 to the brief's §3 list. `gates_passed` records why a signal fired. |
| `sim` | `portfolio`, `backtest_run`, `order_fill`, `equity_curve` | Shared by backtest and paper |
| `ops` | `ingestion_run`, `data_gap`, `decision_audit` | Audit log is append-only (enforced by trigger, tested) and hash-chained |

Storage estimate: US equities 1-minute bars since ~2003 for about 10k symbols comes to roughly 1–2 TB raw and around 10× less with Timescale compression. Daily bars for 50k instruments over 50 years is well under 50 GB. Raw API payloads go to object storage so any table can be rebuilt.

---

## 3. Data provider recommendations

Legend: ✅ public, documented API · 🔑 needs a paid plan or key · 📝 needs a contract or approval · ⚠️ caveat

### 3.1 Historical equities (the 50-year target)

There is no single affordable source with 50 years of survivorship-free global data. A realistic picture:

| Provider | Coverage | Delisted? | Use in QUANTX |
|---|---|---|---|
| **CRSP (via WRDS)** | US stocks from 1925, the academic gold standard | Yes | 📝 Academic/institutional licence only. It is the only real route to clean US data before 1990. Worth it if you have a university affiliation. |
| **Nasdaq Data Link – Sharadar (SEP/SF1/ACTIONS)** | US prices back to ~1998, fundamentals, actions | Yes (survivorship-free) | 🔑 **Primary US daily source.** Personal pricing is cheap (third-party reports ~$9–39/mo per table bundle). Professional use is quoted. |
| **Norgate Data** | US/AU/CA daily with delisted securities, decades deep | Yes | 🔑 Strong alternative/complement for US history before 1998. It is a desktop-oriented product, so check whether its terms allow server-side use. |
| **EODHD** | 70+ exchanges incl. Europe, EOD + intraday + fundamentals | Yes (`delisted=1`) | 🔑 **Primary global/European source.** All-In-One is listed at ~$99.99/mo (promotions vary). EODHD itself warns that the stated history depth is not guaranteed for every ticker, so gaps must be recorded. |
| **Massive (formerly Polygon.io)** | US stocks: tick/minute (~2003+), real-time WebSocket | Yes | 🔑 **Primary US intraday + real-time.** Individual plans ~$29–199/mo per asset class. Commercial or redistribution use requires business pricing. |
| Alpha Vantage | Broad but thin | Partial | Free tier is too rate-limited for a platform (about 25 calls/day). Useful only for cross-checks. |
| Intrinio | US fundamentals/prices | Yes | Good quality but priced for businesses (typically thousands per year). Optional. |

**Recommendation:** Sharadar (US daily, delisted) + EODHD (Europe/global, delisted) + Massive (US intraday/real-time). Add CRSP only if WRDS access is available. Reconcile overlapping vendors nightly and flag disagreements with `quality_flags`. Never average two vendors' prices.

### 3.2 Macro / rates / FX

| Source | Notes |
|---|---|
| **FRED + ALFRED** (St. Louis Fed) ✅ free | Vintage history, essential for P1. API key required, free. |
| BLS, BEA, US Treasury ✅ free | Employment, GDP, yields. Official release timestamps. |
| ECB Data Portal, Eurostat, Bank of England ✅ free | European macro and rates |
| Economic calendar with consensus 🔑 | Free consensus data is rare. Options: Trading Economics API (paid), or news-vendor calendars. Without consensus, the "macro surprise" feature is disabled rather than guessed. |

### 3.3 Crypto

| Source | Notes |
|---|---|
| Binance / Coinbase / Kraken public market-data APIs ✅ | Free OHLCV and WebSockets. Check regional access restrictions (e.g. Binance.com vs Binance.US). |
| CoinGecko API 🔑 | Market caps, listings, broad coverage. Free demo tier, paid tiers for volume. |
| Kaiko / CoinAPI / Tardis.dev 🔑 | Institutional historical tick and order-book data. Only needed for intraday crypto research. |

### 3.4 Solana / memecoins / Pump.fun

| Source | Notes |
|---|---|
| **Helius** (Solana RPC, webhooks, enhanced transactions) 🔑 | Free developer tier, paid for volume. The core source for launches and holder data via on-chain decoding. |
| **Birdeye** 🔑 | Token prices, liquidity, OHLCV. Billed in compute units (a small free tier). Paid tiers are not reliably published, so confirm on their site. Pay-per-request via x402 was added in 2026. |
| DexScreener public API ✅ | Pair and price data, rate-limited. Good for cross-checks. |
| **Pump.fun** ⚠️ | I have not found an officially documented, supported public API with terms for third-party use. **The recommended approach is to read Pump.fun program activity on-chain** (public blockchain data via Helius/RPC) rather than scrape their website or use undocumented endpoints. This will be confirmed before Stage 8. |

### 3.5 News

| Source | Access |
|---|---|
| SEC EDGAR ✅ free | Full-text and submissions APIs. Limited to 10 requests/s and requires a declared User-Agent with contact info. **The primary-source verifier for US company events.** |
| Fed, ECB, BoE, government press releases ✅ | RSS/official pages. Primary sources for macro events. |
| Company IR pages / press-release wires | Business Wire, PR Newswire and GlobeNewswire feeds are mostly 🔑/📝. RSS where offered. Scraping only where the terms allow. |
| Benzinga News API 🔑 | Affordable real-time financial headlines with tickers. A good first paid feed. |
| **Reuters (LSEG)**, **AP**, **Dow Jones/Factiva** 📝 | Enterprise contracts, typically five to six figures per year. |
| **Bloomberg** 📝 | Requires a Terminal/B-PIPE/Data License contract. No self-serve API. |
| **Financial Times** 📝 | Syndication/licensing agreement only. |
| CNBC ⚠️ | No public data API. RSS headlines exist. Check the terms before storing anything. |
| GDELT ✅ free | Global news event database. Useful for macro/geopolitical signals and historical backtests. |
| Crypto news (CoinDesk/CryptoPanic APIs) 🔑 | Affordable crypto headline feeds |

**Historical news for backtests:** live feeds don't give you the past. Archives with reliable timestamps (Benzinga history, Dow Jones/RavenPack, GDELT, EDGAR's own timestamps) are needed. Without them, event strategies are only backtested on EDGAR filings and macro releases, and the UI says so.

### 3.6 Social media

| Platform | Status (Nov 2026) |
|---|---|
| **X** 🔑 | New developers get **pay-per-use credits** (third-party sources cite ~$0.005 per post read), capped at 3M post reads per month. Above that the Enterprise tier is custom-priced, reportedly very expensive. Legacy Basic/Pro subscriptions are closed to new sign-ups. Plan: tracked-cashtag queries only, with a hard daily spend cap enforced in Redis. |
| **Reddit** 📝 | Free access is for **non-commercial** use (100 queries/min per OAuth client), and new access requests require approval. Commercial use needs a contract with Reddit, with no public rate card. Reports say new free access requests may be closing, so apply early. |
| **YouTube Data API v3** ✅ | Free with a daily quota (10,000 units by default). Gives metadata, titles, descriptions and comments. Transcripts are not part of the official API. |
| **StockTwits** ⚠️ | The public API has been closed to new registrations in recent years. Treat it as unavailable unless they approve a partnership. |
| Telegram / Discord ⚠️ | Only channels or servers whose terms permit it, via official bot APIs. Deferred. |

---

## 4. Estimated monthly API costs

| Tier | What you get | Approx. monthly |
|---|---|---|
| **A — Research MVP** (recommended start) | Sharadar personal, EODHD All-In-One, Massive Stocks Starter/Developer, FRED/EDGAR/central banks (free), exchange crypto APIs (free), Helius + Birdeye entry tiers, YouTube (free), Reddit non-commercial, X pay-per-use capped at ~$100 | **~$350–600** |
| **B — Serious research** | A + Massive Advanced (real-time), Benzinga news, CoinGecko paid, higher Helius/Birdeye tiers, X at ~$500–1,500, crypto tick history (Tardis) | **~$1,500–4,000** |
| **C — Institutional** | Reuters/Dow Jones or Bloomberg, X Enterprise, Reddit commercial, CRSP, Kaiko, professional exchange fees | **$20,000+** (contracts) |

Infrastructure: one VPS/cloud VM (8 vCPU, 32 GB RAM, 2 TB SSD) runs everything for Tier A at about $80–250/mo. A GPU is optional. FinBERT-class sentiment runs on CPU at moderate volume.

Optional LLM-based event extraction (e.g. the Claude API) is pay-per-token. Budget it explicitly, and use it only on de-duplicated, high-importance items.

---

## 5. Data licensing requirements

1. **Personal vs professional.** Most cheap plans (Massive individual, Sharadar personal, EODHD standard) cover one non-professional user only. If QUANTX ever serves other people, the data needs **redistribution/display licences**, which typically cost 5–50× more. `ref.provider.redistribution` enforces this: data without that flag is never served to non-owner accounts.
2. **Exchange fees.** Real-time US equity data carries NYSE/Nasdaq/CTA/UTP fees. Non-professional status is a declaration you make, and it lapses if the use becomes commercial. Delayed (15-minute) data is far cheaper.
3. **News content.** Store headlines, links and short excerpts only, unless the licence allows full text. Licensed full text must not be shown to unlicensed users.
4. **Social data.** Follow each platform's developer agreement (display rules, deletion compliance: posts deleted on X must be deleted from our store too, a ban on re-selling data). Under **GDPR**, social posts are personal data. We minimise (pseudonymous author hashes, excerpts), set retention limits (e.g. raw posts for 90 days, aggregates kept), and keep a record of processing.
5. **Scraping.** Only where `robots.txt` and the site's terms allow it. Never get around logins, paywalls or rate limits. Pump.fun's website and CNBC are not scraped.
6. **Academic data (CRSP).** It cannot be used for commercial purposes and cannot be shared outside the licensee.
7. **TradingView.** *Lightweight Charts* is open source (Apache 2.0, attribution required) and is the default. The *Advanced Charts* library is free but needs an application, approval and a public-facing site. Our own datafeed adapter is required either way. TradingView does not supply data to these libraries.

---

## 6. Development roadmap (each stage has an exit gate)

| Stage | Scope | Exit criteria (must pass before the next stage) |
|---|---|---|
| **1** Architecture, DB, frontend | Monorepo, docker-compose (Postgres+Timescale, Redis, MinIO, MLflow, Grafana), schema migrations (Alembic), FastAPI skeleton with auth, Next.js shell with all 15 pages showing **empty states** (no fake data), design system, chart component | `docker compose up` works. CI passes. Point-in-time leakage test harness exists. |
| **2** Historical data | Sharadar + EODHD + FRED/ALFRED + EDGAR + exchange crypto ingestion. Security master, symbol mapping, corporate actions, adjustment engine, gap detection, vendor reconciliation | Data-quality report per dataset. Delisted names present. Adjusted prices match the vendor's to within a tolerance. |
| **3** Quant engine | Feature store (technicals, volatility, regimes via HMM/GARCH, correlations, macro), logistic regression / RF / XGBoost / GBM baselines, walk-forward training, isotonic/Platt calibration, forecast API with every §3 field | Models are compared against naive baselines (always-up, momentum) out-of-sample. Reliability diagrams are published. A model that doesn't beat the baseline isn't promoted. |
| **4** Backtesting | Event-driven simulator, cost/slippage/borrow/impact models, walk-forward and combinatorial purged CV, regime breakdown, benchmark comparison, all metrics | Golden-test strategies (buy and hold, SMA cross) reproduce known results. The leakage test fails if look-ahead is introduced on purpose. |
| **5** Social intelligence | X/Reddit/YouTube collectors with quota guards, entity linking, sentiment, baselines/z-scores, bot and coordination detection | Sentiment model is benchmarked on a labelled financial set. Spike detector precision is measured. |
| **6** News & events | Collectors, dedup/clustering, event taxonomy classifier, verification against primary sources (EDGAR, central banks), impact scoring | Event classifier F1 on a held-out labelled set. Verification state is shown for every event. |
| **7** Event-driven models | Event studies (abnormal returns vs market/sector), expectation surprise (actual vs consensus), priced-in estimation, signal gating | Historical event backtests use only point-in-time news. Results are reported with confidence intervals. |
| **8** Memecoins | Launch decoding, snapshots, similarity engine, risk flags, "insufficient samples" logic | Every token page shows sample counts. No probability appears below the threshold. |
| **9** Paper trading | Live loop using the backtest engine, risk manager, hash-chained audit log, outcome resolver | Run for at least 30 days. Every decision can be traced end to end. |
| **10** Monitoring & optimisation | Live calibration tracking, drift detection, model champion/challenger, alerting, performance tuning | Model Performance page shows live Brier/ECE/hit rate vs backtest expectations. |

### Signal gating (applies from Stage 3 on, refined in Stage 7)

A BUY/SHORT signal fires only if **all** of these hold. Each one is recorded in `ml.signal.gates_passed`:

1. Calibrated probability edge: `p_up ≥ 0.5 + δ` (or `p_down`), where δ comes from the out-of-sample calibration.
2. Expected return after fees, slippage, spread and borrow cost > 0 with margin.
3. `sample_size ≥ N_min` and the calibration error for that bucket ≤ threshold.
4. At least 2 independent supporting evidence families agree (e.g. quant model + event surprise, or quant + sentiment), and none contradict strongly.
5. Liquidity: position ≤ x% of average daily volume. Spread under the limit.
6. The regime is not one where the model historically fails.
7. Portfolio risk limits leave room (gross/net exposure, per-asset, per-sector, crypto sleeve cap).

If any gate fails, the result is NO_TRADE, and the reason is logged.

---

## 7. Project folder structure

```
quantx/
├── docs/                      # this document, ADRs, data dictionary, provider notes
├── db/
│   ├── schema.sql             # canonical DDL (Stage 0)
│   └── migrations/            # Alembic migrations (Stage 1+)
├── infra/
│   ├── docker-compose.yml     # postgres+timescale, redis, minio, mlflow, grafana, prometheus, loki
│   ├── grafana/ prometheus/
│   └── k8s/                   # later, optional
├── packages/                  # shared Python libraries (uv workspace)
│   ├── qx_core/               # config, logging, db session, time/calendars, ids
│   ├── qx_pit/                # point-in-time query layer + leakage test utilities
│   ├── qx_bus/                # Redis Streams abstraction, message schemas (pydantic)
│   ├── qx_ratelimit/          # per-provider token buckets & spend caps
│   └── qx_sim/                # order/fill/cost models shared by backtest & paper
├── services/
│   ├── api_gateway/           # FastAPI app: routers per page, websockets, auth
│   ├── ingest_market/         # providers/{sharadar,eodhd,massive,fred,edgar}.py
│   ├── ingest_chain/          # providers/{binance,coinbase,helius,birdeye,dexscreener}.py
│   ├── ingest_news/
│   ├── ingest_social/
│   ├── nlp_events/
│   ├── quant_engine/          # features/, models/, calibration/, regimes/, eventstudy/
│   ├── backtest_lab/
│   ├── paper_trader/
│   └── memecoin_intel/
├── orchestration/             # Dagster definitions (assets, schedules, sensors)
├── web/                       # Next.js app
│   ├── app/(terminal)/        # dashboard, markets, stocks, crypto, memecoins, social, news,
│   │                          # predictions, events, signals, backtest, portfolio, history,
│   │                          # models, settings
│   ├── components/            # data-grid, chart (lightweight-charts), panels, tickers
│   └── lib/                   # typed API client generated from OpenAPI
├── tests/                     # cross-service integration + leakage tests
├── .env.example               # names only, no values
└── pyproject.toml             # uv workspace root
```

Each provider adapter implements one interface (`fetch_history`, `stream`, `health`). An adapter is "live" in the Settings page only after a real health check with a real key has succeeded. Otherwise it shows *Not configured*.

---

## 8. Security considerations

* **Secrets:** API keys live in environment variables injected from a secret manager (Docker secrets / Doppler / cloud KMS), never in git. `.env.example` lists names only. The Settings page shows whether a key is present, never its value.
* **No trading credentials:** by design there is no field, table or code path for broker or wallet keys. This is also checked as a lint rule in CI.
* **Auth:** the gateway uses OIDC or passkeys plus short-lived JWTs. Role-based access (owner / viewer). Licence-restricted datasets are hidden from viewers (§5.1).
* **Network:** only the gateway is exposed (behind TLS reverse proxy + WAF rate limits). DB, Redis and MinIO stay on a private network. Separate DB roles: ingestion writers, read-only gateway, append-only audit writer.
* **Untrusted input:** news and social text is hostile input. It is sanitised before rendering (no raw HTML) and never executed. Any text sent to an LLM is wrapped as data, outputs are checked against a schema, and a post can never trigger an action by itself ("prompt injection via tweets" is a real risk).
* **Supply chain:** pinned, hashed dependencies (uv lock, pnpm lockfile), Dependabot, container image scanning, SBOM.
* **Data integrity:** hash-chained, append-only decision audit. Raw payload archive with checksums. Backups (pgBackRest) with tested restores.
* **Provider etiquette:** central rate limiting and spend caps per provider. EDGAR User-Agent compliance. Handling of platform deletion requests.
* **Privacy:** GDPR minimisation for social data, retention jobs, and no profiling of private individuals beyond aggregate bot/credibility scores.
* **Observability:** structured JSON logs (no secrets or PII), metrics per ingestion job, alerting on stale data. The UI shows a "data stale" banner rather than old numbers presented as live.

---

## 9. Decisions needed from you before Stage 1

1. **Personal research only, or will others use it?** This decides the licence tier and is the biggest cost driver.
2. **Monthly data budget:** Tier A (~$350–600) is assumed unless you say otherwise.
3. **WRDS/CRSP access** (university affiliation)? It is the only realistic route to clean US data from 1975–1997 with delisted securities.
4. **Hosting:** a single cloud VM (recommended to start) vs managed services.

## Sources consulted for pricing (Nov 2026)
- X API pricing: https://docs.x.com/x-api/getting-started/pricing
- Massive/Polygon plans (third-party summaries): https://apicostcalc.com/blog/polygon-massive-rebrand-api-pricing.html
- EODHD plans & delisted data: https://eodhd.com/pricing , https://eodhd.com/financial-apis/delisted-stock-companies-data-2
- Sharadar (third-party): https://quantrocket.com/pricing/data/sharadar , https://marketparquet.com/vs/quandl
- Birdeye: https://docs.birdeye.so/docs/data-accessibility-by-packages , https://docs.birdeye.so/changelog/20260408-release-x402-payment-support
- Reddit API (third-party summaries): https://data365.co/blog/reddit-api-pricing , https://techcrunch.com/2024/05/09/reddit-locks-down-its-public-data-in-new-content-policy-says-use-now-requires-a-contract
