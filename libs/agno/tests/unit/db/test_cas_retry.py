"""Unit tests for the shared compare-and-swap retry envelope (egpt.db.cas_retry)."""

import pytest

from egpt.db.cas_retry import RetriesExhausted, run_cas_retry


def test_a_first_try_success_returns_immediately_without_retrying():
    calls = []

    def attempt():
        calls.append(1)
        return "landed"

    result = run_cas_retry(attempt, max_attempts=5, exhausted_message="unused")

    assert result == "landed"
    assert len(calls) == 1


def test_it_retries_until_a_non_none_result_lands():
    attempts = iter([None, None, "landed"])

    result = run_cas_retry(lambda: next(attempts), max_attempts=5, exhausted_message="unused")

    assert result == "landed"


def test_a_falsy_but_non_none_result_still_counts_as_landed():
    # 0, "", and False are legitimate success values as long as they are not None -- only
    # None is reserved to mean "retry".
    result = run_cas_retry(lambda: 0, max_attempts=3, exhausted_message="unused")

    assert result == 0


def test_exhaustion_raises_retries_exhausted_with_the_given_message():
    with pytest.raises(RetriesExhausted, match="kept being claimed"):
        run_cas_retry(lambda: None, max_attempts=3, exhausted_message="kept being claimed by concurrent writers")


def test_exhaustion_makes_exactly_max_attempts_tries():
    calls = []

    def attempt():
        calls.append(1)
        return None

    with pytest.raises(RetriesExhausted):
        run_cas_retry(attempt, max_attempts=4, exhausted_message="unused")

    assert len(calls) == 4


def test_retries_exhausted_is_a_value_error():
    # Existing `except ValueError` call sites (routers, callers of upsert_config /
    # set_current_version) must keep catching this without changes.
    assert issubclass(RetriesExhausted, ValueError)
