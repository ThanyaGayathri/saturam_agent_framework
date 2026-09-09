"""K6 -- versioned harvests, diffs between them, and the KPIs a change puts into review (G9).

The gap this closes: a client adds a property, a connector adds a column, and the semantic
layer has no notion of before and after. A KPI formula validated against one harvest breaks
silently on the next — and *silently* is the word that matters. The formula still parses, the
nightly run still fires, and the number is wrong or the statement errors at two in the
morning, which is when nobody is looking and everybody is asleep.

So the point of this package is not the diff. It is **moving the affected KPIs into a review
state before the run, rather than discovering them during it.**

Snapshots live in the control-plane Postgres, not in the graph. The Azure alignment is
explicit that versioned snapshots and KPI definitions belong in governed relational tables:
they are records of what was true at a moment, and a graph that decays or consolidates is the
wrong home for an audit trail.

**Two rules about the review decision, both erring the same way.**

A KPI is put into review when a column it *appears* to reference changes. Appears, because a
formula is SQL text and matching a column name inside it is a heuristic, not a parse. The
heuristic over-reports: a KPI wrongly sent for review costs someone a glance, and one wrongly
left alone fails in production at 2am. When the choice is between those two, this package
picks the glance every time — and says so here rather than hiding the imprecision.

And a **new column-name collision is itself a review item (G19)**, not just a diff line. The
schema became ambiguous in a way it was not before; every KPI touching either table now reads
a name that means two things.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from egpt_retail.contracts.kpi import KpiDefinition
from egpt_retail.semantic.harvest import Harvest, HarvestDiff, diff_harvests

__all__ = [
    "REVIEW_SCHEMA",
    "ReviewItem",
    "ReviewReason",
    "SnapshotStore",
    "kpis_needing_review",
    "structural",
]

#: Its own schema in the control-plane database. Not the warehouse: these are records about
#: the warehouse, and they must survive it being reloaded.
REVIEW_SCHEMA = "semantic"

_DDL = (
    f"CREATE SCHEMA IF NOT EXISTS {REVIEW_SCHEMA}",
    f"""CREATE TABLE IF NOT EXISTS {REVIEW_SCHEMA}.harvest_snapshot (
        version      text PRIMARY KEY,
        schema_name  text        NOT NULL,
        source       text        NOT NULL,
        harvested_at timestamptz NOT NULL,
        payload      jsonb       NOT NULL,
        created_at   timestamptz NOT NULL DEFAULT now()
    )""",
    f"""COMMENT ON TABLE {REVIEW_SCHEMA}.harvest_snapshot IS
        'One row per harvest (K6/G9). The payload is the whole harvest, so a diff between any '
        'two versions can be recomputed later without re-reading a warehouse that has moved on.'""",
)


class ReviewReason(str):
    """Why a KPI needs looking at. Plain strings so a review screen can render them directly."""

    COLUMN_REMOVED = "column removed"
    COLUMN_RETYPED = "column retyped"
    TABLE_REMOVED = "table removed"
    NEW_COLLISION = "new column-name collision"


@dataclass(frozen=True)
class ReviewItem:
    """One KPI, and what about the schema change concerns it."""

    kpi_identifier: str
    reason: str
    #: The thing that changed -- ``table.column``, a table name, or a colliding column name.
    subject: str
    detail: str = ""
    #: The snapshot the KPI was last validated against, so a reviewer can see how far behind
    #: it is rather than only that it is behind.
    validated_against: Optional[str] = None

    def __str__(self) -> str:
        return f"{self.kpi_identifier}: {self.reason} ({self.subject})"


def _references(kpi: KpiDefinition, column: str) -> bool:
    """Whether a KPI's SQL appears to reference a column name.

    Word-boundary matching on the formula text. Deliberately not a SQL parse: the formulas
    carry ``{placeholders}`` and dialect-specific functions, and a parser that failed on one
    of them would silently stop protecting every KPI in that file. A crude match that
    over-reports is the safer failure here.
    """
    return re.search(r"\b" + re.escape(column) + r"\b", kpi.sql) is not None


def kpis_needing_review(
    diff: HarvestDiff,
    kpis: Sequence[KpiDefinition],
    *,
    new_version: str = "",
) -> Tuple[ReviewItem, ...]:
    """Which KPIs a schema change puts into review, and why.

    A KPI qualifies when the changed thing is one it reads: a removed or retyped column whose
    name appears in its SQL and whose table it declares, a removed table it declares, or a
    newly ambiguous column name it references.

    **Added columns are not a review item.** A new column beside the ones a formula already
    reads changes nothing about that formula. Treating every addition as a review item is how
    a review queue becomes noise and then becomes ignored, which is the state in which the
    removal that actually matters slips through.
    """
    items: List[ReviewItem] = []

    for qualified in diff.removed_columns:
        table, _, column = qualified.partition(".")
        for kpi in kpis:
            if table in kpi.source_tables and _references(kpi, column):
                items.append(
                    ReviewItem(
                        kpi_identifier=kpi.identifier,
                        reason=ReviewReason.COLUMN_REMOVED,
                        subject=qualified,
                        detail="the formula reads this column and it no longer exists",
                        validated_against=kpi.validated_against_snapshot,
                    )
                )

    for qualified, before, after in diff.retyped_columns:
        table, _, column = qualified.partition(".")
        for kpi in kpis:
            if table in kpi.source_tables and _references(kpi, column):
                items.append(
                    ReviewItem(
                        kpi_identifier=kpi.identifier,
                        reason=ReviewReason.COLUMN_RETYPED,
                        subject=qualified,
                        detail=f"{before} -> {after}; the formula may still run and mean something else",
                        validated_against=kpi.validated_against_snapshot,
                    )
                )

    for table in diff.removed_tables:
        for kpi in kpis:
            if table in kpi.source_tables:
                items.append(
                    ReviewItem(
                        kpi_identifier=kpi.identifier,
                        reason=ReviewReason.TABLE_REMOVED,
                        subject=table,
                        detail="a declared source table is gone; feasibility will now block this",
                        validated_against=kpi.validated_against_snapshot,
                    )
                )

    # G19: the schema became ambiguous where it was not before.
    for column in diff.new_collisions:
        for kpi in kpis:
            if _references(kpi, column):
                items.append(
                    ReviewItem(
                        kpi_identifier=kpi.identifier,
                        reason=ReviewReason.NEW_COLLISION,
                        subject=column,
                        detail=(
                            "this name now exists on more than one table, so the formula's "
                            "reference to it is newly ambiguous"
                        ),
                        validated_against=kpi.validated_against_snapshot,
                    )
                )

    return tuple(items)


class SnapshotStore:
    """Versioned harvests in the control-plane database.

    The connection is injected: the caller owns its lifetime, and a snapshot written inside a
    harvest's transaction commits or rolls back with it.
    """

    def __init__(self, connection: Any, schema: str = REVIEW_SCHEMA) -> None:
        self._connection = connection
        self._schema = schema

    def ensure_schema(self) -> None:
        """Create the table if absent. Idempotent, and safe to call from several processes.

        **Deliberately self-provisioning**, not a ``MigrationManager`` migration. That
        manager tracks the framework's own tables through a fixed ``BaseDb`` attribute map;
        a product-owned table has no place in it, and teaching it one is a framework change
        that would ship in the wheel for a retail need. Like the pgvector tables, this one
        is created by the code that owns it. The cost is that schema evolution here is by
        ``ADD COLUMN IF NOT EXISTS`` rather than a versioned migration, which is acceptable
        while there are two tables and one owner and should be revisited if either grows.

        ``CREATE TABLE IF NOT EXISTS`` is not race-safe: two callers can both see no table and
        both try to create it, and one fails. A transaction-scoped advisory lock serialises
        them; it is released at commit.
        """
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                ("egpt_retail.semantic.ddl",),
            )
            for statement in _DDL:
                cursor.execute(statement)
        self._connection.commit()

    async def aensure_schema(self) -> None:
        """Async variant."""
        await asyncio.to_thread(self.ensure_schema)

    def save(self, harvest: Harvest, version: str = "") -> str:
        """Store a harvest under a version, returning the version used.

        A version given twice **replaces** the row rather than raising. Re-harvesting the same
        schema on the same day is an ordinary thing to do while debugging, and a store that
        refused would push people toward inventing version strings to get past it -- at which
        point the version stops meaning anything.
        """
        resolved = version or datetime.now(timezone.utc).strftime("%Y%m%d.%H%M%S")
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""INSERT INTO {self._schema}.harvest_snapshot
                    (version, schema_name, source, harvested_at, payload)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (version) DO UPDATE SET
                        schema_name = EXCLUDED.schema_name,
                        source = EXCLUDED.source,
                        harvested_at = EXCLUDED.harvested_at,
                        payload = EXCLUDED.payload""",
                (
                    resolved,
                    harvest.schema,
                    harvest.source,
                    harvest.harvested_at or datetime.now(timezone.utc).isoformat(),
                    json.dumps(structural(harvest.to_dict())),
                ),
            )
        self._connection.commit()
        return resolved

    async def asave(self, harvest: Harvest, version: str = "") -> str:
        """Async variant."""
        return await asyncio.to_thread(self.save, harvest, version)

    def versions(self) -> Tuple[str, ...]:
        """Every stored version, newest first."""
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"SELECT version FROM {self._schema}.harvest_snapshot ORDER BY harvested_at DESC"
            )
            return tuple(row[0] for row in cursor.fetchall())

    async def aversions(self) -> Tuple[str, ...]:
        """Async variant."""
        return await asyncio.to_thread(self.versions)

    def load(self, version: str) -> Optional[Harvest]:
        """One stored harvest, rebuilt from its payload."""
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"SELECT payload FROM {self._schema}.harvest_snapshot WHERE version = %s",
                (version,),
            )
            row = cursor.fetchone()
        return _harvest_from_dict(row[0]) if row else None

    async def aload(self, version: str) -> Optional[Harvest]:
        """Async variant."""
        return await asyncio.to_thread(self.load, version)

    def diff(self, before: str, after: str) -> HarvestDiff:
        """The diff between two stored versions.

        Recomputed from the stored payloads rather than stored alongside them. A diff is
        derivable, and storing a derived thing beside its inputs is how the two drift apart --
        which for an audit record is worse than not having it.
        """
        first, second = self.load(before), self.load(after)
        if first is None or second is None:
            missing = before if first is None else after
            raise LookupError(f"no snapshot stored under version {missing!r}")
        return diff_harvests(first, second)

    async def adiff(self, before: str, after: str) -> HarvestDiff:
        """Async variant."""
        return await asyncio.to_thread(self.diff, before, after)


def structural(payload: Dict[str, Any]) -> Dict[str, Any]:
    """A harvest payload with its sampled values removed.

    A snapshot is a record of *structure* at a moment: it exists so a diff can be recomputed
    later and so a KPI can say which schema it was validated against. Sampled values are row
    data, and five real email addresses per column, versioned forever in an audit table, is
    a retention problem dressed as a feature. They stay in the live harvest a proposer reads
    and go no further (the graph refuses them for the same reason).
    """
    return {
        **payload,
        "tables": [
            {
                **table,
                "columns": [
                    {k: v for k, v in column.items() if k != "sampled_values"}
                    for column in table.get("columns", ())
                ],
            }
            for table in payload.get("tables", ())
        ],
    }


def _harvest_from_dict(payload: Dict[str, Any]) -> Harvest:
    """Rebuild a harvest from its stored payload."""
    from egpt_retail.semantic.harvest import Collision, HarvestedColumn, HarvestedTable

    tables = tuple(
        HarvestedTable(
            schema=table["schema"],
            name=table["name"],
            row_count=table.get("row_count", 0),
            comment=table.get("comment"),
            columns=tuple(
                HarvestedColumn(
                    name=column["name"],
                    data_type=column["data_type"],
                    nullable=column.get("nullable", True),
                    is_key=column.get("is_key", False),
                    default=column.get("default"),
                    comment=column.get("comment"),
                    sampled_values=tuple(column.get("sampled_values", ())),
                )
                for column in table.get("columns", ())
            ),
        )
        for table in payload.get("tables", ())
    )
    collisions = tuple(
        Collision(
            column_name=item["column_name"],
            tables=tuple(item.get("tables", ())),
            data_types=tuple(item.get("data_types", ())),
            is_lineage=item.get("is_lineage", False),
        )
        for item in payload.get("collisions", ())
    )
    return Harvest(
        schema=payload.get("schema", ""),
        tables=tables,
        collisions=collisions,
        harvested_at=payload.get("harvested_at", ""),
        source=payload.get("source", ""),
    )
