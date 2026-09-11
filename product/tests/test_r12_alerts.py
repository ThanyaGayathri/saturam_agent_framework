"""R12's alert executors are registrable and coexist with the rest -- no database for the
condition logic and the registry shape; the store's validation guard needs no database
either, since it never touches the KPI registry itself.

Mirrors test_r7_executors.py's own pattern.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from egpt_retail.alerts.condition import (
    ConditionUnavailable,
    condition_met,
    render_rule_sentence,
    requires_previous_period,
    shift_date,
)
from egpt_retail.alerts.store import AlertDefinitionRejected, validate_alert_target
from egpt_retail.app.components import R12_EXECUTORS, build_registry
from egpt_retail.contracts.kpi import (
    KpiDefinition,
    ResultShape,
    TimeWindow,
    TimeWindowUnit,
)
from egpt_retail.executors.registry import assert_registry_valid, check_registry

_REGISTRY_DOC = (
    Path(__file__).resolve().parents[1]
    / "egpt_retail"
    / "contracts"
    / "EXECUTOR_REGISTRY.md"
)

_SCALAR_KPI = KpiDefinition(
    identifier="supplier__on_time_rate_30d",
    name="Supplier on-time rate (30d)",
    description="Share of orders delivered on time over the trailing 30 days.",
    sql="SELECT avg(on_time::int) FROM supplier_delivery",
    grain="overall",
    business_area="supply_chain",
    source_tables=frozenset({"supplier_delivery"}),
)

_ROWSET_KPI = KpiDefinition(
    identifier="stocklevel__reorder_urgency_list",
    name="Reorder urgency list",
    description="SKUs ranked by reorder urgency.",
    sql="SELECT sku, urgency FROM stock_level",
    grain="product",
    business_area="inventory",
    source_tables=frozenset({"stock_level"}),
    result_shape=ResultShape.ROWSET,
)


# --- the executor registry convention -----------------------------------------------------


def test_the_three_executors_satisfy_the_registry_convention() -> None:
    assert check_registry(list(R12_EXECUTORS)) == ()
    assert len(R12_EXECUTORS) == 3


def test_every_r12_executor_is_async_under_one_name() -> None:
    for executor in R12_EXECUTORS:
        assert inspect.iscoroutinefunction(executor), executor.__name__
        assert not executor.__name__.startswith("a"), (
            f"{executor.__name__} looks like an async twin; executors take one name"
        )


def test_every_r12_executor_takes_exactly_step_input() -> None:
    for executor in R12_EXECUTORS:
        parameters = list(inspect.signature(executor).parameters)
        assert parameters == ["step_input"], (executor.__name__, parameters)


def test_the_registry_document_lists_every_r12_executor() -> None:
    body = _REGISTRY_DOC.read_text(encoding="utf-8")
    for executor in R12_EXECUTORS:
        assert f"`{executor.__name__}`" in body, (
            f"{executor.__name__} is registered in code but missing from "
            "EXECUTOR_REGISTRY.md's list"
        )


def test_build_registry_includes_all_three_r12_executors() -> None:
    registry = build_registry()
    names = {function.__name__ for function in registry.functions}
    assert {executor.__name__ for executor in R12_EXECUTORS} <= names
    assert_registry_valid(registry.functions)


# --- the pure condition -------------------------------------------------------------------


def test_lt_fires_below_threshold_and_not_above() -> None:
    assert condition_met("lt", 0.9, 0.71).met is True
    assert condition_met("lt", 0.9, 0.95).met is False


@pytest.mark.parametrize(
    ("operator", "value", "threshold", "expected"),
    [
        ("gt", 25, 20, True),
        ("gte", 20, 20, True),
        ("lte", 20, 20, True),
        ("eq", 5, 5, True),
        ("eq", 5, 6, False),
    ],
)
def test_every_non_delta_operator(operator, value, threshold, expected) -> None:
    assert condition_met(operator, threshold, value).met is expected


def test_delta_operator_needs_a_previous_value() -> None:
    with pytest.raises(ConditionUnavailable):
        condition_met("delta_gt", 5, 100)
    result = condition_met("delta_gt", 5, 100, previous_value=90)
    assert result.met is True


def test_unknown_operator_is_unavailable_not_false() -> None:
    with pytest.raises(ConditionUnavailable):
        condition_met("startswith", 1, 1)


def test_render_rule_sentence_matches_the_contracts_own_example() -> None:
    window = TimeWindow(length=2, unit=TimeWindowUnit.DAY)
    sentence = render_rule_sentence("supplier__on_time_rate_30d", "lt", 0.9, window)
    assert sentence == "supplier__on_time_rate_30d < 0.9 for 2 days"


def test_render_rule_sentence_with_no_window() -> None:
    assert render_rule_sentence("k", "gt", 20, None) == "k > 20"


def test_requires_previous_period() -> None:
    assert requires_previous_period("delta_gt") is True
    assert requires_previous_period("delta_lt") is True
    assert requires_previous_period("gt") is False


@pytest.mark.parametrize(
    ("unit", "length", "as_of", "expected"),
    [
        (TimeWindowUnit.DAY, 2, "2026-09-07", "2026-09-05"),
        (TimeWindowUnit.WEEK, 1, "2026-09-07", "2026-08-31"),
        (TimeWindowUnit.MONTH, 1, "2026-09-07", "2026-08-07"),
        (TimeWindowUnit.MONTH, 1, "2026-03-31", "2026-02-28"),
        (TimeWindowUnit.QUARTER, 1, "2026-09-07", "2026-06-07"),
        (TimeWindowUnit.YEAR, 1, "2026-09-07", "2025-09-07"),
    ],
)
def test_shift_date(unit, length, as_of, expected) -> None:
    from datetime import date

    window = TimeWindow(length=length, unit=unit)
    assert shift_date(date.fromisoformat(as_of), window).isoformat() == expected


# --- creation-time validation (the contract's own 422 example) --------------------------


def test_scalar_kpi_is_accepted() -> None:
    validate_alert_target(_SCALAR_KPI)  # does not raise


def test_rowset_kpi_is_refused_kpi_not_scalar() -> None:
    with pytest.raises(AlertDefinitionRejected) as excinfo:
        validate_alert_target(_ROWSET_KPI)
    assert excinfo.value.code == "kpi_not_scalar"
    assert "rowset" in str(excinfo.value)


def test_missing_kpi_is_refused_kpi_not_found() -> None:
    with pytest.raises(AlertDefinitionRejected) as excinfo:
        validate_alert_target(None)
    assert excinfo.value.code == "kpi_not_found"
