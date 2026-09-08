"""The output schema for the join path explainer -- Lane R's agent from the K14 template.

Written from ``SKILL_TEMPLATE.md`` alone, as the second half of K14's verification: the
template is proven complete only if the other lane can author a conforming agent without
asking a question. This is that agent. It is the smallest real Lane R agent -- a strict
subset of R11's playground data-scope explanation -- and it exercises the closed-set rule
in the shape that matters most for Lane R: a verdict that must mirror a deterministic
input rather than the model's own judgment.

Mirrors ``synonym_proposer.py`` in structure on purpose. The enum is duplicated between the
prompt and this module, and :func:`closed_sets_match_prompt` is what stops the two drifting.
"""

from __future__ import annotations

import asyncio
import re
from enum import Enum
from pathlib import Path
from typing import Dict, List, Set

from pydantic import BaseModel, Field

__all__ = [
    "JoinVerdict",
    "JoinExplanation",
    "closed_sets_match_prompt",
    "aclosed_sets_match_prompt",
    "verdict_for_path",
]

PROMPT_PATH = Path(__file__).parent / "join_path_explainer.md"


class JoinVerdict(str, Enum):
    """Mirrors the three outcomes of ``SemanticRead.find_join_path``.

    ``JOINABLE`` when the input carried a path, ``NOT_JOINABLE`` when it carried ``null``,
    ``UNKNOWN`` when the input was unusable. The model never chooses between the first two
    on its own judgment -- ``verdict_for_path`` is the deterministic rule, and the prompt's
    first specific rule says the same thing in words.
    """

    JOINABLE = "joinable"
    NOT_JOINABLE = "not_joinable"
    UNKNOWN = "unknown"


class JoinExplanation(BaseModel):
    """The whole response. ``notes`` carries the refusal, per section 8 of the template."""

    from_table: str = Field(description="The from_table input, copied unchanged.")
    to_table: str = Field(description="The to_table input, copied unchanged.")
    # No `description=` here: an enum-typed field serialises to a `$ref`, and strict
    # structured-output schemas reject a `$ref` carrying sibling keywords (template, section 6).
    verdict: JoinVerdict
    hops: int = Field(
        default=0, description="Copied from the path; 0 when the path is null."
    )
    route: List[str] = Field(
        default_factory=list,
        description="Table names in order from from_table to to_table. Empty when the path is null.",
    )
    explanation: str = Field(
        default="",
        description="One to four sentences, each naming the tables and predicate it derives from.",
    )
    caveats: List[str] = Field(
        default_factory=list,
        description="Each traceable to an edge's cardinality or to intermediate_tables.",
    )
    snapshot_version: str = Field(
        description="The snapshot.version input, copied unchanged."
    )
    notes: str = Field(
        default="", description="Empty on a clean run. Otherwise what was missing."
    )


def verdict_for_path(path: object) -> JoinVerdict:
    """The deterministic rule the model's verdict must agree with.

    A cookbook compares the model's ``verdict`` against this; a disagreement is the agent
    doing the one thing its identity section forbids.
    """
    if path is None:
        return JoinVerdict.NOT_JOINABLE
    if isinstance(path, dict) and isinstance(path.get("edges"), list):
        return JoinVerdict.JOINABLE
    return JoinVerdict.UNKNOWN


def _enumerated_values(prompt: str, field_name: str) -> Set[str]:
    values: Set[str] = set()
    for line in prompt.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        named = cells[0].strip("`").split(".")[-1]
        if named != field_name:
            continue
        values.update(re.findall(r"`([a-z_]+)`", cells[-1]))
    return values


def closed_sets_match_prompt(prompt_path: Path = PROMPT_PATH) -> Dict[str, bool]:
    """Whether every enum in this module is enumerated in full in the prompt."""
    prompt = prompt_path.read_text()
    declared = {member.value for member in JoinVerdict}
    return {"verdict": _enumerated_values(prompt, "verdict") == declared}


async def aclosed_sets_match_prompt(prompt_path: Path = PROMPT_PATH) -> Dict[str, bool]:
    """Async variant. The file read happens off the event loop."""
    return await asyncio.to_thread(closed_sets_match_prompt, prompt_path)
