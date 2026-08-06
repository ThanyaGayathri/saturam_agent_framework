"""S2 — Generative-Agents style reflection + importance.

Policy:
  1. Extract atomic facts from the batch, each with an importance score.
  2. Store them with lexical dedup (skip near-duplicates; no LLM merge call).
  3. Accumulate importance. When it crosses a threshold, run a *reflection*:
     the LLM synthesizes a few higher-level insights from recent facts, each
     citing the facts (and thus episodes) it was drawn from. Insights are
     stored as high-importance semantic facts with full provenance.

This tests whether importance-gated insight generation beats mem0's per-fact
merge on QA and cost, and how well insight provenance holds up.
"""

from __future__ import annotations

import uuid
from textwrap import dedent
from typing import Any, Dict, List

from harness.llm import LLMClient
from harness.schemas import ConsolidationResult, Episode, SemanticFact, UsageStats
from harness.strategies.base import ConsolidationStrategy, top_similar

_IMPORTANCE_SYSTEM = dedent(
    """\
    Extract durable atomic facts about the user from these episodes, and rate how
    important each is to remember long-term (1 = mundane, 10 = highly defining).

    Return ONLY a JSON array of:
      {"statement": str, "source_indices": [int...], "importance": int, "topics": [str...]}
    Return [] if nothing is worth keeping.
    """
)

_REFLECT_SYSTEM = dedent(
    """\
    You are reflecting on a user's recent memories to form higher-level insights
    (patterns, generalizations, or conclusions) that are not stated directly.

    You are given numbered facts. Produce 1-3 insights. Each insight must cite the
    fact numbers it is drawn from. Do not restate a single fact verbatim; an
    insight connects or generalizes.

    Return ONLY a JSON array of:
      {"insight": str, "source_facts": [int...], "importance": int}
    Return [] if no genuine insight can be drawn.
    """
)


class ReflectionStrategy(ConsolidationStrategy):
    name = "reflection"

    def __init__(self, llm: LLMClient, reflect_threshold: float = 12.0, dedup_threshold: float = 0.6) -> None:
        super().__init__(llm)
        self.reflect_threshold = reflect_threshold
        self.dedup_threshold = dedup_threshold
        self._importance_since_reflection = 0.0

    # ------------------------------------------------------------------ #
    def _extract_scored(self, episodes: List[Episode], usage: UsageStats) -> List[Dict[str, Any]]:
        user = "Episodes:\n" + self._render_episodes(episodes)
        data = self.llm.complete_json(_IMPORTANCE_SYSTEM, user, usage)
        return _coerce_scored(data)

    async def _aextract_scored(self, episodes: List[Episode], usage: UsageStats) -> List[Dict[str, Any]]:
        user = "Episodes:\n" + self._render_episodes(episodes)
        data = await self.llm.acomplete_json(_IMPORTANCE_SYSTEM, user, usage)
        return _coerce_scored(data)

    def _add_with_dedup(
        self,
        facts: List[SemanticFact],
        statement: str,
        source_ids: List[str],
        importance: float,
        topics: Any,
        ts: int,
        trace: List[Dict[str, Any]],
    ) -> None:
        similar = top_similar(statement, facts, k=1, threshold=self.dedup_threshold)
        if similar:
            target = similar[0][0]
            for sid in source_ids:
                if sid not in target.source_episode_ids:
                    target.source_episode_ids.append(sid)
            target.importance = max(target.importance, importance)
            trace.append({"op": "NOOP", "target_id": target.fact_id, "statement": statement})
            return
        fid = f"f_{uuid.uuid4().hex[:12]}"
        facts.append(
            SemanticFact(
                fact_id=fid,
                statement=statement,
                source_episode_ids=list(source_ids),
                topics=topics,
                user_id=facts[0].user_id if facts else "default",
                created_at=ts,
                valid_from=ts,
                importance=importance,
                derived_by=self.name,
            )
        )
        trace.append({"op": "ADD", "new_id": fid, "statement": statement})

    def _reflect(self, facts: List[SemanticFact], usage: UsageStats, ts: int, trace: List[Dict[str, Any]]) -> None:
        recent = [f for f in facts if f.is_active][-20:]
        if len(recent) < 3:
            return
        numbered = "\n".join(f"{i}. {f.statement}" for i, f in enumerate(recent))
        insights = _coerce_insights(self.llm.complete_json(_REFLECT_SYSTEM, "Facts:\n" + numbered, usage))
        self._store_insights(insights, recent, facts, ts, trace)

    async def _areflect(
        self, facts: List[SemanticFact], usage: UsageStats, ts: int, trace: List[Dict[str, Any]]
    ) -> None:
        recent = [f for f in facts if f.is_active][-20:]
        if len(recent) < 3:
            return
        numbered = "\n".join(f"{i}. {f.statement}" for i, f in enumerate(recent))
        insights = _coerce_insights(await self.llm.acomplete_json(_REFLECT_SYSTEM, "Facts:\n" + numbered, usage))
        self._store_insights(insights, recent, facts, ts, trace)

    def _store_insights(
        self,
        insights: List[Dict[str, Any]],
        recent: List[SemanticFact],
        facts: List[SemanticFact],
        ts: int,
        trace: List[Dict[str, Any]],
    ) -> None:
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
        for ex in self._extract_scored(episodes, usage):
            source_ids = [episodes[i].episode_id for i in ex["source_indices"] if 0 <= i < len(episodes)]
            imp = min(1.0, ex["importance"] / 10.0)
            self._add_with_dedup(facts, ex["statement"], source_ids, imp, ex.get("topics"), ts, trace)
            self._importance_since_reflection += ex["importance"]
        if self._importance_since_reflection >= self.reflect_threshold:
            self._reflect(facts, usage, ts, trace)
            self._importance_since_reflection = 0.0
        return ConsolidationResult(strategy=self.name, facts=facts, usage=usage, op_trace=trace)

    async def aconsolidate(
        self, episodes: List[Episode], existing_facts: List[SemanticFact], usage: UsageStats
    ) -> ConsolidationResult:
        facts = list(existing_facts)
        trace: List[Dict[str, Any]] = []
        ts = max((e.timestamp for e in episodes), default=0)
        for ex in await self._aextract_scored(episodes, usage):
            source_ids = [episodes[i].episode_id for i in ex["source_indices"] if 0 <= i < len(episodes)]
            imp = min(1.0, ex["importance"] / 10.0)
            self._add_with_dedup(facts, ex["statement"], source_ids, imp, ex.get("topics"), ts, trace)
            self._importance_since_reflection += ex["importance"]
        if self._importance_since_reflection >= self.reflect_threshold:
            await self._areflect(facts, usage, ts, trace)
            self._importance_since_reflection = 0.0
        return ConsolidationResult(strategy=self.name, facts=facts, usage=usage, op_trace=trace)


# --------------------------------------------------------------------------- #
def _coerce_scored(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, dict):
        for key in ("facts", "results", "items"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            data = [data]
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        statement = str(item.get("statement", "")).strip()
        if not statement:
            continue
        idx = item.get("source_indices", [])
        if isinstance(idx, int):
            idx = [idx]
        importance = item.get("importance", 5)
        try:
            importance = float(importance)
        except (TypeError, ValueError):
            importance = 5.0
        topics = item.get("topics") or None
        out.append(
            {
                "statement": statement,
                "source_indices": [int(i) for i in (idx or []) if _isint(i)],
                "importance": importance,
                "topics": topics if isinstance(topics, list) else None,
            }
        )
    return out


def _coerce_insights(data: Any) -> List[Dict[str, Any]]:
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        insight = str(item.get("insight", "")).strip()
        if not insight:
            continue
        src = item.get("source_facts", [])
        if isinstance(src, int):
            src = [src]
        importance = item.get("importance", 7)
        try:
            importance = float(importance)
        except (TypeError, ValueError):
            importance = 7.0
        out.append(
            {
                "insight": insight,
                "source_facts": [int(i) for i in (src or []) if _isint(i)],
                "importance": importance,
            }
        )
    return out


def _isint(value: Any) -> bool:
    try:
        int(value)
        return True
    except (TypeError, ValueError):
        return False
