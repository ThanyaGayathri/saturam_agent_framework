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

#: Tolerance for comparing a caller-supplied score against the score a review was recorded
#: against. Both travel through the same ``float`` / Postgres ``double precision`` round trip,
#: so an exact match is the common case; the epsilon exists only so a real match cannot fail on
#: a representational rounding artifact, never to paper over an actually different score.
_SCORE_MATCH_EPSILON = 1e-9


def _review_check(
    review: Optional[RehearsalReview],
    *,
    skill_identifier: Any,
    skill_version: Any,
    pass_rate: float,
    total_cases: int,
) -> Dict[str, Any]:
    """Whether ``review`` is a person's approval of *this exact* score.

    Checking ``review.status`` alone is not enough: the whole package's premise is "an old
    approval is never read as approval of a new score", and that guarantee has to live here,
    not only in callers fetching the right key. A stale review object or a wrong loop variable
    in a batch caller must not pair an approval for one (skill, version, score) with a
    different one -- so identity and score are checked before status is ever consulted.
    """
    if review is None:
        return {
            "name": _REHEARSAL_REVIEWED,
            "passed": False,
            "reason": "No rehearsal review recorded for this score. The default is refuse, "
            "not 'not yet checked' -- a person has not seen this number.",
        }
    if (
        review.skill_identifier != skill_identifier
        or review.skill_version != skill_version
    ):
        return {
            "name": _REHEARSAL_REVIEWED,
            "passed": False,
            "reason": (
                f"Review is for {review.skill_identifier!r} {review.skill_version!r}, not "
                f"{skill_identifier!r} {skill_version!r} being gated now. An approval of a "
                "different skill or version is never read as approval of this one."
            ),
        }
    if (
        abs(review.golden_pass_rate - pass_rate) > _SCORE_MATCH_EPSILON
        or review.total_cases != total_cases
    ):
        return {
            "name": _REHEARSAL_REVIEWED,
            "passed": False,
            "reason": (
                f"Review was recorded against {review.golden_pass_rate:.0%} of "
                f"{review.total_cases} golden cases, not the {pass_rate:.0%} of {total_cases} "
                "being gated now. A caller-supplied score is never trusted over the one a "
                "person actually reviewed -- record a fresh run to reopen review on this score."
            ),
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


def _coerce(
    options: Dict[str, Any], key: str, default: Any, kind: type
) -> tuple[Any, Optional[str]]:
    """``kind(options.get(key, default))``, or ``(default, a reason)`` when the value present
    cannot be read as one -- malformed input degrades the same way missing input does (a
    failing, structured ``StepOutput``), rather than raising out of the executor."""
    raw = options.get(key, default)
    try:
        return kind(raw), None
    except (TypeError, ValueError) as exc:
        return default, f"{key} could not be read as a {kind.__name__} ({raw!r}): {exc}"


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

    A malformed (non-numeric) ``golden_pass_rate``/``total_cases``/``threshold`` fails
    ``golden_set_passed`` with a reason naming the bad value, rather than raising -- the same
    "block rather than raise" treatment already given to *missing* data.
    """
    options: Dict[str, Any] = step_input.additional_data or {}
    skill_identifier = options.get("skill_identifier")
    skill_version = options.get("skill_version")
    review: Optional[RehearsalReview] = options.get("review")

    pass_rate, rate_error = _coerce(options, "golden_pass_rate", 0.0, float)
    total_cases, cases_error = _coerce(options, "total_cases", 0, int)
    threshold, threshold_error = _coerce(
        options, "threshold", DEFAULT_GOLDEN_PASS_THRESHOLD, float
    )
    coercion_error = rate_error or cases_error or threshold_error

    if coercion_error is not None:
        score_check = {
            "name": _GOLDEN_SET_PASSED,
            "passed": False,
            "reason": coercion_error,
        }
        review_check = {
            "name": _REHEARSAL_REVIEWED,
            "passed": False,
            "reason": "The score could not be read, so it cannot be confirmed to match the "
            "recorded review.",
        }
    else:
        score_ok = total_cases > 0 and pass_rate >= threshold
        score_check = {
            "name": _GOLDEN_SET_PASSED,
            "passed": score_ok,
            "reason": (
                f"{pass_rate:.0%} of {total_cases} golden cases passed "
                f"(threshold {threshold:.0%})."
                if total_cases
                else "No golden cases were run; an empty golden set is not evidence of anything."
            ),
        }
        review_check = _review_check(
            review,
            skill_identifier=skill_identifier,
            skill_version=skill_version,
            pass_rate=pass_rate,
            total_cases=total_cases,
        )

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
