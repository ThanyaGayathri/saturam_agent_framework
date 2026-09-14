"""`GET /kpis/{kpi_id}/series` and `POST /kpis/{kpi_id}/compute` (contract 1.1.9), DB-free:
the pure `series_from_gold` date/dimension rule over faked `read_gold` output, and the two
routes through the real router with `read_gold`/`materialise_gold` faked. The warehouse round
trip is `test_kpis_live.py`'s.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from egpt.workflow.types import StepInput, StepOutput
from fastapi import FastAPI
from fastapi.testclient import TestClient

from egpt_retail.api.routers import kpis as kpis_module
from egpt_retail.contracts.kpi import (
    Feasibility,
    FeasibilityVerdict,
    KpiDefinition,
)
from egpt_retail.kpi import series as series_module
from egpt_retail.kpi.series import (
    DEFAULT_SERIES_DAYS,
    MAX_DIMENSIONS,
    read_kpi_series,
    series_from_gold,
)


def _kpi(**overrides) -> KpiDefinition:
    defaults = dict(
        identifier="contact__contact_count",
        name="Contact count",
        description="Distinct contacts.",
        sql="SELECT count(DISTINCT object_id) FROM silver.crm_contact",
        grain="overall",
        business_area="customer",
        source_tables=frozenset({"crm_contact"}),
        concept="Contact",
        unit="Count",
    )
    defaults.update(overrides)
    return KpiDefinition(**defaults)


def _row(as_of, value, **dimension):
    return {
        "as_of": as_of,
        **dimension,
        "value": None if value is None else str(value),
        "computed_at": f"{as_of}T02:20:00+00:00",
        "source_snapshot": "k253.1",
        "run_id": f"run_gold_{as_of.replace('-', '')}",
    }


def _result(rows, history, as_of="2026-09-14"):
    return {
        "as_of": as_of,
        "computed_at": f"{as_of}T02:20:00+00:00",
        "source_snapshot": "k253.1",
        "run_id": f"run_gold_{as_of.replace('-', '')}",
        "rows": rows,
        "history": history,
    }


# ---------------------------------------------------------------------------
# series_from_gold -- pure
# ---------------------------------------------------------------------------


def test_overall_kpi_one_point_per_night_oldest_first_with_exact_delta():
    history = [
        _row("2026-09-12", "1240"),
        _row("2026-09-13", "1262.5"),
        _row("2026-09-14", "1284"),
    ]
    series = series_from_gold(_kpi(), _result([history[-1]], history))
    assert series.kpi_identifier == "contact__contact_count"
    assert series.interval.value == "day"
    assert series.days == DEFAULT_SERIES_DAYS
    assert series.unit == "Count"
    assert series.dimension is None and series.dimensions == []
    assert [p.value for p in series.points] == [1240.0, 1262.5, 1284.0]
    assert [p.at.isoformat() for p in series.points] == [
        "2026-09-12T23:59:59+00:00",
        "2026-09-13T23:59:59+00:00",
        "2026-09-14T23:59:59+00:00",
    ]
    assert series.value == 1284.0
    assert series.delta == 21.5
    assert series.status.value == "fresh"
    assert series.as_of.isoformat() == "2026-09-14T23:59:59+00:00"
    assert series.provenance_ref == "run_gold_20260914"


def test_a_missing_night_is_absent_not_null_and_a_null_value_is_kept():
    history = [
        _row("2026-09-10", "10"),
        _row("2026-09-12", None),
        _row("2026-09-14", "12"),
    ]
    series = series_from_gold(_kpi(), _result([history[-1]], history))
    assert [(p.at.day, p.value) for p in series.points] == [
        (10, 10.0),
        (12, None),
        (14, 12.0),
    ]
    # The previous point is NULL, so there is nothing to subtract from: no delta invented.
    assert series.delta is None


def test_a_single_point_has_no_delta():
    history = [_row("2026-09-14", "7")]
    series = series_from_gold(_kpi(), _result([history[-1]], history))
    assert series.value == 7.0
    assert series.delta is None
    assert len(series.points) == 1


def test_the_latest_date_is_appended_from_rows_when_the_history_read_failed():
    series = series_from_gold(_kpi(), _result([_row("2026-09-14", "7")], []))
    assert [p.value for p in series.points] == [7.0]
    assert series.value == 7.0


def test_never_materialised_is_an_empty_series_not_an_error():
    series = series_from_gold(_kpi(), None, "never materialised", days=7)
    assert series.points == []
    assert series.value is None and series.delta is None
    assert series.status.value == "never_computed"
    assert series.as_of is None and series.computed_at is None
    assert series.days == 7


def test_a_guard_refusal_reads_as_failed():
    series = series_from_gold(_kpi(), None, "guard refused: not allowlisted")
    assert series.status.value == "failed"


def _by_brand_result():
    history = [
        _row("2026-09-01", "1", brand="Acme"),
        _row("2026-09-01", "3", brand="Borealis"),
        _row("2026-09-14", "2", brand="Acme"),
        _row("2026-09-14", "5", brand="Borealis"),
    ]
    return _result(history[-2:], history)


def test_a_dimensioned_kpi_follows_the_first_dimension_by_default_and_lists_the_rest():
    kpi = _kpi(identifier="rfq__quote_requests_by_brand", grain="brand", unit="")
    series = series_from_gold(kpi, _by_brand_result())
    assert series.dimension == {"brand": "Acme"}
    assert series.dimensions == [{"brand": "Acme"}, {"brand": "Borealis"}]
    assert [p.value for p in series.points] == [1.0, 2.0]
    assert series.delta == 1.0


def test_a_dimensioned_kpi_follows_the_requested_dimension():
    kpi = _kpi(identifier="rfq__quote_requests_by_brand", grain="brand")
    series = series_from_gold(kpi, _by_brand_result(), dimension={"brand": "Borealis"})
    assert series.dimension == {"brand": "Borealis"}
    assert [p.value for p in series.points] == [3.0, 5.0]


def test_a_requested_dimension_that_matches_nothing_falls_back_to_the_first():
    kpi = _kpi(identifier="rfq__quote_requests_by_brand", grain="brand")
    series = series_from_gold(kpi, _by_brand_result(), dimension={"brand": "Nobody"})
    assert series.dimension == {"brand": "Acme"}


def test_dimensions_are_capped():
    rows = [
        _row("2026-09-14", str(i), brand=f"b{i}") for i in range(MAX_DIMENSIONS + 5)
    ]
    kpi = _kpi(identifier="rfq__quote_requests_by_brand", grain="brand")
    series = series_from_gold(kpi, _result(rows, rows))
    assert len(series.dimensions) == MAX_DIMENSIONS
    assert series.dimension == {"brand": "b0"}


def test_multi_row_result_with_no_grain_key_columns_has_values_but_no_line():
    # Two rows and nothing to tell them apart: neither is "the" value, so no point is invented.
    rows = [_row("2026-09-14", "1"), _row("2026-09-14", "2")]
    series = series_from_gold(_kpi(), _result(rows, rows))
    assert series.points == []
    assert series.status.value == "never_computed"
    assert series.as_of is not None


# ---------------------------------------------------------------------------
# read_kpi_series -- the read_gold call it makes
# ---------------------------------------------------------------------------


def test_read_kpi_series_asks_read_gold_for_this_kpi_with_history(monkeypatch):
    seen = []

    async def _fake_read_gold(step_input: StepInput) -> StepOutput:
        seen.append(dict(step_input.additional_data or {}))
        return StepOutput(
            content={
                "kpis": {
                    "contact__contact_count": _result([_row("2026-09-14", "7")], [])
                },
                "missing": [],
            },
            success=True,
        )

    monkeypatch.setattr(series_module, "read_gold", _fake_read_gold)
    import asyncio

    series = asyncio.run(read_kpi_series(_kpi(), days=14))
    assert seen == [
        {
            "kpi_identifiers": ["contact__contact_count"],
            "history_days": 14,
            "history_for": ["contact__contact_count"],
        }
    ]
    assert series.value == 7.0 and series.days == 14


def test_read_kpi_series_reports_the_missing_reason(monkeypatch):
    async def _fake_read_gold(step_input: StepInput) -> StepOutput:
        return StepOutput(
            content={
                "kpis": {},
                "missing": [
                    {
                        "kpi_identifier": "contact__contact_count",
                        "reason": "never materialised",
                    }
                ],
            },
            success=True,
        )

    monkeypatch.setattr(series_module, "read_gold", _fake_read_gold)
    import asyncio

    series = asyncio.run(read_kpi_series(_kpi()))
    assert series.status.value == "never_computed" and series.points == []


# ---------------------------------------------------------------------------
# the routes, through the real router
# ---------------------------------------------------------------------------


class _FakeStore:
    def __init__(
        self, kpis, *, verdict=FeasibilityVerdict.READY, missing_tables=frozenset()
    ):
        self._by_id = {k.identifier: k for k in kpis}
        self._verdict = verdict
        self._missing = frozenset(missing_tables)

    def get(self, identifier):
        return self._by_id.get(identifier)

    def feasibility(self, reader, kpis=None):
        return {
            k.identifier: Feasibility(
                kpi_identifier=k.identifier,
                verdict=self._verdict,
                missing_tables=self._missing,
            )
            for k in (kpis or self._by_id.values())
        }


class _FakeSnapshot:
    version = "snap.1"


class _FakeReader:
    def snapshot(self):
        return _FakeSnapshot()


@contextmanager
def _fake_semantic_reader(settings):
    yield _FakeReader()


def _client(monkeypatch, store):
    monkeypatch.setattr(kpis_module, "open_semantic_reader", _fake_semantic_reader)
    app = FastAPI()
    app.include_router(kpis_module.router)
    app.dependency_overrides[kpis_module._kpi_store] = lambda: store
    return TestClient(app)


def _gold_with(result_by_id, missing=()):
    async def _fake_read_gold(step_input: StepInput) -> StepOutput:
        return StepOutput(
            content={"kpis": result_by_id, "missing": list(missing)}, success=True
        )

    return _fake_read_gold


def test_get_series_returns_the_series_for_a_registered_kpi(monkeypatch):
    kpi = _kpi()
    history = [_row("2026-09-13", "1"), _row("2026-09-14", "3")]
    monkeypatch.setattr(
        series_module,
        "read_gold",
        _gold_with({kpi.identifier: _result([history[-1]], history)}),
    )
    client = _client(monkeypatch, _FakeStore([kpi]))

    response = client.get(f"/kpis/{kpi.identifier}/series")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kpi_identifier"] == kpi.identifier
    assert body["interval"] == "day" and body["days"] == 30
    assert [p["value"] for p in body["points"]] == [1.0, 3.0]
    assert body["points"][-1]["at"] == "2026-09-14T23:59:59Z"
    assert body["value"] == 3.0 and body["delta"] == 2.0
    assert body["status"] == "fresh"


def test_get_series_passes_days_and_dimension_through(monkeypatch):
    kpi = _kpi(identifier="rfq__quote_requests_by_brand", grain="brand")
    seen = []

    async def _fake_read_gold(step_input: StepInput) -> StepOutput:
        seen.append(dict(step_input.additional_data or {}))
        return StepOutput(
            content={"kpis": {kpi.identifier: _by_brand_result()}, "missing": []},
            success=True,
        )

    monkeypatch.setattr(series_module, "read_gold", _fake_read_gold)
    client = _client(monkeypatch, _FakeStore([kpi]))

    response = client.get(
        f"/kpis/{kpi.identifier}/series",
        params={"days": 7, "dimension": '{"brand": "Borealis"}'},
    )

    assert response.status_code == 200, response.text
    assert seen[0]["history_days"] == 7
    body = response.json()
    assert body["dimension"] == {"brand": "Borealis"}
    assert [p["value"] for p in body["points"]] == [3.0, 5.0]


def test_get_series_never_computed_is_a_200_with_no_points(monkeypatch):
    kpi = _kpi()
    monkeypatch.setattr(
        series_module,
        "read_gold",
        _gold_with(
            {}, [{"kpi_identifier": kpi.identifier, "reason": "never materialised"}]
        ),
    )
    client = _client(monkeypatch, _FakeStore([kpi]))

    response = client.get(f"/kpis/{kpi.identifier}/series")

    assert response.status_code == 200
    body = response.json()
    assert body["points"] == [] and body["value"] is None
    assert body["status"] == "never_computed"


def test_get_series_404s_on_an_unknown_kpi(monkeypatch):
    client = _client(monkeypatch, _FakeStore([]))
    assert client.get("/kpis/nope/series").status_code == 404


@pytest.mark.parametrize("bad", ['"Acme"', "[1]", "{not json"])
def test_get_series_422s_on_a_dimension_that_is_not_a_json_object(monkeypatch, bad):
    kpi = _kpi()
    client = _client(monkeypatch, _FakeStore([kpi]))
    response = client.get(f"/kpis/{kpi.identifier}/series", params={"dimension": bad})
    assert response.status_code == 422
    assert "JSON object" in response.json()["detail"]


@pytest.mark.parametrize("days", [0, 366])
def test_get_series_bounds_days(monkeypatch, days):
    kpi = _kpi()
    client = _client(monkeypatch, _FakeStore([kpi]))
    response = client.get(f"/kpis/{kpi.identifier}/series", params={"days": days})
    assert response.status_code == 422


def _materialising(entry, *, seen=None):
    async def _fake_materialise_gold(step_input: StepInput) -> StepOutput:
        if seen is not None:
            seen.append(dict(step_input.additional_data or {}))
        return StepOutput(
            content={
                "as_of": "2026-09-15",
                "run_id": "api_x",
                "materialised": [],
                "failed": [],
                "skipped": [],
                "results": [entry],
            },
            success=True,
        )

    return _fake_materialise_gold


def test_compute_runs_the_gold_pass_for_this_kpi_only_and_reports_the_outcome(
    monkeypatch,
):
    kpi = _kpi()
    seen = []
    monkeypatch.setattr(
        kpis_module,
        "materialise_gold",
        _materialising(
            {
                "kpi_identifier": kpi.identifier,
                "run_id": "api_x",
                "as_of": "2026-09-15",
                "status": "materialised",
                "grain": "overall",
                "rows_written": 1,
                "error": None,
                "reason": None,
            },
            seen=seen,
        ),
    )
    client = _client(monkeypatch, _FakeStore([kpi]))

    response = client.post(f"/kpis/{kpi.identifier}/compute")

    assert response.status_code == 200, response.text
    assert seen[0]["kpi_identifiers"] == [kpi.identifier]
    assert seen[0]["run_id"].startswith("api_")
    assert response.json() == {
        "kpi_identifier": kpi.identifier,
        "as_of": "2026-09-15",
        "status": "materialised",
        "rows_written": 1,
        "run_id": "api_x",
        "error": None,
        "reason": None,
    }


def test_compute_a_failed_formula_is_a_200_saying_failed(monkeypatch):
    kpi = _kpi()
    monkeypatch.setattr(
        kpis_module,
        "materialise_gold",
        _materialising(
            {
                "kpi_identifier": kpi.identifier,
                "run_id": "api_x",
                "as_of": "2026-09-15",
                "status": "failed",
                "grain": "overall",
                "rows_written": 0,
                "error": "column crm_contact.nope does not exist",
                "reason": None,
            }
        ),
    )
    client = _client(monkeypatch, _FakeStore([kpi]))

    response = client.post(f"/kpis/{kpi.identifier}/compute")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert "does not exist" in body["error"]


def test_compute_409s_when_the_kpi_is_not_ready(monkeypatch):
    kpi = _kpi()
    calls = []
    monkeypatch.setattr(
        kpis_module,
        "materialise_gold",
        _materialising({}, seen=calls),
    )
    store = _FakeStore(
        [kpi],
        verdict=FeasibilityVerdict.BLOCKED,
        missing_tables={"crm_contact"},
    )
    client = _client(monkeypatch, store)

    response = client.post(f"/kpis/{kpi.identifier}/compute")

    assert response.status_code == 409
    assert "blocked" in response.json()["detail"]
    assert "crm_contact" in response.json()["detail"]
    assert calls == []


def test_compute_503s_when_gold_refuses_to_run(monkeypatch):
    kpi = _kpi()

    async def _refusing(step_input: StepInput) -> StepOutput:
        return StepOutput(
            content={"refused": "a landing-to-silver load holds the advisory lock"},
            success=False,
            error="a landing-to-silver load holds the advisory lock",
            stop=True,
        )

    monkeypatch.setattr(kpis_module, "materialise_gold", _refusing)
    client = _client(monkeypatch, _FakeStore([kpi]))

    response = client.post(f"/kpis/{kpi.identifier}/compute")

    assert response.status_code == 503
    assert "advisory lock" in response.json()["detail"]


def test_compute_404s_on_an_unknown_kpi(monkeypatch):
    client = _client(monkeypatch, _FakeStore([]))
    assert client.post("/kpis/nope/compute").status_code == 404


def test_dashboards_data_still_shares_the_date_helpers():
    # `dashboards/data.py` imports the three helpers from `kpi/series.py` rather than owning
    # copies; a divergence between the tile series and the KPI series would be a bug.
    from egpt_retail.dashboards import data as dashboards_data

    assert dashboards_data._end_of_business_day is series_module.end_of_business_day
    assert dashboards_data._history_by_date is series_module.history_by_date
    assert dashboards_data._status_for_missing is series_module.status_for_missing
