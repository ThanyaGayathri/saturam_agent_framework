"""The output schema for R11's playground agent (K14 template).

``AnswerBlock`` mirrors ``api/openapi/schemas/playground.yaml#AnswerBlock``: only ``id`` and
``kind`` are required there, and the rest is deliberately open ("the playground's answer
format owns the inner shape; the report only needs the id and kind to pin it") -- ``extra=
"allow"`` is what keeps this model accepting the same shape the wire contract does, rather
than silently dropping a field a future block kind adds.
"""

from __future__ import annotations

import asyncio
import re
from enum import Enum
from pathlib import Path
from typing import Dict, List, Set

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "AnswerBlockKind",
    "AnswerBlock",
    "PlaygroundAnswer",
    "closed_sets_match_prompt",
    "aclosed_sets_match_prompt",
]

PROMPT_PATH = Path(__file__).parent / "playground.md"


class AnswerBlockKind(str, Enum):
    TEXT = "text"
    FINDING = "finding"
    TABLE = "table"
    CHART = "chart"
    KPI = "kpi"
    RECOMMENDATION = "recommendation"


class AnswerBlock(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    # No `description=` here: an enum-typed field serialises to a `$ref`, and strict
    # structured-output schemas reject a `$ref` carrying sibling keywords (template, section 6).
    kind: AnswerBlockKind
    title: str = ""
    content: str = ""
    source: str = Field(
        default="",
        description="Which tool call, and result id where relevant, this block cites.",
    )


class PlaygroundAnswer(BaseModel):
    """The whole response. ``notes`` carries the refusal, per section 7 of the template."""

    blocks: List[AnswerBlock] = Field(default_factory=list)
    notes: str = Field(
        default="",
        description="Empty on a clean run. Otherwise what was missing or declined.",
    )


def _enumerated_values(prompt: str, field_name: str) -> Set[str]:
    values: Set[str] = set()
    for line in prompt.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        named = cells[0].strip("`").split(".")[-1].rstrip("[]")
        if named != field_name:
            continue
        values.update(re.findall(r"`([a-z_]+)`", cells[-1]))
    return values


def closed_sets_match_prompt(prompt_path: Path = PROMPT_PATH) -> Dict[str, bool]:
    """Whether every enum in this module is enumerated in full in the prompt."""
    prompt = prompt_path.read_text()
    declared = {member.value for member in AnswerBlockKind}
    return {"kind": _enumerated_values(prompt, "kind") == declared}


async def aclosed_sets_match_prompt(prompt_path: Path = PROMPT_PATH) -> Dict[str, bool]:
    """Async variant. The file read happens off the event loop."""
    return await asyncio.to_thread(closed_sets_match_prompt, prompt_path)
