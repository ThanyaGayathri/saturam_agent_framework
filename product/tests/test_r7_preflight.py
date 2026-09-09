"""Unit tests for R7's check_preflight executor (G4) -- no database, no live semantic layer.

egpt_retail.contracts.kpi.assess_feasibility is pure, so every scenario the delivery plan's
Verify text asks for ("run preflight against a use case referencing a blocked KPI") is
constructible by hand.
"""

from __future__ import annotations

import asyncio

import pytest
from egpt.workflow.types import StepInput

from egpt_retail.contracts.kpi import KpiDefinition
from egpt_retail.executors.preflight import GOLD_STALENESS_BOUND_HOURS
from egpt_retail.executors.preflight import check_preflight as _check_preflight
from egpt_retail.warehouse.health import FRESHNESS_BOUND_HOURS


@pytest.fixture(autouse=True)
def _no_warehouse(monkeypatch):
    """These are R7's DB-free scenarios: with no warehouse configured, gold_fresh stays the
    named placeholder and every assertion below is about kpis_ready alone."""
    monkeypatch.delenv("EGPT_RETAIL_WAREHOUSE_DB_URL", raising=False)


def check_preflight(step_input):
    return asyncio.run(_check_preflight(step_input))


def _kpi(identifier: str, sql: str, source_tables: frozenset) -> KpiDefinition:
    return KpiDefinition(
        identifier=identifier,
        name=identifier,
        description="test fixture",
        sql=sql,
        grain="product",
        business_area="product",
        source_tables=source_tables,
    )


def test_gold_staleness_bound_is_imported_from_health_not_restated():
    """``preflight.py``'s docstring and this executor's own comment both call
    ``GOLD_STALENESS_BOUND_HOURS`` the same bound ``warehouse/health.py`` draws its
    ``fresh``/``stale`` line at -- that claim is only true if it is the *same* value, not two
    ``36.0`` literals that happen to agree today and can silently drift apart tomorrow."""
    assert GOLD_STALENESS_BOUND_HOURS == FRESHNESS_BOUND_HOURS


def test_ready_kpi_with_every_table_available_allows():
    kpi = _kpi("orders__count", "SELECT count(*) FROM orders", frozenset({"orders"}))
    step_input = StepInput(
        additional_data={"kpis": [kpi], "available_tables": {"orders", "customers"}}
    )

    output = check_preflight(step_input)

    assert output.content["allowed"] is True
    assert output.content["blocked_by"] == []
    assert output.stop is False
    assert output.success is True
    assert output.error is None
    kpis_check = output.content["checks"][0]
    assert kpis_check == {
        "name": "kpis_ready",
        "passed": True,
        "reason": "orders__count: ready.",
    }


def test_blocked_kpi_reproduces_the_contracts_own_worked_example():
    """product__avg_rating / web_sessions is the exact blocked example
    product/egpt_retail/api/openapi/paths/use-cases.yaml's runUseCasePreflight ships."""
    kpi = _kpi(
        "product__avg_rating",
        "SELECT avg(rating) FROM product_reviews JOIN web_sessions ON product_reviews.session_id = web_sessions.id",
        frozenset({"product_reviews", "web_sessions"}),
    )
    step_input = StepInput(
        additional_data={"kpis": [kpi], "available_tables": {"product_reviews"}}
    )

    output = check_preflight(step_input)

    assert output.content["allowed"] is False
    assert output.content["blocked_by"] == ["kpis_ready"]
    assert output.stop is True
    assert output.error == (
        "product__avg_rating: partial -- missing from the semantic snapshot: web_sessions."
    )
    kpis_check = output.content["checks"][0]
    assert kpis_check["passed"] is False
    assert "web_sessions" in kpis_check["reason"]


def test_fully_blocked_kpi_names_every_missing_table():
    kpi = _kpi(
        "supplier__on_time_rate",
        "SELECT count(*) FROM supplier_deliveries JOIN product_reviews ON true",
        frozenset({"supplier_deliveries", "product_reviews"}),
    )
    step_input = StepInput(additional_data={"kpis": [kpi], "available_tables": set()})

    output = check_preflight(step_input)

    assert output.content["allowed"] is False
    reason = output.content["checks"][0]["reason"]
    assert "product_reviews" in reason
    assert "supplier_deliveries" in reason


def test_multiple_ready_kpis_all_name_their_own_reason():
    """Only the aggregate reason for an all-blocked-or-partial set is expected to name every
    KPI -- an all-ready set must not silently drop every KPI after the first from the reason
    text either."""
    orders = _kpi("orders__count", "SELECT count(*) FROM orders", frozenset({"orders"}))
    customers = _kpi(
        "customers__count", "SELECT count(*) FROM customers", frozenset({"customers"})
    )
    step_input = StepInput(
        additional_data={
            "kpis": [orders, customers],
            "available_tables": {"orders", "customers"},
        }
    )

    output = check_preflight(step_input)

    reason = output.content["checks"][0]["reason"]
    assert "orders__count: ready." in reason
    assert "customers__count: ready." in reason


def test_no_kpis_referenced_allows():
    step_input = StepInput(additional_data={"kpis": [], "available_tables": set()})

    output = check_preflight(step_input)

    assert output.content["allowed"] is True
    assert output.content["checks"][0] == {
        "name": "kpis_ready",
        "passed": True,
        "reason": "No KPIs referenced.",
    }


def test_unbuilt_checks_are_always_present_and_passing():
    """tables_exposed / gold_fresh / guard_role are honest placeholders, not omitted or faked
    as computed -- every response carries all four contract-named checks."""
    step_input = StepInput(additional_data={})

    output = check_preflight(step_input)

    names = [c["name"] for c in output.content["checks"]]
    assert names == ["kpis_ready", "tables_exposed", "gold_fresh", "guard_role"]
    assert all(c["passed"] for c in output.content["checks"][1:])
    assert all(c["reason"] for c in output.content["checks"])


def test_use_case_id_passes_through():
    step_input = StepInput(additional_data={"use_case_id": "uc_0001"})

    output = check_preflight(step_input)

    assert output.content["use_case_id"] == "uc_0001"


def test_no_additional_data_does_not_raise():
    step_input = StepInput()

    output = check_preflight(step_input)

    assert output.content["allowed"] is True
