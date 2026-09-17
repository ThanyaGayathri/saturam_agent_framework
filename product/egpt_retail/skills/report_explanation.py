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
