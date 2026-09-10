"""Unit tests for build_control_plane_engine's DSN validation (R7, G4).

R7's answer-provenance and delivery DDL (egpt.answer_provenance.ddl, egpt_retail.delivery.ddl)
is Postgres-only -- JSONB, TIMESTAMPTZ, now(). Before this, a non-Postgres EGPT_RETAIL_DB_URL
reached that DDL and failed with an opaque driver error; build_control_plane_engine now
refuses it by name instead. No database needed -- construction fails before any connection.
"""

from __future__ import annotations

import pytest

from egpt_retail.app.db import build_control_plane_engine
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
