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
from typing import Literal, Optional, Protocol, Tuple, runtime_checkable

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


@dataclass(frozen=True)
class ValidTimeSpan:
    """The world-validity a piece of content implies, in the field names
    :class:`~egpt.memory.types.MemoryRecord` already carries -- a caller assigns ``t_valid``/
    ``t_invalid`` straight from this without renaming anything.

    ``source`` names how the span was derived: ``"explicit"`` when the content stated a date,
    ``"ingest_fallback"`` when it did not and ``t_valid`` is the caller-supplied ingest time
    instead (spec section 5's "extracted valid-time, else ingest time"). Never ``None``: a span
    with nothing to report is still a decision (fell back), worth keeping on the row it produced
    rather than losing at the call site.

    Frozen: one span is shared across every relation extracted from one episode (see
    :class:`ValidTimeExtractor`'s docstring), and a caller mutating its copy would silently affect
    every relation still pointing at that instance.
    """

    t_valid: int
    t_invalid: Optional[int] = None
    confidence: float = 0.0
    source: Literal["explicit", "ingest_fallback"] = "ingest_fallback"
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
    (``benchmarks/memory_bitemporal/extraction.py``, measured 6/6 on its committed cases), then
    hardened past three defects that prototype's narrower corpus never exercised (round-1 review on
    PR #101): an out-of-range calendar date crashing instead of falling through, a bare "until"
    clause with no matching start losing its bound in the fallback return, and an ISO date inside
    an "until" clause being mis-scoped as the start rather than the end.

    Handles ``since <Month|Year|ISO date>``, ``from ... until ...``, ``in <Year>``, and standalone
    ISO dates (``YYYY-MM-DD``). A bare month with no year resolves against ``ingest_time``'s year,
    so "since Jan" means the most recent January relative to when the fact was recorded, not an
    absolute one.

    **Not exhaustive prose coverage.** Content phrased outside these four patterns falls back to
    ingest time rather than misparsing -- a deliberate simplification for a rule-based default, not
    a bug (an LLM-backed extractor is the documented escape hatch, see the module docstring).
    """

    _ISO_TOKEN = r"\d{4}-\d{2}-\d{2}"
    _ISO = re.compile(r"\b(" + _ISO_TOKEN + r")\b")
    _SINCE = re.compile(
        r"\b(?:since|from|as of|starting)\s+([A-Za-z]+\b|" + _ISO_TOKEN + r"|\d{4}\b)(?:\s+(\d{4})\b)?", re.I
    )
    _UNTIL = re.compile(
        r"\b(?:until|till|through|no longer\s+\w+\s+after)\s+([A-Za-z]+\b|"
        + _ISO_TOKEN
        + r"|\d{4}\b)(?:\s+(\d{4})\b)?",
        re.I,
    )
    _IN_YEAR = re.compile(r"\bin\s+(\d{4})\b", re.I)

    @staticmethod
    def _resolve(token: str, year_token: Optional[str], ref_year: int) -> Optional[int]:
        """A month name, a bare year, or an ISO date -> epoch seconds, or ``None`` for a token this
        extractor does not recognise **or** one that names a calendar date that does not exist
        (e.g. ``2024-13-45``) -- ``None`` either way, so a caller cannot tell "unrecognised" from
        "invalid" and does not need to: both mean "not a usable cue", handled identically by
        :meth:`extract` falling through to the next pattern or the ingest-time fallback."""
        token = token.strip().lower()
        iso_match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", token)
        try:
            if iso_match:
                return _epoch(*(int(group) for group in iso_match.groups()))
            if token.isdigit() and len(token) == 4:
                return _epoch(int(token), 1, 1)
            if token in _MONTHS:
                year = int(year_token) if year_token else ref_year
                return _epoch(year, _MONTHS[token], 1)
        except ValueError:
            # A regex-shaped token that is not a real calendar date -- "2024-13-45", or a bare
            # "0000" (year 0 is out of `datetime`'s range) -- not a usable cue, same as no match.
            return None
        return None

    @staticmethod
    def _overlaps(a: Tuple[int, int], b: Optional[Tuple[int, int]]) -> bool:
        return b is not None and a[0] < b[1] and b[0] < a[1]

    def extract(self, content: str, ingest_time: Optional[int] = None) -> ValidTimeSpan:
        ingest = now_epoch_s() if ingest_time is None else ingest_time
        ref_year = datetime.fromtimestamp(ingest, tz=timezone.utc).year

        t_valid: Optional[int] = None
        t_invalid: Optional[int] = None
        matched: Optional[str] = None

        # "until"/"till"/etc. resolved first, and its own match span recorded, so a standalone ISO
        # check below can tell an end-date consumed here apart from an unrelated start-date
        # elsewhere in the same sentence ("valid until 2024-09-01" must not also read as a
        # standalone ISO start date -- that was finding #4 on PR #101 round 1).
        m_until = self._UNTIL.search(content)
        until_span = m_until.span() if m_until else None
        if m_until:
            t_invalid = self._resolve(m_until.group(1), m_until.group(2), ref_year)

        m_iso = self._ISO.search(content)
        if m_iso and not self._overlaps(m_iso.span(), until_span):
            t_valid = self._resolve(m_iso.group(1), None, ref_year)
            if t_valid is not None:
                matched = m_iso.group(0)

        m_since = self._SINCE.search(content)
        if m_since and t_valid is None:
            t_valid = self._resolve(m_since.group(1), m_since.group(2), ref_year)
            if t_valid is not None:
                matched = m_since.group(0)

        m_in = self._IN_YEAR.search(content)
        if m_in and t_valid is None:
            resolved = self._resolve(m_in.group(1), None, ref_year)
            if resolved is not None:
                t_valid = resolved
                matched = m_in.group(0)

        explicit = t_valid is not None
        resolved_t_valid: int = t_valid if t_valid is not None else ingest

        # Half-open valid time (t_valid <= at < t_invalid, per MemoryRecord's own guard) admits no
        # instant when inverted or degenerate -- reached not only through a self-contradictory
        # "since X until Y" with Y before X, but also through the fallback branch just above: an
        # extracted end date that predates the ingest-time start it just fell back to inverts the
        # same way. Dropping the unusable bound here, once, after both t_valid paths have run,
        # is what keeps one badly-dated episode from crashing the whole extraction pass instead of
        # degrading to "no end date extracted" for that one edge (finding #3, PR #101 round 1).
        if t_invalid is not None and t_invalid <= resolved_t_valid:
            t_invalid = None

        if explicit:
            return ValidTimeSpan(
                t_valid=resolved_t_valid,
                t_invalid=t_invalid,
                confidence=0.9,
                source="explicit",
                matched_text=matched,
            )
        return ValidTimeSpan(t_valid=resolved_t_valid, t_invalid=t_invalid, confidence=0.3, source="ingest_fallback")

    async def aextract(self, content: str, ingest_time: Optional[int] = None) -> ValidTimeSpan:
        """Async variant of :meth:`extract`. The rule-based path does no I/O, so this is a
        synchronous computation wearing an async signature -- required by the protocol, not by
        anything this method does, so every caller can ``await`` a :class:`ValidTimeExtractor`
        without knowing which implementation it holds."""
        return self.extract(content, ingest_time)
