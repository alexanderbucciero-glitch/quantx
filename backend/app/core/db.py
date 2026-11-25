"""Persistence (SQLAlchemy Core). Defaults to a local SQLite file so QUANTX runs on a Mac with no
extra services; set QUANTX_DATABASE_URL=postgresql+psycopg://... to move to PostgreSQL/TimescaleDB.

Every trading table carries an `account` column: "live" is the autonomous paper-trading account;
"replay-<n>" accounts hold historical-replay demonstrations. They are never mixed in reports.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

from sqlalchemy import (JSON, BigInteger, Column, Float, Index, Integer, MetaData, String, Table, Text, create_engine,
                        delete, func, insert, inspect, select, text, update)
from sqlalchemy.engine import Engine

from .config import settings

meta = MetaData()

candles = Table("candles", meta,
    Column("symbol", String(64), primary_key=True), Column("tf", String(8), primary_key=True),
    Column("t", BigInteger, primary_key=True), Column("source", String(32)),
    Column("o", Float), Column("h", Float), Column("l", Float), Column("c", Float), Column("v", Float))

activity = Table("activity", meta,
    Column("id", Integer, primary_key=True, autoincrement=True), Column("ts", Float, index=True),
    Column("account", String(32), default="live"),
    Column("level", String(8)), Column("kind", String(32)), Column("asset", String(64)),
    Column("strategy", String(32)), Column("message", Text), Column("latency_ms", Float), Column("data", JSON))

opportunities = Table("opportunities", meta,
    Column("id", Integer, primary_key=True, autoincrement=True), Column("ts", Float, index=True),
    Column("account", String(32), default="live", index=True),
    Column("asset", String(64)), Column("asset_class", String(16)), Column("strategy", String(32)),
    Column("direction", String(8)), Column("status", String(16)), Column("reason", Text), Column("report", JSON),
    Column("client_key", String(160)))

positions = Table("positions", meta,
    Column("id", Integer, primary_key=True, autoincrement=True), Column("account", String(32), default="live", index=True),
    Column("asset", String(64)), Column("display", String(64)),
    Column("asset_class", String(16)), Column("strategy", String(32)), Column("tf", String(8)), Column("direction", String(8)),
    Column("tier", String(16)), Column("status", String(16), index=True), Column("opened_at", Float), Column("closed_at", Float),
    Column("entry", Float), Column("qty", Float), Column("remaining_qty", Float), Column("stop", Float),
    Column("initial_stop", Float), Column("tp1", Float), Column("tp2", Float), Column("tp1_done", Integer, default=0),
    Column("trail_atr", Float), Column("atr", Float), Column("extreme", Float),
    Column("max_hold_s", Float), Column("fees", Float, default=0), Column("slippage_cost", Float, default=0),
    Column("realized_pnl", Float, default=0), Column("gross_pnl", Float, default=0),
    Column("exit_avg", Float), Column("exit_reason", String(32)), Column("last_price", Float), Column("last_quote_ts", Float),
    Column("mfe_pct", Float, default=0), Column("mae_pct", Float, default=0),
    Column("expected_net_pct", Float), Column("p_success", Float),
    Column("opportunity_id", Integer), Column("report", JSON), Column("trade_report", JSON))

orders = Table("orders", meta,
    Column("id", Integer, primary_key=True, autoincrement=True), Column("account", String(32), default="live", index=True),
    Column("client_key", String(160), unique=True), Column("asset", String(64)), Column("asset_class", String(16)),
    Column("position_id", Integer, index=True), Column("purpose", String(16)),         # entry | tp1 | tp2 | stop | exit
    Column("side", String(8)), Column("type", String(8)),                                 # buy/sell ; market/limit/stop
    Column("qty", Float), Column("limit_price", Float), Column("stop_price", Float),
    Column("status", String(16), index=True), Column("created_at", Float), Column("expires_at", Float),
    Column("filled_at", Float), Column("fill_price", Float), Column("ref_price", Float), Column("fee", Float),
    Column("liquidity", String(8)), Column("reason", Text), Column("meta", JSON))

fills = Table("fills", meta,
    Column("id", Integer, primary_key=True, autoincrement=True), Column("account", String(32), default="live", index=True),
    Column("position_id", Integer, index=True), Column("order_id", Integer),
    Column("ts", Float), Column("asset", String(64)), Column("display", String(64)), Column("strategy", String(32)),
    Column("kind", String(16)), Column("side", String(8)), Column("price", Float),
    Column("ref_price", Float), Column("qty", Float), Column("fee", Float), Column("slippage", Float),
    Column("liquidity", String(8)), Column("reason", Text))

cash = Table("cash", meta,
    Column("account", String(32), primary_key=True), Column("starting", Float), Column("cash", Float),
    Column("peak_equity", Float), Column("created_at", Float))

equity = Table("equity", meta,
    Column("id", Integer, primary_key=True, autoincrement=True), Column("account", String(32), default="live", index=True),
    Column("ts", Float, index=True), Column("equity", Float), Column("cash", Float),
    Column("unrealized", Float), Column("exposure", Float))

reports = Table("reports", meta,
    Column("id", Integer, primary_key=True, autoincrement=True), Column("account", String(32), index=True),
    Column("kind", String(16)), Column("period_start", Float), Column("period_end", Float),
    Column("position_id", Integer), Column("created_at", Float), Column("body", JSON))

strategy_changes = Table("strategy_changes", meta,
    Column("id", Integer, primary_key=True, autoincrement=True), Column("account", String(32)),
    Column("ts", Float), Column("combo", String(96)), Column("old_tier", String(16)), Column("new_tier", String(16)),
    Column("reason", Text), Column("evidence", JSON))

kv = Table("kv", meta, Column("key", String(64), primary_key=True), Column("value", JSON), Column("updated", Float))

Index("ix_fills_account_ts", fills.c.account, fills.c.ts)

_engine: Engine | None = None


def _migrate(eng: Engine) -> None:
    """Add columns introduced after a database was first created (SQLite has no auto-migration)."""
    insp = inspect(eng)
    with eng.begin() as c:
        for table in meta.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {col["name"] for col in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name not in have:
                    typ = col.type.compile(eng.dialect)
                    default = " DEFAULT 'live'" if col.name == "account" else ""
                    c.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {col.name} {typ}{default}'))


def engine() -> Engine:
    global _engine
    if _engine is None:
        url = settings.database_url
        if url.startswith("sqlite:///"):
            path = url.removeprefix("sqlite:///")
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        _engine = create_engine(url, future=True, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {})
        _migrate(_engine)
        meta.create_all(_engine)
        if url.startswith("sqlite"):
            with _engine.begin() as c:
                c.exec_driver_sql("PRAGMA journal_mode=WAL")
    return _engine


def reset_engine_for_tests(url: str) -> None:
    global _engine
    settings.database_url = url
    _engine = None


def jsonable(v: Any) -> Any:
    return json.loads(json.dumps(v, default=float))


def kv_get(key: str, default: Any = None) -> Any:
    with engine().connect() as c:
        r = c.execute(select(kv.c.value).where(kv.c.key == key)).first()
    return r[0] if r else default


def kv_set(key: str, value: Any) -> None:
    with engine().begin() as c:
        if c.execute(select(kv.c.key).where(kv.c.key == key)).first():
            c.execute(update(kv).where(kv.c.key == key).values(value=jsonable(value), updated=time.time()))
        else:
            c.execute(insert(kv).values(key=key, value=jsonable(value), updated=time.time()))


def rows(q, params: dict | None = None) -> list[dict]:
    with engine().connect() as c:
        res = c.execute(q, params or {})
        return [dict(r._mapping) for r in res]


__all__ = ["func", "text", "engine", "candles", "activity", "opportunities", "positions", "orders", "fills", "cash",
           "equity", "reports", "strategy_changes", "kv", "kv_get", "kv_set", "rows", "jsonable",
           "select", "insert", "update", "delete"]
