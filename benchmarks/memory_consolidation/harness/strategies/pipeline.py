"""The real ``FactExtractionPipeline`` as a harness strategy (aicoe-tracker#194 item 2).

ADR 0003 section A.7 ratifies five consolidation metrics. SM-L7 (#128) measures
``provenance_validity == 1.0`` against the real pipeline in a unit-level quality gate; the other
four -- ``provenance_precision >= 0.80``, ``conflict_accuracy >= 0.90``, ``dedup_score >= 0.75``,
``fact_recall >= 0.95`` -- need a labelled corpus with gold target facts, which
``benchmarks/memory_consolidation``'s synthetic dataset already provides. What was missing is the
subject: ``harness/strategies/`` wrapped only prototype strategies, so the four measurable metrics
were being measured against something other than the code that ships.

This module closes that -- it supplies the *subject* for measurement, not proof that it meets A.7's
ratified thresholds (round-2 review). The four ``@pytest.mark.xfail`` threshold tests in
``libs/agno/tests/unit/memory/test_consolidation_quality_gate.py`` are unchanged by this module: the
metric *functions* now return real numbers when handed this strategy's output instead of a
prototype's, but establishing whether any of them clears its ratified threshold needs a populated
replay cache (``results/llm_cache/`` is empty today) and is not claimed here.

It is deliberately **not** a reimplementation of the pipeline's logic --
the point of the exercise is to measure `FactExtractionPipeline` itself, so a strategy that
re-derived extraction or merge decisions here would report numbers about the harness rather than
about the product. It supplies only what the pipeline needs from its environment:

* ``_HarnessFactExtractor`` / ``_HarnessMergeDecider`` -- the two ports (``FactExtractor``,
  ``MergeDecider``) backed by the harness's own ``LLMClient`` rather than a ``Model``, so every
  call goes through the record/replay cache and a re-run is deterministic and free. They send the
  pipeline's **own** prompt constants (``_EXTRACT_SYSTEM``/``_MERGE_SYSTEM``), not paraphrases:
  a prompt that drifted from the shipped one would make every metric here measure a system nobody
  runs.
* ``_HarnessVector`` -- the read-only ``search`` leg the pipeline retrieves neighbours through,
  over the runner's own ``existing_facts`` list. In-memory and unranked-by-embedding: the harness
  has no embedder, and substituting one would put a second uncontrolled variable into a
  measurement of merge quality. Nearest-neighbour selection here is lexical overlap, which is
  weaker than production retrieval -- so ``dedup_score``/``conflict_accuracy`` measured through
  this adapter are a **lower bound** on the shipped system, not an estimate of it. That asymmetry
  is recorded rather than hidden because it decides how the numbers may be read.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from egpt.memory.consolidation.extract import (
    _EXTRACT_SYSTEM,
    _MERGE_SYSTEM,
    FactCandidate,
    FactExtractionPipeline,
    MergeDecision,
)
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import MemoryRecord, Scope

from harness.schemas import ConsolidationResult, Episode, SemanticFact, UsageStats
from harness.strategies.base import ConsolidationStrategy

_SCOPE = Scope(tenant_id="benchmark", user_id="benchmark-user")


def _record(fact: SemanticFact) -> MemoryRecord:
    """One harness fact as the ``MemoryRecord`` the pipeline's retrieval leg returns.

    Carries ``fact.is_active`` onto ``expired_at`` -- a non-``None`` transaction-time stamp is what
    the rest of the seam (and ``_HarnessVector.search``, round-2 review) reads to tell a retired
    fact from a live one; without it, retirement status is lost the moment a ``SemanticFact``
    becomes a ``MemoryRecord`` and nothing downstream could filter on it even after ``_apply``
    started stamping it correctly.
    """
    return MemoryRecord(
        id=fact.fact_id,
        tier=MemoryTier.SEMANTIC,
        content=fact.statement,
        scope=_SCOPE,
        # `is_active` is the authority, not `valid_to`/`superseded_by` individually -- either one
        # alone already means retired, and a synthetic non-``None`` transaction-time stamp is what a
        # filter needs to see (whatever `valid_to` itself holds, since it is valid time, not
        # transaction time, and can be `None` even for a superseded-by-only retirement).
        expired_at=None if fact.is_active else (fact.valid_to if fact.valid_to is not None else fact.created_at),
        superseded_by=fact.superseded_by,
    )


class _HarnessVector:
    """The read-only ``search`` leg ``FactExtractionPipeline`` retrieves neighbours through.

    Lexical overlap, not embeddings -- see the module docstring for why, and for what that means
    when reading the resulting numbers. ``upsert``/``scan`` exist because the port names them; the
    pipeline is documented read-only against this substrate and the harness runner owns the store,
    so writing here would give the pipeline a second, divergent copy of the fact set.
    """

    def __init__(self, facts: Sequence[SemanticFact]) -> None:
        self._records = [_record(f) for f in facts]

    @staticmethod
    def _overlap(a: str, b: str) -> int:
        return len(set(a.lower().split()) & set(b.lower().split()))

    def search(self, scope: Scope, query: Any, tier: Optional[MemoryTier] = None) -> List[MemoryRecord]:
        """Current belief only (``expired_at IS NULL``), like every real ``VectorSubstrate.search``
        -- ``extract.py``'s own bolded contract, restated there because this is the one caller in the
        tree that hands a search result straight to an LLM decision with no check of its own
        (round-2 review: without this filter, once retirement round-trips correctly through
        ``_apply``/``_record``, the pipeline would be handed already-retired facts as merge
        candidates and could "update" a row it had itself just retired)."""
        text = getattr(query, "text", "") or ""
        limit = getattr(query, "limit", None) or 5
        current = [r for r in self._records if r.expired_at is None]
        ranked = sorted(current, key=lambda r: (-self._overlap(text, r.content or ""), r.id))
        return ranked[:limit]

    async def asearch(self, scope: Scope, query: Any, tier: Optional[MemoryTier] = None) -> List[MemoryRecord]:
        return self.search(scope, query, tier)

    def upsert(self, scope: Scope, records: Sequence[MemoryRecord]) -> None:
        return None

    async def aupsert(self, scope: Scope, records: Sequence[MemoryRecord]) -> None:
        return None

    def scan(
        self, scope: Scope, tier: MemoryTier, limit: Optional[int] = None, after: Any = None
    ) -> List[MemoryRecord]:
        return list(self._records[: limit if limit is not None else len(self._records)])

    async def ascan(
        self, scope: Scope, tier: MemoryTier, limit: Optional[int] = None, after: Any = None
    ) -> List[MemoryRecord]:
        return self.scan(scope, tier, limit, after)

    def get_record(self, scope: Scope, id: str) -> Optional[MemoryRecord]:
        return next((r for r in self._records if r.id == id), None)

    async def aget_record(self, scope: Scope, id: str) -> Optional[MemoryRecord]:
        return self.get_record(scope, id)


class _HarnessFactExtractor:
    """``FactExtractor`` over the harness ``LLMClient``, sending the pipeline's own prompt."""

    def __init__(self, llm: Any, usage: UsageStats) -> None:
        self.llm = llm
        self.usage = usage

    @staticmethod
    def _user(episodes: Sequence[MemoryRecord]) -> str:
        rendered = "\n".join(f"[{i}] {episode.content}" for i, episode in enumerate(episodes))
        return f"Episodes:\n{rendered}"

    @staticmethod
    def _parse(payload: Any) -> List[FactCandidate]:
        """Accepts ``{"facts": [...]}`` or a bare top-level list -- mirroring ``strategies/base.py``'s
        own ``_coerce_extractions`` -- and raises on anything else, rather than silently returning no
        facts (round-2 review). A model answer that is a string, or a dict with neither shape, is not
        readable as "zero facts extracted"; the two produce different metrics (``fact_recall == 0.0``
        from an empty extraction reads as a model-quality result, not a parsing bug that should be
        visible), the same distinction ``LLMFactExtractor._parse`` draws in ``extract.py``.
        """
        if isinstance(payload, dict):
            raw = payload.get("facts")
            if raw is None:
                raise RuntimeError(
                    f"Extractor response is a dict with no 'facts' key: {sorted(payload.keys())!r}. An "
                    "unreadable answer is not an episode set with no facts."
                )
        elif isinstance(payload, list):
            raw = payload
        else:
            raise RuntimeError(
                f"Extractor response is neither a dict nor a list ({type(payload).__name__}). An "
                "unreadable answer is not an episode set with no facts."
            )
        if not isinstance(raw, list):
            raise RuntimeError(f"Extractor response's 'facts' value is not a list ({type(raw).__name__}).")
        out: List[FactCandidate] = []
        for item in raw:
            if not isinstance(item, dict) or not item.get("statement"):
                continue
            out.append(
                FactCandidate(
                    statement=str(item["statement"]),
                    source_indices=[int(i) for i in item.get("source_indices", []) if isinstance(i, int)],
                    topics=item.get("topics"),
                )
            )
        return out

    def extract(self, episodes: Sequence[MemoryRecord]) -> Sequence[FactCandidate]:
        return self._parse(self.llm.complete_json(_EXTRACT_SYSTEM, self._user(episodes), self.usage))

    async def aextract(self, episodes: Sequence[MemoryRecord]) -> Sequence[FactCandidate]:
        return self._parse(await self.llm.acomplete_json(_EXTRACT_SYSTEM, self._user(episodes), self.usage))


class _HarnessMergeDecider:
    """``MergeDecider`` over the harness ``LLMClient``, sending the pipeline's own prompt."""

    def __init__(self, llm: Any, usage: UsageStats) -> None:
        self.llm = llm
        self.usage = usage

    @staticmethod
    def _user(statement: str, neighbors: Sequence[MemoryRecord]) -> str:
        lines = [f"NEW candidate fact:\n{statement}\n", "EXISTING similar facts:"]
        lines.extend(f"[{i}] {fact.content}" for i, fact in enumerate(neighbors))
        return "\n".join(lines)

    @staticmethod
    def _parse(payload: Any) -> MergeDecision:
        data: Dict[str, Any] = payload if isinstance(payload, dict) else {}
        return MergeDecision(
            op=str(data.get("op", "NOOP")).upper(),
            statement=str(data.get("statement", "") or ""),
            target_index=data.get("target_index"),
        )

    def decide(self, statement: str, neighbors: Sequence[MemoryRecord]) -> MergeDecision:
        return self._parse(self.llm.complete_json(_MERGE_SYSTEM, self._user(statement, neighbors), self.usage))

    async def adecide(self, statement: str, neighbors: Sequence[MemoryRecord]) -> MergeDecision:
        return self._parse(
            await self.llm.acomplete_json(_MERGE_SYSTEM, self._user(statement, neighbors), self.usage)
        )


class PipelineStrategy(ConsolidationStrategy):
    """``FactExtractionPipeline`` itself, measured on the harness's labelled corpus."""

    name = "pipeline"
    metric_caveat = (
        "dedup_score/conflict_accuracy measured through this strategy are a lower bound on the shipped "
        "system, not an estimate of it: _HarnessVector ranks candidates by lexical overlap, not "
        "embeddings, since the harness has no embedder and substituting one would put a second "
        "uncontrolled variable into a measurement of merge quality."
    )

    def _episode_records(self, episodes: List[Episode]) -> List[MemoryRecord]:
        return [
            MemoryRecord(id=e.episode_id, tier=MemoryTier.EPISODIC, content=e.text, scope=_SCOPE)
            for e in episodes
        ]

    def _pipeline(self, existing_facts: List[SemanticFact], usage: UsageStats) -> FactExtractionPipeline:
        return FactExtractionPipeline(
            extractor=_HarnessFactExtractor(self.llm, usage),
            decider=_HarnessMergeDecider(self.llm, usage),
            vector=_HarnessVector(existing_facts),
        )

    def _apply(
        self,
        writes: Sequence[Any],
        episodes: List[Episode],
        existing_facts: List[SemanticFact],
        usage: UsageStats,
    ) -> ConsolidationResult:
        """Fold the pipeline's ``FactWrite`` batch into the runner's fact-set + op-trace shape.

        ``source_episode_ids`` is carried through verbatim rather than recomputed -- it is the
        field ``provenance_report`` reads, so a harness that re-derived it would measure its own
        bookkeeping instead of the pipeline's.

        **Retirement writes are carried through, not folded in as ordinary facts** (round-2 review).
        ``FactExtractionPipeline`` emits *two* ``FactWrite``s for an UPDATE/DELETE: the new row, and a
        retired copy of the target with ``record.expired_at`` (transaction time) and, for UPDATE only,
        ``record.superseded_by`` stamped (``extract.py``'s ``_apply_decision``).
        ``record.expired_at is not None`` is the canonical way to tell a retirement write from a new
        one (``extract.py``'s own docstring), used here rather than ``record.id in by_id`` -- the
        retired copy's id is *already* in ``by_id`` (it is the target), so that check would
        misclassify a DELETE's retirement as an ``UPDATE`` too.

        ``SemanticFact.valid_to`` is this harness's valid-time field ("when it stopped being true"),
        not a mirror of ``MemoryRecord.expired_at`` (transaction time) -- but neither retirement path
        stamps ``record.t_invalid`` (DELETE leaves the target's original, usually still ``None``;
        UPDATE's retired copy does too), so ``record.expired_at`` is the only signal either path
        actually sets. Mapping it onto ``valid_to`` is what flips ``SemanticFact.is_active``
        (``valid_to is None and superseded_by is None``) to ``False`` for a DELETE, whose retired copy
        carries no ``superseded_by`` on its own. Without this, every retirement round-trips back in as
        an ordinary active fact and ``conflict_accuracy`` -- which reads ``is_active``, never the
        ``op_trace`` this method separately builds -- measures nothing real.
        """
        by_id = {f.fact_id: f for f in existing_facts}
        timestamp = max((e.timestamp for e in episodes), default=0)
        user_id = episodes[0].user_id if episodes else "default"
        trace: List[Dict[str, Any]] = []
        for write in writes:
            record = write.record
            source_ids = list(getattr(write, "source_episode_ids", []) or [])
            retired = record.expired_at is not None
            if retired:
                op = "DELETE" if record.superseded_by is None else "UPDATE"
            else:
                op = "UPDATE" if record.id in by_id else "ADD"
            by_id[record.id] = SemanticFact(
                fact_id=record.id,
                statement=record.content or "",
                source_episode_ids=source_ids,
                user_id=user_id,
                created_at=timestamp,
                valid_from=timestamp,
                valid_to=record.expired_at if retired else None,
                superseded_by=record.superseded_by if retired else None,
                derived_by=self.name,
            )
            trace.append({"op": op, "new_id": record.id, "statement": record.content or ""})
        return ConsolidationResult(strategy=self.name, facts=list(by_id.values()), usage=usage, op_trace=trace)

    def consolidate(
        self, episodes: List[Episode], existing_facts: List[SemanticFact], usage: UsageStats
    ) -> ConsolidationResult:
        writes, _report = self._pipeline(existing_facts, usage).distil(_SCOPE, self._episode_records(episodes))
        return self._apply(writes, episodes, existing_facts, usage)

    async def aconsolidate(
        self, episodes: List[Episode], existing_facts: List[SemanticFact], usage: UsageStats
    ) -> ConsolidationResult:
        pipeline = self._pipeline(existing_facts, usage)
        writes, _report = await pipeline.adistil(_SCOPE, self._episode_records(episodes))
        return self._apply(writes, episodes, existing_facts, usage)
