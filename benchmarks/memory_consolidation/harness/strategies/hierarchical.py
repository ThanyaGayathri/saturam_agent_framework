"""S3 — Hierarchical summarization (MemoryBank style).

Policy (two levels):
  - Session level: summarize each batch into one session-summary fact citing all
    of that session's episodes.
  - Global level: rewrite a single rolling global summary from the active session
    summaries. The old global is superseded (bi-temporal), not deleted.

This is the classic lossy-summarization approach the field warns about; we
include it precisely to *measure* the lossiness (QA + conflict metrics) against
the extract/merge family.
"""

from __future__ import annotations

import uuid
from textwrap import dedent
from typing import List

from harness.schemas import ConsolidationResult, Episode, SemanticFact, UsageStats
from harness.strategies.base import ConsolidationStrategy

_SESSION_SYSTEM = dedent(
    """\
    Summarize what these episodes reveal about the user into a compact paragraph
    of durable facts and preferences. Preserve concrete facts; drop chit-chat.
    Return ONLY the summary text.
    """
)

_GLOBAL_SYSTEM = dedent(
    """\
    You maintain a single rolling profile of the user. Given the previous profile
    and new session summaries, rewrite the profile so it stays accurate and
    non-redundant. When something changed, reflect the newest state but keep a
    brief note of what changed. Return ONLY the profile text.
    """
)


class HierarchicalSummaryStrategy(ConsolidationStrategy):
    name = "hierarchical"

    def _summarize_session(self, episodes: List[Episode], usage: UsageStats) -> str:
        user = "Episodes:\n" + self._render_episodes(episodes)
        return self.llm.complete(_SESSION_SYSTEM, user, usage).strip()

    async def _asummarize_session(self, episodes: List[Episode], usage: UsageStats) -> str:
        user = "Episodes:\n" + self._render_episodes(episodes)
        return (await self.llm.acomplete(_SESSION_SYSTEM, user, usage)).strip()

    def _global_prompt(self, facts: List[SemanticFact], session_summary: str) -> str:
        prev = next((f for f in facts if f.is_active and f.derived_by == f"{self.name}:global"), None)
        prev_text = prev.statement if prev else "(none yet)"
        return f"Previous profile:\n{prev_text}\n\nNew session summary:\n{session_summary}"

    def _apply(
        self, facts: List[SemanticFact], episodes: List[Episode], session_summary: str, global_text: str, ts: int
    ):
        trace = []
        source_ids = [e.episode_id for e in episodes]
        sid = f"f_{uuid.uuid4().hex[:12]}"
        facts.append(
            SemanticFact(
                fact_id=sid,
                statement=session_summary,
                source_episode_ids=source_ids,
                user_id=episodes[0].user_id if episodes else "default",
                created_at=ts,
                valid_from=ts,
                derived_by=f"{self.name}:session",
            )
        )
        trace.append({"op": "ADD", "new_id": sid, "kind": "session_summary"})

        # supersede previous global
        all_sources: List[str] = []
        for f in facts:
            if f.is_active and f.derived_by == f"{self.name}:session":
                all_sources.extend(f.source_episode_ids)
        prev = next((f for f in facts if f.is_active and f.derived_by == f"{self.name}:global"), None)
        gid = f"f_{uuid.uuid4().hex[:12]}"
        if prev is not None:
            prev.valid_to = ts
            prev.superseded_by = gid
        facts.append(
            SemanticFact(
                fact_id=gid,
                statement=global_text,
                source_episode_ids=list(dict.fromkeys(all_sources)),
                user_id=episodes[0].user_id if episodes else "default",
                created_at=ts,
                valid_from=ts,
                importance=0.9,
                derived_by=f"{self.name}:global",
            )
        )
        trace.append({"op": "UPDATE" if prev else "ADD", "new_id": gid, "kind": "global_summary"})
        return trace

    def consolidate(
        self, episodes: List[Episode], existing_facts: List[SemanticFact], usage: UsageStats
    ) -> ConsolidationResult:
        facts = list(existing_facts)
        ts = max((e.timestamp for e in episodes), default=0)
        session_summary = self._summarize_session(episodes, usage)
        global_text = self.llm.complete(_GLOBAL_SYSTEM, self._global_prompt(facts, session_summary), usage).strip()
        trace = self._apply(facts, episodes, session_summary, global_text, ts)
        return ConsolidationResult(strategy=self.name, facts=facts, usage=usage, op_trace=trace)

    async def aconsolidate(
        self, episodes: List[Episode], existing_facts: List[SemanticFact], usage: UsageStats
    ) -> ConsolidationResult:
        facts = list(existing_facts)
        ts = max((e.timestamp for e in episodes), default=0)
        session_summary = await self._asummarize_session(episodes, usage)
        global_text = (
            await self.llm.acomplete(_GLOBAL_SYSTEM, self._global_prompt(facts, session_summary), usage)
        ).strip()
        trace = self._apply(facts, episodes, session_summary, global_text, ts)
        return ConsolidationResult(strategy=self.name, facts=facts, usage=usage, op_trace=trace)
