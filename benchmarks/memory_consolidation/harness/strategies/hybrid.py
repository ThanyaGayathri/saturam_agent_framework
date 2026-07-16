"""S4 — Hybrid: mem0 extract+merge base + periodic reflection insights.

This is the leading design candidate. Rationale:
  - The durable tier is atomic facts with provenance and bi-temporal conflict
    resolution (from mem0/S1) — this is what keeps memory precise and auditable
    and avoids the fragile whole-blob summarization failure mode.
  - On top, an importance/interval-gated reflection pass (from Generative
    Agents/S2) adds higher-level insights that plain extraction misses, each
    citing its supporting facts.

So we get mem0's precision + dedup/conflict handling AND reflection's
generalization, while never collapsing everything into one lossy summary.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List

from harness.llm import LLMClient
from harness.schemas import ConsolidationResult, Episode, SemanticFact, UsageStats
from harness.strategies.base import ConsolidationStrategy, top_similar
from harness.strategies.ops import apply_merge
from harness.strategies.reflection import _REFLECT_SYSTEM, _coerce_insights


class HybridStrategy(ConsolidationStrategy):
    name = "hybrid"

    def __init__(self, llm: LLMClient, reflect_every: int = 2) -> None:
        super().__init__(llm)
        self.reflect_every = reflect_every
        self._batches = 0

    # -- shared merge loop -------------------------------------------------
    def _merge_extractions(self, episodes, facts, extractions, ts, usage, trace) -> None:
        for ex in extractions:
            source_ids = [episodes[i].episode_id for i in ex["source_indices"] if 0 <= i < len(episodes)]
            neighbors = [f for f, _ in top_similar(ex["statement"], facts)]
            decision = self._merge_decision(ex["statement"], neighbors, usage)
            trace.append(
                apply_merge(
                    facts=facts,
                    decision=decision,
                    source_episode_ids=source_ids,
                    timestamp=ts,
                    strategy_name=self.name,
                    topics=ex.get("topics"),
                )
            )

    async def _amerge_extractions(self, episodes, facts, extractions, ts, usage, trace) -> None:
        for ex in extractions:
            source_ids = [episodes[i].episode_id for i in ex["source_indices"] if 0 <= i < len(episodes)]
            neighbors = [f for f, _ in top_similar(ex["statement"], facts)]
            decision = await self._amerge_decision(ex["statement"], neighbors, usage)
            trace.append(
                apply_merge(
                    facts=facts,
                    decision=decision,
                    source_episode_ids=source_ids,
                    timestamp=ts,
                    strategy_name=self.name,
                    topics=ex.get("topics"),
                )
            )

    def _store_insights(self, insights, recent, facts, ts, trace) -> None:
        for ins in insights:
            source_ids: List[str] = []
            for idx in ins["source_facts"]:
                if 0 <= idx < len(recent):
                    source_ids.extend(recent[idx].source_episode_ids)
            fid = f"f_{uuid.uuid4().hex[:12]}"
            facts.append(
                SemanticFact(
                    fact_id=fid,
                    statement=ins["insight"],
                    source_episode_ids=list(dict.fromkeys(source_ids)),
                    user_id=facts[0].user_id if facts else "default",
                    created_at=ts,
                    valid_from=ts,
                    importance=min(1.0, ins["importance"] / 10.0),
                    derived_by=f"{self.name}:insight",
                )
            )
            trace.append({"op": "ADD", "new_id": fid, "statement": ins["insight"], "kind": "insight"})

    # ------------------------------------------------------------------ #
    def consolidate(
        self, episodes: List[Episode], existing_facts: List[SemanticFact], usage: UsageStats
    ) -> ConsolidationResult:
        facts = list(existing_facts)
        trace: List[Dict[str, Any]] = []
        ts = max((e.timestamp for e in episodes), default=0)
        self._merge_extractions(episodes, facts, self._extract(episodes, usage), ts, usage, trace)
        self._batches += 1
        if self._batches % self.reflect_every == 0:
            recent = [f for f in facts if f.is_active and not str(f.derived_by).endswith("insight")][-20:]
            if len(recent) >= 3:
                numbered = "\n".join(f"{i}. {f.statement}" for i, f in enumerate(recent))
                insights = _coerce_insights(self.llm.complete_json(_REFLECT_SYSTEM, "Facts:\n" + numbered, usage))
                self._store_insights(insights, recent, facts, ts, trace)
        return ConsolidationResult(strategy=self.name, facts=facts, usage=usage, op_trace=trace)

    async def aconsolidate(
        self, episodes: List[Episode], existing_facts: List[SemanticFact], usage: UsageStats
    ) -> ConsolidationResult:
        facts = list(existing_facts)
        trace: List[Dict[str, Any]] = []
        ts = max((e.timestamp for e in episodes), default=0)
        await self._amerge_extractions(episodes, facts, await self._aextract(episodes, usage), ts, usage, trace)
        self._batches += 1
        if self._batches % self.reflect_every == 0:
            recent = [f for f in facts if f.is_active and not str(f.derived_by).endswith("insight")][-20:]
            if len(recent) >= 3:
                numbered = "\n".join(f"{i}. {f.statement}" for i, f in enumerate(recent))
                insights = _coerce_insights(
                    await self.llm.acomplete_json(_REFLECT_SYSTEM, "Facts:\n" + numbered, usage)
                )
                self._store_insights(insights, recent, facts, ts, trace)
        return ConsolidationResult(strategy=self.name, facts=facts, usage=usage, op_trace=trace)
