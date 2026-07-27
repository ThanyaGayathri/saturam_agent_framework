"""S0 — No consolidation. The control.

Every episode is retained verbatim as its own 'fact'. No LLM, no dedup, no
conflict resolution. This is the lower bound: it shows what memory-size growth
and QA look like when you never distil, and gives the other strategies a
zero-cost reference for token/latency.
"""

from __future__ import annotations

import uuid
from typing import List

from harness.schemas import ConsolidationResult, Episode, SemanticFact, UsageStats
from harness.strategies.base import ConsolidationStrategy


class NoConsolidationStrategy(ConsolidationStrategy):
    name = "baseline"

    def _apply(
        self,
        episodes: List[Episode],
        existing_facts: List[SemanticFact],
    ) -> ConsolidationResult:
        facts = list(existing_facts)
        trace = []
        for e in episodes:
            fact_id = f"f_{uuid.uuid4().hex[:12]}"
            facts.append(
                SemanticFact(
                    fact_id=fact_id,
                    statement=e.text,
                    source_episode_ids=[e.episode_id],
                    user_id=e.user_id,
                    created_at=e.timestamp,
                    valid_from=e.timestamp,
                    derived_by=self.name,
                )
            )
            trace.append({"op": "ADD", "new_id": fact_id, "statement": e.text})
        return ConsolidationResult(strategy=self.name, facts=facts, usage=UsageStats(), op_trace=trace)

    def consolidate(
        self, episodes: List[Episode], existing_facts: List[SemanticFact], usage: UsageStats
    ) -> ConsolidationResult:
        return self._apply(episodes, existing_facts)

    async def aconsolidate(
        self, episodes: List[Episode], existing_facts: List[SemanticFact], usage: UsageStats
    ) -> ConsolidationResult:
        return self._apply(episodes, existing_facts)
