"""The output schema for the trivial agent that validates the K14 skill template.

Deliberately the smallest real agent in the system rather than a toy: it is a strict subset
of K8's enrichment proposer, so if the template cannot carry this one it cannot carry the
four Lane K agents either.

The enums here are the closed sets that ``EXAMPLE.md`` section 6 enumerates in full. They
are duplicated between prompt and code on purpose -- the model reads the prompt and the
parser reads this module, and :func:`closed_sets_match_prompt` is what stops the two
drifting apart unnoticed.
"""

from __future__ import annotations

import asyncio
import re
from enum import Enum
from pathlib import Path
from typing import Dict, List, Set, Type

from pydantic import BaseModel, Field

__all__ = [
    "Confidence",
    "ProposalKind",
    "SynonymProposal",
    "SynonymProposalSet",
    "closed_sets_match_prompt",
    "aclosed_sets_match_prompt",
    "enum_members",
]

PROMPT_PATH = Path(__file__).parent / "EXAMPLE.md"


class ProposalKind(str, Enum):
    """What a single proposal is."""

    SYNONYM = "synonym"
    QUESTION_PATTERN = "question_pattern"


class Confidence(str, Enum):
    """How strongly the evidence supports the proposal.

    Not a probability. A reviewer clearing a bulk accept screen (K7) reads this to decide
    what to look at first, so the values have to mean something to a person.
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class SynonymProposal(BaseModel):
    """One proposed term or question pattern for a concept.

    **Where the D10 guarantee actually lives.** Nothing in this schema can stop a model
    emitting a proposal it should not have made -- a required ``rationale`` field makes an
    untraceable claim awkward to express, not impossible, and no output schema can. The
    guarantee is structural and sits downstream: the agent has no write path to the graph, its
    output becomes a row in the proposals table (K7), and only a row a person accepted is
    written. What the prompt and this schema buy is a proposal a reviewer can judge quickly,
    which is what makes bulk review possible at all.

    So a bad proposal here is a review burden, not a corrupted graph. Treat any change that
    would let this type reach the graph without a human decision as breaking D10 and G5,
    whatever the prompt says."""

    value: str = Field(description="The synonym or question pattern being proposed.")
    # No `description=` on these two. An enum-typed field serialises to a `$ref`, and strict
    # structured-output schemas reject a `$ref` carrying sibling keywords -- Azure OpenAI
    # returns a 400 before the model is ever called. What each value means belongs in
    # section 6 of the prompt anyway, which is where the model actually reads it.
    kind: ProposalKind
    confidence: Confidence
    rationale: str = Field(
        description="One sentence, naming what in the input this derives from."
    )


class SynonymProposalSet(BaseModel):
    """The whole response. ``notes`` carries the refusal, per section 8 of the template."""

    concept: str = Field(
        description="The concept these proposals are for, copied from the input."
    )
    proposals: List[SynonymProposal] = Field(
        default_factory=list,
        description="Empty when the input was insufficient. Never padded to look productive.",
    )
    notes: str = Field(
        default="",
        description="Empty on a clean run. Otherwise what was missing or why the list is short.",
    )


def _enumerated_values(prompt: str, field_name: str) -> Set[str]:
    """The backticked values listed against one field in the prompt's output table.

    A field is named in the first cell either bare (``confidence``) or qualified by its
    container (``proposals[].confidence``), so the match is on the final dotted segment
    rather than on the whole cell.
    """
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
    """Whether every enum in this module is enumerated in full in the prompt.

    The template's rule is that a closed set is written out in the prompt rather than
    referred to by name, because a model that can only see a field name will invent a
    plausible value for it. This is that rule as a check: it fails when someone adds an enum
    member and updates only the code.

    Reads one file, so it has an async twin (:func:`aclosed_sets_match_prompt`).
    """
    prompt = prompt_path.read_text()
    checks: Dict[str, bool] = {}
    for field_name, enum_type in (("kind", ProposalKind), ("confidence", Confidence)):
        declared = {member.value for member in enum_type}
        checks[field_name] = _enumerated_values(prompt, field_name) == declared
    return checks


async def aclosed_sets_match_prompt(prompt_path: Path = PROMPT_PATH) -> Dict[str, bool]:
    """Async variant. The file read happens off the event loop."""
    return await asyncio.to_thread(closed_sets_match_prompt, prompt_path)


def enum_members(enum_type: Type[Enum]) -> List[str]:
    """The values of a closed set, for rendering into a prompt or a report."""
    return [str(member.value) for member in enum_type]
