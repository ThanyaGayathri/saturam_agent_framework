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
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Any, Iterator, Mapping, Optional, Sequence, Tuple

from egpt.os.scopes import has_required_scopes
from fastapi import APIRouter, Depends, HTTPException, Request

from egpt_retail.api import models as wire
from egpt_retail.app.db import pooled_control_plane_connection
from egpt_retail.app.scopes import caller_scopes, require_scopes
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
from egpt_retail.contracts.vocabulary import VocabularySet
from egpt_retail.dashboards.store import DashboardStore
from egpt_retail.kpi.store import KpiConcurrentUpdateError, KpiRegistryStore
from egpt_retail.suggestions.agents import build_suggestion_agents, load_catalogue
from egpt_retail.suggestions.run import SuggestionRun, run_suggestions

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
_agent_cache_agents: Optional[Tuple[Any, Any]] = None
_agent_cache_lock = threading.Lock()


def _cached_suggestion_agents(
    settings: RetailSettings, definitions: Sequence[KpiDefinition]
) -> Tuple[Any, Any]:
    """The two suggestion agents for this registry snapshot, built once and reused.

    Keyed by the sorted set of registry identifiers -- the same set the K12 prompt embeds as
    "existing" -- so a KPI created or deleted between requests invalidates the cache the next
    time this is called, rather than serving agents whose prompt still lists a KPI that is now
    gone (or omits one that now exists).
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
) -> wire.KpiSuggestionList:
    """#234/F1: on-demand KPI suggestions, read straight through to K12 -- see the module
    docstring for what this deliberately omits (prose, warehouse samples, and the wasted
    use-case agent call ``run_suggestions`` always makes alongside it).

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
    """
    business_area = _validated_business_area(business_area, vocabularies)
    resolved_agents = (
        agents
        if agents is not None
        else _cached_suggestion_agents(settings, definitions)
    )
    with open_semantic_reader(settings) as reader:
        run: SuggestionRun = run_suggestions(
            settings,
            reader=reader,
            existing_definitions=definitions,
            proposals=None,
            dry_run=True,
            agents=resolved_agents,
        )
    suggestions = _filter_suggestions(run.kpis.suggestions, business_area)
    if not include_sql:
        suggestions = [s.model_copy(update={"proposed_sql": None}) for s in suggestions]
    return wire.KpiSuggestionList(
        suggestions=suggestions,
        notes=_suggestion_notes(run),
        semantic_snapshot=run.snapshot,
    )


@router.get("/kpis/suggestions", response_model=wire.KpiSuggestionList)
def list_kpi_suggestions(
    request: Request,
    business_area: Optional[str] = None,
    snapshot: _SuggestionRegistrySnapshot = Depends(_suggestion_registry_snapshot),
    _: None = Depends(require_scopes("kpis:read")),
):
    return _list_kpi_suggestions(
        RetailSettings(),
        definitions=snapshot.definitions,
        vocabularies=snapshot.vocabularies,
        business_area=business_area,
        include_sql=_may_read_sql(request),
    )


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
