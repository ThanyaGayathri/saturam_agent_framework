"""F2's real router (issue #235, part of #222's frontend/backend wiring split):
``GET /semantic/proposals``, ``POST /semantic/proposals/{id}/accept``,
``POST /semantic/proposals/{id}/reject``, ``POST /semantic/proposals/accept``.

K7's ``ProposalStore`` (``semantic/proposals.py``) is already fully built, tested, and in use
by ``egpt-retail suggest`` (``entrypoints/suggest.py``) -- this router is the review screen's
read and the two decisions a person can make, over HTTP. Of the read-only ``/semantic/*``
operations the contract also defines, ``listSemanticTables`` and ``getSemanticTable`` are
mounted here too (contract 1.2.0): the dashboard chart editor shows a person the same table
and column descriptions the compose agent was shown, and that pane reads them over HTTP. The
rest (``getSemanticSnapshot``, concepts, join paths, harvests) stay unmounted.

**Accept doubles as adopt, for KPI proposals only.** Accepting a ``kind: kpi`` proposal calls
``KpiRegistryStore.adopt_accepted`` in the same request -- confirmed idempotent
(``kpi/store.py:669``, filters ``applied_at is None``) and safe to call as a sweep rather than
scoped to just the one id just decided, which means it also self-heals any KPI proposal
accepted before this router existed. ``kind: use_case`` and the five enrichment kinds have no
consumer wired to an HTTP accept today (``GRAPH_KINDS`` excludes ``use_case``, and nothing
calls ``ProposalStore.apply_accepted`` from a route) -- accepting one only records the
decision. The response's ``applied_at`` says which happened; nothing here pretends otherwise.
"""

from __future__ import annotations

from typing import Iterable, Iterator, List, Optional, Sequence

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from egpt_retail.api import models as wire
from egpt_retail.app.db import pooled_control_plane_connection
from egpt_retail.app.scopes import caller_scopes, require_scopes
from egpt_retail.app.semantic import open_semantic_reader
from egpt_retail.app.settings import RetailSettings
from egpt_retail.configure.store import DataSourceStore
from egpt_retail.contracts.data_source import DataSource
from egpt_retail.contracts.semantic import Column, SemanticReadError, Table, TableRef
from egpt_retail.kpi.store import KpiRegistryStore
from egpt_retail.semantic.proposals import (
    Proposal,
    ProposalKind,
    ProposalStore,
    Refusal,
    ReviewState,
)

__all__ = ["router"]

router = APIRouter()


# --- store dependencies, reviewer identity -------------------------------------------------


def _proposal_store() -> Iterator[ProposalStore]:
    settings = RetailSettings()
    connection = pooled_control_plane_connection(settings)
    try:
        yield ProposalStore(connection)
    finally:
        connection.close()


def _kpi_store() -> Iterator[KpiRegistryStore]:
    settings = RetailSettings()
    connection = pooled_control_plane_connection(settings)
    try:
        yield KpiRegistryStore(connection)
    finally:
        connection.close()


def _reviewer(request: Request) -> str:
    """The caller's identity, attributed on every accept/reject. Mirrors ``dashboards.py``'s
    ``_owner`` exactly: ``"local"`` with authorization off (this deployment is single-tenant,
    D3), the real subject claim with it on, 401 for a token with none -- ``ProposalStore``
    itself refuses a blank reviewer (D10: an unattributed decision is indistinguishable from
    no review at all), so this is what supplies one rather than a route-level default.
    """
    if not getattr(request.state, "authorization_enabled", False):
        return "local"
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(
            status_code=401,
            detail=(
                "This token carries no subject claim; a review decision must be attributed "
                "and cannot be made by an anonymous one."
            ),
        )
    return user_id


# --- wire mapping ----------------------------------------------------------------------------


def _proposal_out(proposal: Proposal) -> wire.Proposal:
    return wire.Proposal(
        id=proposal.identifier,
        kind=wire.ProposalKind(proposal.kind.value),
        target=proposal.target,
        payload=dict(proposal.payload),
        proposer=proposal.proposer,
        state=wire.ProposalDecision(proposal.state.value),
        reviewer=proposal.reviewer,
        decided_at=proposal.decided_at,
        decision_note=proposal.decision_note,
        applied_at=proposal.applied_at,
        apply_error=proposal.apply_error,
        created_at=proposal.created_at,
    )


def _refusal_out(refusal: Refusal) -> wire.ProposalRefusal:
    return wire.ProposalRefusal(
        id=refusal.identifier,
        reason=refusal.reason,
        current_state=(
            wire.ProposalDecision(refusal.current_state.value)
            if refusal.current_state is not None
            else None
        ),
        decided_by=refusal.decided_by,
    )


def _single_refusal_detail(refusal: Refusal) -> str:
    detail = f"{refusal.identifier}: {refusal.reason}"
    if refusal.decided_by:
        detail += f" by {refusal.decided_by}"
    return detail


def _dedupe(proposal_batches: Iterable[Sequence[Proposal]]) -> List[Proposal]:
    """Flatten several ``store.list`` reads into one list, keeping the first occurrence of
    each id -- needed because ``list_semantic_proposals`` reads once per requested kind."""
    proposals: List[Proposal] = []
    seen = set()
    for batch in proposal_batches:
        for proposal in batch:
            if proposal.identifier in seen:
                continue
            seen.add(proposal.identifier)
            proposals.append(proposal)
    return proposals


def _counts(proposals: Sequence[Proposal]) -> wire.ProposalCounts:
    return wire.ProposalCounts(
        pending=sum(1 for p in proposals if p.state is ReviewState.PENDING),
        accepted=sum(1 for p in proposals if p.state is ReviewState.ACCEPTED),
        rejected=sum(1 for p in proposals if p.state is ReviewState.REJECTED),
    )


def _refetch(store: ProposalStore, proposal_id: str) -> Proposal:
    """The just-decided proposal, re-read so the response reflects ``adopt_accepted``'s
    stamp. ``store.get`` is typed ``Optional`` for a caller that hasn't just decided this id
    -- this one has, immediately before calling here, so a ``None`` would mean the row
    vanished between the two reads, which nothing in this store ever does."""
    proposal = store.get(proposal_id)
    if proposal is None:  # pragma: no cover -- see docstring
        raise HTTPException(
            status_code=500,
            detail=f"{proposal_id} was decided but could not be re-read.",
        )
    return proposal


def _refetch_many(store: ProposalStore, proposal_ids: Sequence[str]) -> List[Proposal]:
    """The just-decided proposals, re-read in one round trip via ``get_many`` rather than one
    ``_refetch`` per id, then reordered to match ``proposal_ids`` since a batched
    ``WHERE id = ANY(%s)`` makes no ordering guarantee of its own."""
    by_id = {p.identifier: p for p in store.get_many(proposal_ids)}
    missing = [i for i in proposal_ids if i not in by_id]
    if missing:  # pragma: no cover -- same invariant as _refetch's docstring
        raise HTTPException(
            status_code=500,
            detail=f"{missing[0]} was decided but could not be re-read.",
        )
    return [by_id[i] for i in proposal_ids]


def _adopt_if_kpi(
    proposal_ids: Sequence[str],
    store: ProposalStore,
    kpi_store: KpiRegistryStore,
) -> None:
    """After an accept, sweep KPI adoption if any of the ids just decided were KPI proposals.

    A sweep over every accepted-and-unapplied KPI proposal, not scoped to ``proposal_ids`` --
    ``adopt_accepted`` is idempotent and this is cheap at review-queue sizes, so the extra
    reach is a feature (self-healing) rather than a risk. Skipped entirely when none of the
    ids decided this call were KPI proposals, so a reject or an enrichment/use-case accept
    never pays for a registry round trip it has no stake in.

    By the time this runs, ``store.accept(...)`` has already committed the decision -- on a
    connection separate from ``kpi_store``'s, so the two writes are not one transaction, a
    pre-existing gap in ``adopt_accepted`` this router now exercises on every accept rather
    than an offline batch job (a crash between them just means the next sweep re-adopts, since
    ``save`` is an idempotent upsert).

    ``adopt_accepted`` itself now records a failed proposal's own ``apply_error`` and moves on
    to the next one -- both a bad payload (validation) and a failed ``mark_applied``/
    ``record_apply_error`` write (a DB-level blip) are attributed to the one row that caused
    them, not to whichever proposal this request happened to decide. What is caught here is
    only whatever ``adopt_accepted`` cannot attribute to any single row at all -- its own
    initial ``proposals.list(...)`` read failing, or its own per-proposal recovery failing in
    turn -- and it is still recorded against this request's own KPI proposals as the best
    available attribution, not a precise one; this should be rare enough in practice that it
    is a fallback rather than the primary error path.
    """
    decided = store.get_many(proposal_ids)
    if not any(p.kind is ProposalKind.KPI for p in decided):
        return
    try:
        kpi_store.adopt_accepted(store)
    except Exception as error:  # noqa: BLE001 -- best-effort attribution, not swallowed
        message = f"{type(error).__name__}: {error}"
        for proposal in decided:
            if proposal.kind is ProposalKind.KPI:
                store.record_apply_error(proposal.identifier, message)


# --- routes ------------------------------------------------------------------------------


@router.get("/semantic/proposals", response_model=wire.ProposalList)
def list_semantic_proposals(
    state: Optional[wire.ProposalDecision] = None,
    kind: Optional[List[wire.ProposalKind]] = None,
    store: ProposalStore = Depends(_proposal_store),
    _: None = Depends(require_scopes("semantic:read_proposed")),
) -> wire.ProposalList:
    review_state = ReviewState(state.value) if state is not None else None
    kinds = [ProposalKind(k.value) for k in kind] if kind else [None]
    proposals = _dedupe(
        store.list(state=review_state, kind=one_kind) for one_kind in kinds
    )
    # Counts are totals "by state" (see ProposalCounts' own schema description) so the review
    # screen's badge doesn't need a second call -- which only ``proposals`` above already is
    # when unfiltered by state. Under a state filter, ``proposals`` is that one state's rows,
    # so counting from it would always report the other two states as zero; an unfiltered read
    # is needed to get real totals.
    if review_state is None:
        counts_source = proposals
    else:
        counts_source = _dedupe(store.list(kind=one_kind) for one_kind in kinds)
    return wire.ProposalList(
        proposals=[_proposal_out(p) for p in proposals],
        counts=_counts(counts_source),
    )


@router.post("/semantic/proposals/{proposal_id}/accept", response_model=wire.Proposal)
def accept_semantic_proposal(
    proposal_id: str,
    request: Request,
    body: Optional[wire.ProposalDecisionRequest] = None,
    store: ProposalStore = Depends(_proposal_store),
    kpi_store: KpiRegistryStore = Depends(_kpi_store),
    _: None = Depends(require_scopes("semantic:review")),
) -> wire.Proposal:
    outcome = store.accept(
        [proposal_id],
        reviewer=_reviewer(request),
        note=(body.note if body else "") or "",
    )
    if outcome.refused:
        raise HTTPException(
            status_code=409, detail=_single_refusal_detail(outcome.refused[0])
        )
    _adopt_if_kpi(outcome.decided, store, kpi_store)
    return _proposal_out(_refetch(store, proposal_id))


@router.post("/semantic/proposals/{proposal_id}/reject", response_model=wire.Proposal)
def reject_semantic_proposal(
    proposal_id: str,
    request: Request,
    body: Optional[wire.ProposalDecisionRequest] = None,
    store: ProposalStore = Depends(_proposal_store),
    _: None = Depends(require_scopes("semantic:review")),
) -> wire.Proposal:
    outcome = store.reject(
        [proposal_id],
        reviewer=_reviewer(request),
        note=(body.note if body else "") or "",
    )
    if outcome.refused:
        raise HTTPException(
            status_code=409, detail=_single_refusal_detail(outcome.refused[0])
        )
    return _proposal_out(_refetch(store, proposal_id))


@router.post("/semantic/proposals/accept", response_model=wire.ProposalList)
def accept_semantic_proposals_bulk(
    body: wire.BulkProposalDecisionRequest,
    request: Request,
    store: ProposalStore = Depends(_proposal_store),
    kpi_store: KpiRegistryStore = Depends(_kpi_store),
    _: None = Depends(require_scopes("semantic:review")),
) -> wire.ProposalList:
    outcome = store.accept(body.ids, reviewer=_reviewer(request), note=body.note or "")
    _adopt_if_kpi(outcome.decided, store, kpi_store)
    proposals = _refetch_many(store, outcome.decided)
    return wire.ProposalList(
        proposals=[_proposal_out(p) for p in proposals],
        counts=_counts(proposals),
        refused=[_refusal_out(r) for r in outcome.refused],
    )


# --- the read-only table surface (1.2.0) ---------------------------------------------------


def _include_proposed_allowed(request: Request, include_proposed: bool) -> None:
    """``include_proposed=true`` needs ``semantic:read_proposed`` (CHANGELOG 1.0.0: over HTTP
    it is an attacker-controlled query parameter returning unreviewed rows). 403, not a
    silently filtered 200."""
    if not include_proposed or not getattr(
        request.state, "authorization_enabled", False
    ):
        return
    scopes, admin_scope = caller_scopes(request)
    if "semantic:read_proposed" not in scopes and not admin_scope:
        raise HTTPException(
            status_code=403,
            detail="Insufficient permissions. Required scopes: semantic:read_proposed.",
        )


def _table_ref_out(ref: TableRef) -> wire.TableRef:
    return wire.TableRef(name=ref.name, schema=ref.schema, row_count=ref.row_count)


def _column_out(column: Column) -> wire.Column:
    return wire.Column(
        name=column.name,
        data_type=column.data_type,
        nullable=column.nullable,
        is_key=column.is_key,
        is_personal_data=column.is_personal_data,
        description=column.description,
        # A personal-data column's real observed values never leave the semantic layer over
        # this read-only surface (round-1 review, finding #2): the compose agent's own prompt
        # renderer omits such a column entirely (`skills/dashboard_suggestion.py`'s
        # `_render_tables`, `if column.is_personal_data: continue`), but this endpoint's own
        # docstring promises the chart editor "the same table and column descriptions the
        # compose agent was shown" -- and the editor's schema pane wants the column to still
        # appear, with `is_personal_data=True`, so it can render its "blocked" pill (unlike the
        # agent, which never sees it at all). Keeping the column but stripping only the field
        # that is actual data rather than a description of it satisfies both: nothing sees a
        # real value, and the viewer still sees the column exists and why it's withheld.
        sampled_values=[] if column.is_personal_data else list(column.sampled_values),
        status=wire.ProposalStatus(column.status.value),
        concept=column.concept,
        meaning=column.meaning,
        joinable=column.joinable,
    )


def _table_out(table: Table) -> wire.Table:
    return wire.Table(
        ref=_table_ref_out(table.ref),
        columns=[_column_out(c) for c in table.columns],
        description=table.description,
        concepts=list(table.concepts),
        status=wire.ProposalStatus(table.status.value),
    )


def _registered_source(settings: RetailSettings, source_id: str) -> DataSource:
    """The registered data source ``source_id`` names, or a 404 -- the one place a source-scoped
    read checks that the source exists, so the listing and the table detail cannot disagree
    about what counts as "no such source"."""
    connection = pooled_control_plane_connection(settings)
    try:
        source = DataSourceStore(connection).get(source_id)
    finally:
        connection.close()
    if source is None:
        raise HTTPException(status_code=404, detail=f"No such source: {source_id!r}.")
    return source


def _source_listing(
    settings: RetailSettings, source_id: str, include_proposed: bool
) -> wire.TableList:
    """``GET /semantic/tables?source_id=`` (#307): one registered source's own tables.

    404 for an id no data source was registered under -- an unknown id silently returning an
    empty list would look identical to a source that is registered and simply not harvested
    yet, and those are different answers. ``snapshot`` is that source's: the graph's one
    ``SemanticSnapshot`` node is the operator's pointer and a source's harvest never writes it,
    so the version comes off the tables it tagged and the timestamp and name off the
    registration row.
    """
    source = _registered_source(settings, source_id)
    try:
        with open_semantic_reader(settings, source_id=source_id) as reader:
            refs = reader.list_tables(include_proposed=include_proposed)
            version = reader.source_snapshot_version()
    except (SemanticReadError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    # ``version`` comes off the tables the source holds *now*, ``harvested_at`` off the
    # registration row, so a harvested source whose tables were since all pruned has a real
    # timestamp and no version to read. Both fields answer "has this source been harvested",
    # so they must not disagree: "unharvested" only ever sits beside "never".
    harvested_at = source.last_harvest_at
    return wire.TableList(
        tables=[_table_ref_out(r) for r in refs],
        snapshot=wire.Snapshot(
            version=version or ("empty" if harvested_at else "unharvested"),
            harvested_at=harvested_at.isoformat() if harvested_at else "never",
            source=source.name,
        ),
    )


@router.get("/semantic/tables", response_model=wire.TableList)
def list_semantic_tables(
    request: Request,
    include_proposed: bool = Query(default=False),
    source_id: Optional[str] = Query(default=None),
    _: None = Depends(require_scopes("semantic:read")),
):
    _include_proposed_allowed(request, include_proposed)
    settings = RetailSettings()
    # ``is not None``, not truthiness: an omitted parameter is the operator's listing, but an
    # explicit ``source_id=`` names no registered source and is a 404 like any other unknown
    # id. It would otherwise fall into the operator branch only because the operator's own
    # ``source_id`` happens to be ``""`` too.
    if source_id is not None:
        return _source_listing(settings, source_id, include_proposed)
    try:
        with open_semantic_reader(settings) as reader:
            refs = reader.list_tables(include_proposed=include_proposed)
            snapshot = reader.snapshot()
    except (SemanticReadError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return wire.TableList(
        tables=[_table_ref_out(r) for r in refs],
        snapshot=wire.Snapshot(
            version=snapshot.version,
            harvested_at=snapshot.harvested_at,
            source=snapshot.source,
        ),
    )


@router.get("/semantic/tables/{table_name}", response_model=wire.Table)
def get_semantic_table(
    table_name: str,
    request: Request,
    include_proposed: bool = Query(default=False),
    source_id: Optional[str] = Query(default=None),
    _: None = Depends(require_scopes("semantic:read")),
):
    """One table with its columns. With ``source_id`` (contract 1.2.9) the table is looked up
    among that registered source's own harvest -- the scoping ``GET /semantic/tables`` already
    applies -- so a table the listing shows under a source can be read back under it. Without
    it, the deployment's own warehouse, as before: a source's tables never answer there."""
    _include_proposed_allowed(request, include_proposed)
    settings = RetailSettings()
    # ``is not None``, not truthiness, for the same reason as the listing: an explicit empty
    # ``source_id=`` names no registered source and is a 404, not the operator's warehouse.
    reader_scope = {}
    if source_id is not None:
        _registered_source(settings, source_id)
        reader_scope = {"source_id": source_id}
    try:
        with open_semantic_reader(settings, **reader_scope) as reader:
            table = reader.get_table(table_name, include_proposed=include_proposed)
    except (SemanticReadError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if table is None:
        raise HTTPException(
            status_code=404,
            detail=f"No table named '{table_name}' in the semantic layer.",
        )
    return _table_out(table)
