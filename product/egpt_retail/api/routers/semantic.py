"""F2's real router (issue #235, part of #222's frontend/backend wiring split):
``GET /semantic/proposals``, ``POST /semantic/proposals/{id}/accept``,
``POST /semantic/proposals/{id}/reject``, ``POST /semantic/proposals/accept``.

K7's ``ProposalStore`` (``semantic/proposals.py``) is already fully built, tested, and in use
by ``egpt-retail suggest`` (``entrypoints/suggest.py``) -- this router is the review screen's
read and the two decisions a person can make, over HTTP, nothing more. The seven read-only
``/semantic/*`` operations the contract also defines (``getSemanticSnapshot``,
``listSemanticTables``, ...) are a materially larger, separate effort with no connection to
this review-screen scope and stay unmounted here.

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

from typing import Iterator, List, Optional, Sequence

from fastapi import APIRouter, Depends, HTTPException, Request

from egpt_retail.api import models as wire
from egpt_retail.app.db import pooled_control_plane_connection
from egpt_retail.app.scopes import require_scopes
from egpt_retail.app.settings import RetailSettings
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
    """
    decided = [store.get(i) for i in proposal_ids]
    if any(p is not None and p.kind is ProposalKind.KPI for p in decided):
        kpi_store.adopt_accepted(store)


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
    proposals: List[Proposal] = []
    seen = set()
    for one_kind in kinds:
        for proposal in store.list(state=review_state, kind=one_kind):
            if proposal.identifier in seen:
                continue
            seen.add(proposal.identifier)
            proposals.append(proposal)
    return wire.ProposalList(
        proposals=[_proposal_out(p) for p in proposals],
        counts=_counts(proposals),
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
    proposals = [_refetch(store, i) for i in outcome.decided]
    return wire.ProposalList(
        proposals=[_proposal_out(p) for p in proposals],
        counts=_counts(proposals),
        refused=[_refusal_out(r) for r in outcome.refused],
    )
