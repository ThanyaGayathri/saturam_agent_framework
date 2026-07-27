"""The QA-correctness judge seam.

QA correctness is graded by a ``Judge``. Two implementations sit behind one
protocol so the harness runs offline in CI yet uses a real LLM judge for full
runs — swapping one for the other is a config choice, no call-site edits
(skill §3):

- ``StubJudge`` — deterministic, offline, zero-cost. Normalized lexical match
  (exact-substring OR token-recall >= threshold). Used for CI and for unit
  tests, and clearly NOT a substitute for semantic grading — it is labeled as a
  stub in every report so a stub score is never mistaken for a real one.
- ``LLMJudge`` (Phase 4) — wraps the seed's ``egpt.eval.agent_as_judge`` so the
  real graded verdict flows through the same protocol. Model is env-driven
  (``HARNESS_JUDGE_MODEL``; gpt-5.4/5.5 Azure per .env).

Both expose sync ``judge`` and async ``ajudge`` (public methods get both variants
per the codebase convention).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

_WORD_RE = re.compile(r"[a-z0-9]+")
_STOP = {
    "a",
    "an",
    "the",
    "is",
    "are",
    "was",
    "were",
    "of",
    "to",
    "in",
    "on",
    "at",
    "and",
    "or",
    "for",
    "with",
    "as",
    "by",
    "that",
    "this",
    "it",
    "user",
    "s",
    "does",
    "do",
    "did",
    "has",
    "have",
    "had",
    "their",
    "they",
    "he",
    "she",
}


def _tokens(text: str) -> list:
    return [w for w in _WORD_RE.findall(text.lower()) if w not in _STOP]


def _normalize(text: str) -> str:
    return " ".join(_WORD_RE.findall(text.lower()))


@dataclass(frozen=True)
class JudgeResult:
    """A grading verdict. ``score`` is 1-10 when the judge grades numerically."""

    correct: bool
    score: Optional[float]
    rationale: str
    judge_name: str


@runtime_checkable
class Judge(Protocol):
    """Grades whether ``answer`` satisfies the gold ``expected`` for ``question``."""

    name: str

    def judge(self, question: str, expected: str, answer: str) -> JudgeResult: ...

    async def ajudge(self, question: str, expected: str, answer: str) -> JudgeResult: ...


class StubJudge:
    """Deterministic offline judge: normalized exact-substring OR token-recall.

    correct := (normalized expected is a substring of normalized answer)
               OR (recall of expected content tokens in the answer >= threshold)

    Token recall = |expected_tokens ∩ answer_tokens| / |expected_tokens| after
    stopword removal. ``score`` is 10 * recall (rounded to 1-10) so numeric-mode
    consumers still get a graded number. This is a lexical proxy, NOT semantic
    grading — reports tag it ``stub`` so it is never read as a real judge score.
    """

    name = "stub"

    def __init__(self, threshold: float = 0.6) -> None:
        self.threshold = threshold

    def _grade(self, expected: str, answer: str) -> JudgeResult:
        exp_n, ans_n = _normalize(expected), _normalize(answer)
        exp_tok = _tokens(expected)
        if not exp_tok:
            # Degenerate gold (all stopwords): fall back to substring only.
            ok = exp_n in ans_n
            return JudgeResult(ok, 10.0 if ok else 1.0, "empty-content-gold", self.name)
        ans_set = set(_tokens(answer))
        recall = sum(1 for t in exp_tok if t in ans_set) / len(exp_tok)
        substring = exp_n in ans_n
        correct = substring or recall >= self.threshold
        score = max(1.0, min(10.0, round(10.0 * recall)))
        rationale = f"substring={substring} recall={recall:.2f} thr={self.threshold}"
        return JudgeResult(correct, score, rationale, self.name)

    def judge(self, question: str, expected: str, answer: str) -> JudgeResult:
        return self._grade(expected, answer)

    async def ajudge(self, question: str, expected: str, answer: str) -> JudgeResult:
        return self._grade(expected, answer)
