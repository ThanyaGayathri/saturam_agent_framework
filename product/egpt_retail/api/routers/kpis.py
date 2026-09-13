"""B1's real router (issue #229, part of #222's frontend/backend wiring split): read-only
access to the KPI registry -- ``GET /kpis``, ``GET /kpis/{id}``, ``GET /kpis/{id}/feasibility``.

Write endpoints (create/update/delete) are #231/B2, along with the registry schema extension
this router works around: the wire ``Kpi`` model carries fields --``id`` (distinct from
``identifier``), ``subject_area``, ``level``, ``unit``, ``industry_benchmark``, ``source``,
``dataset_ids``, ``process_ids``, ``updated_at`` -- that ``KpiDefinition`` and
``semantic.kpi_definition`` do not store yet. Until B2 adds them, ``_kpi_out`` below fills them
with honest static defaults (``id = identifier``, ``source = shipped``, ``active = True``, the
rest empty/null) rather than inventing data or blocking on a schema migration.

Feasibility here is the join-aware ``KpiRegistryStore.feasibility`` (checks recorded unjoinable
table pairs), not the plain set-difference ``assess_registry`` that ``dashboards.py``'s own
tile-readiness gate uses -- a KPI could show ``blocked`` here for a known-bad join and still be
accepted by ``POST /dashboards`` today, since that gate isn't join-aware. That is a pre-existing
gap in ``dashboards.py``, not something this router fixes.
"""

from __future__ import annotations

from typing import Iterator, Mapping, Optional, Sequence, Tuple

from egpt.os.scopes import has_required_scopes
from fastapi import APIRouter, Depends, HTTPException, Request

from egpt_retail.api import models as wire
from egpt_retail.app.db import pooled_control_plane_connection
from egpt_retail.app.scopes import caller_scopes, require_scopes
from egpt_retail.app.semantic import open_semantic_reader
from egpt_retail.app.settings import RetailSettings
from egpt_retail.contracts.kpi import (
    Feasibility,
    KpiDefinition,
    KpiParameter,
    TimeWindow,
)
from egpt_retail.kpi.store import KpiRegistryStore

__all__ = ["router"]

router = APIRouter()


# --- store dependency, feasibility, scope-gated field -------------------------------------


def _kpi_store() -> Iterator[KpiRegistryStore]:
    settings = RetailSettings()
    connection = pooled_control_plane_connection(settings)
    try:
        yield KpiRegistryStore(connection)
    finally:
        connection.close()


def _feasibility_map(
    settings: RetailSettings,
    store: KpiRegistryStore,
    kpis: Sequence[KpiDefinition],
) -> Tuple[Mapping[str, Feasibility], str]:
    """Feasibility for exactly ``kpis`` -- the identifiers the caller already fetched with its
    own read -- plus the snapshot version it was read against.

    Takes the caller's already-fetched definitions rather than defaulting to a second,
    independent registry read: the previous shape called ``store.list()``/``store.get()``
    once and then had ``KpiRegistryStore.feasibility`` re-read the *entire* registry
    separately, with no shared snapshot between the two reads -- a KPI deleted or renamed in
    between raised an uncaught ``KeyError`` when the route indexed the result by identifier.
    Passing ``kpis`` through removes the second read (and, for a single-KPI route, the
    whole-registry computation neither ``get_kpi`` nor ``get_kpi_feasibility`` needs) instead
    of merely guarding the lookup.
    """
    with open_semantic_reader(settings) as reader:
        feasibility = store.feasibility(reader, kpis)
        snapshot_version = reader.snapshot().version
    return feasibility, snapshot_version


def _may_read_sql(request: Request) -> bool:
    """Whether this caller may see ``Kpi.sql``. The contract scopes it to ``kpis:write``
    holders (a viewer sees the field absent) -- same no-op-when-authorization-is-off posture
    ``require_scopes`` already has, so a local/dev deployment always sees it.
    """
    if not getattr(request.state, "authorization_enabled", False):
        return True
    user_scopes, admin_scope = caller_scopes(request)
    return has_required_scopes(user_scopes, ["kpis:write"], admin_scope=admin_scope)


# --- wire conversions -----------------------------------------------------------------------


def _parameter_out(parameter: KpiParameter) -> wire.KpiParameter:
    return wire.KpiParameter(
        name=parameter.name,
        kind=wire.ParameterKind(parameter.kind.value),
        description=parameter.description,
    )


def _time_window_out(window: TimeWindow) -> wire.TimeWindow:
    return wire.TimeWindow(
        length=window.length, unit=wire.TimeWindowUnit(window.unit.value)
    )


def _feasibility_out(
    feasibility: Feasibility, snapshot_version: str
) -> wire.Feasibility:
    return wire.Feasibility(
        kpi_id=feasibility.kpi_identifier,
        verdict=wire.FeasibilityVerdict(feasibility.verdict.value),
        missing_tables=sorted(feasibility.missing_tables),
        rationale=feasibility.rationale,
        semantic_snapshot=snapshot_version,
    )


def _kpi_out(
    kpi: KpiDefinition, feasibility: wire.Feasibility, *, include_sql: bool
) -> wire.Kpi:
    """Domain -> wire. Fields with no backing data yet (see module docstring) get honest
    static defaults rather than invented values, until #231/B2's schema extension lands.
    """
    return wire.Kpi(
        id=kpi.identifier,
        identifier=kpi.identifier,
        name=kpi.name,
        description=kpi.description,
        business_area=kpi.business_area,
        subject_area="",
        concept=kpi.concept,
        grain=kpi.grain,
        result_shape=wire.ResultShape(kpi.result_shape.value),
        dialect=wire.Dialect(kpi.dialect.value),
        sql=kpi.sql if include_sql else None,
        source_tables=sorted(kpi.source_tables),
        parameters=[_parameter_out(p) for p in kpi.parameters],
        time_window=_time_window_out(kpi.time_window) if kpi.time_window else None,
        validated_against_snapshot=kpi.validated_against_snapshot,
        level=None,
        unit="",
        industry_benchmark="",
        active=True,
        source=wire.KpiOrigin.shipped,
        dataset_ids=None,
        process_ids=None,
        feasibility=feasibility,
        updated_at=None,
    )


def _matches_filters(
    kpi: wire.Kpi,
    subject_area: Optional[str],
    active: Optional[bool],
    feasibility: Optional[wire.FeasibilityVerdict],
) -> bool:
    """Pure, so ``GET /kpis``'s in-router filtering is unit-testable without a request. Honest
    about ``subject_area``/``active`` currently being static defaults (see ``_kpi_out``): a
    filter naming a value no KPI carries today correctly returns nothing, rather than lying.
    """
    if subject_area is not None and kpi.subject_area != subject_area:
        return False
    if active is not None and kpi.active != active:
        return False
    if feasibility is not None and kpi.feasibility.verdict != feasibility:
        return False
    return True


# --- routes -----------------------------------------------------------------------------------


@router.get("/kpis", response_model=list[wire.Kpi])
def list_kpis(
    request: Request,
    business_area: Optional[str] = None,
    subject_area: Optional[str] = None,
    active: Optional[bool] = None,
    feasibility: Optional[wire.FeasibilityVerdict] = None,
    store: KpiRegistryStore = Depends(_kpi_store),
    _: None = Depends(require_scopes("kpis:read")),
):
    try:
        definitions = store.list(business_area=business_area)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    settings = RetailSettings()
    feasibility_map, snapshot_version = _feasibility_map(settings, store, definitions)
    include_sql = _may_read_sql(request)
    kpis = [
        _kpi_out(
            definition,
            _feasibility_out(feasibility_map[definition.identifier], snapshot_version),
            include_sql=include_sql,
        )
        for definition in definitions
    ]
    return [k for k in kpis if _matches_filters(k, subject_area, active, feasibility)]


@router.get("/kpis/{kpi_id}", response_model=wire.Kpi)
def get_kpi(
    kpi_id: str,
    request: Request,
    store: KpiRegistryStore = Depends(_kpi_store),
    _: None = Depends(require_scopes("kpis:read")),
):
    definition = store.get(kpi_id)
    if definition is None:
        raise HTTPException(status_code=404, detail=f"No KPI {kpi_id!r}.")
    settings = RetailSettings()
    feasibility_map, snapshot_version = _feasibility_map(settings, store, [definition])
    return _kpi_out(
        definition,
        _feasibility_out(feasibility_map[definition.identifier], snapshot_version),
        include_sql=_may_read_sql(request),
    )


@router.get("/kpis/{kpi_id}/feasibility", response_model=wire.Feasibility)
def get_kpi_feasibility(
    kpi_id: str,
    store: KpiRegistryStore = Depends(_kpi_store),
    _: None = Depends(require_scopes("kpis:read")),
):
    definition = store.get(kpi_id)
    if definition is None:
        raise HTTPException(status_code=404, detail=f"No KPI {kpi_id!r}.")
    settings = RetailSettings()
    feasibility_map, snapshot_version = _feasibility_map(settings, store, [definition])
    return _feasibility_out(feasibility_map[definition.identifier], snapshot_version)
