"""One suggestion pass: gather, run both agents, assemble, file proposals.

What ``egpt-retail suggest`` does, and what ``harvest --suggest`` does after a refresh. The
model is consulted exactly twice -- once per agent -- and every verdict attached to what it
said is a set difference computed afterwards: KPI feasibility from K11, shape readiness and
data feasibility from K13's own checks. What reaches the proposals table is what a person
will accept or reject; nothing here writes to the graph, the registry or the warehouse.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, FrozenSet, List, Mapping, Optional, Sequence, Tuple

from egpt_retail.app.settings import RetailSettings
from egpt_retail.skills import kpi_suggestion as k12
from egpt_retail.skills import use_case_suggestion as k13
from egpt_retail.suggestions.agents import build_suggestion_agents, load_catalogue
from egpt_retail.suggestions.context import (
    SuggestionContext,
    gather_context,
    render_context,
)

__all__ = ["SuggestionRun", "arun_suggestions", "run_suggestions"]

#: What a deployment has wired, for K13's shape gate. A literal until Lane R's settings say
#: which capability each shape's executors supply (R-12, R-14); `delivery_channel` alone is
#: the honest floor, since every deployment can at least show a person a number.
DEFAULT_CAPABILITIES: FrozenSet[str] = frozenset({"delivery_channel"})


@dataclass
class SuggestionRun:
    """What one pass produced, in the order it produced it."""

    snapshot: str
    context: SuggestionContext
    kpis: k12.AssemblyReport
    use_cases: k13.AssemblyReport
    #: How many proposals were new. Re-proposing what is already pending, accepted or
    #: rejected is a no-op by K7's rule, so a nightly pass over unchanged data files nothing.
    proposals_new: int = 0
    proposals_offered: int = 0
    dry_run: bool = False
    notes: List[str] = field(default_factory=list)

    def lines(self) -> List[str]:
        out = [
            f"snapshot {self.snapshot}: {len(self.context.tables)} tables, "
            f"{len(self.context.prose)} prose passages",
            f"KPI suggestions: {len(self.kpis.suggestions)} kept, "
            f"{len(self.kpis.without_formula)} without a formula, "
            f"{len(self.kpis.dropped)} dropped",
        ]
        for kpi in self.kpis.suggestions:
            source = self.kpis.catalogue_source.get(kpi.identifier)
            badge = "catalogue" if source else "inferred"
            verdict = kpi.feasibility.verdict.value if kpi.feasibility else "unknown"
            out.append(f"  [{verdict:<7}] {kpi.identifier:<34} {badge}")
        out.append(
            f"use case suggestions: {len(self.use_cases.suggestions)} kept, "
            f"{len(self.use_cases.dropped)} dropped"
        )
        for use_case, readiness, feasibility in zip(
            self.use_cases.suggestions,
            self.use_cases.readiness,
            self.use_cases.feasibility,
        ):
            out.append(
                f"  {readiness.shape.value:<10} {feasibility.label:<30} {use_case.title}"
            )
        for table, titles in sorted(self.use_cases.unlocks.items()):
            out.append(
                f"  connect {table} to unlock {len(titles)}: {', '.join(titles)}"
            )
        if self.dry_run:
            out.append(f"dry run: {self.proposals_offered} proposal(s) not filed")
        else:
            out.append(
                f"proposals: {self.proposals_new} new of {self.proposals_offered} offered"
            )
        out.extend(self.notes)
        return out


def _drafts_of(response: Any, schema: type) -> Any:
    """The typed draft set an agent returned, or a refusal that names what came back.

    ``output_schema`` makes the content an instance of the schema on a clean run; a run that
    failed structured output returns prose or ``None``, and passing that to ``assemble`` would
    raise inside pydantic with no mention of which agent produced it.
    """
    content = getattr(response, "content", None)
    if isinstance(content, schema):
        return content
    raise ValueError(
        f"{schema.__name__} expected from the agent; got "
        f"{type(content).__name__}: {str(content)[:120]!r}"
    )


def run_suggestions(
    settings: Optional[RetailSettings] = None,
    *,
    reader: Any,
    registry: Any = None,
    proposals: Any = None,
    knowledge: Any = None,
    ontology: Any = None,
    agents: Optional[Sequence[Any]] = None,
    configured: FrozenSet[str] = DEFAULT_CAPABILITIES,
    dry_run: bool = False,
    samples: Optional[Mapping[Tuple[str, str], Sequence[str]]] = None,
    existing_definitions: Optional[Sequence[Any]] = None,
    focus_areas: Optional[Sequence[str]] = None,
) -> SuggestionRun:
    """Run both suggestion agents over the current semantic layer and file the results.

    Stores are injected so the caller owns their lifetimes and a test can pass fakes:
    ``reader`` is the semantic read API, ``registry`` the K11 store, ``proposals`` the K7
    store (``None`` with ``dry_run`` to assemble without filing). ``agents`` lets a caller
    supply the two agents already built -- the entrypoint builds them once per process, and
    a test passes stand-ins that return canned drafts.

    ``existing_definitions``, when given, is used instead of calling ``registry.list()`` --
    for a caller that already read the registry and wants to release a pooled connection
    (``registry``'s) before the two live model calls below run, rather than hold it open
    across both. ``api/routers/kpis.py``'s ``GET /kpis/suggestions`` is exactly this caller:
    it fetches the registry snapshot up front, closes the control-plane connection, and only
    then calls this function -- ``registry`` stays ``None`` and is never touched. Every other
    caller keeps passing ``registry`` and this behaves exactly as before.

    ``focus_areas`` are the business areas the customer said they care about (the
    discovery page's focus). They reach both agents as a priority in the message
    (:func:`render_focus`), never as a filter: what comes back is still judged by
    ``assemble`` and stamped with the ontology's area, and nothing is dropped for being
    outside the focus. ``None`` or empty means no focus, and the message is unchanged.
    """
    settings = settings or RetailSettings()
    if existing_definitions is not None:
        definitions = tuple(existing_definitions)
    else:
        if registry is None:
            raise ValueError(
                "run_suggestions needs either `registry` or `existing_definitions`."
            )
        definitions = tuple(registry.list())
    existing = [definition.identifier for definition in definitions]
    by_identifier = {definition.identifier: definition for definition in definitions}

    context = gather_context(
        reader,
        knowledge=knowledge,
        ontology=ontology,
        tenant_id=settings.retail_customer_slug or "",
        samples=samples,
    )
    if agents is None:
        agents = build_suggestion_agents(
            settings,
            existing_kpis=existing,
            registry_kpis=definitions,
            catalogue=load_catalogue(),
        )
    kpi_agent, use_case_agent = agents

    kpi_drafts = _drafts_of(
        kpi_agent.run(
            render_context(
                context,
                ask="Propose the KPIs this data supports.",
                focus_areas=focus_areas,
            )
        ),
        k12.KpiSuggestionDraftSet,
    )
    kpi_report = k12.assemble(
        kpi_drafts,
        available_tables=context.available_tables,
        snapshot_version=context.snapshot,
        known_identifiers=existing,
        area_of_concept=context.area_of_concept,
        # The same join-aware verdict the registry shows after adoption.
        known_unjoinable=reader.unjoinable_oracle(),
    )

    use_case_drafts = _drafts_of(
        use_case_agent.run(
            render_context(
                context,
                ask="Propose the use cases this data supports.",
                focus_areas=focus_areas,
            )
        ),
        k13.UseCaseSuggestionDraftSet,
    )
    use_case_report = k13.assemble(
        use_case_drafts,
        configured=configured,
        known_kpis=existing,
        available_tables=context.available_tables,
        registry=by_identifier,
    )

    run = SuggestionRun(
        snapshot=context.snapshot,
        context=context,
        kpis=kpi_report,
        use_cases=use_case_report,
        dry_run=dry_run,
    )
    if not context.prose:
        run.notes.append(
            "no domain prose reached the agents: no knowledge store was supplied, or the "
            "corpus is not loaded (egpt-retail load-knowledge)"
        )

    offered = kpi_report.as_proposals() + use_case_report.as_proposals()
    run.proposals_offered = len(offered)
    if not dry_run:
        if proposals is None:
            raise ValueError(
                "a proposals store is required to file suggestions; pass dry_run=True to "
                "assemble without filing"
            )
        run.proposals_new = proposals.propose_many(offered) if offered else 0
    return run


async def arun_suggestions(
    settings: Optional[RetailSettings] = None,
    *,
    reader: Any,
    registry: Any = None,
    proposals: Any = None,
    knowledge: Any = None,
    ontology: Any = None,
    agents: Optional[Sequence[Any]] = None,
    configured: FrozenSet[str] = DEFAULT_CAPABILITIES,
    dry_run: bool = False,
    samples: Optional[Mapping[Tuple[str, str], Sequence[str]]] = None,
    existing_definitions: Optional[Sequence[Any]] = None,
    focus_areas: Optional[Sequence[str]] = None,
) -> SuggestionRun:
    """Async variant. Two model calls and several store reads happen off the event loop."""
    return await asyncio.to_thread(
        run_suggestions,
        settings,
        reader=reader,
        registry=registry,
        proposals=proposals,
        knowledge=knowledge,
        ontology=ontology,
        agents=agents,
        configured=configured,
        dry_run=dry_run,
        samples=samples,
        existing_definitions=existing_definitions,
        focus_areas=focus_areas,
    )
