# -*- coding: utf-8 -*-
"""DATA-010 (Medium): the PostgreSQL backend must reuse ONE SQLAlchemy engine.

Every query helper used to call ``sqlalchemy.create_engine()`` itself — a fresh
engine (and connection pool) per call, on the 20s node-poll hot path, so the
pooling never actually pooled. These pin the singleton contract: ``_get_engine()``
builds the engine at most once and returns the same object thereafter, and
``close_db()`` disposes + clears it so a re-init (or a test swapping
``DATABASE_URL``) starts fresh.

The PG query path is not exercised by the SQLite CI suite, so the singleton
itself is tested directly with ``sqlalchemy.create_engine`` stubbed (no real DB
connection is attempted).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

import central_server.database as db


class _FakeEngine:
    def __init__(self):
        self.disposed = False

    def dispose(self):
        self.disposed = True


@pytest.fixture
def stub_create_engine(monkeypatch):
    import sqlalchemy

    calls = {"n": 0, "urls": []}

    def fake(url, *a, **kw):
        calls["n"] += 1
        calls["urls"].append(url)
        return _FakeEngine()

    monkeypatch.setattr(sqlalchemy, "create_engine", fake)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h:5432/db")

    # Isolate the module globals close_db()/_get_engine() touch, so this test
    # neither inherits a cached engine nor closes another suite's connection.
    saved = (db._pg_engine, db._db_connection, db._pg_database)
    db._pg_engine = None
    db._db_connection = None
    db._pg_database = None
    try:
        yield calls
    finally:
        db._pg_engine, db._db_connection, db._pg_database = saved


def test_get_engine_is_a_singleton(stub_create_engine):
    e1 = db._get_engine()
    e2 = db._get_engine()
    e3 = db._get_engine()
    assert e1 is e2 is e3
    assert stub_create_engine["n"] == 1, stub_create_engine


def test_get_engine_normalizes_psycopg2_driver(stub_create_engine):
    db._get_engine()
    # postgresql:// -> postgresql+psycopg2:// (explicit driver, matches init).
    assert stub_create_engine["urls"] == ["postgresql+psycopg2://u:p@h:5432/db"], stub_create_engine


def test_close_db_disposes_and_resets_engine(stub_create_engine):
    e1 = db._get_engine()
    assert stub_create_engine["n"] == 1

    db.close_db()
    assert e1.disposed is True
    assert db._pg_engine is None

    # After close, a fresh engine is built on next use.
    e2 = db._get_engine()
    assert e2 is not e1
    assert stub_create_engine["n"] == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
