"""S1 — mem0-style extract + merge.

Two LLM stages, mirroring the seed ``memory/manager.py`` path and the mem0
"page 4" dedup design:

  1. Extract atomic candidate facts from the new episodes.
  2. For each candidate, retrieve the most similar existing facts and let the
     LLM choose ADD / UPDATE / DELETE / NOOP.

Provenance is attached at extraction (candidate cites its source episodes) and
carried through merges. Conflicts are resolved by UPDATE (supersede, keep
newer) so history survives.
"""

from __future__ import annotations

from typing import List

from harness.schemas import ConsolidationResult, Episode, SemanticFact, UsageStats
from harness.strategies.base import ConsolidationStrategy, top_similar
from harness.strategies.ops import apply_merge


class Mem0MergeStrategy(ConsolidationStrategy):
    name = "mem0"

    def _batch_timestamp(self, episodes: List[Episode]) -> int:
        return max((e.timestamp for e in episodes), default=0)

    def consolidate(
        self, episodes: List[Episode], existing_facts: List[SemanticFact], usage: UsageStats
    ) -> ConsolidationResult:
        facts = list(existing_facts)
        trace = []
        ts = self._batch_timestamp(episodes)
        extractions = self._extract(episodes, usage)
        for ex in extractions:
            source_ids = [episodes[i].episode_id for i in ex["source_indices"] if 0 <= i < len(episodes)]
            neighbors = [f for f, _ in top_similar(ex["statement"], facts)]
            decision = self._merge_decision(ex["statement"], neighbors, usage)
            record = apply_merge(
                facts=facts,
                decision=decision,
                source_episode_ids=source_ids,
                timestamp=ts,
                strategy_name=self.name,
                topics=ex.get("topics"),
            )
            trace.append(record)
        return ConsolidationResult(strategy=self.name, facts=facts, usage=usage_snapshot(usage), op_trace=trace)

    async def aconsolidate(
        self, episodes: List[Episode], existing_facts: List[SemanticFact], usage: UsageStats
    ) -> ConsolidationResult:
        facts = list(existing_facts)
        trace = []
        ts = self._batch_timestamp(episodes)
        extractions = await self._aextract(episodes, usage)
        for ex in extractions:
            source_ids = [episodes[i].episode_id for i in ex["source_indices"] if 0 <= i < len(episodes)]
            neighbors = [f for f, _ in top_similar(ex["statement"], facts)]
            decision = await self._amerge_decision(ex["statement"], neighbors, usage)
            record = apply_merge(
                facts=facts,
                decision=decision,
                source_episode_ids=source_ids,
                timestamp=ts,
                strategy_name=self.name,
                topics=ex.get("topics"),
            )
            trace.append(record)
        return ConsolidationResult(strategy=self.name, facts=facts, usage=usage_snapshot(usage), op_trace=trace)


def usage_snapshot(usage: UsageStats) -> UsageStats:
    """The runner owns the cumulative usage; results carry the same reference."""
    return usage
