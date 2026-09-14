"""The dashboard suggestion agent's output, and the executor that stamps it real.

The agent proposes a dashboard for a purpose written in the caller's own words: which of this
deployment's *already registered, already ready* KPIs fit, a name, and -- where it applies -- a
``business_area`` filter. Unlike K12 (:mod:`egpt_retail.skills.kpi_suggestion`), this agent
never invents a KPI: it selects from a closed list handed to it in the prompt, the same
``ready`` set ``POST /dashboards`` itself requires every tile to reference
(``api/routers/dashboards.py``'s ``_check_tiles_ready``). That is what makes a suggestion here
exactly as adoptable as a dashboard built by hand -- there is no separate feasibility check to
run before ``POST /dashboards`` accepts it, because every candidate already passed one.

**The agent's schema has no field for a metric's name, unit or description.** Those exist on
the KPI it already picked; asking it to restate them invites drift between what it said and
what the registry actually holds. :func:`assemble` looks the identifier up and copies the real
name across, the same "the executor fills in what the model must not restate" split K12 uses
for feasibility.

**The one filter this agent may propose is `business_area`.** It is the one
``DashboardFilterField`` with a well-defined value space to validate against -- this
deployment's own vocabulary, exactly as ``KpiSuggestionDraft.business_area`` is checked.
``time_window`` has no established filter-value format anywhere in this contract yet (nothing
today constructs one -- see ``dashboards.yaml``'s ``DashboardFilter`` and its one worked
example), so this agent is never asked for one rather than being left to guess a shape nothing
downstream would recognise.

**Drift with the API contract is impossible by construction here**, the same reason K12 returns
``egpt_retail.api.models.KpiSuggestion`` rather than a hand-written copy: :func:`assemble`
returns ``egpt_retail.api.models.DashboardSuggestion``, generated from the OpenAPI schema the
create-dashboard screen renders. A field the agent's draft carries and the screen does not know
would be silently invisible; building the generated type means that mismatch is an error at
construction instead.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import List, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field

from egpt_retail.api import models as wire
from egpt_retail.contracts.kpi import KpiDefinition
from egpt_retail.contracts.vocabulary import VocabularySet, load_vocabulary_set

__all__ = [
    "PROMPT_PATH",
    "AssemblyReport",
    "DashboardSuggestionDraft",
    "DashboardTileDraft",
    "aassemble",
    "arender_prompt",
    "assemble",
    "render_prompt",
]

PROMPT_PATH = Path(__file__).parent / "dashboard_suggestion.md"


class DashboardTileDraft(BaseModel):
    """One candidate metric, as the agent proposes it. No name, unit or description of its
    own -- :func:`assemble` copies those from the KPI it names, never from the model."""

    model_config = ConfigDict(extra="forbid")

    kpi_identifier: str = Field(
        description="Must be one of the identifiers listed in section 3 below."
    )
    rationale: str = Field(
        description="One sentence: why this metric fits the stated purpose."
    )


class DashboardSuggestionDraft(BaseModel):
    """The whole response, with the refusal channel section 6 requires."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="A short dashboard name that reflects the purpose.")
    tiles: List[DashboardTileDraft] = Field(default_factory=list)
    business_area: Optional[str] = Field(
        default=None,
        description=(
            "A term from the business_area vocabulary in section 3, to scope the dashboard "
            "to -- only when the purpose names or clearly implies one area. Null otherwise."
        ),
    )
    notes: str = Field(
        default="",
        description="Empty on a clean run; otherwise what was missing or why the list is short.",
    )


class AssemblyReport(BaseModel):
    """What :func:`assemble` accepted, and what it dropped and why. Dropped tiles are reported
    rather than raised on -- one hallucinated identifier out of five should cost the caller
    that one tile, not the other four (the same rule K12's :class:`AssemblyReport` follows)."""

    suggestion: wire.DashboardSuggestion
    dropped: List[str] = Field(default_factory=list)


def assemble(
    response: DashboardSuggestionDraft,
    *,
    ready_kpis: Sequence[KpiDefinition],
    vocabularies: Optional[VocabularySet] = None,
) -> AssemblyReport:
    """Turn a draft into the contract's ``DashboardSuggestion``, trusting nothing the model
    said about a KPI beyond its identifier.

    ``ready_kpis`` must already be the ``ready`` subset -- the same set the prompt itself was
    given to choose from (:func:`render_prompt`'s ``kpis``). A tile naming an identifier outside
    it is dropped with a reason rather than raising: a suggestion run that returns four good
    tiles and one hallucinated one should cost the caller that one tile, not the whole run.
    """
    vocabularies = vocabularies or load_vocabulary_set()
    by_identifier = {kpi.identifier: kpi for kpi in ready_kpis}

    tiles: List[wire.DashboardTileSuggestion] = []
    dropped: List[str] = []
    seen: set = set()
    for draft in response.tiles:
        identifier = draft.kpi_identifier.strip()
        definition = by_identifier.get(identifier)
        if definition is None:
            dropped.append(f"{identifier!r}: not a registered, ready KPI")
            continue
        if identifier in seen:
            dropped.append(f"{identifier}: proposed twice in one response")
            continue
        seen.add(identifier)
        tiles.append(
            wire.DashboardTileSuggestion(
                kpi_identifier=identifier,
                name=definition.name,
                rationale=draft.rationale,
            )
        )

    filters: List[wire.DashboardFilter] = []
    notes = response.notes
    if response.business_area:
        try:
            vocabularies.validate("business_area", response.business_area)
        except ValueError as error:
            notes = "\n".join(
                part
                for part in (notes, f"dropped business_area filter: {error}")
                if part
            )
        else:
            filters.append(
                wire.DashboardFilter(
                    field=wire.DashboardFilterField.business_area,
                    value=response.business_area,
                )
            )

    suggestion = wire.DashboardSuggestion(
        name=response.name.strip() or "Untitled dashboard",
        tiles=tiles,
        filters=filters,
        notes=notes,
    )
    return AssemblyReport(suggestion=suggestion, dropped=dropped)


async def aassemble(
    response: DashboardSuggestionDraft,
    *,
    ready_kpis: Sequence[KpiDefinition],
    vocabularies: Optional[VocabularySet] = None,
) -> AssemblyReport:
    """Async variant. Pure, so it exists to keep the calling convention uniform with K12's."""
    return await asyncio.to_thread(
        assemble, response, ready_kpis=ready_kpis, vocabularies=vocabularies
    )


def _render_kpis(kpis: Sequence[KpiDefinition]) -> str:
    """One line per selectable KPI: its identifier, name, business area, grain and unit --
    everything the agent needs to judge fit, and nothing it could mistake for an invitation to
    restate (there is no description line for it to reword and drift from)."""
    if not kpis:
        return (
            "(no ready KPIs in this registry -- propose no tiles and say so in notes)"
        )
    return "\n".join(
        f"- `{kpi.identifier}` — {kpi.name} ({kpi.business_area}, grain: {kpi.grain}"
        + (f", unit: {kpi.unit}" if kpi.unit else "")
        + ")"
        for kpi in sorted(kpis, key=lambda k: k.identifier)
    )


def render_prompt(
    *,
    kpis: Sequence[KpiDefinition],
    vocabularies: Optional[VocabularySet] = None,
    prompt_path: Path = PROMPT_PATH,
) -> str:
    """The prompt with the caller's selectable KPIs and the business_area vocabulary filled in.

    ``kpis`` is rendered because the alternative is invention: a model asked to build a
    dashboard with no catalogue in hand will name a plausible-sounding ``kpi_identifier``, and
    plausible is exactly what :func:`assemble` cannot tell from real without the registry.
    """
    vocabularies = vocabularies or load_vocabulary_set()
    prompt = prompt_path.read_text()
    filled = {
        "kpis": _render_kpis(kpis),
        "business_area": vocabularies["business_area"].render_for_prompt(),
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
    kpis: Sequence[KpiDefinition],
    vocabularies: Optional[VocabularySet] = None,
    prompt_path: Path = PROMPT_PATH,
) -> str:
    """Async variant. The file read happens off the event loop."""
    return await asyncio.to_thread(
        render_prompt, kpis=kpis, vocabularies=vocabularies, prompt_path=prompt_path
    )
