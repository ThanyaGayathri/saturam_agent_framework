"""R5 without a warehouse -- the binder, the result-column convention, the statements, health,
the executors' registry conformance, and the nightly chain's shape.

Everything that touches Postgres is exercised by cookbook/94_retail/R05_gold_materialisation.py
against the real warehouse; what is pinned here is every rule that can be wrong without one.
"""

from __future__ import annotations

import inspect
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from egpt.workflow.types import OnError

from egpt_retail.api.models._generated import KpiHealth
from egpt_retail.app.components import (
    LANDING_TO_SILVER_WORKFLOW_ID,
    NIGHTLY_REFRESH_WORKFLOW_ID,
    build_registry,
    build_workflows,
)
from egpt_retail.app.db import build_control_plane_db
from egpt_retail.app.settings import RetailSettings
from egpt_retail.app.sql_guard import WarehouseReadAllowlist, build_warehouse_read_guard
from egpt_retail.contracts.fixture_builder import CONTRACT_FIXTURE_PATH
from egpt_retail.contracts.kpi import (
    Feasibility,
    FeasibilityVerdict,
    KpiDefinition,
    KpiParameter,
    ParameterKind,
    ResultShape,
)
from egpt_retail.contracts.semantic_stub import StubSemanticRead
from egpt_retail.executors.gold import GOLD_EXECUTORS
from egpt_retail.executors.preflight import gold_fresh_check
from egpt_retail.executors.registry import check_registry
from egpt_retail.executors.warehouse import WAREHOUSE_EXECUTORS
from egpt_retail.warehouse.gold import (
    FAILED,
    MATERIALISED,
    SKIPPED,
    FormulaBindError,
    bind_formula,
    classify_result_columns,
    formula_sha256,
    insert_statement,
    probe_statement,
    read_gold_statement,
    skip_reason,
    view_statements,
)
from egpt_retail.warehouse.health import classify_health
from egpt_retail.warehouse.schema import DDL_SCRIPTS, GOLD_TABLES, gold_view

_REGISTRY_DOC = (
    Path(__file__).resolve().parents[1]
    / "egpt_retail"
    / "contracts"
    / "EXECUTOR_REGISTRY.md"
)
AS_OF = date(2026, 9, 9)
SCOPE = KpiParameter("scope", ParameterKind.PREDICATE, "concept filter")
AS_OF_PARAM = KpiParameter("as_of", ParameterKind.SCALAR, "run date")


def _kpi(
    sql: str,
    *,
    grain: str = "overall",
    parameters=(),
    concept=None,
    shape=ResultShape.SCALAR,
    identifier="k",
) -> KpiDefinition:
    return KpiDefinition(
        identifier=identifier,
        name=identifier,
        description="fixture",
        sql=sql,
        grain=grain,
        business_area="sales",
        source_tables=frozenset({"crm_deal"}),
        parameters=tuple(parameters),
        concept=concept,
        result_shape=shape,
    )


# ---------------------------------------------------------------------------
# Binding
# ---------------------------------------------------------------------------


def test_scope_is_interpolated_from_the_concept_filter_in_parentheses():
    bound = bind_formula(
        _kpi(
            "SELECT sum(amount) FROM crm_deal WHERE {scope}",
            parameters=(SCOPE,),
            concept="Deal",
        ),
        as_of=AS_OF,
        scope_filter="stage = 'won' OR stage = 'closed'",
    )
    assert (
        bound.sql
        == "SELECT sum(amount) FROM crm_deal WHERE (stage = 'won' OR stage = 'closed')"
    )
    assert bound.params == {}
    assert any("concept filter" in note for note in bound.notes)


def test_a_blank_scope_binds_true_and_says_why():
    kpi = _kpi(
        "SELECT sum(amount) FROM crm_deal WHERE {scope}",
        parameters=(SCOPE,),
        concept="Deal",
    )
    bound = bind_formula(kpi, as_of=AS_OF, scope_filter=None)
    assert bound.sql == "SELECT sum(amount) FROM crm_deal WHERE TRUE"
    assert "no accepted sql_filter" in " ".join(bound.notes)


def test_as_of_becomes_a_named_driver_bind_not_text():
    """`:as_of`, which sqlglot parses as a placeholder and the guard accepts; never the date
    interpolated as text, and never psycopg's `%(as_of)s`, which sqlglot cannot parse."""
    kpi = _kpi(
        "SELECT count(*) FROM crm_deal WHERE created_at <= {as_of}",
        parameters=(AS_OF_PARAM,),
    )
    bound = bind_formula(kpi, as_of=AS_OF, scope_filter=None)
    assert bound.sql == "SELECT count(*) FROM crm_deal WHERE created_at <= :as_of"
    assert bound.params == {"as_of": AS_OF}
    assert "2026-09-09" not in bound.sql


def test_the_guard_accepts_the_bound_shape():
    """The whole point of `:as_of` over any other rendering."""
    reader = StubSemanticRead.from_fixture(CONTRACT_FIXTURE_PATH)
    guard = build_warehouse_read_guard(reader)
    assert guard.check("SELECT count(*) FROM orders WHERE order_date <= :as_of") == (
        "orders",
    )


@pytest.mark.parametrize(
    "parameters, message",
    [
        (
            (KpiParameter("region", ParameterKind.PREDICATE, "x"),),
            "predicate parameter 'region' has no source",
        ),
        (
            (KpiParameter("threshold", ParameterKind.SCALAR, "x"),),
            "scalar parameter 'threshold' has no value",
        ),
    ],
)
def test_an_unknown_parameter_is_refused_by_name(parameters, message):
    kpi = _kpi(
        "SELECT count(*) FROM crm_deal WHERE {" + parameters[0].name + "}",
        parameters=parameters,
    )
    with pytest.raises(FormulaBindError, match=re.escape(message)):
        bind_formula(kpi, as_of=AS_OF, scope_filter="x = 1")


def test_the_lineage_hash_is_of_the_template_not_the_binding():
    kpi = _kpi(
        "SELECT sum(amount) FROM crm_deal WHERE {scope}",
        parameters=(SCOPE,),
        concept="Deal",
    )
    assert formula_sha256(kpi) == formula_sha256(kpi)
    other = _kpi(
        "SELECT sum(amount) FROM crm_deal WHERE {scope} ",
        parameters=(SCOPE,),
        concept="Deal",
    )
    assert formula_sha256(kpi) != formula_sha256(other)


# ---------------------------------------------------------------------------
# The result-column convention
# ---------------------------------------------------------------------------


def test_overall_returns_exactly_one_column():
    assert classify_result_columns([("sum", 1700)], "overall").error is None
    verdict = classify_result_columns([("supplier_id", 25), ("sum", 1700)], "overall")
    assert verdict.error and "exactly one column" in verdict.error


def test_a_grain_needs_a_key_before_the_value():
    verdict = classify_result_columns([("sum", 1700)], "supplier")
    assert verdict.error and "at least one grain-key column" in verdict.error
    ok = classify_result_columns(
        [("supplier_id", 25), ("region", 25), ("value", 1700)], "supplier"
    )
    assert (
        ok.error is None
        and ok.grain_keys == ("supplier_id", "region")
        and ok.value_column == "value"
    )


def test_unnamed_or_duplicate_keys_are_refused():
    assert (
        "not named"
        in classify_result_columns([("?column?", 25), ("v", 1700)], "supplier").error
    )
    assert (
        "not distinct"
        in classify_result_columns(
            [("a", 25), ("a", 25), ("v", 1700)], "supplier"
        ).error
    )


def test_a_known_non_numeric_value_type_is_refused_by_name_and_unknown_is_left_to_the_cast():
    assert "type date" in classify_result_columns([("max", 1082)], "overall").error
    assert "type text" in classify_result_columns([("x", 25)], "overall").error
    assert classify_result_columns([("x", 99999)], "overall").error is None
    assert classify_result_columns([("x", None)], "overall").error is None


def test_no_columns_is_an_error():
    assert classify_result_columns([], "overall").error


# ---------------------------------------------------------------------------
# The statements
# ---------------------------------------------------------------------------


def test_the_probe_is_a_limit_zero_over_the_formula():
    assert (
        probe_statement("SELECT 1") == "SELECT * FROM (SELECT 1) AS kpi_probe LIMIT 0"
    )


def test_the_insert_upserts_on_the_dimension_and_casts_keys_to_text():
    columns = classify_result_columns([("supplier_id", 25), ("sum", 1700)], "supplier")
    sql = insert_statement("k", "SELECT supplier_id, sum(x) FROM t GROUP BY 1", columns)
    assert "ON CONFLICT (kpi_identifier, as_of, dimension) DO UPDATE" in sql
    assert "jsonb_build_object('supplier_id', CAST(q.\"supplier_id\" AS text))" in sql
    assert 'CAST(q."sum" AS numeric)' in sql
    assert "::" not in sql, "CAST only; `::` collides with :name binds"


def test_overall_writes_an_empty_dimension():
    sql = insert_statement(
        "k", "SELECT 1", classify_result_columns([("x", 23)], "overall")
    )
    assert "CAST('{}' AS jsonb)" in sql


def test_the_view_projects_the_keys_as_columns_as_the_contract_queries_them():
    drop, create = view_statements("supplier__on_time_rate_30d", ("supplier_id",))
    assert drop == "DROP VIEW IF EXISTS gold.kpi_supplier__on_time_rate_30d"
    assert "dimension->>'supplier_id' AS \"supplier_id\"" in create
    assert create.endswith("WHERE kpi_identifier = 'supplier__on_time_rate_30d'")
    assert read_gold_statement("supplier__on_time_rate_30d").startswith(
        "SELECT * FROM gold.kpi_supplier__on_time_rate_30d WHERE as_of = :as_of"
    )


def test_a_kpi_is_skipped_for_the_right_reasons():
    ready = Feasibility("k", FeasibilityVerdict.READY)
    blocked = Feasibility("k", FeasibilityVerdict.BLOCKED, frozenset({"crm_deal"}))
    assert skip_reason(_kpi("SELECT 1 FROM crm_deal"), ready) is None
    assert "blocked" in skip_reason(_kpi("SELECT 1 FROM crm_deal"), blocked)
    assert "listing" in skip_reason(
        _kpi("SELECT 1 FROM crm_deal", shape=ResultShape.ROWSET), ready
    )
    assert skip_reason(_kpi("SELECT 1 FROM crm_deal"), None) is None


# ---------------------------------------------------------------------------
# The allow-list and the DDL
# ---------------------------------------------------------------------------


def test_the_allow_list_is_silver_plus_gold_plus_the_registrys_views():
    reader = StubSemanticRead.from_fixture(CONTRACT_FIXTURE_PATH)
    allowed = WarehouseReadAllowlist(
        reader, ["supplier__on_time_rate_30d"]
    ).allowed_tables()
    assert set(GOLD_TABLES) <= allowed
    assert gold_view("supplier__on_time_rate_30d") in allowed
    assert "orders" in allowed
    guard = build_warehouse_read_guard(
        reader, kpi_identifiers=["supplier__on_time_rate_30d"]
    )
    assert guard.check(
        "SELECT supplier_id, value FROM gold.kpi_supplier__on_time_rate_30d WHERE as_of = '2026-09-06'"
    )
    assert guard.check("SELECT * FROM silver.orders")


def test_a_gold_view_not_in_the_registry_is_refused():
    from egpt.exceptions import SQLGuardRefusal

    reader = StubSemanticRead.from_fixture(CONTRACT_FIXTURE_PATH)
    guard = build_warehouse_read_guard(reader, kpi_identifiers=[])
    with pytest.raises(SQLGuardRefusal):
        guard.check("SELECT * FROM gold.kpi_somebody_elses")
    with pytest.raises(SQLGuardRefusal):
        guard.check("SELECT * FROM landing.raw_row")


def test_gold_ddl_is_declared_after_the_role_and_before_its_grants():
    assert (
        DDL_SCRIPTS.index("065_readonly_role.sql")
        < DDL_SCRIPTS.index("070_gold.sql")
        < DDL_SCRIPTS.index("075_gold_grants.sql")
    )


# ---------------------------------------------------------------------------
# Health -- the contract's KpiHealth, including the two registry-only statuses
# ---------------------------------------------------------------------------

NOW = datetime(2026, 9, 9, 9, 0, tzinfo=timezone.utc)


def _run(status, *, hours_ago=2.0, error=None, reason=None, run_id="run_1"):
    return {
        "run_id": run_id,
        "as_of": AS_OF,
        "status": status,
        "error": error,
        "reason": reason,
        "finished_at": NOW - timedelta(hours=hours_ago),
    }


@pytest.mark.parametrize(
    "latest, materialised, expected",
    [
        (None, None, "never_computed"),
        (_run(MATERIALISED), _run(MATERIALISED), "fresh"),
        (_run(MATERIALISED, hours_ago=40), _run(MATERIALISED, hours_ago=40), "stale"),
        (
            _run(FAILED, error="column amount does not exist", run_id="run_2"),
            _run(MATERIALISED, hours_ago=26),
            "failed",
        ),
        (_run(SKIPPED, reason="blocked: missing orders"), None, "not_scheduled"),
    ],
)
def test_every_status_the_contract_names_is_reachable_and_validates(
    latest, materialised, expected
):
    row = classify_health(
        _kpi("SELECT 1 FROM crm_deal", identifier="k"), latest, materialised, now=NOW
    )
    assert row["status"] == expected
    KpiHealth.model_validate(row)
    if expected == "failed":
        assert row["last_error"] == "column amount does not exist"
        assert row["last_computed_at"] is not None, (
            "a failed run keeps the last good stamp"
        )
        assert row["last_run_id"] == "run_2"
    if expected == "not_scheduled":
        assert "blocked" in row["last_error"]
    if expected in ("fresh", "stale"):
        assert row["as_of"] == "2026-09-09T23:59:59Z"


def test_gold_fresh_names_every_problem_and_passes_only_when_all_fresh():
    kpis = [
        _kpi("SELECT 1 FROM crm_deal", identifier="a"),
        _kpi("SELECT 1 FROM crm_deal", identifier="b"),
    ]
    health = {
        "a": {"status": "fresh", "staleness_hours": 2.0},
        "b": {"status": "failed", "last_error": "boom"},
    }
    check = gold_fresh_check(kpis, health)
    # A stale value fails too -- a mutant that only looked at failed/never/skipped survived
    # the first version of this test.
    stale = gold_fresh_check(
        kpis, {"a": health["a"], "b": {"status": "stale", "staleness_hours": 40.0}}
    )
    assert stale["passed"] is False
    assert "b: stale -- last computed 40.0 h ago" in stale["reason"]
    assert check["passed"] is False and "b: last run failed -- boom" in check["reason"]
    assert (
        gold_fresh_check(kpis, {"a": health["a"], "b": health["a"]})["passed"] is True
    )
    assert (
        "c: never computed"
        in gold_fresh_check([_kpi("SELECT 1 FROM crm_deal", identifier="c")], {})[
            "reason"
        ]
    )
    assert gold_fresh_check([], {})["passed"] is True


# ---------------------------------------------------------------------------
# Executors and the chain
# ---------------------------------------------------------------------------


def test_the_two_executors_satisfy_the_registry_convention_and_are_async():
    assert check_registry(list(GOLD_EXECUTORS)) == ()
    for executor in GOLD_EXECUTORS:
        assert inspect.iscoroutinefunction(executor)
        assert list(inspect.signature(executor).parameters) == ["step_input"]
    assert check_registry(list(WAREHOUSE_EXECUTORS) + list(GOLD_EXECUTORS)) == ()


def test_the_registry_document_lists_both():
    body = _REGISTRY_DOC.read_text(encoding="utf-8")
    for executor in GOLD_EXECUTORS:
        assert f"| `{executor.__name__}` |" in body


def test_build_registry_includes_both():
    names = {f.__name__ for f in build_registry().functions}
    assert {"materialise_gold", "read_gold"} <= names


def test_the_nightly_chain_is_r4s_six_then_gold_every_step_failing_loudly():
    settings = RetailSettings(
        retail_db_url="postgresql+psycopg://nobody:nope@127.0.0.1:1/none"
    )
    workflows = {
        w.id: w for w in build_workflows(settings, build_control_plane_db(settings))
    }
    nightly, landing = (
        workflows[NIGHTLY_REFRESH_WORKFLOW_ID],
        workflows[LANDING_TO_SILVER_WORKFLOW_ID],
    )
    assert [s.name for s in nightly.steps] == [s.name for s in landing.steps] + [
        "materialise-gold"
    ]
    assert all(s.on_error is OnError.fail for s in nightly.steps)
    assert nightly.steps[-2].name == "decide-load-verdict", (
        "the blocking step is the one before gold"
    )
