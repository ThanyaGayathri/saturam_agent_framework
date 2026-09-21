"""K15's model half -- grounding the prompt for each model step a compiled workflow carries.

**The template is the shape, and that applies to prompts as much as to steps.** Two Inform use
cases both carry an ``explanation`` step, and what that step does is the same in both: read the
rows, state the movement, never judge significance. Authoring that from scratch twice would
produce two prompts that differ in wording, drift apart under editing, and cannot be reviewed
against each other -- for no gain, because nothing about the difference between the two use
cases lives in those sentences.

So the invariant part is a **role template** (:data:`ROLE_TEMPLATES`), written once and shared
by every use case of that role, and the model supplies only what genuinely varies: what these
particular metrics mean in this deployment, the rules that arise from them, and a worked
example using them. That is the same template-plus-parameters discipline the compiler applies
to steps, one level up.

Three things a model is never asked for, because none is a judgement and all are what a model
quietly drops or paraphrases: the header (whose values follow from the step), the four standing
rules that must appear verbatim in every skill, and section 6's closed sets (rendered from the
vocabulary). :class:`StepPromptDraft` has no field for any of them, so an authored prompt is
conforming by construction rather than by the authoring agent having remembered.

``model_tier`` follows from the role under G6 -- an agent that could pick its own tier would
pick frontier.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from pydantic import BaseModel, ConfigDict, Field

from egpt_retail.contracts.use_case import TIER_FOR_ROLE
from egpt_retail.contracts.vocabulary import VocabularySet, load_vocabulary_set

__all__ = [
    "PROMPT_PATH",
    "ROLE_TEMPLATES",
    "SECTION_HEADINGS",
    "SKILL_VERSION",
    "STANDING_RULES",
    "AuthoredPrompt",
    "RoleTemplate",
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


@dataclass(frozen=True)
class InputSpec:
    """One row of a role template's section 3 table."""

    name: str
    source: str
    required: bool
    notes: str = ""


@dataclass(frozen=True)
class RoleTemplate:
    """Everything about a model step that is the same for every use case of its role.

    Written once, by hand, reviewed once. The parts a specific use case changes are supplied
    by :class:`StepPromptDraft` and slotted in around this.
    """

    role: str
    identity: str
    inputs: Tuple[InputSpec, ...]
    phases: Tuple[str, ...]
    rules: Tuple[str, ...]
    output_notes: str
    refusals: Tuple[str, ...]
    #: The tools the role's agent may call inside its step -- the "agentic interior" of the
    #: review's *declared skeleton, agentic interior*. Names, not objects: Lane K says what a
    #: role needs, Lane R binds each name to a tool at activation. A closed set here for the
    #: same reason executors are: an agent that could pick its own tools would pick a writer.
    tools: Tuple[str, ...] = ()


ROLE_TEMPLATES: Mapping[str, RoleTemplate] = {
    "explanation": RoleTemplate(
        role="explanation",
        identity=(
            "You explain how the numbers in this use case moved over the reported period. A "
            "person reads what you write and decides what to do about it; you decide nothing.\n\n"
            "You do not judge whether a movement is significant, whether the data is fresh "
            "enough, or whether this use case should have run. Significance is a threshold "
            "comparison and belongs to the Alert shape, where `evaluate_alert_condition` "
            "computes it. Freshness was decided by `check_preflight` before you were called. "
            "Both verdicts override anything you might infer, and your schema has no field for "
            "either.\n\n"
            "You are not the proposal agent. You never suggest a change to any system."
        ),
        inputs=(
            InputSpec("rows", "read_gold", True, "One row per KPI per period."),
            InputSpec("freshness", "read_gold", True, "How current gold is, per KPI."),
            InputSpec(
                "comparison", "compare_to_baseline", False, "The prior-period delta."
            ),
        ),
        phases=(
            "Read every row. Note which periods are present and which are absent.",
            "State the movement in plain numbers: what it was, what it is, the difference.",
            "Where a comparison is supplied, report it. Never compute one yourself from "
            "rows the comparison step did not produce.",
            "Emit the schema.",
        ),
        rules=(
            "Never state a percentage change when the prior period's value is zero. Say the "
            "prior period had none.",
            "Never describe a movement as good, bad, concerning or significant. Report the "
            "direction and the size, and stop.",
            "Where a KPI's value is null, say the metric did not compute. Never report zero.",
        ),
        output_notes=(
            "One paragraph per KPI, in the order the rows arrive. Numbers as they appear in "
            "gold, with the units the KPI declares."
        ),
        refusals=(
            "No rows for the reported period: return the empty schema and say gold held "
            "nothing for it.",
            "Rows present but every value null: return the empty schema and say the metrics "
            "did not compute.",
        ),
        # Read-only by construction. An explanation may look up what a column means and
        # may run a guarded read to check a figure; it may not write anywhere.
        tools=("semantic_read", "sql_through_guard"),
    ),
    "proposal": RoleTemplate(
        role="proposal",
        identity=(
            "You propose changes for this use case to make, from the candidates and metrics "
            "the earlier steps produced. Every proposal you make is then filtered by "
            "deterministic steps, and in the Recommend shape a person approves it before "
            "anything is written.\n\n"
            "You do not decide which proposals stand. `apply_hard_limits` strikes the ones "
            "that breach a guardrail and logs the rule that struck them; in the Act shape "
            "`apply_per_run_cap` holds the overflow for the next run. Neither consults you, "
            "and a proposal you rank highest is struck exactly as readily as any other.\n\n"
            "You are not the explanation agent. Describing what happened is not your step."
        ),
        inputs=(
            InputSpec("candidates", "select_candidates", True, "The rows in scope."),
            InputSpec("rows", "read_gold", True, "The metric values per candidate."),
            InputSpec(
                "guardrails", "the spec", True, "The limits that will be applied."
            ),
        ),
        phases=(
            "Read the candidates and their metric values.",
            "For each, decide whether a change is warranted and what change.",
            "State the expected effect of each proposal in the metric's own units.",
            "Emit the schema. Propose nothing you cannot tie to a candidate row.",
        ),
        rules=(
            "Never propose a change to a candidate that is not in the input. A proposal "
            "about a row nobody selected cannot be applied and cannot be audited.",
            "Never omit a proposal because you expect a guardrail to strike it. The strike is "
            "logged with its rule, and a proposal you suppressed is invisible.",
            "State the expected effect as a number, not as a direction. 'Raises margin' is "
            "not reviewable; 'raises margin by 1.4 points' is.",
        ),
        output_notes=(
            "One entry per proposal, each naming its candidate, the change and the expected "
            "effect. An empty list is a valid answer."
        ),
        refusals=(
            "No candidates supplied: return the empty schema and say the selection step "
            "produced nothing.",
            "Guardrails absent from the input: return the empty schema and say so. Proposing "
            "without knowing the limits invites a run where everything is struck.",
        ),
        # Still read-only: proposing is not writing. The write is `write_to_target`, a
        # declared step after the limits and the gate, and no tool here reaches it.
        tools=("semantic_read", "sql_through_guard", "domain_prose"),
    ),
    "extraction": RoleTemplate(
        role="extraction",
        identity=(
            "You read one document and fill one schema from it: the extraction template this "
            "use case declares, field by field. The document is a purchase order, an invoice, "
            "a return request -- whatever the template says -- sent by a customer or a supplier "
            "and screened before it reached you.\n\n"
            "You extract; you never infer. A field that is not on the document is null, with "
            "no guess in its place. You do not decide who the customer is, which product a "
            "line names, or whether the order should be created: `resolve_entities` matches "
            "your text against master data with scored rules, and `apply_hard_limits` "
            "decides what stands. Your job ends when the schema is full and every value can "
            "be pointed at on the page.\n\n"
            "You are not the proposal agent and not the explanation agent. You describe "
            "nothing and recommend nothing."
        ),
        inputs=(
            InputSpec(
                "document",
                "screen_documents",
                True,
                "The screened document: pages of text, or images where there is no text layer.",
            ),
            InputSpec(
                "template",
                "the spec",
                True,
                "The extraction template: the header and line fields to fill, which are required, and what each usually looks like on the page.",
            ),
            InputSpec(
                "example",
                "the spec",
                False,
                "One example document of this format with its expected values, when the use case supplied one.",
            ),
        ),
        phases=(
            "Read the whole document once. Note the page and region where each labelled block sits: the header block, the line table, notes under lines, terms.",
            "Fill the header fields from their labels. Where a label is absent, look for the template's alternative labels; where none is present, the field is null.",
            "Fill one line per row of the line table, in the document's order. A line with no quantity is still a line, with quantity null.",
            "For every value, record the page and region it was read from and how confident you are that the label matched.",
            "Emit the schema.",
        ),
        rules=(
            "Never fill a field with a value the document does not show. A due date the "
            "document does not carry is null, not today, not the order date plus a week.",
            "Never merge two lines or split one. The line count is a fact of the document.",
            "Copy numbers as the document writes them, with their units as written; a unit "
            "conversion is `resolve_entities`' job, not yours.",
            "Never fill a customer or product identifier from your own knowledge of the world. "
            "If the document names 'Acme', the customer name is 'Acme' and nothing more.",
            "Where the example document is supplied, use it to learn where this format puts "
            "its fields -- never to copy its values.",
        ),
        output_notes=(
            "The template's schema exactly: a header object and a lines array, every field "
            "present, unknown values null, each value paired with its confidence and page "
            "region. No prose outside the schema."
        ),
        refusals=(
            "No readable text and no images: return the empty schema and say the document could not be read.",
            "The document is plainly not the template's kind (terms and conditions, a certificate, a reply thread): return the empty schema and say what it appears to be.",
            "More than one document of the template's kind in the input: return the empty schema and say how many; the screen step should have split them.",
        ),
        # Read-only, and read-only of one thing: the document. No semantic read, no SQL --
        # the extractor must not be able to look anything up, or it will "correct" the
        # document from master data and the resolver's scores become meaningless.
        tools=("document_read",),
    ),
}


class StepPromptDraft(BaseModel):
    """Only what this particular use case changes. The rest comes from the role template.

    Deliberately small. Everything a model could be asked for here that is the same across
    two use cases of one role is in :data:`ROLE_TEMPLATES` instead -- authoring it per use
    case would produce prompts that differ in wording and not in meaning, which is the
    opposite of reviewable.
    """

    model_config = ConfigDict(extra="forbid")

    grounding: str = Field(
        description=(
            "What these particular metrics mean in this deployment, and anything about them a "
            "reader of the numbers would otherwise get wrong. Two or three sentences, traceable "
            "to the KPIs and semantic context supplied."
        )
    )
    metric_rules: List[str] = Field(
        default_factory=list,
        description=(
            "Rules that arise from THESE metrics and would not apply to another use case of "
            "the same role -- a ratio that is undefined at zero denominator, a count that "
            "excludes cancellations. Empty where the role's own rules already cover it."
        ),
    )
    worked_example: str = Field(
        description="A concrete input using these KPIs, and the output it should produce."
    )
    near_miss: str = Field(
        description="An output that looks right and is not. A plausible failure, not an absurd one."
    )
    near_miss_reason: str = Field(
        description="Why the near miss is wrong, in one or two sentences."
    )
    extra_refusals: List[str] = Field(
        default_factory=list,
        description=(
            "Refusal cases specific to this data, beyond the role's own. Empty is fine -- the "
            "role template always supplies at least one."
        ),
    )


class AuthoredPrompt(BaseModel):
    """One finished prompt, plus what assembly refused to take on trust."""

    agent_id: str
    role: str
    model_tier: str
    markdown: str
    output_schema: str = ""
    tools: List[str] = Field(default_factory=list)
    problems: List[str] = Field(default_factory=list)

    @property
    def publishable(self) -> bool:
        return not self.problems

    def as_component(self, *, tenant_id: str) -> Dict[str, Any]:
        """The second artifact: an agent component the components API can publish.

        The review is explicit that generation produces two artifacts -- *"the workflow
        definition... and the agent components for the model steps, each carrying its
        generated prompt"* -- and that a deployed use case runs *"with the entire memory tiers
        connected"*. This is the agent half, with everything activation needs and nothing it
        has to guess: the prompt, the schema, the tier R8 must route it on, the tools Lane R
        binds, and the memory scope K1's convention fixes for a scheduled run -- the agent's
        own partition under the scheduler identity, never a person's.
        """
        from egpt_retail.memory.scopes import SYSTEM_USER_ID

        return {
            "id": self.agent_id,
            "name": self.agent_id,
            "role": self.role,
            "instructions": self.markdown,
            "output_schema": self.output_schema,
            "model_tier": self.model_tier,
            "tools": list(self.tools),
            "memory": {
                "write_scope": {
                    "tenant_id": tenant_id,
                    "agent_id": self.agent_id,
                    "user_id": SYSTEM_USER_ID,
                },
                "read_scope": {"tenant_id": tenant_id, "user_id": SYSTEM_USER_ID},
            },
            "publishable": self.publishable,
            "problems": list(self.problems),
        }


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
        following = re.search(r"^##\s+\d\.", markdown[body_start:], re.MULTILINE)
        body = (
            markdown[body_start : body_start + following.start()]
            if following
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
    """Assemble one prompt: the role's template, this use case's grounding, K14's frame."""
    vocabularies = vocabularies or load_vocabulary_set()
    problems: List[str] = []

    template = ROLE_TEMPLATES.get(role)
    if template is None:
        # A role with no template is a role nobody has written the invariant half for, and
        # inventing one per use case is exactly what the role templates exist to prevent.
        return AuthoredPrompt(
            agent_id=agent_id,
            role=role,
            model_tier=TIER_FOR_ROLE.get(role, "unassigned"),
            markdown="",
            problems=[
                f"role {role!r} has no entry in ROLE_TEMPLATES, so the invariant half of its "
                "prompt does not exist. Write one and review it once, rather than having a "
                "model author it per use case."
            ],
        )

    tier = TIER_FOR_ROLE.get(role)
    if tier is None:
        problems.append(
            f"role {role!r} has no model_tier ruling in K14 section 1; add one to "
            "TIER_FOR_ROLE with the G6 argument before publishing this step"
        )
        tier = "unassigned"
    if not draft.grounding.strip():
        problems.append(
            "the draft carries no grounding, so the prompt says nothing this use case's "
            "metrics need that the role template did not already say"
        )

    inputs_table = "\n".join(
        f"| `{row.name}` | {row.source} | {'yes' if row.required else 'no'} | {row.notes} |"
        for row in template.inputs
    )
    phases = "\n".join(
        f"{i}. {line}" for i, line in enumerate(template.phases, start=1)
    )
    rules = "\n".join(f"- {rule}" for rule in (*template.rules, *draft.metric_rules))
    refusals = "\n".join(
        f"- {line}" for line in (*template.refusals, *draft.extra_refusals)
    )
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

Assembled by `step_prompt` {SKILL_VERSION} from the `{role}` role template, for use case
`{use_case_id}`. The workflow definition reads the block above.

## 2. Identity

{template.identity.strip()}

**In this use case specifically.** {draft.grounding.strip()}

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

{rules}

## 6. Output

Schema: `{output_schema}`.

{template.output_notes.strip()}

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
        output_schema=output_schema,
        tools=list(template.tools),
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
    template = ROLE_TEMPLATES.get(role)
    prompt = prompt_path.read_text()
    filled: Dict[str, str] = {
        "role": role,
        "use_case_id": use_case_id,
        "question": question,
        "kpis": ", ".join(f"`{k}`" for k in kpi_identifiers) or "(none bound)",
        "semantic_context": semantic_context or "(none supplied)",
        "business_area": vocabularies["business_area"].render_for_prompt(),
        "role_template": _render_role_template(template),
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
    # The mirror of the check above, and it caught a real one: a placeholder present in the
    # file but absent from `filled` reaches the model as the literal text `{{standing_rules}}`,
    # which no exception and no eye-reading of the Python would have found.
    unfilled = sorted(set(re.findall(r"\{\{[a-z_]+\}\}", prompt)))
    if unfilled:
        raise ValueError(
            f"{prompt_path.name} still holds {', '.join(unfilled)} after rendering, so the "
            "model would receive the placeholder as text. Add each to the filled mapping."
        )
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


def _render_role_template(template: Optional[RoleTemplate]) -> str:
    """The role's invariant half, shown to the authoring agent so it does not repeat it."""
    if template is None:
        return "(no role template exists for this role -- see section 8)"
    rules = "\n".join(f"  - {rule}" for rule in template.rules)
    refusals = "\n".join(f"  - {line}" for line in template.refusals)
    return (
        f"**Identity, already written:**\n{template.identity}\n\n"
        f"**Rules, already written:**\n{rules}\n\n"
        f"**Refusals, already written:**\n{refusals}"
    )
