"""
Hierarchical Summarization: Session -> Day -> Global
======================================================

This example shows SM-L8's summarization stage -- how a scope's episodes are
narrated into a rolling session summary, and how session/day material folds
upward into higher-level rollups, without losing the gist as history grows.

Key concepts:
- SummaryPipeline decides *what* to summarize and *when* a rollup is due; the
  caller (CoLocatedMemoryProvider.consolidate) writes the result, the same
  split ReflectionPipeline uses (see 33_reflection.py).
- The session level is rolling: one row per scope, continuously updated in
  place as new episodes arrive (LLMSessionSummarizer reuses
  SessionSummaryResponse from session/summary.py -- the reuse the ticket
  names, not a redefinition of what a summary carries).
- Day and global are periodic rollups, gated by LatencyTrigger (SM-L2's own
  "has the oldest pending item waited long enough" policy, fed a period
  length) and folded via SummarizeStrategy (memory/strategies/summarize.py --
  the other reuse the ticket names).
- Every summary lands on the SEMANTIC tier through the same write path
  CoLocatedMemoryProvider.consolidate already uses for a distilled fact or a
  reflected insight, tagged metadata={"kind": "summary", "level": ...} --
  retrievable through an ordinary read, not a side channel.

Requires a real model, so this needs OPENAI_API_KEY (or your configured
provider's credentials) -- there is no offline path for LLM-narrated summary.
"""

import time
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
from egpt.memory.consolidation.summarize import LLMSessionSummarizer, SummaryPipeline
from egpt.models.openai import OpenAIResponses

SCOPE_S1 = Scope(tenant_id="acme", agent_id="assistant", user_id="ada", session_id="s1")
SCOPE_S2 = Scope(tenant_id="acme", agent_id="assistant", user_id="ada", session_id="s2")


def _scope_matches(record_scope: Scope, query_scope: Scope) -> bool:
    """A dimension the query scope leaves unset (``None``) is a wildcard; one it sets must match
    the record's own value exactly -- this is the one property `33_reflection.py`'s single-session
    copy of this fake never had to carry, since consolidating and reading always used the same
    scope there. Here `read`'s rollup scope (no ``session_id``) must still match rows written
    under session-scoped calls, while each session's own `consolidate` call must not see the
    other's episodes -- real substrates enforce exactly this, which is what
    ``assert_scope_isolation`` checks for.
    """
    for field in ("tenant_id", "agent_id", "user_id", "team_id", "session_id"):
        wanted = getattr(query_scope, field)
        if wanted is not None and getattr(record_scope, field) != wanted:
            return False
    return True


class InMemoryVector:
    """Keyword overlap standing in for embedding similarity -- see
    `09_memory_provider_seam.py` for the fuller, annotated version this trims,
    and `23_consolidation_worker.py`/`33_reflection.py` for the shape this one
    extends with real scope filtering (see `_scope_matches`), needed here
    because this example -- unlike those two -- reads and consolidates under
    two different scopes (per-session vs. the day/global rollup scope)."""

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
            if record.tier in wanted
            and record.expired_at is None
            and _scope_matches(record.scope, scope)
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
        rows = [
            replace(r)
            for r in self._bucket(scope).values()
            if r.tier is tier and _scope_matches(r.scope, scope)
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
        after: Optional[Tuple[int, str]] = None,
    ) -> List[MemoryRecord]:
        require_scope(scope, method="ascan")
        return self.scan(scope, tier, limit, after)

    def get_record(self, scope: Scope, id: str) -> Optional[MemoryRecord]:
        require_scope(scope, method="get_record")
        row = self._bucket(scope).get(id)
        return (
            replace(row)
            if row is not None and _scope_matches(row.scope, scope)
            else None
        )

    async def aget_record(self, scope: Scope, id: str) -> Optional[MemoryRecord]:
        require_scope(scope, method="aget_record")
        return self.get_record(scope, id)


# ---------------------------------------------------------------------------
# 1. Wire a SummaryPipeline into a provider, with a short day period so this
#    example rolls up within two sessions rather than needing real elapsed
#    days.
# ---------------------------------------------------------------------------

model = OpenAIResponses(id="gpt-5.5")
summarizer = LLMSessionSummarizer(model=model)
pipeline = SummaryPipeline(summarizer, model, day_seconds=1)

vector = InMemoryVector()
memory = CoLocatedMemoryProvider(
    vector=vector,
    config=MemoryProviderConfig(provider="co_located"),
    summary_pipeline=pipeline,
)

# ---------------------------------------------------------------------------
# 2. Write and consolidate a first session -- this narrates a rolling session
#    summary but does not yet roll up (the day trigger has just armed).
# ---------------------------------------------------------------------------

memory.write(
    SCOPE_S1,
    [
        MemoryEvent(
            content="Ada asked about enterprise pricing.", scope=SCOPE_S1, id="s1-e1"
        ),
        MemoryEvent(
            content="Ada requested a demo for her team.", scope=SCOPE_S1, id="s1-e2"
        ),
    ],
)
first = memory.consolidate(SCOPE_S1)
print("Session 1 summary details:", first.details["summary"])

# ---------------------------------------------------------------------------
# 3. A second session, after the day period has elapsed -- the day rollup
#    fires, folding both sessions' rolling summaries via SummarizeStrategy.
# ---------------------------------------------------------------------------

time.sleep(1.1)

memory.write(
    SCOPE_S2,
    [
        MemoryEvent(
            content="Ada signed up for the enterprise plan.", scope=SCOPE_S2, id="s2-e1"
        )
    ],
)
second = memory.consolidate(SCOPE_S2)
print("Session 2 summary details:", second.details["summary"])

# ---------------------------------------------------------------------------
# 4. Every summary produced is retrievable like any other semantic fact --
#    filtered here by its "kind"/"level" metadata to show each tier.
# ---------------------------------------------------------------------------

rollup_scope = Scope(tenant_id="acme", agent_id="assistant", user_id="ada")
slice_ = memory.read(
    rollup_scope,
    MemoryQuery(text="pricing demo enterprise", tiers=[MemoryTier.SEMANTIC]),
)
summaries = [r for r in slice_.records if r.metadata.get("kind") == "summary"]

for record in sorted(summaries, key=lambda r: r.metadata["level"]):
    print()
    print(f"[{record.metadata['level']}] {record.content}")

# ---------------------------------------------------------------------------
# 5. What this layer deliberately does not do
# ---------------------------------------------------------------------------

print()
print("Owned elsewhere")
print(
    "  retiring a summary whose source episodes were later invalidated: SM-L3 (#124)/SM-L7 (#128)"
)
print(
    "  grouping raw episodes into sessions when a caller's scope doesn't already do it: caller's own scope"
)
print(
    "  calendar-aligned day boundaries (this uses a rolling duration instead): out of this ticket's size"
)
