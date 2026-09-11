"""R12's alert executors are registrable and coexist with the rest -- no database for the
condition logic and the registry shape; the store's validation guard needs no database
either, since it never touches the KPI registry itself.

Mirrors test_r7_executors.py's own pattern. The batch-orchestration tests below
(``evaluate_alerts``) follow ``test_r7_delivery.py``'s own no-database pattern: every
DB-touching name the executor imports (``AlertDefinitionStore``, ``AlertEventStore``,
``build_control_plane_engine``, ``read_gold``, ``awrite_delivery``,
``_fetch_kpi_definitions``) is monkeypatched to an in-memory fake, so the orchestration logic
itself -- the as_of leak, duplicate-fire suppression, per-definition isolation, the run_id
pin-back, read_gold caching -- is exercised without a running Postgres. The end-to-end,
real-database proof that the same scenarios hold against a real warehouse and control plane
is the cookbook, ``cookbook/94_retail/R12_alerts.py``.
"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest
from egpt.workflow.types import StepInput, StepOutput

from egpt_retail.alerts.condition import (
    ConditionUnavailable,
    condition_met,
    render_rule_sentence,
    requires_previous_period,
    shift_date,
)
from egpt_retail.alerts.ddl import _EVENTS_ALERT_ID_AS_OF_UNIQUE_DDL
from egpt_retail.alerts.store import (
    AlertDefinitionRejected,
    AlertDefinitionStore,
    validate_alert_target,
)
from egpt_retail.app.components import R12_EXECUTORS, build_registry
from egpt_retail.contracts.kpi import (
    KpiDefinition,
    ResultShape,
    TimeWindow,
    TimeWindowUnit,
)
from egpt_retail.executors import alerts as alerts_module
from egpt_retail.executors import gold as gold_module
from egpt_retail.executors.alerts import _select_row, evaluate_alerts, write_alert
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


# --- eq's tolerance -- round 2 review, PR #210 -------------------------------------------
# `eq` compared two float() casts of independent NUMERIC columns with bit-for-bit `==`,
# which is why a realistic continuous KPI (a rate, a percentage) essentially never fired.


def test_eq_fires_on_realistic_decimal_values_a_bare_float_cast_would_miss() -> None:
    # The textbook float-arithmetic artifact: two operations that are the same decimal
    # number land on different doubles. This is exactly the gap a `float()` cast of a
    # Postgres NUMERIC opens for a continuous KPI (this module's own worked example,
    # supplier__on_time_rate_30d) -- the bug this test proves fixed is real, not contrived.
    assert 0.1 + 0.2 != 0.3
    assert condition_met("eq", 0.3, 0.1 + 0.2).met is True


def test_eq_still_refuses_a_real_difference_not_just_float_noise() -> None:
    assert condition_met("eq", 0.9, 0.87).met is False


def test_eq_tolerance_does_not_swallow_a_meaningful_integer_difference() -> None:
    # A pure count/integer KPI (eq's other intended use) is unaffected: the tolerance is
    # many orders of magnitude below "1 count".
    assert condition_met("eq", 5, 6).met is False
    assert condition_met("eq", 5, 5).met is True


# --- _select_row's grain_column (round 2 review, PR #210) --------------------------------
# A gold row can carry more than one grain-key column (`region` and `channel`); a
# `grain_value` that happens to equal both must never silently select the wrong one.

_TWO_GRAIN_COLUMN_ROWS: List[Dict[str, Any]] = [
    {
        "as_of": "2026-09-10",
        "value": 1,
        "computed_at": "c",
        "source_snapshot": "s",
        "run_id": "r",
        "region": "5",
        "channel": "9",
    },
    {
        "as_of": "2026-09-10",
        "value": 2,
        "computed_at": "c",
        "source_snapshot": "s",
        "run_id": "r",
        "region": "9",
        "channel": "5",
    },
]


def test_select_row_refuses_to_guess_when_two_columns_could_match() -> None:
    assert _select_row(_TWO_GRAIN_COLUMN_ROWS, {"grain_value": "5"}) is None


def test_select_row_uses_the_named_grain_column_not_a_coincidental_match() -> None:
    by_region = _select_row(
        _TWO_GRAIN_COLUMN_ROWS, {"grain_value": "5", "grain_column": "region"}
    )
    assert by_region is not None and by_region["value"] == 1

    by_channel = _select_row(
        _TWO_GRAIN_COLUMN_ROWS, {"grain_value": "5", "grain_column": "channel"}
    )
    assert by_channel is not None and by_channel["value"] == 2


def test_select_row_matches_the_lone_grain_column_when_none_is_named() -> None:
    rows = [
        {
            "as_of": "2026-09-10",
            "value": 1,
            "computed_at": "c",
            "source_snapshot": "s",
            "run_id": "r",
            "region": "east",
        },
        {
            "as_of": "2026-09-10",
            "value": 2,
            "computed_at": "c",
            "source_snapshot": "s",
            "run_id": "r",
            "region": "west",
        },
    ]
    picked = _select_row(rows, {"grain_value": "west"})
    assert picked is not None and picked["value"] == 2


def test_select_row_with_an_unnamed_grain_column_and_no_grain_value_still_refuses() -> (
    None
):
    assert _select_row(_TWO_GRAIN_COLUMN_ROWS, {}) is None
    assert _select_row(_TWO_GRAIN_COLUMN_ROWS, None) is None


# --- the row-matching helper alerts.py and dashboards/data.py now share (round 2, #210) ---


def test_alerts_grain_key_columns_is_the_shared_gold_helper_not_a_copy() -> None:
    assert alerts_module.grain_key_columns is gold_module.grain_key_columns


def test_dashboards_row_for_grain_delegates_to_the_shared_helper() -> None:
    from egpt_retail.dashboards.data import _row_for_grain
    from egpt_retail.executors.gold import row_for_grain_value

    rows = [
        {
            "as_of": "d",
            "value": 1,
            "computed_at": "c",
            "source_snapshot": "s",
            "run_id": "r",
            "region": "east",
        }
    ]
    assert _row_for_grain(rows, "east") == row_for_grain_value(rows, "east")


# --- alert_events' duplicate-fire guard, at the DDL level (round 2 review, PR #210) -------


def test_alert_events_ddl_has_a_unique_index_on_alert_id_and_as_of() -> None:
    assert "UNIQUE INDEX" in _EVENTS_ALERT_ID_AS_OF_UNIQUE_DDL
    assert "(alert_id, as_of)" in _EVENTS_ALERT_ID_AS_OF_UNIQUE_DDL


# --- AlertDefinitionStore.update (round 2 review, PR #210) --------------------------------
# No table has ever existed for this store to run real SQL against in this file (the module
# docstring's own "no database" rule) -- `update()`'s SQL execution is faked exactly the way
# test_r7_delivery.py fakes `awrite_delivery`, so what is actually under test is the
# re-validation gating, not SQLAlchemy.


class _FakeUpdateConnection:
    def __init__(self, calls: List[Tuple[str, Dict[str, Any]]]) -> None:
        self._calls = calls

    def execute(self, statement: Any, params: Dict[str, Any]) -> Any:
        self._calls.append((str(statement), params))

        class _Result:
            rowcount = 1

        return _Result()


class _FakeUpdateEngine:
    """Stands in for the real ``Engine`` -- records every statement ``update()`` runs, needs
    no real connection."""

    def __init__(self) -> None:
        self.calls: List[Tuple[str, Dict[str, Any]]] = []

    def begin(self) -> Any:
        calls = self.calls

        class _Ctx:
            def __enter__(self_inner) -> _FakeUpdateConnection:
                return _FakeUpdateConnection(calls)

            def __exit__(self_inner, *exc: Any) -> bool:
                return False

        return _Ctx()


def test_update_with_no_kpi_change_does_not_revalidate(monkeypatch) -> None:
    engine = _FakeUpdateEngine()
    store = AlertDefinitionStore(engine)
    monkeypatch.setattr(store, "get", lambda id: {"id": id, "threshold": 0.5})

    called: List[Any] = []
    monkeypatch.setattr(
        "egpt_retail.alerts.store.validate_alert_target",
        lambda kpi: called.append(kpi),
    )

    result = store.update("alr_x", threshold=0.5)

    assert called == []
    assert engine.calls, "a threshold-only update must still run an UPDATE"
    assert result == {"id": "alr_x", "threshold": 0.5}


def test_update_with_kpi_identifier_but_no_kpi_is_a_caller_error() -> None:
    store = AlertDefinitionStore(_FakeUpdateEngine())
    with pytest.raises(ValueError):
        store.update("alr_x", kpi_identifier=_SCALAR_KPI.identifier)


def test_update_refuses_retargeting_to_a_rowset_kpi(monkeypatch) -> None:
    store = AlertDefinitionStore(_FakeUpdateEngine())
    with pytest.raises(AlertDefinitionRejected) as excinfo:
        store.update("alr_x", kpi_identifier=_ROWSET_KPI.identifier, kpi=_ROWSET_KPI)
    assert excinfo.value.code == "kpi_not_scalar"


def test_update_revalidates_and_succeeds_when_retargeting_to_a_scalar_kpi(
    monkeypatch,
) -> None:
    engine = _FakeUpdateEngine()
    store = AlertDefinitionStore(engine)
    monkeypatch.setattr(
        store, "get", lambda id: {"id": id, "kpi_identifier": _SCALAR_KPI.identifier}
    )

    result = store.update(
        "alr_x", kpi_identifier=_SCALAR_KPI.identifier, kpi=_SCALAR_KPI
    )

    assert engine.calls
    assert result == {"id": "alr_x", "kpi_identifier": _SCALAR_KPI.identifier}


def test_update_with_no_fields_given_is_a_no_op_read(monkeypatch) -> None:
    engine = _FakeUpdateEngine()
    store = AlertDefinitionStore(engine)
    monkeypatch.setattr(store, "get", lambda id: {"id": id})

    result = store.update("alr_x")

    assert engine.calls == []
    assert result == {"id": "alr_x"}


# --- evaluate_alerts, the batch orchestrator (round 2 review, PR #210) --------------------
# Every DB-touching name it imports is faked; see the module docstring at the top of this
# file for why.


def _definition(
    *,
    id: str,
    kpi_identifier: str = "r12_test__kpi",
    operator: str = "gt",
    threshold: float = 1.0,
    window: Optional[Dict[str, Any]] = None,
    scope: Optional[Dict[str, Any]] = None,
    recipients: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    return {
        "id": id,
        "name": id,
        "kpi_identifier": kpi_identifier,
        "operator": operator,
        "threshold": threshold,
        "window": window,
        "scope": scope,
        "severity": "warning",
        "recipients": recipients if recipients is not None else [],
        "recommended_actions": [],
        "enabled": True,
        "use_case_id": None,
        "process_id": None,
        "created_at": None,
        "last_evaluated_at": None,
        "last_fired_at": None,
    }


class _FakeDefinitionStore:
    def __init__(self, definitions: List[Dict[str, Any]]) -> None:
        self._definitions = definitions
        self.marked: List[Tuple[str, bool]] = []

    async def alist(self, *, enabled: Optional[bool] = None) -> List[Dict[str, Any]]:
        return list(self._definitions)

    async def amark_evaluated(self, id: str, *, at: Any, fired: bool) -> None:
        self.marked.append((id, fired))


class _FakeEngine:
    def dispose(self) -> None:
        pass


def _fake_event_store_factory(shared_table: Dict[Tuple[str, Any], Dict[str, Any]]):
    class _FakeEventStore:
        def __init__(self, engine: Any) -> None:
            self.engine = engine

        async def acreate_or_get(
            self, event: Dict[str, Any]
        ) -> Tuple[Dict[str, Any], bool]:
            key = (event["alert_id"], event.get("as_of"))
            if key in shared_table:
                return shared_table[key], False
            row = {**event, "id": event.get("id") or f"aev_fake_{len(shared_table)}"}
            shared_table[key] = row
            return row, True

    return _FakeEventStore


class _RecordingReadGold:
    """Fakes ``read_gold``: rows keyed by ``(kpi_identifier, as_of)``, ``as_of=None`` meaning
    "no as_of was set" -- and every call recorded, so a test can assert exactly which
    ``(kpi_identifier, as_of)`` pairs were actually read."""

    def __init__(
        self, rows_by_key: Dict[Tuple[str, Optional[str]], List[Dict[str, Any]]]
    ) -> None:
        self._rows_by_key = rows_by_key
        self.calls: List[Tuple[str, Optional[str]]] = []

    async def __call__(self, step_input: StepInput) -> StepOutput:
        additional_data = step_input.additional_data or {}
        kpi_identifier = additional_data["kpi_identifiers"][0]
        as_of = additional_data.get("as_of")
        self.calls.append((kpi_identifier, as_of))
        rows = self._rows_by_key.get((kpi_identifier, as_of), [])
        return StepOutput(
            content={"kpis": {kpi_identifier: {"rows": rows}}, "missing": []},
            success=True,
        )


def _patch_infra(
    monkeypatch,
    *,
    definitions: List[Dict[str, Any]],
    kpis: List[KpiDefinition],
    rows_by_key: Dict[Tuple[str, Optional[str]], List[Dict[str, Any]]],
    event_table: Optional[Dict[Tuple[str, Any], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    definition_store = _FakeDefinitionStore(definitions)
    event_table = event_table if event_table is not None else {}
    read_gold_fake = _RecordingReadGold(rows_by_key)
    delivered: List[Dict[str, Any]] = []
    engines_built: List[Any] = []

    async def fake_awrite_delivery(engine: Any, **kwargs: Any) -> None:
        delivered.append(kwargs)

    def fake_build_control_plane_engine(settings: Any) -> Any:
        engine = _FakeEngine()
        engines_built.append(engine)
        return engine

    monkeypatch.setattr(
        alerts_module, "AlertDefinitionStore", lambda engine: definition_store
    )
    monkeypatch.setattr(
        alerts_module, "AlertEventStore", _fake_event_store_factory(event_table)
    )
    monkeypatch.setattr(
        alerts_module, "build_control_plane_engine", fake_build_control_plane_engine
    )
    monkeypatch.setattr(alerts_module, "read_gold", read_gold_fake)
    monkeypatch.setattr(alerts_module, "awrite_delivery", fake_awrite_delivery)
    monkeypatch.setattr(
        alerts_module,
        "_fetch_kpi_definitions",
        lambda settings, identifiers: {kpi.identifier: kpi for kpi in kpis},
    )

    return {
        "definition_store": definition_store,
        "event_table": event_table,
        "read_gold": read_gold_fake,
        "delivered": delivered,
        "engines_built": engines_built,
    }


_TEST_KPI = KpiDefinition(
    identifier="r12_test__kpi",
    name="Test KPI",
    description="A scalar KPI for evaluate_alerts' own unit tests.",
    sql="SELECT 1",
    grain="overall",
    business_area="supply_chain",
    source_tables=frozenset({"whatever"}),
)


def _row(as_of: str, value: float) -> Dict[str, Any]:
    return {
        "as_of": as_of,
        "value": value,
        "computed_at": f"{as_of}T00:00:00Z",
        "source_snapshot": "s1",
        "run_id": "seed",
    }


def test_evaluate_alerts_as_of_does_not_leak_across_definitions(monkeypatch) -> None:
    """A delta_* definition shifts `as_of` back for its own previous-period read; the next
    definition sharing the same KPI must still see the caller's own `as_of`, not the shifted
    one -- round 1 review, id=3988588573."""
    delta_def = _definition(
        id="alr_delta",
        operator="delta_gt",
        threshold=1.0,
        window={"length": 1, "unit": "day"},
    )
    current_def = _definition(id="alr_current", operator="gt", threshold=1.0)
    fixtures = _patch_infra(
        monkeypatch,
        definitions=[delta_def, current_def],
        kpis=[_TEST_KPI],
        rows_by_key={
            ("r12_test__kpi", "2026-09-10"): [_row("2026-09-10", 10.0)],
            ("r12_test__kpi", "2026-09-09"): [_row("2026-09-09", 3.0)],
        },
    )

    output = asyncio.run(
        evaluate_alerts(StepInput(additional_data={"as_of": "2026-09-10"}))
    )

    current_entry = next(
        e for e in output.content["evaluated"] if e["alert_id"] == "alr_current"
    )
    assert current_entry["value"] == 10.0, (
        "alr_current must read the caller's own as_of (value 10.0), not alr_delta's "
        "shifted prior-period as_of (which would read value 3.0)"
    )
    assert current_entry["fired"] is True

    # And: read_gold is cached per (kpi_identifier, as_of) -- only the two distinct pairs
    # this batch actually needs are ever read, regardless of how many definitions share them
    # (round 1 review, id=3988588620).
    assert sorted(fixtures["read_gold"].calls) == [
        ("r12_test__kpi", "2026-09-09"),
        ("r12_test__kpi", "2026-09-10"),
    ]


def test_evaluate_alerts_isolates_a_malformed_recipient(monkeypatch) -> None:
    """A recipient missing `channel` must not abort the batch -- the definition after it
    still evaluates, fires and records normally (round 1 review, id=3988588581)."""
    bad_def = _definition(
        id="alr_bad_recipient",
        recipients=[{"address": "no-channel-here"}],
    )
    good_def = _definition(
        id="alr_good", recipients=[{"channel": "in_app", "address": ""}]
    )
    fixtures = _patch_infra(
        monkeypatch,
        definitions=[bad_def, good_def],
        kpis=[_TEST_KPI],
        rows_by_key={("r12_test__kpi", None): [_row("2026-09-10", 10.0)]},
    )

    output = asyncio.run(evaluate_alerts(StepInput(additional_data={})))

    fired_ids = {e["alert_id"] for e in output.content["evaluated"] if e["fired"]}
    assert fired_ids == {"alr_bad_recipient", "alr_good"}
    assert dict(fixtures["definition_store"].marked) == {
        "alr_bad_recipient": True,
        "alr_good": True,
    }
    # Both alerts still got their alert_events row -- only the bad recipient's own delivery
    # failed, isolated by asyncio.gather(..., return_exceptions=True) (id=3988588614).
    assert {"alr_bad_recipient", "alr_good"} <= {
        row["alert_id"] for row in fixtures["event_table"].values()
    }
    # Only the good definition's recipient was actually delivered to.
    assert len(fixtures["delivered"]) == 1


def test_evaluate_alerts_does_not_re_fire_or_re_deliver_a_duplicate_window(
    monkeypatch,
) -> None:
    """Two evaluations of the same still-breaching definition for the same as_of must
    produce one alert_events row and page recipients once, not twice (round 1 review,
    id=3988588585 / id=3988649689)."""
    definition = _definition(
        id="alr_dup", recipients=[{"channel": "in_app", "address": ""}]
    )
    shared_event_table: Dict[Tuple[str, Any], Dict[str, Any]] = {}
    rows_by_key = {("r12_test__kpi", None): [_row("2026-09-10", 10.0)]}

    fixtures_1 = _patch_infra(
        monkeypatch,
        definitions=[definition],
        kpis=[_TEST_KPI],
        rows_by_key=rows_by_key,
        event_table=shared_event_table,
    )
    asyncio.run(evaluate_alerts(StepInput(additional_data={})))
    assert len(fixtures_1["delivered"]) == 1
    assert len(shared_event_table) == 1

    fixtures_2 = _patch_infra(
        monkeypatch,
        definitions=[definition],
        kpis=[_TEST_KPI],
        rows_by_key=rows_by_key,
        event_table=shared_event_table,
    )
    second_output = asyncio.run(evaluate_alerts(StepInput(additional_data={})))

    assert len(shared_event_table) == 1, (
        "no second alert_events row for the same window"
    )
    assert fixtures_2["delivered"] == [], "recipients are not paged a second time"
    entry = next(
        e for e in second_output.content["evaluated"] if e["alert_id"] == "alr_dup"
    )
    assert entry["fired"] is True, "the condition still honestly reports as met"
    assert second_output.content["events"] == [], "but nothing NEW happened this pass"


def test_evaluate_alerts_pins_its_run_id_fallback_back_into_additional_data(
    monkeypatch,
) -> None:
    """record_provenance, chained after this step in the alert-evaluation workflow, reads
    the same additional_data dict for its own run_id fallback -- if evaluate_alerts never
    writes its minted fallback back, the two steps mint two different ids and
    alert_events.run_id/provenance_ref disagree with the persisted ProvenanceRecord (round 1
    review, id=3988588589)."""
    _patch_infra(
        monkeypatch,
        definitions=[_definition(id="alr_solo")],
        kpis=[_TEST_KPI],
        rows_by_key={("r12_test__kpi", None): [_row("2026-09-10", 0.0)]},
    )
    step_input = StepInput(additional_data={})

    output = asyncio.run(evaluate_alerts(step_input))

    assert step_input.additional_data is not None
    assert step_input.additional_data["run_id"] == output.content["run_id"]


def test_evaluate_alerts_reuses_one_control_plane_engine_for_the_whole_batch(
    monkeypatch,
) -> None:
    """write_alert and delivery must reuse the one engine evaluate_alerts already holds
    open, not build (and dispose) a fresh one per fired alert (round 1 review,
    id=3988588606 / id=3988651251)."""
    fixtures = _patch_infra(
        monkeypatch,
        definitions=[
            _definition(
                id="alr_one", recipients=[{"channel": "in_app", "address": ""}]
            ),
            _definition(
                id="alr_two", recipients=[{"channel": "in_app", "address": ""}]
            ),
        ],
        kpis=[_TEST_KPI],
        rows_by_key={("r12_test__kpi", None): [_row("2026-09-10", 10.0)]},
    )

    asyncio.run(evaluate_alerts(StepInput(additional_data={})))

    assert len(fixtures["engines_built"]) == 1


def test_write_alert_does_not_build_an_engine_when_one_is_given(monkeypatch) -> None:
    """The standalone-call path (no engine given) is unaffected; a caller that does pass one
    must never trigger a fresh build_control_plane_engine call."""

    def _explode(settings: Any) -> Any:
        raise AssertionError(
            "write_alert must not build its own engine when one is given"
        )

    monkeypatch.setattr(alerts_module, "build_control_plane_engine", _explode)
    event_table: Dict[Tuple[str, Any], Dict[str, Any]] = {}
    monkeypatch.setattr(
        alerts_module, "AlertEventStore", _fake_event_store_factory(event_table)
    )

    output = asyncio.run(
        write_alert(
            StepInput(
                additional_data={
                    "alert_id": "alr_engine_reuse",
                    "severity": "info",
                    "control_plane_engine": _FakeEngine(),
                },
                previous_step_content={
                    "fired": True,
                    "kpi_identifier": "r12_test__kpi",
                    "operator": "gt",
                    "threshold": 1.0,
                    "value": 5.0,
                    "as_of": "2026-09-10",
                },
            )
        )
    )

    assert output.content["newly_fired"] is True
    assert "alr_engine_reuse" in {row["alert_id"] for row in event_table.values()}
