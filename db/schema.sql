-- =====================================================================
-- QUANTX — core database schema (PostgreSQL 16 + TimescaleDB)
-- =====================================================================
-- Design principles
--   1. Point-in-time correctness: every fact that a model or backtest can
--      read carries `available_at` = the moment QUANTX (or the market)
--      could genuinely have known it. Backtests filter on available_at.
--   2. Never fabricate: missing observations are recorded as gaps in
--      `data_gap`, never interpolated into the price tables.
--   3. Stable identity: instruments have a permanent internal id; tickers
--      are time-ranged attributes (tickers get reused after delistings).
--   4. Provenance: every row points at the provider/dataset it came from.
--   5. Equities and crypto live in the same security master but are
--      modelled by separate model families (see model_registry.market).
-- =====================================================================

CREATE SCHEMA IF NOT EXISTS ref;      -- reference / security master
CREATE SCHEMA IF NOT EXISTS mkt;      -- market data (time series)
CREATE SCHEMA IF NOT EXISTS macro;    -- economic data with vintages
CREATE SCHEMA IF NOT EXISTS intel;    -- news, social, events
CREATE SCHEMA IF NOT EXISTS chain;    -- on-chain / memecoin
CREATE SCHEMA IF NOT EXISTS ml;       -- features, models, forecasts
CREATE SCHEMA IF NOT EXISTS sim;      -- backtests & paper trading
CREATE SCHEMA IF NOT EXISTS ops;      -- ingestion runs, data quality, audit

-- ---------------------------------------------------------------------
-- Enumerations
-- ---------------------------------------------------------------------
CREATE TYPE ref.asset_class   AS ENUM ('equity','etf','index','fund','fx','crypto','token','rate','commodity');
CREATE TYPE ref.market_family AS ENUM ('traditional','crypto');
CREATE TYPE ref.listing_status AS ENUM ('active','delisted','bankrupt','merged','suspended','unknown');
CREATE TYPE mkt.bar_interval  AS ENUM ('1m','5m','15m','1h','4h','1d','1w','1mo');
CREATE TYPE ml.signal_action  AS ENUM ('BUY_LONG','SELL_EXIT','SHORT','COVER','HOLD','NO_TRADE');
CREATE TYPE intel.verification_status AS ENUM ('unverified','single_source','multi_source','primary_source_confirmed','contradicted','retracted');
CREATE TYPE intel.polarity    AS ENUM ('positive','negative','neutral','mixed','unknown');

-- ---------------------------------------------------------------------
-- Providers & datasets (provenance + licensing)
-- ---------------------------------------------------------------------
CREATE TABLE ref.provider (
    provider_id      SMALLSERIAL PRIMARY KEY,
    name             TEXT NOT NULL UNIQUE,            -- 'sharadar','eodhd','massive','fred','sec_edgar',...
    license_tier     TEXT,                            -- 'personal','professional','academic','public_domain'
    redistribution   BOOLEAN NOT NULL DEFAULT FALSE,  -- may the data be shown to other users?
    terms_url        TEXT,
    notes            TEXT
);

CREATE TABLE ref.dataset (
    dataset_id       SERIAL PRIMARY KEY,
    provider_id      SMALLINT NOT NULL REFERENCES ref.provider,
    code             TEXT NOT NULL,                   -- e.g. 'SHARADAR/SEP'
    description      TEXT,
    is_point_in_time BOOLEAN NOT NULL DEFAULT FALSE,  -- does provider supply as-known-at history?
    UNIQUE (provider_id, code)
);

-- ---------------------------------------------------------------------
-- Security master
-- ---------------------------------------------------------------------
CREATE TABLE ref.exchange (
    exchange_id   SMALLSERIAL PRIMARY KEY,
    mic           TEXT UNIQUE,                        -- ISO 10383 MIC; NULL for DEX/CEX
    name          TEXT NOT NULL,
    country       CHAR(2),
    timezone      TEXT NOT NULL,                      -- IANA tz
    is_crypto     BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE ref.instrument (
    instrument_id   BIGSERIAL PRIMARY KEY,            -- permanent internal id
    asset_class     ref.asset_class NOT NULL,
    market_family   ref.market_family NOT NULL,
    name            TEXT,
    isin            TEXT,
    figi            TEXT,
    cik             TEXT,                             -- SEC CIK for US issuers
    chain           TEXT,                             -- 'solana','ethereum',... for tokens
    contract_address TEXT,                            -- mint / contract address
    currency        CHAR(3),
    sector          TEXT,
    industry        TEXT,
    first_trade_date DATE,
    last_trade_date  DATE,                            -- NOT NULL once delisted
    status          ref.listing_status NOT NULL DEFAULT 'unknown',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (chain, contract_address)
);
CREATE INDEX ON ref.instrument (isin);
CREATE INDEX ON ref.instrument (cik);

-- Tickers are time-ranged (e.g. FB -> META; reused tickers after delisting)
CREATE TABLE ref.symbol_history (
    instrument_id  BIGINT NOT NULL REFERENCES ref.instrument,
    exchange_id    SMALLINT REFERENCES ref.exchange,
    symbol         TEXT NOT NULL,
    valid_from     DATE NOT NULL,
    valid_to       DATE,                              -- NULL = current
    provider_id    SMALLINT REFERENCES ref.provider,
    PRIMARY KEY (instrument_id, symbol, valid_from)
);
CREATE INDEX ON ref.symbol_history (symbol, valid_from);

-- Provider-specific identifiers mapped to the internal id
CREATE TABLE ref.provider_symbol (
    provider_id    SMALLINT NOT NULL REFERENCES ref.provider,
    provider_key   TEXT NOT NULL,                     -- e.g. 'AAPL.US', permaticker
    instrument_id  BIGINT NOT NULL REFERENCES ref.instrument,
    PRIMARY KEY (provider_id, provider_key)
);

-- Point-in-time index membership (survivorship-bias control for universes)
CREATE TABLE ref.index_membership (
    index_instrument_id BIGINT NOT NULL REFERENCES ref.instrument,
    member_instrument_id BIGINT NOT NULL REFERENCES ref.instrument,
    valid_from  DATE NOT NULL,
    valid_to    DATE,
    PRIMARY KEY (index_instrument_id, member_instrument_id, valid_from)
);

-- ---------------------------------------------------------------------
-- Market data (Timescale hypertables)
-- ---------------------------------------------------------------------
-- Raw, UNADJUSTED bars. Adjustments are derived from mkt.corporate_action
-- so that adjusted series can be recomputed as-of any date.
CREATE TABLE mkt.bar (
    instrument_id  BIGINT NOT NULL REFERENCES ref.instrument,
    interval       mkt.bar_interval NOT NULL,
    ts             TIMESTAMPTZ NOT NULL,              -- bar open time (UTC)
    open           NUMERIC(20,8),
    high           NUMERIC(20,8),
    low            NUMERIC(20,8),
    close          NUMERIC(20,8),
    volume         NUMERIC(28,8),
    vwap           NUMERIC(20,8),
    trade_count    BIGINT,
    adj_close_vendor NUMERIC(20,8),                  -- vendor-adjusted close, kept for reconciliation
    dataset_id     INT NOT NULL REFERENCES ref.dataset,
    available_at   TIMESTAMPTZ NOT NULL,              -- bar close + publication lag
    ingested_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    quality_flags  INT NOT NULL DEFAULT 0,            -- bitmask: 1=outlier,2=zero_vol,4=stale,8=vendor_disagree
    PRIMARY KEY (instrument_id, interval, ts),
    CHECK (high IS NULL OR low IS NULL OR high >= low)
);

CREATE TABLE mkt.corporate_action (
    action_id      BIGSERIAL,
    instrument_id  BIGINT NOT NULL REFERENCES ref.instrument,
    action_type    TEXT NOT NULL,                     -- 'split','cash_dividend','stock_dividend','spinoff','merger','delisting','symbol_change'
    ex_date        DATE NOT NULL,
    record_date    DATE,
    pay_date       DATE,
    announced_at   TIMESTAMPTZ,                       -- when it became public
    ratio          NUMERIC(20,10),                    -- split ratio (new/old)
    cash_amount    NUMERIC(20,8),
    currency       CHAR(3),
    related_instrument_id BIGINT REFERENCES ref.instrument,
    dataset_id     INT NOT NULL REFERENCES ref.dataset,
    available_at   TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (action_id)
);
CREATE INDEX ON mkt.corporate_action (instrument_id, ex_date);

-- Fundamentals stored as reported, with filing timestamp (no restatement leakage)
CREATE TABLE mkt.fundamental (
    instrument_id  BIGINT NOT NULL REFERENCES ref.instrument,
    period_end     DATE NOT NULL,
    period_type    TEXT NOT NULL,                     -- 'Q','FY','TTM'
    metric         TEXT NOT NULL,                     -- 'revenue','eps_diluted','shares_out',...
    value          NUMERIC(28,8),
    is_restated    BOOLEAN NOT NULL DEFAULT FALSE,
    filed_at       TIMESTAMPTZ NOT NULL,              -- = available_at
    source_ref     TEXT,                              -- SEC accession number etc.
    dataset_id     INT NOT NULL REFERENCES ref.dataset,
    PRIMARY KEY (instrument_id, period_end, period_type, metric, filed_at)
);

-- Earnings calendar & consensus expectations (needed for "surprise")
CREATE TABLE mkt.earnings_event (
    earnings_id    BIGSERIAL PRIMARY KEY,
    instrument_id  BIGINT NOT NULL REFERENCES ref.instrument,
    fiscal_period  TEXT NOT NULL,
    scheduled_at   TIMESTAMPTZ,
    reported_at    TIMESTAMPTZ,
    eps_actual     NUMERIC(20,6),
    eps_consensus  NUMERIC(20,6),                     -- consensus AS OF just before report
    revenue_actual NUMERIC(28,2),
    revenue_consensus NUMERIC(28,2),
    consensus_asof TIMESTAMPTZ,
    dataset_id     INT NOT NULL REFERENCES ref.dataset,
    available_at   TIMESTAMPTZ NOT NULL
);

-- Historical market cap / shares outstanding (daily)
CREATE TABLE mkt.market_cap (
    instrument_id  BIGINT NOT NULL REFERENCES ref.instrument,
    date           DATE NOT NULL,
    shares_outstanding NUMERIC(28,2),
    market_cap     NUMERIC(28,2),
    currency       CHAR(3),
    dataset_id     INT NOT NULL REFERENCES ref.dataset,
    available_at   TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (instrument_id, date)
);

-- ---------------------------------------------------------------------
-- Macro data WITH VINTAGES (ALFRED-style). A GDP number revised 3 times
-- has 4 rows; backtests read the vintage that existed at decision time.
-- ---------------------------------------------------------------------
CREATE TABLE macro.series (
    series_id      SERIAL PRIMARY KEY,
    code           TEXT NOT NULL UNIQUE,              -- e.g. 'FRED:CPIAUCSL'
    name           TEXT NOT NULL,
    country        CHAR(2),
    frequency      TEXT,
    units          TEXT,
    dataset_id     INT NOT NULL REFERENCES ref.dataset
);

CREATE TABLE macro.observation (
    series_id      INT NOT NULL REFERENCES macro.series,
    period_date    DATE NOT NULL,                     -- reference period
    vintage_date   DATE NOT NULL,                     -- realtime_start in ALFRED terms
    value          NUMERIC(28,8),                     -- NULL = provider reported missing
    available_at   TIMESTAMPTZ NOT NULL,              -- release timestamp
    PRIMARY KEY (series_id, period_date, vintage_date)
);

CREATE TABLE macro.release_calendar (
    release_id     BIGSERIAL PRIMARY KEY,
    series_id      INT REFERENCES macro.series,
    name           TEXT NOT NULL,                     -- 'US CPI','FOMC decision',...
    scheduled_at   TIMESTAMPTZ NOT NULL,
    consensus      NUMERIC(28,8),
    actual         NUMERIC(28,8),
    importance     SMALLINT                           -- 1..3
);

-- ---------------------------------------------------------------------
-- News, social, events
-- ---------------------------------------------------------------------
CREATE TABLE intel.source (
    source_id      SERIAL PRIMARY KEY,
    kind           TEXT NOT NULL,                     -- 'newswire','regulator','central_bank','ir_page','social','forum','video'
    name           TEXT NOT NULL,
    domain         TEXT,
    is_primary     BOOLEAN NOT NULL DEFAULT FALSE,    -- SEC, central banks, company IR
    base_credibility REAL NOT NULL DEFAULT 0.5,       -- 0..1, updated by track record
    provider_id    SMALLINT REFERENCES ref.provider,
    UNIQUE (kind, name)
);

CREATE TABLE intel.news_item (
    news_id        BIGSERIAL,
    source_id      INT NOT NULL REFERENCES intel.source,
    external_id    TEXT,
    url            TEXT,
    headline       TEXT NOT NULL,
    body_excerpt   TEXT,                              -- store only what the licence allows
    language       TEXT,
    published_at   TIMESTAMPTZ NOT NULL,              -- as stated by source
    first_seen_at  TIMESTAMPTZ NOT NULL,              -- when QUANTX received it (= available_at)
    content_hash   BYTEA NOT NULL,                    -- exact dedup
    simhash        BIGINT,                            -- near-duplicate detection
    cluster_id     BIGINT,                            -- story cluster
    PRIMARY KEY (news_id, first_seen_at),
    UNIQUE (source_id, external_id, first_seen_at)
);

-- Social posts: author ids are pseudonymised (GDPR data minimisation)
CREATE TABLE intel.social_post (
    post_id        BIGSERIAL,
    platform       TEXT NOT NULL,                     -- 'x','reddit','youtube','stocktwits'
    external_id    TEXT NOT NULL,
    author_hash    BYTEA NOT NULL,                    -- HMAC(platform_user_id, secret)
    author_followers INT,
    text_excerpt   TEXT,
    lang           TEXT,
    created_at     TIMESTAMPTZ NOT NULL,
    first_seen_at  TIMESTAMPTZ NOT NULL,
    engagement     JSONB,                             -- likes/replies/reposts at capture
    bot_score      REAL,                              -- 0..1
    PRIMARY KEY (post_id, first_seen_at),
    UNIQUE (platform, external_id, first_seen_at)
);

CREATE TABLE intel.author_profile (
    platform       TEXT NOT NULL,
    author_hash    BYTEA NOT NULL,
    first_seen_at  TIMESTAMPTZ NOT NULL,
    account_age_days INT,
    bot_score      REAL,
    credibility    REAL,                              -- learned from historical accuracy
    is_influential BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (platform, author_hash)
);

-- Entity links: which instruments a document mentions (+ NLP output)
CREATE TABLE intel.mention (
    doc_kind       TEXT NOT NULL,                     -- 'news','social'
    doc_id         BIGINT NOT NULL,
    instrument_id  BIGINT NOT NULL REFERENCES ref.instrument,
    first_seen_at  TIMESTAMPTZ NOT NULL,
    link_confidence REAL NOT NULL,
    sentiment      REAL,                              -- -1..1
    sentiment_model TEXT,
    PRIMARY KEY (doc_kind, doc_id, instrument_id, first_seen_at)
);

-- Aggregated social activity per instrument per bucket + baseline z-scores
CREATE TABLE intel.social_metric (
    instrument_id  BIGINT NOT NULL REFERENCES ref.instrument,
    platform       TEXT NOT NULL,
    bucket         TIMESTAMPTZ NOT NULL,              -- 15-min bucket start
    mentions       INT NOT NULL,
    unique_authors INT NOT NULL,
    mean_sentiment REAL,
    weighted_sentiment REAL,                          -- credibility & bot-adjusted
    mention_zscore REAL,                              -- vs trailing baseline
    coordination_score REAL,                          -- 0..1 campaign/brigading likelihood
    available_at   TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (instrument_id, platform, bucket)
);

-- Classified market events (from news, filings, calendars, on-chain)
CREATE TABLE intel.event (
    event_id       BIGSERIAL PRIMARY KEY,
    event_type     TEXT NOT NULL,                     -- 'earnings_beat','profit_warning','m_and_a','ceo_exit','rate_decision','token_unlock','exploit',...
    category       TEXT NOT NULL,                     -- 'company','macro','crypto'
    detected_at    TIMESTAMPTZ NOT NULL,              -- = available_at
    occurred_at    TIMESTAMPTZ,
    verification   intel.verification_status NOT NULL DEFAULT 'unverified',
    verified_at    TIMESTAMPTZ,
    primary_source_url TEXT,
    polarity       intel.polarity NOT NULL DEFAULT 'unknown',
    importance     REAL,                              -- 0..1
    surprise       REAL,                              -- vs expectation (standardised), NULL if no expectation
    priced_in_estimate REAL,                          -- fraction of expected move already realised
    impact_horizon TEXT,                              -- '15m','1d','1w',...
    impact_score   REAL,                              -- model output
    extracted      JSONB,                             -- structured fields (EPS, deal value, ...)
    news_cluster_id BIGINT,
    supersedes_event_id BIGINT REFERENCES intel.event
);

CREATE TABLE intel.event_instrument (
    event_id       BIGINT NOT NULL REFERENCES intel.event,
    instrument_id  BIGINT NOT NULL REFERENCES ref.instrument,
    role           TEXT NOT NULL,                     -- 'subject','acquirer','target','peer','sector'
    PRIMARY KEY (event_id, instrument_id, role)
);

-- Measured market reaction to each event (event-study output)
CREATE TABLE intel.event_reaction (
    event_id       BIGINT NOT NULL REFERENCES intel.event,
    instrument_id  BIGINT NOT NULL REFERENCES ref.instrument,
    horizon        TEXT NOT NULL,
    raw_return     REAL,
    abnormal_return REAL,                             -- vs market/sector model
    volume_ratio   REAL,
    computed_at    TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (event_id, instrument_id, horizon)
);

-- ---------------------------------------------------------------------
-- On-chain / memecoin
-- ---------------------------------------------------------------------
CREATE TABLE chain.token_launch (
    instrument_id  BIGINT PRIMARY KEY REFERENCES ref.instrument,
    launchpad      TEXT,                              -- 'pump.fun','raydium_launchlab',...
    creator_hash   BYTEA,
    launched_at    TIMESTAMPTZ NOT NULL,
    launch_slot    BIGINT,
    initial_supply NUMERIC(38,0),
    mint_authority_revoked BOOLEAN,
    freeze_authority_revoked BOOLEAN,
    graduated_at   TIMESTAMPTZ,                       -- bonding curve completion / migration
    first_seen_at  TIMESTAMPTZ NOT NULL
);

CREATE TABLE chain.token_snapshot (
    instrument_id  BIGINT NOT NULL REFERENCES ref.instrument,
    ts             TIMESTAMPTZ NOT NULL,
    price_usd      NUMERIC(38,18),
    liquidity_usd  NUMERIC(28,2),
    volume_usd_5m  NUMERIC(28,2),
    tx_count_5m    INT,
    unique_traders_5m INT,
    holders        INT,
    top10_holder_pct REAL,
    creator_holding_pct REAL,
    market_cap_usd NUMERIC(28,2),
    source         TEXT NOT NULL,
    reliability    REAL,                              -- data-quality score
    available_at   TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (instrument_id, ts)
);

CREATE TABLE chain.risk_flag (
    instrument_id  BIGINT NOT NULL REFERENCES ref.instrument,
    flag           TEXT NOT NULL,                     -- 'low_liquidity','concentrated_holders','wash_trading','unreliable_data','insufficient_samples','mint_authority_active'
    severity       SMALLINT NOT NULL,                 -- 1..3
    raised_at      TIMESTAMPTZ NOT NULL,
    cleared_at     TIMESTAMPTZ,
    evidence       JSONB,
    PRIMARY KEY (instrument_id, flag, raised_at)
);

-- ---------------------------------------------------------------------
-- ML: models, forecasts, outcomes, calibration, signals
-- ---------------------------------------------------------------------
CREATE TABLE ml.model_version (
    model_version_id SERIAL PRIMARY KEY,
    name           TEXT NOT NULL,                     -- 'eq_xgb_direction'
    market_family  ref.market_family NOT NULL,
    algorithm      TEXT NOT NULL,                     -- 'logreg','rf','xgboost','gbm','arima','garch','hmm_regime'
    horizon        TEXT NOT NULL,
    version        TEXT NOT NULL,
    git_sha        TEXT,
    feature_set    TEXT NOT NULL,
    train_start    DATE, train_end DATE,
    validation     JSONB,                             -- walk-forward metrics (AUC, Brier, log-loss, ECE)
    artifact_uri   TEXT,                              -- MLflow / object storage
    status         TEXT NOT NULL DEFAULT 'candidate', -- candidate|shadow|production|retired
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (name, version)
);

CREATE TABLE ml.forecast (
    forecast_id    BIGSERIAL,
    model_version_id INT NOT NULL REFERENCES ml.model_version,
    instrument_id  BIGINT NOT NULL REFERENCES ref.instrument,
    issued_at      TIMESTAMPTZ NOT NULL,              -- decision time; all inputs must have available_at <= issued_at
    horizon        TEXT NOT NULL,
    target_at      TIMESTAMPTZ NOT NULL,
    p_up           REAL NOT NULL CHECK (p_up BETWEEN 0 AND 1),   -- calibrated probability
    p_down         REAL NOT NULL CHECK (p_down BETWEEN 0 AND 1),
    expected_return REAL,
    expected_downside REAL,                           -- e.g. 5% quantile / CVaR
    ci_low         REAL, ci_high REAL, ci_level REAL,
    sample_size    INT NOT NULL,                      -- effective historical sample backing the estimate
    calibration_bucket_ece REAL,                      -- calibration error for this probability bucket
    regime         TEXT,
    top_factors    JSONB,                             -- SHAP-style attributions
    insufficient_data BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (forecast_id, issued_at)
);

CREATE TABLE ml.forecast_outcome (
    forecast_id    BIGINT NOT NULL,
    issued_at      TIMESTAMPTZ NOT NULL,
    realized_return REAL,
    realized_up    BOOLEAN,
    brier          REAL,
    log_loss       REAL,
    resolved_at    TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (forecast_id, issued_at)
);

CREATE TABLE ml.signal (
    signal_id      BIGSERIAL PRIMARY KEY,
    instrument_id  BIGINT NOT NULL REFERENCES ref.instrument,
    issued_at      TIMESTAMPTZ NOT NULL,
    action         ml.signal_action NOT NULL,
    strategy       TEXT NOT NULL,
    forecast_ids   BIGINT[] NOT NULL,
    event_ids      BIGINT[],
    gates_passed   JSONB NOT NULL,                    -- every rule evaluated, pass/fail + values
    expected_net_return REAL,                         -- after fees/slippage
    expected_holding TEXT,
    rationale      TEXT NOT NULL
);

-- ---------------------------------------------------------------------
-- Simulation: backtests and paper trading share the same order model
-- ---------------------------------------------------------------------
CREATE TABLE sim.portfolio (
    portfolio_id   SERIAL PRIMARY KEY,
    kind           TEXT NOT NULL CHECK (kind IN ('backtest','paper')),
    name           TEXT NOT NULL,
    base_currency  CHAR(3) NOT NULL DEFAULT 'USD',
    starting_cash  NUMERIC(20,2) NOT NULL,
    risk_config    JSONB NOT NULL,                    -- max position, max gross/net exposure, stops, sizing rule
    cost_config    JSONB NOT NULL,                    -- fees, slippage model, borrow cost, impact
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE sim.backtest_run (
    run_id         SERIAL PRIMARY KEY,
    portfolio_id   INT NOT NULL REFERENCES sim.portfolio,
    strategy       TEXT NOT NULL,
    params         JSONB NOT NULL,
    universe_rule  TEXT NOT NULL,                     -- point-in-time universe definition
    period_start   DATE NOT NULL, period_end DATE NOT NULL,
    validation     TEXT NOT NULL,                     -- 'walk_forward','holdout','cpcv'
    code_sha       TEXT,
    data_snapshot  TEXT,                              -- ingestion watermark used
    metrics        JSONB,                             -- total/annual return, MDD, Sharpe, Sortino, PF, win rate, exposure...
    benchmark_metrics JSONB,
    started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at    TIMESTAMPTZ
);

CREATE TABLE sim.order_fill (
    fill_id        BIGSERIAL PRIMARY KEY,
    portfolio_id   INT NOT NULL REFERENCES sim.portfolio,
    run_id         INT REFERENCES sim.backtest_run,
    signal_id      BIGINT REFERENCES ml.signal,
    instrument_id  BIGINT NOT NULL REFERENCES ref.instrument,
    side           TEXT NOT NULL CHECK (side IN ('buy','sell','short','cover')),
    qty            NUMERIC(28,8) NOT NULL,
    decision_price NUMERIC(20,8) NOT NULL,
    fill_price     NUMERIC(20,8) NOT NULL,            -- after slippage model
    fees           NUMERIC(20,8) NOT NULL,
    filled_at      TIMESTAMPTZ NOT NULL,
    reason         TEXT NOT NULL                      -- 'signal','stop_loss','take_profit','time_exit','risk_limit'
);

CREATE TABLE sim.equity_curve (
    portfolio_id   INT NOT NULL REFERENCES sim.portfolio,
    run_id         INT NOT NULL DEFAULT 0,            -- 0 = paper portfolio
    ts             TIMESTAMPTZ NOT NULL,
    equity         NUMERIC(20,2) NOT NULL,
    cash           NUMERIC(20,2) NOT NULL,
    gross_exposure NUMERIC(20,2) NOT NULL,
    net_exposure   NUMERIC(20,2) NOT NULL,
    benchmark_equity NUMERIC(20,2),
    PRIMARY KEY (portfolio_id, run_id, ts)
);

-- ---------------------------------------------------------------------
-- Ops: ingestion, data quality, audit
-- ---------------------------------------------------------------------
CREATE TABLE ops.ingestion_run (
    run_id         BIGSERIAL PRIMARY KEY,
    job            TEXT NOT NULL,
    dataset_id     INT REFERENCES ref.dataset,
    started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at    TIMESTAMPTZ,
    status         TEXT NOT NULL DEFAULT 'running',
    rows_written   BIGINT,
    api_calls      INT,
    error          TEXT
);

-- Explicit record of missing data. Never filled with synthetic values.
CREATE TABLE ops.data_gap (
    instrument_id  BIGINT NOT NULL REFERENCES ref.instrument,
    dataset_id     INT NOT NULL REFERENCES ref.dataset,
    interval       mkt.bar_interval NOT NULL,
    gap_start      TIMESTAMPTZ NOT NULL,
    gap_end        TIMESTAMPTZ NOT NULL,
    reason         TEXT,                              -- 'not_provided','halt','pre_listing','provider_error'
    detected_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (instrument_id, dataset_id, interval, gap_start)
);

-- Append-only audit log for every paper-trading decision
CREATE TABLE ops.decision_audit (
    audit_id       BIGSERIAL,
    ts             TIMESTAMPTZ NOT NULL DEFAULT now(),
    portfolio_id   INT REFERENCES sim.portfolio,
    instrument_id  BIGINT REFERENCES ref.instrument,
    decision       ml.signal_action NOT NULL,
    signal_id      BIGINT REFERENCES ml.signal,
    model_version_ids INT[] NOT NULL,
    forecast_snapshot JSONB NOT NULL,                 -- probabilities exactly as seen
    evidence       JSONB NOT NULL,                    -- news ids, social metrics, indicators
    hypothetical_entry NUMERIC(20,8),
    hypothetical_exit  NUMERIC(20,8),
    outcome        JSONB,                             -- filled later by outcome resolver
    prev_hash      BYTEA,                             -- hash chain for tamper evidence
    row_hash       BYTEA,
    PRIMARY KEY (audit_id, ts)
);

-- ---------------------------------------------------------------------
-- TimescaleDB: hypertables, compression, continuous aggregates.
-- Guarded so the schema also loads on vanilla PostgreSQL for tests.
-- ---------------------------------------------------------------------
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb') THEN
    CREATE EXTENSION IF NOT EXISTS timescaledb;
    PERFORM create_hypertable('mkt.bar',              'ts',            chunk_time_interval => INTERVAL '30 days', if_not_exists => TRUE);
    PERFORM create_hypertable('chain.token_snapshot', 'ts',            chunk_time_interval => INTERVAL '1 day',   if_not_exists => TRUE);
    PERFORM create_hypertable('intel.social_metric',  'bucket',        chunk_time_interval => INTERVAL '7 days',  if_not_exists => TRUE);
    PERFORM create_hypertable('intel.news_item',      'first_seen_at', chunk_time_interval => INTERVAL '30 days', if_not_exists => TRUE);
    PERFORM create_hypertable('intel.social_post',    'first_seen_at', chunk_time_interval => INTERVAL '7 days',  if_not_exists => TRUE);
    PERFORM create_hypertable('ml.forecast',          'issued_at',     chunk_time_interval => INTERVAL '30 days', if_not_exists => TRUE);
    PERFORM create_hypertable('ops.decision_audit',   'ts',            chunk_time_interval => INTERVAL '30 days', if_not_exists => TRUE);
    EXECUTE 'ALTER TABLE mkt.bar SET (timescaledb.compress, timescaledb.compress_segmentby = ''instrument_id, interval'')';
    PERFORM add_compression_policy('mkt.bar', INTERVAL '90 days', if_not_exists => TRUE);
  ELSE
    RAISE NOTICE 'timescaledb not available: tables created as plain PostgreSQL tables';
  END IF;
END $$;

-- Audit log is append-only for the application role
CREATE OR REPLACE FUNCTION ops.forbid_audit_mutation() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'DELETE' OR (to_jsonb(NEW) - 'outcome') IS DISTINCT FROM (to_jsonb(OLD) - 'outcome') THEN
    RAISE EXCEPTION 'decision_audit is append-only (only the outcome column may be filled)';
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER decision_audit_append_only
  BEFORE UPDATE OR DELETE ON ops.decision_audit
  FOR EACH ROW EXECUTE FUNCTION ops.forbid_audit_mutation();
