"""Valid-time extraction (SM-R10).

Derives the *world*-validity of a fact from its content ("lives in LA **since Jan**") ->
``t_valid`` (and optionally ``t_invalid``). This feeds the ``t_valid`` of a ``BiTemporalEdge``;
when nothing is stated, valid-time falls back to the ingest (transaction) time.

Plug-and-play seam: call sites depend on ``ValidTimeExtractor``; the concrete extractor is
chosen by config.

* ``RuleBasedValidTimeExtractor`` -- default, offline, deterministic (regex + month map).
  Zero external dependencies; what CI runs.
* ``LLMValidTimeExtractor`` -- optional; delegates to an egpt ``Agent`` with an
  ``output_schema`` (defaults to ``OpenAIResponses(id="gpt-5.5")``; any egpt model instance --
  Azure, etc. -- can be injected). Never required for the prototype/tests to pass. The agent is
  built once and reused (no agent-in-loop).

Both sync and async variants are provided.
"""

from __future__ import annotations

import asyncio
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from egpt.utils.dttm import now_epoch_s

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


@dataclass
class ValidTimeSpan:
    """Extracted world-validity for a fact (epoch seconds; ``None`` = open/unknown)."""

    t_valid: Optional[int]
    t_invalid: Optional[int] = None
    confidence: float = 0.0
    source: str = "ingest_fallback"  # "explicit" | "ingest_fallback" | "llm"
    matched_text: Optional[str] = None


class ValidTimeExtractor(ABC):
    """Abstract valid-time extractor."""

    @abstractmethod
    def extract(self, content: str, ingest_time: Optional[int] = None) -> ValidTimeSpan:
        """Return the world-validity span implied by ``content`` (fallback to ingest time)."""

    async def aextract(self, content: str, ingest_time: Optional[int] = None) -> ValidTimeSpan:
        return await asyncio.to_thread(self.extract, content, ingest_time)


def _epoch(year: int, month: int, day: int) -> int:
    return int(datetime(year, month, day, tzinfo=timezone.utc).timestamp())


class RuleBasedValidTimeExtractor(ValidTimeExtractor):
    """Deterministic regex-based extractor for explicit temporal cues."""

    _ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
    _SINCE = re.compile(r"\b(?:since|from|as of|starting)\s+([A-Za-z]+|\d{4})(?:\s+(\d{4}))?", re.I)
    _UNTIL = re.compile(r"\b(?:until|till|through|no longer\s+\w+\s+after)\s+([A-Za-z]+|\d{4})(?:\s+(\d{4}))?", re.I)
    _IN_YEAR = re.compile(r"\bin\s+(\d{4})\b", re.I)

    def _resolve(self, token: str, year_token: Optional[str], ref_year: int) -> Optional[int]:
        token = token.strip().lower()
        if token.isdigit() and len(token) == 4:  # bare year
            return _epoch(int(token), 1, 1)
        if token in _MONTHS:
            year = int(year_token) if year_token else ref_year
            return _epoch(year, _MONTHS[token], 1)
        return None

    def extract(self, content: str, ingest_time: Optional[int] = None) -> ValidTimeSpan:
        ingest = now_epoch_s() if ingest_time is None else ingest_time
        ref_year = datetime.fromtimestamp(ingest, tz=timezone.utc).year

        t_valid: Optional[int] = None
        t_invalid: Optional[int] = None
        matched: Optional[str] = None

        m_iso = self._ISO.search(content)
        if m_iso:
            y, mo, d = (int(g) for g in m_iso.groups())
            t_valid = _epoch(y, mo, d)
            matched = m_iso.group(0)

        m_since = self._SINCE.search(content)
        if m_since and t_valid is None:
            t_valid = self._resolve(m_since.group(1), m_since.group(2), ref_year)
            if t_valid is not None:
                matched = m_since.group(0)

        m_until = self._UNTIL.search(content)
        if m_until:
            t_invalid = self._resolve(m_until.group(1), m_until.group(2), ref_year)

        m_in = self._IN_YEAR.search(content)
        if m_in and t_valid is None:
            t_valid = _epoch(int(m_in.group(1)), 1, 1)
            matched = m_in.group(0)

        if t_valid is None:
            return ValidTimeSpan(t_valid=ingest, confidence=0.3, source="ingest_fallback")
        return ValidTimeSpan(
            t_valid=t_valid, t_invalid=t_invalid, confidence=0.9, source="explicit", matched_text=matched
        )


# --- LLM prompt / schema (only imported lazily inside LLMValidTimeExtractor) ---
_LLM_PROMPT = (
    "Extract the world-validity of the stated fact. Return valid_from (the date the fact became "
    "true) and valid_to (the date it stopped being true, if stated), each as an ISO date "
    "(YYYY-MM-DD) or null if not stated. Do not invent dates."
)


class LLMValidTimeExtractor(ValidTimeExtractor):
    """Optional LLM-backed extractor (egpt Agent + output_schema). Not used by CI/tests."""

    def __init__(self, model: Optional[object] = None) -> None:
        self._model = model
        self._agent = None  # built once on first use, then reused

    def _get_agent(self):  # type: ignore[no-untyped-def]
        if self._agent is None:
            from egpt.agent import Agent
            from egpt.models.openai import OpenAIResponses
            from pydantic import BaseModel, Field

            class _LLMValidTime(BaseModel):
                valid_from: Optional[str] = Field(None, description="ISO date the fact became true")
                valid_to: Optional[str] = Field(None, description="ISO date the fact stopped being true")

            model = self._model or OpenAIResponses(id="gpt-5.5")
            self._agent = Agent(model=model, description=_LLM_PROMPT, output_schema=_LLMValidTime)
        return self._agent

    @staticmethod
    def _to_epoch(value: Optional[str]) -> Optional[int]:
        if not value:
            return None
        from egpt.utils.dttm import to_epoch_s

        return to_epoch_s(value)

    def extract(self, content: str, ingest_time: Optional[int] = None) -> ValidTimeSpan:
        run = self._get_agent().run(content)
        vt = run.content
        t_valid = self._to_epoch(getattr(vt, "valid_from", None))
        return ValidTimeSpan(
            t_valid=t_valid if t_valid is not None else (ingest_time or now_epoch_s()),
            t_invalid=self._to_epoch(getattr(vt, "valid_to", None)),
            confidence=0.8,
            source="llm",
            matched_text=content,
        )

    async def aextract(self, content: str, ingest_time: Optional[int] = None) -> ValidTimeSpan:
        run = await self._get_agent().arun(content)
        vt = run.content
        t_valid = self._to_epoch(getattr(vt, "valid_from", None))
        return ValidTimeSpan(
            t_valid=t_valid if t_valid is not None else (ingest_time or now_epoch_s()),
            t_invalid=self._to_epoch(getattr(vt, "valid_to", None)),
            confidence=0.8,
            source="llm",
            matched_text=content,
        )


# --- Reproducible rule-based extraction eval (committed; run from run.py) ---
_EVAL_INGEST = _epoch(2024, 7, 15)  # fixed reference "now" so relative cues are deterministic
# (content, expected_t_valid, expected_t_invalid)
EXTRACTION_CASES = [
    ("User lives in LA since Jan", _epoch(2024, 1, 1), None),
    ("Works at Acme since March 2022", _epoch(2022, 3, 1), None),
    ("Moved in 2019", _epoch(2019, 1, 1), None),
    ("Relationship valid from Jun until Sep", _epoch(2024, 6, 1), _epoch(2024, 9, 1)),
    ("Contract effective 2023-04-10", _epoch(2023, 4, 10), None),
    ("Enjoys hiking", _EVAL_INGEST, None),  # no cue -> ingest fallback
]


def evaluate_rule_based() -> tuple[int, int]:
    """Run the rule-based extractor over EXTRACTION_CASES; return (passed, total)."""
    ex = RuleBasedValidTimeExtractor()
    passed = 0
    for content, exp_valid, exp_invalid in EXTRACTION_CASES:
        s = ex.extract(content, ingest_time=_EVAL_INGEST)
        if s.t_valid == exp_valid and s.t_invalid == exp_invalid:
            passed += 1
    return passed, len(EXTRACTION_CASES)
