"""Unit tests for #229/B1's DB-free halves: the wire<->domain mappers, the in-router filtering
helper, and the scope check that gates `Kpi.sql` visibility -- everything `api/routers/kpis.py`
does that needs no Postgres or Neo4j. The store/feasibility calls against a real registry and
semantic graph are exercised only by `test_kpis_live.py` (gated `EGPT_INTEGRATION_POSTGRES` +
`EGPT_INTEGRATION_NEO4J`), the same split `test_r13_dashboards.py` documents for R13.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from fastapi import HTTPException

from egpt_retail.api.routers import kpis as kpis_module
from egpt_retail.api.routers.kpis import (
    _feasibility_out,
    _kpi_out,
    _matches_filters,
    _may_read_sql,
    get_kpi,
    get_kpi_feasibility,
    list_kpis,
)
from egpt_retail.contracts.kpi import (
    Feasibility,
    FeasibilityReason,
    FeasibilityVerdict,
    KpiDefinition,
    KpiParameter,
    ParameterKind,
    TimeWindow,
    TimeWindowUnit,
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


def test_kpi_out_fields_with_no_backing_data_get_honest_static_defaults():
    kpi = _kpi()
    feasibility = _feasibility_out(_ready_feasibility(kpi.identifier), "snap.7")
    wire = _kpi_out(kpi, feasibility, include_sql=True)

    assert wire.subject_area == ""
    assert wire.level is None
    assert wire.unit == ""
    assert wire.industry_benchmark == ""
    assert wire.active is True
    assert wire.source.value == "shipped"
    assert wire.dataset_ids is None
    assert wire.process_ids is None
    assert wire.updated_at is None


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

    def feasibility(self, reader, kpis):
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
