"""K8 -- the mapping proposer's output schema (D10, G19, G20).

Which of *this customer's* tables is an instance of which shipped concept. The ontology says
what a Quote is, once, for everyone; this agent says that here it lives in ``crm_deal``.

Two harder jobs come with it, and both are review decisions the agent only drafts:

**G19 -- a column name that means two things.** K4 reports every collision it found. A
collision is not resolved by picking a winner: it is resolved by saying what each side
actually is, so ``analytics_event.session_id`` and ``session_replay_session.session_id``
stop being one name and become two properties of two concepts. An unresolved collision is a
join waiting to be made wrongly.

**G20 -- a concept two tables can both answer for.** When more than one table maps to a
concept, one of them is authoritative and the others are alternatives. A provenance record
has to be able to say *"answered from crm_deal, not from analytics_event"*, and a decision
recorded without the options it beat is indistinguishable from there having been no decision.

The agent drafts all three kinds and writes none of them. :func:`to_proposals` produces
``PENDING`` rows for K7's table; only a row a person accepted reaches the graph.
"""

from __future__ import annotations

import asyncio
from enum import Enum
from pathlib import Path
from typing import Dict, List, Sequence, Set

from pydantic import BaseModel, Field

from egpt_retail.semantic.proposals import Proposal, ProposalKind

__all__ = [
    "PROMPT_PATH",
    "Confidence",
    "CollisionResolution",
    "MappingProposal",
    "MappingProposalSet",
    "arender_prompt",
    "render_prompt",
    "to_proposals",
    "unresolved_collisions",
]

PROMPT_PATH = Path(__file__).parent / "mapping_proposer.md"


class Confidence(str, Enum):
    """How strongly the evidence supports the mapping. Read by a reviewer, not a scorer."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class MappingProposal(BaseModel):
    """One table proposed as an instance of one concept."""

    table: str = Field(
        description="The table, qualified as schema.table exactly as the harvest names it."
    )
    concept: str = Field(
        description="A concept name from the ontology input. Never a new one."
    )
    confidence: Confidence
    evidence: str = Field(
        description="The columns, comments or sampled values this mapping rests on."
    )
    authoritative: bool = Field(
        default=False,
        description=(
            "True when this table should answer for the concept in preference to other "
            "tables also mapped to it (G20). At most one table per concept."
        ),
    )
    alternatives: List[str] = Field(
        default_factory=list,
        description=(
            "The other tables that could have served this concept and did not. Advisory: "
            "the alternatives written into the proposal are derived from the other mappings "
            "in this same response, not taken from here."
        ),
    )


class CollisionResolution(BaseModel):
    """What one colliding column name means on each of the tables that carry it (G19)."""

    column_name: str = Field(
        description="The colliding column name, as K4 reported it."
    )
    table: str = Field(
        description="One of the tables carrying it, qualified schema.table."
    )
    concept: str = Field(
        description="The concept this table's copy of the column belongs to."
    )
    means: str = Field(
        description=(
            "What this copy of the column actually identifies, in one clause. Two "
            "resolutions of the same name must not say the same thing."
        )
    )
    joinable: bool = Field(
        default=False,
        description=(
            "Whether this column may be joined to another table's column of the same name. "
            "False unless a column comment or key constraint says so."
        ),
    )


class MappingProposalSet(BaseModel):
    """Everything the mapping agent drafted, plus the refusal channel."""

    mappings: List[MappingProposal] = Field(default_factory=list)
    collision_resolutions: List[CollisionResolution] = Field(default_factory=list)
    notes: str = Field(
        default="",
        description="Empty on a clean run. Otherwise what was missing or left undecided.",
    )


def render_prompt(
    *,
    concepts: Sequence[str],
    collisions: Sequence[str] = (),
    prompt_path: Path = PROMPT_PATH,
) -> str:
    """The prompt with the concept list and the collisions K4 found filled in.

    The concept list is rendered rather than described because of the one failure this agent
    has: a model asked to map tables to concepts, given no list, will produce concept names
    that sound like an ontology and are not in this one. Every name it may use is written out.
    """
    prompt = prompt_path.read_text()
    for placeholder, rendered in (
        (
            "{{concepts}}",
            ", ".join(f"`{name}`" for name in sorted(concepts)) or "(none)",
        ),
        (
            "{{collisions}}",
            "\n".join(f"- `{name}`" for name in sorted(collisions))
            or "- (none reported)",
        ),
    ):
        if placeholder not in prompt:
            raise ValueError(
                f"{prompt_path.name} has no {placeholder} placeholder, so the agent would "
                "work from an unstated list and invent names."
            )
        prompt = prompt.replace(placeholder, rendered)
    return prompt


async def arender_prompt(
    *,
    concepts: Sequence[str],
    collisions: Sequence[str] = (),
    prompt_path: Path = PROMPT_PATH,
) -> str:
    """Async variant. The file read happens off the event loop."""
    return await asyncio.to_thread(
        render_prompt, concepts=concepts, collisions=collisions, prompt_path=prompt_path
    )


def unresolved_collisions(
    response: MappingProposalSet, reported: Sequence[str]
) -> List[str]:
    """Collisions K4 reported that the agent did not resolve on **every** side.

    A resolution naming one of two colliding tables is not a resolution: the name still means
    two things and only one of them has been written down. The package's verification asserts
    this list is empty, which is the only mechanical check on G19 there is -- whether the
    meanings are *right* is what the review by eye is for.
    """
    resolved: Dict[str, Set[str]] = {}
    for item in response.collision_resolutions:
        resolved.setdefault(item.column_name, set()).add(item.table)
    return sorted(name for name in reported if len(resolved.get(name, set())) < 2)


def to_proposals(
    response: MappingProposalSet,
    *,
    proposer: str,
    known_concepts: Sequence[str] = (),
) -> List[Proposal]:
    """Turn one agent response into rows for the proposals table.

    Three kinds come out: a ``MAPPING`` per table-to-concept claim, an
    ``AUTHORITATIVE_SOURCE`` per concept the agent named a winner for (G20), and a
    ``COLUMN_MEANING`` per collision resolution (G19), which on acceptance lands on the
    column itself -- its concept, its meaning, whether it joins. The first version parked
    resolutions as question patterns on the concept, which was the wrong home in a field the
    enrichment agent owns; the P3 verification moved them.

    A mapping onto a concept outside ``known_concepts`` is dropped rather than proposed. The
    prompt enumerates every permitted name, so a name outside it is the closed-set rule
    failing, and passing it downstream would put a concept nobody defined in front of a
    reviewer as though the system had vouched for it.
    """
    permitted = set(known_concepts)
    proposals: List[Proposal] = []

    # Which tables each concept was mapped to, across the whole response. Derived rather than
    # read from `alternatives`, because it *is* derivable: the alternatives to a table are the
    # other tables mapped to the same concept, and the response already says what those are.
    # Asking the model to restate them is asking it to get right something no model needs to
    # judge -- and on the first live run it did exactly that, marking a single-table concept
    # authoritative with an empty list.
    tables_by_concept: Dict[str, List[str]] = {}
    for mapping in response.mappings:
        tables_by_concept.setdefault(mapping.concept, []).append(mapping.table)

    for mapping in response.mappings:
        if permitted and mapping.concept not in permitted:
            continue
        proposals.append(
            Proposal(
                kind=ProposalKind.MAPPING,
                target=mapping.table,
                payload={
                    "table": mapping.table,
                    "concept": mapping.concept,
                    "confidence": mapping.confidence.value,
                    "evidence": mapping.evidence,
                },
                proposer=proposer,
            )
        )
        # An authoritative source is only a decision when there was a contest (G20). A
        # concept one table serves has no alternatives to beat, and a proposal saying "this
        # is the only table" is a review item with nothing to decide -- queue noise, which is
        # how a review queue becomes ignored and the removal that matters slips through.
        contenders = [
            t for t in tables_by_concept[mapping.concept] if t != mapping.table
        ]
        if mapping.authoritative and contenders:
            proposals.append(
                Proposal(
                    kind=ProposalKind.AUTHORITATIVE_SOURCE,
                    target=mapping.concept,
                    payload={
                        "concept": mapping.concept,
                        "table": _bare(mapping.table),
                        "alternatives": sorted(_bare(t) for t in contenders),
                        "rationale": mapping.evidence,
                    },
                    proposer=proposer,
                )
            )

    for item in response.collision_resolutions:
        if permitted and item.concept not in permitted:
            continue
        proposals.append(
            Proposal(
                kind=ProposalKind.COLUMN_MEANING,
                target=f"{item.table}.{item.column_name}",
                payload={
                    "table": item.table,
                    "column": item.column_name,
                    "concept": item.concept,
                    "means": item.means,
                    "joinable": item.joinable,
                },
                proposer=proposer,
            )
        )
    return proposals


def _bare(qualified: str) -> str:
    """The table name without its schema, which is how the graph keys tables by name."""
    return qualified.split(".")[-1]


def evidence_lines(response: MappingProposalSet) -> List[str]:
    """One readable line per draft, for the review by eye the verification asks for."""
    lines = [
        f"map    {m.confidence.value:<7} {m.table:<32} -> {m.concept}"
        + ("  [authoritative]" if m.authoritative else "")
        + f"  <- {m.evidence}"
        for m in response.mappings
    ]
    lines += [
        f"G19    {'':<7} {c.table}.{c.column_name:<20} -> {c.concept}: {c.means}"
        + ("" if c.joinable else "  (not joinable)")
        for c in response.collision_resolutions
    ]
    return lines
