"""Trivial baseline MemoryProviders + a stub answerer.

Two providers are shipped on purpose:

- ``LexicalProvider`` — naive: rank by lexical overlap only. Ignores time, so it
  leaks future values on "as of T" queries and does not suppress stale memories.
- ``RecencyDecayProvider`` — same lexical core PLUS ``as_of`` filtering (never
  return a memory newer than the query time) and exponential recency decay that
  down-ranks stale, low-salience memories.

Shipping both proves two skill requirements at once: (§3) a second adapter drops
in behind the ``MemoryProvider`` protocol with zero runner/metric changes, and
(§4) the metrics measurably SEPARATE a naive provider from a better one — the
whole point of an eval harness. Neither is production memory; they exist to
exercise the harness and to be the reference SM-R7/R8's real providers beat.
"""

from __future__ import annotations

import re
from typing import Callable, Dict, List, Optional, Sequence

from harness.provider import Answerer, MemoryEvent, MemoryProvider, RetrievedMemory

_WORD_RE = re.compile(r"[a-z0-9]+")
_STOP = {"a", "an", "the", "is", "are", "of", "to", "in", "on", "at", "and", "or", "i", "s"}


def _tokens(text: str) -> set:
    return {w for w in _WORD_RE.findall(text.lower()) if w not in _STOP}


def _overlap(query_tokens: set, text: str) -> float:
    """Fraction of query content tokens present in the memory text (in [0, 1])."""
    if not query_tokens:
        return 0.0
    tt = _tokens(text)
    return len(query_tokens & tt) / len(query_tokens)


class _BaseLexical:
    """Shared in-memory store + lexical scoring. Subclasses set the ranking policy."""

    name = "base-lexical"

    def __init__(self) -> None:
        self._events: List[MemoryEvent] = []

    def write(self, events: Sequence[MemoryEvent]) -> None:
        self._events.extend(events)

    async def awrite(self, events: Sequence[MemoryEvent]) -> None:
        self.write(events)

    def consolidate(self, tiers: Optional[Sequence[str]] = None) -> None:
        # Baselines do not consolidate; the seam exists so SM-R7's provider can.
        # Param is ``tiers`` to match R11's consolidate(scope, tiers).
        return None

    async def aconsolidate(self, tiers: Optional[Sequence[str]] = None) -> None:
        return None

    def _score(self, event: MemoryEvent, base: float, now: Optional[int]) -> float:
        raise NotImplementedError

    def read(
        self, query: str, *, k: int, as_of: Optional[int] = None, types: Optional[Sequence[str]] = None
    ) -> List[RetrievedMemory]:
        qt = _tokens(query)
        scored: List[RetrievedMemory] = []
        for ev in self._events:
            if as_of is not None and ev.timestamp > as_of and self._respects_as_of:
                continue
            base = _overlap(qt, ev.text)
            if base <= 0:
                continue
            score = self._score(ev, base, as_of)
            scored.append(RetrievedMemory(source_ids=[ev.event_id], text=ev.text, score=score, timestamp=ev.timestamp))
        scored.sort(key=lambda m: m.score, reverse=True)
        return scored[:k]

    async def aread(
        self, query: str, *, k: int, as_of: Optional[int] = None, types: Optional[Sequence[str]] = None
    ) -> List[RetrievedMemory]:
        return self.read(query, k=k, as_of=as_of, types=types)

    _respects_as_of = False


class LexicalProvider(_BaseLexical):
    """Naive: pure lexical overlap, no notion of time. Leaks future / stale info."""

    name = "lexical"
    _respects_as_of = False

    def _score(self, event: MemoryEvent, base: float, now: Optional[int]) -> float:
        return base


class RecencyDecayProvider(_BaseLexical):
    """Lexical + as_of filtering + exponential recency decay (half-life in days).

    score = overlap * 2 ** (-age_days / half_life), where age is measured from the
    newest memory not exceeding ``as_of`` (or the newest overall). This down-ranks
    stale, low-salience memories (decay) and, with ``_respects_as_of``, never
    returns a memory newer than the query time (bi-temporal "true at T").

    NON-PRODUCTION CLOCK — DO NOT COPY AS SM-R5's DECAY. This baseline ages memories
    from **creation time** (``event.timestamp``). SM-R5's ratified formula ages from
    **last access**, reset on confirmed access — this is the exact clock-choice bug
    SM-R5's own review caught (SM-R5 DECISION.md §2.1: honest decay-resistance 0.362
    not 0.787, and §"age_since_access = now - last_accessed_at  # reset on confirmed
    access"). It is deliberately NOT reproduced here: this provider exists only to
    exercise the harness's decay metric with a trivial, stateless ranker, not to be
    SM-R5's provider. A real SM-R5 decay provider (last_accessed_at + reset-on-access
    + importance) drops in behind the ``MemoryProvider`` protocol unchanged.
    """

    name = "recency_decay"
    _respects_as_of = True

    def __init__(self, half_life_days: float = 14.0) -> None:
        super().__init__()
        self.half_life_s = half_life_days * 86400.0
        self._ref: int = 0

    def _reference_time(self, as_of: Optional[int]) -> int:
        candidates = [e.timestamp for e in self._events if as_of is None or e.timestamp <= as_of]
        return max(candidates) if candidates else 0

    def read(
        self, query: str, *, k: int, as_of: Optional[int] = None, types: Optional[Sequence[str]] = None
    ) -> List[RetrievedMemory]:
        self._ref = self._reference_time(as_of)
        return super().read(query, k=k, as_of=as_of, types=types)

    def _score(self, event: MemoryEvent, base: float, now: Optional[int]) -> float:
        # age from CREATION (non-production; SM-R5 uses now - last_accessed_at).
        age = max(0, self._ref - event.timestamp)
        return base * (2.0 ** (-age / self.half_life_s))


class StubAnswerer:
    """Offline answerer: returns the top-``n`` retrieved memory texts (default 1).

    Using the single top memory makes RANK quality flow into QA-correctness (a
    joined top-k would mask a mis-ranked answer). QA-correctness is then a lexical
    proxy for "did retrieval put the gold answer first". The real LLM answerer
    (Phase 4) replaces this behind the same ``Answerer`` protocol; the metric
    definitions do not change.
    """

    name = "stub"

    def __init__(self, top_n: int = 1) -> None:
        self.top_n = top_n

    def answer(self, question: str, memories: Sequence[RetrievedMemory]) -> str:
        if not memories:
            return "I don't know; nothing relevant was found in memory."
        return " ".join(m.text for m in memories[: self.top_n])

    async def aanswer(self, question: str, memories: Sequence[RetrievedMemory]) -> str:
        return self.answer(question, memories)


# Registry: name -> zero-arg factory. Adding a provider is one entry.
PROVIDERS: Dict[str, Callable[[], MemoryProvider]] = {
    "lexical": LexicalProvider,
    "recency_decay": RecencyDecayProvider,
}


def get_provider(name: str) -> MemoryProvider:
    if name not in PROVIDERS:
        raise KeyError(f"unknown provider {name!r}; choices: {sorted(PROVIDERS)}")
    return PROVIDERS[name]()


def get_answerer(name: str = "stub") -> Answerer:
    if name != "stub":
        raise KeyError(f"unknown answerer {name!r}; only 'stub' exists offline (LLM answerer in Phase 4)")
    return StubAnswerer()
