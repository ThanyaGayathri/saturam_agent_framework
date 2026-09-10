"""K13 -- use case candidates across all four shapes, gated per shape by what a deployment has.

A use case is not a KPI on a screen. The build plan fixes **four shapes on the side-effect
axis** and argues the set is closed: Inform writes nothing, Alert notifies, Recommend writes
after approval, Act writes unattended. A suggestion that does not say which it is leaves the
most consequential fact about itself unstated -- Alert and Act are wildly different asks of a
customer, and one of them touches their systems without a person in the loop.

**So the shape is proposed, and the deployment decides whether it can run it.** Same rule as
K11 and K12: the agent proposes, a deterministic check decides. Each shape needs different
things wired -- an Alert needs somewhere to deliver, a Recommend needs an approver and a
write target, an Act needs a per-run cap and a rollback point -- and
:func:`assess_shape_readiness` is a set difference over what the deployment has configured.
No model is asked whether an unattended write is safe here.

**One known incompleteness, deliberate and recorded.** The API contract's
``UseCaseSuggestion`` carries no ``shape`` or ``autonomy`` field yet (open item R-12), so
:func:`assemble` cannot put them on the wire and says so in the list's ``notes`` rather than
dropping them silently. The draft schema carries both, the readiness gate uses both, and when
R-12 lands the assembly is one line. Waiting for the field would have cost the other ninety
per cent of the package for the last ten.
"""

from __future__ import annotations

import asyncio
from enum import Enum
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    FrozenSet,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from pydantic import BaseModel, ConfigDict, Field

from egpt_retail.api.models import Confidence, UseCaseSuggestion, UseCaseSuggestionList
from egpt_retail.contracts.kpi import (
    FeasibilityVerdict,
    KpiDefinition,
    assess_feasibility,
)
from egpt_retail.contracts.use_case import Autonomy, UseCaseShape
from egpt_retail.contracts.vocabulary import VocabularySet, load_vocabulary_set

if TYPE_CHECKING:
    from egpt_retail.semantic.proposals import Proposal

__all__ = [
    "PROMPT_PATH",
    "SHAPE_REQUIREMENTS",
    "SKILL_VERSION",
    "AssemblyReport",
    "DataFeasibility",
    "UseCaseFeasibility",
    "assess_data_feasibility",
    "Autonomy",
    "ShapeReadiness",
    "UseCaseShape",
    "UseCaseSuggestionDraft",
    "UseCaseSuggestionDraftSet",
    "aassemble",
    "arender_prompt",
    "assemble",
    "assess_shape_readiness",
    "readiness_lines",
    "render_prompt",
]

PROMPT_PATH = Path(__file__).parent / "use_case_suggestion.md"
SKILL_IDENTIFIER = "use_case_suggestion"
SKILL_VERSION = "1.0.0"


class DraftConfidence(str, Enum):
    """Mirrors the contract's ``Confidence``. Checked equal to it in the tests."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


#: What each shape needs a deployment to have configured before it could run.
#:
#: Not simply cumulative down the list, and the exception is the interesting part.
#: ``delivery_channel`` is universal -- every shape has to reach somebody eventually. The
#: *write* capabilities are cumulative from Recommend to Act, because an Act does everything
#: a Recommend does and then stops waiting for the approver. But ``alert_threshold`` belongs
#: to Alert alone: a Recommend can be produced on a schedule with no condition to watch, and
#: requiring a threshold of it would block a use case that needs nothing of the kind.
#:
#: Named as capability strings rather than settings keys, because which setting supplies a
#: write target is Lane R's to decide and this only needs to know whether one exists.
SHAPE_REQUIREMENTS: Mapping[UseCaseShape, Tuple[str, ...]] = {
    UseCaseShape.INFORM: ("delivery_channel",),
    UseCaseShape.ALERT: ("delivery_channel", "alert_threshold"),
    UseCaseShape.RECOMMEND: (
        "delivery_channel",
        "approver",
        "write_target",
        "guardrails",
    ),
    UseCaseShape.ACT: (
        "delivery_channel",
        "approver",
        "write_target",
        "guardrails",
        "per_run_cap",
        "rollback_point",
    ),
}


class UseCaseSuggestionDraft(BaseModel):
    """One candidate use case, as the agent proposes it.

    ``extra="forbid"`` for the reason K12's draft has it: what this schema *omits* is a
    guarantee, and pydantic's default is to ignore an unexpected key rather than refuse it.
    """

    model_config = ConfigDict(extra="forbid")

    title: str = Field(description="What a person would call it. A short phrase.")
    description: str = Field(
        description="One or two sentences. What it does, and when."
    )
    business_area: str = Field(
        description="A term from the business_area vocabulary in section 6."
    )
    # No `description=` on enum fields: an enum serialises to a `$ref`, and a strict
    # structured-output schema rejects a `$ref` with sibling keywords.
    shape: UseCaseShape
    autonomy: Autonomy
    confidence: DraftConfidence
    rationale: str = Field(
        description="One sentence naming the tables and KPIs this derives from."
    )
    derived_from_tables: List[str] = Field(
        description=(
            "Every table this use case reads, as the harvest names them. Never empty: a "
            "suggestion traceable to no table was invented rather than derived."
        )
    )
    kpi_identifiers: List[str] = Field(
        default_factory=list,
        description=(
            "The registered KPIs it would use. Only identifiers that exist -- naming one "
            "that does not is the failure mode this list has."
        ),
    )
    template_id: Optional[str] = Field(
        default=None,
        description=(
            "The shipped template this corresponds to, from the list in section 6, or null. "
            "A template arrives with its parameters pre-filled, so citing one turns a "
            "suggestion into something a person can accept rather than fill in. Null is the "
            "right answer where nothing shipped matches -- never the nearest one."
        ),
    )


class UseCaseSuggestionDraftSet(BaseModel):
    """The whole response, with the refusal channel section 8 requires."""

    model_config = ConfigDict(extra="forbid")

    suggestions: List[UseCaseSuggestionDraft] = Field(default_factory=list)
    notes: str = Field(default="")


class ShapeReadiness(BaseModel):
    """Whether a deployment could run a use case of this shape, and what is missing.

    A set difference over configured capabilities, computed here and never asked of a model.
    An ``Act`` proposed into a deployment with no write target is the expensive case: it reads
    like every other suggestion, and accepting it is the one that writes to a customer's
    systems unattended.
    """

    shape: UseCaseShape
    ready: bool
    missing: List[str] = Field(default_factory=list)

    @property
    def rationale(self) -> str:
        article = "an" if self.shape.value[0] in "aeiou" else "a"
        if self.ready:
            return (
                f"This deployment has what {article} {self.shape.value} use case needs."
            )
        return (
            f"{article.capitalize()} {self.shape.value} use case needs "
            f"{', '.join(self.missing)}, which this deployment has not configured."
        )


def assess_shape_readiness(
    shape: UseCaseShape, configured: FrozenSet[str]
) -> ShapeReadiness:
    """Subtract what the deployment has from what the shape needs. Data in, data out."""
    missing = [
        capability
        for capability in SHAPE_REQUIREMENTS[shape]
        if capability not in configured
    ]
    return ShapeReadiness(shape=shape, ready=not missing, missing=missing)


class UseCaseFeasibility(str, Enum):
    """Whether the data can compute what a use case needs. An aggregate, so its own words.

    A KPI is computable or it is not. A use case needs several, so it can be *partly* there,
    and "1 of 2 metrics" tells a person how close they are and what to fix in a way that
    "partial" does not. The architecture review's ruling, and the reason this is not the
    KPI-level ``FeasibilityVerdict`` reused.
    """

    READY = "ready"
    MOSTLY_READY = "mostly_ready"
    BLOCKED = "blocked"


class DataFeasibility(BaseModel):
    """What the semantic layer can and cannot compute for one suggestion. A set difference."""

    verdict: UseCaseFeasibility
    ready_kpis: List[str] = Field(default_factory=list)
    blocked_kpis: List[str] = Field(default_factory=list)
    missing_tables: List[str] = Field(default_factory=list)

    @property
    def label(self) -> str:
        total = len(self.ready_kpis) + len(self.blocked_kpis)
        if self.verdict is UseCaseFeasibility.MOSTLY_READY:
            return f"mostly ready -- {len(self.ready_kpis)} of {total} metrics"
        return self.verdict.value


class AssemblyReport(BaseModel):
    """What survived assembly, what was dropped, and what each shape needs."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    suggestions: List[UseCaseSuggestion] = Field(default_factory=list)
    #: Per accepted suggestion, in the same order: its shape and whether this deployment
    #: could run it. Carried alongside rather than on the suggestion because the contract has
    #: nowhere to put it yet (R-12).
    readiness: List[ShapeReadiness] = Field(default_factory=list)
    #: Per accepted suggestion, in the same order: whether the DATA can compute what it needs.
    #: Distinct from readiness -- a deployment may have every channel wired and still hold
    #: none of the tables. Also nowhere on the contract yet (R-16).
    feasibility: List[DataFeasibility] = Field(default_factory=list)
    #: Missing table -> the titles connecting it would unlock. The review's "connect a
    #: promotions source to unlock 6 more use cases": it turns a blocked list into a roadmap.
    unlocks: Dict[str, List[str]] = Field(default_factory=dict)
    dropped: List[str] = Field(default_factory=list)
    notes: str = ""

    def as_contract_list(self, snapshot_version: str = "") -> UseCaseSuggestionList:
        """The wire shape, with the incompleteness stated rather than hidden."""
        caveat = (
            "Shape and autonomy are proposed and gated but not carried on this response: "
            "UseCaseSuggestion has no field for them yet (open item R-12). Until it does, a "
            "reader cannot tell an Alert from an Act here."
        )
        notes = f"{self.notes} {caveat}".strip() if self.suggestions else self.notes
        return UseCaseSuggestionList(
            suggestions=self.suggestions,
            notes=notes,
            semantic_snapshot=snapshot_version or None,
        )

    def as_proposals(self, *, proposer: str = "") -> List["Proposal"]:
        """The accepted suggestions as K7 ``USE_CASE`` proposals, for the adopt action.

        The open-items resolution for the proposals table names its three consumers --
        ontology mapping, KPI adoption, and use case suggestions -- and this is the third's
        producer. The payload carries everything the contract cannot: the shape, the
        readiness and feasibility verdicts, so a reviewer sees on the proposal exactly what
        the discovery screen would have shown.
        """
        from egpt_retail.semantic.proposals import Proposal, ProposalKind

        proposer = proposer or f"{SKILL_IDENTIFIER}@{SKILL_VERSION}"
        proposals: List[Proposal] = []
        for suggestion, readiness, feasibility in zip(
            self.suggestions, self.readiness, self.feasibility
        ):
            proposals.append(
                Proposal(
                    kind=ProposalKind.USE_CASE,
                    target=_slug(suggestion.title),
                    payload={
                        "question": suggestion.title,
                        "description": suggestion.description,
                        "business_area": suggestion.business_area,
                        "rationale": suggestion.rationale,
                        "kpi_identifiers": list(suggestion.kpi_identifiers),
                        "derived_from_tables": list(suggestion.derived_from_tables),
                        "confidence": suggestion.confidence.value,
                        "shape": readiness.shape.value,
                        "shape_ready": readiness.ready,
                        "shape_missing": list(readiness.missing),
                        "feasibility": feasibility.verdict.value,
                        "ready_kpis": list(feasibility.ready_kpis),
                        "blocked_kpis": list(feasibility.blocked_kpis),
                        "missing_tables": list(feasibility.missing_tables),
                        "template_id": suggestion.template_id,
                    },
                    proposer=proposer,
                )
            )
        return proposals


def _slug(text: str) -> str:
    return "_".join(part for part in text.lower().replace("-", " ").split() if part)


def assess_data_feasibility(
    kpi_identifiers: Sequence[str],
    derived_from_tables: Sequence[str],
    *,
    registry: Mapping[str, KpiDefinition],
    available_tables: FrozenSet[str],
) -> DataFeasibility:
    """Aggregate K11's per-KPI verdicts into the use case's. Pure; consults no model.

    Each named KPI is assessed with ``assess_feasibility`` -- the same set difference the
    discovery screen shows for the KPI on its own -- and the aggregate is a count. A use case
    naming no KPI is judged on its tables alone.
    """
    ready: List[str] = []
    blocked: List[str] = []
    missing: set = set()
    # No registry means no basis to judge a KPI, not a verdict that every KPI is blocked.
    # The DB-free callers (tests, the cookbook's control) pass none and get a tables-only
    # verdict; a run with the registry in hand gets the per-KPI aggregate.
    for identifier in kpi_identifiers if registry else ():
        definition = registry.get(identifier)
        if definition is None:
            blocked.append(identifier)
            continue
        verdict = assess_feasibility(definition, available_tables)
        if verdict.verdict is FeasibilityVerdict.READY:
            ready.append(identifier)
        else:
            blocked.append(identifier)
            missing.update(verdict.missing_tables)
    table_gaps = [
        t for t in derived_from_tables if available_tables and t not in available_tables
    ]
    missing.update(table_gaps)

    if ready or blocked:
        if not blocked:
            verdict_value = UseCaseFeasibility.READY
        elif ready:
            verdict_value = UseCaseFeasibility.MOSTLY_READY
        else:
            verdict_value = UseCaseFeasibility.BLOCKED
    else:
        verdict_value = (
            UseCaseFeasibility.BLOCKED if table_gaps else UseCaseFeasibility.READY
        )
    return DataFeasibility(
        verdict=verdict_value,
        ready_kpis=ready,
        blocked_kpis=blocked,
        missing_tables=sorted(missing),
    )


def assemble(
    response: UseCaseSuggestionDraftSet,
    *,
    configured: FrozenSet[str],
    known_kpis: Sequence[str] = (),
    available_tables: FrozenSet[str] = frozenset(),
    known_templates: Sequence[str] = (),
    registry: Optional[Mapping[str, KpiDefinition]] = None,
    vocabularies: Optional[VocabularySet] = None,
) -> AssemblyReport:
    """Turn drafts into contract suggestions, gating each by what its shape needs.

    ``registry`` is the K11 definitions keyed by identifier. With it, every kept suggestion
    also carries a data-feasibility verdict aggregated from its KPIs; without it (the DB-free
    case) feasibility is judged on tables alone and ``ready_kpis`` stays empty.

    Every draft is checked before it is kept: its business area is a term this deployment
    has, every table it names is in the semantic layer, and every KPI it names is in the
    registry. A draft that fails is dropped with a reason rather than raising, so one bad
    suggestion costs a reviewer that one.

    **Nothing is dropped for being unready.** An ``Act`` this deployment cannot run is still a
    real suggestion -- it tells a customer what configuring a write target would buy them --
    and the readiness verdict travels beside it. Dropping it would hide the answer to "what
    would I get if I set this up", which is the question the discovery screen exists for.
    """
    vocabularies = vocabularies or load_vocabulary_set()
    kpis = {identifier.lower() for identifier in known_kpis}
    tables = {table.lower() for table in available_tables}
    known_templates = tuple(known_templates)
    report = AssemblyReport(notes=response.notes)
    seen: set = set()

    for draft in response.suggestions:
        title = draft.title.strip()
        if not title:
            report.dropped.append("a suggestion with no title")
            continue
        if title.lower() in seen:
            report.dropped.append(f"{title}: proposed twice in one response")
            continue
        if not draft.derived_from_tables:
            report.dropped.append(f"{title}: derived from no table")
            continue
        try:
            vocabularies.validate("business_area", draft.business_area)
        except ValueError as error:
            report.dropped.append(f"{title}: {error.args[0].split('.')[0]}")
            continue

        named_tables = sorted({_bare(t) for t in draft.derived_from_tables})
        # A suggestion over a table the layer does not hold is NOT dropped any more. The
        # review's ruling: blocked suggestions stay visible, because "connect a promotions
        # source to unlock 6 more use cases" is the roadmap, and a dropped one is invisible.
        # It is kept, marked blocked, and its missing tables feed `unlocks`.
        template_id = (draft.template_id or "").strip() or None
        if template_id and known_templates and template_id not in known_templates:
            # A template id nobody ships is worse than none: the draft is created from it, so
            # the prefill silently produces nothing and the author sees an empty form where
            # the suggestion promised a starting point.
            report.dropped.append(
                f"{title}: cites template {template_id!r}, which this deployment does not ship"
            )
            continue
        named_kpis = sorted(
            {k.strip().lower() for k in draft.kpi_identifiers if k.strip()}
        )
        unknown_kpis = [k for k in named_kpis if kpis and k not in kpis]
        if unknown_kpis:
            # The failure mode this list has: a use case whose KPIs do not exist looks
            # adoptable and cannot be built, and a reviewer would have to check the registry
            # by hand to find out.
            report.dropped.append(
                f"{title}: names KPI(s) not in the registry: {', '.join(unknown_kpis)}"
            )
            continue

        seen.add(title.lower())
        data = assess_data_feasibility(
            named_kpis,
            named_tables,
            registry=registry or {},
            available_tables=frozenset(tables),
        )
        for table in data.missing_tables:
            report.unlocks.setdefault(table, []).append(title)
        report.feasibility.append(data)
        report.suggestions.append(
            UseCaseSuggestion(
                title=title,
                description=draft.description,
                business_area=draft.business_area,
                rationale=draft.rationale,
                derived_from_tables=named_tables,
                kpi_identifiers=named_kpis,
                confidence=Confidence(draft.confidence.value),
                template_id=template_id,
            )
        )
        report.readiness.append(assess_shape_readiness(draft.shape, configured))
    return report


async def aassemble(
    response: UseCaseSuggestionDraftSet,
    *,
    configured: FrozenSet[str],
    known_kpis: Sequence[str] = (),
    available_tables: FrozenSet[str] = frozenset(),
    vocabularies: Optional[VocabularySet] = None,
) -> AssemblyReport:
    """Async variant. Pure, so it exists to keep the calling convention uniform."""
    return await asyncio.to_thread(
        assemble,
        response,
        configured=configured,
        known_kpis=known_kpis,
        available_tables=available_tables,
        vocabularies=vocabularies,
    )


def render_prompt(
    *,
    vocabularies: Optional[VocabularySet] = None,
    kpis: Sequence[Any] = (),
    templates: Sequence[Any] = (),
    prompt_path: Path = PROMPT_PATH,
) -> str:
    """The prompt with its closed sets and the registry filled in."""
    vocabularies = vocabularies or load_vocabulary_set()
    prompt = prompt_path.read_text()
    filled = {
        "business_area": vocabularies["business_area"].render_for_prompt(),
        "shapes": vocabularies["use_case_shape"].describe(),
        "kpis": _render_kpis(kpis),
        "templates": _render_templates(templates),
        "shape_requirements": "\n".join(
            f"- **{shape.value}** needs: {', '.join(needs)}"
            for shape, needs in SHAPE_REQUIREMENTS.items()
        ),
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
    kpis: Sequence[Any] = (),
    templates: Sequence[Any] = (),
    prompt_path: Path = PROMPT_PATH,
) -> str:
    """Async variant. The file read happens off the event loop."""
    return await asyncio.to_thread(
        render_prompt,
        vocabularies=vocabularies,
        kpis=kpis,
        templates=templates,
        prompt_path=prompt_path,
    )


def _render_kpis(kpis: Sequence[Any]) -> str:
    """One line per registered KPI: identifier, name and the tables it reads."""
    if not kpis:
        return "(none registered -- propose use cases the harvest alone supports)"
    lines = []
    for kpi in kpis:
        tables = ", ".join(sorted(getattr(kpi, "source_tables", ()) or ()))
        lines.append(f"- `{kpi.identifier}` ({kpi.name}) over {tables or 'no table'}")
    return "\n".join(lines)


def _render_templates(templates: Sequence[Any]) -> str:
    """One line per shipped template: id, title and the shape it is."""
    if not templates:
        return (
            "(none shipped -- use `template_id: null` on every suggestion, and a person will "
            "fill the parameters by hand)"
        )
    lines = []
    for template in templates:
        identifier = getattr(template, "id", None) or getattr(
            template, "identifier", ""
        )
        shape = getattr(template, "shape", "") or ""
        title = getattr(template, "title", "")
        lines.append(f"- `{identifier}` ({shape}) {title}".rstrip())
    return "\n".join(lines)


def _bare(table: str) -> str:
    """A table name as everything downstream compares it: unqualified, lower case."""
    return table.strip().split(".")[-1].lower()


def readiness_lines(report: AssemblyReport) -> List[str]:
    """One readable line per suggestion, for the trace K13's verification asks for."""
    return [
        f"{readiness.shape.value:<10} {'ready ' if readiness.ready else 'blocked'} "
        f"{suggestion.title}  <- {', '.join(suggestion.derived_from_tables)}"
        + (f"  needs: {', '.join(readiness.missing)}" if readiness.missing else "")
        for suggestion, readiness in zip(report.suggestions, report.readiness)
    ]
