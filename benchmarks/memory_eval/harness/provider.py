"""The MemoryProvider eval contract (a thin projection of the SM-R11 seam).

SM-R11 (#86) OWNS the canonical ``MemoryProvider`` ABC. Its CURRENT, ratified
signatures (``benchmarks/memory_provider_spec/interface.py``, twice-fixed) are:

    read(scope: Scope, query: MemoryQuery) -> MemorySlice
    write(scope: Scope, events: Sequence[MemoryEvent]) -> WriteReceipt
    consolidate(scope: Scope, tiers: Optional[Sequence[MemoryTier]] = None) -> ConsolidationReport

NOTE — earlier this docstring copied the issue-#86 *sketch*
``read(tenant, agent, query, types)`` / ``write(tenant, agent, events)`` /
``consolidate(tenant, scope)``. That was wrong: SM-R11's ``interface.py`` ``read``
docstring and SPEC.md §5.3 carry a **DELIBERATE-DEVIATION** callout (which names
this PR, SM-R13 #19) precisely because that strawman was propagated. Corrected
here. The real seam folds tenant+agent into a ``Scope`` object and expresses the
issue's ``types`` as ``MemoryQuery.tiers``.

This harness does NOT redefine R11's interface — it declares the **minimal
eval-facing contract** the scoring loop needs and maps 1:1 onto R11 so one adapter
satisfies it with no runner/metric changes (skill §3):

    harness contract                      <- maps onto ->  SM-R11 seam
    provider.write(events)                                 write(scope, events) -> WriteReceipt
    provider.read(query, k, as_of, types)                 read(scope, MemoryQuery(
                                                             text=query, limit=k,
                                                             as_of=as_of, tiers=types)) -> MemorySlice
    provider.consolidate(tiers)                            consolidate(scope, tiers) -> ConsolidationReport

Binding of the thin params to R11:
- ``Scope`` — the harness runs single-tenant; tenant/agent live on ``RunConfig``
  (``RunConfig.tenant``/``.agent``) and the R11 adapter builds a ``Scope`` from them.
- ``k`` -> ``MemoryQuery.limit`` ; ``as_of`` -> ``MemoryQuery.as_of`` (epoch s,
  bi-temporal, SM-R10) ; ``types`` -> ``MemoryQuery.tiers``.
- harness ``MemoryEvent`` (event_id/text/timestamp) projects R11's ``MemoryEvent``
  (id/content/occurred_at).

The ONE requirement the harness places on any provider, beyond R11, is
**retrieval provenance**: each returned ``RetrievedMemory`` exposes ``source_ids``
(originating ``turn_id``s) so recall@k / nDCG@k score against a probe's gold
``evidence_ids``. R11's ``MemoryRecord`` already carries this in ``provenance``
(source_episode_ids); the adapter surfaces it.

Both sync and async methods are declared (codebase convention).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Protocol, Sequence, runtime_checkable

from harness.schemas import Session, Turn


@dataclass(frozen=True)
class MemoryEvent:
    """A unit written to memory. Built from a Turn on ingest (the write side)."""

    event_id: str  # == turn_id, so retrieved provenance maps back to gold evidence
    text: str
    speaker: str
    timestamp: int
    session_id: str

    @staticmethod
    def from_turn(turn: Turn, session_id: str) -> "MemoryEvent":
        return MemoryEvent(
            event_id=turn.turn_id,
            text=turn.as_memory_text(),
            speaker=turn.speaker,
            timestamp=turn.timestamp,
            session_id=session_id,
        )


def events_of(session: Session) -> List[MemoryEvent]:
    return [MemoryEvent.from_turn(t, session.session_id) for t in session.turns]


@dataclass(frozen=True)
class RetrievedMemory:
    """One item from a read(). ``source_ids`` is the provenance used for recall/nDCG.

    A raw episodic memory has a single source (its turn). A consolidated semantic
    memory may cite several turns -> ``source_ids`` lists them all; the runner
    expands them in rank order when computing retrieval metrics.
    """

    source_ids: List[str]
    text: str
    score: float
    timestamp: int


@runtime_checkable
class MemoryProvider(Protocol):
    """Eval-facing memory seam. A projection of the SM-R11 read/write/consolidate."""

    name: str

    def write(self, events: Sequence[MemoryEvent]) -> None: ...

    async def awrite(self, events: Sequence[MemoryEvent]) -> None: ...

    def read(
        self, query: str, *, k: int, as_of: Optional[int] = None, types: Optional[Sequence[str]] = None
    ) -> List[RetrievedMemory]: ...

    async def aread(
        self, query: str, *, k: int, as_of: Optional[int] = None, types: Optional[Sequence[str]] = None
    ) -> List[RetrievedMemory]: ...

    # Param is ``tiers`` (which tiers to consolidate) to match R11's
    # ``consolidate(scope, tiers)``. It is NOT R11's ``scope`` (a ``Scope``) — the
    # old ``scope: Optional[str]`` here collided with that type while meaning R11's
    # ``tiers``. Renamed to remove the collision.
    def consolidate(self, tiers: Optional[Sequence[str]] = None) -> None: ...

    async def aconsolidate(self, tiers: Optional[Sequence[str]] = None) -> None: ...


@runtime_checkable
class Answerer(Protocol):
    """Turns retrieved memories into an answer string.

    Separated from the provider so retrieval quality and answer generation are
    measured independently. Offline: ``StubAnswerer`` (returns retrieved text, so
    QA-correctness is a lexical proxy that tracks recall). Full runs: an
    egpt-Agent-backed answerer (Phase 4) behind this same protocol.
    """

    name: str

    def answer(self, question: str, memories: Sequence[RetrievedMemory]) -> str: ...

    async def aanswer(self, question: str, memories: Sequence[RetrievedMemory]) -> str: ...


@dataclass
class RunConfig:
    """Knobs for one eval run, kept in one place so call sites don't hardcode them."""

    k: int = 5
    consolidate: bool = False
    tenant: str = "eval"
    agent: str = "eval-agent"
    types: Optional[List[str]] = field(default=None)
