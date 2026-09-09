"""The one function that runs a harvest end to end: warehouse -> snapshot -> graph -> review.

K4, K5 and K6 each do their part and each cookbook wired them together by hand. That left
the product with no path that did all three, so "every harvest produces a versioned
snapshot" was true of the cookbooks and not of the deployment -- and the graph's snapshot
version and the Postgres snapshot version were assigned separately by whoever called them,
with nothing tying the two together. This is the orchestration those packages were missing.

One version, used in both stores. The graph says which snapshot it reflects; the snapshot
table holds that version's structure; a KPI validated against it names the same string.

The harvest is taken without sampled values. Sampling is for a proposer reading live data
to make a suggestion; neither the snapshot nor the graph holds row values (see
``snapshots.structural`` and ``SemanticGraphStore.write_harvest``).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, List, Optional, Sequence, Tuple

from egpt_retail.app.settings import RetailSettings
from egpt_retail.contracts.kpi import KpiDefinition
from egpt_retail.semantic.harvest import HarvestDiff, diff_harvests, harvest_schema
from egpt_retail.semantic.snapshots import (
    ReviewItem,
    SnapshotStore,
    kpis_needing_review,
)
from egpt_retail.semantic.store import EmptyHarvestRefused, SemanticGraphStore

__all__ = ["RefreshReport", "arefresh_semantic_layer", "refresh_semantic_layer"]


@dataclass(frozen=True)
class RefreshReport:
    """What one refresh did, in the order it did it."""

    version: str
    previous_version: Optional[str]
    schema: str
    tables: int
    columns: int
    diff: Optional[HarvestDiff]
    review: Tuple[ReviewItem, ...] = ()

    @property
    def changed(self) -> bool:
        return self.diff is not None and not self.diff.is_empty

    def lines(self) -> List[str]:
        out = [
            f"harvest: {self.schema} -- {self.tables} tables, {self.columns} columns",
            f"snapshot: {self.version}"
            + (
                " (replaced the row of the same version; drift is against what it held)"
                if self.previous_version == self.version
                else f" (previous {self.previous_version})"
                if self.previous_version
                else " (first)"
            ),
            f"graph: written at snapshot {self.version}",
        ]
        if self.diff is None:
            out.append("drift: no previous snapshot to compare with")
        elif self.diff.is_empty:
            out.append("drift: none")
        else:
            out.extend(f"drift: {line}" for line in self.diff.describe().splitlines())
        for item in self.review:
            out.append(f"review: {item}")
        return out


def refresh_semantic_layer(
    settings: Optional[RetailSettings] = None,
    *,
    schema: str = "silver",
    version: str = "",
    kpis: Sequence[KpiDefinition] = (),
    warehouse: Any = None,
    control_plane: Any = None,
    driver: Any = None,
) -> RefreshReport:
    """Harvest the warehouse schema, version it, write it to the graph, and say what moved.

    ``warehouse``, ``control_plane`` and ``driver`` are injectable so a test can drive this
    against whatever it likes; left unset, each is built from settings and closed here.
    ``kpis`` are the definitions to check against the diff; until K11 persists them the
    caller supplies them, and an empty sequence means no review items rather than an error.
    """
    settings = settings or RetailSettings()
    owned: List[Any] = []
    try:
        if warehouse is None:
            warehouse = _connect(
                settings.retail_warehouse_db_url, "EGPT_RETAIL_WAREHOUSE_DB_URL"
            )
            owned.append(warehouse)
        if control_plane is None:
            control_plane = _connect(settings.retail_db_url, "EGPT_RETAIL_DB_URL")
            owned.append(control_plane)
        if driver is None:
            driver = _neo4j(settings)
            owned.append(driver)

        warehouse.autocommit = True
        harvest = harvest_schema(
            warehouse, schema, source=_source_name(settings), sample=False
        )

        if not harvest.tables:
            raise EmptyHarvestRefused(
                f"schema {schema!r} harvested as empty. Not snapshotting it and not writing "
                "it: an empty harvest is what a wrong schema name, a revoked grant or an "
                "outage look like, and recording it would make the next diff report every "
                "table as removed."
            )

        snapshots = SnapshotStore(control_plane)
        snapshots.ensure_schema()
        known = snapshots.versions(schema)
        resolved = version or _default_version()
        # A re-run with the same explicit version replaces its row. Diffing the new row
        # against itself would report no drift however much the schema moved, and there may
        # be no other row at all (a cron job with a constant --version has exactly one). So
        # the previous structure is read *before* the overwrite and diffed directly.
        replacing = snapshots.load(resolved) if resolved in known else None
        previous = resolved if replacing is not None else (known[0] if known else None)

        # Graph first, then the snapshot. A snapshot row is permanent once committed and
        # becomes `previous` for the next diff; if the graph write failed after it, the
        # record would say a version existed that the graph never reflected. The other way
        # round, a failed snapshot leaves the graph one run ahead, which the graph's own
        # snapshot version reports and the next run corrects.
        graph = SemanticGraphStore(
            driver=driver, database=settings.retail_neo4j_database
        )
        graph.ensure_indexes()
        tables = graph.write_harvest(harvest, snapshot_version=resolved)

        snapshots.save(harvest, resolved)
        if replacing is not None:
            diff: Optional[HarvestDiff] = diff_harvests(replacing, harvest)
        elif previous:
            diff = snapshots.diff(previous, resolved)
        else:
            diff = None

        review = (
            kpis_needing_review(diff, kpis, new_version=resolved)
            if diff is not None and kpis
            else ()
        )
        return RefreshReport(
            version=resolved,
            previous_version=previous,
            schema=schema,
            tables=tables,
            columns=harvest.column_count,
            diff=diff,
            review=tuple(review),
        )
    finally:
        for resource in owned:
            resource.close()


async def arefresh_semantic_layer(
    settings: Optional[RetailSettings] = None,
    *,
    schema: str = "silver",
    version: str = "",
    kpis: Sequence[KpiDefinition] = (),
    warehouse: Any = None,
    control_plane: Any = None,
    driver: Any = None,
) -> RefreshReport:
    """Async variant. Blocking I/O against three stores, run off the event loop."""
    return await asyncio.to_thread(
        refresh_semantic_layer,
        settings,
        schema=schema,
        version=version,
        kpis=kpis,
        warehouse=warehouse,
        control_plane=control_plane,
        driver=driver,
    )


def _default_version() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y%m%d.%H%M%S")


def _connect(url: Optional[str], variable: str) -> Any:
    if not url:
        raise SystemExit(
            f"{variable} is not set, so there is nothing to harvest from or into. "
            "Refusing to start rather than reporting a successful refresh of nothing."
        )
    import psycopg

    from egpt_retail.app.db import psycopg_dsn

    return psycopg.connect(psycopg_dsn(url))


def _neo4j(settings: RetailSettings) -> Any:
    if not settings.retail_neo4j_password:
        raise SystemExit(
            "EGPT_RETAIL_NEO4J_PASSWORD is not set, so the harvest has no graph to go to."
        )
    from neo4j import GraphDatabase

    return GraphDatabase.driver(
        settings.retail_neo4j_uri,
        auth=(settings.retail_neo4j_user, settings.retail_neo4j_password),
    )


def _source_name(settings: RetailSettings) -> str:
    """The warehouse database's name, as the harvest's ``source``."""
    url = settings.retail_warehouse_db_url or ""
    return url.rsplit("/", 1)[-1].split("?")[0] or "warehouse"
