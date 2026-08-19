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

#: Confidence assigned when at least one bound came from content vs. neither did. Named rather than
#: inlined at both :meth:`RuleBasedValidTimeExtractor.extract` return points (round-2 review,
#: PR #101) -- keeps their 3x relationship visible and makes future tuning a single-site change.
_EXPLICIT_CONFIDENCE = 0.9
_FALLBACK_CONFIDENCE = 0.3

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

    ``source`` names how the span was derived: ``"explicit"`` when the content stated **either**
    bound, ``"ingest_fallback"`` when it stated neither and ``t_valid`` is the caller-supplied
    ingest time instead (spec section 5's "extracted valid-time, else ingest time"). Never
    ``None``: a span with nothing to report is still a decision (fell back), worth keeping on the
    row it produced rather than losing at the call site.

    Frozen: one span is shared across every relation extracted from one episode (see
    :class:`ValidTimeExtractor`'s docstring), and a caller mutating its copy would silently affect
    every relation still pointing at that instance.
    """

    t_valid: int
    t_invalid: Optional[int] = None
    # Defaults to `_FALLBACK_CONFIDENCE`, not `0.0` (round-3 review, PR #101): a caller building a
    # span without naming `confidence` -- a test stub, most concretely -- gets the honest floor for
    # an unlabelled span rather than a value below every real one `RuleBasedValidTimeExtractor`
    # produces, which would silently mislead a consumer branching on `confidence < _FALLBACK_CONFIDENCE`.
    confidence: float = _FALLBACK_CONFIDENCE
    source: Literal["explicit", "ingest_fallback"] = "ingest_fallback"
    matched_text: Optional[str] = None

    def __post_init__(self) -> None:
        # `t_valid` is the one field every other decision in this module assumes is present --
        # `co_located._valid_at` and `MemoryRecord.__post_init__` both read it unconditionally.
        # The protocol's own docstring promises "never None"; a misbehaving custom
        # `ValidTimeExtractor` is the only way that promise breaks, so it is enforced here rather
        # than trusted (round-3 review, PR #101).
        if self.t_valid is None:
            raise ValueError("ValidTimeSpan.t_valid must not be None; see the protocol's own docstring.")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"ValidTimeSpan.confidence must be within [0.0, 1.0], got {self.confidence!r}.")


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
    hardened past defects that prototype's narrower corpus never exercised across two review
    rounds on PR #101: an out-of-range calendar date crashing instead of falling through, a bare
    "until" clause with no matching start losing its bound in the fallback return, an ISO date
    inside an "until" clause being mis-scoped as the start rather than the end (round 1); a span
    with only an end date mislabelled as a fallback rather than explicit, and an unresolvable
    first trigger-word match (e.g. "from New" in "...from New York... since 2022") blocking a
    resolvable one later in the same sentence (round 2).

    Handles ``since <Month|Year|ISO date>``, ``from ... until ...``, ``in <Year>``, and standalone
    ISO dates (``YYYY-MM-DD``). A bare month with no year resolves against ``ingest_time``'s year,
    so "since Jan" means the most recent January relative to when the fact was recorded, not an
    absolute one.

    **Bare-month resolution is not idempotent across re-extraction.** Re-running this extractor
    over one archived episode's content at two different ``ingest_time``\\ s can produce two
    different ``t_valid`` values for a bare month with no year ("since Jan" resolved in 2024 vs.
    resolved again in 2025) -- documented rather than silently relied on (round-2 review, PR #101);
    whether that is the intended disambiguation for a *re*-extraction (vs. the first, live one,
    where it is unambiguously correct) is a question for SM-R10 to confirm, not settled here.

    **Not exhaustive prose coverage.** Content phrased outside these four patterns falls back to
    ingest time rather than misparsing -- a deliberate simplification for a rule-based default, not
    a bug (an LLM-backed extractor is the documented escape hatch, see the module docstring).
    """

    _ISO_TOKEN = r"\d{4}-\d{2}-\d{2}"
    _ISO = re.compile(r"\b(" + _ISO_TOKEN + r")\b")
    #: The same shape as ``_ISO_TOKEN``, anchored for :func:`re.fullmatch` on an already-isolated
    #: token in :meth:`_resolve` -- a named constant rather than an inline literal there, so the
    #: two patterns' shared ancestry is explicit instead of duplicated by hand.
    _ISO_FULL = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
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

    def _resolve(self, token: str, year_token: Optional[str], ref_year: int) -> Optional[int]:
        """A month name, a bare year, or an ISO date -> epoch seconds, or ``None`` for a token this
        extractor does not recognise **or** one that names a calendar date that does not exist
        (e.g. ``2024-13-45``) -- ``None`` either way, so a caller cannot tell "unrecognised" from
        "invalid" and does not need to: both mean "not a usable cue", handled identically by
        :meth:`extract` falling through to the next pattern or the ingest-time fallback."""
        token = token.strip().lower()
        iso_match = self._ISO_FULL.fullmatch(token)
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

    def _first_resolvable(
        self,
        pattern: re.Pattern[str],
        content: str,
        ref_year: int,
        exclude_span: Optional[Tuple[int, int]] = None,
    ) -> Tuple[Optional[int], Optional[re.Match[str]]]:
        """The first match of ``pattern`` whose captured token actually resolves, skipping any
        earlier match that does not -- ``re.search`` alone stops at the first *syntactic* match,
        which is wrong when a trigger word's own object isn't a date (round-2 review, PR #101):
        "moved from New York to LA since 2022" has ``_SINCE`` match "from New" before "since 2022",
        and "New" resolves to nothing, so a plain ``.search()`` never reaches the real date later in
        the sentence. Returns ``(None, None)`` when no match resolves, same as no match at all.

        Shared across all four patterns (``_UNTIL``, ``_ISO``, ``_SINCE``, ``_IN_YEAR``) since
        round 3 -- ``_ISO`` and ``_IN_YEAR`` only had one capture group before, so this reads a
        year token from the second one **only when the pattern has one** (``match.re.groups``),
        rather than requiring every caller's pattern to declare a group it does not use.

        ``exclude_span`` skips any match overlapping it -- used to keep a standalone ``_ISO`` match
        already consumed by an ``_UNTIL`` clause from also being read as an unrelated start date
        (round 1 finding #4), now applied match-by-match rather than only to the first one (round 3
        finding #1: a *second*, unconsumed ISO date elsewhere in the content used to be silently
        dropped when the first one happened to overlap the until-span)."""
        for match in pattern.finditer(content):
            if exclude_span is not None and self._overlaps(match.span(), exclude_span):
                continue
            year_token = match.group(2) if match.re.groups >= 2 else None
            resolved = self._resolve(match.group(1), year_token, ref_year)
            if resolved is not None:
                return resolved, match
        return None, None

    def extract(self, content: str, ingest_time: Optional[int] = None) -> ValidTimeSpan:
        ingest = now_epoch_s() if ingest_time is None else ingest_time
        ref_year = datetime.fromtimestamp(ingest, tz=timezone.utc).year

        t_valid: Optional[int] = None
        t_invalid: Optional[int] = None
        matched: Optional[str] = None

        # "until"/"till"/etc. resolved first, and its own match span recorded, so the ISO check
        # below can tell an end-date consumed here apart from an unrelated start-date elsewhere in
        # the same sentence ("valid until 2024-09-01" must not also read as a standalone ISO start
        # date -- round 1 finding #4). The span used for that check is the *resolved* until-match's,
        # not necessarily the first syntactic one.
        t_invalid, m_until = self._first_resolvable(self._UNTIL, content, ref_year)
        until_span = m_until.span() if m_until else None

        t_valid, m_iso = self._first_resolvable(self._ISO, content, ref_year, exclude_span=until_span)
        if m_iso is not None:
            matched = m_iso.group(0)

        if t_valid is None:
            t_valid, m_since = self._first_resolvable(self._SINCE, content, ref_year)
            if m_since is not None:
                matched = m_since.group(0)

        if t_valid is None:
            t_valid, m_in = self._first_resolvable(self._IN_YEAR, content, ref_year)
            if m_in is not None:
                matched = m_in.group(0)

        # Explicit whenever content stated *either* bound, not only the start -- content like
        # "contract valid until Sep 2024" states a real end date with no start, and labelling that
        # `source="ingest_fallback"` (round-2 review finding #2) misrepresents it to any consumer
        # branching on `span.source` as "nothing was extracted here".
        explicit = t_valid is not None or t_invalid is not None
        resolved_t_valid: int = t_valid if t_valid is not None else ingest
        # `matched` only ever gets set from a start-date match above, so an until-only extraction
        # (no start date stated at all) reached `source="explicit"` via `t_invalid` alone but
        # carried `matched_text=None` -- a real match existed and was simply never recorded
        # (round-3 review, PR #101 finding #2). Falls back to the until-match's own text only when
        # nothing else already claimed `matched`, so a sentence with both a start and an end date
        # keeps reporting the start's text, matching every existing case.
        if matched is None and m_until is not None:
            matched = m_until.group(0)

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
                confidence=_EXPLICIT_CONFIDENCE,
                source="explicit",
                matched_text=matched,
            )
        return ValidTimeSpan(
            t_valid=resolved_t_valid, t_invalid=t_invalid, confidence=_FALLBACK_CONFIDENCE, source="ingest_fallback"
        )

    async def aextract(self, content: str, ingest_time: Optional[int] = None) -> ValidTimeSpan:
        """Async variant of :meth:`extract`. The rule-based path does no I/O, so this is a
        synchronous computation wearing an async signature -- required by the protocol, not by
        anything this method does, so every caller can ``await`` a :class:`ValidTimeExtractor`
        without knowing which implementation it holds."""
        return self.extract(content, ingest_time)
