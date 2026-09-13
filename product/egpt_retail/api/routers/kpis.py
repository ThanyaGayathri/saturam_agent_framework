"""B1+B2's real router (issues #229/#231, part of #222's frontend/backend wiring split):
``GET/POST/PATCH/DELETE /kpis``, ``GET /kpis/{id}/feasibility``.

B2 gave the wire ``Kpi`` model's presentation fields (``subject_area``, ``level``, ``unit``,
``industry_benchmark``, ``source``, ``dataset_ids``, ``process_ids``, ``updated_at``) real
columns on ``semantic.kpi_definition`` and real values on ``KpiDefinition`` -- see
``contracts/kpi.py``'s ``KpiLevel``/``KpiOrigin`` and the fields added there. ``id`` still
equals ``identifier`` on the wire: no consumer references a separate id, and there is no
precedent anywhere for minting a ``kpi_XXXX``-style one, so B2 did not invent one.

Feasibility here is the join-aware ``KpiRegistryStore.feasibility`` (checks recorded unjoinable
table pairs), not the plain set-difference ``assess_registry`` that ``dashboards.py``'s own
tile-readiness gate uses -- a KPI could show ``blocked`` here for a known-bad join and still be
accepted by ``POST /dashboards`` today, since that gate isn't join-aware. That is a pre-existing
gap in ``dashboards.py``, not something this router fixes.

``DELETE``'s 409 reference check covers dashboard tiles only, not the full "dashboard tile,
alert definition, or activated use case" the contract's own summary line names: alert
definitions live behind a SQLAlchemy ``Engine`` (a different connection type than every other
dependency here) with no router of their own yet, and use cases have no reference-tracking
store at all. Wiring a second connection type into this router for a subsystem nothing else
touches was judged disproportionate to B2 -- flagged here and in #231's PR, not silently
narrowed.
"""

from __future__ import annotations

import re
from typing import Any, Iterator, Mapping, Optional, Sequence, Tuple

from egpt.os.scopes import has_required_scopes
from fastapi import APIRouter, Depends, HTTPException, Request

from egpt_retail.api import models as wire
from egpt_retail.app.db import pooled_control_plane_connection
from egpt_retail.app.scopes import require_scopes
from egpt_retail.app.semantic import open_semantic_reader
from egpt_retail.app.settings import RetailSettings
from egpt_retail.contracts.kpi import (
    Dialect,
    Feasibility,
    KpiDefinition,
    KpiLevel,
    KpiOrigin,
    KpiParameter,
    ParameterKind,
    ResultShape,
    TimeWindow,
    TimeWindowUnit,
    kpi_identifier,
)
from egpt_retail.kpi.store import KpiConcurrentUpdateError, KpiRegistryStore

__all__ = ["router"]

router = APIRouter()

_SLUG_RE = re.compile(r"[^a-z0-9]+")


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
    *,
    kpis: Optional[Sequence[KpiDefinition]] = None,
) -> Tuple[Mapping[str, Feasibility], str]:
    """Feasibility for the given (or, by default, every registered) KPI, plus the snapshot
    version it was read against.

    ``kpis=None`` (list, detail) computes the whole registry in one semantic-reader session --
    there is no per-identifier variant, so a single-KPI route pays the same cost as the list
    route rather than a cheaper one existing. ``kpis=[definition]`` (create, update, via
    :func:`_respond_with_kpi`) computes feasibility for exactly the row just written instead,
    so the answer comes from that definition rather than a second, independent read of the
    whole table that a concurrent delete could race.
    """
    with open_semantic_reader(settings) as reader:
        feasibility = store.feasibility(reader, kpis=kpis)
        snapshot_version = reader.snapshot().version
    return feasibility, snapshot_version


def _may_read_sql(request: Request) -> bool:
    """Whether this caller may see ``Kpi.sql``. The contract scopes it to ``kpis:write``
    holders (a viewer sees the field absent) -- same no-op-when-authorization-is-off posture
    ``require_scopes`` already has, so a local/dev deployment always sees it.
    """
    if not getattr(request.state, "authorization_enabled", False):
        return True
    user_scopes = getattr(request.state, "scopes", None) or []
    admin_scope = getattr(request.state, "admin_scope", None)
    return has_required_scopes(
        list(user_scopes), ["kpis:write"], admin_scope=admin_scope
    )


# --- wire conversions -----------------------------------------------------------------------


def _parameter_out(parameter: KpiParameter) -> wire.KpiParameter:
    return wire.KpiParameter(
        name=parameter.name,
        kind=wire.ParameterKind(parameter.kind.value),
        description=parameter.description,
    )


def _parameter_from_write(parameter: wire.KpiParameter) -> KpiParameter:
    return KpiParameter(
        name=parameter.name,
        kind=ParameterKind(parameter.kind.value),
        description=parameter.description or "",
    )


def _time_window_out(window: TimeWindow) -> wire.TimeWindow:
    return wire.TimeWindow(
        length=window.length, unit=wire.TimeWindowUnit(window.unit.value)
    )


def _time_window_from_write(window: wire.TimeWindow) -> TimeWindow:
    return TimeWindow(length=window.length, unit=TimeWindowUnit(window.unit.value))


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
    return wire.Kpi(
        id=kpi.identifier,
        identifier=kpi.identifier,
        name=kpi.name,
        description=kpi.description,
        business_area=kpi.business_area,
        subject_area=kpi.subject_area or "",
        concept=kpi.concept,
        grain=kpi.grain,
        result_shape=wire.ResultShape(kpi.result_shape.value),
        dialect=wire.Dialect(kpi.dialect.value),
        sql=kpi.sql if include_sql else None,
        source_tables=sorted(kpi.source_tables),
        parameters=[_parameter_out(p) for p in kpi.parameters],
        time_window=_time_window_out(kpi.time_window) if kpi.time_window else None,
        validated_against_snapshot=kpi.validated_against_snapshot,
        level=wire.KpiLevel(kpi.level.value) if kpi.level else None,
        unit=kpi.unit or "",
        industry_benchmark=kpi.industry_benchmark or "",
        active=kpi.active,
        source=wire.KpiOrigin(kpi.source.value),
        # `or None` makes an explicit `dataset_ids=[]` indistinguishable on the wire from a
        # KPI that never set it at all -- both round-trip as `None`. Accepted, not fixed: the
        # contract has no third state to tell them apart, and no consumer has needed to yet.
        dataset_ids=list(kpi.dataset_ids) or None,
        process_ids=list(kpi.process_ids) or None,
        feasibility=feasibility,
        updated_at=kpi.updated_at,
    )


def _slugify(text: str) -> str:
    """Lower-cases and collapses runs of non-alphanumeric characters to a single ``_``, so the
    result always satisfies ``str.isidentifier()`` -- ``KpiDefinition.__post_init__`` enforces
    that regardless. The contract's own illustrative examples (e.g. ``supplier__fill_rate`` from
    the name "Supplier Fill Rate") imply extra dedup logic against ``concept`` that is not
    specified or tested anywhere; not reverse-engineered here.
    """
    slug = _SLUG_RE.sub("_", text.lower()).strip("_")
    return slug or "kpi"


def _derive_identifier(write: wire.KpiWrite) -> str:
    if write.identifier:
        return write.identifier
    if not write.concept:
        raise HTTPException(
            status_code=422,
            detail=(
                "identifier or concept must be given so an identifier can be derived from "
                "the name."
            ),
        )
    return kpi_identifier(write.concept, _slugify(write.name))


def _carry_forward_validation(
    existing: Optional[KpiDefinition], new_sql: str, new_source_tables: Sequence[str]
) -> Optional[str]:
    """ "A formula edit re-validates and moves the KPI back to review against the current
    snapshot" -- the contract's own words for ``updateKpi``. Carries the existing validation
    stamp forward only when neither the formula nor its declared tables changed; a brand-new
    KPI (no ``existing``) always starts unvalidated.
    """
    if existing is None:
        return None
    if existing.sql != new_sql:
        return None
    if existing.source_tables != frozenset(t.lower() for t in new_source_tables):
        return None
    return existing.validated_against_snapshot


def _kpi_from_write(
    write: wire.KpiWrite, *, identifier: str, existing: Optional[KpiDefinition]
) -> KpiDefinition:
    """Wire -> domain, shared by create and update.

    ``existing`` is ``None`` for a create (full-resubmit semantics: there is nothing to
    preserve, so an omitted optional field takes its create-time default) and the current row
    for an update. On update, every *optional* ``KpiWrite`` field the caller omits (``None`` on
    the wire) carries forward from ``existing`` rather than resetting to that same
    create-time default -- PATCH's "full-shape resubmit" would otherwise silently deactivate an
    inactive-but-unmentioned KPI, drop its ``unit``/``subject_area``/etc., the same TOCTOU-
    adjacent footgun the #240 review flagged. ``source`` already worked this way (a shipped
    KPI must not be silently reclassified as customer-authored just because ``KpiWrite`` has no
    ``source`` field to resend); the fields below now match it. Required fields (``name``,
    ``description``, ``business_area``, ``grain``, ``sql``, ``source_tables``) are unaffected --
    PATCH still requires resending those in full, as the docstrings on the routes below say.
    """

    def _carried(value: Any, attr: str, default: Any) -> Any:
        if value is not None:
            return value
        if existing is not None:
            return getattr(existing, attr)
        return default

    result_shape = _carried(
        ResultShape(write.result_shape.value) if write.result_shape else None,
        "result_shape",
        ResultShape.SCALAR,
    )
    dialect = _carried(
        Dialect(write.dialect.value) if write.dialect else None,
        "dialect",
        Dialect.POSTGRES,
    )
    parameters = _carried(
        tuple(_parameter_from_write(p) for p in write.parameters)
        if write.parameters is not None
        else None,
        "parameters",
        (),
    )
    time_window = _carried(
        _time_window_from_write(write.time_window) if write.time_window else None,
        "time_window",
        None,
    )
    level = _carried(
        KpiLevel(write.level.value) if write.level else None, "level", None
    )
    active = _carried(write.active, "active", True)
    dataset_ids = _carried(
        tuple(write.dataset_ids) if write.dataset_ids is not None else None,
        "dataset_ids",
        (),
    )
    process_ids = _carried(
        tuple(write.process_ids) if write.process_ids is not None else None,
        "process_ids",
        (),
    )

    return KpiDefinition(
        identifier=identifier,
        name=write.name,
        description=write.description,
        sql=write.sql,
        grain=write.grain,
        business_area=write.business_area,
        source_tables=frozenset(write.source_tables),
        result_shape=result_shape,
        dialect=dialect,
        parameters=parameters,
        time_window=time_window,
        validated_against_snapshot=_carry_forward_validation(
            existing, write.sql, write.source_tables
        ),
        concept=_carried(write.concept, "concept", None),
        subject_area=_carried(write.subject_area, "subject_area", None),
        level=level,
        unit=_carried(write.unit, "unit", None),
        industry_benchmark=_carried(
            write.industry_benchmark, "industry_benchmark", None
        ),
        active=active,
        source=existing.source if existing is not None else KpiOrigin.CUSTOMER,
        dataset_ids=dataset_ids,
        process_ids=process_ids,
    )


def _matches_filters(
    kpi: wire.Kpi,
    subject_area: Optional[str],
    active: Optional[bool],
    feasibility: Optional[wire.FeasibilityVerdict],
) -> bool:
    """Pure, so ``GET /kpis``'s in-router filtering is unit-testable without a request."""
    if subject_area is not None and kpi.subject_area != subject_area:
        return False
    if active is not None and kpi.active != active:
        return False
    if feasibility is not None and kpi.feasibility.verdict != feasibility:
        return False
    return True


def _respond_with_kpi(
    definition: KpiDefinition,
    request: Request,
    store: KpiRegistryStore,
    *,
    single: bool = False,
) -> wire.Kpi:
    """Feasibility lookup + wire assembly, shared by ``get_kpi``, ``create_kpi`` and
    ``update_kpi`` -- factored out because the block was duplicated near-verbatim across all
    three (#240 review, author's own finding).

    ``single=True`` (create/update, called with the exact row just written) passes
    ``kpis=[definition]`` through to :func:`_feasibility_map`, so feasibility comes from that
    definition rather than a second, independent read of the whole registry -- closing the
    TOCTOU window a concurrent ``DELETE`` could otherwise open between the write and this
    lookup. ``single=False`` (get) keeps computing feasibility for the whole registry, matching
    ``list_kpis``'s existing cost profile. Either way, the lookup uses ``.get()`` rather than
    ``[...]``: a concurrent delete landing in the ``single=False`` gap between ``store.get()``
    and this call would otherwise raise an unguarded ``KeyError`` FastAPI turns into a raw 500,
    rather than the 404 a vanished KPI should produce.
    """
    settings = RetailSettings()
    feasibility_map, snapshot_version = _feasibility_map(
        settings, store, kpis=[definition] if single else None
    )
    feasibility = feasibility_map.get(definition.identifier)
    if feasibility is None:
        raise HTTPException(
            status_code=404, detail=f"No KPI {definition.identifier!r}."
        )
    return _kpi_out(
        definition,
        _feasibility_out(feasibility, snapshot_version),
        include_sql=_may_read_sql(request),
    )


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
    feasibility_map, snapshot_version = _feasibility_map(settings, store)
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


@router.post("/kpis", response_model=wire.Kpi, status_code=201)
def create_kpi(
    body: wire.KpiWrite,
    request: Request,
    store: KpiRegistryStore = Depends(_kpi_store),
    _: None = Depends(require_scopes("kpis:write")),
):
    identifier = _derive_identifier(body)
    # A fast-path optimization only -- not the 409 guard. Two concurrent POSTs for the same
    # identifier can both pass this check; `store.create`'s `INSERT ... ON CONFLICT DO NOTHING`
    # below is the actual source of truth, closing the check-then-upsert race this pre-check
    # alone would leave open (#240 review).
    if store.get(identifier) is not None:
        raise HTTPException(
            status_code=409,
            detail=f"{identifier} already exists; use PATCH /kpis/{identifier} to edit it.",
        )
    try:
        definition = _kpi_from_write(body, identifier=identifier, existing=None)
        saved = store.create(definition)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if saved is None:
        # Lost the race: another create for this identifier committed between the pre-check
        # above and `store.create`'s INSERT.
        raise HTTPException(
            status_code=409,
            detail=f"{identifier} already exists; use PATCH /kpis/{identifier} to edit it.",
        )
    return _respond_with_kpi(saved, request, store, single=True)


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
    return _respond_with_kpi(definition, request, store)


@router.patch("/kpis/{kpi_id}", response_model=wire.Kpi)
def update_kpi(
    kpi_id: str,
    body: wire.KpiWrite,
    request: Request,
    store: KpiRegistryStore = Depends(_kpi_store),
    _: None = Depends(require_scopes("kpis:write")),
):
    """Every optional field the body omits carries forward from the existing row rather than
    resetting to a default (see ``_kpi_from_write``); required fields (``name``, ``description``,
    ``business_area``, ``grain``, ``sql``, ``source_tables``) must still be resent in full on
    every call.
    """
    existing = store.get(kpi_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"No KPI {kpi_id!r}.")
    try:
        # `body.identifier` (if given) is ignored: the path is authoritative. Honoring a
        # different identifier here would be a rename, which `store.save`'s upsert-by-identifier
        # cannot express -- it would insert a second row and orphan the first.
        definition = _kpi_from_write(body, identifier=kpi_id, existing=existing)
        saved = store.save_if_unchanged(definition, existing.updated_at)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KpiConcurrentUpdateError as exc:
        # The lost-update race a plain upsert-by-identifier used to allow: another write (or a
        # delete) landed between this route's `store.get()` above and the conditional update.
        # Whichever request loses this race must not silently overwrite the other's write, so
        # it is refused rather than retried automatically -- the caller re-fetches and decides.
        raise HTTPException(
            status_code=409,
            detail=(
                f"{kpi_id} was modified concurrently; re-fetch GET /kpis/{kpi_id} and retry "
                f"the PATCH."
            ),
        ) from exc

    return _respond_with_kpi(saved, request, store, single=True)


@router.delete("/kpis/{kpi_id}", status_code=204)
def delete_kpi(
    kpi_id: str,
    request: Request,
    store: KpiRegistryStore = Depends(_kpi_store),
    _: None = Depends(require_scopes("kpis:write")),
):
    """Refuses (409) while any dashboard still has a tile naming this KPI -- checked across
    every dashboard regardless of owner or visibility (``KpiRegistryStore.
    delete_if_unreferenced`` / ``DashboardStore.list_referencing``), not only ones the deleting
    caller could see, and as part of the same statement that removes the row so a tile created
    in between cannot be left pointing at a deleted KPI.
    """
    result = store.delete_if_unreferenced(kpi_id)
    if result.referenced_by:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{kpi_id} is still referenced by dashboard(s): "
                f"{', '.join(result.referenced_by)}."
            ),
        )
    if not result.removed:
        raise HTTPException(status_code=404, detail=f"No KPI {kpi_id!r}.")


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
    feasibility_map, snapshot_version = _feasibility_map(settings, store)
    return _feasibility_out(feasibility_map[definition.identifier], snapshot_version)
