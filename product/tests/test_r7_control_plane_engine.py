"""Unit tests for build_control_plane_engine's DSN validation (R7, G4), and for the
process-wide pooled connection (R11, R13) that replaced a fresh psycopg.connect() per
request in both routers.

R7's answer-provenance and delivery DDL (egpt.answer_provenance.ddl, egpt_retail.delivery.ddl)
is Postgres-only -- JSONB, TIMESTAMPTZ, now(). Before this, a non-Postgres EGPT_RETAIL_DB_URL
reached that DDL and failed with an opaque driver error; build_control_plane_engine now
refuses it by name instead. No database needed -- construction fails before any connection.
"""

from __future__ import annotations

import pytest

from egpt_retail.app import db as db_module
from egpt_retail.app.db import (
    build_control_plane_engine,
    pooled_control_plane_connection,
)
from egpt_retail.app.settings import RetailSettings


def test_accepts_a_postgresql_psycopg_dsn():
    settings = RetailSettings(
        retail_db_url="postgresql+psycopg://ai:ai@localhost:5532/ai"
    )
    engine = build_control_plane_engine(settings)
    assert engine.dialect.name == "postgresql"


@pytest.mark.parametrize(
    "bad_url",
    [
        "sqlite:///local.db",
        "mysql+pymysql://root@localhost/ai",
        "postgresql://ai:ai@localhost:5532/ai",  # no +driver
    ],
)
def test_refuses_a_non_postgres_dsn_by_name(bad_url):
    settings = RetailSettings(retail_db_url=bad_url)
    with pytest.raises(ValueError, match="EGPT_RETAIL_DB_URL"):
        build_control_plane_engine(settings)


# ---------------------------------------------------------------------------
# pooled_control_plane_connection -- round-2 review's own suggested regression guard:
# a future revert to a fresh psycopg.connect() per call must be caught by something.
# No database needed -- create_engine() never connects until first use, and this only
# checks that the *same* Engine object is reused per DSN.
# ---------------------------------------------------------------------------


def test_pooled_control_plane_engine_is_cached_per_dsn():
    dsn = "postgresql+psycopg://ai:ai@localhost:5532/pooled_engine_cache_probe"
    first = db_module._pooled_control_plane_engine(dsn)
    second = db_module._pooled_control_plane_engine(dsn)
    assert first is second


def test_pooled_control_plane_engine_is_distinct_per_dsn():
    engine_a = db_module._pooled_control_plane_engine(
        "postgresql+psycopg://ai:ai@localhost:5532/pooled_engine_probe_a"
    )
    engine_b = db_module._pooled_control_plane_engine(
        "postgresql+psycopg://ai:ai@localhost:5532/pooled_engine_probe_b"
    )
    assert engine_a is not engine_b


def test_pooled_control_plane_connection_acquires_from_the_cached_engine(monkeypatch):
    """`_dashboard_store`/`_playground_store` call this per request; asserting it goes
    through the cached engine's `raw_connection()` -- rather than a fresh `psycopg.connect()`
    -- is what would catch a future revert of either router's dependency."""
    calls = []

    class _FakeConnection:
        def close(self):
            calls.append("closed")

    class _FakeEngine:
        def raw_connection(self):
            calls.append("acquired")
            return _FakeConnection()

    monkeypatch.setattr(
        db_module, "_pooled_control_plane_engine", lambda dsn: _FakeEngine()
    )
    settings = RetailSettings(
        retail_db_url="postgresql+psycopg://ai:ai@localhost:5532/ai"
    )

    connection = pooled_control_plane_connection(settings)
    connection.close()

    assert calls == ["acquired", "closed"]
