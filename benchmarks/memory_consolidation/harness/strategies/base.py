"""Strategy base class + shared extract/merge primitives.

The primitives here are intentionally small and reusable so the five strategies
differ only in *policy* (when to reflect, when to summarize, how aggressively to
merge), not in plumbing. That keeps the comparison fair.

Retrieval of "candidate existing facts" for a merge decision uses a cheap
lexical similarity (token Jaccard) rather than embeddings, so the harness runs
fully offline and deterministically. Semantic retrieval is an SM-R3 (store)
concern; swapping it in later does not change the strategy policies measured here.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from textwrap import dedent
from typing import Any, Dict, List, Tuple

from harness.llm import LLMClient
from harness.schemas import ConsolidationResult, Episode, MergeOp, SemanticFact, UsageStats

_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set:
    return set(_WORD_RE.findall(text.lower()))


def lexical_similarity(a: str, b: str) -> float:
    """Token Jaccard in [0, 1]. Cheap stand-in for embedding similarity."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def top_similar(
    statement: str,
    facts: List[SemanticFact],
    k: int = 5,
    threshold: float = 0.05,
) -> List[Tuple[SemanticFact, float]]:
    """Return up to k active facts most lexically similar to ``statement``."""
    scored = [(f, lexical_similarity(statement, f.statement)) for f in facts if f.is_active]
    scored = [(f, s) for f, s in scored if s >= threshold]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:k]


_EXTRACT_SYSTEM = dedent(
    """\
    You extract durable, atomic facts about a user from episodic memories
    (things the user did or said). Each fact is a brief third-person statement
    that would still be useful weeks later.

    Rules:
    - One fact per idea; keep them atomic. Split compound statements.
    - Only facts grounded in the episodes. Do not invent or generalize wildly.
    - Skip one-off trivia and generic world knowledge.
    - Capture preferences, traits, goals, relationships, stable circumstances,
      and explicit changes ("no longer ...", "switched to ...").

    Return ONLY a JSON array of objects, each:
      {"statement": str, "source_indices": [int, ...], "topics": [str, ...]}
    where source_indices are the 0-based indices of the episodes that support it.
    Return [] if nothing is worth keeping.
    """
)

_MERGE_SYSTEM = dedent(
    """\
    You maintain a user's semantic memory. Given a NEW candidate fact and the
    most similar EXISTING facts (each shown with a number), choose exactly one
    operation:

    - ADD:    the candidate is net-new information.
    - UPDATE: the candidate refines/augments/replaces an existing fact (e.g. a
              changed preference such as moving city, a new job, switching a
              habit). Give the target number. Preserve what changed.
    - DELETE: the candidate says an existing fact is no longer true / should be
              forgotten. Give the target number.
    - NOOP:   the candidate is already fully captured. Change nothing (optionally
              give the target number of the fact that already covers it).

    A changed preference or circumstance is an UPDATE of the existing fact, not an
    ADD of a second contradictory fact. Prefer NOOP/UPDATE over ADD when an
    existing fact covers the same topic, to avoid duplicates and contradictions.

    Return ONLY a JSON object:
      {"op": "ADD|UPDATE|DELETE|NOOP",
       "target_index": int or null,
       "statement": str}
    where target_index refers to the numbered EXISTING facts and statement is the
    final text to store (for ADD/UPDATE).
    """
)


class ConsolidationStrategy(ABC):
    """Base class. Subclasses implement ``consolidate`` and ``aconsolidate``.

    A strategy is called once per episode *batch* (typically a session). It
    receives the facts accumulated so far and returns the updated fact set plus
    an operation trace. Strategies are stateless across calls except through the
    ``existing_facts`` they are handed — the runner owns the running store.
    """

    name: str = "base"

    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    @abstractmethod
    def consolidate(
        self,
        episodes: List[Episode],
        existing_facts: List[SemanticFact],
        usage: UsageStats,
    ) -> ConsolidationResult:
        raise NotImplementedError

    @abstractmethod
    async def aconsolidate(
        self,
        episodes: List[Episode],
        existing_facts: List[SemanticFact],
        usage: UsageStats,
    ) -> ConsolidationResult:
        raise NotImplementedError

    # ------------------------------------------------------------------ #
    # Shared primitives
    # ------------------------------------------------------------------ #
    @staticmethod
    def _render_episodes(episodes: List[Episode]) -> str:
        return "\n".join(f"[{i}] {e.text}" for i, e in enumerate(episodes))

    def _extract(self, episodes: List[Episode], usage: UsageStats) -> List[Dict[str, Any]]:
        """LLM fact extraction from a batch of episodes (sync)."""
        user = "Episodes:\n" + self._render_episodes(episodes)
        data = self.llm.complete_json(_EXTRACT_SYSTEM, user, usage)
        return _coerce_extractions(data)

    async def _aextract(self, episodes: List[Episode], usage: UsageStats) -> List[Dict[str, Any]]:
        user = "Episodes:\n" + self._render_episodes(episodes)
        data = await self.llm.acomplete_json(_EXTRACT_SYSTEM, user, usage)
        return _coerce_extractions(data)

    def _merge_decision(self, candidate: str, neighbors: List[SemanticFact], usage: UsageStats) -> Dict[str, Any]:
        """Ask the LLM for ADD/UPDATE/DELETE/NOOP against similar facts (sync).

        Neighbors are referenced by index in the prompt (not by fact id) so the
        prompt — and therefore the record/replay cache key — is deterministic and
        independent of randomly generated ids.
        """
        if not neighbors:
            return {"op": "ADD", "target_id": None, "statement": candidate}
        user = _merge_user_prompt(candidate, neighbors)
        data = self.llm.complete_json(_MERGE_SYSTEM, user, usage)
        return _coerce_merge(data, candidate, neighbors)

    async def _amerge_decision(
        self, candidate: str, neighbors: List[SemanticFact], usage: UsageStats
    ) -> Dict[str, Any]:
        if not neighbors:
            return {"op": "ADD", "target_id": None, "statement": candidate}
        user = _merge_user_prompt(candidate, neighbors)
        data = await self.llm.acomplete_json(_MERGE_SYSTEM, user, usage)
        return _coerce_merge(data, candidate, neighbors)


# --------------------------------------------------------------------------- #
# Coercion helpers (defend against loose LLM JSON)
# --------------------------------------------------------------------------- #
def _coerce_extractions(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, dict):
        # tolerate {"facts": [...]}
        for key in ("facts", "results", "items"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            data = [data]
    if not isinstance(data, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        statement = str(item.get("statement", "")).strip()
        if not statement:
            continue
        idx = item.get("source_indices", [])
        if isinstance(idx, int):
            idx = [idx]
        if not isinstance(idx, list):
            idx = []
        topics = item.get("topics") or None
        if topics is not None and not isinstance(topics, list):
            topics = [str(topics)]
        out.append({"statement": statement, "source_indices": [int(i) for i in idx if _isint(i)], "topics": topics})
    return out


def _coerce_merge(data: Any, candidate: str, neighbors: List[SemanticFact]) -> Dict[str, Any]:
    """Normalize the LLM merge decision and resolve target_index -> fact id."""
    if not isinstance(data, dict):
        return {"op": "ADD", "target_id": None, "statement": candidate}
    op = str(data.get("op", "ADD")).upper()
    if op not in {o.value for o in MergeOp}:
        op = "ADD"
    idx = data.get("target_index")
    target_id = None
    if _isint(idx):
        i = int(idx)
        if 0 <= i < len(neighbors):
            target_id = neighbors[i].fact_id
    # UPDATE/DELETE without a resolvable target degrades to ADD/NOOP respectively.
    if op in {"UPDATE", "DELETE"} and target_id is None:
        op = "ADD" if op == "UPDATE" else "NOOP"
    statement = str(data.get("statement") or candidate).strip() or candidate
    return {"op": op, "target_id": target_id, "statement": statement}


def _merge_user_prompt(candidate: str, neighbors: List[SemanticFact]) -> str:
    lines = [f"NEW candidate fact:\n{candidate}\n", "EXISTING similar facts:"]
    for i, f in enumerate(neighbors):
        lines.append(f"[{i}] {f.statement}")
    return "\n".join(lines)


def _isint(value: Any) -> bool:
    try:
        int(value)
        return True
    except (TypeError, ValueError):
        return False
