"""``egpt-retail propose`` -- the K8 proposers over the current semantic layer (K22).

The review's sequence is connect, build the layer, then suggest. Between "build the layer"
and "suggest" sits the step that says which of *this customer's* tables is an instance of
which concept -- without it a business area has no evidence behind it (K9), a KPI has no
concept to hang from, and "what my data supports" cannot rank anything. K8 built the two
proposers and the cookbook drove them; nothing ran them on a deployment. This module does:
the mapping proposer once over the whole harvest, the enrichment proposer once per concept
the mapping named, and everything they say filed as PENDING proposals for a person to
review. Nothing here writes to the graph; accepting a proposal does that
(``ProposalStore.apply_accepted``, called by the review route).

Idempotent by K7's rule: a proposal whose content is already pending, accepted or rejected
is not raised again, so running this after every harvest files only what is new.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, TypeVar

from egpt.agent import Agent

from egpt_retail.app.settings import RetailSettings
from egpt_retail.ontology.model import ARTIFACT_PATH, load_ontology
from egpt_retail.routing.providers import build_tier_providers, route_agents
from egpt_retail.semantic.harvest import harvest_schema
from egpt_retail.semantic.proposals import Proposal, ProposalStore
from egpt_retail.semantic.refresh import _connect, _neo4j
from egpt_retail.semantic.store import SemanticGraphStore
from egpt_retail.skills import enrichment_proposer as enrich
from egpt_retail.skills import mapping_proposer as mapping
from egpt_retail.suggestions.context import _harvest_text, samples_from_harvest

__all__ = ["ProposeRun", "propose", "apropose", "build_proposer_agents"]

log = logging.getLogger("egpt_retail.propose")

MAPPING_PROPOSER_ID = "mapping_proposer"
ENRICHMENT_PROPOSER_ID = "enrichment_proposer"


@dataclass
class ProposeRun:
    """What one pass proposed, for the caller to print."""

    tables: int = 0
    concepts_offered: int = 0
    collisions: Tuple[str, ...] = ()
    mappings: List[str] = field(default_factory=list)
    enrichments: List[str] = field(default_factory=list)
    unresolved_collisions: Tuple[str, ...] = ()
    proposals: int = 0
    filed: int = 0
    dry_run: bool = False
    notes: str = ""

    def lines(self) -> List[str]:
        out = [
            f"harvest: {self.tables} tables; ontology: {self.concepts_offered} concepts; "
            f"collisions: {len(self.collisions)}",
            f"mapping proposer: {len(self.mappings)} mapping(s)",
        ]
        out += [f"  {line}" for line in self.mappings]
        if self.unresolved_collisions:
            out.append(
                f"  collisions left open: {', '.join(self.unresolved_collisions)}"
            )
        if self.notes:
            out.append(f"  notes: {self.notes}")
        out.append(f"enrichment proposer: {len(self.enrichments)} line(s)")
        out += [f"  {line}" for line in self.enrichments]
        out.append(
            f"dry run: {self.proposals} proposal(s) not filed"
            if self.dry_run
            else f"filed {self.filed} new proposal(s) of {self.proposals} "
            "(the rest were already pending, accepted or rejected)"
        )
        return out


def build_proposer_agents(
    settings: RetailSettings,
    *,
    concepts: Sequence[str],
    collisions: Sequence[str],
    db: Any = None,
) -> Tuple[Agent, Agent]:
    """The two K8 agents, instructions rendered for this harvest, routed through R8."""
    mapping_agent = Agent(
        id=MAPPING_PROPOSER_ID,
        name=MAPPING_PROPOSER_ID,
        description=(
            "Proposes which of the customer's tables instantiate which shipped concept, "
            "and what each colliding column name means; a person accepts (Lane K, K8)."
        ),
        instructions=mapping.render_prompt(concepts=concepts, collisions=collisions),
        output_schema=mapping.MappingProposalSet,
        db=db,
    )
    enrichment_agent = Agent(
        id=ENRICHMENT_PROPOSER_ID,
        name=ENRICHMENT_PROPOSER_ID,
        description=(
            "Proposes synonyms, question patterns, filters and personal-data fields for "
            "one concept, from the harvest; a person accepts (Lane K, K8)."
        ),
        instructions=enrich.render_prompt(),
        output_schema=enrich.EnrichmentProposalSet,
        db=db,
    )
    route_agents([mapping_agent, enrichment_agent], build_tier_providers(settings))
    return mapping_agent, enrichment_agent


T = TypeVar("T")


def _content(response: Any, kind: type[T]) -> T:
    """The agent's structured answer, or a refusal naming what came back instead."""
    content = getattr(response, "content", None)
    if not isinstance(content, kind):
        raise RuntimeError(
            f"the {kind.__name__} agent returned {type(content).__name__}, not "
            f"{kind.__name__}"
        )
    return content


def propose(
    settings: Optional[RetailSettings] = None,
    *,
    dry_run: bool = False,
    schema: str = "silver",
    enrich_concepts: Optional[Sequence[str]] = None,
) -> ProposeRun:
    """Open the stores, run one pass, close them. Returns the run for the caller to print.

    ``enrich_concepts`` limits the enrichment proposer to those concepts; absent, it runs
    for every concept the mapping proposer mapped a table to.
    """
    settings = settings or RetailSettings()
    run = ProposeRun(dry_run=dry_run)

    warehouse = _connect(
        settings.retail_warehouse_db_url, "EGPT_RETAIL_WAREHOUSE_DB_URL"
    )
    try:
        warehouse.autocommit = True
        harvest = harvest_schema(warehouse, schema, source="warehouse", sample=True)
    finally:
        warehouse.close()
    samples = samples_from_harvest(harvest)

    overlay = (
        Path(settings.retail_ontology_overlay)
        if settings.retail_ontology_overlay
        else None
    )
    ontology = load_ontology(ARTIFACT_PATH, overlay_path=overlay)
    concept_names = sorted(c.name for c in ontology.concepts)
    collisions = tuple(
        c.column_name for c in harvest.collisions if c.needs_disambiguation
    )
    run.tables = len(harvest.tables)
    run.concepts_offered = len(concept_names)
    run.collisions = collisions

    control_plane = _connect(settings.retail_db_url, "EGPT_RETAIL_DB_URL")
    driver = _neo4j(settings)
    try:
        reader = SemanticGraphStore(driver=driver)
        harvest_text = _harvest_text(reader, samples)
        mapping_agent, enrichment_agent = build_proposer_agents(
            settings, concepts=concept_names, collisions=collisions
        )

        response = _content(
            mapping_agent.run(
                f"harvest:\n{harvest_text}\n\n"
                "Map these tables to concepts and resolve every collision."
            ),
            mapping.MappingProposalSet,
        )
        run.mappings = list(mapping.evidence_lines(response))
        run.notes = response.notes or ""
        run.unresolved_collisions = tuple(
            mapping.unresolved_collisions(response, collisions)
        )
        proposals: List[Proposal] = mapping.to_proposals(
            response, proposer=MAPPING_PROPOSER_ID, known_concepts=concept_names
        )

        mapped = sorted({m.concept for m in response.mappings})
        targets = [c for c in (enrich_concepts or mapped) if c in set(concept_names)]
        by_name = {c.name: c for c in ontology.concepts}
        for name in targets:
            concept = by_name[name]
            taken = sorted(
                {other.name for other in ontology.concepts if other.name != name}
                | {
                    synonym
                    for other in ontology.concepts
                    if other.name != name
                    for synonym in (other.synonyms or ())
                }
            )
            enrichment = _content(
                enrichment_agent.run(
                    f"concept: {concept.name}\ndescription: {concept.description}\n\n"
                    f"harvest:\n{harvest_text}\n\n"
                    "terms already spoken for by other concepts, never propose these as "
                    f"synonyms:\n{', '.join(taken)}"
                ),
                enrich.EnrichmentProposalSet,
            )
            run.enrichments.append(
                f"{enrichment.concept} (business_area={enrichment.business_area or '-'})"
            )
            run.enrichments += [
                f"  {line}" for line in enrich.evidence_lines(enrichment)
            ]
            proposals += enrich.to_proposals(
                enrichment, proposer=ENRICHMENT_PROPOSER_ID, ontology_concept=concept
            )

        run.proposals = len(proposals)
        if not dry_run:
            store = ProposalStore(control_plane)
            store.ensure_schema()
            run.filed = store.propose_many(proposals)
        return run
    finally:
        control_plane.close()
        driver.close()


async def apropose(
    settings: Optional[RetailSettings] = None,
    *,
    dry_run: bool = False,
    schema: str = "silver",
    enrich_concepts: Optional[Sequence[str]] = None,
) -> ProposeRun:
    """Async variant: the pass off the event loop."""
    return await asyncio.to_thread(
        propose,
        settings,
        dry_run=dry_run,
        schema=schema,
        enrich_concepts=enrich_concepts,
    )


def summary(run: ProposeRun) -> Dict[str, Any]:
    return {
        "tables": run.tables,
        "mappings": len(run.mappings),
        "proposals": run.proposals,
        "filed": run.filed,
        "dry_run": run.dry_run,
    }
