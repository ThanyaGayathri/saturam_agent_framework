"""Unit tests for R7's check_preflight executor (G4) -- no database, no live semantic layer.

egpt_retail.contracts.kpi.assess_feasibility is pure, so every scenario the delivery plan's
Verify text asks for ("run preflight against a use case referencing a blocked KPI") is
constructible by hand.
"""

from __future__ import annotations

from egpt.workflow.types import StepInput

from egpt_retail.contracts.kpi import KpiDefinition
from egpt_retail.executors.preflight import check_preflight


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
