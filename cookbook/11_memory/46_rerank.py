"""Optional cross-encoder rerank stage after fusion (SM-H3 #133).

`42_rrf_fusion.py` shows fusion; this shows the stage after it. SM-R8 DECISION.md S1.2 measured
CPU cross-encoder rerank at ~2.0-2.5s/query -- ~100x its own ~300ms budget -- so it stays off the
default hot path (`config.rerank` defaults to `{"enabled": False}`, same shape as `config.fusion`).
Enabling it means naming a `Reranker` from `egpt.integrations.knowledge.reranker.*`
(`SentenceTransformerReranker`/`InfinityReranker` for Mode A -- self-hosted, offline-capable;
`CohereReranker`/`AwsBedrockReranker` are Mode-B-only and rejected at construction under
`mode=LOCAL`, mirroring ADR 0003 A.3c's embedder air-gap check).

Three runs against the same fused candidates: rerank off (fused order untouched), rerank on with a
fast in-process reranker (reorders, reports `rerank_applied=True`), and rerank on with a reranker
slower than its own budget (falls back to the fused order, reports `rerank_budget_exceeded=True`
-- the budget is enforced by abandoning the call, not by timing it: the caller gets control back
near the budget, the abandoned call is left to finish in the background).

Self-contained: no database, no model, no network. The "reranker" here is a small deterministic
`Reranker` subclass rather than a real cross-encoder, so the demo runs in milliseconds and needs
no GPU -- the wiring under test is the budget/toggle/fallback machinery, not model quality.
"""

import time
from dataclasses import replace
from typing import Dict, List, Optional, Sequence

from egpt.integrations.knowledge.reranker.base import Reranker
from egpt.knowledge.document import Document
from egpt.memory import (
    SCOPE_SLOTS,
    MemoryEvent,
    MemoryProviderConfig,
    MemoryQuery,
    MemoryRecord,
    MemoryTier,
    Scope,
    Substrate,
    build_memory_provider,
    require_scope,
)
from egpt.memory.types import RetrievalCandidates


def word_overlap(query_text: str, content: str) -> float:
    q, c = set(query_text.lower().split()), set(content.lower().split())
    return 0.0 if not q or not c else len(q & c) / len(q | c)


def scoped(record_scope: Scope, call_scope: Scope) -> bool:
    for slot in SCOPE_SLOTS:
        wanted = getattr(call_scope, slot)
        if wanted is not None and getattr(record_scope, slot) != wanted:
            return False
    return True


class InMemoryVector:
    """Vector-only stand-in (no lexical leg -- this demo is about the rerank stage, not fusion)."""

    def __init__(self) -> None:
        self.rows: Dict[str, Dict[str, MemoryRecord]] = {}

    def _bucket(self, scope: Scope) -> Dict[str, MemoryRecord]:
        return self.rows.setdefault(scope.tenant_id, {})

    def upsert(self, scope: Scope, records: Sequence[MemoryRecord]) -> None:
        require_scope(scope, method="upsert")
        for record in records:
            self._bucket(scope)[record.id] = replace(record, substrate=Substrate.VECTOR)

    async def aupsert(self, scope: Scope, records: Sequence[MemoryRecord]) -> None:
        require_scope(scope, method="aupsert")
        self.upsert(scope, records)

    def search(
        self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None
    ) -> List[MemoryRecord]:
        require_scope(scope, method="search")
        wanted = {tier} if tier else set(query.resolved_tiers())
        hits = [
            replace(record, score=score)
            for record in self._bucket(scope).values()
            if record.tier in wanted
            and record.expired_at is None
            and scoped(record.scope, scope)
            for score in [word_overlap(query.text, record.content)]
            if score
        ]
        ranked = sorted(hits, key=lambda r: -(r.score or 0.0))
        return ranked[: query.limit] if query.limit else []

    async def asearch(
        self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None
    ) -> List[MemoryRecord]:
        require_scope(scope, method="asearch")
        return self.search(scope, query, tier)

    def scan(
        self,
        scope: Scope,
        tier: MemoryTier,
        limit: Optional[int] = None,
        after: Optional[tuple] = None,
    ) -> List[MemoryRecord]:
        require_scope(scope, method="scan")
        rows = [
            replace(r)
            for r in self._bucket(scope).values()
            if r.tier is tier and scoped(r.scope, scope)
        ]
        rows.sort(key=lambda r: ((r.created_at or 0), r.id))
        if after is not None:
            rows = [r for r in rows if ((r.created_at or 0), r.id) > after]
        return rows if limit is None else rows[:limit]

    async def ascan(
        self,
        scope: Scope,
        tier: MemoryTier,
        limit: Optional[int] = None,
        after: Optional[tuple] = None,
    ) -> List[MemoryRecord]:
        require_scope(scope, method="ascan")
        return self.scan(scope, tier, limit, after)

    def get_record(self, scope: Scope, id: str) -> Optional[MemoryRecord]:
        require_scope(scope, method="get_record")
        row = self._bucket(scope).get(id)
        return replace(row) if row is not None and scoped(row.scope, scope) else None

    async def aget_record(self, scope: Scope, id: str) -> Optional[MemoryRecord]:
        require_scope(scope, method="aget_record")
        return self.get_record(scope, id)

    # -- unused by this demo, present only so `checked_vector`'s structural check passes ---------
    def search_candidates(self, scope, query, tier=None) -> RetrievalCandidates:
        return RetrievalCandidates(vector=self.search(scope, query, tier), lexical=[])

    async def asearch_candidates(self, scope, query, tier=None) -> RetrievalCandidates:
        return self.search_candidates(scope, query, tier)


class PhraseFlipReranker(Reranker):
    """A deterministic stand-in for a real cross-encoder: scores a document higher the more of the
    query's words it contains *contiguously* -- a signal word-overlap (the vector leg's own
    scoring, above) cannot see, so reranking visibly changes the order rather than confirming it.

    Declares `offline_capable = True` so it passes the same Mode-A construction check a real
    self-hosted reranker (`SentenceTransformerReranker`, module `sentence_transformer`) would --
    see `egpt.memory.config._reject_online_reranker_for_local`.
    """

    offline_capable: bool = True

    def rerank(self, query: str, documents: List[Document]) -> List[Document]:
        scored = [
            (
                doc,
                float(
                    len(query.strip())
                    if query.strip().lower() in doc.content.lower()
                    else 0
                ),
            )
            for doc in documents
        ]
        scored.sort(key=lambda pair: -pair[1])
        for doc, score in scored:
            doc.reranking_score = score
        return [doc for doc, _ in scored]


class SlowReranker(Reranker):
    """A reranker slower than any budget this demo sets -- shows the fallback path."""

    offline_capable: bool = True
    delay_s: float = 1.0

    def rerank(self, query: str, documents: List[Document]) -> List[Document]:
        time.sleep(self.delay_s)
        return documents


def _read_with(rerank: dict, scope: Scope) -> None:
    provider = build_memory_provider(
        MemoryProviderConfig(
            provider="co_located",
            rerank=rerank,
            options={"vector_substrate": InMemoryVector()},
        )
    )
    provider.write(
        scope,
        [
            # Same 3 words as the query, as a *set* -- word-overlap (Jaccard) scores this 1.0,
            # the maximum -- but reordered, so it does not contain the literal phrase.
            MemoryEvent(id="overlap_only", content="fox brown jumps", scope=scope),
            # Contains the literal phrase, but padded with unrelated words, so its word-overlap
            # Jaccard (3 shared words over a 7-word union) is lower than `overlap_only`'s.
            MemoryEvent(
                id="has_phrase",
                content="brown fox jumps over the lazy dog",
                scope=scope,
            ),
        ],
    )
    start = time.monotonic()
    slice_ = provider.read(scope, MemoryQuery(text="brown fox jumps", limit=5))
    elapsed_ms = (time.monotonic() - start) * 1000.0
    print(f"  records: {[r.id for r in slice_.records]}")
    print(
        f"  rerank_applied={slice_.fusion.get('rerank_applied')} "
        f"rerank_budget_exceeded={slice_.fusion.get('rerank_budget_exceeded')} "
        f"wall_time={elapsed_ms:.0f}ms"
    )


if __name__ == "__main__":
    demo_scope = Scope(tenant_id="acme", user_id="ada")

    print("Rerank off (default) -- fused (vector) order stands:")
    _read_with({}, demo_scope)

    print(
        "\nRerank on, fast reranker -- the phrase match overtakes the higher word-overlap hit:"
    )
    _read_with({"enabled": True, "reranker": PhraseFlipReranker()}, demo_scope)

    print(
        "\nRerank on, reranker slower than its budget -- falls back to the fused order:"
    )
    _read_with(
        {"enabled": True, "reranker": SlowReranker(delay_s=1.0), "budget_ms": 50},
        demo_scope,
    )
    print(
        "  -> the read returns near the 50ms budget, not after the reranker's full 1s: the "
        "abandoned call keeps running in the background, but nothing here waits on it."
    )
