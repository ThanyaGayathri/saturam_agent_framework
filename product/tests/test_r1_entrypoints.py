"""R1 without a database: settings binding, scope building, memory config shape, dispatch.

Everything that needs Postgres is a cookbook scenario run by hand and recorded in TEST_LOG.md.
These pin the parts that must hold before that run and would fail silently if they did not --
the env-prefix trap above all.
"""

from __future__ import annotations

import pytest
from egpt.memory.consolidation.worker import ConsolidationOutcome
from egpt.memory.types import ConsolidationReport, Scope
from egpt.providers.model_provider import ProviderMode

from egpt_retail.app.db import psycopg_dsn
from egpt_retail.app.factory import build_agent_os
from egpt_retail.app.settings import RetailSettings
from egpt_retail.entrypoints.__main__ import build_parser
from egpt_retail.entrypoints.consolidator import summarise
from egpt_retail.memory.config import build_memory_config, build_memory_resolver
from egpt_retail.memory.scopes import (
    SYSTEM_USER_ID,
    build_consolidation_scopes,
    consolidation_scope_source,
)


def test_settings_bind_from_the_branded_prefix(monkeypatch):
    """EGPT_RETAIL_* binds because the prefix is in the field name -- not via env_prefix."""
    monkeypatch.setenv("EGPT_RETAIL_CUSTOMER_SLUG", "acme")
    monkeypatch.setenv("EGPT_RETAIL_ROLE", "pipelines")
    monkeypatch.setenv("EGPT_RETAIL_INTERNAL_SERVICE_TOKEN", "shared")
    s = RetailSettings()
    assert s.retail_customer_slug == "acme"
    assert s.scheduler_enabled is True
    assert s.retail_internal_service_token == "shared"


def test_unprefixed_spelling_does_not_bind(monkeypatch):
    monkeypatch.delenv("EGPT_RETAIL_CUSTOMER_SLUG", raising=False)
    monkeypatch.setenv("RETAIL_CUSTOMER_SLUG", "acme")
    monkeypatch.setenv("CUSTOMER_SLUG", "acme")
    assert RetailSettings().retail_customer_slug is None


def test_pipelines_role_refuses_to_build_without_a_shared_token(monkeypatch):
    monkeypatch.setenv("EGPT_RETAIL_ROLE", "pipelines")
    monkeypatch.delenv("EGPT_RETAIL_INTERNAL_SERVICE_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="EGPT_RETAIL_INTERNAL_SERVICE_TOKEN"):
        build_agent_os(RetailSettings())


def test_no_slug_means_no_scopes_and_no_error(monkeypatch):
    monkeypatch.delenv("EGPT_RETAIL_CUSTOMER_SLUG", raising=False)
    assert build_consolidation_scopes(RetailSettings(), ["synonym_proposer"]) == []
    monkeypatch.setenv("EGPT_RETAIL_CUSTOMER_SLUG", "   ")
    assert build_consolidation_scopes(RetailSettings(), ["synonym_proposer"]) == []


def test_scopes_follow_memory_scope_md(monkeypatch):
    monkeypatch.setenv("EGPT_RETAIL_CUSTOMER_SLUG", "acme")
    scopes = build_consolidation_scopes(RetailSettings(), ["synonym_proposer", "other"])
    assert scopes == [
        Scope(tenant_id="acme", agent_id="synonym_proposer", user_id=SYSTEM_USER_ID),
        Scope(tenant_id="acme", agent_id="other", user_id=SYSTEM_USER_ID),
    ]
    assert SYSTEM_USER_ID == "__scheduler__"
    source = consolidation_scope_source(RetailSettings(), ["synonym_proposer"])
    assert callable(source) and list(source()) == scopes[:1]


def test_memory_config_is_vector_only_mode_b():
    # The backend is named explicitly: since AZ-B the default is azure_ai_search, and this
    # test is about the pgvector dict's shape, not about which backend the deployment runs
    # (test_az_b_memory_backend.py pins that).
    settings = RetailSettings(retail_memory_backend="pgvector")
    config = build_memory_config(settings)
    assert config.provider == "co_located"
    assert config.mode is ProviderMode.FRONTIER, (
        "an Azure embedder is only legal under Mode B"
    )
    assert config.vector["backend"] == "pgvector"
    assert "db_url" in config.vector and "dsn" not in config.vector
    assert config.graph == {}, "no Neo4j in phase zero"
    assert build_memory_resolver(settings).default == config


def test_dsn_conversion_for_the_advisory_lock():
    assert (
        psycopg_dsn("postgresql+psycopg://ai:ai@localhost:5532/ai")
        == "postgresql://ai:ai@localhost:5532/ai"
    )
    assert psycopg_dsn("postgresql://x") == "postgresql://x"


def test_dsn_conversion_strips_any_sqlalchemy_driver_suffix():
    """Not just ``+psycopg`` -- a DSN written for a different driver must still reach

    ``psycopg.connect`` as plain libpq, or only the consolidator fails, opaquely, after the
    settings already looked correct everywhere else.
    """
    assert (
        psycopg_dsn("postgresql+psycopg2://ai:ai@localhost:5532/ai")
        == "postgresql://ai:ai@localhost:5532/ai"
    )
    assert (
        psycopg_dsn("postgresql+asyncpg://ai:ai@localhost:5532/ai")
        == "postgresql://ai:ai@localhost:5532/ai"
    )


def test_dispatch_knows_five_commands():
    parser = build_parser()
    assert parser.parse_args(["api"]).command == "api"
    assert parser.parse_args(["pipelines", "--port", "8000"]).port == 8000
    ns = parser.parse_args(["consolidator", "--once", "--poll-interval", "5"])
    assert ns.once is True and ns.poll_interval == 5.0
    assert parser.parse_args(["migrate"]).command == "migrate"
    # R4. Separate from `migrate`, which stays control-plane only.
    assert parser.parse_args(["warehouse-migrate"]).command == "warehouse-migrate"
    with pytest.raises(SystemExit):
        parser.parse_args(["worker"])


def test_summary_line_distinguishes_no_work_from_work():
    assert (
        summarise([], 0.01)
        == "consolidator: tick complete -- 0 scope(s) checked, 0 with work, 0 pass(es), 0 error(s) in 0.01s"
    )

    scope_a = Scope(
        tenant_id="acme", agent_id="synonym_proposer", user_id="__scheduler__"
    )
    scope_b = Scope(tenant_id="acme", agent_id="other", user_id="__scheduler__")
    outcomes = [
        ConsolidationOutcome(
            scope=scope_a,
            reports=[
                ConsolidationReport(scope=scope_a, episodic_scanned=3),
                ConsolidationReport(scope=scope_a),
            ],
        ),
        ConsolidationOutcome(scope=scope_b, error=RuntimeError("x")),
    ]
    assert (
        summarise(outcomes, 0.05)
        == "consolidator: tick complete -- 2 scope(s) checked, 1 with work, 2 pass(es), 1 error(s) in 0.05s"
    )
