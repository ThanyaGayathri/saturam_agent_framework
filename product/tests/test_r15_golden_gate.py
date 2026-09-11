"""R15 -- the parts of the golden-set publish gate that need no database or model (D14, G13).

``check_golden_gate``'s two checks are pure functions of already-computed values (a pass rate,
a ``RehearsalReview`` or ``None``), so every scenario the delivery plan's Verify line implies --
an unreviewed score, an approved one, a rejected one, a score under threshold -- is constructible
by hand, the same way ``test_r7_preflight.py`` tests ``check_preflight`` without a warehouse.
``RehearsalReviewStore``'s state machine needs real Postgres and is proved by the R15 cookbook
against one, matching ``test_k7_proposals.py``'s own split between DB-free logic here and a
DB-backed cookbook for the store itself.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from egpt.workflow.types import StepInput

from egpt_retail.evals.golden import GoldenSet, golden_pass_rate, skill_prompt_version
from egpt_retail.evals.rehearsal import RehearsalReview, RehearsalStatus
from egpt_retail.executors.golden_gate import DEFAULT_GOLDEN_PASS_THRESHOLD
from egpt_retail.executors.golden_gate import check_golden_gate as _check_golden_gate


def check_golden_gate(step_input):
    return asyncio.run(_check_golden_gate(step_input))


def _review(
    status: RehearsalStatus, *, reviewer=None, note: str = ""
) -> RehearsalReview:
    now = datetime.now(timezone.utc)
    return RehearsalReview(
        skill_identifier="join_path_explainer",
        skill_version="1.0.0",
        status=status,
        golden_pass_rate=1.0,
        total_cases=3,
        reviewer=reviewer,
        note=note,
        recorded_at=now,
        decided_at=None if status is RehearsalStatus.PENDING else now,
    )


def _gate_input(*, rate: float, total: int, review) -> StepInput:
    return StepInput(
        additional_data={
            "skill_identifier": "join_path_explainer",
            "skill_version": "1.0.0",
            "golden_pass_rate": rate,
            "total_cases": total,
            "review": review,
        }
    )


def test_no_review_blocks_even_a_perfect_score():
    output = check_golden_gate(_gate_input(rate=1.0, total=10, review=None))

    assert output.content["allowed"] is False
    assert output.content["blocked_by"] == ["rehearsal_reviewed"]
    assert output.stop is True


def test_approved_and_passing_allows():
    review = _review(RehearsalStatus.APPROVED, reviewer="nandhakumar@saturam.com")

    output = check_golden_gate(_gate_input(rate=0.9, total=10, review=review))

    assert output.content["allowed"] is True
    assert output.content["blocked_by"] == []
    assert output.stop is False
    names = [c["name"] for c in output.content["checks"]]
    assert names == ["golden_set_passed", "rehearsal_reviewed"]


def test_rejected_review_blocks_regardless_of_score():
    review = _review(
        RehearsalStatus.REJECTED, reviewer="nandhakumar@saturam.com", note="Too easy."
    )

    output = check_golden_gate(_gate_input(rate=1.0, total=10, review=review))

    assert output.content["allowed"] is False
    assert "rehearsal_reviewed" in output.content["blocked_by"]


def test_pending_review_blocks():
    review = _review(RehearsalStatus.PENDING)

    output = check_golden_gate(_gate_input(rate=1.0, total=10, review=review))

    assert output.content["allowed"] is False
    assert "rehearsal_reviewed" in output.content["blocked_by"]


def test_score_under_threshold_blocks_even_when_approved():
    review = _review(RehearsalStatus.APPROVED, reviewer="nandhakumar@saturam.com")

    output = check_golden_gate(_gate_input(rate=0.6, total=10, review=review))

    assert output.content["allowed"] is False
    assert output.content["blocked_by"] == ["golden_set_passed"]
    assert "60%" in output.content["checks"][0]["reason"]


def test_empty_golden_set_fails_the_score_check_regardless_of_the_rate():
    """A pass rate with no cases behind it is not evidence -- golden_pass_rate() itself already
    returns 0.0 for an empty suite, but the gate must refuse to be fooled by a caller passing a
    stale nonzero rate with total_cases=0."""
    review = _review(RehearsalStatus.APPROVED, reviewer="nandhakumar@saturam.com")

    output = check_golden_gate(_gate_input(rate=1.0, total=0, review=review))

    assert output.content["allowed"] is False
    assert output.content["blocked_by"] == ["golden_set_passed"]


def test_default_threshold_is_eighty_percent():
    assert DEFAULT_GOLDEN_PASS_THRESHOLD == 0.8


def test_custom_threshold_is_read_from_additional_data():
    review = _review(RehearsalStatus.APPROVED, reviewer="nandhakumar@saturam.com")
    step_input = StepInput(
        additional_data={
            "skill_identifier": "join_path_explainer",
            "skill_version": "1.0.0",
            "golden_pass_rate": 0.5,
            "total_cases": 10,
            "review": review,
            "threshold": 0.4,
        }
    )

    output = check_golden_gate(step_input)

    assert output.content["allowed"] is True


def test_no_additional_data_blocks_rather_than_raising():
    output = check_golden_gate(StepInput())

    assert output.content["allowed"] is False
    assert output.content["blocked_by"] == ["golden_set_passed", "rehearsal_reviewed"]


# --- golden.py -------------------------------------------------------------------------------


class _FakeCase:
    pass


class _FakeResult:
    def __init__(self, passed: int, total: int) -> None:
        self.passed = passed
        self.total = total


def test_golden_pass_rate_empty_suite_is_zero_not_vacuous_one():
    assert golden_pass_rate(_FakeResult(0, 0)) == 0.0


def test_golden_pass_rate_is_passed_over_total():
    assert golden_pass_rate(_FakeResult(3, 4)) == 0.75


def test_golden_set_rejects_empty_cases():
    with pytest.raises(ValueError, match="no cases"):
        GoldenSet(
            skill_identifier="join_path_explainer", skill_version="1.0.0", cases=()
        )


def test_skill_prompt_version_reads_the_header_line():
    prompt = "```\nidentifier:    join_path_explainer\nversion:       1.0.0\nmodel_tier:    small\n```\n"

    assert skill_prompt_version(prompt) == "1.0.0"


def test_skill_prompt_version_raises_when_the_header_has_no_version_line():
    with pytest.raises(ValueError, match="version"):
        skill_prompt_version("no header here")
