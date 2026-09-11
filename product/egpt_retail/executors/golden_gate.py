"""``check_golden_gate`` (R15, D14, G13): may this agent skill's prompt version be published?

Mirrors ``check_preflight`` (R7) in shape deliberately: same ``{name, passed, reason}`` check
list, same ``blocked_by``/``allowed``/``checked_at`` envelope, same ``stop=True`` on a block --
a screen already rendering one needs no new code to render the other, and the API contract's
``PreflightResult.checks[].name`` is already an open string, so no contract change is needed to
add a new check name to it.

**Deterministic by construction: this executor never calls a model.** Everything it reads was
computed elsewhere -- ``golden_pass_rate`` by running the golden set (a real model call, made by
``egpt_retail.evals.golden.run_golden_set`` before this executor is ever invoked) and the
``RehearsalReview`` by a person. That split is the alignment rule applied to publishing rather
than to running: "anything that permits or blocks is an executor, never a model." Scoring may
judge an open-ended answer; deciding never does.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from egpt.workflow.types import StepInput, StepOutput

from egpt_retail.evals.rehearsal import RehearsalReview, RehearsalStatus

__all__ = ["check_golden_gate", "DEFAULT_GOLDEN_PASS_THRESHOLD"]

#: Below this pass rate, `golden_set_passed` fails regardless of rehearsal review. Overridable
#: per call via `additional_data["threshold"]` -- a use case with a smaller, harder golden set
#: may reasonably set a different bar than one with a large, easy one.
DEFAULT_GOLDEN_PASS_THRESHOLD = 0.8

_GOLDEN_SET_PASSED = "golden_set_passed"
_REHEARSAL_REVIEWED = "rehearsal_reviewed"


def _review_check(review: Optional[RehearsalReview]) -> Dict[str, Any]:
    if review is None:
        return {
            "name": _REHEARSAL_REVIEWED,
            "passed": False,
            "reason": "No rehearsal review recorded for this score. The default is refuse, "
            "not 'not yet checked' -- a person has not seen this number.",
        }
    if review.status is RehearsalStatus.APPROVED:
        return {
            "name": _REHEARSAL_REVIEWED,
            "passed": True,
            "reason": f"Approved by {review.reviewer}"
            + (f": {review.note}" if review.note else "."),
        }
    if review.status is RehearsalStatus.REJECTED:
        return {
            "name": _REHEARSAL_REVIEWED,
            "passed": False,
            "reason": f"Rejected by {review.reviewer}"
            + (f": {review.note}" if review.note else "."),
        }
    return {
        "name": _REHEARSAL_REVIEWED,
        "passed": False,
        "reason": "Rehearsal review is pending. A person has not confirmed this score yet.",
    }


async def check_golden_gate(step_input: StepInput) -> StepOutput:
    """May this skill's prompt version be published?

    ``step_input.additional_data``:
        skill_identifier, skill_version (str): identify the score being gated.
        golden_pass_rate (float): from ``egpt_retail.evals.golden.golden_pass_rate``.
        total_cases (int): how many golden cases the rate is over -- a rate with zero cases
            behind it fails ``golden_set_passed`` regardless of the number itself.
        threshold (float, optional): defaults to ``DEFAULT_GOLDEN_PASS_THRESHOLD``.
        review (RehearsalReview | None): the stored row for this exact
            (skill_identifier, skill_version), or ``None`` if no golden run has been recorded.

    Two checks, ``golden_set_passed`` and ``rehearsal_reviewed``. Both must pass to allow --
    a high pass rate nobody has reviewed does not publish, and a reviewed-but-failing score
    does not publish either. ``async`` because a caller wires this to a real
    ``RehearsalReviewStore`` read (I/O); the executor-registry exemption from the sync/async
    rule applies here exactly as it does to ``check_preflight``.
    """
    options: Dict[str, Any] = step_input.additional_data or {}
    skill_identifier = options.get("skill_identifier")
    skill_version = options.get("skill_version")
    pass_rate = float(options.get("golden_pass_rate", 0.0))
    total_cases = int(options.get("total_cases", 0))
    threshold = float(options.get("threshold", DEFAULT_GOLDEN_PASS_THRESHOLD))
    review: Optional[RehearsalReview] = options.get("review")

    score_ok = total_cases > 0 and pass_rate >= threshold
    score_check = {
        "name": _GOLDEN_SET_PASSED,
        "passed": score_ok,
        "reason": (
            f"{pass_rate:.0%} of {total_cases} golden cases passed (threshold {threshold:.0%})."
            if total_cases
            else "No golden cases were run; an empty golden set is not evidence of anything."
        ),
    }
    review_check = _review_check(review)

    checks: List[Dict[str, Any]] = [score_check, review_check]
    blocked_by = [check["name"] for check in checks if not check["passed"]]
    allowed = not blocked_by

    return StepOutput(
        content={
            "skill_identifier": skill_identifier,
            "skill_version": skill_version,
            "allowed": allowed,
            "checks": checks,
            "blocked_by": blocked_by,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        },
        success=True,
        error=None
        if allowed
        else " ".join(check["reason"] for check in checks if not check["passed"]),
        stop=not allowed,
    )
