"""R13's real router: dashboard storage, gold reads for ``kpi`` tiles, cached guarded reads
for ``query`` tiles, per-tile edits, and the compose-then-poll suggestion run (1.2.0).

Every ``kpi_identifier`` a ``kpi`` tile names must be a registered, ``ready`` KPI (K11's
feasibility, the same set difference ``materialise_gold``/``assess_kpi_feasibility`` use) --
checked here, not in ``dashboards/store.py``, because it needs the KPI registry and an open
semantic reader, which are concerns this store deliberately does not carry. Every ``query``
tile's SQL must pass the warehouse read guard (``dashboards/queries.py``'s ``check_query``)
-- also checked here, on every write that carries one, and the statement stored is the
guard's rewrite when it made one, so what is saved is exactly what will run.

The readiness check (``_check_tiles_ready``) runs before ``store.create``/``store.update``
actually write the row, over a separate connection, with nothing holding the checked KPIs
still in between -- a KPI deleted in that gap (K11/#240's ``DELETE /kpis`` review, round 2)
would otherwise leave a tile pointing at nothing. ``DashboardStore.create``/``update``/
``upsert_tile`` themselves guard against exactly that, atomically, as part of the same
statement that writes the tiles; this router turns the ``DashboardKpiVanishedError`` that
guard raises into a 409, the same way a lost-update race on ``PATCH /kpis`` becomes one.

A ``query`` tile is computed once, in the request that writes it (bounded by the guard's
statement timeout and row cap), so it renders immediately -- there is no scheduler honouring
``refresh_schedule`` yet, for these tiles or for gold, and ``POST /dashboards/{id}/refresh``
is the "refresh now" until there is. Opening a dashboard never runs a tile's SQL.
"""

from __future__ import annotations

import asyncio
import dataclasses
import threading
import traceback
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from itertools import combinations
from typing import (
    Any,
    Callable,
    Dict,
    FrozenSet,
    Iterator,
    List,
    Optional,
    Sequence,
    Tuple,
    TypeVar,
)

from egpt.agent import Agent
from egpt.utils.log import log_error, log_warning
from fastapi import APIRouter, Depends, HTTPException, Request

from egpt_retail.api import models as wire
from egpt_retail.app.db import pooled_control_plane_connection
from egpt_retail.app.scopes import require_scopes
from egpt_retail.app.semantic import open_semantic_reader
from egpt_retail.app.settings import RetailSettings
from egpt_retail.app.sql_guard import build_warehouse_read_guard
from egpt_retail.contracts.dashboards import (
    AxisKind,
    ChartAxis,
    ChartKind,
    Dashboard,
    DashboardFilter,
    RefreshSchedule,
    Tile,
    TilePosition,
    TileQuery,
    TileSource,
    Visibility,
)
from egpt_retail.contracts.kpi import (
    FeasibilityVerdict,
    KpiDefinition,
    assess_registry,
    referenced_tables,
)
from egpt_retail.contracts.semantic import (
    JoinEdge,
    SemanticRead,
    SemanticReadError,
    Table,
    available_tables,
)
from egpt_retail.contracts.vocabulary import VocabularySet
from egpt_retail.dashboards.data import read_dashboard_data
from egpt_retail.dashboards.queries import (
    arefresh_query_tiles,
    build_query_engine,
    check_query,
    personal_data_columns,
    preview_query,
)
from egpt_retail.dashboards.query_results import (
    DashboardQueryResultStore,
    QueryResultRecord,
)
from egpt_retail.dashboards.store import (
    UNSET,
    DashboardKpiVanishedError,
    DashboardStore,
)
from egpt_retail.kpi.store import KpiRegistryStore
from egpt_retail.skills import dashboard_suggestion as dsg
from egpt_retail.suggestions.dashboard_agents import build_dashboard_suggestion_agent
from egpt_retail.suggestions.dashboard_runs import (
    DashboardSuggestionRunRecord,
    DashboardSuggestionRunStore,
)

__all__ = ["router", "tile_readiness_refusal"]

router = APIRouter()
_T = TypeVar("_T")


class DashboardValidationError(ValueError):
    """A tile names a KPI that is not registered or not ready, or carries a query the guard
    refused. The router turns this into a 422 with the message the spec's own example uses."""


# --- store dependency, owner, readiness gate ----------------------------------------------


def _dashboard_store() -> Iterator[DashboardStore]:
    settings = RetailSettings()
    connection = pooled_control_plane_connection(settings)
    try:
        yield DashboardStore(connection)
    finally:
        connection.close()


@contextmanager
def _open_query_results(
    settings: RetailSettings,
) -> Iterator[DashboardQueryResultStore]:
    """The query-result cache on its own pooled connection, released on exit."""
    connection = pooled_control_plane_connection(settings)
    try:
        yield DashboardQueryResultStore(connection)
    finally:
        connection.close()


def _owner(request: Request) -> str:
    """The caller's identity, scoping ownership and edit/delete rights. This deployment is
    single-tenant (D3): with authorization off, ``"local"`` stands in for the one local
    caller. With authorization on, ``user_id`` must be present -- ``AuthMiddleware`` sets it
    to ``None`` *explicitly* (not merely omits it) for a token that decodes and passes scope
    checks but carries no ``sub`` claim, and collapsing that onto a shared ``"local"`` would
    let every such caller read and mutate every other's dashboards. Refuse instead: there is
    no other identity in the token this router can safely key ownership on.
    """
    if not getattr(request.state, "authorization_enabled", False):
        return "local"
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(
            status_code=401,
            detail=(
                "This token carries no subject claim; dashboards are scoped to a caller "
                "identity and cannot be owned by an anonymous one."
            ),
        )
    return user_id


def tile_readiness_refusal(
    wanted: Sequence[str],
    definitions: Sequence[KpiDefinition],
    tables: FrozenSet[str],
) -> Optional[str]:
    """The 422 refusal message for a set of wanted KPI identifiers, or ``None`` if every one
    is registered and ``ready``. Pure -- no I/O, no Postgres, no Neo4j -- so this decision
    (R13's own headline guarantee) is unit-testable directly, the same way
    ``assess_registry``/``FeasibilityVerdict`` already are. :func:`_check_tiles_ready` is the
    thin I/O wrapper that gathers ``definitions`` and ``tables`` and calls this.
    """
    found = {d.identifier for d in definitions}
    unregistered = set(wanted) - found
    if unregistered:
        return f"{sorted(unregistered)[0]} is not a registered KPI and cannot be tiled."

    feasibility = assess_registry(definitions, tables)
    for identifier in sorted(wanted):
        result = feasibility[identifier]
        if result.verdict is not FeasibilityVerdict.READY:
            missing = ", ".join(sorted(result.missing_tables)) or "everything"
            return (
                f"{identifier} is {result.verdict.value} (missing: {missing}) and cannot "
                "be tiled."
            )
    return None


def _check_tiles_ready(tiles: Sequence[Tile], settings: RetailSettings) -> None:
    """Refuses (raising :class:`DashboardValidationError`) unless every ``kpi`` tile's KPI is
    registered and ``ready`` -- a blocked or partial KPI has nothing in gold to read. Gathers
    the inputs :func:`tile_readiness_refusal` needs and raises on its verdict. ``query``
    tiles have no KPI to check; :func:`_check_query_tiles` is their gate."""
    wanted = sorted(
        {str(tile.kpi_identifier) for tile in tiles if tile.source is TileSource.KPI}
    )
    if not wanted:
        return

    connection = pooled_control_plane_connection(settings)
    try:
        definitions = [
            d for d in KpiRegistryStore(connection).list() if d.identifier in wanted
        ]
    finally:
        connection.close()

    with open_semantic_reader(settings) as reader:
        tables = available_tables(reader)

    message = tile_readiness_refusal(wanted, definitions, tables)
    if message is not None:
        raise DashboardValidationError(message)


def _check_query_tiles(
    tiles: Sequence[Tile],
    settings: RetailSettings,
    *,
    only: Optional[Sequence[str]] = None,
) -> List[Tile]:
    """Run every ``query`` tile (or just the ids in ``only``) through the guard. A refusal
    raises :class:`DashboardValidationError`; a pass returns the tiles with the statement
    the guard cleared (its rewrite, when it made one) and ``source_tables`` as the guard
    parsed them -- what gets stored is exactly what will run.

    **No ``context_tables`` is passed here, by design (round-1 review, finding #5).**
    ``context_tables`` (see :func:`~egpt_retail.dashboards.queries.check_query`) narrows the
    guard's own semantic-layer allow-list further, to a specific set someone committed to in
    advance -- meaningful for the compose agent (:func:`_build_dashboard_suggestion`'s
    ``_check`` closure), which is shown only the tables a person selected as this run's
    context and must not wander past them even though the guard's allow-list would let it.
    A hand-written or hand-edited tile has no equivalent prior commitment to narrow against:
    the boundary every write already gets, human or agent, identical and independent of this
    function, is the guard's own semantic-layer allow-list (``build_warehouse_read_guard``) --
    passing ``tile.query.source_tables`` here would be tautological (that field is *derived
    from* the SQL by :func:`_query_from_wire`/the guard's own parse, never an independent
    constraint), and inventing a narrower one with no real "what was this person shown" backing
    it would be theater, not a check. The one place a comparable, genuine prior commitment
    exists for a human editor -- the chart editor's own schema pane, populated from whatever
    the chart already declared -- narrows its own ``POST /dashboards/queries/preview`` calls
    client-side instead (``ChartEditorPanel.jsx``'s ``initialSourceTables``).
    """
    wanted = set(only) if only is not None else None
    to_check = [
        t
        for t in tiles
        if t.source is TileSource.QUERY and (wanted is None or t.id in wanted)
    ]
    if not to_check:
        return list(tiles)

    checked: Dict[str, Tile] = {}
    with open_semantic_reader(settings) as reader:
        guard = build_warehouse_read_guard(reader)

        def _personal(names: Sequence[str]) -> Dict[str, FrozenSet[str]]:
            return personal_data_columns(reader, names)

        for tile in to_check:
            assert tile.query is not None
            verdict = check_query(tile.query.sql, guard=guard, personal_data=_personal)
            if not verdict.runnable:
                raise DashboardValidationError(
                    f"{tile.title or tile.id}: {verdict.detail}"
                )
            checked[tile.id] = dataclasses.replace(
                tile,
                query=dataclasses.replace(
                    tile.query,
                    sql=verdict.sql,
                    source_tables=frozenset(verdict.tables),
                ),
            )
    return [checked.get(t.id, t) for t in tiles]


def _compute_query_tiles(
    dashboard: Dashboard,
    settings: RetailSettings,
    *,
    only: Optional[Sequence[str]] = None,
) -> None:
    """Fill the cache for the dashboard's ``query`` tiles (or just ``only``, when given --
    an empty sequence means none, ``None`` means every query tile) so they render on the next
    open. Best-effort: a failure is logged and the tile reads as ``never_computed`` until the
    next refresh -- the write it follows has already landed."""
    if not any(t.source is TileSource.QUERY for t in dashboard.tiles):
        return
    if only is not None and not only:
        return
    try:
        with _open_query_results(settings) as results:
            # `arefresh_query_tiles` runs every tile's guard check and warehouse round trip
            # concurrently (round-1 review, finding #12: the strictly serial loop this
            # replaced cost `sum(duration)` across tiles instead of `max(duration)`; that
            # sync version was itself deleted in round 3 once both call sites here had moved
            # to this one and nothing else called it). `asyncio.run` is safe here: every
            # caller of this function is a plain `def` route FastAPI already runs off its
            # own worker thread, so there is no event loop on this thread for `asyncio.run`
            # to collide with.
            asyncio.run(arefresh_query_tiles(dashboard, settings, results, only=only))
    except Exception:
        log_warning(
            f"dashboard {dashboard.id}: query tiles could not be computed: "
            f"{traceback.format_exc(limit=3)}"
        )


def _changed_query_tile_ids(before: Dashboard, tiles: Sequence[Tile]) -> List[str]:
    """Which of ``tiles``' ids are new, or whose ``query`` actually changed, relative to
    ``before`` -- what a whole-array write's recompute should scope to instead of every
    ``query`` tile unconditionally (round-1 review, finding #11: a whole-array PATCH that
    merely renames the dashboard or repositions tiles was re-running every query tile against
    the warehouse). A tile whose ``source``/``chart``/``title``/position changed but whose
    ``query`` did not needs no recompute -- its cached rows are still exactly right."""
    previous = {t.id: t for t in before.tiles}
    changed: List[str] = []
    for tile in tiles:
        if tile.source is not TileSource.QUERY:
            continue
        prior = previous.get(tile.id)
        if prior is None or prior.query != tile.query:
            changed.append(tile.id)
    return changed


def _remove_orphaned_query_results(
    dashboard_id: str, removed_tile_ids: Sequence[str], settings: RetailSettings
) -> None:
    """Delete the cached result of every id in ``removed_tile_ids``. The dedicated
    ``DELETE /dashboards/{id}/tiles/{tile_id}`` route already does this for a single removal;
    this closes the same gap for a whole-array write that drops a tile by omitting it from the
    array rather than calling that route -- previously left as an unbounded, silently
    accumulating cache row (round-1 review, finding #11)."""
    if not removed_tile_ids:
        return
    try:
        with _open_query_results(settings) as results:
            for tile_id in removed_tile_ids:
                results.delete(dashboard_id, tile_id)
    except Exception:
        log_warning(
            f"dashboard {dashboard_id}: cached result(s) of removed tile(s) "
            f"{sorted(removed_tile_ids)} not cleaned up: {traceback.format_exc(limit=3)}"
        )


# --- wire conversions ------------------------------------------------------------------------


def _axis_from_wire(axis: wire.ChartAxis) -> ChartAxis:
    return ChartAxis(
        column=axis.column,
        kind=AxisKind((axis.kind or wire.AxisKind.category).value),
        label=axis.label or "",
        unit=axis.unit or "",
    )


def _query_from_wire(query: wire.TileQuery) -> TileQuery:
    """A ``TileQuery`` from the wire. ``source_tables`` is always re-derived server-side --
    from the SQL here, then from the guard's parse in :func:`_check_query_tiles` -- so a
    client cannot declare fewer tables than the statement reads."""
    sql = query.sql.strip()
    tables = referenced_tables(sql)
    return TileQuery(
        sql=sql,
        source_tables=tables if tables else frozenset({"__unparsed__"}),
        dialect=query.dialect or "postgres",
        x=_axis_from_wire(query.x) if query.x is not None else None,
        y=tuple(_axis_from_wire(axis) for axis in (query.y or ())),
        series_by=query.series_by,
    )


def _tile_from_write(write: wire.TileWrite) -> Tile:
    source = TileSource((write.source or wire.TileSource.kpi).value)
    try:
        return Tile(
            id=write.id or f"tl_{uuid.uuid4().hex[:8]}",
            kpi_identifier=write.kpi_identifier if source is TileSource.KPI else None,
            chart=ChartKind(write.chart.value),
            position=TilePosition(
                x=write.position.x,
                y=write.position.y,
                w=write.position.w,
                h=write.position.h,
            ),
            title=write.title or "",
            grain_value=write.grain_value,
            source=source,
            query=(
                _query_from_wire(write.query)
                if source is TileSource.QUERY and write.query is not None
                else None
            ),
            description=write.description or "",
            rationale=write.rationale or "",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _filter_from_wire(item: wire.DashboardFilter) -> DashboardFilter:
    return DashboardFilter(field=item.field.value, value=item.value)


def _axis_out(axis: ChartAxis) -> wire.ChartAxis:
    return wire.ChartAxis(
        column=axis.column,
        kind=wire.AxisKind(axis.kind.value),
        label=axis.label,
        unit=axis.unit,
    )


def _query_out(query: TileQuery) -> wire.TileQuery:
    return wire.TileQuery(
        sql=query.sql,
        dialect=query.dialect,
        source_tables=sorted(query.source_tables),
        x=_axis_out(query.x) if query.x is not None else None,
        y=[_axis_out(axis) for axis in query.y],
        series_by=query.series_by,
    )


def _tile_out(tile: Tile) -> wire.Tile:
    return wire.Tile(
        id=tile.id,
        source=wire.TileSource(tile.source.value),
        kpi_identifier=tile.kpi_identifier,
        query=_query_out(tile.query) if tile.query is not None else None,
        title=tile.title,
        chart=wire.ChartKind(tile.chart.value),
        position=wire.TilePosition(
            x=tile.position.x, y=tile.position.y, w=tile.position.w, h=tile.position.h
        ),
        grain_value=tile.grain_value,
        description=tile.description,
        rationale=tile.rationale,
    )


def _filter_out(item: DashboardFilter) -> wire.DashboardFilter:
    return wire.DashboardFilter(
        field=wire.DashboardFilterField(item.field), value=item.value
    )


def _dashboard_out(dashboard: Dashboard) -> wire.Dashboard:
    return wire.Dashboard(
        id=dashboard.id,
        name=dashboard.name,
        description=dashboard.description,
        tiles=[_tile_out(t) for t in dashboard.tiles],
        filters=[_filter_out(f) for f in dashboard.filters],
        refresh_schedule=wire.RefreshSchedule(dashboard.refresh_schedule.value),
        visibility=wire.Visibility(dashboard.visibility.value),
        owner=dashboard.owner,
        created_at=dashboard.created_at,
        updated_at=dashboard.updated_at,
    )


# --- dashboard suggestion runs ------------------------------------------------------------------
#
# Mirrors `api/routers/kpis.py`'s #254 shape (run recorded, `202` returned, executed as a task,
# polled until terminal) for the same reason: a live model call takes long enough that it does
# not belong inside one request, and the api serves from more than one instance. See
# `egpt_retail.skills.dashboard_suggestion`'s module docstring for what this agent composes from.


@dataclass(frozen=True)
class _DashboardSuggestionSnapshot:
    """What a dashboard suggestion run needs from the registry, read once up front -- the
    ``ready`` subset only, the same set ``POST /dashboards`` itself requires every ``kpi``
    tile to reference, so a suggestion is exactly as adoptable as a dashboard built by hand."""

    ready_kpis: Tuple[KpiDefinition, ...]
    vocabularies: VocabularySet


def _dashboard_suggestion_snapshot() -> _DashboardSuggestionSnapshot:
    """The ready-KPI snapshot a suggestion run needs, read and released up front -- not a
    request-scoped ``Depends(_dashboard_store)``/``_kpi_store`` that would otherwise stay open
    for the whole span of the live model call this snapshot feeds."""
    settings = RetailSettings()
    connection = pooled_control_plane_connection(settings)
    try:
        store = KpiRegistryStore(connection)
        definitions = tuple(store.list())
        vocabularies = store.vocabularies
    finally:
        connection.close()
    with open_semantic_reader(settings) as reader:
        tables = available_tables(reader)
    feasibility = assess_registry(definitions, tables)
    ready = tuple(
        d
        for d in definitions
        if feasibility[d.identifier].verdict is FeasibilityVerdict.READY
    )
    return _DashboardSuggestionSnapshot(ready_kpis=ready, vocabularies=vocabularies)


#: How many context tables a run will still pair up for join paths. Above this the pairwise
#: `find_join_path` walk (n choose 2 graph queries) stops being worth its cost; the agent is
#: then told only the joins the caller's own tables declare among themselves.
_MAX_JOIN_PAIRS = 45


def _build_context(
    settings: RetailSettings,
    snapshot: _DashboardSuggestionSnapshot,
    *,
    kpi_identifiers: Sequence[str],
    table_names: Sequence[str],
    chart_budget: Optional[int],
    reader: Optional[SemanticRead] = None,
) -> dsg.SuggestionContext:
    """What one run shows the agent: the selected ready KPIs, the union of their source
    tables plus ``table_names``, the tables one join hop away on a declared path between
    any two of those, and the join edges themselves. ``reader`` is a seam for a caller that
    already holds one; a bare call opens its own.

    A caller who narrowed neither -- ``kpi_identifiers`` and ``table_names`` both empty --
    gets every ready KPI as context instead of an empty one. Narrowing is opt-in: it exists
    so a person composing from a chat prompt alone (no metric picked, no table added) still
    gets a dashboard built from everything this deployment already knows how to compute,
    rather than the empty-charts-with-a-note result an empty context deterministically
    produces (the agent's own refusal rule, section 8 of ``dashboard_suggestion.md``). This
    is what the pre-1.2.0 agent always did -- select from the whole ready registry, every
    run; 1.2.0's narrower default now applies only once the caller has actually started
    narrowing it themselves, by picking a metric or adding a table.

    Raises ``ValueError`` for an identifier outside the ready set -- readable, so the run
    row can carry it -- and lets ``SemanticReadError`` through for a graph that cannot
    answer, which is a different failure and must not look like an empty context.
    """
    by_identifier = {k.identifier: k for k in snapshot.ready_kpis}
    unknown = sorted(set(kpi_identifiers) - set(by_identifier))
    if unknown:
        raise ValueError(
            f"{unknown[0]} is not a registered, ready KPI and cannot be composed from."
        )
    if kpi_identifiers or table_names:
        kpis = tuple(by_identifier[i] for i in sorted(set(kpi_identifiers)))
    else:
        kpis = tuple(sorted(snapshot.ready_kpis, key=lambda k: k.identifier))
    names = {t.lower() for k in kpis for t in k.source_tables}
    names |= {t.strip().lower() for t in table_names if t.strip()}

    if reader is None:
        with open_semantic_reader(settings) as opened:
            return _build_context(
                settings,
                snapshot,
                kpi_identifiers=kpi_identifiers,
                table_names=table_names,
                chart_budget=chart_budget,
                reader=opened,
            )

    tables: Dict[str, Table] = {}
    for name in sorted(names):
        table = reader.get_table(name)
        if table is not None:
            tables[table.ref.name.lower()] = table

    joins: Dict[Tuple[str, str, str], JoinEdge] = {}
    pairs = list(combinations(sorted(tables), 2))
    if len(pairs) <= _MAX_JOIN_PAIRS:
        for left, right in pairs:
            try:
                path = reader.find_join_path(left, right)
            except SemanticReadError:
                continue
            if path is None:
                continue
            for edge in path.edges:
                joins[(edge.left_table, edge.right_table, edge.on)] = edge
            for hop in path.intermediate_tables:
                if hop.lower() not in tables:
                    table = reader.get_table(hop)
                    if table is not None:
                        tables[table.ref.name.lower()] = table

    return dsg.SuggestionContext(
        kpis=kpis,
        tables=tuple(tables[name] for name in sorted(tables)),
        joins=tuple(joins[key] for key in sorted(joins)),
        chart_budget=chart_budget,
    )


#: The dashboard suggestion agent, reused across requests until the context changes.
#: CLAUDE.md: "Never create agents in loops -- reuse them for performance." Single-slot, like
#: `kpis.py`'s own cache -- only the current context is ever worth serving a stale agent for.
_dash_agent_cache_key: Optional[Tuple[Any, ...]] = None
_dash_agent_cache_agent: Optional[Agent] = None
_dash_agent_cache_lock = threading.Lock()


def _context_cache_key(context: dsg.SuggestionContext) -> Tuple[Any, ...]:
    return (
        tuple(sorted(k.identifier for k in context.kpis)),
        tuple(sorted(context.table_names)),
        context.budget,
    )


def _cached_dashboard_suggestion_agent(
    settings: RetailSettings,
    snapshot: _DashboardSuggestionSnapshot,
    context: Optional[dsg.SuggestionContext] = None,
) -> Agent:
    """The suggestion agent for this context, built once and reused. Keyed by the selected
    identifiers, the context table names and the chart budget -- what its prompt embeds --
    so a different selection builds a new agent and the same selection twice does not. The
    returned ``Agent`` is shared across concurrent requests; callers must ``deep_copy()``
    before ``.run()`` (mirrors `kpis.py`'s `_cached_suggestion_agents`)."""
    global _dash_agent_cache_key, _dash_agent_cache_agent
    resolved = context or dsg.SuggestionContext(kpis=snapshot.ready_kpis)
    key = _context_cache_key(resolved)
    with _dash_agent_cache_lock:
        if _dash_agent_cache_key == key and _dash_agent_cache_agent is not None:
            return _dash_agent_cache_agent
        agent = build_dashboard_suggestion_agent(
            settings, context=resolved, vocabularies=snapshot.vocabularies
        )
        _dash_agent_cache_key = key
        _dash_agent_cache_agent = agent
        return agent


def _drafts_of(response: Any, schema: type) -> Any:
    """The typed draft an agent returned, or a refusal that names what came back -- mirrors
    `suggestions/run.py`'s own helper of the same name and purpose."""
    content = getattr(response, "content", None)
    if isinstance(content, schema):
        return content
    raise ValueError(
        f"{schema.__name__} expected from the agent; got "
        f"{type(content).__name__}: {str(content)[:120]!r}"
    )


def _build_dashboard_suggestion(
    settings: RetailSettings,
    *,
    snapshot: _DashboardSuggestionSnapshot,
    purpose: str,
    agent: Optional[Agent] = None,
    context: Optional[dsg.SuggestionContext] = None,
    on_stage: Optional[Callable[[str], None]] = None,
) -> wire.DashboardSuggestion:
    """One suggestion pass: run the agent, then assemble its draft -- every composed query
    through the guard and a bounded preview. ``agent`` is a test-only seam (mirrors
    `kpis.py`'s own); a real call resolves it from the cache and ``deep_copy()``s it first
    -- the cache shares one ``Agent`` across concurrent requests, and ``.run()`` mutates
    unsynchronized per-agent state that isn't safe to race. ``on_stage`` is told where the
    pass is, for the run row."""
    resolved_context = context or dsg.SuggestionContext(kpis=snapshot.ready_kpis)
    resolved_agent = (
        agent
        if agent is not None
        else _cached_dashboard_suggestion_agent(
            settings, snapshot, resolved_context
        ).deep_copy()
    )
    if on_stage:
        on_stage("drafting")
    draft = _drafts_of(resolved_agent.run(purpose), dsg.DashboardSuggestionDraft)

    if on_stage:
        on_stage("guarding")
    with open_semantic_reader(settings) as reader:
        guard = build_warehouse_read_guard(reader)
        engine: Any = None
        previewed = {"stage": False}

        def _check(sql: str, tables: FrozenSet[str]) -> Any:
            return check_query(
                sql,
                guard=guard,
                context_tables=tables,
                personal_data=lambda names: personal_data_columns(reader, names),
            )

        def _preview(sql: str) -> wire.QueryPreview:
            nonlocal engine
            if on_stage and not previewed["stage"]:
                previewed["stage"] = True
                on_stage("previewing")
            if engine is None:
                engine = build_query_engine(settings)
            return preview_query(settings, sql, reader=reader, engine=engine)

        try:
            report = dsg.assemble(
                draft,
                ready_kpis=snapshot.ready_kpis,
                context=resolved_context,
                vocabularies=snapshot.vocabularies,
                check=_check,
                preview=_preview,
            )
        finally:
            if engine is not None:
                engine.dispose()
    return report.suggestion


class DashboardSuggestionTimeout(Exception):
    """A dashboard suggestion run exceeded ``retail_suggestion_timeout_seconds``."""

    def __init__(self, seconds: float) -> None:
        super().__init__(f"The suggestion run did not finish within {seconds:g}s.")
        self.seconds = seconds


async def _run_bounded(fn: Callable[[], _T], timeout: float) -> _T:
    """``fn`` on a worker thread, waited on for at most ``timeout`` seconds -- mirrors
    `kpis.py`'s `_run_bounded`/#254 exactly; see its docstring for why the wait, not the work,
    is what the bound cuts."""
    try:
        return await asyncio.wait_for(asyncio.to_thread(fn), timeout)
    except asyncio.TimeoutError as exc:
        raise DashboardSuggestionTimeout(timeout) from exc


#: Same grace period `kpis.py` gives a `queued`/`running` row before concluding its executor
#: is gone -- generous, so a run still inside its own bound is never closed by a poll.
_LOST_RUN_GRACE_SECONDS = 60.0


def _stale_after(settings: RetailSettings) -> timedelta:
    return timedelta(
        seconds=settings.retail_suggestion_timeout_seconds + _LOST_RUN_GRACE_SECONDS
    )


@contextmanager
def _open_suggestion_run_store(
    settings: RetailSettings,
) -> Iterator[DashboardSuggestionRunStore]:
    """A ``DashboardSuggestionRunStore`` on its own pooled connection, released on exit -- not
    a ``Depends``: the executor task needs one outside any request."""
    connection = pooled_control_plane_connection(settings)
    try:
        yield DashboardSuggestionRunStore(connection)
    finally:
        connection.close()


def _with_suggestion_run_store(
    settings: RetailSettings, action: Callable[[DashboardSuggestionRunStore], _T]
) -> _T:
    with _open_suggestion_run_store(settings) as store:
        return action(store)


def _suggestion_run_out(
    record: DashboardSuggestionRunRecord,
) -> wire.DashboardSuggestionRun:
    result: Optional[wire.DashboardSuggestion] = None
    if record.result is not None:
        result = wire.DashboardSuggestion.model_validate(record.result)
    stage: Optional[wire.DashboardSuggestionStage] = None
    if record.stage:
        try:
            stage = wire.DashboardSuggestionStage(record.stage)
        except ValueError:
            stage = None
    return wire.DashboardSuggestionRun(
        id=record.identifier,
        status=wire.DashboardSuggestionRunStatus(record.status.value),
        stage=stage,
        purpose=record.purpose,
        created_at=record.created_at,
        started_at=record.started_at,
        finished_at=record.finished_at,
        result=result,
        error=record.error,
    )


#: Strong references to in-flight executor tasks, so the event loop cannot collect one
#: mid-run -- the same reason `kpis.py`'s own `_suggestion_run_tasks` set exists.
_dashboard_suggestion_run_tasks: set[asyncio.Task[None]] = set()


async def _await_dashboard_suggestion_runs() -> None:
    """Wait for every run this process has in flight -- a test-only seam, mirrors
    `kpis.py`'s `_await_suggestion_runs`."""
    if _dashboard_suggestion_run_tasks:
        await asyncio.gather(
            *list(_dashboard_suggestion_run_tasks), return_exceptions=True
        )


def _describe_suggestion_failure(exc: BaseException) -> str:
    """What a `failed` row says -- mirrors `kpis.py`'s `_describe_failure`: the pipeline's own
    refusals are readable and pass through; anything else is summarised by type only, since raw
    exception text from a driver error can carry connection strings."""
    if isinstance(exc, (ValueError, SemanticReadError)):
        return str(exc)
    return (
        f"The suggestion run failed ({type(exc).__name__}); "
        "the api log has the traceback."
    )


async def _execute_dashboard_suggestion_run(
    run_id: str,
    settings: RetailSettings,
    snapshot: _DashboardSuggestionSnapshot,
    purpose: str,
    *,
    kpi_identifiers: Sequence[str] = (),
    table_names: Sequence[str] = (),
    chart_budget: Optional[int] = None,
) -> None:
    """The executor behind `POST /dashboards/suggestions`: `queued` -> `running` -> build the
    context (`reading`) -> one bounded compose pass (`drafting`, `guarding`, `previewing`)
    -> `succeeded`/`failed`. Each row write opens and releases its own connection; the
    model call in between holds none."""

    def _stage(stage: str) -> None:
        try:
            _with_suggestion_run_store(
                settings, lambda store: store.mark_stage(run_id, stage)
            )
        except Exception:
            log_warning(
                f"dashboard suggestion run {run_id}: stage {stage} not recorded"
            )

    def _pass() -> wire.DashboardSuggestion:
        _stage("reading")
        context = _build_context(
            settings,
            snapshot,
            kpi_identifiers=kpi_identifiers,
            table_names=table_names,
            chart_budget=chart_budget,
        )
        return _build_dashboard_suggestion(
            settings,
            snapshot=snapshot,
            purpose=purpose,
            context=context,
            on_stage=_stage,
        )

    try:
        await asyncio.to_thread(
            _with_suggestion_run_store,
            settings,
            lambda store: store.mark_running(run_id),
        )
        try:
            result = await _run_bounded(
                _pass, settings.retail_suggestion_timeout_seconds
            )
        except DashboardSuggestionTimeout as exc:
            timed_out = str(exc)
            await asyncio.to_thread(
                _with_suggestion_run_store,
                settings,
                lambda store: store.mark_failed(run_id, timed_out),
            )
            return
        except Exception as exc:
            log_error(
                f"dashboard suggestion run {run_id} failed: {traceback.format_exc(limit=5)}"
            )
            failure = _describe_suggestion_failure(exc)
            await asyncio.to_thread(
                _with_suggestion_run_store,
                settings,
                lambda store: store.mark_failed(run_id, failure),
            )
            return
        payload = result.model_dump(mode="json")
        await asyncio.to_thread(
            _with_suggestion_run_store,
            settings,
            lambda store: store.mark_succeeded(run_id, payload),
        )
    except Exception:
        log_error(
            f"dashboard suggestion run {run_id}: its outcome could not be recorded: "
            f"{traceback.format_exc(limit=5)}"
        )


# --- routes ------------------------------------------------------------------------------------


@router.get("/dashboards", response_model=list[wire.Dashboard])
def list_dashboards(
    request: Request,
    store: DashboardStore = Depends(_dashboard_store),
    _: None = Depends(require_scopes("dashboards:read")),
):
    return [_dashboard_out(d) for d in store.list_visible_to(_owner(request))]


def _reject_duplicate_tile_ids(tiles: Sequence[Tile]) -> None:
    """422 if the same tile ``id`` appears twice in one whole-array write.

    ``add_dashboard_tile`` already 409s a new tile whose id collides with an *existing* one
    (``current.tile(tile.id) is not None``), but nothing catches two entries of the *same*
    payload sharing an id -- neither dataclass's ``__post_init__``, nor
    ``store.create``/``store.update``. Traced through ``arefresh_query_tiles``: two ``query``
    tiles with the same id in one payload both persist in the stored JSON array, but
    ``written[tile.id] = ...`` in the refresh loop means whichever is processed last silently
    wins the one cache row for that id -- the other tile's real result is dropped even though
    the tile itself is still there. Caught here, once, for both whole-array write routes
    (``create_dashboard``, the whole-array branch of ``update_dashboard``) rather than in the
    domain layer, since a duplicate *within one request* is a client-input shape problem, not a
    invariant either ``Tile`` or ``Dashboard`` can see on their own (each tile is valid in
    isolation; it is the pair that collides).
    """
    seen: set = set()
    for tile in tiles:
        if tile.id in seen:
            raise DashboardValidationError(
                f"tile id {tile.id!r} appears more than once in this write; every tile "
                "needs its own id."
            )
        seen.add(tile.id)


def _validated_tiles(tiles: List[Tile], settings: RetailSettings) -> List[Tile]:
    """Both gates, in order: no duplicate ids within the payload, KPI readiness for ``kpi``
    tiles, the guard for ``query`` tiles. Any refusal is a 422."""
    try:
        _reject_duplicate_tile_ids(tiles)
        _check_tiles_ready(tiles, settings)
        return _check_query_tiles(tiles, settings)
    except DashboardValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/dashboards", response_model=wire.Dashboard, status_code=201)
def create_dashboard(
    body: wire.DashboardWrite,
    request: Request,
    store: DashboardStore = Depends(_dashboard_store),
    _: None = Depends(require_scopes("dashboards:write")),
):
    settings = RetailSettings()
    tiles = _validated_tiles(
        [_tile_from_write(t) for t in (body.tiles or [])], settings
    )

    try:
        dashboard = store.create(
            name=body.name,
            owner=_owner(request),
            description=body.description or "",
            tiles=tiles,
            filters=[_filter_from_wire(f) for f in (body.filters or [])],
            refresh_schedule=RefreshSchedule(
                (body.refresh_schedule or wire.RefreshSchedule.nightly).value
            ),
            visibility=Visibility((body.visibility or wire.Visibility.private).value),
        )
    except DashboardKpiVanishedError as exc:
        # A tile's KPI passed `_check_tiles_ready` above but was deleted before this write
        # landed -- re-check readiness and retry, the same as a `PATCH /kpis` lost-update 409.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _compute_query_tiles(dashboard, settings)
    return _dashboard_out(dashboard)


@router.post("/dashboards/queries/preview", response_model=wire.QueryPreview)
async def preview_dashboard_query(
    body: wire.QueryPreviewRequest,
    _: None = Depends(require_scopes("dashboards:write")),
):
    """The chart editor's "Run preview": the guard, then a bounded run. Stores nothing."""
    settings = RetailSettings()
    sql = body.query.sql.strip()
    if not sql:
        raise HTTPException(status_code=422, detail="query.sql must not be empty.")
    try:
        return await asyncio.to_thread(
            preview_query, settings, sql, table_names=body.table_names
        )
    except SemanticReadError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post(
    "/dashboards/suggestions",
    response_model=wire.DashboardSuggestionRun,
    status_code=202,
)
async def start_dashboard_suggestion_run(
    body: wire.DashboardSuggestionRunRequest,
    snapshot: _DashboardSuggestionSnapshot = Depends(_dashboard_suggestion_snapshot),
    _: None = Depends(require_scopes("dashboards:read")),
):
    """Start a run and return it `queued`. `dashboards:read`, like the route it mirrors
    (`POST /kpis/suggestions`): what is created is a run row, never a dashboard -- adoption is
    still `POST /dashboards`."""
    purpose = body.purpose.strip()
    if not purpose:
        raise HTTPException(status_code=422, detail="purpose must not be empty.")
    kpi_identifiers = sorted(
        {i.strip() for i in (body.kpi_identifiers or []) if i.strip()}
    )
    ready = {k.identifier for k in snapshot.ready_kpis}
    unknown = sorted(set(kpi_identifiers) - ready)
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"{unknown[0]} is not a registered, ready KPI and cannot be composed from.",
        )
    table_names = sorted({t.strip() for t in (body.table_names or []) if t.strip()})
    settings = RetailSettings()
    record = await asyncio.to_thread(
        _with_suggestion_run_store, settings, lambda store: store.create(purpose)
    )
    task = asyncio.create_task(
        _execute_dashboard_suggestion_run(
            record.identifier,
            settings,
            snapshot,
            purpose,
            kpi_identifiers=kpi_identifiers,
            table_names=table_names,
            chart_budget=body.chart_budget,
        )
    )
    _dashboard_suggestion_run_tasks.add(task)
    task.add_done_callback(_dashboard_suggestion_run_tasks.discard)
    return _suggestion_run_out(record)


@router.get(
    "/dashboards/suggestions/{run_id}",
    response_model=wire.DashboardSuggestionRun,
)
async def get_dashboard_suggestion_run(
    run_id: str,
    _: None = Depends(require_scopes("dashboards:read")),
):
    """The run, for polling. Reading applies the lost-run guard, so a row whose executor is
    gone comes back `failed` here rather than `running` forever."""
    settings = RetailSettings()
    record = await asyncio.to_thread(
        _with_suggestion_run_store,
        settings,
        lambda store: store.get(run_id, stale_after=_stale_after(settings)),
    )
    if record is None:
        raise HTTPException(status_code=404, detail=f"No suggestion run {run_id!r}.")
    return _suggestion_run_out(record)


@router.get("/dashboards/{dashboard_id}", response_model=wire.Dashboard)
def get_dashboard(
    dashboard_id: str,
    request: Request,
    store: DashboardStore = Depends(_dashboard_store),
    _: None = Depends(require_scopes("dashboards:read")),
):
    dashboard = store.get(dashboard_id)
    if dashboard is None or not dashboard.visible_to(_owner(request)):
        raise HTTPException(status_code=404, detail=f"No dashboard {dashboard_id!r}.")
    return _dashboard_out(dashboard)


@router.patch("/dashboards/{dashboard_id}", response_model=wire.Dashboard)
def update_dashboard(
    dashboard_id: str,
    body: wire.DashboardWrite,
    request: Request,
    store: DashboardStore = Depends(_dashboard_store),
    _: None = Depends(require_scopes("dashboards:write")),
):
    settings = RetailSettings()
    tiles: Any = UNSET
    if body.tiles is not None:
        tiles = _validated_tiles([_tile_from_write(t) for t in body.tiles], settings)
    # Read before the write, so the recompute below can be scoped to only the query tiles
    # whose `query` actually changed, and any tile dropped from the array can have its cached
    # result cleaned up rather than orphaned. `None` (not fetched) when `tiles` was not sent at
    # all -- nothing about the tile set can have changed, so there is nothing to diff.
    before = store.get(dashboard_id) if body.tiles is not None else None

    try:
        dashboard = store.update(
            dashboard_id,
            _owner(request),
            name=body.name if body.name is not None else UNSET,
            description=body.description if body.description is not None else UNSET,
            tiles=tiles,
            filters=(
                UNSET
                if body.filters is None
                else [_filter_from_wire(f) for f in body.filters]
            ),
            refresh_schedule=(
                RefreshSchedule(body.refresh_schedule.value)
                if body.refresh_schedule is not None
                else UNSET
            ),
            visibility=(
                Visibility(body.visibility.value)
                if body.visibility is not None
                else UNSET
            ),
        )
    except DashboardKpiVanishedError as exc:
        # Same race as `create_dashboard`: a tile's KPI passed `_check_tiles_ready` above but
        # was deleted before this write landed.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if dashboard is None:
        raise HTTPException(status_code=404, detail=f"No dashboard {dashboard_id!r}.")
    if tiles is not UNSET and before is not None:
        _compute_query_tiles(
            dashboard, settings, only=_changed_query_tile_ids(before, tiles)
        )
        removed = {t.id for t in before.tiles} - {t.id for t in dashboard.tiles}
        _remove_orphaned_query_results(dashboard_id, sorted(removed), settings)
    return _dashboard_out(dashboard)


@router.delete("/dashboards/{dashboard_id}", status_code=204)
def delete_dashboard(
    dashboard_id: str,
    request: Request,
    store: DashboardStore = Depends(_dashboard_store),
    _: None = Depends(require_scopes("dashboards:write")),
):
    if not store.delete(dashboard_id, _owner(request)):
        raise HTTPException(status_code=404, detail=f"No dashboard {dashboard_id!r}.")
    try:
        with _open_query_results(RetailSettings()) as results:
            results.delete(dashboard_id)
    except Exception:
        log_warning(f"dashboard {dashboard_id}: cached query results not removed")


def _load_query_results(
    dashboard: Dashboard, settings: RetailSettings
) -> Dict[str, QueryResultRecord]:
    if not any(t.source is TileSource.QUERY for t in dashboard.tiles):
        return {}
    with _open_query_results(settings) as results:
        return results.get_many(dashboard.id)


@router.get("/dashboards/{dashboard_id}/data", response_model=wire.DashboardData)
async def get_dashboard_data(
    dashboard_id: str,
    request: Request,
    store: DashboardStore = Depends(_dashboard_store),
    _: None = Depends(require_scopes("dashboards:read")),
):
    dashboard = store.get(dashboard_id)
    if dashboard is None or not dashboard.visible_to(_owner(request)):
        raise HTTPException(status_code=404, detail=f"No dashboard {dashboard_id!r}.")
    settings = RetailSettings()
    cached = await asyncio.to_thread(_load_query_results, dashboard, settings)
    return await read_dashboard_data(dashboard, query_results=cached)


@router.post("/dashboards/{dashboard_id}/refresh", response_model=wire.DashboardData)
async def refresh_dashboard(
    dashboard_id: str,
    request: Request,
    store: DashboardStore = Depends(_dashboard_store),
    _: None = Depends(require_scopes("dashboards:write")),
):
    """Recompute every ``query`` tile now, then read the dashboard back. Owner only."""
    dashboard = store.get(dashboard_id)
    if dashboard is None or dashboard.owner != _owner(request):
        raise HTTPException(status_code=404, detail=f"No dashboard {dashboard_id!r}.")
    settings = RetailSettings()

    def _refresh() -> Dict[str, QueryResultRecord]:
        with _open_query_results(settings) as results:
            # Concurrent across tiles -- see `_compute_query_tiles`'s comment on the same
            # call. `_refresh` runs off the event loop (via `asyncio.to_thread` below), so
            # `asyncio.run` here is safe.
            asyncio.run(arefresh_query_tiles(dashboard, settings, results))
            return results.get_many(dashboard.id)

    try:
        cached = await asyncio.to_thread(_refresh)
    except SemanticReadError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return await read_dashboard_data(dashboard, query_results=cached)


def _owned_dashboard(store: DashboardStore, dashboard_id: str, owner: str) -> Dashboard:
    dashboard = store.get(dashboard_id)
    if dashboard is None or dashboard.owner != owner:
        raise HTTPException(status_code=404, detail=f"No dashboard {dashboard_id!r}.")
    return dashboard


@router.post(
    "/dashboards/{dashboard_id}/tiles", response_model=wire.Dashboard, status_code=201
)
def add_dashboard_tile(
    dashboard_id: str,
    body: wire.TileWrite,
    request: Request,
    store: DashboardStore = Depends(_dashboard_store),
    _: None = Depends(require_scopes("dashboards:write")),
):
    """Add one tile. Same gates as `POST /dashboards`, for the one tile; every other tile's
    id is untouched."""
    owner = _owner(request)
    settings = RetailSettings()
    current = _owned_dashboard(store, dashboard_id, owner)
    tile = _tile_from_write(body)
    if current.tile(tile.id) is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Tile {tile.id!r} already exists; PATCH it instead.",
        )
    tile = _validated_tiles([tile], settings)[0]
    try:
        dashboard = store.upsert_tile(dashboard_id, owner, tile)
    except DashboardKpiVanishedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if dashboard is None:
        raise HTTPException(status_code=404, detail=f"No dashboard {dashboard_id!r}.")
    _compute_query_tiles(dashboard, settings, only=[tile.id])
    return _dashboard_out(dashboard)


def _patched_tile(current: Tile, patch: wire.TilePatch) -> Tuple[Tile, bool]:
    """``current`` with the patch applied, and whether its query changed. A ``kpi`` tile
    given a ``query`` becomes a ``query`` tile -- the editor's "detach and edit as query"."""
    changes: Dict[str, Any] = {}
    if patch.title is not None:
        changes["title"] = patch.title
    if patch.chart is not None:
        changes["chart"] = ChartKind(patch.chart.value)
    if patch.position is not None:
        changes["position"] = TilePosition(
            x=patch.position.x,
            y=patch.position.y,
            w=patch.position.w,
            h=patch.position.h,
        )
    if patch.grain_value is not None:
        changes["grain_value"] = patch.grain_value
    if patch.description is not None:
        changes["description"] = patch.description
    query_changed = False
    if patch.query is not None:
        new_query = _query_from_wire(patch.query)
        query_changed = (
            current.query is None
            or new_query.sql != current.query.sql
            or new_query.x != current.query.x
            or new_query.y != current.query.y
            or new_query.series_by != current.query.series_by
        )
        changes["query"] = new_query
        changes["source"] = TileSource.QUERY
        changes["kpi_identifier"] = None
    try:
        return dataclasses.replace(current, **changes), query_changed
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.patch(
    "/dashboards/{dashboard_id}/tiles/{tile_id}", response_model=wire.Dashboard
)
def update_dashboard_tile(
    dashboard_id: str,
    tile_id: str,
    body: wire.TilePatch,
    request: Request,
    store: DashboardStore = Depends(_dashboard_store),
    _: None = Depends(require_scopes("dashboards:write")),
):
    """Edit one tile in place; it keeps its id. A changed query is re-checked and
    recomputed; a description-only edit touches nothing else."""
    owner = _owner(request)
    settings = RetailSettings()
    current = _owned_dashboard(store, dashboard_id, owner)
    existing = current.tile(tile_id)
    if existing is None:
        raise HTTPException(
            status_code=404,
            detail=f"No tile {tile_id!r} on dashboard {dashboard_id!r}.",
        )
    tile, query_changed = _patched_tile(existing, body)
    if query_changed:
        tile = _validated_tiles([tile], settings)[0]
    try:
        dashboard = store.upsert_tile(dashboard_id, owner, tile)
    except DashboardKpiVanishedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if dashboard is None:
        raise HTTPException(status_code=404, detail=f"No dashboard {dashboard_id!r}.")
    if query_changed:
        _compute_query_tiles(dashboard, settings, only=[tile.id])
    return _dashboard_out(dashboard)


@router.delete("/dashboards/{dashboard_id}/tiles/{tile_id}", status_code=204)
def delete_dashboard_tile(
    dashboard_id: str,
    tile_id: str,
    request: Request,
    store: DashboardStore = Depends(_dashboard_store),
    _: None = Depends(require_scopes("dashboards:write")),
):
    if store.remove_tile(dashboard_id, _owner(request), tile_id) is None:
        raise HTTPException(
            status_code=404,
            detail=f"No tile {tile_id!r} on dashboard {dashboard_id!r}.",
        )
    try:
        with _open_query_results(RetailSettings()) as results:
            results.delete(dashboard_id, tile_id)
    except Exception:
        log_warning(f"dashboard {dashboard_id}: cached result of {tile_id} not removed")
