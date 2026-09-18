"""``egpt-retail suggest`` -- propose KPIs and use cases over the current semantic layer.

The review's sequence is connect, build the layer, *then immediately suggest*. ``harvest``
builds the layer; this is the "then". It runs the two suggestion agents through R8's routing,
feeds them the harvest, the ontology and K10's domain prose, and files what survives assembly
as K7 proposals for a person to adopt. It never writes to the graph or the registry.

Idempotent by K7's rule: a proposal whose content is already pending, accepted or rejected is
not raised again, so running this nightly over unchanged data files nothing new.
"""

from __future__ import annotations

import argparse
import logging
from typing import TYPE_CHECKING, Optional, Sequence

from egpt_retail.app.settings import RetailSettings

if TYPE_CHECKING:
    from egpt_retail.suggestions.run import SuggestionRun

__all__ = ["build_parser", "main", "suggest"]

log = logging.getLogger("egpt_retail.suggest")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="egpt-retail suggest",
        description="Propose KPIs and use cases over the semantic layer, then exit.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Assemble and report, but file no proposals.",
    )
    parser.add_argument(
        "--no-prose",
        action="store_true",
        help="Skip the knowledge store; the agents see the harvest and ontology only.",
    )
    return parser


def suggest(
    settings: Optional[RetailSettings] = None,
    *,
    dry_run: bool = False,
    with_prose: bool = True,
    schema: str = "silver",
) -> "SuggestionRun":
    """Open the stores, run one pass, close them. Returns the run for the caller to print."""
    from egpt_retail.kpi.store import KpiRegistryStore
    from egpt_retail.semantic.proposals import ProposalStore
    from egpt_retail.semantic.refresh import _connect, _neo4j
    from egpt_retail.semantic.store import SemanticGraphStore
    from egpt_retail.suggestions.run import run_suggestions

    settings = settings or RetailSettings()
    control_plane = _connect(settings.retail_db_url, "EGPT_RETAIL_DB_URL")
    driver = _neo4j(settings)
    try:
        reader = SemanticGraphStore(driver=driver)
        registry = KpiRegistryStore(control_plane)
        registry.ensure_schema()
        proposals = ProposalStore(control_plane)
        proposals.ensure_schema()

        knowledge = ontology = None
        if with_prose:
            from egpt.knowledge.knowledge import Knowledge

            from egpt_retail.knowledge.embedding import resolve_knowledge_embedder
            from egpt_retail.knowledge.vector import build_knowledge_vector_db
            from egpt_retail.ontology.store import OntologyStore

            embedder = resolve_knowledge_embedder(mode=settings.retail_provider_mode)
            knowledge = Knowledge(
                vector_db=build_knowledge_vector_db(settings, embedder.embedder)
            )
            ontology = OntologyStore(driver=driver, embedder=embedder.embedder)

        # Example values, read from the warehouse for this pass only. The graph never holds
        # them (rows stay in the warehouse), and without them the KPI agent's three-run
        # spread went from 1.2x to 9x. A deployment with no warehouse URL runs without.
        samples = None
        if settings.retail_warehouse_db_url:
            from egpt_retail.semantic.harvest import harvest_schema
            from egpt_retail.suggestions.context import samples_from_harvest

            warehouse = _connect(
                settings.retail_warehouse_db_url, "EGPT_RETAIL_WAREHOUSE_DB_URL"
            )
            try:
                warehouse.autocommit = True
                samples = samples_from_harvest(
                    harvest_schema(warehouse, schema, source="warehouse", sample=True)
                )
            finally:
                warehouse.close()

        run: SuggestionRun = run_suggestions(
            settings,
            reader=reader,
            registry=registry,
            proposals=proposals,
            knowledge=knowledge,
            ontology=ontology,
            dry_run=dry_run,
            samples=samples,
        )
        return run
    finally:
        control_plane.close()
        driver.close()


def main(argv: Optional[Sequence[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = build_parser().parse_args(argv)
    run = suggest(dry_run=args.dry_run, with_prose=not args.no_prose)
    for line in run.lines():
        log.info("%s", line)
    return 0
