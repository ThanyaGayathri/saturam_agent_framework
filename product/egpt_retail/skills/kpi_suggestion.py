"""K12 -- the KPI suggestion agent's output, and the executor that stamps its feasibility.

The agent proposes candidate KPIs from the ontology, the prose and the semantic layer. What
it may **not** do is say whether one is feasible: that is a set difference over source tables
(K11), and the moment a model decides it the ready and blocked badges stop meaning anything.

**So the agent's schema has no feasibility field at all.** It returns
:class:`KpiSuggestionDraft` -- what it proposes and what it derived that from -- and
:func:`assemble` turns each draft into the contract's ``KpiSuggestion`` by calling
``assess_feasibility`` and stamping the verdict. A model that cannot express a verdict cannot
emit a wrong one, which is a stronger guarantee than a prompt asking it not to.

**Drift with the API contract is impossible by construction here.** :func:`assemble` returns
``egpt_retail.api.models.KpiSuggestion`` -- the model generated from the OpenAPI schema the
discovery screen renders -- rather than a hand-written copy of it. The plan requires these to
render "without transformation", so a field the agent invents and the screen does not know
would be an empty cell in front of a customer. Building the generated type means a mismatch
is an error at construction, in this process, rather than a blank on a screen.
"""

from __future__ import annotations


import asyncio
import re
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional, Sequence, Tuple

from pydantic import BaseModel, ConfigDict, Field

from egpt_retail.api.models import (
    Confidence,
    Feasibility,
    FeasibilityVerdict,
    KpiSuggestion,
    ResultShape,
    SkillRef,
)
from egpt_retail.contracts.kpi import (
    KpiDefinition,
    KpiParameter,
    ParameterKind,
    assess_feasibility,
)
from egpt_retail.contracts.kpi import ResultShape as ContractResultShape
from egpt_retail.contracts.vocabulary import VocabularySet, load_vocabulary_set

if TYPE_CHECKING:
    from egpt_retail.semantic.proposals import Proposal

__all__ = [
    "PROMPT_PATH",
    "SKILL",
    "AssemblyReport",
    "KpiSuggestionDraft",
    "KpiSuggestionDraftSet",
    "aassemble",
    "arender_prompt",
    "assemble",
    "render_prompt",
]

PROMPT_PATH = Path(__file__).parent / "kpi_suggestion.md"

#: Stamped onto every suggestion. The version is the prompt's, so a score drop after an edit
#: is attributable to the edit (G13, and the ``SkillRef`` contract's own reason for existing).
SKILL = SkillRef(identifier="kpi_suggestion", version="1.0.0")

#: A KPI identifier is used as a Python identifier and a view name; both refuse the rest.
_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]{2,62}$")


class DraftConfidence(str, Enum):
    """How strongly the evidence supports the suggestion.

    Mirrors the contract's ``Confidence`` rather than importing it, because a generated enum
    is regenerated from the spec and this one is read by a model through the prompt. They are
    checked equal in the tests, which is the same guard ``enums_match_vocabulary`` gives the
    vocabulary-backed sets.
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class DraftResultShape(str, Enum):
    """One value per grain per run, or many rows. Mirrors the contract's ``ResultShape``."""

    SCALAR = "scalar"
    ROWSET = "rowset"


class KpiSuggestionDraft(BaseModel):
    """One candidate KPI, as the agent proposes it.

    Deliberately **not** the contract's ``KpiSuggestion``: no ``feasibility``, because that is
    the executor's to decide, and no ``proposed_by``, because a model asserting which skill
    produced it proves nothing. Both are added by :func:`assemble`.

    ``extra="forbid"`` because the absence of a feasibility field is the guarantee, and
    pydantic's default is to *ignore* an unexpected key rather than refuse it. Ignoring is
    safe -- the verdict would be dropped -- but it means a model emitting one is invisible,
    and the first sign of a prompt drifting toward deciding feasibility is exactly the thing
    worth seeing. It also makes the JSON schema explicit about it for strict structured
    output, which is what the provider enforces against.
    """

    model_config = ConfigDict(extra="forbid")

    identifier: str = Field(
        description="snake_case, unique within the response, stable across runs."
    )
    name: str = Field(description="What a person would call this metric.")
    description: str = Field(description="One or two sentences. What it measures.")
    business_area: str = Field(
        description="A term from the business_area vocabulary in section 6."
    )
    grain: str = Field(description="A term from the grain vocabulary in section 6.")
    concept: Optional[str] = Field(
        default=None,
        description="The ontology concept this metric hangs off, or null.",
    )
    # No `description=` on the enum-typed fields: an enum serialises to a `$ref`, and a strict
    # structured-output schema rejects a `$ref` carrying sibling keywords -- Azure returns a
    # 400 before the model runs. Their meanings live in section 6, where the model reads them.
    result_shape: DraftResultShape
    confidence: DraftConfidence
    rationale: str = Field(
        description="One sentence naming the tables or concepts this derives from."
    )
    derived_from_tables: List[str] = Field(
        description=(
            "Every table the formula reads, as the harvest names them. Never empty: a "
            "suggestion traceable to no table was invented rather than derived."
        )
    )
    proposed_sql: Optional[str] = Field(
        default=None,
        description=(
            "The formula, if you can write one that reads only the tables above. Null "
            "rather than a guess."
        ),
    )
    catalogue_source: Optional[str] = Field(
        default=None,
        description=(
            "The catalogue metric name this corresponds to, where one does. Cites a curated "
            "definition instead of inventing a metric."
        ),
    )


class KpiSuggestionDraftSet(BaseModel):
    """The whole response, with the refusal channel section 8 requires."""

    model_config = ConfigDict(extra="forbid")

    suggestions: List[KpiSuggestionDraft] = Field(default_factory=list)
    notes: str = Field(
        default="",
        description="Empty on a clean run; otherwise what was missing or why the list is short.",
    )


class AssemblyReport(BaseModel):
    """What :func:`assemble` accepted, and what it dropped and why.

    Dropped drafts are reported rather than raised on: one malformed suggestion out of twelve
    should cost a reviewer that one, not the other eleven. The same rule K8's enrichment
    proposer arrived at after review round 2.
    """

    suggestions: List[KpiSuggestion] = Field(default_factory=list)
    #: Identifier -> the curated catalogue metric it corresponds to, or None where the agent
    #: inferred it from the schema. Carried beside the suggestion because the contract's
    #: ``KpiSuggestion`` has no field for it (R-16), and the two deserve different trust: a
    #: catalogue metric has a vetted definition someone stands behind; an inferred one is a
    #: guess from column names that happens to parse.
    catalogue_source: Dict[str, Optional[str]] = Field(default_factory=dict)
    #: Identifiers the agent proposed without a formula. They are real suggestions -- the
    #: discovery screen shows them -- but they cannot become KPI proposals, because a proposal
    #: carries the whole definition and the registry adopts it without a second opinion.
    without_formula: List[str] = Field(default_factory=list)
    dropped: List[str] = Field(default_factory=list)
    notes: str = ""

    def as_proposals(self, *, proposer: str = "") -> List["Proposal"]:
        """The accepted suggestions as K7 proposals, for the adopt action D10 requires.

        Build plan section 06: candidates the suggestion agent derives *"are proposals, so
        they carry an adopt action and enter the registry only when a person accepts them."*
        The registry's ``adopt_accepted`` already consumes accepted ``KPI`` proposals; this is
        the producer that was missing.

        Only suggestions carrying ``proposed_sql`` become proposals. A KPI proposal is the whole
        definition -- ``validate_payload`` builds a ``KpiDefinition`` from it, and adoption saves
        that without looking again -- so a placeholder formula would be adopted as a formula.
        The rest are listed in ``without_formula`` rather than dropped: they are still worth
        showing, and asking the agent for a formula is the next step, not a failure.
        """
        from egpt_retail.semantic.proposals import Proposal, ProposalKind

        proposer = proposer or f"{SKILL.identifier}@{SKILL.version}"
        proposals: List[Proposal] = []
        for suggestion in self.suggestions:
            if not suggestion.proposed_sql:
                continue
            payload: Dict[str, Any] = {
                "identifier": suggestion.identifier,
                "name": suggestion.name,
                "description": suggestion.description,
                "sql": suggestion.proposed_sql,
                "grain": suggestion.grain.value
                if hasattr(suggestion.grain, "value")
                else suggestion.grain,
                "business_area": suggestion.business_area,
                "source_tables": list(suggestion.derived_from_tables),
                "result_shape": suggestion.result_shape.value,
                "concept": suggestion.concept,
                "parameters": (
                    [
                        {
                            "name": "scope",
                            "kind": "predicate",
                            "description": "Concept scoping filter.",
                        }
                    ]
                    if "{scope}" in suggestion.proposed_sql
                    else []
                ),
                # Provenance for the review screen's badge. `_kpi_from` ignores keys it does
                # not build from, so this rides along in the jsonb without affecting adoption.
                "catalogue_source": self.catalogue_source.get(suggestion.identifier),
                "feasibility": (
                    suggestion.feasibility.verdict.value
                    if suggestion.feasibility
                    else "unknown"
                ),
                "missing_tables": (
                    list(suggestion.feasibility.missing_tables)
                    if suggestion.feasibility
                    else []
                ),
                "rationale": suggestion.rationale,
            }
            proposals.append(
                Proposal(
                    kind=ProposalKind.KPI,
                    target=suggestion.identifier,
                    payload=payload,
                    proposer=proposer,
                )
            )
        return proposals


def _bare(table: str) -> str:
    """A table name in the form everything downstream compares on.

    The harvest presents tables qualified (``silver.crm_deal``) and an agent shown that text
    naturally lists them that way -- but ``referenced_tables`` reads bare names out of the
    SQL, and ``available_tables`` returns bare names from the semantic layer. Without
    normalising here, every draft the agent got *right* was rejected for a mismatch between
    two spellings of the same table. The first live run dropped all ten suggestions this way.
    """
    return table.strip().split(".")[-1].lower()


def _looks_like_a_definition(draft: KpiSuggestionDraft) -> KpiDefinition:
    """The draft as a ``KpiDefinition``, so its own construction rules apply.

    ``KpiDefinition`` refuses a formula that reads a table ``source_tables`` does not list,
    which is exactly the drift that makes a feasibility verdict lie. Running the draft through
    it means a suggestion whose SQL and tables disagree is caught here rather than at adoption.
    """
    tables = sorted({_bare(table) for table in draft.derived_from_tables})
    sql = draft.proposed_sql or f"SELECT 1 FROM {tables[0]}"
    parameters: Tuple[KpiParameter, ...] = ()
    if "{scope}" in sql:
        parameters = (
            KpiParameter("scope", ParameterKind.PREDICATE, "Concept scoping filter."),
        )
    return KpiDefinition(
        identifier=draft.identifier,
        name=draft.name,
        description=draft.description,
        sql=sql,
        grain=draft.grain,
        business_area=draft.business_area,
        source_tables=frozenset(tables),
        result_shape=ContractResultShape(draft.result_shape.value),
        parameters=parameters,
        concept=draft.concept,
    )


def assemble(
    response: KpiSuggestionDraftSet,
    *,
    available_tables: Any,
    snapshot_version: str = "",
    vocabularies: Optional[VocabularySet] = None,
    known_identifiers: Sequence[str] = (),
    area_of_concept: Optional[Mapping[str, str]] = None,
) -> AssemblyReport:
    """Turn drafts into contract suggestions, stamping feasibility from the executor.

    Every draft is checked before it is stamped: its identifier is well formed and new, its
    ``business_area`` and ``grain`` are terms this deployment has, and its formula and tables
    agree. A draft that fails is dropped with a reason rather than raising.

    The verdict itself comes from ``assess_feasibility`` and nothing else. That function takes
    two frozensets and returns a verdict; there is no argument through which a model's opinion
    could reach it, which is what the evidence line "no suggestion carries a feasibility the
    executor disagrees with" actually rests on.

    ``area_of_concept`` maps an ontology concept to the business area the **ontology** places
    it in, and when a draft names a concept that mapping wins over whatever the agent wrote.
    Which area a concept belongs to is shipped knowledge, not a per-deployment judgment, and
    an agent asked for it answers differently on different runs -- the first three baseline
    runs put ``WebSession`` in ``customer``, ``operations`` and ``marketing``. K9 reached the
    same conclusion one package earlier and this is the same fix applied where I had not
    thought to apply it.
    """
    vocabularies = vocabularies or load_vocabulary_set()
    taken = {identifier.lower() for identifier in known_identifiers}
    seen: set = set()
    report = AssemblyReport(notes=response.notes)

    for draft in response.suggestions:
        identifier = draft.identifier.strip().lower()
        if not _IDENTIFIER_RE.match(identifier):
            report.dropped.append(f"{draft.identifier!r}: not a usable identifier")
            continue
        if identifier in taken:
            report.dropped.append(f"{identifier}: already in the registry")
            continue
        if identifier in seen:
            report.dropped.append(f"{identifier}: proposed twice in one response")
            continue
        if not draft.derived_from_tables:
            report.dropped.append(f"{identifier}: derived from no table")
            continue
        try:
            vocabularies.validate("business_area", draft.business_area)
            vocabularies.validate("grain", draft.grain)
        except ValueError as error:
            report.dropped.append(f"{identifier}: {error.args[0].split('.')[0]}")
            continue
        try:
            definition = _looks_like_a_definition(draft)
        except ValueError as error:
            report.dropped.append(f"{identifier}: {error}")
            continue

        seen.add(identifier)
        report.catalogue_source[identifier] = (
            draft.catalogue_source or ""
        ).strip() or None
        if not draft.proposed_sql:
            report.without_formula.append(identifier)
        # The ontology's area, where it has one for this concept.
        area = (area_of_concept or {}).get(draft.concept or "", draft.business_area)
        verdict = assess_feasibility(definition, frozenset(available_tables))
        report.suggestions.append(
            KpiSuggestion(
                identifier=identifier,
                name=draft.name,
                description=draft.description,
                business_area=area,
                concept=draft.concept,
                grain=draft.grain,
                result_shape=ResultShape(draft.result_shape.value),
                rationale=draft.rationale,
                derived_from_tables=sorted(
                    {_bare(t) for t in draft.derived_from_tables}
                ),
                confidence=Confidence(draft.confidence.value),
                proposed_sql=draft.proposed_sql,
                feasibility=Feasibility(
                    kpi_id=identifier,
                    verdict=FeasibilityVerdict(verdict.verdict.value),
                    missing_tables=sorted(verdict.missing_tables),
                    rationale=verdict.rationale,
                    semantic_snapshot=snapshot_version or None,
                ),
                proposed_by=SKILL,
            )
        )
    return report


async def aassemble(
    response: KpiSuggestionDraftSet,
    *,
    available_tables: Any,
    snapshot_version: str = "",
    vocabularies: Optional[VocabularySet] = None,
    known_identifiers: Sequence[str] = (),
) -> AssemblyReport:
    """Async variant. Pure, so it exists to keep the calling convention uniform."""
    return await asyncio.to_thread(
        assemble,
        response,
        available_tables=available_tables,
        snapshot_version=snapshot_version,
        vocabularies=vocabularies,
        known_identifiers=known_identifiers,
    )


def render_prompt(
    *,
    vocabularies: Optional[VocabularySet] = None,
    catalogue: Sequence[Dict[str, Any]] = (),
    existing: Sequence[str] = (),
    prompt_path: Path = PROMPT_PATH,
) -> str:
    """The prompt with its closed sets, the metric catalogue and the registry filled in.

    ``catalogue`` is the curated retail metric list. It is rendered because the alternative is
    invention: a model asked for candidate KPIs with no catalogue will produce plausible metric
    names, and plausible is exactly what makes a wrong one expensive to catch.
    """
    vocabularies = vocabularies or load_vocabulary_set()
    prompt = prompt_path.read_text()
    filled = {
        "business_area": vocabularies["business_area"].render_for_prompt(),
        "grain": vocabularies["grain"].render_for_prompt(),
        "catalogue": _render_catalogue(catalogue),
        "existing_kpis": ", ".join(f"`{name}`" for name in sorted(existing))
        or "(none)",
    }
    for name, rendered in filled.items():
        placeholder = "{{" + name + "}}"
        if placeholder not in prompt:
            raise ValueError(
                f"{prompt_path.name} has no {placeholder} placeholder, so {name} would never "
                "reach the model and it would invent values."
            )
        prompt = prompt.replace(placeholder, rendered)
    return prompt


async def arender_prompt(
    *,
    vocabularies: Optional[VocabularySet] = None,
    catalogue: Sequence[Dict[str, Any]] = (),
    existing: Sequence[str] = (),
    prompt_path: Path = PROMPT_PATH,
) -> str:
    """Async variant. The file read happens off the event loop."""
    return await asyncio.to_thread(
        render_prompt,
        vocabularies=vocabularies,
        catalogue=catalogue,
        existing=existing,
        prompt_path=prompt_path,
    )


def _render_catalogue(catalogue: Sequence[Dict[str, Any]]) -> str:
    """One line per curated metric: its name and its conceptual formula."""
    if not catalogue:
        return (
            "(no catalogue supplied -- propose only what the harvest itself supports)"
        )
    return "\n".join(
        f"- {entry['name']}: {entry.get('formula_text', '')}".rstrip(": ")
        for entry in catalogue
    )
