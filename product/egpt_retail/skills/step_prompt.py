"""K15's model half -- authoring the prompt for each model step a compiled workflow carries.

The compiler decides which steps exist; this decides what the model steps among them are told.
It is the part of K15 that needs judgment, and it is why the package sits in Lane K rather
than beside the runtime: writing an explanation prompt that is grounded in *this* deployment's
semantic layer and *this* use case's KPIs is not a template substitution.

**What the model authors is only the part that varies.** The eight-section template (K14) has
a header whose values follow from the step, four standing rules that must appear verbatim in
every skill, and a section 6 whose closed sets are rendered from the vocabulary. None of that
is a judgement, and all of it is what a model quietly drops or paraphrases. So
:func:`assemble` writes those, and :class:`StepPromptDraft` has no field for any of them --
the same construction guarantee K12 and K13 use, one level up. An authored prompt cannot omit
a standing rule, because the agent that authored it was never asked for one.

The header's ``model_tier`` follows from the step's role rather than from the model's opinion:
K14 fixes explanation on the small tier and suggestion on the frontier tier under G6, and a
proposal step is a suggestion. An agent that could choose its own tier would choose frontier.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from pydantic import BaseModel, ConfigDict, Field

from egpt_retail.contracts.use_case import TIER_FOR_ROLE
from egpt_retail.contracts.vocabulary import VocabularySet, load_vocabulary_set

__all__ = [
    "PROMPT_PATH",
    "SECTION_HEADINGS",
    "SKILL_VERSION",
    "STANDING_RULES",
    "TIER_FOR_ROLE",
    "AuthoredPrompt",
    "StepPromptDraft",
    "aassemble",
    "arender_prompt",
    "assemble",
    "missing_sections",
    "render_prompt",
]

PROMPT_PATH = Path(__file__).parent / "step_prompt.md"
SKILL_VERSION = "1.0.0"

#: The eight headings, in K14's order. An authored prompt is checked against this list rather
#: than against a count, because "eight sections present" and "the right eight in the right
#: order" are different claims and only the second one is useful.
SECTION_HEADINGS: Tuple[str, ...] = (
    "1. Header",
    "2. Identity",
    "3. Inputs",
    "4. Phases",
    "5. Rules",
    "6. Output",
    "7. Worked example",
    "8. Refusal and uncertainty",
)

#: K14 section 5, verbatim. Copied by code into every authored prompt.
#:
#: The template is explicit that these are copied *even where a sentence reads oddly for a
#: particular agent*, because reviewing seven prompts against each other is only possible when
#: the same four sentences appear in all seven. A model asked to reproduce them would
#: paraphrase one eventually, and the paraphrase would pass every review that reads for sense.
STANDING_RULES = """- You produce output for a person or a calling executor to act on. You never write to the
  graph, the registry or the warehouse yourself. Where your output is a proposal, a person
  accepts or rejects it (D10, G5); where it is an explanation, the executor that called you
  decides what to do with it. If your output reaches the graph without a human decision,
  that is a defect in the pipeline, not a licence for you.
- You never decide what is permitted, supported, feasible or safe. Those are set
  differences and deterministic checks computed elsewhere, and your view of them is not
  consulted.
- Every value you emit for a closed-set field comes from the enumerated list in section 6.
  If nothing in the list fits, use the refusal behaviour in section 8. Do not coin a new
  value, and do not pick the nearest one.
- Every claim you make is traceable to a named input. If you cannot point at the table,
  column, concept or document a statement came from, do not make the statement."""


#: What each role is permitted to write, for the header's ``writes`` field. Never "graph".
_WRITES_FOR_ROLE: Mapping[str, str] = {
    "explanation": "nothing",
    "proposal": "proposals",
}


class InputRow(BaseModel):
    """One row of the authored prompt's section 3 table."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="The input's name, as the step receives it.")
    source: str = Field(description="Which package or step produces it.")
    required: bool
    notes: str = Field(default="", description="What it contains, in a few words.")


class StepPromptDraft(BaseModel):
    """The judgement half of an authored prompt. Everything else is assembled.

    No header fields, no standing rules, no closed-set rendering: those are the parts a model
    drops or paraphrases, and none of them is a judgement. ``extra="forbid"`` so a future
    prompt edit inviting one of them fails here rather than producing a prompt whose rules
    quietly differ from the other six.
    """

    model_config = ConfigDict(extra="forbid")

    identity: str = Field(
        description=(
            "Section 2. Two or three paragraphs: what this step does, what it explicitly does "
            "not decide, and which neighbouring agent it is not."
        )
    )
    inputs: List[InputRow] = Field(
        default_factory=list,
        description="Section 3. Every input the step reads. Never empty.",
    )
    phases: List[str] = Field(
        default_factory=list,
        description=(
            "Section 4. Numbered working order, one line each, ending with emitting the "
            "schema."
        ),
    )
    specific_rules: List[str] = Field(
        default_factory=list,
        description=(
            "Section 5, after the four standing rules. The prohibitions particular to this "
            "step -- what it must not infer, claim or compute."
        ),
    )
    output_notes: str = Field(
        default="",
        description="Section 6. What the schema's fields mean here and how to fill them.",
    )
    worked_example: str = Field(
        description="Section 7. A concrete input and the output it should produce."
    )
    near_miss: str = Field(
        description="Section 7. An output that looks right and is not."
    )
    near_miss_reason: str = Field(
        description="Why the near miss is wrong, in one or two sentences."
    )
    refusals: List[str] = Field(
        default_factory=list,
        description=(
            "Section 8. One line per case where the step must return the empty schema with "
            "notes rather than answer. Never empty."
        ),
    )


class AuthoredPrompt(BaseModel):
    """One finished prompt, plus what assembly refused to take on trust."""

    agent_id: str
    role: str
    model_tier: str
    markdown: str
    problems: List[str] = Field(default_factory=list)

    @property
    def publishable(self) -> bool:
        return not self.problems


def missing_sections(markdown: str) -> List[str]:
    """Which of K14's eight headings are absent or empty, in template order.

    The authoring checklist's first item -- *"All eight sections present, in order, none
    empty"* -- as a function rather than as a box someone ticks. A heading followed by nothing
    but the next heading counts as absent, which is the failure a count of headings misses.
    """
    problems: List[str] = []
    positions: List[int] = []
    for heading in SECTION_HEADINGS:
        match = re.search(rf"^##\s+{re.escape(heading)}\s*$", markdown, re.MULTILINE)
        if match is None:
            problems.append(f"section {heading!r} is missing")
            positions.append(-1)
            continue
        positions.append(match.start())
        body_start = match.end()
        next_match = re.search(r"^##\s+\d\.", markdown[body_start:], re.MULTILINE)
        body = (
            markdown[body_start : body_start + next_match.start()]
            if next_match
            else markdown[body_start:]
        )
        if not body.strip():
            problems.append(f"section {heading!r} is present but empty")
    ordered = [p for p in positions if p >= 0]
    if ordered != sorted(ordered):
        problems.append("the eight sections are not in K14's order")
    return problems


def assemble(
    draft: StepPromptDraft,
    *,
    agent_id: str,
    role: str,
    output_schema: str,
    use_case_id: str,
    kpi_identifiers: Sequence[str] = (),
    semantic_snapshot: str = "",
    vocabularies: Optional[VocabularySet] = None,
    version: str = "1.0.0",
) -> AuthoredPrompt:
    """Assemble one authored prompt in K14's eight-section shape.

    The header, the four standing rules and section 6's closed sets are written here from
    inputs the model never saw, so an authored prompt is conforming by construction rather
    than by the authoring agent having remembered.
    """
    vocabularies = vocabularies or load_vocabulary_set()
    problems: List[str] = []
    tier = TIER_FOR_ROLE.get(role)
    if tier is None:
        # A role with no tier is a role K14 has no ruling on, and guessing one would put an
        # agent on a tier nobody chose. G6 says the tier is a real decision; this refuses to
        # make it silently.
        problems.append(
            f"role {role!r} has no model_tier ruling in K14 section 1; add one to "
            "TIER_FOR_ROLE with the G6 argument before publishing this step"
        )
        tier = "unassigned"
    if not draft.inputs:
        problems.append(
            "section 3 names no inputs, so nothing the step says is traceable"
        )
    if not draft.refusals:
        problems.append(
            "section 8 lists no refusal case, so the step can only ever answer"
        )

    inputs_table = (
        "\n".join(
            f"| `{row.name}` | {row.source} | {'yes' if row.required else 'no'} | {row.notes} |"
            for row in draft.inputs
        )
        or "| (none) | | | |"
    )
    phases = "\n".join(f"{i}. {line}" for i, line in enumerate(draft.phases, start=1))
    specific = "\n".join(f"- {rule}" for rule in draft.specific_rules)
    refusals = "\n".join(f"- {line}" for line in draft.refusals)
    kpis = ", ".join(f"`{k}`" for k in kpi_identifiers) or "(none bound)"

    markdown = f"""```
identifier:      {agent_id}
version:         {version}
model_tier:      {tier}
writes:          {_WRITES_FOR_ROLE.get(role, "nothing")}
output_schema:   {output_schema}
owner:           Lane K
```

## 1. Header

Authored by `step_prompt` {SKILL_VERSION} for the `{role}` step of use case `{use_case_id}`.
The workflow definition reads the block above.

## 2. Identity

{draft.identity.strip()}

## 3. Inputs

| Input | Source | Required | Notes |
|---|---|---|---|
{inputs_table}

You may read **only** these.

## 4. Phases

```
{phases}
```

## 5. Rules

{STANDING_RULES}

Specific to this step:

{specific or "- (none beyond the four above)"}

## 6. Output

Schema: `{output_schema}`.

{draft.output_notes.strip()}

**Business areas** -- the closed set, rendered from this deployment's vocabulary:
{vocabularies["business_area"].render_for_prompt()}

**Grains:** {vocabularies["grain"].render_for_prompt()}

**The KPIs this step may name:** {kpis}

**Semantic snapshot:** {semantic_snapshot or "(unbound)"}

## 7. Worked example

{draft.worked_example.strip()}

**Near-miss** -- this looks right and is not:

{draft.near_miss.strip()}

{draft.near_miss_reason.strip()}

## 8. Refusal and uncertainty

Return the schema with an empty result and a populated `notes` field naming what was missing.
Never return prose instead of the schema. Never fill a required enum with your best guess in
order to return something.

{refusals}
"""
    problems.extend(missing_sections(markdown))
    return AuthoredPrompt(
        agent_id=agent_id,
        role=role,
        model_tier=tier,
        markdown=markdown,
        problems=problems,
    )


async def aassemble(
    draft: StepPromptDraft,
    *,
    agent_id: str,
    role: str,
    output_schema: str,
    use_case_id: str,
    kpi_identifiers: Sequence[str] = (),
    semantic_snapshot: str = "",
    vocabularies: Optional[VocabularySet] = None,
    version: str = "1.0.0",
) -> AuthoredPrompt:
    """Async variant. Pure, so it exists to keep the calling convention uniform."""
    return await asyncio.to_thread(
        assemble,
        draft,
        agent_id=agent_id,
        role=role,
        output_schema=output_schema,
        use_case_id=use_case_id,
        kpi_identifiers=kpi_identifiers,
        semantic_snapshot=semantic_snapshot,
        vocabularies=vocabularies,
        version=version,
    )


def render_prompt(
    *,
    role: str,
    use_case_id: str,
    question: str,
    kpi_identifiers: Sequence[str] = (),
    semantic_context: str = "",
    vocabularies: Optional[VocabularySet] = None,
    prompt_path: Path = PROMPT_PATH,
) -> str:
    """The authoring agent's own instructions, with this step's context filled in."""
    vocabularies = vocabularies or load_vocabulary_set()
    prompt = prompt_path.read_text()
    filled: Dict[str, str] = {
        "role": role,
        "use_case_id": use_case_id,
        "question": question,
        "kpis": ", ".join(f"`{k}`" for k in kpi_identifiers) or "(none bound)",
        "semantic_context": semantic_context or "(none supplied)",
        "business_area": vocabularies["business_area"].render_for_prompt(),
        "standing_rules": STANDING_RULES,
    }
    for name, value in filled.items():
        placeholder = "{{" + name + "}}"
        if placeholder not in prompt:
            raise ValueError(
                f"{prompt_path.name} has no {placeholder} placeholder, so {name} would never "
                "reach the model and it would invent a value."
            )
        prompt = prompt.replace(placeholder, value)
    return prompt


async def arender_prompt(
    *,
    role: str,
    use_case_id: str,
    question: str,
    kpi_identifiers: Sequence[str] = (),
    semantic_context: str = "",
    vocabularies: Optional[VocabularySet] = None,
    prompt_path: Path = PROMPT_PATH,
) -> str:
    """Async variant. The file read happens off the event loop."""
    return await asyncio.to_thread(
        render_prompt,
        role=role,
        use_case_id=use_case_id,
        question=question,
        kpi_identifiers=kpi_identifiers,
        semantic_context=semantic_context,
        vocabularies=vocabularies,
        prompt_path=prompt_path,
    )
