"""Unit tests for #235/F2's DB-free half: the route functions, the wire<->domain mapping, the
reviewer-identity gate, and the accept-adopts-a-KPI wiring -- everything `api/routers/semantic.py`
does that needs no Postgres. The store's own behaviour (`_decide`'s one-way state machine,
`adopt_accepted`'s idempotency) is exercised by `semantic/proposals.py`'s own test suite and by
`test_semantic_live.py`'s HTTP round trip; this file only proves the router calls them right.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

import pytest
from fastapi import HTTPException

from egpt_retail.api.routers import semantic as semantic_module
from egpt_retail.api.routers.semantic import (
    accept_semantic_proposal,
    accept_semantic_proposals_bulk,
    list_semantic_proposals,
    reject_semantic_proposal,
)
from egpt_retail.semantic.proposals import (
    Proposal,
    ProposalKind,
    Refusal,
    ReviewOutcome,
    ReviewState,
)


def _kpi_proposal(identifier: str = "prop_kpi_1", **overrides) -> Proposal:
    defaults = dict(
        kind=ProposalKind.KPI,
        target="rfq__quote_to_deal_rate",
        payload={
            "identifier": "rfq__quote_to_deal_rate",
            "name": "Quote to deal rate",
            "sql": "SELECT 1",
            "grain": "brand",
            "business_area": "sales",
            "source_tables": ["rfq_quote"],
        },
        proposer="kpi_suggestion_skill@1.0.0",
        identifier=identifier,
        created_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return Proposal(**defaults)


def _use_case_proposal(identifier: str = "prop_uc_1", **overrides) -> Proposal:
    defaults = dict(
        kind=ProposalKind.USE_CASE,
        target="which-brands-are-slowest",
        payload={"question": "Which brands are slowest?"},
        proposer="use_case_suggestion_skill@1.0.0",
        identifier=identifier,
        created_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return Proposal(**defaults)


class _FakeState:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _FakeRequest:
    def __init__(self, **state_kwargs):
        self.state = _FakeState(**state_kwargs)


class _FakeProposalStore:
    """An in-memory stand-in with the same public shape as `ProposalStore`, enough for the
    router to exercise its own logic without a database."""

    def __init__(self, proposals: Sequence[Proposal] = ()):
        self._by_id: Dict[str, Proposal] = {p.identifier: p for p in proposals}
        self.get_many_calls = 0
        self.apply_errors: Dict[str, str] = {}

    def get(self, identifier: str) -> Optional[Proposal]:
        return self._by_id.get(identifier)

    def get_many(self, identifiers: Sequence[str]) -> Tuple[Proposal, ...]:
        self.get_many_calls += 1
        return tuple(p for i in identifiers if (p := self._by_id.get(i)) is not None)

    def record_apply_error(self, identifier: str, message: str) -> None:
        self.apply_errors[identifier] = message
        self._by_id[identifier] = replace(self._by_id[identifier], apply_error=message)

    def list(
        self,
        *,
        state: Optional[ReviewState] = None,
        kind: Optional[ProposalKind] = None,
    ) -> Tuple[Proposal, ...]:
        rows = self._by_id.values()
        if state is not None:
            rows = [p for p in rows if p.state is state]
        if kind is not None:
            rows = [p for p in rows if p.kind is kind]
        return tuple(rows)

    def _decide(self, identifiers, state, reviewer, note) -> ReviewOutcome:
        if not reviewer.strip():
            raise ValueError("reviewer must not be blank")
        decided: List[str] = []
        refused: List[Refusal] = []
        for identifier in dict.fromkeys(identifiers):
            existing = self._by_id.get(identifier)
            if existing is None:
                refused.append(Refusal(identifier, "no such proposal"))
                continue
            if existing.state is not ReviewState.PENDING:
                refused.append(
                    Refusal(
                        identifier,
                        f"already {existing.state.value}",
                        current_state=existing.state,
                        decided_by=existing.reviewer,
                    )
                )
                continue
            updated = Proposal(
                kind=existing.kind,
                target=existing.target,
                payload=existing.payload,
                proposer=existing.proposer,
                state=state,
                reviewer=reviewer,
                decided_at=datetime.now(timezone.utc),
                decision_note=note,
                applied_at=existing.applied_at,
                identifier=existing.identifier,
                created_at=existing.created_at,
            )
            self._by_id[identifier] = updated
            decided.append(identifier)
        return ReviewOutcome(decided=tuple(decided), refused=tuple(refused))

    def accept(self, identifiers, reviewer, note="") -> ReviewOutcome:
        return self._decide(identifiers, ReviewState.ACCEPTED, reviewer, note)

    def reject(self, identifiers, reviewer, note="") -> ReviewOutcome:
        return self._decide(identifiers, ReviewState.REJECTED, reviewer, note)


class _FakeKpiRegistryStore:
    """Records whether `adopt_accepted` was called and stamps `applied_at` on every accepted,
    unapplied KPI proposal in the store it is given -- the one real effect this router relies
    on, without a real registry table behind it."""

    def __init__(self, raises: Optional[Exception] = None):
        self.adopt_calls = 0
        self._raises = raises

    def adopt_accepted(self, proposals: _FakeProposalStore) -> Tuple[str, ...]:
        self.adopt_calls += 1
        if self._raises is not None:
            raise self._raises
        adopted = []
        for proposal in proposals.list(
            state=ReviewState.ACCEPTED, kind=ProposalKind.KPI
        ):
            if proposal.applied_at is not None:
                continue
            stamped = Proposal(
                kind=proposal.kind,
                target=proposal.target,
                payload=proposal.payload,
                proposer=proposal.proposer,
                state=proposal.state,
                reviewer=proposal.reviewer,
                decided_at=proposal.decided_at,
                decision_note=proposal.decision_note,
                applied_at=datetime.now(timezone.utc),
                identifier=proposal.identifier,
                created_at=proposal.created_at,
            )
            proposals._by_id[proposal.identifier] = stamped
            adopted.append(proposal.identifier)
        return tuple(adopted)


# ---------------------------------------------------------------------------
# _reviewer
# ---------------------------------------------------------------------------


def test_reviewer_is_local_when_authorization_is_off():
    assert (
        semantic_module._reviewer(_FakeRequest(authorization_enabled=False)) == "local"
    )


def test_reviewer_is_the_subject_claim_when_authorization_is_on():
    request = _FakeRequest(authorization_enabled=True, user_id="analyst@example.test")
    assert semantic_module._reviewer(request) == "analyst@example.test"


def test_reviewer_401s_on_a_token_with_no_subject_claim():
    request = _FakeRequest(authorization_enabled=True, user_id=None)
    with pytest.raises(HTTPException) as excinfo:
        semantic_module._reviewer(request)
    assert excinfo.value.status_code == 401


# ---------------------------------------------------------------------------
# list_semantic_proposals
# ---------------------------------------------------------------------------


def test_list_returns_every_kind_by_default():
    store = _FakeProposalStore([_kpi_proposal(), _use_case_proposal()])
    result = list_semantic_proposals(store=store)
    assert {p.id for p in result.proposals} == {"prop_kpi_1", "prop_uc_1"}
    assert result.counts.pending == 2


def test_list_filters_to_the_requested_kinds_and_dedupes():
    store = _FakeProposalStore([_kpi_proposal(), _use_case_proposal()])
    result = list_semantic_proposals(
        kind=[
            semantic_module.wire.ProposalKind.kpi,
            semantic_module.wire.ProposalKind.kpi,
        ],
        store=store,
    )
    assert [p.id for p in result.proposals] == ["prop_kpi_1"]


def test_list_filters_by_state():
    accepted = _kpi_proposal(identifier="p1", state=ReviewState.ACCEPTED)
    pending = _use_case_proposal(identifier="p2")
    store = _FakeProposalStore([accepted, pending])
    result = list_semantic_proposals(
        state=semantic_module.wire.ProposalDecision.accepted, store=store
    )
    assert [p.id for p in result.proposals] == ["p1"]
    # counts are totals across every state, not scoped to the `state` filter that
    # narrowed `proposals` above.
    assert result.counts.accepted == 1
    assert result.counts.pending == 1


def test_list_counts_are_totals_not_the_state_filtered_subset():
    accepted = _kpi_proposal(identifier="p1", state=ReviewState.ACCEPTED)
    pending = _use_case_proposal(identifier="p2")
    rejected = _kpi_proposal(identifier="p3", state=ReviewState.REJECTED)
    store = _FakeProposalStore([accepted, pending, rejected])

    result = list_semantic_proposals(
        state=semantic_module.wire.ProposalDecision.pending, store=store
    )

    assert [p.id for p in result.proposals] == ["p2"]
    assert result.counts.pending == 1
    assert result.counts.accepted == 1
    assert result.counts.rejected == 1


def test_list_maps_payload_and_state_faithfully():
    store = _FakeProposalStore([_kpi_proposal()])
    result = list_semantic_proposals(store=store)
    wire_proposal = result.proposals[0]
    assert wire_proposal.kind.value == "kpi"
    assert wire_proposal.target == "rfq__quote_to_deal_rate"
    assert wire_proposal.payload["name"] == "Quote to deal rate"
    assert wire_proposal.state.value == "pending"
    assert wire_proposal.applied_at is None


# ---------------------------------------------------------------------------
# accept_semantic_proposal -- the auto-adopt wiring is the whole point of F2's design decision
# ---------------------------------------------------------------------------


def test_accepting_a_kpi_proposal_adopts_it_in_the_same_call():
    store = _FakeProposalStore([_kpi_proposal()])
    kpi_store = _FakeKpiRegistryStore()
    request = _FakeRequest(authorization_enabled=False)

    result = accept_semantic_proposal(
        "prop_kpi_1", request, store=store, kpi_store=kpi_store
    )

    assert result.state.value == "accepted"
    assert result.applied_at is not None
    assert kpi_store.adopt_calls == 1


def test_accepting_a_use_case_proposal_does_not_touch_the_kpi_store():
    store = _FakeProposalStore([_use_case_proposal()])
    kpi_store = _FakeKpiRegistryStore()
    request = _FakeRequest(authorization_enabled=False)

    result = accept_semantic_proposal(
        "prop_uc_1", request, store=store, kpi_store=kpi_store
    )

    assert result.state.value == "accepted"
    assert result.applied_at is None
    assert kpi_store.adopt_calls == 0


def test_accepting_an_already_decided_proposal_409s():
    store = _FakeProposalStore(
        [_kpi_proposal(state=ReviewState.REJECTED, reviewer="dana")]
    )
    kpi_store = _FakeKpiRegistryStore()
    request = _FakeRequest(authorization_enabled=False)

    with pytest.raises(HTTPException) as excinfo:
        accept_semantic_proposal(
            "prop_kpi_1", request, store=store, kpi_store=kpi_store
        )
    assert excinfo.value.status_code == 409
    assert "already rejected" in excinfo.value.detail
    assert "dana" in excinfo.value.detail
    assert kpi_store.adopt_calls == 0


def test_accepting_a_kpi_proposal_survives_a_failed_adopt_sweep():
    """A raise from `adopt_accepted` must not 500 a decision `store.accept` already
    committed -- it's caught and recorded as this proposal's `apply_error` instead."""
    store = _FakeProposalStore([_kpi_proposal()])
    kpi_store = _FakeKpiRegistryStore(
        raises=ValueError("business_area no longer valid")
    )
    request = _FakeRequest(authorization_enabled=False)

    result = accept_semantic_proposal(
        "prop_kpi_1", request, store=store, kpi_store=kpi_store
    )

    assert result.state.value == "accepted"
    assert result.applied_at is None
    assert result.apply_error is not None
    assert "business_area no longer valid" in result.apply_error


def test_accept_attributes_the_reviewer_from_the_request():
    store = _FakeProposalStore([_kpi_proposal()])
    kpi_store = _FakeKpiRegistryStore()
    request = _FakeRequest(authorization_enabled=True, user_id="analyst@example.test")

    result = accept_semantic_proposal(
        "prop_kpi_1", request, store=store, kpi_store=kpi_store
    )
    assert result.reviewer == "analyst@example.test"


# ---------------------------------------------------------------------------
# reject_semantic_proposal
# ---------------------------------------------------------------------------


def test_rejecting_a_pending_proposal_records_the_decision():
    store = _FakeProposalStore([_use_case_proposal()])
    request = _FakeRequest(authorization_enabled=False)
    result = reject_semantic_proposal("prop_uc_1", request, store=store)
    assert result.state.value == "rejected"


def test_rejecting_an_already_decided_proposal_409s():
    store = _FakeProposalStore(
        [_kpi_proposal(state=ReviewState.ACCEPTED, reviewer="dana")]
    )
    request = _FakeRequest(authorization_enabled=False)
    with pytest.raises(HTTPException) as excinfo:
        reject_semantic_proposal("prop_kpi_1", request, store=store)
    assert excinfo.value.status_code == 409


# ---------------------------------------------------------------------------
# accept_semantic_proposals_bulk -- not atomic: a refusal is reported, not raised
# ---------------------------------------------------------------------------


def test_bulk_accept_decides_every_pending_id_and_adopts_kpis():
    store = _FakeProposalStore([_kpi_proposal(), _use_case_proposal()])
    kpi_store = _FakeKpiRegistryStore()
    request = _FakeRequest(authorization_enabled=False)
    body = semantic_module.wire.BulkProposalDecisionRequest(
        ids=["prop_kpi_1", "prop_uc_1"]
    )

    result = accept_semantic_proposals_bulk(
        body, request, store=store, kpi_store=kpi_store
    )

    assert {p.id for p in result.proposals} == {"prop_kpi_1", "prop_uc_1"}
    assert result.refused == []
    kpi_result = next(p for p in result.proposals if p.id == "prop_kpi_1")
    use_case_result = next(p for p in result.proposals if p.id == "prop_uc_1")
    assert kpi_result.applied_at is not None
    assert use_case_result.applied_at is None
    assert kpi_store.adopt_calls == 1


def test_bulk_accept_is_not_atomic_a_refused_id_does_not_block_the_rest():
    store = _FakeProposalStore(
        [
            _kpi_proposal(),
            _use_case_proposal(state=ReviewState.REJECTED, reviewer="dana"),
        ]
    )
    kpi_store = _FakeKpiRegistryStore()
    request = _FakeRequest(authorization_enabled=False)
    body = semantic_module.wire.BulkProposalDecisionRequest(
        ids=["prop_kpi_1", "prop_uc_1"]
    )

    result = accept_semantic_proposals_bulk(
        body, request, store=store, kpi_store=kpi_store
    )

    assert [p.id for p in result.proposals] == ["prop_kpi_1"]
    assert len(result.refused) == 1
    assert result.refused[0].id == "prop_uc_1"
    assert result.refused[0].reason == "already rejected"
    assert result.refused[0].decided_by == "dana"
    # the one live KPI id still adopted, despite the other id in the same request refusing.
    assert kpi_store.adopt_calls == 1


def test_bulk_accept_refetches_decided_proposals_in_one_batched_call():
    store = _FakeProposalStore([_kpi_proposal(), _use_case_proposal()])
    kpi_store = _FakeKpiRegistryStore()
    request = _FakeRequest(authorization_enabled=False)
    body = semantic_module.wire.BulkProposalDecisionRequest(
        ids=["prop_kpi_1", "prop_uc_1"]
    )

    accept_semantic_proposals_bulk(body, request, store=store, kpi_store=kpi_store)

    # One `get_many` for `_adopt_if_kpi`'s kind check, one for the refetch -- not one
    # `get`/`_refetch` per decided id.
    assert store.get_many_calls == 2


def test_bulk_accept_survives_a_failed_adopt_sweep_for_every_decided_kpi():
    store = _FakeProposalStore(
        [_kpi_proposal(), _kpi_proposal(identifier="prop_kpi_2")]
    )
    kpi_store = _FakeKpiRegistryStore(raises=ValueError("registry unavailable"))
    request = _FakeRequest(authorization_enabled=False)
    body = semantic_module.wire.BulkProposalDecisionRequest(
        ids=["prop_kpi_1", "prop_kpi_2"]
    )

    result = accept_semantic_proposals_bulk(
        body, request, store=store, kpi_store=kpi_store
    )

    assert result.refused == []
    assert {p.id for p in result.proposals} == {"prop_kpi_1", "prop_kpi_2"}
    assert all(p.applied_at is None and p.apply_error for p in result.proposals)


def test_bulk_accept_skips_the_adopt_sweep_when_nothing_decided_was_a_kpi():
    store = _FakeProposalStore([_use_case_proposal()])
    kpi_store = _FakeKpiRegistryStore()
    request = _FakeRequest(authorization_enabled=False)
    body = semantic_module.wire.BulkProposalDecisionRequest(ids=["prop_uc_1"])

    accept_semantic_proposals_bulk(body, request, store=store, kpi_store=kpi_store)

    assert kpi_store.adopt_calls == 0
