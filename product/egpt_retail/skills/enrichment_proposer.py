"""K8 -- the enrichment proposer's output schema (D10, G5).

What a concept *means in this deployment*: the synonyms a person here would use for it, the
questions they would ask about it, the SQL fragment that scopes it, the fields that identify
a person, the tags that restrict it, and the metrics it could carry. This is the enrichment
the build plan calls the moat -- organisation-specific, not replicable, and the reason a
question asked in a customer's own words reaches the right data.

**The agent proposes. It never writes.** Every field it emits becomes a row in the proposals
table (K7); only a row a person accepted reaches the graph. That is structural rather than
promised: this module has no write path, and the one function that produces graph-bound
output (:func:`to_proposals`) produces ``Proposal`` objects in ``PENDING`` state.

**Two closed sets, two different reasons.** ``EnrichmentKind`` is a Python enum because it
enumerates the shapes of enrichment this system understands, and no customer adds a seventh.
``compliance_tag`` and ``business_area`` are vocabularies, rendered into the prompt at call
time, because their permitted terms are the shipped ontology plus a customer's overlay
(D9, G10) -- a hand-typed list is correct exactly until the first customer extends it, and
then it is silently short and the agent never proposes the term they added.
"""

from __future__ import annotations

import asyncio
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from pydantic import BaseModel, Field

from egpt_retail.contracts.vocabulary import VocabularySet, load_vocabulary_set
from egpt_retail.semantic.proposals import Proposal, ProposalKind

__all__ = [
    "PROMPT_PATH",
    "Confidence",
    "EnrichmentKind",
    "EnrichmentProposal",
    "EnrichmentProposalSet",
    "arender_prompt",
    "render_prompt",
    "to_proposals",
]

PROMPT_PATH = Path(__file__).parent / "enrichment_proposer.md"


class EnrichmentKind(str, Enum):
    """What one proposal contributes to a concept.

    A Python enum, not a vocabulary: these are the shapes of enrichment the semantic layer
    can store, which is a fact about this system rather than about a customer's business.
    ``Concept`` has exactly these fields; a seventh kind would need a seventh field.
    """

    SYNONYM = "synonym"
    QUESTION_PATTERN = "question_pattern"
    SQL_FILTER = "sql_filter"
    PERSONAL_DATA_FIELD = "personal_data_field"
    COMPLIANCE_TAG = "compliance_tag"
    CANDIDATE_METRIC = "candidate_metric"


class Confidence(str, Enum):
    """How strongly the evidence supports the proposal.

    Not a probability. A reviewer clearing a bulk accept screen (K7) reads this to decide
    what to look at first, so the values have to mean something to a person.
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class EnrichmentProposal(BaseModel):
    """One thing proposed about one concept."""

    value: str = Field(
        description=(
            "The proposed term, question, SQL fragment, column name, tag or metric name. "
            "For a compliance_tag this must be a term from the vocabulary in section 6."
        )
    )
    # No `description=` on the enum-typed fields: pydantic serialises them to a `$ref`, and a
    # strict structured-output schema rejects a `$ref` carrying sibling keywords -- Azure
    # returns a 400 before the model runs. Their meanings live in section 6 of the prompt.
    kind: EnrichmentKind
    confidence: Confidence
    evidence: str = Field(
        description=(
            "The table, column, sampled value or corpus document this derives from. "
            "A proposal that cannot name one was invented, not derived."
        )
    )


class EnrichmentProposalSet(BaseModel):
    """Everything proposed for one concept, plus the refusal channel."""

    concept: str = Field(description="The concept, copied from the input.")
    business_area: str = Field(
        default="",
        description=(
            "The business area this concept belongs to -- a term from the vocabulary in "
            "section 6, or empty when the evidence does not support one."
        ),
    )
    proposals: List[EnrichmentProposal] = Field(
        default_factory=list,
        description="Empty when the input was insufficient. Never padded to look productive.",
    )
    notes: str = Field(
        default="",
        description="Empty on a clean run. Otherwise what was missing or why the list is short.",
    )


def render_prompt(
    *,
    vocabularies: Optional[VocabularySet] = None,
    prompt_path: Path = PROMPT_PATH,
) -> str:
    """The prompt with its customer-varying closed sets filled in.

    ``business_area`` and ``compliance_tag`` are rendered from the vocabulary rather than
    written into the file, so a deployment that extended its ontology gets an agent that can
    propose the terms it added. The two placeholders are the only substitution: everything
    else in the prompt is the same for every customer, which is what makes seven prompts
    reviewable against each other.
    """
    vocabularies = vocabularies or load_vocabulary_set()
    prompt = prompt_path.read_text()
    for name in ("business_area", "compliance_tag"):
        placeholder = "{{" + name + "}}"
        if placeholder not in prompt:
            raise ValueError(
                f"{prompt_path.name} has no {placeholder} placeholder, so the {name} "
                "vocabulary would never reach the model and it would invent terms."
            )
        prompt = prompt.replace(placeholder, vocabularies[name].render_for_prompt())
    return prompt


async def arender_prompt(
    *,
    vocabularies: Optional[VocabularySet] = None,
    prompt_path: Path = PROMPT_PATH,
) -> str:
    """Async variant. The file read happens off the event loop."""
    return await asyncio.to_thread(
        render_prompt, vocabularies=vocabularies, prompt_path=prompt_path
    )


#: Which ``Concept`` field each kind contributes to, and whether it is a list.
_FIELD_FOR_KIND = {
    EnrichmentKind.SYNONYM: ("synonyms", True),
    EnrichmentKind.QUESTION_PATTERN: ("question_patterns", True),
    EnrichmentKind.SQL_FILTER: ("sql_filter", False),
    EnrichmentKind.PERSONAL_DATA_FIELD: ("personal_data_fields", True),
    EnrichmentKind.COMPLIANCE_TAG: ("compliance_tags", True),
    # Candidate metrics are names an agent thought of, not registry identifiers. They ride
    # on the proposal for K12 to turn into KPI proposals; `Concept.metrics` is derived from
    # the registry mirror (K11) and is never written from here.
    EnrichmentKind.CANDIDATE_METRIC: ("candidate_metrics", True),
}


def to_proposals(
    response: EnrichmentProposalSet,
    *,
    proposer: str,
    vocabularies: Optional[VocabularySet] = None,
    ontology_concept: Any = None,
) -> List[Proposal]:
    """Turn one agent response into rows for the proposals table.

    **One proposal per concept, not one per suggestion.** A reviewer accepting "Contact" is
    accepting a concept as enriched -- its synonyms, its filter, its tags together -- because
    that is the unit the graph writes and the unit that makes sense to judge. Twenty separate
    rows for one concept would be twenty decisions about fragments, and a reviewer who
    accepted the synonyms and rejected the filter would leave a concept in a state no single
    proposal describes.

    Returns an empty list for a refusal: an agent that found nothing proposes nothing, and a
    row saying "nothing" is a review item with no decision to make.

    ``ontology_concept`` is the shipped concept this enrichment is for. Its description and
    business area are facts about the ontology, not things an agent proposes per deployment,
    so they fill the payload when the agent left them out -- and the payload never carries an
    empty description, because the graph write treats an empty scalar as "leave it alone" and
    a proposal that reset a concept's description on acceptance was the P3 verification's
    first finding.
    """
    if not response.proposals:
        return []
    vocabularies = vocabularies or load_vocabulary_set()

    payload: Dict[str, Any] = {"name": response.concept}
    shipped_description = (
        str(ontology_concept.description)
        if ontology_concept is not None
        and getattr(ontology_concept, "description", None)
        else ""
    )
    if shipped_description:
        payload["description"] = shipped_description
    evidence: List[str] = []
    for item in response.proposals:
        field, is_list = _FIELD_FOR_KIND[item.kind]
        if item.kind is EnrichmentKind.COMPLIANCE_TAG:
            # Refuse a tag outside the closed set here rather than at the graph. The model was
            # given the list; a term that is not in it is the failure the closed-set rule
            # exists to catch, and letting it through would put an unreviewable tag in front
            # of a person as if the system had vouched for it.
            vocabularies.validate("compliance_tag", item.value)
        if is_list:
            payload.setdefault(field, [])
            if item.value not in payload[field]:
                payload[field].append(item.value)
        else:
            payload[field] = item.value
        evidence.append(
            f"{item.kind.value}={item.value} ({item.confidence.value}): {item.evidence}"
        )

    area = response.business_area or (
        getattr(ontology_concept, "business_area", None) if ontology_concept else None
    )
    if area:
        vocabularies.validate("business_area", area)
        payload["business_area"] = area

    return [
        Proposal(
            kind=ProposalKind.ENRICHMENT,
            target=response.concept,
            payload=payload,
            proposer=proposer,
        )
    ]


def evidence_lines(response: EnrichmentProposalSet) -> Sequence[str]:
    """One readable line per suggestion, for the review-by-eye the package's verification asks for."""
    return [
        f"{item.kind.value:<21} {item.confidence.value:<7} {item.value}  <- {item.evidence}"
        for item in response.proposals
    ]
