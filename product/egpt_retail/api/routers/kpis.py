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

``GET /kpis/suggestions`` (#234/F1's on-demand half) maps straight onto
``suggestions.run.run_suggestions(..., dry_run=True)`` -- nothing is filed, the K12 agent's
drafts come back stamped with the same join-aware feasibility this router already uses.  Two
scope decisions, both flagged rather than silently narrowed:

- ``run_suggestions`` always runs *both* the KPI and use-case agents in one pass (the module's
  own docstring: "the model is consulted exactly twice"); there is no lower-level entry point
  that runs only K12. This route pays for a use-case suggestion pass on every call and throws
  it away. Splitting ``run_suggestions`` to make the KPI half callable alone is a real
  optimization if this endpoint gets hit often, but it is a change to shared, tested code
  outside this router's scope -- not made here.
- Domain prose (``knowledge``/``ontology``) and warehouse example values (``samples``) are both
  omitted here (``suggest.py``'s CLI entrypoint gathers both). Skipping them measurably lowers
  suggestion quality (this module's own K12 baseline notes a 1.2x-9x run-to-run spread without
  examples) but keeps this route to the two connections every other one here already opens
  (control plane, semantic graph) instead of adding a warehouse connection and a knowledge-store
  dependency to a read endpoint. Worth a follow-up once this ships, not blocking it.

Verified locally: an unconfigured or unreachable model provider surfaces as a raw, traceback-
carrying ``500`` (``build_suggestion_agents``'s routed agent returns a plain refusal string,
which ``run_suggestions``' ``_drafts_of`` turns into a ``ValueError`` that nothing here catches)
-- the same class of gap #233 already flags for this router's own Neo4j/warehouse dependencies.
Not mapped to a friendlier status here, for the same reason: a real fix belongs where the other
two instances of it would also be fixed, not as a one-off in this route.

**#254: the run is no longer inside the request.** Those two model calls take minutes, not
"several seconds", and nothing below this router bounds them; the api also serves from more
than one instance. So ``POST /kpis/suggestions`` records a row (``suggestions/runs.py``),
returns ``202`` with it, and executes the run as a task in this process, bounded by
``retail_suggestion_timeout_seconds``; ``GET /kpis/suggestions/{run_id}`` is polled until the
row is ``succeeded``/``failed``. The row is in Postgres so the poll may land on any instance,
and a row whose executor vanished (instance recycled mid-run) is closed as ``failed`` by the
read side after the timeout plus a grace period rather than spinning forever. The synchronous
``GET /kpis/suggestions`` stays, deprecated (an additive contract change), and is held to the
same bound -- ``504`` past it instead of an open-ended hang. Only the run path turns the
provider failure above into something readable: a ``failed`` row whose ``error`` a screen can
show, which is what the request path could not do without a fix that belongs elsewhere.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
import traceback
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    Iterator,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    TypeVar,
)

from egpt.agent import Agent
from egpt.os.scopes import has_required_scopes
from egpt.utils.log import log_error, log_warning
from egpt.workflow.types import StepInput
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.engine import Engine

from egpt_retail.api import models as wire
from egpt_retail.app.db import build_warehouse_engine, pooled_control_plane_connection
from egpt_retail.app.scopes import caller_scopes, require_scopes
from egpt_retail.app.semantic import open_semantic_reader
from egpt_retail.app.settings import RetailSettings
from egpt_retail.contracts.kpi import (
    Dialect,
    Feasibility,
    FeasibilityVerdict,
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
from egpt_retail.contracts.vocabulary import VocabularySet
from egpt_retail.executors.gold import materialise_gold
from egpt_retail.kpi.series import DEFAULT_SERIES_DAYS, read_kpi_series
from egpt_retail.kpi.store import KpiConcurrentUpdateError, KpiRegistryStore
from egpt_retail.suggestions.agents import build_suggestion_agents, load_catalogue
from egpt_retail.suggestions.run import PhaseCallback, SuggestionRun, run_suggestions
from egpt_retail.suggestions.runs import (
    SuggestionRunPhase,
    SuggestionRunRecord,
    SuggestionRunStore,
)
from egpt_retail.warehouse.health import akpi_health

__all__ = ["router"]

router = APIRouter()

_SLUG_RE = re.compile(r"[^a-z0-9]+")

_T = TypeVar("_T")


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
    of merely guarding the lookup. The result is still looked up with ``.get()``, not
    ``[...]``, everywhere it is read below: a concurrent delete landing between the caller's
    own read and this call must 404, not raise an unguarded ``KeyError`` FastAPI turns into a
    raw 500 (#240 review, round 2).
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


def _caller_id(request: Request) -> str:
    """The caller's identity -- used only to decide which dashboard *names* ``delete_kpi``'s
    409 is allowed to reveal (#240 review: naming a dashboard the caller cannot otherwise see
    is a minor information leak), never for the reference check itself. ``KpiRegistryStore.
    delete_if_unreferenced`` deliberately blocks on every dashboard regardless of owner or
    visibility -- a private dashboard belonging to someone else must still refuse the delete;
    this identity only narrows what the error message is allowed to say about it. Mirrors
    ``api/routers/dashboards.py``'s ``_owner`` exactly -- duplicated, not imported, matching
    this codebase's existing convention of each router owning its own copy rather than sharing
    a private helper across modules.
    """
    if not getattr(request.state, "authorization_enabled", False):
        return "local"
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(
            status_code=401,
            detail="This token carries no subject claim.",
        )
    return user_id


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
) -> wire.Kpi:
    """Feasibility lookup + wire assembly, shared by ``get_kpi``, ``create_kpi`` and
    ``update_kpi`` -- factored out because the block was duplicated near-verbatim across all
    three (#240 review, author's own finding).

    Passes ``kpis=[definition]`` through to :func:`_feasibility_map` -- the exact row already
    read (get) or just written (create/update) -- rather than a second, independent read of
    the whole registry: that closes the TOCTOU window a concurrent ``DELETE`` could otherwise
    open between this route's own read/write and the feasibility lookup, and avoids the
    whole-registry computation none of these single-KPI routes need. The lookup uses ``.get()``
    rather than ``[...]``: a concurrent delete landing in that window would otherwise raise an
    unguarded ``KeyError`` FastAPI turns into a raw 500, rather than the 404 a vanished KPI
    should produce.
    """
    settings = RetailSettings()
    feasibility_map, snapshot_version = _feasibility_map(settings, store, [definition])
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
    feasibility_map, snapshot_version = _feasibility_map(settings, store, definitions)
    include_sql = _may_read_sql(request)
    kpis = []
    for definition in definitions:
        # `.get()`, not `[...]`: `definitions` and `feasibility_map` come from two
        # independent reads (this route's own `store.list()` above and `_feasibility_map`'s
        # internal one), so a KPI deleted in the gap between them is in the first and missing
        # from the second. Drop it from the listing rather than a raw 500 (#240 review, round
        # 2 -- the same unguarded-lookup regression `_respond_with_kpi` already guards
        # against for create/update/get, now closed here too).
        verdict = feasibility_map.get(definition.identifier)
        if verdict is None:
            continue
        kpis.append(
            _kpi_out(
                definition,
                _feasibility_out(verdict, snapshot_version),
                include_sql=include_sql,
            )
        )
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
    return _respond_with_kpi(saved, request, store)


def _validated_business_area(
    business_area: Optional[str], vocabularies: VocabularySet
) -> Optional[str]:
    """422, not a raw ``ValueError``, for a business area outside this deployment's vocabulary
    -- the same regression ``list_kpis`` fixed for #229 (an unhandled ``ValueError`` from
    ``VocabularySet.validate`` was leaking as a 500).

    ``vocabularies`` is threaded in by the caller rather than loaded here: every route that
    reaches this already has a ``KpiRegistryStore`` (or, for the suggestions route, a
    ``_SuggestionRegistrySnapshot``) that loaded and cached its own ``VocabularySet`` once at
    construction (``kpi/store.py``), so a fresh ``load_vocabulary_set()`` on every call would
    just re-read the same shipped file this store already has in hand.
    """
    if business_area is not None:
        try:
            vocabularies.validate("business_area", business_area)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return business_area


def _validated_business_areas(
    business_areas: Optional[Sequence[str]], vocabularies: VocabularySet
) -> Tuple[str, ...]:
    """The focus a run is started with: every id checked against the vocabulary (422 on
    the first that is not), de-duplicated, order kept. Empty means no focus."""
    seen: list[str] = []
    for area in business_areas or ():
        _validated_business_area(area, vocabularies)
        if area not in seen:
            seen.append(area)
    return tuple(seen)


def _filter_suggestions(
    suggestions: Sequence[wire.KpiSuggestion], business_area: Optional[str]
) -> list[wire.KpiSuggestion]:
    """Pure, so the business-area narrowing is unit-testable without a real semantic reader --
    ``run_suggestions`` has no ``business_area`` parameter of its own, so this narrows its
    output after the fact, the same after-the-fact-narrowing shape ``list_kpis`` gets from
    ``store.list(business_area=...)``, just done here in Python since ``run_suggestions`` has
    no such parameter.
    """
    if business_area is None:
        return list(suggestions)
    return [s for s in suggestions if s.business_area == business_area]


def _suggestion_notes(run: SuggestionRun) -> str:
    """``SuggestionRun.notes`` (e.g. "no domain prose reached the agents: ...", which
    ``run_suggestions`` always appends here since this route never passes ``knowledge=``/
    ``ontology=``) joined with ``AssemblyReport.notes`` (K12's own free-text field, previously
    the only one of the two that reached the wire) -- both surfaced, since the wire model's
    ``notes`` is a single string with room for both.
    """
    parts = [note for note in (*run.notes, run.kpis.notes) if note]
    return "\n".join(parts)


@dataclass(frozen=True)
class _SuggestionRegistrySnapshot:
    """What ``GET /kpis/suggestions`` needs from the registry, read once up front.

    Fetched by :func:`_suggestion_registry_snapshot` -- a plain (non-generator) dependency
    that opens the pooled control-plane connection, reads it, and closes it before returning,
    rather than a request-scoped ``Depends(_kpi_store)`` that would otherwise stay open for
    the entire span of the two live model calls ``run_suggestions`` makes below it.
    """

    definitions: Tuple[KpiDefinition, ...]
    vocabularies: VocabularySet


def _suggestion_registry_snapshot() -> _SuggestionRegistrySnapshot:
    """The registry snapshot ``GET /kpis/suggestions`` needs, read and released up front.

    Opens its own pooled connection (mirrors ``_kpi_store``) rather than depending on it,
    because ``_kpi_store``'s ``yield`` keeps the connection checked out for the whole request
    -- exactly the resource-holding pattern this route must avoid (Finding 5, PR #242 review):
    ``registry``/``store`` is only ever used for one ``list()`` call, so there is no reason to
    hold a scarce, pooled Postgres connection open across the two live model calls that follow.
    """
    settings = RetailSettings()
    connection = pooled_control_plane_connection(settings)
    try:
        store = KpiRegistryStore(connection)
        return _SuggestionRegistrySnapshot(
            definitions=tuple(store.list()), vocabularies=store.vocabularies
        )
    finally:
        connection.close()


#: The two K12/K13 agents built by ``build_suggestion_agents``, reused across requests until
#: the registry's identifier set changes. CLAUDE.md: "Never create agents in loops -- reuse
#: them for performance" -- without this, every ``GET /kpis/suggestions`` request built two
#: fresh ``Agent`` instances, re-rendered the K12 prompt against the registry and re-read
#: ``load_catalogue()`` from disk, even though nothing had changed since the last request.
#: A single-slot cache (not an unbounded dict keyed by every snapshot ever seen) since only
#: the current registry state is ever worth serving stale agents for.
_agent_cache_key: Optional[Tuple[str, ...]] = None
_agent_cache_agents: Optional[Tuple[Agent, Agent]] = None
_agent_cache_lock = threading.Lock()


def _cached_suggestion_agents(
    settings: RetailSettings, definitions: Sequence[KpiDefinition]
) -> Tuple[Agent, Agent]:
    """The two suggestion agents for this registry snapshot, built once and reused.

    Keyed by the sorted set of registry identifiers -- the same set the K12 prompt embeds as
    "existing" -- so a KPI created or deleted between requests invalidates the cache the next
    time this is called, rather than serving agents whose prompt still lists a KPI that is now
    gone (or omits one that now exists). This tracks only identifier churn, not content edits:
    a KPI whose description/business_area changes via ``PATCH /kpis/{id}`` without its
    identifier set changing keeps serving K13 its old ``KpiDefinition`` content until some
    other KPI is added or removed. Left as-is (PR #242 review) -- this route discards
    ``run.use_cases`` entirely today, so K13's prompt content is unobserved; worth a content
    fingerprint in the cache key if a caller ever starts reading that output.

    The two returned ``Agent`` objects are shared across concurrent requests -- callers must
    ``deep_copy()`` before calling ``.run()`` on them (see :func:`_list_kpi_suggestions`);
    ``.run()`` mutates unsynchronized per-agent state (``session_id``, hook normalization,
    connectable-tool init lists) that a lock held only around this function's cache population
    cannot protect.
    """
    global _agent_cache_key, _agent_cache_agents
    key = tuple(sorted(definition.identifier for definition in definitions))
    with _agent_cache_lock:
        if _agent_cache_key == key and _agent_cache_agents is not None:
            return _agent_cache_agents
        agents = build_suggestion_agents(
            settings,
            existing_kpis=key,
            registry_kpis=definitions,
            catalogue=load_catalogue(),
        )
        _agent_cache_key = key
        _agent_cache_agents = agents
        return agents


def _list_kpi_suggestions(
    settings: RetailSettings,
    *,
    definitions: Sequence[KpiDefinition],
    vocabularies: VocabularySet,
    business_area: Optional[str],
    include_sql: bool,
    agents: Optional[Tuple[Any, Any]] = None,
    focus_areas: Sequence[str] = (),
    on_phase: Optional[PhaseCallback] = None,
) -> wire.KpiSuggestionList:
    """#234/F1: on-demand KPI suggestions, read straight through to K12 -- see the module
    docstring for what this deliberately omits (prose, warehouse samples, and the wasted
    use-case agent call ``run_suggestions`` always makes alongside it).

    Two different things can name areas here. ``business_area`` (the deprecated single id)
    narrows what comes *back*, after the fact. ``focus_areas`` (the run request's
    ``business_areas``) go *into* the message as a priority -- the agent proposes for them
    first -- and nothing is dropped for being outside them.

    ``on_phase`` is forwarded to ``run_suggestions`` so a run row can record which stage
    the pass is in (``_execute_suggestion_run``); the synchronous route passes nothing.

    ``definitions``/``vocabularies`` come from :func:`_suggestion_registry_snapshot`, read
    before the store's pooled connection was released, rather than a live ``KpiRegistryStore``
    held open for this call's two live model calls.

    ``include_sql`` gates ``KpiSuggestion.proposed_sql`` the same way ``_kpi_out`` gates
    ``Kpi.sql``: a caller holding only ``kpis:read`` never sees candidate SQL, only a caller
    with ``kpis:write`` (``_may_read_sql``) does.

    ``agents`` is a test-only seam (mirrors ``run_suggestions``' own): a real call always
    resolves it from :func:`_cached_suggestion_agents` instead of leaving it ``None`` and
    paying for two fresh ``Agent`` builds on every request; a test injects stand-ins that
    return canned drafts, exactly as ``test_k12_k13_suggestion_pass.py`` already does for
    K12/K13 directly.

    A real call's two agents are ``deep_copy()``'d before ``.run()`` (PR #242 review): the
    cache shares one ``Agent`` pair across every concurrent request, and ``.run()`` mutates
    unsynchronized per-agent state (``session_id``, hook normalization, connectable-tool init
    lists) that isn't safe to race two requests over. ``deep_copy()`` -- the same
    isolate-per-run-state/share-heavy-resources split ``os/utils.py`` already uses for
    concurrently-dispatched registered agents -- keeps the cache's actual savings (prompt
    render, catalogue read) while giving each request its own per-run state, so this doesn't
    need to widen the cache lock around two live model calls.
    """
    business_area = _validated_business_area(business_area, vocabularies)
    if settings.retail_suggestion_replay:
        # DEMO/TESTING ONLY: replay a saved result instead of calling the model. The phases
        # are still reported, a few seconds apart, so the page's run panel behaves as it
        # would on a real run. `retail_suggestion_replay` is unset in every real deployment
        # (its own description says so); this branch is inert unless someone sets it.
        replayed = wire.KpiSuggestionList.model_validate(
            json.loads(Path(settings.retail_suggestion_replay).read_text())
        )
        for phase, pause in (
            (SuggestionRunPhase.READING, 2.0),
            (SuggestionRunPhase.PROPOSING, 5.0),
            (SuggestionRunPhase.CHECKING, 1.5),
        ):
            if on_phase is not None:
                on_phase(phase)
            time.sleep(pause)
        suggestions = _filter_suggestions(replayed.suggestions, business_area)
        if not include_sql:
            suggestions = [
                s.model_copy(update={"proposed_sql": None}) for s in suggestions
            ]
        return replayed.model_copy(update={"suggestions": suggestions})
    if agents is not None:
        resolved_agents = agents
    else:
        cached_kpi_agent, cached_use_case_agent = _cached_suggestion_agents(
            settings, definitions
        )
        resolved_agents = (
            cached_kpi_agent.deep_copy(),
            cached_use_case_agent.deep_copy(),
        )
    # Neo4j reader/driver held open across both live model calls below (PR #242 review,
    # rounds 2-3, not fixed): `run_suggestions` calls `reader.unjoinable_oracle()` between
    # `kpi_agent.run()` and `use_case_agent.run()`, so this can't be narrowed to wrap only
    # context-gathering without splitting `run_suggestions` itself -- out of scope per this
    # module's own docstring (decision 1). Tracked as a follow-up alongside that split.
    with open_semantic_reader(settings) as reader:
        run: SuggestionRun = run_suggestions(
            settings,
            reader=reader,
            existing_definitions=definitions,
            proposals=None,
            dry_run=True,
            agents=resolved_agents,
            focus_areas=focus_areas,
            on_phase=on_phase,
        )
    suggestions = _filter_suggestions(run.kpis.suggestions, business_area)
    if not include_sql:
        suggestions = [s.model_copy(update={"proposed_sql": None}) for s in suggestions]
    return wire.KpiSuggestionList(
        suggestions=suggestions,
        notes=_suggestion_notes(run),
        semantic_snapshot=run.snapshot,
    )


class SuggestionTimeout(Exception):
    """A suggestion run exceeded ``retail_suggestion_timeout_seconds``."""

    def __init__(self, seconds: float) -> None:
        super().__init__(f"The suggestion run did not finish within {seconds:g}s.")
        self.seconds = seconds


async def _run_bounded(fn: Callable[[], _T], timeout: float) -> _T:
    """``fn`` on a worker thread, waited on for at most ``timeout`` seconds (#254).

    ``run_suggestions`` is synchronous (two blocking ``agent.run()`` calls), so it goes to a
    thread; the wait, not the work, is what the bound cuts. A model call cannot be
    interrupted from outside: on timeout the thread keeps running to its own end and its
    result is discarded, so this bounds what a caller waits for -- the request, or the run
    row -- not what the deployment spends on a call already in flight.
    """
    try:
        return await asyncio.wait_for(asyncio.to_thread(fn), timeout)
    except asyncio.TimeoutError as exc:
        raise SuggestionTimeout(timeout) from exc


#: How long past the timeout a ``queued``/``running`` row may sit before the read side
#: concludes its executor is gone. Generous, so a run still inside its own bound is never
#: closed by a poll; only one that nothing in any process is still waiting on.
_LOST_RUN_GRACE_SECONDS = 60.0


def _stale_after(settings: RetailSettings) -> timedelta:
    return timedelta(
        seconds=settings.retail_suggestion_timeout_seconds + _LOST_RUN_GRACE_SECONDS
    )


@contextmanager
def _open_run_store(settings: RetailSettings) -> Iterator[SuggestionRunStore]:
    """A ``SuggestionRunStore`` on its own pooled connection, released on exit. Not a
    ``Depends``: the executor task needs one outside any request, and every use here is
    one short call -- open, do it, release -- never held across the model calls."""
    connection = pooled_control_plane_connection(settings)
    try:
        yield SuggestionRunStore(connection)
    finally:
        connection.close()


def _with_run_store(
    settings: RetailSettings, action: Callable[[SuggestionRunStore], _T]
) -> _T:
    with _open_run_store(settings) as store:
        return action(store)


def _run_out(
    record: SuggestionRunRecord,
    *,
    include_sql: bool,
    typical_duration_seconds: Optional[float] = None,
) -> wire.KpiSuggestionRun:
    """The wire shape of a run row. The stored ``result`` always carries ``proposed_sql``;
    the ``kpis:write`` gate (``_may_read_sql``) is applied here, per reader, exactly as the
    synchronous route applies it -- one run, read differently by a viewer and an editor.

    ``typical_duration_seconds`` is the deployment's own recent history
    (``SuggestionRunStore.typical_duration``), read alongside the row by the caller so the
    screen can set an expectation before the run has done anything."""
    result: Optional[wire.KpiSuggestionList] = None
    if record.result is not None:
        result = wire.KpiSuggestionList.model_validate(record.result)
        if not include_sql:
            result = result.model_copy(
                update={
                    "suggestions": [
                        s.model_copy(update={"proposed_sql": None})
                        for s in result.suggestions
                    ]
                }
            )
    return wire.KpiSuggestionRun(
        id=record.identifier,
        status=wire.KpiSuggestionRunStatus(record.status.value),
        business_area=record.business_area,
        business_areas=list(record.business_areas),
        created_at=record.created_at,
        started_at=record.started_at,
        finished_at=record.finished_at,
        result=result,
        error=record.error,
        phase=(
            wire.KpiSuggestionRunPhase(record.phase.value)
            if record.phase is not None
            else None
        ),
        typical_duration_seconds=typical_duration_seconds,
    )


def _read_run_with_history(
    store: SuggestionRunStore, run_id: str, stale_after: timedelta
) -> Tuple[Optional[SuggestionRunRecord], Optional[float]]:
    """The row and the typical duration, in one visit to the store."""
    return store.get(run_id, stale_after=stale_after), store.typical_duration()


def _phase_reporter(run_id: str, settings: RetailSettings) -> PhaseCallback:
    """The ``on_phase`` listener for one run: each stage the pipeline reports is written to
    the row, on its own short-lived connection, from the worker thread the pass runs on.
    Never raises -- the row's phase is a courtesy to the poller, and a failure to write it
    must not fail the run it describes."""

    def _report(phase: SuggestionRunPhase) -> None:
        try:
            _with_run_store(settings, lambda store: store.mark_phase(run_id, phase))
        except Exception:
            log_warning(
                f"suggestion run {run_id}: phase {phase.value!r} not recorded: "
                f"{traceback.format_exc(limit=3)}"
            )

    return _report


#: Strong references to in-flight executor tasks, so the event loop cannot collect one
#: mid-run -- the same reason ``egpt/agent/_run.py`` keeps ``_background_tasks``.
_suggestion_run_tasks: set[asyncio.Task[None]] = set()


async def _await_suggestion_runs() -> None:
    """Wait for every run this process has in flight. A seam for tests (the ``TestClient``
    portal awaits it after a ``POST`` so the poll that follows sees a terminal row) -- a
    real caller never waits here; it polls."""
    if _suggestion_run_tasks:
        await asyncio.gather(*list(_suggestion_run_tasks), return_exceptions=True)


def _describe_failure(exc: BaseException) -> str:
    """What a ``failed`` row says. The pipeline's own refusals (``_drafts_of``'s
    ``ValueError``: "KpiSuggestionDraftSet expected from the agent; got str: ...") are
    written for a reader and name the actual reason, so they pass through. Anything else
    (a driver error, a connection failure) is summarised by type only: exception text from
    those can carry connection strings, and the traceback is already in the api log."""
    if isinstance(exc, ValueError):
        return str(exc)
    return (
        f"The suggestion run failed ({type(exc).__name__}); "
        "the api log has the traceback."
    )


async def _execute_suggestion_run(
    run_id: str,
    settings: RetailSettings,
    snapshot: _SuggestionRegistrySnapshot,
    business_area: Optional[str],
    focus_areas: Sequence[str] = (),
) -> None:
    """The executor behind ``POST /kpis/suggestions``: ``queued`` -> ``running`` -> one
    bounded ``_list_kpi_suggestions`` -> ``succeeded``/``failed``. Each row write opens and
    releases its own connection; the model calls in between hold none.

    Runs with ``include_sql=True`` unconditionally: the row is read by many callers, and the
    SQL gate is theirs (``_run_out``), not the run's. Nothing here raises -- a failure to
    record an outcome is logged, since the task has no caller to raise to.
    """
    try:
        await asyncio.to_thread(
            _with_run_store, settings, lambda store: store.mark_running(run_id)
        )
        try:
            result = await _run_bounded(
                lambda: _list_kpi_suggestions(
                    settings,
                    definitions=snapshot.definitions,
                    vocabularies=snapshot.vocabularies,
                    business_area=business_area,
                    include_sql=True,
                    focus_areas=focus_areas,
                    on_phase=_phase_reporter(run_id, settings),
                ),
                settings.retail_suggestion_timeout_seconds,
            )
        except SuggestionTimeout as exc:
            timed_out = str(exc)
            await asyncio.to_thread(
                _with_run_store,
                settings,
                lambda store: store.mark_failed(run_id, timed_out),
            )
            return
        except Exception as exc:
            log_error(
                f"suggestion run {run_id} failed: {traceback.format_exc(limit=5)}"
            )
            failure = _describe_failure(exc)
            await asyncio.to_thread(
                _with_run_store,
                settings,
                lambda store: store.mark_failed(run_id, failure),
            )
            return
        payload = result.model_dump(mode="json")
        await asyncio.to_thread(
            _with_run_store,
            settings,
            lambda store: store.mark_succeeded(run_id, payload),
        )
    except Exception:
        log_error(
            f"suggestion run {run_id}: its outcome could not be recorded: "
            f"{traceback.format_exc(limit=5)}"
        )


@router.post("/kpis/suggestions", response_model=wire.KpiSuggestionRun, status_code=202)
async def start_kpi_suggestion_run(
    request: Request,
    body: Optional[wire.KpiSuggestionRunRequest] = None,
    snapshot: _SuggestionRegistrySnapshot = Depends(_suggestion_registry_snapshot),
    _: None = Depends(require_scopes("kpis:read")),
):
    """Start a run and return it ``queued`` (#254). ``kpis:read``, like the synchronous
    route: what is created is a run row, never a KPI -- adoption is still ``POST /kpis``.
    Every area named is validated before the row exists, so a 422 leaves nothing behind.

    ``business_areas`` is the focus (contract 1.1.8): the areas the customer cares about,
    handed to the agents as a priority. ``business_area`` is the older single narrowing
    filter, still honoured. The frontend sends the first; the second stays for callers
    written against 1.1.5.
    """
    settings = RetailSettings()
    business_area = _validated_business_area(
        body.business_area if body is not None else None, snapshot.vocabularies
    )
    focus_areas = _validated_business_areas(
        body.business_areas if body is not None else None, snapshot.vocabularies
    )
    record, typical = await asyncio.to_thread(
        _with_run_store,
        settings,
        lambda store: (
            store.create(business_area, focus_areas),
            store.typical_duration(),
        ),
    )
    task = asyncio.create_task(
        _execute_suggestion_run(
            record.identifier, settings, snapshot, business_area, focus_areas
        )
    )
    _suggestion_run_tasks.add(task)
    task.add_done_callback(_suggestion_run_tasks.discard)
    return _run_out(
        record, include_sql=_may_read_sql(request), typical_duration_seconds=typical
    )


@router.get("/kpis/suggestions/{run_id}", response_model=wire.KpiSuggestionRun)
async def get_kpi_suggestion_run(
    run_id: str,
    request: Request,
    _: None = Depends(require_scopes("kpis:read")),
):
    """The run, for polling. Reading applies the lost-run guard (``SuggestionRunStore.get``'s
    ``stale_after``), so a row whose executor is gone comes back ``failed`` here rather than
    ``running`` forever."""
    settings = RetailSettings()
    record, typical = await asyncio.to_thread(
        _with_run_store,
        settings,
        lambda store: _read_run_with_history(store, run_id, _stale_after(settings)),
    )
    if record is None:
        raise HTTPException(status_code=404, detail=f"No suggestion run {run_id!r}.")
    return _run_out(
        record, include_sql=_may_read_sql(request), typical_duration_seconds=typical
    )


@router.get("/kpis/suggestions", response_model=wire.KpiSuggestionList, deprecated=True)
async def list_kpi_suggestions(
    request: Request,
    business_area: Optional[str] = None,
    snapshot: _SuggestionRegistrySnapshot = Depends(_suggestion_registry_snapshot),
    _: None = Depends(require_scopes("kpis:read")),
):
    """Deprecated by ``POST /kpis/suggestions`` (#254); kept because removing a contract
    path is a breaking change. Now bounded: ``504`` past ``retail_suggestion_timeout_seconds``
    instead of a request that hangs until something outside this app cuts it."""
    settings = RetailSettings()
    include_sql = _may_read_sql(request)
    try:
        return await _run_bounded(
            lambda: _list_kpi_suggestions(
                settings,
                definitions=snapshot.definitions,
                vocabularies=snapshot.vocabularies,
                business_area=business_area,
                include_sql=include_sql,
            ),
            settings.retail_suggestion_timeout_seconds,
        )
    except SuggestionTimeout as exc:
        raise HTTPException(
            status_code=504,
            detail=f"{exc} Use POST /kpis/suggestions and poll the run instead.",
        ) from exc


# --- health (R5's gold freshness, joined against the registry) ----------------------------
#
# Declared before `/kpis/{kpi_id}` (same reason `/kpis/suggestions` is, above): a path
# parameter route registered first would otherwise swallow `/kpis/health` as `kpi_id="health"`.


def _warehouse_engine() -> Iterator[Engine]:
    """A warehouse ``Engine`` for the span of one request, disposed on exit.

    ``kpi_health`` reads two gold tables directly over SQLAlchemy (``warehouse/health.py``)
    rather than through the ``read_gold`` executor ``get_kpi_series`` uses below, so this opens
    its own engine the same way ``executors/preflight.py``'s ``_read_gold_health`` does --
    ``build_warehouse_engine`` builds a fresh engine (``create_engine``'s default ``QueuePool``,
    not a single unpooled connection) per call, unlike ``_kpi_store``'s cached, pooled
    control-plane connection, so disposal here tears the whole per-call pool down rather than
    returning a connection to a shared one. ``preflight.py``'s call site pays this roughly once
    per workflow run; this route pays it once per dashboard mount, a much higher frequency --
    worth revisiting with a small cached engine if that turns out to matter in practice.

    ``build_warehouse_engine`` raises ``RuntimeError`` when ``EGPT_RETAIL_WAREHOUSE_DB_URL``
    is unset; turned into a ``503`` here, the same posture ``compute_kpi`` already takes for
    "gold refused to run" -- freshness has nothing to report from a warehouse that was never
    configured, not a 500.
    """
    settings = RetailSettings()
    try:
        engine = build_warehouse_engine(settings)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    try:
        yield engine
    finally:
        engine.dispose()


@router.get("/kpis/health", response_model=list[wire.KpiHealth])
async def list_kpi_health(
    request: Request,
    store: KpiRegistryStore = Depends(_kpi_store),
    _: None = Depends(require_scopes("kpis:read")),
    engine: Engine = Depends(_warehouse_engine),
):
    """Freshness of every registered KPI, from gold. One row per KPI in the registry --
    including ``never_computed``/``not_scheduled``, the two statuses only the registry half
    of ``classify_health`` can produce, since gold has no run at all to read for those.

    ``store.list()`` reads every KPI (no filter, mirroring ``_suggestion_registry_snapshot``'s
    own unfiltered read) rather than only ``active`` ones: ``active`` is still the #231
    placeholder every KPI reports ``true`` for (see ``GET /kpis``'s own docstring), so filtering
    on it here would not narrow anything real yet.

    ``engine`` is declared *after* the scope check (unlike every other route's dependency
    order in this file, which is store-then-scope): ``_warehouse_engine`` raises a 503 naming
    the unset config variable when the warehouse isn't configured, and FastAPI resolves
    dependencies in declared order, so putting it before ``require_scopes`` would let an
    unauthorized caller learn that detail before ever being told they lack the scope.

    ``last_error`` is the database's own refusal text verbatim (``warehouse/health.py``) --
    comparably sensitive to ``Kpi.sql``/``proposed_sql``, which this router already gates
    behind ``kpis:write`` via ``_may_read_sql``. Gated here the same way, so a ``kpis:read``-only
    caller sees every other field but not the raw error string.
    """
    definitions = await store.alist()
    rows = await akpi_health(definitions, engine)
    include_sql = _may_read_sql(request)
    return [
        wire.KpiHealth.model_validate(
            row if include_sql else {**row, "last_error": None}
        )
        for row in rows
    ]


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

    return _respond_with_kpi(saved, request, store)


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
    in between cannot be left pointing at a deleted KPI. The 409 *message*, unlike the check
    itself, only names dashboards the caller can actually see -- others referencing it are
    reported only as a count, not by name (#240 review: naming them would leak into the error
    the existence of a dashboard the caller has no visibility into).
    """
    result = store.delete_if_unreferenced(kpi_id, caller=_caller_id(request))
    if result.referenced_by or result.hidden_reference_count:
        parts = list(result.referenced_by)
        if result.hidden_reference_count:
            plural = "s" if result.hidden_reference_count != 1 else ""
            parts.append(
                f"{result.hidden_reference_count} dashboard{plural} you do not have "
                "visibility into"
            )
        raise HTTPException(
            status_code=409,
            detail=f"{kpi_id} is still referenced by dashboard(s): {', '.join(parts)}.",
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
    # `kpis=[definition]`, not a whole-registry read: feasibility comes from the row just
    # read rather than a second, independent registry read a concurrent delete could race
    # (same fix as `_respond_with_kpi`; #240 review, round 2).
    feasibility_map, snapshot_version = _feasibility_map(settings, store, [definition])
    verdict = feasibility_map.get(definition.identifier)
    if verdict is None:
        raise HTTPException(status_code=404, detail=f"No KPI {kpi_id!r}.")
    return _feasibility_out(verdict, snapshot_version)


# --- one KPI's value over time, and computing it now --------------------------------------


def _parsed_dimension(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    """``?dimension=`` as a JSON object of grain-key values, or a 422 saying what it must be.
    Empty and absent are the same: follow the first dimension the latest night carried."""
    if raw is None or raw.strip() == "":
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        value = None
    if not isinstance(value, dict):
        raise HTTPException(
            status_code=422,
            detail=(
                "dimension must be a JSON object of grain-key values, "
                'e.g. {"brand": "Acme"}.'
            ),
        )
    return value


@router.get("/kpis/{kpi_id}/series", response_model=wire.KpiSeries)
async def get_kpi_series(
    kpi_id: str,
    days: int = Query(DEFAULT_SERIES_DAYS, ge=1, le=365),
    dimension: Optional[str] = Query(None),
    store: KpiRegistryStore = Depends(_kpi_store),
    _: None = Depends(require_scopes("kpis:read")),
):
    """This KPI's points from gold over the last ``days`` business dates -- a read of what
    the nightly run (or ``compute_kpi``) wrote, never a recomputation. A KPI with no gold
    rows is a ``200`` with empty ``points`` and ``status: never_computed``: the KPI exists;
    its value does not yet. ``async`` because ``read_gold`` is."""
    definition = store.get(kpi_id)
    if definition is None:
        raise HTTPException(status_code=404, detail=f"No KPI {kpi_id!r}.")
    return await read_kpi_series(
        definition, days=days, dimension=_parsed_dimension(dimension)
    )


@router.post("/kpis/{kpi_id}/compute", response_model=wire.KpiComputeResult)
async def compute_kpi(
    kpi_id: str,
    store: KpiRegistryStore = Depends(_kpi_store),
    _: None = Depends(require_scopes("kpis:write")),
):
    """The nightly ``materialise_gold`` pass, narrowed to this KPI and today's business date:
    bound, guarded, executed and stamped exactly as the scheduled run would. What "Add to my
    metrics" calls so a newly adopted KPI has a value before its first night.

    ``409`` when the KPI is not ``ready`` -- gold skips a blocked KPI, so running the pass
    would only report ``skipped``; saying so up front, with the feasibility rationale, is the
    more useful answer. ``503`` when the pass itself refuses (the silver gates, the load lock:
    ``StepOutput.stop``), the same two refusals the nightly run has. A formula that fails is
    a ``200`` with ``status: failed`` and the error: the request did what it said.
    """
    definition = store.get(kpi_id)
    if definition is None:
        raise HTTPException(status_code=404, detail=f"No KPI {kpi_id!r}.")
    settings = RetailSettings()
    feasibility_map, _snapshot = _feasibility_map(settings, store, [definition])
    verdict = feasibility_map.get(definition.identifier)
    if verdict is None:
        raise HTTPException(status_code=404, detail=f"No KPI {kpi_id!r}.")
    if verdict.verdict is not FeasibilityVerdict.READY:
        raise HTTPException(
            status_code=409,
            detail=f"{kpi_id} is {verdict.verdict.value}: {verdict.rationale}",
        )
    run_id = f"api_{uuid.uuid4().hex[:12]}"
    output = await materialise_gold(
        StepInput(additional_data={"kpi_identifiers": [kpi_id], "run_id": run_id})
    )
    content: Mapping[str, Any] = (
        output.content if isinstance(output.content, Mapping) else {}
    )
    if output.stop or not output.success:
        raise HTTPException(
            status_code=503,
            detail=str(
                content.get("refused") or output.error or "gold refused to run."
            ),
        )
    for entry in content.get("results", ()):
        if entry.get("kpi_identifier") != kpi_id:
            continue
        return wire.KpiComputeResult(
            kpi_identifier=kpi_id,
            as_of=date.fromisoformat(str(entry["as_of"])),
            status=wire.KpiComputeStatus(str(entry["status"])),
            rows_written=int(entry.get("rows_written") or 0),
            run_id=str(entry.get("run_id") or run_id),
            error=entry.get("error"),
            reason=entry.get("reason"),
        )
    # The pass re-reads the registry itself; the row vanished between this route's own read
    # and that one (same race `get_kpi_feasibility` guards against).
    raise HTTPException(status_code=404, detail=f"No KPI {kpi_id!r}.")
