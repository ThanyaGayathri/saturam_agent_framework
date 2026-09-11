"""The golden-set container and runner (R15, D14, G13).

The delivery plan's "Build" line asks for a golden question set per use case and per agent
skill, "scored with the existing eval suite". ``libs/agno/egpt/eval/suite.py`` already is that
suite: ``Case`` is one input to one agent plus a judge (``AgentAsJudgeEval`` via ``criteria``)
and/or a reliability check (``ReliabilityEval`` via ``expected_tool_calls``), and
``run_cases``/``arun_cases`` produce a ``SuiteResult`` whose ``passed``/``total`` is exactly the
pass rate the API contract's ``UseCaseQuality.golden_pass_rate`` names.

So a "golden set" here is not a new storage layer -- it is a labelled, versioned sequence of
``Case``\\ s, run through the framework's own runner. ``GoldenSet`` carries the label
(``skill_identifier``, ``skill_version``) that ``egpt.eval`` itself has no place for, because a
bare ``SuiteResult`` cannot say which prompt version produced it and R15's whole point is
attributing a score drop to an edit (SKILL_TEMPLATE.md section 1: "bump the minor on any prompt
change, so R15 can attribute a score drop").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple, Union

from egpt.db.base import AsyncBaseDb, BaseDb
from egpt.eval.suite import Case, SuiteResult, arun_cases, run_cases
from egpt.models.base import Model

__all__ = [
    "GoldenSet",
    "golden_pass_rate",
    "run_golden_set",
    "arun_golden_set",
    "skill_prompt_version",
]


@dataclass(frozen=True)
class GoldenSet:
    """The golden question set for one agent skill, at one prompt version.

    ``skill_version`` mirrors the skill's own header ``version:`` field (SKILL_TEMPLATE.md
    section 1) -- read it with :func:`skill_prompt_version` rather than hand-typing it, or a
    golden set can silently claim a score for a prompt version it never ran against.
    """

    skill_identifier: str
    skill_version: str
    cases: Tuple[Case, ...]

    def __post_init__(self) -> None:
        # Truthiness rather than a length check written out: a golden set with no cases passes
        # vacuously (SuiteResult.status already refuses an empty suite as "PASS" for the same
        # reason), and a vacuous gate is worse than an absent one -- it reads as "checked".
        if not self.cases:
            raise ValueError(
                f"golden set for {self.skill_identifier!r} {self.skill_version} has no cases -- "
                "an empty golden set would pass vacuously, which defeats the gate it feeds."
            )


def golden_pass_rate(result: SuiteResult) -> float:
    """``passed / total``. Mirrors ``SuiteResult.status``'s own rule: no cases run is ``0.0``,
    never a vacuous ``1.0`` that would let an empty or aborted golden run publish clean."""
    if result.total == 0:
        return 0.0
    return result.passed / result.total


def run_golden_set(
    golden: GoldenSet,
    *,
    db: Optional[Union[BaseDb, AsyncBaseDb]] = None,
    judge_model: Optional[Model] = None,
    default_timeout: int = 120,
) -> SuiteResult:
    """Run every case in the golden set and return the suite's raw result.

    A thin call-through to ``egpt.eval.suite.run_cases`` -- the "existing eval suite" the
    delivery plan asks for, not a reimplementation of it. ``db=`` logs each case's judge and
    reliability results the same way any other eval run does.
    """
    return run_cases(
        golden.cases, db=db, judge_model=judge_model, default_timeout=default_timeout
    )


async def arun_golden_set(
    golden: GoldenSet,
    *,
    db: Optional[Union[BaseDb, AsyncBaseDb]] = None,
    judge_model: Optional[Model] = None,
    default_timeout: int = 120,
) -> SuiteResult:
    """Async variant."""
    return await arun_cases(
        golden.cases, db=db, judge_model=judge_model, default_timeout=default_timeout
    )


def skill_prompt_version(prompt_text: str) -> str:
    """The ``version:`` line out of a skill's own fenced header (SKILL_TEMPLATE.md section 1).

    Read from the prompt text itself rather than restated as a Python literal next to it, so a
    prompt edited without bumping its header version and a golden set that claims a version are
    never two numbers somebody has to keep in sync by hand -- there is only the one in the file.
    """
    for line in prompt_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("version:"):
            return stripped.split(":", 1)[1].strip()
    raise ValueError(
        "prompt has no 'version:' line in its header (SKILL_TEMPLATE.md section 1)"
    )
