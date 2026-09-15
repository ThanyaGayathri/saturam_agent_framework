"""R13's real router: dashboard storage plus gold reads.

Every ``kpi_identifier`` a tile names must be a registered, ``ready`` KPI (K11's
feasibility, the same set difference ``materialise_gold``/``assess_kpi_feasibility`` use) --
checked here, not in ``dashboards/store.py``, because it needs the KPI registry and an open
semantic reader, which are concerns this store deliberately does not carry.

That check (``_check_tiles_ready``) runs before ``store.create``/``store.update`` actually
write the row, over a separate connection, with nothing holding the checked KPIs still in
between -- a KPI deleted in that gap (K11/#240's ``DELETE /kpis`` review, round 2) would
otherwise leave a tile pointing at nothing. ``DashboardStore.create``/``update`` themselves
guard against exactly that, atomically, as part of the same statement that writes the tiles;
this router turns the ``DashboardKpiVanishedError`` that guard raises into a 409, the same way
a lost-update race on ``PATCH /kpis`` becomes one.
"""

from __future__ import annotations

import asyncio
import threading
import traceback
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import (
    Any,
    Callable,
    FrozenSet,
    Iterator,
    List,
    Optional,
    Sequence,
    Tuple,
    TypeVar,
)

from egpt.agent import Agent
from egpt.utils.log import log_error
from fastapi import APIRouter, Depends, HTTPException, Request

from egpt_retail.api import models as wire
from egpt_retail.app.db import pooled_control_plane_connection
from egpt_retail.app.scopes import require_scopes
from egpt_retail.app.semantic import open_semantic_reader
from egpt_retail.app.settings import RetailSettings
from egpt_retail.contracts.dashboards import (
    ChartKind,
    Dashboard,
    DashboardFilter,
    RefreshSchedule,
    Tile,
    TilePosition,
    Visibility,
)
from egpt_retail.contracts.kpi import FeasibilityVerdict, KpiDefinition, assess_registry
from egpt_retail.contracts.semantic import available_tables
from egpt_retail.contracts.vocabulary import VocabularySet
from egpt_retail.dashboards.data import read_dashboard_data
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
    """A tile names a KPI that is not registered or not ready. The router turns this into a
    422 with the message the spec's own example uses."""


# --- store dependency, owner, readiness gate ----------------------------------------------


def _dashboard_store() -> Iterator[DashboardStore]:
    settings = RetailSettings()
    connection = pooled_control_plane_connection(settings)
    try:
        yield DashboardStore(connection)
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
    """Refuses (raising :class:`DashboardValidationError`) unless every tile's KPI is
    registered and ``ready`` -- a blocked or partial KPI has nothing in gold to read. Gathers
    the inputs :func:`tile_readiness_refusal` needs and raises on its verdict."""
    if not tiles:
        return
    wanted = sorted({tile.kpi_identifier for tile in tiles})

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


# --- wire conversions ------------------------------------------------------------------------


def _tile_from_write(write: wire.TileWrite) -> Tile:
    return Tile(
        id=f"tl_{uuid.uuid4().hex[:8]}",
        kpi_identifier=write.kpi_identifier,
        chart=ChartKind(write.chart.value),
        position=TilePosition(
            x=write.position.x,
            y=write.position.y,
            w=write.position.w,
            h=write.position.h,
        ),
        title=write.title or "",
        grain_value=write.grain_value,
    )


def _filter_from_wire(item: wire.DashboardFilter) -> DashboardFilter:
    return DashboardFilter(field=item.field.value, value=item.value)


def _tile_out(tile: Tile) -> wire.Tile:
    return wire.Tile(
        id=tile.id,
        kpi_identifier=tile.kpi_identifier,
        title=tile.title,
        chart=wire.ChartKind(tile.chart.value),
        position=wire.TilePosition(
            x=tile.position.x, y=tile.position.y, w=tile.position.w, h=tile.position.h
        ),
        grain_value=tile.grain_value,
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
# `egpt_retail.skills.dashboard_suggestion`'s module docstring for why this agent selects from
# the ready registry rather than inventing a KPI the way K12 does.


@dataclass(frozen=True)
class _DashboardSuggestionSnapshot:
    """What a dashboard suggestion run needs from the registry, read once up front -- the
    ``ready`` subset only, the same set ``POST /dashboards`` itself requires every tile to
    reference, so a suggestion is exactly as adoptable as a dashboard built by hand."""

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


#: The dashboard suggestion agent, reused across requests until the ready-KPI set changes.
#: CLAUDE.md: "Never create agents in loops -- reuse them for performance." Single-slot, like
#: `kpis.py`'s own cache -- only the current ready set is ever worth serving a stale agent for.
_dash_agent_cache_key: Optional[Tuple[str, ...]] = None
_dash_agent_cache_agent: Optional[Agent] = None
_dash_agent_cache_lock = threading.Lock()


def _cached_dashboard_suggestion_agent(
    settings: RetailSettings, snapshot: _DashboardSuggestionSnapshot
) -> Agent:
    """The suggestion agent for this ready-KPI snapshot, built once and reused. Keyed by the
    sorted set of ready identifiers -- the same set its prompt embeds -- so a KPI registered,
    retired, or moved off `ready` between requests invalidates the cache the next time this is
    called. The returned ``Agent`` is shared across concurrent requests; callers must
    ``deep_copy()`` before ``.run()`` (mirrors `kpis.py`'s `_cached_suggestion_agents`)."""
    global _dash_agent_cache_key, _dash_agent_cache_agent
    key = tuple(sorted(kpi.identifier for kpi in snapshot.ready_kpis))
    with _dash_agent_cache_lock:
        if _dash_agent_cache_key == key and _dash_agent_cache_agent is not None:
            return _dash_agent_cache_agent
        agent = build_dashboard_suggestion_agent(
            settings,
            ready_kpis=snapshot.ready_kpis,
            vocabularies=snapshot.vocabularies,
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
) -> wire.DashboardSuggestion:
    """One suggestion pass: run the agent, assemble its draft against the ready registry.
    ``agent`` is a test-only seam (mirrors `kpis.py`'s own); a real call resolves it from the
    cache and ``deep_copy()``s it first -- the cache shares one ``Agent`` across concurrent
    requests, and ``.run()`` mutates unsynchronized per-agent state that isn't safe to race."""
    resolved_agent = (
        agent
        if agent is not None
        else _cached_dashboard_suggestion_agent(settings, snapshot).deep_copy()
    )
    draft = _drafts_of(resolved_agent.run(purpose), dsg.DashboardSuggestionDraft)
    report = dsg.assemble(
        draft, ready_kpis=snapshot.ready_kpis, vocabularies=snapshot.vocabularies
    )
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
    return wire.DashboardSuggestionRun(
        id=record.identifier,
        status=wire.DashboardSuggestionRunStatus(record.status.value),
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
    if isinstance(exc, ValueError):
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
) -> None:
    """The executor behind `POST /dashboards/suggestions`: `queued` -> `running` -> one bounded
    suggestion pass -> `succeeded`/`failed`. Each row write opens and releases its own
    connection; the model call in between holds none."""
    try:
        await asyncio.to_thread(
            _with_suggestion_run_store,
            settings,
            lambda store: store.mark_running(run_id),
        )
        try:
            result = await _run_bounded(
                lambda: _build_dashboard_suggestion(
                    settings, snapshot=snapshot, purpose=purpose
                ),
                settings.retail_suggestion_timeout_seconds,
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


@router.post("/dashboards", response_model=wire.Dashboard, status_code=201)
def create_dashboard(
    body: wire.DashboardWrite,
    request: Request,
    store: DashboardStore = Depends(_dashboard_store),
    _: None = Depends(require_scopes("dashboards:write")),
):
    tiles: List[Tile] = [_tile_from_write(t) for t in (body.tiles or [])]
    settings = RetailSettings()
    try:
        _check_tiles_ready(tiles, settings)
    except DashboardValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

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
    return _dashboard_out(dashboard)


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
    tiles = UNSET if body.tiles is None else [_tile_from_write(t) for t in body.tiles]
    if tiles is not UNSET:
        try:
            _check_tiles_ready(tiles, settings)
        except DashboardValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

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
    return await read_dashboard_data(dashboard)


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
    settings = RetailSettings()
    record = await asyncio.to_thread(
        _with_suggestion_run_store, settings, lambda store: store.create(purpose)
    )
    task = asyncio.create_task(
        _execute_dashboard_suggestion_run(
            record.identifier, settings, snapshot, purpose
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
