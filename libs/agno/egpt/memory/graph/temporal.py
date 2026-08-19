"""Valid-time extraction for bi-temporal graph edges (SM-G4 #102, spec SM-R10 section 8).

The four-timestamp envelope itself (``t_valid``/``t_invalid``/``created_at``/``expired_at``) and
its point-in-time query semantics (``MemoryQuery.as_of``, ``co_located._valid_at``) already exist
on :class:`~egpt.memory.types.MemoryRecord` and the Neo4j substrate -- SM-G1 (#99) built the
envelope generically for every record shape, episodes included, and SM-C2/SM-C3/SM-V4 wired the
merged read path to filter on it. What is still missing, and what this module supplies, is the
**source**: nothing populates a relation edge's ``t_valid``/``t_invalid`` from what the episode
actually says, so every edge SM-G3's :class:`~egpt.memory.graph.relations.RelationExtractionPipeline`
writes today carries ``t_valid=None`` -- open, matching "always valid", never the dated interval a
sentence like "since Jan 2024" states.

Pluggable, matching :class:`~egpt.memory.graph.relations.RelationExtractor`'s shape: callers depend
on the :class:`ValidTimeExtractor` protocol, not on :class:`RuleBasedValidTimeExtractor` directly.
Only the rule-based extractor ships here -- deterministic and offline, so it is the one a CI run
can exercise without a model call. An LLM-backed extractor is spec'd (SM-R10 section 8) but not
built: it would need a double standing in for a real model response to test at all, which is the
kind of unfalsifiable-by-construction coverage this ticket's scope note rules out. A caller that
wants one can supply any object satisfying the protocol.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Protocol, runtime_checkable

from egpt.utils.dttm import now_epoch_s

__all__ = [
    "RuleBasedValidTimeExtractor",
    "ValidTimeExtractor",
    "ValidTimeSpan",
]

_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


@dataclass
class ValidTimeSpan:
    """The world-validity a piece of content implies, in the field names
    :class:`~egpt.memory.types.MemoryRecord` already carries -- a caller assigns ``t_valid``/
    ``t_invalid`` straight from this without renaming anything.

    ``source`` names how the span was derived: ``"explicit"`` when the content stated a date,
    ``"ingest_fallback"`` when it did not and ``t_valid`` is the caller-supplied ingest time
    instead (spec section 5's "extracted valid-time, else ingest time"). Never ``None``: a span
    with nothing to report is still a decision (fell back), worth keeping on the row it produced
    rather than losing at the call site.
    """

    t_valid: int
    t_invalid: Optional[int] = None
    confidence: float = 0.0
    source: str = "ingest_fallback"
    matched_text: Optional[str] = None


@runtime_checkable
class ValidTimeExtractor(Protocol):
    """Turns a fact's content into the world-validity interval it states, if any.

    One call per episode, not per relation: an episode's relations share the content they were
    read from, so extracting once and reusing the span across every relation in that episode's
    batch is both cheaper and the only shape that makes sense -- two relations from one sentence
    cannot disagree about when that sentence's dating applies.
    """

    def extract(self, content: str, ingest_time: Optional[int] = None) -> ValidTimeSpan:
        """The world-validity ``content`` states, or a fallback span anchored on ``ingest_time``
        (the current instant when omitted) if it states none."""
        ...

    async def aextract(self, content: str, ingest_time: Optional[int] = None) -> ValidTimeSpan:
        """Async variant of :meth:`extract`."""
        ...


def _epoch(year: int, month: int, day: int) -> int:
    return int(datetime(year, month, day, tzinfo=timezone.utc).timestamp())


class RuleBasedValidTimeExtractor:
    """The default :class:`ValidTimeExtractor`: regex + month-name lookup over explicit temporal
    cues, deterministic and offline. Ported from the SM-R10 spec prototype
    (``benchmarks/memory_bitemporal/extraction.py``, measured 6/6 on its committed cases); this is
    the same rule, not a re-derivation of it.

    Handles ``since <Month>``, ``since <Month> <Year>``, ``from ... until ...``, ``in <Year>``, and
    ISO dates (``YYYY-MM-DD``). A bare month with no year resolves against ``ingest_time``'s year,
    so "since Jan" means the most recent January relative to when the fact was recorded, not an
    absolute one.
    """

    _ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
    _SINCE = re.compile(r"\b(?:since|from|as of|starting)\s+([A-Za-z]+|\d{4})(?:\s+(\d{4}))?", re.I)
    _UNTIL = re.compile(r"\b(?:until|till|through|no longer\s+\w+\s+after)\s+([A-Za-z]+|\d{4})(?:\s+(\d{4}))?", re.I)
    _IN_YEAR = re.compile(r"\bin\s+(\d{4})\b", re.I)

    @staticmethod
    def _resolve(token: str, year_token: Optional[str], ref_year: int) -> Optional[int]:
        token = token.strip().lower()
        if token.isdigit() and len(token) == 4:
            return _epoch(int(token), 1, 1)
        if token in _MONTHS:
            year = int(year_token) if year_token else ref_year
            return _epoch(year, _MONTHS[token], 1)
        return None

    def extract(self, content: str, ingest_time: Optional[int] = None) -> ValidTimeSpan:
        ingest = now_epoch_s() if ingest_time is None else ingest_time
        ref_year = datetime.fromtimestamp(ingest, tz=timezone.utc).year

        t_valid: Optional[int] = None
        t_invalid: Optional[int] = None
        matched: Optional[str] = None

        m_iso = self._ISO.search(content)
        if m_iso:
            year, month, day = (int(group) for group in m_iso.groups())
            t_valid = _epoch(year, month, day)
            matched = m_iso.group(0)

        m_since = self._SINCE.search(content)
        if m_since and t_valid is None:
            t_valid = self._resolve(m_since.group(1), m_since.group(2), ref_year)
            if t_valid is not None:
                matched = m_since.group(0)

        m_until = self._UNTIL.search(content)
        if m_until:
            t_invalid = self._resolve(m_until.group(1), m_until.group(2), ref_year)

        m_in = self._IN_YEAR.search(content)
        if m_in and t_valid is None:
            t_valid = _epoch(int(m_in.group(1)), 1, 1)
            matched = m_in.group(0)

        if t_valid is None:
            return ValidTimeSpan(t_valid=ingest, confidence=0.3, source="ingest_fallback")
        return ValidTimeSpan(
            t_valid=t_valid, t_invalid=t_invalid, confidence=0.9, source="explicit", matched_text=matched
        )

    async def aextract(self, content: str, ingest_time: Optional[int] = None) -> ValidTimeSpan:
        """Async variant of :meth:`extract`. The rule-based path does no I/O, so this is a
        synchronous computation wearing an async signature -- required by the protocol, not by
        anything this method does, so every caller can ``await`` a :class:`ValidTimeExtractor`
        without knowing which implementation it holds."""
        return self.extract(content, ingest_time)
