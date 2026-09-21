"""R5 without a warehouse -- the binder, the result-column convention, the statements, health,
the executors' registry conformance, and the nightly chain's shape.

Everything that touches Postgres is exercised by cookbook/94_retail/R05_gold_materialisation.py
against the real warehouse; what is pinned here is every rule that can be wrong without one.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from egpt.exceptions import SQLGuardRefusal
from egpt.workflow.types import OnError, StepInput

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
from egpt_retail.executors import gold as gold_module
from egpt_retail.executors.gold import GOLD_EXECUTORS, read_gold
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
    read_gold_history_statement,
    read_gold_statement,
    skip_reason,
    view_statements,
)
from egpt_retail.warehouse.health import classify_health
from egpt_retail.warehouse.schema import (
    DDL_SCRIPTS,
    GOLD_SCHEMA,
    GOLD_TABLES,
    gold_view,
)

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
    assert read_gold_statement("supplier__on_time_rate_30d") == (
        "SELECT * FROM gold.kpi_supplier__on_time_rate_30d WHERE as_of = :as_of "
        "ORDER BY kpi_supplier__on_time_rate_30d"
    )


def test_read_gold_statement_orders_by_the_whole_row_not_just_as_of():
    """``ORDER BY 1`` alone is a no-op once ``WHERE as_of = :as_of`` fixes column 1 to a
    single value -- a KPI at a non-``overall`` grain has more than one row per ``as_of``, and
    without a real order the rows come back in whatever order the query plan picks (confirmed
    against a real warehouse: the same data comes back in a different order under a forced
    sequential scan than under the index scan Postgres otherwise prefers). Ordering by the row
    itself (Postgres's default whole-row record comparison, referencing the view by its own
    unqualified name as the implicit range-table alias) is deterministic regardless of grain,
    which is also why this stays checkable without a warehouse: the statement text is the same
    whether the KPI has zero, one or five grain keys."""
    statement = read_gold_statement("kpi_with_two_dims")
    assert statement == (
        "SELECT * FROM gold.kpi_kpi_with_two_dims WHERE as_of = :as_of "
        "ORDER BY kpi_kpi_with_two_dims"
    )
    assert "ORDER BY 1" not in statement


def test_read_gold_history_statement_bounds_the_window_on_both_ends():
    """The history a trend or delta is read from: the same view and the same whole-row
    ordering as the single-date read, over ``(:since, :as_of]``. Bounded above as well as
    below so a reader pinned to an older ``as_of`` never sees rows materialised after it."""
    statement = read_gold_history_statement("supplier__on_time_rate_30d")
    assert statement == (
        "SELECT * FROM gold.kpi_supplier__on_time_rate_30d "
        "WHERE as_of > :since AND as_of <= :as_of "
        "ORDER BY kpi_supplier__on_time_rate_30d"
    )
    assert "ORDER BY 1" not in statement


# ---------------------------------------------------------------------------
# _as_of / _validate_as_of -- the as_of bound (round-2 review, PR #300): a caller-supplied
# as_of in the future, or more than `_AS_OF_MAX_LOOKBACK_DAYS` in the past, is refused before
# it ever reaches gold. Pure, no engine needed.
# ---------------------------------------------------------------------------


def test_as_of_defaults_to_today_utc_when_absent():
    assert gold_module._as_of({}) == datetime.now(timezone.utc).date()


def test_as_of_rejects_a_future_date():
    with pytest.raises(ValueError, match="in the future"):
        gold_module._as_of({"as_of": "2099-01-01"})


def test_as_of_rejects_a_date_further_back_than_the_lookback_floor():
    too_old = datetime.now(timezone.utc).date() - timedelta(
        days=gold_module._AS_OF_MAX_LOOKBACK_DAYS + 1
    )
    with pytest.raises(ValueError, match="in the past"):
        gold_module._as_of({"as_of": too_old.isoformat()})


def test_as_of_accepts_today_and_the_lookback_floor_boundary():
    today = datetime.now(timezone.utc).date()
    assert gold_module._as_of({"as_of": today.isoformat()}) == today
    floor = today - timedelta(days=gold_module._AS_OF_MAX_LOOKBACK_DAYS)
    assert gold_module._as_of({"as_of": floor.isoformat()}) == floor


def test_validate_as_of_is_the_actual_shared_bound_read_gold_applies_too():
    """Round-2 review: `_as_of`'s docstring used to claim it was the single choke point for
    both `materialise_gold` and `read_gold`, which wasn't true -- `read_gold` parses its own
    `as_of` and defaults it differently (a KPI's own latest materialised date, not today), so
    it cannot call `_as_of` itself, only share its bound via `_validate_as_of`. Pinned here
    directly so a future regression that reintroduces two divergent bounds is caught."""
    today = datetime.now(timezone.utc).date()
    with pytest.raises(ValueError, match="in the future"):
        gold_module._validate_as_of(today + timedelta(days=1))


# ---------------------------------------------------------------------------
# read_gold / _read_history -- exercised with a fake engine, no live warehouse
#
# The pure statement shapes above pin what SQL is sent; these pin what `read_gold` actually
# does with the response -- the guard integration, the history-narrowing option, and the
# error handling around the history branch, none of which the statement-shape tests alone
# can catch a regression in (e.g. `_serialise_gold_row` reuse silently dropping, or a guard
# refusal on the history read losing the current value too).
# ---------------------------------------------------------------------------


class _FakeRows:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self._rows


class _FakeConnection:
    def __init__(self, engine):
        self._engine = engine

    def execute(self, statement, params):
        sql = str(statement)
        bound = dict(params)
        self._engine.calls.append((sql, bound))
        return _FakeRows(self._engine.respond(sql, bound))


class _FakeBegin:
    def __init__(self, engine):
        self._engine = engine

    def __enter__(self):
        return _FakeConnection(self._engine)

    def __exit__(self, *exc_info):
        return False


class _FakeEngine:
    """Stands in for the real ``Engine`` `_fetch_gold_rows` opens: ``respond(sql, params)``
    decides what each ``execute`` call gets back, keyed on whatever the test cares about
    (here, whether ``since`` is bound -- the history statement's own tell)."""

    def __init__(self, respond):
        self.respond = respond
        self.calls: list = []
        self.disposed = False

    def begin(self):
        return _FakeBegin(self)

    def dispose(self):
        self.disposed = True


class _FakeGuard:
    def __init__(self, refuse=None):
        self._refuse = refuse or (lambda statement: False)

    def check(self, statement):
        if self._refuse(statement):
            raise SQLGuardRefusal("refused for test", check="test", statement=statement)
        return ("gold.kpi_k1",)


def _patch_read_gold(monkeypatch, *, respond, guard=None):
    """Wires `read_gold` to a fake engine and a fake/no-op guard, so its history branch runs
    for real -- guard check, warehouse round-trip, provenance notes, serialisation -- without
    a live Postgres or Neo4j."""
    engine = _FakeEngine(respond)
    monkeypatch.setattr(
        gold_module,
        "RetailSettings",
        lambda: SimpleNamespace(retail_warehouse_readonly_db_url=None),
    )
    monkeypatch.setattr(gold_module, "build_warehouse_engine", lambda settings: engine)

    @contextmanager
    def _reader(settings):
        yield StubSemanticRead.from_fixture(CONTRACT_FIXTURE_PATH)

    monkeypatch.setattr(gold_module, "open_semantic_reader", _reader)
    monkeypatch.setattr(
        gold_module,
        "build_warehouse_read_guard",
        lambda reader, **kwargs: guard or _FakeGuard(),
    )
    return engine


def _row(as_of: date, value: str) -> dict:
    return {
        "kpi_identifier": "k1",
        "as_of": as_of,
        "dimension": {},
        "value": Decimal(value),
        "computed_at": datetime(2026, 9, 11, 2, 0, tzinfo=timezone.utc),
        "run_id": "run_1",
        "source_snapshot": "r1",
    }


CURRENT_ROW = _row(date(2026, 9, 11), "1.00")
HISTORY_ROWS = [_row(date(2026, 9, 10), "0.95"), _row(date(2026, 9, 11), "1.00")]


def test_read_gold_reads_and_serialises_history_like_the_current_row(monkeypatch):
    def respond(sql, params):
        return list(HISTORY_ROWS) if "since" in params else [CURRENT_ROW]

    _patch_read_gold(monkeypatch, respond=respond)
    step_input = StepInput(
        additional_data={
            "kpi_identifiers": ["k1"],
            "as_of": "2026-09-11",
            "history_days": 30,
        }
    )
    output = asyncio.run(read_gold(step_input))
    history = output.content["kpis"]["k1"]["history"]
    assert [row["value"] for row in history] == ["0.95", "1.00"]
    assert all(isinstance(row["value"], str) for row in history), (
        "history rows must reuse _serialise_gold_row -- value as str, not float, or the "
        "binary-rounding bug the DDL's numeric type exists to prevent comes back"
    )

    # The history read is a second real SQL read of the same gold view, so it must note all
    # three of R7's bag on success, same as the current-date read a few lines up in
    # `read_gold` -- not just `note_sql_execution`.
    prov = step_input.additional_data["prov"]
    assert len(prov["sql_executed"]) == 2, (
        "both the current-date read and the history read must note_sql_execution"
    )
    assert all(entry["guard"] == "allowed" for entry in prov["sql_executed"])
    gold_datasets = [
        entry for entry in prov["datasets_read"] if entry["table"] == gold_view("k1")
    ]
    assert len(gold_datasets) == 2, (
        "the history read must note_dataset_read alongside note_sql_execution, same as the "
        "current-date read"
    )
    assert all(entry["schema"] == GOLD_SCHEMA for entry in gold_datasets)
    gold_freshness = [
        entry for entry in prov["freshness"] if entry["table"] == gold_view("k1")
    ]
    assert len(gold_freshness) == 2, (
        "the history read must note_freshness from its rows' computed_at, same as the "
        "current-date read"
    )
    assert all(
        entry["computed_at"] == CURRENT_ROW["computed_at"].isoformat()
        for entry in gold_freshness
    )


def test_read_gold_history_for_narrows_which_identifiers_get_the_second_read(
    monkeypatch,
):
    def respond(sql, params):
        return [CURRENT_ROW]

    engine = _patch_read_gold(monkeypatch, respond=respond)
    step_input = StepInput(
        additional_data={
            "kpi_identifiers": ["k1", "k2"],
            "as_of": "2026-09-11",
            "history_days": 30,
            "history_for": ["k1"],
        }
    )
    output = asyncio.run(read_gold(step_input))
    assert "history" in output.content["kpis"]["k1"]
    assert "history" not in output.content["kpis"]["k2"]
    history_reads = [call for call in engine.calls if "since" in call[1]]
    assert len(history_reads) == 1, (
        "only the identifier in history_for should pay for the second read"
    )


def test_read_gold_history_for_accepts_a_bare_string_as_one_identifier(monkeypatch):
    """``history_for="k1"`` must mean the single identifier ``"k1"``, not the five
    characters of that string -- a caller with one identifier has no reason to know it
    must wrap it in a list, and the docstring itself only promises "a set/list of
    identifiers", not that a bare string is rejected or misread."""

    def respond(sql, params):
        return [CURRENT_ROW]

    engine = _patch_read_gold(monkeypatch, respond=respond)
    step_input = StepInput(
        additional_data={
            "kpi_identifiers": ["k1", "k2"],
            "as_of": "2026-09-11",
            "history_days": 30,
            "history_for": "k1",
        }
    )
    output = asyncio.run(read_gold(step_input))
    assert "history" in output.content["kpis"]["k1"]
    assert "history" not in output.content["kpis"]["k2"]
    history_reads = [call for call in engine.calls if "since" in call[1]]
    assert len(history_reads) == 1, (
        "a bare-string history_for must narrow to the one identifier it names, not to a "
        "frozenset of its characters that matches nothing"
    )


def test_read_gold_history_refusal_empties_history_but_keeps_the_current_value(
    monkeypatch, caplog
):
    def respond(sql, params):
        return [CURRENT_ROW]

    guard = _FakeGuard(refuse=lambda statement: "as_of >" in statement)
    _patch_read_gold(monkeypatch, respond=respond, guard=guard)
    step_input = StepInput(
        additional_data={
            "kpi_identifiers": ["k1"],
            "as_of": "2026-09-11",
            "history_days": 30,
        }
    )
    with caplog.at_level(logging.WARNING):
        output = asyncio.run(read_gold(step_input))
    entry = output.content["kpis"]["k1"]
    assert entry["history"] == []
    assert entry["rows"], "a history-only guard refusal must not lose the current value"
    assert any("history for k1 refused" in message for message in caplog.messages)


def test_read_gold_history_read_failure_empties_history_but_keeps_the_current_value(
    monkeypatch, caplog
):
    def respond(sql, params):
        if "since" in params:
            raise RuntimeError('relation "gold.kpi_k1" does not exist')
        return [CURRENT_ROW]

    _patch_read_gold(monkeypatch, respond=respond)
    step_input = StepInput(
        additional_data={
            "kpi_identifiers": ["k1"],
            "as_of": "2026-09-11",
            "history_days": 30,
        }
    )
    with caplog.at_level(logging.WARNING):
        output = asyncio.run(read_gold(step_input))
    entry = output.content["kpis"]["k1"]
    assert entry["history"] == []
    assert entry["rows"], "a failed history read must not lose the current value"
    assert any("history for k1 failed" in message for message in caplog.messages)


@pytest.mark.parametrize("bad_value", ["not-a-number", -5])
def test_read_gold_ignores_a_malformed_history_days_instead_of_raising(
    monkeypatch, caplog, bad_value
):
    def respond(sql, params):
        return [CURRENT_ROW]

    _patch_read_gold(monkeypatch, respond=respond)
    step_input = StepInput(
        additional_data={
            "kpi_identifiers": ["k1"],
            "as_of": "2026-09-11",
            "history_days": bad_value,
        }
    )
    with caplog.at_level(logging.WARNING):
        output = asyncio.run(read_gold(step_input))
    entry = output.content["kpis"]["k1"]
    assert "history" not in entry
    assert entry["rows"], "a malformed history_days must not fail the whole read"


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
