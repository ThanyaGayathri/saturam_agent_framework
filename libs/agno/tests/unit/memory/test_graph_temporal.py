"""Valid-time extraction for bi-temporal graph edges (SM-G4 #102).

The acceptance criteria this covers directly: valid-time is extracted from content where present,
and falls back to an ingest time (not left ``None``) where it is not. ``relations.py`` covers the
pipeline wiring and the point-in-time read composed on top; this file is the extractor alone.
"""

from datetime import datetime, timezone

import pytest

from egpt.memory.graph.temporal import RuleBasedValidTimeExtractor, ValidTimeSpan


def epoch(year, month, day=1):
    return int(datetime(year, month, day, tzinfo=timezone.utc).timestamp())


@pytest.fixture
def extractor():
    return RuleBasedValidTimeExtractor()


# ---------------------------------------------------------------------------
# Explicit dates are read out of content
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content, expected_t_valid",
    [
        # "since Jan" names no year, so the expected value here matches the fixture's own
        # ingest_time below (2024-07-15) -- see test_a_bare_month_resolves_against_the_ingest_years_january
        # for the year-varies-with-ingest-time behavior this value is *not* asserting.
        ("User lives in LA since Jan", epoch(2024, 1)),
        ("Works at Acme since March 2022", epoch(2022, 3)),
        ("Moved in 2019", epoch(2019, 1)),
        ("Contract effective 2023-04-10", epoch(2023, 4, 10)),
    ],
)
def test_an_explicit_date_is_extracted(extractor, content, expected_t_valid):
    span = extractor.extract(content, ingest_time=epoch(2024, 7, 15))

    assert span.t_valid == expected_t_valid
    assert span.source == "explicit"
    assert span.confidence > 0.3  # strictly above the fallback confidence


def test_a_bare_month_resolves_against_the_ingest_years_january(extractor):
    """ "since Jan" names no year, so it resolves relative to when the fact was recorded, not an
    absolute year -- the same fact re-extracted a year later would date differently, which is
    correct: it is describing "the most recent January" at each ingest time, not a fixed one."""
    span_2024 = extractor.extract("User lives in LA since Jan", ingest_time=epoch(2024, 7, 15))
    span_2025 = extractor.extract("User lives in LA since Jan", ingest_time=epoch(2025, 7, 15))

    assert span_2024.t_valid == epoch(2024, 1)
    assert span_2025.t_valid == epoch(2025, 1)


def test_an_until_clause_sets_t_invalid_alongside_t_valid(extractor):
    span = extractor.extract("Relationship valid from Jun until Sep", ingest_time=epoch(2024, 7, 15))

    assert span.t_valid == epoch(2024, 6)
    assert span.t_invalid == epoch(2024, 9)


def test_an_iso_date_wins_over_a_since_clause_naming_a_different_date(extractor):
    """The ISO check runs first and is exact, so it takes precedence over a looser "since <month>"
    match elsewhere in the same sentence rather than the two silently disagreeing on which wins."""
    span = extractor.extract("Contract effective 2023-04-10, discussed since March", ingest_time=epoch(2024, 7, 15))

    assert span.t_valid == epoch(2023, 4, 10)


# ---------------------------------------------------------------------------
# Fallback -- spec section 5: "extracted valid-time, else ingest time"
# ---------------------------------------------------------------------------


def test_content_with_no_temporal_cue_falls_back_to_ingest_time(extractor):
    span = extractor.extract("Enjoys hiking", ingest_time=epoch(2024, 7, 15))

    assert span.t_valid == epoch(2024, 7, 15)
    assert span.t_invalid is None
    assert span.source == "ingest_fallback"
    assert span.confidence < 0.9  # strictly below the explicit-match confidence


def test_a_missing_ingest_time_falls_back_to_the_current_instant(extractor, monkeypatch):
    """``ingest_time=None`` (the default) must not raise or leave ``t_valid`` unset -- a fact with
    no date and no caller-supplied anchor still needs *some* valid-from, or it never round-trips
    through :class:`~egpt.memory.types.MemoryRecord`, whose ``t_valid`` no other field defaults."""
    monkeypatch.setattr("egpt.memory.graph.temporal.now_epoch_s", lambda: epoch(2024, 7, 15))

    span = extractor.extract("Enjoys hiking")

    assert span.t_valid == epoch(2024, 7, 15)


# ---------------------------------------------------------------------------
# The span never comes back with nothing to report
# ---------------------------------------------------------------------------


def test_t_valid_is_never_none():
    """Unlike ``t_invalid`` (legitimately open), a span with ``t_valid=None`` would fail
    ``MemoryRecord``'s own half-open-interval guard the moment a caller assigned it -- so this
    extractor never produces one, by construction of every branch in ``extract``."""
    extractor = RuleBasedValidTimeExtractor()
    for content in ("Enjoys hiking", "Since Jan", "in 2019", "no cue at all here"):
        span = extractor.extract(content, ingest_time=epoch(2024, 1, 1))
        assert span.t_valid is not None


# ---------------------------------------------------------------------------
# Sync/async parity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aextract_makes_the_same_decision_as_extract():
    extractor = RuleBasedValidTimeExtractor()
    content = "Works at Acme since March 2022"

    sync_span = extractor.extract(content, ingest_time=epoch(2024, 7, 15))
    async_span = await extractor.aextract(content, ingest_time=epoch(2024, 7, 15))

    assert async_span == sync_span


def test_extract_returns_a_valid_time_span():
    span = RuleBasedValidTimeExtractor().extract("Moved in 2019", ingest_time=epoch(2024, 1, 1))

    assert isinstance(span, ValidTimeSpan)


def test_the_span_is_frozen():
    span = RuleBasedValidTimeExtractor().extract("Moved in 2019", ingest_time=epoch(2024, 1, 1))

    # dataclasses.FrozenInstanceError, a subclass of AttributeError -- asserting the narrow class
    # (round-2 review, PR #101) so a removed `frozen=True` fails this test rather than passing on
    # some unrelated Exception a broken frozen guard happens to also raise.
    with pytest.raises(AttributeError):
        span.t_valid = 0


# ---------------------------------------------------------------------------
# Hardening (round-1 review, PR #101): a malformed or self-contradicting date
# in content must degrade, never crash the extraction pass over one episode
# ---------------------------------------------------------------------------


def test_an_out_of_range_calendar_date_does_not_crash_and_falls_through(extractor):
    """ "2024-13-45" is shaped like an ISO date but names no real month or day -- must fall through
    to the ingest-time fallback rather than let `datetime()` raise past this method."""
    span = extractor.extract("Contract void as of 2024-13-45", ingest_time=epoch(2024, 7, 15))

    assert span.t_valid == epoch(2024, 7, 15)
    assert span.source == "ingest_fallback"


def test_an_out_of_range_month_name_date_does_not_crash(extractor):
    """The month-name path can also invert via `year_token`, not only the ISO path -- exercised
    separately so a fix scoped only to the ISO branch is not mistaken for covering both."""
    span = extractor.extract("Valid since Jan 0000", ingest_time=epoch(2024, 7, 15))

    assert span.t_valid == epoch(2024, 7, 15)
    assert span.source == "ingest_fallback"


def test_an_until_only_clause_keeps_its_t_invalid_in_the_fallback_span():
    """Round-1 finding #2: with no start date stated, ``t_valid`` falls back to ingest time, but
    the extracted end date must still reach the span -- the earlier code dropped it entirely.

    ``source`` asserted here too (round-2 review, PR #101): the round-1 fix passed ``t_invalid``
    through but still labelled the span ``"ingest_fallback"`` even though an end date *was*
    extracted -- this test's own name says "keeps its t_invalid" and did not check the field that
    contradicted it, which is exactly the gap the round-2 finding named."""
    span = RuleBasedValidTimeExtractor().extract("Contract valid until Sep 2024", ingest_time=epoch(2024, 1, 1))

    assert span.t_valid == epoch(2024, 1, 1)
    assert span.t_invalid == epoch(2024, 9)
    assert span.source == "explicit"
    assert span.confidence > 0.3


@pytest.mark.parametrize(
    "content",
    [
        "Valid from Sep 2024 until Jun 2024",  # explicit since + until, inverted
        "Valid from Sep 2024 until Sep 2024",  # explicit since + until, degenerate (equal)
    ],
)
def test_an_inverted_or_degenerate_explicit_interval_drops_t_invalid_instead_of_crashing(content):
    """Round-1 finding #3: two explicit dates that together describe an interval empty at every
    instant must not reach ``MemoryRecord`` -- which would raise on it, per its own half-open-
    interval guard -- so the unusable end bound is dropped here instead."""
    span = RuleBasedValidTimeExtractor().extract(content, ingest_time=epoch(2024, 1, 1))

    assert span.t_invalid is None
    assert span.t_valid == epoch(2024, 9)


def test_an_end_date_before_the_ingest_fallback_also_drops_rather_than_inverts():
    """Round-1 finding #3's other reproduction: no start date is stated, so ``t_valid`` falls back
    to ingest time -- and an extracted end date that predates *that* fallback inverts the interval
    just as surely as two explicit dates would, so the same guard must catch it too."""
    span = RuleBasedValidTimeExtractor().extract("No longer valid after Jan 2020", ingest_time=epoch(2024, 7, 15))

    assert span.t_valid == epoch(2024, 7, 15)
    assert span.t_invalid is None


def test_an_iso_date_inside_an_until_clause_is_scoped_as_the_end_not_the_start():
    """Round-1 finding #4: the earlier code ran a blanket ISO search with no regard for the "until"
    keyword right before it, so "valid until 2024-09-01" set ``t_valid`` to the date meant as the
    interval's *end*. With no separate start date stated, the correct answer is the ingest-time
    fallback for ``t_valid`` and the ISO date on ``t_invalid`` -- covered together with finding #2's
    fix, since both land in the same fallback-carries-t_invalid path."""
    span = RuleBasedValidTimeExtractor().extract("Valid until 2024-09-01", ingest_time=epoch(2024, 1, 1))

    assert span.t_valid == epoch(2024, 1, 1)
    assert span.t_invalid == epoch(2024, 9, 1)


def test_an_iso_start_date_outside_an_until_clause_still_sets_t_valid():
    """The overlap check that fixes finding #4 must not blind the extractor to a legitimate
    standalone ISO date merely because the same content also has an unrelated "until" clause."""
    span = RuleBasedValidTimeExtractor().extract(
        "Contract effective 2023-04-10, valid until Sep 2024", ingest_time=epoch(2024, 1, 1)
    )

    assert span.t_valid == epoch(2023, 4, 10)
    assert span.t_invalid == epoch(2024, 9)


# ---------------------------------------------------------------------------
# Hardening (round-2 review, PR #101)
# ---------------------------------------------------------------------------


def test_a_span_with_only_an_end_date_is_labelled_explicit_not_fallback():
    """Round-2 finding #2's direct reproduction, independent of the until-only test above: a span
    carrying a real extracted ``t_invalid`` and no ``t_valid`` must not read as "nothing was
    extracted here" to a consumer branching on ``span.source``."""
    span = RuleBasedValidTimeExtractor().extract("Contract valid until Sep 2024", ingest_time=epoch(2024, 1, 1))

    assert span.source == "explicit"
    assert span.confidence == pytest.approx(0.9)


def test_an_unresolvable_since_match_does_not_block_a_resolvable_one_later_in_the_sentence():
    """Round-2 finding #3's exact reproduction: "from New" (the first ``_SINCE`` match) resolves
    to nothing, so ``.search()`` alone stopped there and never reached "since 2022" -- a real,
    common biographical phrasing, not a constructed edge case."""
    span = RuleBasedValidTimeExtractor().extract("Moved from New York to LA since 2022", ingest_time=epoch(2024, 1, 1))

    assert span.t_valid == epoch(2022, 1)
    assert span.source == "explicit"


def test_an_unresolvable_until_match_does_not_block_a_resolvable_one_later_in_the_sentence():
    """The ``_UNTIL`` twin of the finding above -- the review named it as "the same bug applies"
    rather than reproducing it, so this pins it directly rather than by inference from ``_SINCE``'s
    fix. "Until Friday" is the first ``_UNTIL`` match and resolves to nothing (not a month, year,
    or ISO date); "until Sep 2024" later in the sentence must still be reached.

    ("No longer valid tomorrow, ended until Sep 2024" -- an earlier version of this test -- turned
    out to have only *one* ``_UNTIL`` match, "no longer...after" requiring the literal word "after"
    that content never supplied, so it silently didn't exercise the retry at all; verified by
    ``_UNTIL.finditer`` before writing this replacement, not by inspection alone.)"""
    span = RuleBasedValidTimeExtractor().extract(
        "Valid until Friday, ended until Sep 2024", ingest_time=epoch(2024, 1, 1)
    )

    assert span.t_invalid == epoch(2024, 9)


def test_the_first_resolvable_cue_wins_not_the_first_syntactic_one():
    """Round-2 finding #6: content with two *resolvable* temporal cues must pick the first one a
    left-to-right scan reaches, once the finding-3 fix is in place -- not silently prefer a later
    one, and not raise. This is a regression guard for that fix, not a new capability: nothing here
    claims the extractor merges or disambiguates between the two, only that it is deterministic and
    does not regress to "first syntactic match, even if unresolvable" (finding 3's original bug)."""
    span = RuleBasedValidTimeExtractor().extract(
        "Works at Acme since Jan 2024, formerly at Beta since Mar 2022", ingest_time=epoch(2024, 7, 15)
    )

    assert span.t_valid == epoch(2024, 1)
    assert span.source == "explicit"


# ---------------------------------------------------------------------------
# ValidTimeSpan's own invariant
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("confidence", [-0.01, 1.01, -5.0, 100.0])
def test_confidence_outside_the_unit_interval_is_refused(confidence):
    with pytest.raises(ValueError):
        ValidTimeSpan(t_valid=epoch(2024, 1, 1), confidence=confidence)


@pytest.mark.parametrize("confidence", [0.0, 1.0, 0.5])
def test_confidence_at_or_inside_the_unit_interval_boundaries_is_accepted(confidence):
    ValidTimeSpan(t_valid=epoch(2024, 1, 1), confidence=confidence)
