"""Unit tests for #229/B1 and #231/B2's DB-free halves: the wire<->domain mappers, the
in-router filtering helper, the scope check that gates `Kpi.sql` visibility, and the
create/update/delete logic -- everything `api/routers/kpis.py` does that needs no Postgres or
Neo4j. The store/feasibility calls against a real registry and semantic graph are exercised
only by `test_kpis_live.py` (gated `EGPT_INTEGRATION_POSTGRES` + `EGPT_INTEGRATION_NEO4J`), the
same split `test_r13_dashboards.py` documents for R13.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from egpt_retail.api import models as wire_models
from egpt_retail.api.routers.kpis import (
    _carry_forward_validation,
    _derive_identifier,
    _feasibility_out,
    _kpi_from_write,
    _kpi_out,
    _matches_filters,
    _may_read_sql,
    _slugify,
    create_kpi,
    delete_kpi,
    get_kpi,
    list_kpis,
    update_kpi,
)
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
from egpt_retail.kpi.store import KpiConcurrentUpdateError, KpiDeleteResult


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

    def feasibility(self, reader, *, kpis=None):
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

    def delete_if_unreferenced(self, identifier):
        if identifier not in self._by_id:
            return KpiDeleteResult(removed=False)
        del self._by_id[identifier]
        return KpiDeleteResult(removed=True)

    def feasibility(self, reader, *, kpis=None):
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


class _ReferencedKpiStore(_FakeKpiStore):
    """``delete_if_unreferenced`` refuses with names, as it would for a KPI a dashboard --
    including one the deleting caller cannot even see -- still has a tile for."""

    def __init__(self, existing, referenced_by):
        super().__init__(existing=existing)
        self._referenced_by = referenced_by

    def delete_if_unreferenced(self, identifier):
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
