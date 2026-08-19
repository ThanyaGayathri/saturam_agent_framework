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
        ("User lives in LA since Jan", None),  # resolved against ingest year -- see below
        ("Works at Acme since March 2022", epoch(2022, 3)),
        ("Moved in 2019", epoch(2019, 1)),
        ("Contract effective 2023-04-10", epoch(2023, 4, 10)),
    ],
)
def test_an_explicit_date_is_extracted(extractor, content, expected_t_valid):
    span = extractor.extract(content, ingest_time=epoch(2024, 7, 15))

    if expected_t_valid is not None:
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
