"""``egpt-retail harvest`` -- refresh the semantic layer from the warehouse, then exit.

The nightly counterpart to ``load-knowledge``. That one loads what ships; this one reads
what the customer has: the warehouse schema into a versioned snapshot and into the graph,
with the drift since last time reported.

Scheduled after the connectors land and R4 materialises silver, because harvesting before
the load reads yesterday's schema. The schedule itself is Lane R's (their chart, their
pipelines); this is the command it runs.

Idempotent: the same schema harvested twice produces an empty diff and an unchanged graph.
Reviewed mappings, joins and authoritative sources survive it -- the graph write upserts
and prunes rather than wiping, which the P2 verification exists to prove.

``--source-id`` (#309) runs the *other* harvest: a self-serve source registered through
``/configure/sources`` (#304), read with its stored credential and written into the graph
tagged by source (#306). It is the same executor the ``harvest-source`` workflow runs when
the Configure screen's Connect button starts it (#308) -- this flag is the operator's way to
run or re-run one from a shell, without the OS up. Exit status follows the step: 0 when the
source reaches ``modelled``, 1 when the executor refused or failed (the row's
``harvest_state`` says which, and the reason is logged).
"""

from __future__ import annotations

import argparse
import logging
from typing import Optional, Sequence

from egpt_retail.semantic.refresh import refresh_semantic_layer

__all__ = ["build_parser", "main"]

log = logging.getLogger("egpt_retail.harvest")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="egpt-retail harvest",
        description="Harvest the warehouse schema into a snapshot and the graph, then exit.",
    )
    parser.add_argument(
        "--schema",
        default="silver",
        help="Warehouse schema to harvest (default: silver).",
    )
    parser.add_argument(
        "--version",
        default="",
        help="Snapshot version to record. Defaults to a UTC timestamp.",
    )
    parser.add_argument(
        "--suggest",
        action="store_true",
        help=(
            "After the refresh, propose KPIs and use cases over the new layer and file them "
            "as proposals. The review's 'as soon as source is connected... immediately "
            "suggested'."
        ),
    )
    parser.add_argument(
        "--source-id",
        default="",
        help=(
            "Harvest one self-serve data source (its /configure/sources id) with its stored "
            "credential instead of the operator warehouse. Runs the harvest-source workflow's "
            "executor directly; --schema, --version and --suggest do not apply."
        ),
    )
    return parser


def _harvest_registered_source(source_id: str) -> int:
    """The ``--source-id`` path. Imported lazily so the plain warehouse harvest does not
    pay for the executor's dependencies, and run through :func:`asyncio.run` because the
    executor is the workflow step itself (``async def``), not a sync twin of it -- one code
    path for the shell, the scheduler and the Connect button."""
    import asyncio

    from egpt.workflow.types import StepInput

    from egpt_retail.executors.data_source_harvest import harvest_source

    output = asyncio.run(
        harvest_source(StepInput(additional_data={"source_id": source_id}))
    )
    if output.success:
        log.info("%s", output.content or f"source {source_id}: modelled")
        return 0
    log.error("%s", output.error or f"source {source_id}: harvest did not complete")
    return 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.source_id:
        if args.suggest or args.version or args.schema != "silver":
            parser.error(
                "--source-id harvests one registered source as-is; "
                "--schema, --version and --suggest do not apply to it"
            )
        return _harvest_registered_source(args.source_id)
    report = refresh_semantic_layer(schema=args.schema, version=args.version)
    for line in report.lines():
        log.info("%s", line)
    if args.suggest:
        from egpt_retail.entrypoints.suggest import suggest

        run = suggest()
        for line in run.lines():
            log.info("%s", line)
    return 0
