"""The spec extractor's output schema, its closed sets, and the checks that keep it honest (R9).

Mirrors ``join_path_explainer.py`` in structure. What differs is that most of this agent's
closed sets vary by deployment -- business areas, grains, concept names, KPI identifiers --
so they cannot be Python enums enumerated once in the prompt. They are rendered from the
vocabulary, the semantic layer and the registry at run time (``render_closed_sets``), appended
to the prompt (``build_instructions``), and an output is checked against exactly the lists
that were rendered (``validate_spec``). The three sets that cannot vary -- a role, a time unit,
a delivery channel -- stay enums, and ``closed_sets_match_prompt`` keeps them and the prompt's
section 6 agreeing.

Nothing here decides anything: ``validate_spec`` reports a coined value, it does not fix one.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from egpt.agent import Agent
from pydantic import BaseModel, Field

from egpt_retail.contracts.kpi import KpiDefinition
from egpt_retail.contracts.semantic import SemanticRead, concept_vocabulary
from egpt_retail.contracts.vocabulary import VocabularySet

__all__ = [
    "SKILL_IDENTIFIER",
    "SKILL_VERSION",
    "PROMPT_PATH",
    "ClosedSets",
    "Delivery",
    "SpecEntityDraft",
    "SpecRole",
    "TimeUnit",
    "TimeWindowDraft",
    "UseCaseSpec",
    "build_instructions",
    "build_spec_extractor",
    "closed_sets_from",
    "closed_sets_match_prompt",
    "aclosed_sets_match_prompt",
    "render_closed_sets",
    "spec_prompt",
    "validate_spec",
]

SKILL_IDENTIFIER = "spec_extractor"
SKILL_VERSION = "1.0.0"
PROMPT_PATH = Path(__file__).parent / "spec_extractor.md"

#: The heading under which the deployment's lists are appended to the prompt.
RENDERED_HEADING = "## Closed sets for this deployment"


class SpecRole(str, Enum):
    """The contract's ``SpecEntityRole``: what a concept does in the question."""

    SUBJECT = "subject"
    FILTER = "filter"
    MEASURE = "measure"
    GROUP_BY = "group_by"


class TimeUnit(str, Enum):
    """The contract's ``TimeWindowUnit``; the same five members as ``contracts.kpi.TimeWindowUnit``."""

    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"


class Delivery(str, Enum):
    """The contract's ``DeliveryChannel``. In-app is the result; the other two are pointers (D13)."""

    IN_APP = "in_app"
    EMAIL = "email"
    SLACK = "slack"


class SpecEntityDraft(BaseModel):
    concept: str = Field(
        description="A concept name from the rendered concept list, verbatim."
    )
    # No `description=` on an enum field: see the template's section 6 constraint.
    role: SpecRole


class TimeWindowDraft(BaseModel):
    length: int = Field(ge=1, description="A positive number of units.")
    unit: TimeUnit


class UseCaseSpec(BaseModel):
    """The contract's ``Spec`` as the model returns it. ``extracted_by`` is added by code."""

    use_case_id: str = Field(description="The use_case_id input, copied unchanged.")
    question: str = Field(description="The question input, copied unchanged.")
    business_area: str = Field(
        description="One of the rendered business areas, verbatim."
    )
    entities: List[SpecEntityDraft] = Field(default_factory=list)
    kpi_identifiers: List[str] = Field(
        default_factory=list,
        description="Registered KPI identifiers from the rendered list, verbatim.",
    )
    grain: str = Field(description="One of the rendered grains, verbatim.")
    time_window: Optional[TimeWindowDraft] = None
    delivery: Delivery = Delivery.IN_APP
    notes: str = Field(
        default="",
        description="Empty on a clean extraction. Otherwise what could not be expressed.",
    )
    questions: List[str] = Field(
        default_factory=list,
        description="One clarifying question per unknown or ambiguity. Empty on a clean run.",
    )
    semantic_snapshot: str = Field(
        description="The snapshot.version input, copied unchanged."
    )

    def to_contract(self) -> Dict[str, Any]:
        """The API contract's ``Spec`` payload: this plus ``extracted_by`` from the prompt header."""
        payload = self.model_dump(mode="json")
        payload["extracted_by"] = {
            "identifier": SKILL_IDENTIFIER,
            "version": SKILL_VERSION,
        }
        return payload


@dataclass(frozen=True)
class ClosedSets:
    """The deployment-varying lists a spec may draw from. Rendered into the prompt, verbatim."""

    business_areas: Tuple[str, ...]
    grains: Tuple[str, ...]
    concepts: Tuple[str, ...]
    kpi_identifiers: Tuple[str, ...]


def closed_sets_from(
    reader: SemanticRead, kpis: Iterable[KpiDefinition], vocabularies: VocabularySet
) -> ClosedSets:
    """The lists from where they live: vocabulary for areas and grains, the semantic layer for
    concepts, the registry for identifiers. Data in, data out."""
    return ClosedSets(
        business_areas=tuple(sorted(vocabularies["business_area"].values)),
        grains=tuple(sorted(vocabularies["grain"].values)),
        concepts=concept_vocabulary(reader),
        kpi_identifiers=tuple(sorted(kpi.identifier for kpi in kpis)),
    )


def _bullets(values: Sequence[str]) -> str:
    return (
        ", ".join(f"`{value}`" for value in values)
        if values
        else "(none in this deployment)"
    )


def render_closed_sets(sets: ClosedSets) -> str:
    """Section 6's deployment-specific half, rendered not hand-typed (template, section 6)."""
    return "\n".join(
        [
            RENDERED_HEADING,
            "",
            "These are the only values you may emit for the fields named. Verbatim; never the nearest.",
            "",
            f"- `business_area`: {_bullets(sets.business_areas)}",
            f"- `grain`: {_bullets(sets.grains)}",
            f"- `entities[].concept`: {_bullets(sets.concepts)}",
            f"- `kpi_identifiers`: {_bullets(sets.kpi_identifiers)}",
            "",
        ]
    )


def build_instructions(sets: ClosedSets, prompt_path: Path = PROMPT_PATH) -> str:
    """The agent's instructions: the authored prompt plus the rendered lists."""
    return prompt_path.read_text() + "\n\n" + render_closed_sets(sets)


def spec_prompt(
    use_case_id: str,
    question: str,
    snapshot_version: str,
    supporting_questions: Sequence[str] = (),
) -> str:
    """The input as the prompt's section 3 declares it."""
    lines = [
        f"use_case_id: {use_case_id}",
        f"question: {question}",
        f'snapshot: {{"version": "{snapshot_version}"}}',
    ]
    if supporting_questions:
        lines.append("supporting_questions:")
        lines.extend(f"  - {q}" for q in supporting_questions)
    return "\n".join(lines)


def validate_spec(spec: UseCaseSpec, sets: ClosedSets) -> Tuple[str, ...]:
    """Every closed-set value the spec coined, as one sentence each. Empty means clean.

    The deterministic half of "values come from closed sets": the prompt asks, this checks.
    Reports and never repairs -- a coined value is the agent doing the one thing its rules
    forbid, and the cookbook asserts on this, not on a reviewer's eye.
    """
    problems: List[str] = []
    if spec.business_area not in sets.business_areas:
        problems.append(
            f"business_area {spec.business_area!r} is not in the vocabulary"
        )
    if spec.grain not in sets.grains:
        problems.append(f"grain {spec.grain!r} is not in the vocabulary")
    for entity in spec.entities:
        if entity.concept not in sets.concepts:
            problems.append(
                f"entity concept {entity.concept!r} is not in the semantic layer"
            )
    for identifier in spec.kpi_identifiers:
        if identifier not in sets.kpi_identifiers:
            problems.append(f"kpi identifier {identifier!r} is not in the registry")
    return tuple(problems)


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
    """Whether the three enums this module declares are enumerated in full in the prompt."""
    prompt = prompt_path.read_text()
    return {
        "role": _enumerated_values(prompt, "role") == {m.value for m in SpecRole},
        "unit": _enumerated_values(prompt, "unit") == {m.value for m in TimeUnit},
        "delivery": _enumerated_values(prompt, "delivery")
        == {m.value for m in Delivery},
    }


async def aclosed_sets_match_prompt(prompt_path: Path = PROMPT_PATH) -> Dict[str, bool]:
    """Async variant. The file read happens off the event loop."""
    return await asyncio.to_thread(closed_sets_match_prompt, prompt_path)


def build_spec_extractor(sets: ClosedSets, *, db: Any = None) -> Agent:
    """The agent, built per request because its instructions carry the deployment's lists.

    Not registered in ``build_agents``: that is pure construction at import, and these lists
    need the semantic layer and the registry. Built here with ``model=None`` so R8's routing
    (``spec_extractor`` is a reserved small-tier row) decides the model; the caller installs
    the provider with ``route_agents``.
    """
    return Agent(
        id=SKILL_IDENTIFIER,
        name=SKILL_IDENTIFIER,
        description="Extracts the typed use-case spec from a plain-language question (Lane R, R9).",
        instructions=build_instructions(sets),
        output_schema=UseCaseSpec,
        db=db,
    )
