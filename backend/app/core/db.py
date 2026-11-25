"""Persistence (SQLAlchemy Core). Defaults to a local SQLite file so QUANTX runs on a Mac with no
extra services; set QUANTX_DATABASE_URL=postgresql+psycopg://... to move to PostgreSQL/TimescaleDB."""
from __future__ import annotations

import json
import os
import time
from typing import Any

from sqlalchemy import (JSON, func, BigInteger, Column, Float, Integer, MetaData, String, Table, Text, create_engine,
                        delete, insert, select, update)
from sqlalchemy.engine import Engine

from .config import settings

meta = MetaData()

candles = Table("candles", meta,
    Column("symbol", String(64), primary_key=True), Column("tf", String(8), primary_key=True),
    Column("t", BigInteger, primary_key=True), Column("source", String(32)),
    Column("o", Float), Column("h", Float), Column("l", Float), Column("c", Float), Column("v", Float))

activity = Table("activity", meta,
    Column("id", Integer, primary_key=True, autoincrement=True), Column("ts", Float, index=True),
    Column("level", String(8)), Column("kind", String(32)), Column("asset", String(64)),
    Column("strategy", String(32)), Column("message", Text), Column("latency_ms", Float), Column("data", JSON))

opportunities = Table("opportunities", meta,
    Column("id", Integer, primary_key=True, autoincrement=True), Column("ts", Float, index=True),
    Column("asset", String(64)), Column("asset_class", String(16)), Column("strategy", String(32)),
    Column("direction", String(8)), Column("status", String(16)), Column("reason", Text), Column("report", JSON))

positions = Table("positions", meta,
    Column("id", Integer, primary_key=True, autoincrement=True), Column("asset", String(64)),
    Column("asset_class", String(16)), Column("strategy", String(32)), Column("direction", String(8)),
    Column("status", String(16), index=True), Column("opened_at", Float), Column("closed_at", Float),
    Column("entry", Float), Column("qty", Float), Column("remaining_qty", Float), Column("stop", Float),
    Column("tp1", Float), Column("tp2", Float), Column("tp1_done", Integer, default=0),
    Column("max_hold_s", Float), Column("fees", Float, default=0), Column("realized_pnl", Float, default=0),
    Column("exit_avg", Float), Column("last_price", Float), Column("opportunity_id", Integer), Column("report", JSON))

fills = Table("fills", meta,
    Column("id", Integer, primary_key=True, autoincrement=True), Column("position_id", Integer, index=True),
    Column("ts", Float), Column("kind", String(16)), Column("side", String(8)), Column("price", Float),
    Column("ref_price", Float), Column("qty", Float), Column("fee", Float), Column("slippage", Float))

equity = Table("equity", meta,
    Column("ts", Float, primary_key=True), Column("equity", Float), Column("cash", Float),
    Column("unrealized", Float), Column("exposure", Float))

kv = Table("kv", meta, Column("key", String(64), primary_key=True), Column("value", JSON), Column("updated", Float))

_engine: Engine | None = None


def engine() -> Engine:
    global _engine
    if _engine is None:
        url = settings.database_url
        if url.startswith("sqlite:///"):
            path = url.removeprefix("sqlite:///")
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        _engine = create_engine(url, future=True, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {})
        meta.create_all(_engine)
        if url.startswith("sqlite"):
            with _engine.begin() as c:
                c.exec_driver_sql("PRAGMA journal_mode=WAL")
    return _engine


def reset_engine_for_tests(url: str) -> None:
    global _engine
    settings.database_url = url
    _engine = None


def kv_get(key: str, default: Any = None) -> Any:
    with engine().connect() as c:
        r = c.execute(select(kv.c.value).where(kv.c.key == key)).first()
    return r[0] if r else default


def kv_set(key: str, value: Any) -> None:
    with engine().begin() as c:
        if c.execute(select(kv.c.key).where(kv.c.key == key)).first():
            c.execute(update(kv).where(kv.c.key == key).values(value=json.loads(json.dumps(value, default=float)), updated=time.time()))
        else:
            c.execute(insert(kv).values(key=key, value=json.loads(json.dumps(value, default=float)), updated=time.time()))


__all__ = ["func", "engine", "candles", "activity", "opportunities", "positions", "fills", "equity", "kv",
           "kv_get", "kv_set", "select", "insert", "update", "delete"]
