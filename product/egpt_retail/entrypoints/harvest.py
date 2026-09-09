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
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = build_parser().parse_args(argv)
    report = refresh_semantic_layer(schema=args.schema, version=args.version)
    for line in report.lines():
        log.info("%s", line)
    return 0
