"""The MemoryProvider eval contract (a thin projection of the SM-R11 seam).

SM-R11 (#86) OWNS the canonical ``MemoryProvider`` interface:
    read(tenant, agent, query, types) -> MemorySlice
    write(tenant, agent, events)
    consolidate(tenant, scope)
over vector + graph substrates and Mode A/B. This harness does NOT redefine that
interface. It declares the **minimal eval-facing contract** the scoring loop
needs, and states exactly how it maps onto R11 so that when SM-C1 implements the
real provider, one adapter satisfies this contract with no change to the runner
or metrics (skill §3 plug-and-play):

    harness contract          <- maps onto ->     SM-R11 seam
    provider.write(events)                        write(tenant, agent, events)
    provider.read(query,k,as_of,types)            read(tenant, agent, query, types)
    provider.consolidate(scope)                   consolidate(tenant, scope)

``tenant`` and ``agent`` are provider-instance configuration here (constructor),
so the per-call signatures the runner uses stay small; the R11 adapter binds them.

The ONE requirement the harness places on any provider, beyond R11, is
**retrieval provenance**: each returned ``RetrievedMemory`` must expose the
``source_ids`` (the originating ``turn_id``s) so recall@k / nDCG@k can be scored
against a probe's gold ``evidence_ids``. R11's ``MemorySlice`` items are expected
to carry this provenance already (it is core to the neuro-symbolic design); the
adapter surfaces it. ``as_of`` is required for bi-temporal "true at T".

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

    def consolidate(self, scope: Optional[str] = None) -> None: ...

    async def aconsolidate(self, scope: Optional[str] = None) -> None: ...


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
