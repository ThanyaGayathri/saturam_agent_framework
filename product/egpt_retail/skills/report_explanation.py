"""K23 -- the report explanation's output schema and prompt (the Analytics Automation
chain's one model step). ``report_explanation.md`` is the prompt; this module is the
contract it fills and the message a run builds for it.

The narrative is checked, not trusted: ``executors/analytics.py::check_report_grounding``
rejects an explanation that states a figure the comparison did not carry, and the report
then goes out with the numbers alone.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List, Mapping

from pydantic import BaseModel, Field

__all__ = [
    "ReportExplanation",
    "Finding",
    "REPORT_EXPLANATION_ID",
    "render_prompt",
    "render_message",
]

REPORT_EXPLANATION_ID = "report_explanation"
_PROMPT_PATH = Path(__file__).with_name("report_explanation.md")


class Finding(BaseModel):
    kpi_identifier: str = Field(
        description="The KPI the finding is about, by identifier."
    )
    text: str = Field(
        description="One or two sentences, every figure copied from the input exactly."
    )


class ReportExplanation(BaseModel):
    headline: str = Field(description="One sentence: the most important movement.")
    findings: List[Finding] = Field(default_factory=list)
    caveats: List[str] = Field(default_factory=list)


def render_prompt() -> str:
    """The instructions, as shipped."""
    return _PROMPT_PATH.read_text()


_DIAGNOSIS_PROMPT_PATH = _PROMPT_PATH.with_name("diagnosis_explanation.md")


def render_diagnosis_prompt() -> str:
    """The diagnostic kind's instructions (K25): narrate the ranking, never choose."""
    return _DIAGNOSIS_PROMPT_PATH.read_text()


def render_diagnosis_message(
    diagnosis: Mapping[str, Any], *, question: str = ""
) -> str:
    """The user message for one diagnostic run: the ranking, compacted, as JSON."""
    ranking = list(diagnosis.get("ranking") or [])
    winner = diagnosis.get("winner")
    runners_up: List[Any] = []
    for entry in ranking:
        for driver in entry.get("drivers") or []:
            if (
                winner
                and driver.get("dimension") == winner.get("dimension")
                and driver.get("value") == winner.get("value")
            ):
                continue
            runners_up.append(driver)
            if len(runners_up) >= 3:
                break
        if len(runners_up) >= 3:
            break
    evidence = diagnosis.get("evidence") or {}
    unit = str((diagnosis.get("headline") or {}).get("unit") or "")

    def driver_words(driver: Any) -> Any:
        """A driver with its share as a percent and its contribution in the KPI's unit,
        so the narrator copies "45% of the change" and "+0.6 pts" rather than 0.4454."""
        if not isinstance(driver, Mapping):
            return driver
        out = dict(driver)
        share = out.pop("share_of_gap", None)
        contribution = out.pop("contribution", None)
        out.pop("rate_effect", None)
        out.pop("mix_effect", None)
        if share is not None:
            out["share_of_change"] = f"{float(share) * 100:.0f}%"
        if contribution is not None:
            sign = "+" if float(contribution) >= 0 else ""
            out["contribution"] = (
                f"{sign}{float(contribution):.1f} pts"
                if unit == "%"
                else f"{sign}{float(contribution):.1f}"
            )
        return _tidy(out)

    payload = {
        "question": question,
        "headline": _tidy(
            {
                k: v
                for k, v in (diagnosis.get("headline") or {}).items()
                if k not in {"exact_delta", "history", "kind"}
            }
        ),
        "winner": driver_words(winner),
        "runners_up": [driver_words(r) for r in runners_up],
        "intersections": [
            {
                "dimension": i.get("dimension"),
                "top": driver_words((i.get("drivers") or [None])[0]),
            }
            for i in (diagnosis.get("intersections") or [])[:2]
        ],
        "evidence": _tidy(
            {
                "side": evidence.get("side"),
                "count": evidence.get("count"),
                "tallies": evidence.get("tallies"),
            }
        ),
        "confidence": _tidy(diagnosis.get("confidence")),
        "caveats": diagnosis.get("caveats") or [],
    }
    return (
        "Write the diagnosis for this ranking. Copy every figure exactly; the winner is "
        "the winner.\n\n" + json.dumps(payload, indent=1, default=str)
    )


def _tidy(value: Any) -> Any:
    """Whole numbers as integers, others to one decimal: the figures the model copies are
    the figures the report prints, so the checker sees the same rounding on both sides."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return int(value) if float(value).is_integer() else round(float(value), 1)
    if isinstance(value, Mapping):
        return {k: _tidy(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_tidy(v) for v in value]
    return value


def _compact(kpi: Mapping[str, Any]) -> Mapping[str, Any]:
    out = {
        k: _tidy(kpi.get(k))
        for k in (
            "identifier",
            "name",
            "unit",
            "grain",
            "current",
            "baseline",
            "delta",
            "delta_pct",
        )
    }
    if kpi.get("movers"):
        out["movers"] = _tidy(kpi["movers"])
    if kpi.get("rows"):
        out["rows"] = _tidy(kpi["rows"][:15])
    if kpi.get("reason"):
        out["reason"] = kpi["reason"]
    return out


def render_message(comparison: Mapping[str, Any], *, question: str = "") -> str:
    """The user message for one run: the comparison, compacted, as JSON."""
    payload = {
        "question": question,
        "as_of": comparison.get("as_of"),
        "comparison_label": comparison.get("comparison_label"),
        "kpis": [_compact(k) for k in comparison.get("kpis") or []],
        "missing": comparison.get("missing") or [],
    }
    return (
        "Write the report narrative for this comparison. Copy every figure exactly.\n\n"
        + json.dumps(payload, indent=1, default=str)
    )
