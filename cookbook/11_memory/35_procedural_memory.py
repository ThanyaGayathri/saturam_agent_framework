"""
Procedural Memory: Reflection-Driven Skill/Rule Synthesis
===========================================================

This example shows SM-S2's procedural tier -- how a cluster of episodic
memories becomes one reusable rule, skill, or prompt fragment, and how that
rule is retrieved and injected into context for a later, related task.

Key concepts:
- ProceduralReflectionPipeline is importance-gated like SM-L6's own
  ReflectionPipeline, but stateless across calls: PROCEDURAL does not
  participate in CoLocatedMemoryProvider.consolidate (TIER_POLICY reserves
  that sweep's single source/target to EPISODIC/SEMANTIC), so this pipeline
  is invoked directly over whatever episode batch a caller hands it, rather
  than accumulating across a scheduler's drip-fed passes.
- A ProcedureSynthesizer (here, LLMProcedureSynthesizer) decides whether a
  cluster yields an actionable rule/skill/prompt fragment -- or reports that
  none of it does, which is the common, expected answer.
- A synthesized procedure becomes a plain MemoryEvent on the PROCEDURAL tier.
  PROCEDURAL is vector-only (TIER_POLICY), so it needs no new write path: the
  ordinary CoLocatedMemoryProvider.write already routes it to the vector
  substrate and nowhere else.
- Once written, the procedure is retrieved and injected exactly like any
  other memory -- PROCEDURAL is in PERSISTENT_TIERS, so an ordinary read with
  no tier named already returns it, and MemorySlice.as_context already
  renders it under a "[procedural]" line.

Requires a real model, so this needs OPENAI_API_KEY (or your configured
provider's credentials) -- there is no offline path for LLM-synthesized
procedures.
"""

import asyncio
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
from egpt.memory.procedural import LLMProcedureSynthesizer, ProceduralReflectionPipeline
from egpt.models.openai import OpenAIResponses

SCOPE = Scope(tenant_id="acme", agent_id="assistant", user_id="ada")


class InMemoryVector:
    """Keyword overlap standing in for embedding similarity -- see
    `09_memory_provider_seam.py` for the fuller, annotated version this trims,
    and `33_reflection.py` for the identical copy this one mirrors."""

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


# ---------------------------------------------------------------------------
# 1. A ProceduralReflectionPipeline, standing alone -- not wired into a
#    provider or a consolidate pass, since PROCEDURAL does not take part in
#    that sweep. A low threshold makes this example fire on one small batch
#    rather than needing dozens of episodes.
# ---------------------------------------------------------------------------

synthesizer = LLMProcedureSynthesizer(model=OpenAIResponses(id="gpt-5.5"))
pipeline = ProceduralReflectionPipeline(synthesizer, importance_threshold=1.2)

vector = InMemoryVector()
memory = CoLocatedMemoryProvider(
    vector=vector, config=MemoryProviderConfig(provider="co_located")
)

# ---------------------------------------------------------------------------
# 2. A cluster of episodes describing a mistake and its fix -- exactly the
#    shape a reusable rule comes from.
# ---------------------------------------------------------------------------

episodes = [
    MemoryEvent(
        content="Deployed on a Friday afternoon; the on-call engineer was already offline.",
        scope=SCOPE,
        id="ep-0",
        metadata={"importance": 0.6},
    ),
    MemoryEvent(
        content="The Friday deploy broke checkout; nobody caught it until Monday morning.",
        scope=SCOPE,
        id="ep-1",
        metadata={"importance": 0.7},
    ),
]

episode_records = [
    MemoryRecord(
        id=e.id,
        tier=MemoryTier.EPISODIC,
        content=e.content,
        scope=e.scope,
        metadata=e.metadata,
    )
    for e in episodes
]

print(
    "Episodes considered, total importance:",
    sum(r.metadata["importance"] for r in episode_records),
)

# ---------------------------------------------------------------------------
# 3. Reflect. The accumulated importance (1.3) crosses the 1.2 threshold, so
#    this call asks the synthesizer to distill a rule from the cluster. Sync
#    and async make the identical decision -- either call site works; this
#    example uses arun() to double as a copy-pasteable async starting point.
# ---------------------------------------------------------------------------

report = asyncio.run(pipeline.arun(SCOPE, episode_records))

print()
print("Procedural reflection report")
print("  triggered:", report.details["triggered"])
print("  accumulated_importance:", report.accumulated_importance)

# ---------------------------------------------------------------------------
# 4. If the synthesizer found a procedure, write it through the ordinary
#    write path -- PROCEDURAL needs no new one -- then retrieve it exactly
#    like any other memory, for a query about a related, future task.
# ---------------------------------------------------------------------------

if report.write is not None:
    asyncio.run(memory.awrite(SCOPE, [report.write]))

    slice_ = asyncio.run(
        memory.aread(SCOPE, MemoryQuery(text="should we deploy on Friday"))
    )

    print()
    print("Injected context for a related task:")
    print(slice_.as_context())
else:
    print()
    print(
        "The cluster did not rise to a reusable rule this time -- a common, expected answer."
    )

# ---------------------------------------------------------------------------
# 5. What this layer deliberately does not do
# ---------------------------------------------------------------------------

print()
print("Owned elsewhere")
print(
    "  retiring a procedure whose sources were later invalidated: unowned, same gap SM-L3/SM-L6 name"
)
print(
    "  deciding when to invoke this pipeline (a scheduler policy): SM-L1 schedules consolidate, not this"
)
print("  assembling a MemorySlice into an agent's system prompt: SM-H6 (#136)")
