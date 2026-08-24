"""
KB vs Memory: One Retrieval Interface, Labeled by Source
==========================================================

This example shows the SM-S4 boundary (#121): the curated Knowledge Base
(RAG) and dynamic memory stay separate stores -- different mutability, time,
and ownership regimes -- and unified_read() is the one call that reaches both
and labels every result by which store it came from.

Key concepts:
- unified_read(scope, query, memory=..., knowledge=...) -> UnifiedSlice
- Every UnifiedResult carries a RetrievalSource: RetrievalSource.KB or
  RetrievalSource.MEMORY. A caller building an agent prompt can cite a KB
  fact and a memory fact differently, or trust one more than the other.
- A leg with no store wired returns an empty list, not an absent one -- the
  same "did not run" convention the memory seam's own substrate legs use.
- Neither store is touched or merged: MemoryProvider.read and
  Knowledge.retrieve are called independently and composed after the fact.

The two legs below are tiny stand-ins satisfying MemoryProvider and
KnowledgeProtocol -- the same two ports 09_memory_provider_seam.py and a real
Knowledge instance satisfy -- so this file needs no database, no model, and
no API key.
"""

from dataclasses import replace
from typing import Any, Dict, List, Optional, Sequence

from egpt.knowledge.document import Document
from egpt.memory import (
    ConsolidationReport,
    MemoryEvent,
    MemoryProvider,
    MemoryQuery,
    MemoryRecord,
    MemorySlice,
    MemoryTier,
    Scope,
    Substrate,
    WriteReceipt,
    unified_read,
)

# ---------------------------------------------------------------------------
# A tiny memory leg -- keyword overlap standing in for a real MemoryProvider
# ---------------------------------------------------------------------------


class ToyMemory(MemoryProvider):
    """Enough of MemoryProvider to answer read/aread; nothing else is exercised here."""

    provider_key = "toy"

    def __init__(self) -> None:
        self.config = None
        self.rows: Dict[str, MemoryRecord] = {}

    def remember(self, record: MemoryRecord) -> None:
        self.rows[record.id] = record

    def read(self, scope: Scope, query: MemoryQuery) -> MemorySlice:
        matches = [
            replace(record)
            for record in self.rows.values()
            if record.scope == scope and query.text.lower() in record.content.lower()
        ]
        return MemorySlice(scope=scope, query=query, records=matches[: query.limit])

    async def aread(self, scope: Scope, query: MemoryQuery) -> MemorySlice:
        return self.read(scope, query)

    def write(self, scope: Scope, events: Sequence[MemoryEvent]) -> WriteReceipt:
        raise NotImplementedError("this demo only exercises the read leg")

    async def awrite(self, scope: Scope, events: Sequence[MemoryEvent]) -> WriteReceipt:
        raise NotImplementedError("this demo only exercises the read leg")

    def consolidate(self, scope: Scope, tiers=None) -> ConsolidationReport:
        raise NotImplementedError("this demo only exercises the read leg")

    async def aconsolidate(self, scope: Scope, tiers=None) -> ConsolidationReport:
        raise NotImplementedError("this demo only exercises the read leg")

    def read_tier(
        self, scope: Scope, tier: MemoryTier, query: MemoryQuery
    ) -> List[MemoryRecord]:
        raise NotImplementedError("this demo only exercises the read leg")

    async def aread_tier(
        self, scope: Scope, tier: MemoryTier, query: MemoryQuery
    ) -> List[MemoryRecord]:
        raise NotImplementedError("this demo only exercises the read leg")

    def write_tier(
        self, scope: Scope, tier: MemoryTier, events: Sequence[MemoryEvent]
    ) -> WriteReceipt:
        raise NotImplementedError("this demo only exercises the read leg")

    async def awrite_tier(
        self, scope: Scope, tier: MemoryTier, events: Sequence[MemoryEvent]
    ) -> WriteReceipt:
        raise NotImplementedError("this demo only exercises the read leg")

    def substrates(self) -> Dict[Substrate, bool]:
        return {Substrate.VECTOR: True, Substrate.GRAPH: False}

    def tiers(self) -> Dict[MemoryTier, bool]:
        return {tier: tier == MemoryTier.SEMANTIC for tier in MemoryTier}


# ---------------------------------------------------------------------------
# A tiny KB leg -- satisfies KnowledgeProtocol without a vector_db or reader
# ---------------------------------------------------------------------------


class ToyKnowledge:
    """Enough of KnowledgeProtocol to answer retrieve/aretrieve; a real Knowledge instance
    satisfies the same protocol via knowledge/knowledge.py's search()."""

    def __init__(self, documents: Sequence[Document]) -> None:
        self.documents = list(documents)

    def retrieve(
        self,
        query: str,
        max_results: Optional[int] = None,
        filters: Optional[Any] = None,
        **kwargs,
    ) -> List[Document]:
        matches = [d for d in self.documents if query.lower() in d.content.lower()]
        return matches[:max_results] if max_results else matches

    async def aretrieve(
        self,
        query: str,
        max_results: Optional[int] = None,
        filters: Optional[Any] = None,
        **kwargs,
    ) -> List[Document]:
        return self.retrieve(query, max_results=max_results, filters=filters)


if __name__ == "__main__":
    scope = Scope(tenant_id="acme", user_id="ada@example.com")

    memory = ToyMemory()
    memory.remember(
        MemoryRecord(
            id="fact-1",
            tier=MemoryTier.SEMANTIC,
            content="ada prefers dark mode and terse answers",
            scope=scope,
            score=0.95,
        )
    )

    knowledge = ToyKnowledge(
        [
            Document(
                id="doc-1",
                content="The public API's rate limit is 100 requests per minute.",
                reranking_score=0.8,
                meta_data={"source": "api-docs.md"},
            )
        ]
    )

    query = MemoryQuery(text="rate limit", limit=5)
    print("Query: %r, wired to both KB and memory" % query.text)
    slice_ = unified_read(scope, query, memory=memory, knowledge=knowledge)
    for result in slice_.results:
        print(
            "  [%s] %s (score=%s)" % (result.source.value, result.content, result.score)
        )

    query = MemoryQuery(text="dark mode", limit=5)
    print()
    print("Query: %r, wired to both KB and memory" % query.text)
    slice_ = unified_read(scope, query, memory=memory, knowledge=knowledge)
    for result in slice_.results:
        print(
            "  [%s] %s (score=%s)" % (result.source.value, result.content, result.score)
        )

    # A leg with no store wired is an empty list, not an absent one.
    print()
    print("Same query, KB not wired at all:")
    slice_ = unified_read(scope, query, memory=memory, knowledge=None)
    print("  kb:", slice_.kb, "-- memory:", [r.content for r in slice_.memory])
