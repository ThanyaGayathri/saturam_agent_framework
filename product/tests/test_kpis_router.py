"""Unit tests for #229/B1 and #231/B2's DB-free halves: the wire<->domain mappers, the
in-router filtering helper, the scope check that gates `Kpi.sql` visibility, and the
create/update/delete logic -- everything `api/routers/kpis.py` does that needs no Postgres or
Neo4j. The store/feasibility calls against a real registry and semantic graph are exercised
only by `test_kpis_live.py` (gated `EGPT_INTEGRATION_POSTGRES` + `EGPT_INTEGRATION_NEO4J`), the
same split `test_r13_dashboards.py` documents for R13.
"""

from __future__ import annotations

import asyncio
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from egpt_retail.api import models as wire_models
from egpt_retail.api.routers import kpis as kpis_module
from egpt_retail.api.routers.kpis import (
    _carry_forward_validation,
    _derive_identifier,
    _feasibility_out,
    _filter_suggestions,
    _kpi_from_write,
    _kpi_out,
    _list_kpi_suggestions,
    _matches_filters,
    _may_read_sql,
    _slugify,
    _suggestion_notes,
    _SuggestionRegistrySnapshot,
    _validated_business_area,
    _warehouse_engine,
    create_kpi,
    delete_kpi,
    get_kpi,
    get_kpi_feasibility,
    list_kpi_health,
    list_kpi_suggestions,
    list_kpis,
    update_kpi,
)
from egpt_retail.app.settings import RetailSettings
from egpt_retail.contracts.kpi import (
    Feasibility,
    FeasibilityReason,
    FeasibilityVerdict,
    KpiDefinition,
    KpiLevel,
    KpiOrigin,
    KpiParameter,
    ParameterKind,
    TimeWindow,
    TimeWindowUnit,
)
from egpt_retail.contracts.vocabulary import load_vocabulary_set
from egpt_retail.kpi.store import KpiConcurrentUpdateError, KpiDeleteResult
from egpt_retail.suggestions.runs import (
    LOST_RUN_ERROR,
    SuggestionRunPhase,
    SuggestionRunRecord,
    SuggestionRunStatus,
)


def _kpi(**overrides) -> KpiDefinition:
    defaults = dict(
        identifier="rfq__quote_to_deal_rate",
        name="Quote to Deal Rate",
        description="Share of quotes that become deals.",
        sql="SELECT count(*) FROM rfq_quote WHERE brand = {brand}",
        grain="brand",
        business_area="sales",
        source_tables=frozenset({"rfq_quote"}),
        parameters=(KpiParameter(name="brand", kind=ParameterKind.SCALAR),),
        time_window=TimeWindow(length=30, unit=TimeWindowUnit.DAY),
        concept="rfq",
    )
    defaults.update(overrides)
    return KpiDefinition(**defaults)


def _ready_feasibility(identifier: str = "rfq__quote_to_deal_rate") -> Feasibility:
    return Feasibility(kpi_identifier=identifier, verdict=FeasibilityVerdict.READY)


# ---------------------------------------------------------------------------
# _feasibility_out
# ---------------------------------------------------------------------------


def test_feasibility_out_maps_kpi_identifier_to_kpi_id():
    domain = _ready_feasibility("k1")
    wire = _feasibility_out(domain, "snap.7")
    assert wire.kpi_id == "k1"
    assert wire.verdict.value == "ready"
    assert wire.semantic_snapshot == "snap.7"
    assert wire.rationale == domain.rationale


def test_feasibility_out_sorts_missing_tables_and_carries_rationale():
    domain = Feasibility(
        kpi_identifier="k1",
        verdict=FeasibilityVerdict.PARTIAL,
        missing_tables=frozenset({"zeta", "alpha"}),
    )
    wire = _feasibility_out(domain, "snap.7")
    assert wire.missing_tables == ["alpha", "zeta"]
    assert "alpha, zeta" in wire.rationale


def test_feasibility_out_not_joinable_rationale_survives_the_mapping():
    domain = Feasibility(
        kpi_identifier="k1",
        verdict=FeasibilityVerdict.BLOCKED,
        reason=FeasibilityReason.NOT_JOINABLE,
        unjoinable=(("orders", "customers"),),
    )
    wire = _feasibility_out(domain, "snap.7")
    assert "orders and customers" in wire.rationale


# ---------------------------------------------------------------------------
# _kpi_out
# ---------------------------------------------------------------------------


def test_kpi_out_maps_every_field_that_exists_on_the_domain_object():
    kpi = _kpi()
    feasibility = _feasibility_out(_ready_feasibility(kpi.identifier), "snap.7")

    wire = _kpi_out(kpi, feasibility, include_sql=True)

    assert wire.identifier == kpi.identifier
    assert wire.name == kpi.name
    assert wire.description == kpi.description
    assert wire.business_area == kpi.business_area
    assert wire.grain == kpi.grain
    assert wire.concept == kpi.concept
    assert wire.result_shape.value == kpi.result_shape.value
    assert wire.dialect.value == kpi.dialect.value
    assert wire.source_tables == sorted(kpi.source_tables)
    assert [p.name for p in wire.parameters] == [p.name for p in kpi.parameters]
    assert wire.time_window.length == kpi.time_window.length
    assert wire.feasibility is feasibility


def test_kpi_out_id_defaults_to_identifier_no_schema_for_a_separate_id_yet():
    kpi = _kpi()
    feasibility = _feasibility_out(_ready_feasibility(kpi.identifier), "snap.7")
    wire = _kpi_out(kpi, feasibility, include_sql=True)
    assert wire.id == kpi.identifier


def test_kpi_out_presentation_fields_default_sensibly_when_unset():
    # A KPI built with none of B2's presentation fields set: KpiDefinition's own field
    # defaults apply (active=True, source=CUSTOMER), and the router fills the rest in as
    # empty/None -- these are real values now (#231/B2), not the B1-era hardcoded stand-ins.
    kpi = _kpi()
    feasibility = _feasibility_out(_ready_feasibility(kpi.identifier), "snap.7")
    wire = _kpi_out(kpi, feasibility, include_sql=True)

    assert wire.subject_area == ""
    assert wire.level is None
    assert wire.unit == ""
    assert wire.industry_benchmark == ""
    assert wire.active is True
    assert wire.source.value == "customer"
    assert wire.dataset_ids is None
    assert wire.process_ids is None
    assert wire.updated_at is None


def test_kpi_out_maps_every_b2_presentation_field_when_set():
    kpi = _kpi(
        subject_area="Replenishment",
        level=KpiLevel.OPERATIONAL,
        unit="%",
        industry_benchmark=">95%",
        active=False,
        source=KpiOrigin.SHIPPED,
        dataset_ids=("ds_1", "ds_2"),
        process_ids=("sc_1",),
    )
    feasibility = _feasibility_out(_ready_feasibility(kpi.identifier), "snap.7")
    wire = _kpi_out(kpi, feasibility, include_sql=True)

    assert wire.subject_area == "Replenishment"
    assert wire.level.value == "operational"
    assert wire.unit == "%"
    assert wire.industry_benchmark == ">95%"
    assert wire.active is False
    assert wire.source.value == "shipped"
    assert wire.dataset_ids == ["ds_1", "ds_2"]
    assert wire.process_ids == ["sc_1"]


def test_kpi_out_omits_sql_when_include_sql_is_false():
    kpi = _kpi()
    feasibility = _feasibility_out(_ready_feasibility(kpi.identifier), "snap.7")

    assert _kpi_out(kpi, feasibility, include_sql=True).sql == kpi.sql
    assert _kpi_out(kpi, feasibility, include_sql=False).sql is None


def test_kpi_out_handles_a_kpi_with_no_time_window_or_parameters():
    kpi = _kpi(parameters=(), time_window=None, sql="SELECT count(*) FROM rfq_quote")
    feasibility = _feasibility_out(_ready_feasibility(kpi.identifier), "snap.7")
    wire = _kpi_out(kpi, feasibility, include_sql=True)
    assert wire.parameters == []
    assert wire.time_window is None


# ---------------------------------------------------------------------------
# _matches_filters -- pure, so GET /kpis's query-param filtering is testable directly
# ---------------------------------------------------------------------------


def _wire_kpi(subject_area="", active=True, verdict=FeasibilityVerdict.READY):
    kpi = _kpi()
    feasibility = _feasibility_out(
        Feasibility(kpi_identifier=kpi.identifier, verdict=verdict), "snap.7"
    )
    wire = _kpi_out(kpi, feasibility, include_sql=True)
    wire = wire.model_copy(update={"subject_area": subject_area, "active": active})
    return wire


def test_matches_filters_with_no_filters_set():
    assert _matches_filters(_wire_kpi(), None, None, None) is True


def test_matches_filters_by_subject_area():
    kpi = _wire_kpi(subject_area="Replenishment")
    assert _matches_filters(kpi, "Replenishment", None, None) is True
    assert _matches_filters(kpi, "Something Else", None, None) is False


def test_matches_filters_by_active():
    kpi = _wire_kpi(active=True)
    assert _matches_filters(kpi, None, True, None) is True
    assert _matches_filters(kpi, None, False, None) is False


def test_matches_filters_by_feasibility_verdict():
    import egpt_retail.api.models as wire_models

    kpi = _wire_kpi(verdict=FeasibilityVerdict.BLOCKED)
    assert (
        _matches_filters(kpi, None, None, wire_models.FeasibilityVerdict.blocked)
        is True
    )
    assert (
        _matches_filters(kpi, None, None, wire_models.FeasibilityVerdict.ready) is False
    )


# ---------------------------------------------------------------------------
# _may_read_sql -- same no-op-when-authorization-is-off posture as require_scopes
# ---------------------------------------------------------------------------


class _FakeState:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _FakeRequest:
    def __init__(self, **state_kwargs):
        self.state = _FakeState(**state_kwargs)


def test_may_read_sql_is_true_when_authorization_is_off():
    assert _may_read_sql(_FakeRequest(authorization_enabled=False)) is True


def test_may_read_sql_is_true_with_the_write_scope():
    request = _FakeRequest(authorization_enabled=True, scopes=["kpis:write"])
    assert _may_read_sql(request) is True


def test_may_read_sql_is_false_with_only_the_read_scope():
    request = _FakeRequest(authorization_enabled=True, scopes=["kpis:read"])
    assert _may_read_sql(request) is False


# ---------------------------------------------------------------------------
# list_kpis -- an invalid business_area must 422, not leak the vocabulary's raw
# ValueError as an unhandled 500 (found by manual curl testing, then covered here)
# ---------------------------------------------------------------------------


class _RaisingStore:
    def list(self, *, business_area=None):
        raise ValueError(f"{business_area!r} is not a permitted business_area.")

    def feasibility(self, reader, kpis=None):
        return {}


def test_list_kpis_turns_an_invalid_business_area_into_a_422():
    with pytest.raises(HTTPException) as excinfo:
        list_kpis(
            _FakeRequest(authorization_enabled=False),
            business_area="not_a_real_area",
            store=_RaisingStore(),
        )
    assert excinfo.value.status_code == 422
    assert "not_a_real_area" in excinfo.value.detail


# ---------------------------------------------------------------------------
# _slugify / _derive_identifier -- #231/B2
# ---------------------------------------------------------------------------


def test_slugify_lowercases_and_collapses_punctuation():
    assert _slugify("Supplier Fill Rate") == "supplier_fill_rate"
    assert _slugify("Already_clean") == "already_clean"
    assert _slugify("100% On-Time!!") == "100_on_time"
    assert _slugify("   ") == "kpi"


def _write(**overrides):
    defaults = dict(
        name="Quote to Deal Rate",
        description="Share of quotes that become deals.",
        business_area="sales",
        grain="brand",
        sql="SELECT count(*) FROM rfq_quote",
        source_tables=["rfq_quote"],
    )
    defaults.update(overrides)
    return wire_models.KpiWrite(**defaults)


def test_derive_identifier_uses_the_given_identifier_verbatim():
    body = _write(identifier="custom__id")
    assert _derive_identifier(body) == "custom__id"


def test_derive_identifier_derives_from_concept_and_name():
    body = _write(concept="Supplier", name="Fill Rate")
    assert _derive_identifier(body) == "supplier__fill_rate"


def test_derive_identifier_422s_with_neither_identifier_nor_concept():
    body = _write()
    with pytest.raises(HTTPException) as excinfo:
        _derive_identifier(body)
    assert excinfo.value.status_code == 422


# ---------------------------------------------------------------------------
# _carry_forward_validation -- "a formula edit re-validates and moves the KPI back to
# review", the contract's own words for updateKpi
# ---------------------------------------------------------------------------


def test_carry_forward_validation_is_none_for_a_new_kpi():
    assert _carry_forward_validation(None, "SELECT 1", []) is None


def test_carry_forward_validation_carries_forward_when_formula_unchanged():
    existing = _kpi(validated_against_snapshot="snap.3")
    result = _carry_forward_validation(
        existing, existing.sql, list(existing.source_tables)
    )
    assert result == "snap.3"


def test_carry_forward_validation_resets_when_sql_changes():
    existing = _kpi(validated_against_snapshot="snap.3")
    result = _carry_forward_validation(
        existing, "SELECT 2 FROM rfq_quote", list(existing.source_tables)
    )
    assert result is None


def test_carry_forward_validation_resets_when_source_tables_change():
    existing = _kpi(validated_against_snapshot="snap.3")
    result = _carry_forward_validation(existing, existing.sql, ["other_table"])
    assert result is None


# ---------------------------------------------------------------------------
# _kpi_from_write
# ---------------------------------------------------------------------------


def test_kpi_from_write_maps_required_and_optional_fields():
    body = _write(
        identifier="rfq__x",
        subject_area="Replenishment",
        level=wire_models.KpiLevel.operational,
        unit="%",
        industry_benchmark=">95%",
        active=False,
        dataset_ids=["ds_1"],
        process_ids=["sc_1"],
    )
    kpi = _kpi_from_write(body, identifier="rfq__x", existing=None)
    assert kpi.identifier == "rfq__x"
    assert kpi.subject_area == "Replenishment"
    assert kpi.level.value == "operational"
    assert kpi.unit == "%"
    assert kpi.industry_benchmark == ">95%"
    assert kpi.active is False
    assert kpi.source is KpiOrigin.CUSTOMER
    assert kpi.dataset_ids == ("ds_1",)
    assert kpi.process_ids == ("sc_1",)
    assert kpi.validated_against_snapshot is None


def test_kpi_from_write_defaults_source_to_customer_on_create():
    kpi = _kpi_from_write(
        _write(identifier="rfq__x"), identifier="rfq__x", existing=None
    )
    assert kpi.source is KpiOrigin.CUSTOMER


def test_kpi_from_write_preserves_source_on_update():
    existing = _kpi(source=KpiOrigin.SHIPPED)
    # `sql` matches `existing`'s -- required fields are always fully resent on PATCH, and the
    # carried-forward `parameters` below must stay consistent with whatever `sql` is resent.
    body = _write(sql=existing.sql)
    kpi = _kpi_from_write(body, identifier=existing.identifier, existing=existing)
    assert kpi.source is KpiOrigin.SHIPPED


# ---------------------------------------------------------------------------
# _kpi_from_write -- path-vs-body identifier precedence (#240 review, finding #7): the comment
# in `update_kpi` says `body.identifier` is ignored and the path wins. Nothing exercised that
# invariant before; this makes it enforceable.
# ---------------------------------------------------------------------------


def test_kpi_from_write_ignores_body_identifier_and_uses_the_path_identifier():
    existing = _kpi(identifier="rfq__quote_to_deal_rate")
    body = _write(identifier="a_totally_different_identifier", sql=existing.sql)
    kpi = _kpi_from_write(body, identifier="rfq__quote_to_deal_rate", existing=existing)
    assert kpi.identifier == "rfq__quote_to_deal_rate"


# ---------------------------------------------------------------------------
# _kpi_from_write -- PATCH carries forward omitted optional fields (#240 review, finding #6).
# Before this, every optional presentation field the caller omitted on PATCH was silently
# reset to its create-time default (e.g. `active` back to `True`) rather than left unchanged --
# the PR's own manual test 5, now automated.
# ---------------------------------------------------------------------------


def test_kpi_from_write_carries_forward_omitted_optional_fields_on_update():
    existing = _kpi(
        subject_area="Replenishment",
        level=KpiLevel.OPERATIONAL,
        unit="%",
        industry_benchmark=">95%",
        active=False,
        dataset_ids=("ds_1",),
        process_ids=("sc_1",),
    )
    # `_write(sql=...)` sets none of the optional fields below -- a PATCH body that only
    # resends the required fields (`sql` matching `existing`'s, since the carried-forward
    # `parameters` must stay consistent with whatever `sql` is resent).
    body = _write(sql=existing.sql)
    kpi = _kpi_from_write(body, identifier=existing.identifier, existing=existing)
    assert kpi.subject_area == "Replenishment"
    assert kpi.level is KpiLevel.OPERATIONAL
    assert kpi.unit == "%"
    assert kpi.industry_benchmark == ">95%"
    assert kpi.active is False  # not silently reset to the True default
    assert kpi.dataset_ids == ("ds_1",)
    assert kpi.process_ids == ("sc_1",)


def test_kpi_from_write_carries_forward_time_window_parameters_and_concept_on_update():
    existing = _kpi()  # _kpi()'s own defaults set time_window, parameters and concept
    # `sql` matches `existing`'s so the carried-forward `parameters` stay valid against it.
    body = _write(sql=existing.sql)
    kpi = _kpi_from_write(body, identifier=existing.identifier, existing=existing)
    assert kpi.time_window == existing.time_window
    assert kpi.parameters == existing.parameters
    assert kpi.concept == existing.concept


def test_kpi_from_write_explicit_empty_lists_clear_rather_than_carry_forward():
    # `[]` is a real value the caller sent, not an omission -- distinct from `None`/absent.
    existing = _kpi(dataset_ids=("ds_1",), process_ids=("sc_1",))
    body = _write(dataset_ids=[], process_ids=[], sql=existing.sql)
    kpi = _kpi_from_write(body, identifier=existing.identifier, existing=existing)
    assert kpi.dataset_ids == ()
    assert kpi.process_ids == ()


def test_kpi_from_write_still_applies_create_time_defaults_when_optional_fields_omitted():
    # No `existing` (a create): omitted optional fields take their create-time default, not a
    # carried-forward value that does not exist yet.
    kpi = _kpi_from_write(_write(), identifier="rfq__x", existing=None)
    assert kpi.active is True
    assert kpi.dataset_ids == ()
    assert kpi.process_ids == ()
    assert kpi.subject_area is None


# ---------------------------------------------------------------------------
# create_kpi / update_kpi / delete_kpi -- the error paths that short-circuit before the
# semantic reader (feasibility) is ever opened, so they're DB-free. The success paths and
# delete's dashboard-reference check need a real registry/reader and live in
# test_kpis_live.py, matching the split test_r13_dashboards.py documents for its own
# DashboardValidationError paths.
# ---------------------------------------------------------------------------


class _FakeKpiStore:
    def __init__(self, existing=None):
        self._by_id = {existing.identifier: existing} if existing is not None else {}

    def get(self, identifier):
        return self._by_id.get(identifier)

    def create(self, kpi):
        if kpi.identifier in self._by_id:
            return None
        self._by_id[kpi.identifier] = kpi
        return kpi

    def save_if_unchanged(self, kpi, expected_updated_at):
        current = self._by_id.get(kpi.identifier)
        if current is None or current.updated_at != expected_updated_at:
            raise KpiConcurrentUpdateError(kpi.identifier)
        self._by_id[kpi.identifier] = kpi
        return kpi

    def delete_if_unreferenced(self, identifier, *, caller=None):
        if identifier not in self._by_id:
            return KpiDeleteResult(removed=False)
        del self._by_id[identifier]
        return KpiDeleteResult(removed=True)

    def feasibility(self, reader, kpis=None):
        return {}


def test_create_kpi_refuses_a_duplicate_identifier():
    existing = _kpi(identifier="rfq__quote_to_deal_rate")
    store = _FakeKpiStore(existing=existing)
    body = _write(identifier="rfq__quote_to_deal_rate")
    with pytest.raises(HTTPException) as excinfo:
        create_kpi(body, _FakeRequest(authorization_enabled=False), store=store)
    assert excinfo.value.status_code == 409


def test_create_kpi_turns_a_bad_formula_into_a_422():
    store = _FakeKpiStore(existing=None)
    body = _write(
        identifier="rfq__bad",
        sql="SELECT count(*) FROM rfq_quote WHERE brand = {brand}",
    )
    with pytest.raises(HTTPException) as excinfo:
        create_kpi(body, _FakeRequest(authorization_enabled=False), store=store)
    assert excinfo.value.status_code == 422
    assert "undeclared parameter" in excinfo.value.detail


def test_create_kpi_422s_when_identifier_cannot_be_derived():
    store = _FakeKpiStore(existing=None)
    with pytest.raises(HTTPException) as excinfo:
        create_kpi(_write(), _FakeRequest(authorization_enabled=False), store=store)
    assert excinfo.value.status_code == 422


# ---------------------------------------------------------------------------
# create_kpi / update_kpi -- the concurrency-race findings from the #240 review (findings #1
# and #2). Each fake simulates "the pre-check passed, but the store-level guard that is the
# actual source of truth lost the race", the same shape a real two-connection race produces.
# ---------------------------------------------------------------------------


class _RaceLosingCreateStore(_FakeKpiStore):
    """``get`` says "no such identifier" -- as it would if a concurrent create's INSERT
    hadn't committed yet when this request's pre-check ran -- but ``create`` still loses the
    database's own uniqueness race, simulating two concurrent ``POST /kpis`` for the same
    identifier."""

    def create(self, kpi):
        return None


def test_create_kpi_returns_409_when_it_loses_the_create_race():
    store = _RaceLosingCreateStore(existing=None)
    body = _write(identifier="rfq__quote_to_deal_rate")
    with pytest.raises(HTTPException) as excinfo:
        create_kpi(body, _FakeRequest(authorization_enabled=False), store=store)
    assert excinfo.value.status_code == 409
    assert "rfq__quote_to_deal_rate" in excinfo.value.detail


class _RaceLosingUpdateStore(_FakeKpiStore):
    """``save_if_unchanged`` always refuses, as if another writer had already changed
    ``updated_at`` between this route's ``get`` and its conditional update -- simulating two
    concurrent ``PATCH /kpis/{id}`` calls for the same identifier racing to a lost update."""

    def save_if_unchanged(self, kpi, expected_updated_at):
        raise KpiConcurrentUpdateError(kpi.identifier)


def test_update_kpi_returns_409_when_the_row_changed_underneath_it():
    existing = _kpi(identifier="rfq__quote_to_deal_rate")
    store = _RaceLosingUpdateStore(existing=existing)
    body = _write(active=False, sql=existing.sql)
    with pytest.raises(HTTPException) as excinfo:
        update_kpi(
            "rfq__quote_to_deal_rate",
            body,
            _FakeRequest(authorization_enabled=False),
            store=store,
        )
    assert excinfo.value.status_code == 409
    assert "modified concurrently" in excinfo.value.detail


def test_update_kpi_404s_on_a_missing_identifier():
    store = _FakeKpiStore(existing=None)
    with pytest.raises(HTTPException) as excinfo:
        update_kpi(
            "does_not_exist",
            _write(),
            _FakeRequest(authorization_enabled=False),
            store=store,
        )
    assert excinfo.value.status_code == 404


def test_update_kpi_turns_a_bad_formula_into_a_422():
    existing = _kpi(identifier="rfq__quote_to_deal_rate")
    store = _FakeKpiStore(existing=existing)
    # References a placeholder that is neither declared in this body nor in `existing`'s
    # carried-forward `parameters` -- genuinely undeclared, regardless of carry-forward.
    body = _write(sql="SELECT count(*) FROM rfq_quote WHERE segment = {segment}")
    with pytest.raises(HTTPException) as excinfo:
        update_kpi(
            "rfq__quote_to_deal_rate",
            body,
            _FakeRequest(authorization_enabled=False),
            store=store,
        )
    assert excinfo.value.status_code == 422
    assert "undeclared parameter" in excinfo.value.detail


def test_delete_kpi_404s_on_a_missing_identifier():
    store = _FakeKpiStore(existing=None)
    with pytest.raises(HTTPException) as excinfo:
        delete_kpi(
            "does_not_exist", _FakeRequest(authorization_enabled=False), store=store
        )
    assert excinfo.value.status_code == 404


# ---------------------------------------------------------------------------
# GET /kpis/health
# ---------------------------------------------------------------------------


class _ListableKpiStore(_FakeKpiStore):
    """``_FakeKpiStore`` plus the unfiltered ``list()``/``alist()`` read ``list_kpi_health``
    needs -- mirrors ``_suggestion_registry_snapshot``'s own unfiltered ``store.list()`` call."""

    def list(self, business_area=None):
        return list(self._by_id.values())

    async def alist(self, business_area=None):
        return self.list(business_area=business_area)


async def _fake_akpi_health(kpis, engine, **kwargs):
    """Stands in for a real gold read: one row per KPI, shaped exactly like
    ``classify_health``'s own output (``test_r5_gold.py`` pins that shape)."""
    return [
        {
            "kpi_id": kpi.identifier,
            "identifier": kpi.identifier,
            "status": "fresh",
            "last_computed_at": "2026-09-14T02:20:00Z",
            "as_of": "2026-09-13T23:59:59Z",
            "staleness_hours": 8.5,
            "last_run_id": "run_gold_20260914",
            "last_error": None,
        }
        for kpi in kpis
    ]


def test_list_kpi_health_maps_every_registered_kpi_into_a_wire_kpi_health(monkeypatch):
    monkeypatch.setattr(kpis_module, "akpi_health", _fake_akpi_health)
    kpi = _kpi()
    store = _ListableKpiStore(existing=kpi)

    result = asyncio.run(
        list_kpi_health(
            _FakeRequest(authorization_enabled=False), store=store, engine=object()
        )
    )

    assert len(result) == 1
    row = result[0]
    assert isinstance(row, wire_models.KpiHealth)
    assert row.kpi_id == kpi.identifier
    assert row.identifier == kpi.identifier
    assert row.status == wire_models.KpiHealthStatus.fresh
    assert row.staleness_hours == 8.5
    assert row.last_run_id == "run_gold_20260914"


def test_list_kpi_health_returns_an_empty_list_for_an_empty_registry(monkeypatch):
    monkeypatch.setattr(kpis_module, "akpi_health", _fake_akpi_health)
    store = _ListableKpiStore(existing=None)

    result = asyncio.run(
        list_kpi_health(
            _FakeRequest(authorization_enabled=False), store=store, engine=object()
        )
    )
    assert result == []


def test_warehouse_engine_dependency_503s_when_the_warehouse_is_not_configured(
    monkeypatch,
):
    def _refuse(settings):
        raise RuntimeError("EGPT_RETAIL_WAREHOUSE_DB_URL is not set.")

    monkeypatch.setattr(kpis_module, "build_warehouse_engine", _refuse)

    with pytest.raises(HTTPException) as excinfo:
        next(_warehouse_engine())
    assert excinfo.value.status_code == 503
    assert "EGPT_RETAIL_WAREHOUSE_DB_URL" in excinfo.value.detail


def _health_app(monkeypatch, *, scopes):
    """A real FastAPI app wired to the actual ``kpis.py`` router (not a direct function call),
    so the scope-then-warehouse dependency order below is exercised through FastAPI's own
    ``solve_dependencies`` rather than trusted from reading the signature."""

    def _refuse(settings):
        raise RuntimeError("EGPT_RETAIL_WAREHOUSE_DB_URL is not set.")

    monkeypatch.setattr(kpis_module, "build_warehouse_engine", _refuse)
    monkeypatch.setattr(kpis_module, "akpi_health", _fake_akpi_health)

    app = FastAPI()
    app.include_router(kpis_module.router)
    app.dependency_overrides[kpis_module._kpi_store] = lambda: _ListableKpiStore(
        existing=_kpi()
    )

    @app.middleware("http")
    async def _fake_auth(request, call_next):
        request.state.authorization_enabled = True
        request.state.scopes = scopes
        request.state.admin_scope = None
        return await call_next(request)

    return TestClient(app)


def test_list_kpi_health_403s_before_the_warehouse_503_when_the_scope_is_missing(
    monkeypatch,
):
    """The ordering fix: an unauthorized caller must be refused for that reason, not learn
    that the warehouse is unconfigured before the scope check ever runs."""
    client = _health_app(monkeypatch, scopes=[])
    response = client.get("/kpis/health")
    assert response.status_code == 403


def test_list_kpi_health_503s_for_an_authorized_caller_when_the_warehouse_is_not_configured(
    monkeypatch,
):
    """A caller who does hold the scope still gets the honest 503 -- the fix only reorders
    which check runs first, it doesn't swallow the warehouse-unconfigured case."""
    client = _health_app(monkeypatch, scopes=["kpis:read"])
    response = client.get("/kpis/health")
    assert response.status_code == 503
    assert "EGPT_RETAIL_WAREHOUSE_DB_URL" in response.json()["detail"]


async def _fake_akpi_health_with_error(kpis, engine, **kwargs):
    """Same shape as `_fake_akpi_health`, but with the failed-run `last_error` the contract's
    own bundled example documents (a literal column-name refusal, verbatim from gold)."""
    return [
        {
            "kpi_id": kpi.identifier,
            "identifier": kpi.identifier,
            "status": "failed",
            "last_computed_at": None,
            "as_of": None,
            "staleness_hours": None,
            "last_run_id": "run_gold_20260914",
            "last_error": "column supplier_deliveries.actual_date does not exist",
        }
        for kpi in kpis
    ]


def _health_app_with_configured_warehouse(monkeypatch, *, scopes):
    """Like `_health_app`, but the warehouse engine resolves instead of 503ing, and
    `akpi_health` returns a row with a real `last_error` -- what the `last_error`-gating tests
    below need to actually observe the field present/absent."""
    monkeypatch.setattr(kpis_module, "akpi_health", _fake_akpi_health_with_error)

    app = FastAPI()
    app.include_router(kpis_module.router)
    app.dependency_overrides[kpis_module._kpi_store] = lambda: _ListableKpiStore(
        existing=_kpi()
    )
    app.dependency_overrides[kpis_module._warehouse_engine] = lambda: object()

    @app.middleware("http")
    async def _fake_auth(request, call_next):
        request.state.authorization_enabled = True
        request.state.scopes = scopes
        request.state.admin_scope = None
        return await call_next(request)

    return TestClient(app)


def test_list_kpi_health_hides_last_error_from_a_kpis_read_only_caller(monkeypatch):
    """`last_error` is the database's own refusal text verbatim -- as sensitive as `Kpi.sql`,
    which this router already gates behind `kpis:write` via `_may_read_sql`. A `kpis:read`-only
    caller must see every other field but not the raw error string."""
    client = _health_app_with_configured_warehouse(monkeypatch, scopes=["kpis:read"])
    response = client.get("/kpis/health")
    assert response.status_code == 200
    row = response.json()[0]
    assert row["last_error"] is None
    assert row["status"] == "failed"


def test_list_kpi_health_shows_last_error_to_a_kpis_write_caller(monkeypatch):
    """The same field, unhidden for a caller who already holds the write scope -- the same
    posture `Kpi.sql`/`proposed_sql` have everywhere else in this router. Still needs
    `kpis:read` too: that's the route's own scope gate, unrelated to `_may_read_sql`'s."""
    client = _health_app_with_configured_warehouse(
        monkeypatch, scopes=["kpis:read", "kpis:write"]
    )
    response = client.get("/kpis/health")
    assert response.status_code == 200
    row = response.json()[0]
    assert row["last_error"] == "column supplier_deliveries.actual_date does not exist"


# ---------------------------------------------------------------------------
# #234/F1 -- GET /kpis/suggestions
# ---------------------------------------------------------------------------


def _suggestion(**overrides) -> wire_models.KpiSuggestion:
    defaults = dict(
        identifier="supplier__avg_lead_time_days",
        name="Average Supplier Lead Time",
        description="Average days between expected and actual delivery.",
        business_area="supply_chain",
        grain="supplier",
        result_shape=wire_models.ResultShape.scalar,
        rationale="supplier_deliveries carries expected_date and actual_date.",
        derived_from_tables=["supplier_deliveries"],
        confidence=wire_models.Confidence.high,
    )
    defaults.update(overrides)
    return wire_models.KpiSuggestion(**defaults)


_VOCABULARIES = load_vocabulary_set()


def test_validated_business_area_passes_through_a_known_term():
    assert _validated_business_area("supply_chain", _VOCABULARIES) == "supply_chain"


def test_validated_business_area_passes_through_none():
    assert _validated_business_area(None, _VOCABULARIES) is None


def test_validated_business_area_turns_an_unknown_term_into_a_422():
    with pytest.raises(HTTPException) as excinfo:
        _validated_business_area("not_a_real_business_area", _VOCABULARIES)
    assert excinfo.value.status_code == 422


def test_filter_suggestions_with_no_business_area_returns_everything():
    suggestions = [
        _suggestion(),
        _suggestion(identifier="other", business_area="sales"),
    ]
    assert _filter_suggestions(suggestions, None) == suggestions


def test_filter_suggestions_narrows_to_the_requested_business_area():
    keep = _suggestion()
    drop = _suggestion(identifier="other", business_area="sales")
    result = _filter_suggestions([keep, drop], "supply_chain")
    assert result == [keep]


def test_list_kpi_suggestions_turns_an_invalid_business_area_into_a_422_before_touching_any_store():
    with pytest.raises(HTTPException) as excinfo:
        _list_kpi_suggestions(
            RetailSettings(),
            # unreachable: the 422 fires before either is ever used
            definitions=(),
            vocabularies=_VOCABULARIES,
            business_area="not_a_real_business_area",
            include_sql=True,
        )
    assert excinfo.value.status_code == 422


# ---------------------------------------------------------------------------
# _suggestion_notes -- Finding 2 (PR #242 review): `SuggestionRun.notes` (the
# "no domain prose reached the agents" line `run_suggestions` always appends here) was being
# dropped entirely; only `AssemblyReport.notes` reached the wire.
# ---------------------------------------------------------------------------


class _FakeAssemblyReport:
    def __init__(self, notes: str = ""):
        self.notes = notes


class _FakeSuggestionRun:
    def __init__(self, notes=(), kpis_notes: str = ""):
        self.notes = list(notes)
        self.kpis = _FakeAssemblyReport(kpis_notes)


def test_suggestion_notes_is_empty_when_neither_source_has_anything():
    assert _suggestion_notes(_FakeSuggestionRun()) == ""


def test_suggestion_notes_surfaces_run_notes():
    run = _FakeSuggestionRun(notes=["no domain prose reached the agents: ..."])
    assert _suggestion_notes(run) == "no domain prose reached the agents: ..."


def test_suggestion_notes_surfaces_both_sources_when_both_are_present():
    run = _FakeSuggestionRun(notes=["run note"], kpis_notes="assembly note")
    notes = _suggestion_notes(run)
    assert "run note" in notes
    assert "assembly note" in notes


def test_suggestion_notes_keeps_kpis_notes_when_run_notes_is_empty():
    run = _FakeSuggestionRun(notes=[], kpis_notes="assembly note only")
    assert _suggestion_notes(run) == "assembly note only"


# ---------------------------------------------------------------------------
# include_sql gating on GET /kpis/suggestions -- Finding 1 (PR #242 review):
# `proposed_sql` must be gated by `_may_read_sql` the same way `Kpi.sql` already is.
# ---------------------------------------------------------------------------


def _fake_run_suggestions(kpi_suggestions):
    def _fake(
        settings,
        *,
        reader,
        existing_definitions,
        proposals,
        dry_run,
        agents,
        focus_areas=(),
        on_phase=None,
    ):
        return _FakeSuggestionRunWithSuggestions(kpi_suggestions)

    return _fake


class _FakeAssemblyReportWithSuggestions:
    def __init__(self, suggestions, notes: str = ""):
        self.suggestions = suggestions
        self.notes = notes


class _FakeSuggestionRunWithSuggestions:
    def __init__(self, suggestions, notes=(), kpis_notes: str = ""):
        self.notes = list(notes)
        self.kpis = _FakeAssemblyReportWithSuggestions(suggestions, kpis_notes)
        self.snapshot = "snap.0"


class _NullSemanticReaderCtx:
    def __enter__(self):
        return object()

    def __exit__(self, *exc_info):
        return False


def _patch_suggestion_pipeline(monkeypatch, kpi_suggestions):
    monkeypatch.setattr(
        kpis_module, "open_semantic_reader", lambda settings: _NullSemanticReaderCtx()
    )
    monkeypatch.setattr(
        kpis_module,
        "run_suggestions",
        _fake_run_suggestions(kpi_suggestions),
    )


def test_list_kpi_suggestions_includes_proposed_sql_when_include_sql_is_true(
    monkeypatch,
):
    suggestion = _suggestion(proposed_sql="SELECT 1 FROM supplier_deliveries")
    _patch_suggestion_pipeline(monkeypatch, [suggestion])

    result = _list_kpi_suggestions(
        RetailSettings(),
        definitions=(),
        vocabularies=_VOCABULARIES,
        business_area=None,
        include_sql=True,
        agents=("kpi_agent", "use_case_agent"),
    )

    assert result.suggestions[0].proposed_sql == "SELECT 1 FROM supplier_deliveries"


def test_list_kpi_suggestions_nulls_proposed_sql_when_include_sql_is_false(
    monkeypatch,
):
    suggestion = _suggestion(proposed_sql="SELECT 1 FROM supplier_deliveries")
    _patch_suggestion_pipeline(monkeypatch, [suggestion])

    result = _list_kpi_suggestions(
        RetailSettings(),
        definitions=(),
        vocabularies=_VOCABULARIES,
        business_area=None,
        include_sql=False,
        agents=("kpi_agent", "use_case_agent"),
    )

    assert result.suggestions[0].proposed_sql is None
    # the original object handed back by the (fake) assembly step is untouched
    assert suggestion.proposed_sql == "SELECT 1 FROM supplier_deliveries"


# ---------------------------------------------------------------------------
# retail_suggestion_replay -- demo/testing rehearsal without a live model call
# ---------------------------------------------------------------------------


def test_suggestion_replay_returns_the_saved_file_without_calling_the_model(
    monkeypatch, tmp_path
):
    """`retail_suggestion_replay` short-circuits before `run_suggestions` is ever reached --
    patched here to raise, so this test fails loudly if the replay branch stops short-
    circuiting rather than silently falling through to a real (test-breaking) model call."""
    saved = wire_models.KpiSuggestionList(
        suggestions=[_suggestion(identifier="a", business_area="supply_chain")],
        notes="",
    )
    replay_path = tmp_path / "replay.json"
    replay_path.write_text(saved.model_dump_json())

    def _must_not_be_called(*args, **kwargs):
        raise AssertionError("run_suggestions must not run while replay is set")

    monkeypatch.setattr(kpis_module, "run_suggestions", _must_not_be_called)
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    result = _list_kpi_suggestions(
        RetailSettings(retail_suggestion_replay=str(replay_path)),
        definitions=(),
        vocabularies=_VOCABULARIES,
        business_area=None,
        include_sql=True,
    )

    assert [s.identifier for s in result.suggestions] == ["a"]


def test_suggestion_replay_still_applies_the_business_area_filter_and_sql_gate(
    monkeypatch, tmp_path
):
    saved = wire_models.KpiSuggestionList(
        suggestions=[
            _suggestion(
                identifier="a",
                business_area="supply_chain",
                proposed_sql="SELECT 1 FROM supplier_deliveries",
            ),
            _suggestion(identifier="b", business_area="marketing"),
        ],
        notes="",
    )
    replay_path = tmp_path / "replay.json"
    replay_path.write_text(saved.model_dump_json())
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    result = _list_kpi_suggestions(
        RetailSettings(retail_suggestion_replay=str(replay_path)),
        definitions=(),
        vocabularies=_VOCABULARIES,
        business_area="supply_chain",
        include_sql=False,
    )

    assert [s.identifier for s in result.suggestions] == ["a"]
    assert result.suggestions[0].proposed_sql is None


def test_suggestion_replay_skips_the_phase_sleeps_when_on_phase_is_none(
    monkeypatch, tmp_path
):
    """The deprecated sync `GET /kpis/suggestions` route calls `_list_kpi_suggestions` with no
    `on_phase` at all, so there is no run panel for the phase pauses to animate. With replay set,
    the sleep loop must be skipped entirely rather than blocking ~8.5s for nothing."""
    saved = wire_models.KpiSuggestionList(
        suggestions=[_suggestion(identifier="a", business_area="supply_chain")],
        notes="",
    )
    replay_path = tmp_path / "replay.json"
    replay_path.write_text(saved.model_dump_json())

    def _must_not_sleep(_seconds):
        raise AssertionError("time.sleep must not run when on_phase is None")

    monkeypatch.setattr(time, "sleep", _must_not_sleep)

    result = _list_kpi_suggestions(
        RetailSettings(retail_suggestion_replay=str(replay_path)),
        definitions=(),
        vocabularies=_VOCABULARIES,
        business_area=None,
        include_sql=True,
        on_phase=None,
    )

    assert [s.identifier for s in result.suggestions] == ["a"]


def test_suggestion_replay_reports_phases_in_order_when_on_phase_is_given(
    monkeypatch, tmp_path
):
    """The mirror of the skip test above: with a real callback, replay must still report the
    three phases in the same order a live run would (`kpis.py`'s `on_phase(phase)` before
    `time.sleep(pause)`, `[READING, PROPOSING, CHECKING]`) -- the actual mechanism that makes a
    replayed run "behave exactly as it would on a live run" per this PR's description."""
    saved = wire_models.KpiSuggestionList(
        suggestions=[_suggestion(identifier="a", business_area="supply_chain")],
        notes="",
    )
    replay_path = tmp_path / "replay.json"
    replay_path.write_text(saved.model_dump_json())
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    seen: list[SuggestionRunPhase] = []

    result = _list_kpi_suggestions(
        RetailSettings(retail_suggestion_replay=str(replay_path)),
        definitions=(),
        vocabularies=_VOCABULARIES,
        business_area=None,
        include_sql=True,
        on_phase=seen.append,
    )

    assert seen == [
        SuggestionRunPhase.READING,
        SuggestionRunPhase.PROPOSING,
        SuggestionRunPhase.CHECKING,
    ]
    assert [s.identifier for s in result.suggestions] == ["a"]


def test_suggestion_replay_drops_a_suggestion_already_in_the_registry(
    monkeypatch, tmp_path
):
    """The live path drops a draft already in the registry (K12's `assemble`,
    `known_identifiers=existing`). A replayed file is a static snapshot that cannot know about
    adoptions made since it was captured, so the same drop must be redone against the current
    registry -- otherwise replaying after adopting one of the replayed suggestions would show
    that same KPI again as a fresh "ready" proposal, which a live run can never do."""
    saved = wire_models.KpiSuggestionList(
        suggestions=[
            _suggestion(identifier="a", business_area="supply_chain"),
            _suggestion(identifier="b", business_area="supply_chain"),
        ],
        notes="",
    )
    replay_path = tmp_path / "replay.json"
    replay_path.write_text(saved.model_dump_json())
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    result = _list_kpi_suggestions(
        RetailSettings(retail_suggestion_replay=str(replay_path)),
        definitions=(_kpi(identifier="a"),),
        vocabularies=_VOCABULARIES,
        business_area=None,
        include_sql=True,
    )

    assert [s.identifier for s in result.suggestions] == ["b"]


def test_suggestion_replay_raises_safely_on_a_malformed_file(tmp_path):
    """A malformed/unreadable `retail_suggestion_replay` file must not raise a bare
    `json.JSONDecodeError` or pydantic `ValidationError` -- both are `ValueError` subclasses,
    and `_describe_failure` passes any `ValueError` through to a run's `error` field verbatim,
    reserved for `_drafts_of`'s own deliberate reader-facing refusal. Parse/validation text off
    a misconfigured file is not that, so it must come out as something else -- here, not a
    `ValueError` at all -- so `_describe_failure` falls back to its type-only summary."""
    replay_path = tmp_path / "replay.json"
    replay_path.write_text("not valid json")

    with pytest.raises(Exception) as excinfo:
        _list_kpi_suggestions(
            RetailSettings(retail_suggestion_replay=str(replay_path)),
            definitions=(),
            vocabularies=_VOCABULARIES,
            business_area=None,
            include_sql=True,
        )

    assert not isinstance(excinfo.value, ValueError)


# ---------------------------------------------------------------------------
# _cached_suggestion_agents -- Finding 4 (PR #242 review): CLAUDE.md says never create agents
# in loops; every real request was building two fresh `Agent`s from scratch.
# ---------------------------------------------------------------------------


def test_cached_suggestion_agents_reuses_the_same_agents_for_an_unchanged_registry(
    monkeypatch,
):
    build_calls = []

    def _fake_build(settings, *, existing_kpis, registry_kpis, catalogue):
        agents = (object(), object())
        build_calls.append(agents)
        return agents

    monkeypatch.setattr(kpis_module, "build_suggestion_agents", _fake_build)
    monkeypatch.setattr(kpis_module, "load_catalogue", lambda: [])
    monkeypatch.setattr(kpis_module, "_agent_cache_key", None)
    monkeypatch.setattr(kpis_module, "_agent_cache_agents", None)

    definitions = (_kpi(identifier="a"), _kpi(identifier="b"))
    first = kpis_module._cached_suggestion_agents(RetailSettings(), definitions)
    second = kpis_module._cached_suggestion_agents(RetailSettings(), definitions)

    assert first is second
    assert len(build_calls) == 1


def test_cached_suggestion_agents_rebuilds_when_the_registry_changes(monkeypatch):
    build_calls = []

    def _fake_build(settings, *, existing_kpis, registry_kpis, catalogue):
        agents = (object(), object())
        build_calls.append(agents)
        return agents

    monkeypatch.setattr(kpis_module, "build_suggestion_agents", _fake_build)
    monkeypatch.setattr(kpis_module, "load_catalogue", lambda: [])
    monkeypatch.setattr(kpis_module, "_agent_cache_key", None)
    monkeypatch.setattr(kpis_module, "_agent_cache_agents", None)

    first = kpis_module._cached_suggestion_agents(
        RetailSettings(), (_kpi(identifier="a"),)
    )
    second = kpis_module._cached_suggestion_agents(
        RetailSettings(), (_kpi(identifier="a"), _kpi(identifier="b"))
    )

    assert first is not second
    assert len(build_calls) == 2


# ---------------------------------------------------------------------------
# Round-3 review: `_agent_cache_lock` only guards cache population, not the `.run()` calls
# that follow -- concurrent requests sharing the cached `Agent` pair race on unsynchronized
# per-agent state (session_id, hook normalization, connectable-tool init lists). Every real
# call must `.run()` its own `deep_copy()`, never the cached originals.
# ---------------------------------------------------------------------------


class _FakeCacheableAgentCopy:
    def __init__(self, name: str):
        self.name = name


class _FakeCacheableAgent:
    def __init__(self, name: str):
        self.name = name
        self.deep_copy_calls = 0

    def deep_copy(self):
        self.deep_copy_calls += 1
        return _FakeCacheableAgentCopy(self.name)


def test_list_kpi_suggestions_runs_a_deep_copy_of_each_cached_agent_not_the_original(
    monkeypatch,
):
    cached_kpi_agent = _FakeCacheableAgent("kpi")
    cached_use_case_agent = _FakeCacheableAgent("use_case")
    monkeypatch.setattr(
        kpis_module,
        "_cached_suggestion_agents",
        lambda settings, definitions: (cached_kpi_agent, cached_use_case_agent),
    )
    monkeypatch.setattr(
        kpis_module, "open_semantic_reader", lambda settings: _NullSemanticReaderCtx()
    )

    captured_agents = {}

    def _fake_run_suggestions_capturing(
        settings,
        *,
        reader,
        existing_definitions,
        proposals,
        dry_run,
        agents,
        focus_areas=(),
        on_phase=None,
    ):
        captured_agents["agents"] = agents
        return _FakeSuggestionRunWithSuggestions([])

    monkeypatch.setattr(kpis_module, "run_suggestions", _fake_run_suggestions_capturing)

    _list_kpi_suggestions(
        RetailSettings(),
        definitions=(),
        vocabularies=_VOCABULARIES,
        business_area=None,
        include_sql=True,
        agents=None,
    )

    assert cached_kpi_agent.deep_copy_calls == 1
    assert cached_use_case_agent.deep_copy_calls == 1
    run_kpi_agent, run_use_case_agent = captured_agents["agents"]
    assert run_kpi_agent is not cached_kpi_agent
    assert run_use_case_agent is not cached_use_case_agent
    assert isinstance(run_kpi_agent, _FakeCacheableAgentCopy)
    assert isinstance(run_use_case_agent, _FakeCacheableAgentCopy)


# ---------------------------------------------------------------------------
# Finding 7 (PR #242 review): the real @router-decorated `list_kpi_suggestions` was never
# called directly by any test -- only the underscored helper was.
# ---------------------------------------------------------------------------


def test_list_kpi_suggestions_route_reaches_the_helper_with_the_given_business_area(
    monkeypatch,
):
    captured = {}

    def _fake_helper(
        settings,
        *,
        definitions,
        vocabularies,
        business_area,
        include_sql,
        focus_areas=(),
    ):
        captured["business_area"] = business_area
        captured["definitions"] = definitions
        captured["include_sql"] = include_sql
        return wire_models.KpiSuggestionList(
            suggestions=[], notes="", semantic_snapshot="snap.0"
        )

    monkeypatch.setattr(kpis_module, "_list_kpi_suggestions", _fake_helper)

    # A fake registry snapshot in place of the real store dependency's -- exercising the
    # real, `@router`-decorated `list_kpi_suggestions` directly rather than only the
    # underscored `_list_kpi_suggestions` helper.
    snapshot = _SuggestionRegistrySnapshot(
        definitions=(_kpi(identifier="a"),), vocabularies=_VOCABULARIES
    )
    # The route is `async` since #254 (it waits on the helper with a bound), so it is run
    # to completion here rather than called for its coroutine.
    result = asyncio.run(
        list_kpi_suggestions(
            _FakeRequest(authorization_enabled=False),
            business_area="supply_chain",
            snapshot=snapshot,
        )
    )

    assert captured["business_area"] == "supply_chain"
    assert captured["definitions"] == snapshot.definitions
    assert captured["include_sql"] is True
    assert result.semantic_snapshot == "snap.0"


# ---------------------------------------------------------------------------
# Finding 6 (PR #242 review): nothing dispatched through the real router/TestClient, so a
# route-registration-order regression (a static "/kpis/suggestions" swallowed by the dynamic
# "/kpis/{kpi_id}" route) would have shipped silently.
# ---------------------------------------------------------------------------


def test_get_kpis_suggestions_dispatches_to_the_suggestions_handler_not_get_kpi(
    monkeypatch,
):
    calls = []

    def _fake_helper(
        settings,
        *,
        definitions,
        vocabularies,
        business_area,
        include_sql,
        focus_areas=(),
    ):
        calls.append(business_area)
        return wire_models.KpiSuggestionList(
            suggestions=[], notes="", semantic_snapshot="snap.0"
        )

    monkeypatch.setattr(kpis_module, "_list_kpi_suggestions", _fake_helper)

    app = FastAPI()
    app.include_router(kpis_module.router)
    # `Depends(_suggestion_registry_snapshot)` captured the function object at decoration
    # time, so it must be swapped out through FastAPI's own override hook, not by patching
    # the module attribute (which a plain module-level monkeypatch would not reach).
    app.dependency_overrides[kpis_module._suggestion_registry_snapshot] = lambda: (
        _SuggestionRegistrySnapshot(definitions=(), vocabularies=_VOCABULARIES)
    )
    client = TestClient(app)

    response = client.get("/kpis/suggestions")

    assert response.status_code == 200
    body = response.json()
    # get_kpi's shape (a single Kpi) has no "suggestions"/"semantic_snapshot" keys; a request
    # swallowed by "/kpis/{kpi_id}" would 404 ("No KPI 'suggestions'.") instead of this.
    assert body == {"suggestions": [], "notes": "", "semantic_snapshot": "snap.0"}
    assert calls == [None]


class _ReferencedKpiStore(_FakeKpiStore):
    """``delete_if_unreferenced`` refuses with names, as it would for a KPI a dashboard --
    including one the deleting caller cannot even see -- still has a tile for."""

    def __init__(self, existing, referenced_by):
        super().__init__(existing=existing)
        self._referenced_by = referenced_by

    def delete_if_unreferenced(self, identifier, *, caller=None):
        if identifier not in self._by_id:
            return KpiDeleteResult(removed=False)
        return KpiDeleteResult(removed=False, referenced_by=self._referenced_by)


def test_delete_kpi_refuses_when_the_store_reports_a_reference():
    existing = _kpi(identifier="rfq__quote_to_deal_rate")
    store = _ReferencedKpiStore(
        existing, referenced_by=("someone_elses_private_dashboard",)
    )
    with pytest.raises(HTTPException) as excinfo:
        delete_kpi(
            "rfq__quote_to_deal_rate",
            _FakeRequest(authorization_enabled=False),
            store=store,
        )
    assert excinfo.value.status_code == 409
    assert "someone_elses_private_dashboard" in excinfo.value.detail


def test_get_kpi_404s_on_a_missing_identifier():
    store = _FakeKpiStore(existing=None)
    with pytest.raises(HTTPException) as excinfo:
        get_kpi(
            "does_not_exist", _FakeRequest(authorization_enabled=False), store=store
        )
    assert excinfo.value.status_code == 404


# ---------------------------------------------------------------------------
# get_kpi / get_kpi_feasibility -- previously uncovered by this DB-free suite
# (round-1 review): the only tests exercising these two route functions were
# gated behind test_kpis_live.py's live Postgres/Neo4j requirement.
#
# The fake store below also pins the TOCTOU fix: `_feasibility_map` is now
# called with exactly the caller's already-fetched definition(s), never a
# second, independent `self.list()` -- so `.feasibility()` here records what
# it was actually asked to compute over, and a store that "forgets" an
# identifier between two reads can no longer produce a bare KeyError, because
# there is no second read left to disagree with the first.
# ---------------------------------------------------------------------------


class _FakeSnapshot:
    version = "snap.1"


class _FakeReader:
    def snapshot(self):
        return _FakeSnapshot()


@contextmanager
def _fake_semantic_reader(settings):
    yield _FakeReader()


class _FakeStore:
    def __init__(self, kpis, *, omit_from_feasibility=frozenset()):
        self._kpis = list(kpis)
        self._omit = frozenset(omit_from_feasibility)
        self.feasibility_calls: list = []

    def list(self, *, business_area=None):
        if business_area is None:
            return tuple(self._kpis)
        return tuple(k for k in self._kpis if k.business_area == business_area)

    def get(self, identifier):
        return next((k for k in self._kpis if k.identifier == identifier), None)

    def feasibility(self, reader, kpis):
        self.feasibility_calls.append([k.identifier for k in kpis])
        return {
            kpi.identifier: _ready_feasibility(kpi.identifier)
            for kpi in kpis
            if kpi.identifier not in self._omit
        }


@pytest.fixture(autouse=True)
def _patch_semantic_reader(monkeypatch):
    monkeypatch.setattr(kpis_module, "open_semantic_reader", _fake_semantic_reader)


def test_get_kpi_returns_the_kpi_with_its_own_feasibility_only():
    store = _FakeStore([_kpi(identifier="a"), _kpi(identifier="b")])
    result = get_kpi("a", _FakeRequest(authorization_enabled=False), store=store)
    assert result.identifier == "a"
    assert result.feasibility.kpi_id == "a"
    # The whole point of the fix: one KPI's route computes feasibility over one KPI, not
    # the entire registry `_FakeStore` was constructed with.
    assert store.feasibility_calls == [["a"]]


def test_get_kpi_missing_id_is_404():
    store = _FakeStore([_kpi(identifier="a")])
    with pytest.raises(HTTPException) as excinfo:
        get_kpi("nope", _FakeRequest(authorization_enabled=False), store=store)
    assert excinfo.value.status_code == 404
    assert store.feasibility_calls == [], "a 404 must not compute feasibility at all"


def test_get_kpi_feasibility_returns_the_wire_feasibility():
    store = _FakeStore([_kpi(identifier="a")])
    result = get_kpi_feasibility("a", store=store)
    assert result.kpi_id == "a"
    assert result.verdict.value == "ready"
    assert store.feasibility_calls == [["a"]]


def test_get_kpi_feasibility_missing_id_is_404():
    store = _FakeStore([_kpi(identifier="a")])
    with pytest.raises(HTTPException) as excinfo:
        get_kpi_feasibility("nope", store=store)
    assert excinfo.value.status_code == 404


def test_list_kpis_computes_feasibility_only_over_the_business_area_filtered_kpis():
    store = _FakeStore(
        [
            _kpi(identifier="a", business_area="sales"),
            _kpi(identifier="b", business_area="inventory"),
        ]
    )
    result = list_kpis(
        _FakeRequest(authorization_enabled=False),
        business_area="sales",
        store=store,
    )
    assert [k.identifier for k in result] == ["a"]
    # Narrowed by business_area *before* feasibility is computed -- not a second,
    # unfiltered full-registry query the router then mostly discards.
    assert store.feasibility_calls == [["a"]]


# ---------------------------------------------------------------------------
# #254: `POST /kpis/suggestions` -> 202 + `GET /kpis/suggestions/{run_id}`. The run row is
# faked in memory with the store's own transition rules; the pipeline helper is faked as the
# tests above already fake it. Every test here drives the real router through `TestClient`
# *as a context manager*: the executor is an `asyncio.Task` on the app's loop, and only the
# context-managed client keeps one loop alive across the POST, the drain and the poll.
# ---------------------------------------------------------------------------


class _FakeRunStore:
    """``SuggestionRunStore`` without Postgres: same rows, same guarded transitions."""

    _OPEN = (SuggestionRunStatus.QUEUED, SuggestionRunStatus.RUNNING)

    def __init__(self):
        self.rows: dict = {}
        #: What `typical_duration()` answers; a test sets it to stand for history.
        self.typical = None

    def create(self, business_area, business_areas=()):
        record = SuggestionRunRecord(
            identifier=f"sr_{len(self.rows) + 1}",
            status=SuggestionRunStatus.QUEUED,
            business_area=business_area,
            created_at=datetime.now(timezone.utc),
            business_areas=tuple(business_areas),
        )
        self.rows[record.identifier] = record
        return record

    def _set(self, identifier, **changes):
        self.rows[identifier] = replace(self.rows[identifier], **changes)

    def mark_running(self, identifier):
        if self.rows[identifier].status is SuggestionRunStatus.QUEUED:
            self._set(
                identifier,
                status=SuggestionRunStatus.RUNNING,
                started_at=datetime.now(timezone.utc),
            )

    def mark_phase(self, identifier, phase):
        if self.rows[identifier].status is SuggestionRunStatus.RUNNING:
            self._set(identifier, phase=SuggestionRunPhase(phase))

    def mark_succeeded(self, identifier, result):
        if self.rows[identifier].status in self._OPEN:
            self._set(
                identifier,
                status=SuggestionRunStatus.SUCCEEDED,
                result=dict(result),
                finished_at=datetime.now(timezone.utc),
            )

    def mark_failed(self, identifier, error):
        if self.rows[identifier].status in self._OPEN:
            self._set(
                identifier,
                status=SuggestionRunStatus.FAILED,
                error=error,
                finished_at=datetime.now(timezone.utc),
            )

    def get(self, identifier, *, stale_after=None):
        record = self.rows.get(identifier)
        if record is None:
            return None
        if (
            stale_after is not None
            and record.status in self._OPEN
            and record.created_at < datetime.now(timezone.utc) - stale_after
        ):
            self.mark_failed(identifier, LOST_RUN_ERROR)
        return self.rows[identifier]

    def typical_duration(self, *, sample=5):
        return self.typical


def _run_client(monkeypatch, store, *, authorization=False, scopes=()):
    @contextmanager
    def _fake_open_run_store(settings):
        yield store

    monkeypatch.setattr(kpis_module, "_open_run_store", _fake_open_run_store)

    app = FastAPI()

    @app.middleware("http")
    async def _fake_auth(request: Request, call_next):
        request.state.authorization_enabled = authorization
        request.state.scopes = list(scopes)
        request.state.admin_scope = None
        return await call_next(request)

    app.include_router(kpis_module.router)
    app.dependency_overrides[kpis_module._suggestion_registry_snapshot] = lambda: (
        _SuggestionRegistrySnapshot(definitions=(), vocabularies=_VOCABULARIES)
    )
    return TestClient(app)


def _drain(client):
    """Wait for every executor task the app's loop has in flight."""
    client.portal.call(kpis_module._await_suggestion_runs)


def _suggesting(suggestions, *, notes=""):
    def _fake_helper(
        settings,
        *,
        definitions,
        vocabularies,
        business_area,
        include_sql,
        focus_areas=(),
        on_phase=None,
    ):
        return wire_models.KpiSuggestionList(
            suggestions=list(suggestions), notes=notes, semantic_snapshot="snap.0"
        )

    return _fake_helper


def test_start_kpi_suggestion_run_returns_202_queued_then_the_poll_reaches_succeeded(
    monkeypatch,
):
    suggestion = _suggestion(proposed_sql="SELECT 1 FROM supplier_deliveries")
    monkeypatch.setattr(kpis_module, "_list_kpi_suggestions", _suggesting([suggestion]))
    store = _FakeRunStore()

    with _run_client(monkeypatch, store) as client:
        started = client.post(
            "/kpis/suggestions", json={"business_area": "supply_chain"}
        )
        assert started.status_code == 202
        body = started.json()
        assert body["status"] == "queued"
        assert body["business_area"] == "supply_chain"
        assert body["result"] is None and body["error"] is None
        run_id = body["id"]

        _drain(client)

        polled = client.get(f"/kpis/suggestions/{run_id}")

    assert polled.status_code == 200
    done = polled.json()
    assert done["status"] == "succeeded"
    assert done["started_at"] is not None and done["finished_at"] is not None
    assert done["error"] is None
    assert done["result"]["semantic_snapshot"] == "snap.0"
    assert [s["identifier"] for s in done["result"]["suggestions"]] == [
        suggestion.identifier
    ]
    # authorization off: every caller sees candidate SQL, as on the synchronous route
    assert (
        done["result"]["suggestions"][0]["proposed_sql"]
        == "SELECT 1 FROM supplier_deliveries"
    )


def test_start_kpi_suggestion_run_with_no_body_runs_across_every_area(monkeypatch):
    captured = {}

    def _fake_helper(
        settings,
        *,
        definitions,
        vocabularies,
        business_area,
        include_sql,
        focus_areas=(),
        on_phase=None,
    ):
        captured["business_area"] = business_area
        captured["include_sql"] = include_sql
        return wire_models.KpiSuggestionList(
            suggestions=[], notes="", semantic_snapshot="snap.0"
        )

    monkeypatch.setattr(kpis_module, "_list_kpi_suggestions", _fake_helper)
    store = _FakeRunStore()

    with _run_client(monkeypatch, store) as client:
        started = client.post("/kpis/suggestions")
        assert started.status_code == 202
        assert started.json()["business_area"] is None
        _drain(client)

    assert captured == {"business_area": None, "include_sql": True}


def test_get_kpi_suggestion_run_gates_proposed_sql_per_reader_not_per_run(monkeypatch):
    """One run, read by a viewer and an editor: the row keeps the SQL, the reader's scopes
    decide whether it is shown -- the same `_may_read_sql` gate the synchronous route has."""
    suggestion = _suggestion(proposed_sql="SELECT 1 FROM supplier_deliveries")
    monkeypatch.setattr(kpis_module, "_list_kpi_suggestions", _suggesting([suggestion]))
    store = _FakeRunStore()

    with _run_client(monkeypatch, store, authorization=True, scopes=["kpis:read"]) as (
        viewer
    ):
        run_id = viewer.post("/kpis/suggestions").json()["id"]
        _drain(viewer)
        as_viewer = viewer.get(f"/kpis/suggestions/{run_id}").json()

    with _run_client(
        monkeypatch, store, authorization=True, scopes=["kpis:read", "kpis:write"]
    ) as editor:
        as_editor = editor.get(f"/kpis/suggestions/{run_id}").json()

    assert as_viewer["status"] == as_editor["status"] == "succeeded"
    assert as_viewer["result"]["suggestions"][0]["proposed_sql"] is None
    assert (
        as_editor["result"]["suggestions"][0]["proposed_sql"]
        == "SELECT 1 FROM supplier_deliveries"
    )
    # the stored row itself was never stripped
    assert store.rows[run_id].result["suggestions"][0]["proposed_sql"] is not None


def test_start_kpi_suggestion_run_422s_on_an_unknown_business_area_before_any_row_exists(
    monkeypatch,
):
    monkeypatch.setattr(kpis_module, "_list_kpi_suggestions", _suggesting([]))
    store = _FakeRunStore()

    with _run_client(monkeypatch, store) as client:
        response = client.post("/kpis/suggestions", json={"business_area": "nope"})

    assert response.status_code == 422
    assert "nope" in response.json()["detail"]
    assert store.rows == {}


def test_a_pipeline_refusal_becomes_a_failed_run_with_its_own_words(monkeypatch):
    """`_drafts_of`'s ValueError -- a raw traceback-carrying 500 on the synchronous route --
    is a readable `failed` row here."""

    def _refusing(settings, **kwargs):
        raise ValueError(
            "KpiSuggestionDraftSet expected from the agent; got str: no model configured"
        )

    monkeypatch.setattr(kpis_module, "_list_kpi_suggestions", _refusing)
    store = _FakeRunStore()

    with _run_client(monkeypatch, store) as client:
        run_id = client.post("/kpis/suggestions").json()["id"]
        _drain(client)
        polled = client.get(f"/kpis/suggestions/{run_id}").json()

    assert polled["status"] == "failed"
    assert polled["result"] is None
    assert "KpiSuggestionDraftSet expected from the agent" in polled["error"]


def test_a_non_pipeline_failure_is_summarised_by_type_never_by_its_text(monkeypatch):
    """Driver/connection errors can carry a DSN in their text; the row names the type and
    points at the log (the same posture the app's 500 handler took in PR #252, round 2)."""

    def _exploding(settings, **kwargs):
        raise RuntimeError("could not connect to postgresql://ai:hunter2@db/ai")

    monkeypatch.setattr(kpis_module, "_list_kpi_suggestions", _exploding)
    logged = []
    monkeypatch.setattr(kpis_module, "log_error", logged.append)
    store = _FakeRunStore()

    with _run_client(monkeypatch, store) as client:
        run_id = client.post("/kpis/suggestions").json()["id"]
        _drain(client)
        polled = client.get(f"/kpis/suggestions/{run_id}").json()

    assert polled["status"] == "failed"
    assert "RuntimeError" in polled["error"]
    assert "hunter2" not in polled["error"]
    # ...and the actual reason did reach the log, traceback included
    assert any("hunter2" in entry and "Traceback" in entry for entry in logged)


def test_a_run_past_the_timeout_is_marked_failed_naming_the_bound(monkeypatch):
    monkeypatch.setenv("EGPT_RETAIL_SUGGESTION_TIMEOUT_SECONDS", "0.05")

    def _slow(settings, **kwargs):
        time.sleep(0.5)
        return wire_models.KpiSuggestionList(
            suggestions=[], notes="", semantic_snapshot="snap.0"
        )

    monkeypatch.setattr(kpis_module, "_list_kpi_suggestions", _slow)
    store = _FakeRunStore()

    with _run_client(monkeypatch, store) as client:
        run_id = client.post("/kpis/suggestions").json()["id"]
        _drain(client)
        polled = client.get(f"/kpis/suggestions/{run_id}").json()

    assert polled["status"] == "failed"
    assert "did not finish within 0.05s" in polled["error"]


def test_a_late_result_cannot_reopen_a_run_the_timeout_already_closed(monkeypatch):
    """The thread outlives the bound and eventually returns; the row stays `failed`."""
    monkeypatch.setenv("EGPT_RETAIL_SUGGESTION_TIMEOUT_SECONDS", "0.05")
    suggestion = _suggestion(proposed_sql="SELECT 1 FROM supplier_deliveries")

    def _slow(settings, **kwargs):
        time.sleep(0.3)
        return wire_models.KpiSuggestionList(
            suggestions=[suggestion], notes="", semantic_snapshot="snap.0"
        )

    monkeypatch.setattr(kpis_module, "_list_kpi_suggestions", _slow)
    store = _FakeRunStore()

    with _run_client(monkeypatch, store) as client:
        run_id = client.post("/kpis/suggestions").json()["id"]
        _drain(client)
        time.sleep(0.4)  # let the orphaned thread finish
        polled = client.get(f"/kpis/suggestions/{run_id}").json()

    assert polled["status"] == "failed"
    assert polled["result"] is None


def test_get_kpi_suggestion_run_closes_a_run_whose_executor_is_gone(monkeypatch):
    """A `running` row older than the timeout plus grace has no executor left in any process
    (the instance that accepted it was recycled): the read side closes it as `failed`."""
    monkeypatch.setattr(kpis_module, "_list_kpi_suggestions", _suggesting([]))
    store = _FakeRunStore()
    store.rows["sr_lost"] = SuggestionRunRecord(
        identifier="sr_lost",
        status=SuggestionRunStatus.RUNNING,
        business_area=None,
        created_at=datetime.now(timezone.utc) - timedelta(hours=1),
        started_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )

    with _run_client(monkeypatch, store) as client:
        polled = client.get("/kpis/suggestions/sr_lost").json()

    assert polled["status"] == "failed"
    assert polled["error"] == LOST_RUN_ERROR


def test_get_kpi_suggestion_run_leaves_a_recent_running_row_alone(monkeypatch):
    monkeypatch.setattr(kpis_module, "_list_kpi_suggestions", _suggesting([]))
    store = _FakeRunStore()
    store.rows["sr_live"] = SuggestionRunRecord(
        identifier="sr_live",
        status=SuggestionRunStatus.RUNNING,
        business_area=None,
        created_at=datetime.now(timezone.utc) - timedelta(seconds=30),
        started_at=datetime.now(timezone.utc) - timedelta(seconds=30),
    )

    with _run_client(monkeypatch, store) as client:
        polled = client.get("/kpis/suggestions/sr_live").json()

    assert polled["status"] == "running"
    assert polled["error"] is None


def test_get_kpi_suggestion_run_404s_and_is_not_swallowed_by_get_kpi(monkeypatch):
    monkeypatch.setattr(kpis_module, "_list_kpi_suggestions", _suggesting([]))
    store = _FakeRunStore()

    with _run_client(monkeypatch, store) as client:
        response = client.get("/kpis/suggestions/sr_missing")

    assert response.status_code == 404
    # `get_kpi` would have said "No KPI 'suggestions'." -- and opened a real store first.
    assert response.json()["detail"] == "No suggestion run 'sr_missing'."


def test_list_kpi_suggestions_504s_past_the_timeout_instead_of_hanging(monkeypatch):
    monkeypatch.setenv("EGPT_RETAIL_SUGGESTION_TIMEOUT_SECONDS", "0.05")

    def _slow(settings, **kwargs):
        time.sleep(0.5)
        return wire_models.KpiSuggestionList(
            suggestions=[], notes="", semantic_snapshot="snap.0"
        )

    monkeypatch.setattr(kpis_module, "_list_kpi_suggestions", _slow)
    store = _FakeRunStore()

    with _run_client(monkeypatch, store) as client:
        response = client.get("/kpis/suggestions")

    assert response.status_code == 504
    assert "did not finish within 0.05s" in response.json()["detail"]
    assert "POST /kpis/suggestions" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Contract 1.1.8: `business_areas`, the focus a run is started with. It reaches the
# pipeline as `focus_areas` (a priority in the agents' message), is echoed on the row, and
# is validated before the row exists.
# ---------------------------------------------------------------------------


def test_start_kpi_suggestion_run_hands_the_focus_to_the_pipeline_and_echoes_it(
    monkeypatch,
):
    captured = {}

    def _fake_helper(
        settings,
        *,
        definitions,
        vocabularies,
        business_area,
        include_sql,
        focus_areas=(),
        on_phase=None,
    ):
        captured["focus_areas"] = tuple(focus_areas)
        captured["business_area"] = business_area
        return wire_models.KpiSuggestionList(
            suggestions=[], notes="", semantic_snapshot="snap.0"
        )

    monkeypatch.setattr(kpis_module, "_list_kpi_suggestions", _fake_helper)
    store = _FakeRunStore()

    with _run_client(monkeypatch, store) as client:
        started = client.post(
            "/kpis/suggestions",
            json={"business_areas": ["sales", "supply_chain", "sales"]},
        )
        assert started.status_code == 202
        # echoed, de-duplicated, order kept; the single-area filter stays off
        assert started.json()["business_areas"] == ["sales", "supply_chain"]
        assert started.json()["business_area"] is None
        run_id = started.json()["id"]
        _drain(client)
        polled = client.get(f"/kpis/suggestions/{run_id}").json()

    assert captured == {
        "focus_areas": ("sales", "supply_chain"),
        "business_area": None,
    }
    assert polled["business_areas"] == ["sales", "supply_chain"]


def test_start_kpi_suggestion_run_with_no_focus_sends_none_and_echoes_an_empty_list(
    monkeypatch,
):
    captured = {}

    def _fake_helper(settings, *, focus_areas=(), **kwargs):
        captured["focus_areas"] = tuple(focus_areas)
        return wire_models.KpiSuggestionList(
            suggestions=[], notes="", semantic_snapshot="snap.0"
        )

    monkeypatch.setattr(kpis_module, "_list_kpi_suggestions", _fake_helper)
    store = _FakeRunStore()

    with _run_client(monkeypatch, store) as client:
        started = client.post("/kpis/suggestions", json={})
        assert started.json()["business_areas"] == []
        _drain(client)

    assert captured == {"focus_areas": ()}


def test_start_kpi_suggestion_run_422s_on_an_unknown_focus_area_before_any_row_exists(
    monkeypatch,
):
    monkeypatch.setattr(kpis_module, "_list_kpi_suggestions", _suggesting([]))
    store = _FakeRunStore()

    with _run_client(monkeypatch, store) as client:
        response = client.post(
            "/kpis/suggestions", json={"business_areas": ["sales", "logistics"]}
        )

    assert response.status_code == 422
    assert "logistics" in response.json()["detail"]
    assert store.rows == {}


# ---------------------------------------------------------------------------
# `KpiSuggestionRun.phase` and `.typical_duration_seconds` (contract 1.1.6): what the
# waiting screen is built on. The executor records each stage the pipeline reports; the
# typical duration rides along with the row on the 202 and on every poll.
# ---------------------------------------------------------------------------


def test_the_202_and_the_poll_both_carry_the_deployments_typical_duration(monkeypatch):
    monkeypatch.setattr(kpis_module, "_list_kpi_suggestions", _suggesting([]))
    store = _FakeRunStore()
    store.typical = 94.0

    with _run_client(monkeypatch, store) as client:
        started = client.post("/kpis/suggestions")
        assert started.status_code == 202
        assert started.json()["typical_duration_seconds"] == 94.0
        assert started.json()["phase"] is None  # queued: nothing has begun
        run_id = started.json()["id"]
        _drain(client)
        polled = client.get(f"/kpis/suggestions/{run_id}").json()

    assert polled["status"] == "succeeded"
    assert polled["typical_duration_seconds"] == 94.0


def test_typical_duration_is_null_before_any_run_has_succeeded(monkeypatch):
    monkeypatch.setattr(kpis_module, "_list_kpi_suggestions", _suggesting([]))
    store = _FakeRunStore()

    with _run_client(monkeypatch, store) as client:
        started = client.post("/kpis/suggestions")

    assert started.json()["typical_duration_seconds"] is None


def test_a_running_run_reports_the_phase_the_pipeline_is_in(monkeypatch):
    """The fake pipeline reports `proposing` and then holds until the test has polled, so
    the poll observes a `running` row mid-stage -- the state the stepper renders."""
    reached_proposing = threading.Event()
    release = threading.Event()

    def _held(settings, *, on_phase=None, **kwargs):
        on_phase(SuggestionRunPhase.READING)
        on_phase(SuggestionRunPhase.PROPOSING)
        reached_proposing.set()
        release.wait(timeout=5)
        on_phase(SuggestionRunPhase.CHECKING)
        return wire_models.KpiSuggestionList(
            suggestions=[], notes="", semantic_snapshot="snap.0"
        )

    monkeypatch.setattr(kpis_module, "_list_kpi_suggestions", _held)
    store = _FakeRunStore()

    with _run_client(monkeypatch, store) as client:
        run_id = client.post("/kpis/suggestions").json()["id"]
        assert reached_proposing.wait(timeout=5)
        mid_run = client.get(f"/kpis/suggestions/{run_id}").json()
        release.set()
        _drain(client)
        done = client.get(f"/kpis/suggestions/{run_id}").json()

    assert mid_run["status"] == "running"
    assert mid_run["phase"] == "proposing"
    # the last stage reported stays on the terminal row
    assert done["status"] == "succeeded"
    assert done["phase"] == "checking"


def test_a_failed_run_keeps_the_phase_it_failed_in(monkeypatch):
    def _refusing(settings, *, on_phase=None, **kwargs):
        on_phase(SuggestionRunPhase.READING)
        on_phase(SuggestionRunPhase.PROPOSING)
        raise ValueError("KpiSuggestionDraftSet expected from the agent; got str: 'no'")

    monkeypatch.setattr(kpis_module, "_list_kpi_suggestions", _refusing)
    store = _FakeRunStore()

    with _run_client(monkeypatch, store) as client:
        run_id = client.post("/kpis/suggestions").json()["id"]
        _drain(client)
        polled = client.get(f"/kpis/suggestions/{run_id}").json()

    assert polled["status"] == "failed"
    assert polled["phase"] == "proposing"


def test_a_phase_reported_after_the_timeout_closed_the_run_does_not_relabel_it(
    monkeypatch,
):
    monkeypatch.setenv("EGPT_RETAIL_SUGGESTION_TIMEOUT_SECONDS", "0.05")

    def _slow(settings, *, on_phase=None, **kwargs):
        on_phase(SuggestionRunPhase.READING)
        time.sleep(0.3)
        on_phase(SuggestionRunPhase.CHECKING)  # the orphaned thread, still reporting
        return wire_models.KpiSuggestionList(
            suggestions=[], notes="", semantic_snapshot="snap.0"
        )

    monkeypatch.setattr(kpis_module, "_list_kpi_suggestions", _slow)
    store = _FakeRunStore()

    with _run_client(monkeypatch, store) as client:
        run_id = client.post("/kpis/suggestions").json()["id"]
        _drain(client)
        time.sleep(0.4)
        polled = client.get(f"/kpis/suggestions/{run_id}").json()

    assert polled["status"] == "failed"
    assert polled["phase"] == "reading"


def test_a_phase_write_that_fails_does_not_fail_the_run(monkeypatch):
    """The phase is a courtesy to the poller; the run's outcome must not depend on it."""

    class _PhaseWriteBroken(_FakeRunStore):
        def mark_phase(self, identifier, phase):
            raise RuntimeError("connection reset by peer")

    def _reporting(settings, *, on_phase=None, **kwargs):
        on_phase(SuggestionRunPhase.READING)
        return wire_models.KpiSuggestionList(
            suggestions=[], notes="", semantic_snapshot="snap.0"
        )

    monkeypatch.setattr(kpis_module, "_list_kpi_suggestions", _reporting)
    warned = []
    monkeypatch.setattr(kpis_module, "log_warning", warned.append)
    store = _PhaseWriteBroken()

    with _run_client(monkeypatch, store) as client:
        run_id = client.post("/kpis/suggestions").json()["id"]
        _drain(client)
        polled = client.get(f"/kpis/suggestions/{run_id}").json()

    assert polled["status"] == "succeeded"
    assert polled["phase"] is None
    assert any("not recorded" in entry for entry in warned)
