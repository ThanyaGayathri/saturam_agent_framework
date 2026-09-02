"""
Reflection: Importance-Gated Insight Synthesis
================================================

This example shows SM-L6's reflection stage -- how a cluster of episodic memories
becomes one higher-level, reusable insight, once their accumulated importance
(SM-E5) crosses a threshold.

Key concepts:
- ReflectionPipeline accumulates episode importance per scope, across as many
  calls to run()/arun() as it takes, using SM-L2's own SizeTrigger (the same
  "accumulate a float, fire past a threshold, reset on drain" policy, fed
  importance instead of a byte/event count).
- A pass whose own episodes stay under the threshold does not trigger -- the
  accumulation carries forward to the next pass, the same way Generative
  Agents' reflection accumulates over many perceptions, not one batch.
- Once triggered, an InsightSynthesizer (here, LLMInsightSynthesizer) is asked
  to distill the accumulated cluster into one insight -- or to report that none
  of it rises to one, which is the common, expected answer.
- A synthesized insight becomes a FactWrite citing every episode it came from
  (provenance["source_episode_ids"]) and lands on the SEMANTIC tier through the
  same write path CoLocatedMemoryProvider.consolidate already uses for
  deterministically-deduped facts -- retrievable through an ordinary read, not
  a side channel only reflection code can see.

Requires a real model, so this needs OPENAI_API_KEY (or your configured
provider's credentials) -- there is no offline path for LLM-synthesized
insight.
"""

from dataclasses import replace
from typing import Dict, List, Optional, Sequence, Tuple

from egpt.memory import (
    MemoryEvent,
    MemoryQuery,
    MemoryRecord,
    MemoryTier,
    Scope,
    Substrate,
    require_scope,
)
from egpt.memory.adapters.co_located import CoLocatedMemoryProvider
from egpt.memory.config import MemoryProviderConfig
from egpt.memory.consolidation import LLMInsightSynthesizer, ReflectionPipeline
from egpt.models.openai import OpenAIResponses

SCOPE = Scope(tenant_id="acme", agent_id="assistant", user_id="ada")


class InMemoryVector:
    """Keyword overlap standing in for embedding similarity -- see
    `09_memory_provider_seam.py` for the fuller, annotated version this trims,
    and `23_consolidation_worker.py` for the identical copy this one mirrors."""

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
        terms = set(query.text.lower().split())
        hits = [
            replace(
                record, score=float(len(terms & set(record.content.lower().split())))
            )
            for record in self._bucket(scope).values()
            if record.tier in wanted and record.expired_at is None
        ]
        ranked = sorted((h for h in hits if h.score), key=lambda r: -(r.score or 0.0))
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
        after: Optional[Tuple[int, str]] = None,
    ) -> List[MemoryRecord]:
        require_scope(scope, method="scan")
        rows = [replace(r) for r in self._bucket(scope).values() if r.tier is tier]
        rows.sort(key=lambda r: ((r.created_at or 0), r.id))
        if after is not None:
            rows = [r for r in rows if ((r.created_at or 0), r.id) > after]
        return rows if limit is None else rows[:limit]

    async def ascan(
        self,
        scope: Scope,
        tier: MemoryTier,
        limit: Optional[int] = None,
        after: Optional[Tuple[int, str]] = None,
    ) -> List[MemoryRecord]:
        require_scope(scope, method="ascan")
        return self.scan(scope, tier, limit, after)

    def get_record(self, scope: Scope, id: str) -> Optional[MemoryRecord]:
        require_scope(scope, method="get_record")
        return self._bucket(scope).get(id)

    async def aget_record(self, scope: Scope, id: str) -> Optional[MemoryRecord]:
        require_scope(scope, method="aget_record")
        return self.get_record(scope, id)


# ---------------------------------------------------------------------------
# 1. Wire a ReflectionPipeline into a provider, with a low threshold so this
#    example fires within a few episodes rather than needing dozens.
# ---------------------------------------------------------------------------

synthesizer = LLMInsightSynthesizer(model=OpenAIResponses(id="gpt-5.5"))
pipeline = ReflectionPipeline(synthesizer, importance_threshold=1.2)

vector = InMemoryVector()
memory = CoLocatedMemoryProvider(
    vector=vector,
    config=MemoryProviderConfig(provider="co_located"),
    reflection_pipeline=pipeline,
)

# ---------------------------------------------------------------------------
# 2. Write a cluster of episodes that share a theme, each carrying an
#    importance score (SM-E5) already assigned at write time.
# ---------------------------------------------------------------------------

episodes = [
    ("Missed the sprint deadline because QA found a regression late.", 0.5),
    (
        "Shipped a hotfix same-day, but the regression should have been caught earlier.",
        0.6,
    ),
    ("Added a regression test for the exact bug that slipped through.", 0.4),
]

for i, (content, importance) in enumerate(episodes):
    memory.write(
        SCOPE,
        [
            MemoryEvent(
                content=content,
                scope=SCOPE,
                id=f"ep-{i}",
                metadata={"importance": importance},
            )
        ],
    )

print(
    "Episodes written, total importance:", sum(importance for _, importance in episodes)
)

# ---------------------------------------------------------------------------
# 3. Consolidate. The accumulated importance (1.5) crosses the 1.2 threshold,
#    so this pass asks the synthesizer to reflect on the cluster.
# ---------------------------------------------------------------------------

report = memory.consolidate(SCOPE)

print()
print("Consolidation report")
print("  semantic_written:  ", report.semantic_written)
print("  reflection details:", report.details["reflection"])

# ---------------------------------------------------------------------------
# 4. If the synthesizer found an insight, it is retrievable like any other
#    semantic fact -- and still carries its citation.
# ---------------------------------------------------------------------------

if report.details["reflection"]["triggered"]:
    slice_ = memory.read(
        SCOPE, MemoryQuery(text="deadline regression", tiers=[MemoryTier.SEMANTIC])
    )
    # The deterministic dedup pass (SM-C1) also turns each non-duplicate episode into its
    # own singleton semantic fact -- filtered out here so only the multi-episode reflection
    # insight prints, since that citation count is what distinguishes the two.
    insights = [
        r for r in slice_.records if len(r.provenance.get("source_episode_ids", [])) > 1
    ]
    for record in insights:
        print()
        print("Retrieved insight:", record.content)
        print("  cites:", record.provenance.get("source_episode_ids"))

# ---------------------------------------------------------------------------
# 5. What this layer deliberately does not do
# ---------------------------------------------------------------------------

print()
print("Owned elsewhere")
print("  contradiction resolution against an existing fact: SM-L5 (#126)")
print("  retiring an insight whose sources were invalidated: SM-L3 (#124)")
print("  the mem0-style extract+merge dedup facts sit beside: SM-L7 (#128)")
