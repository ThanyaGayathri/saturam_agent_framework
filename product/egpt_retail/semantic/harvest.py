"""K4 -- reading a warehouse schema into a harvest record. Deterministic, no model (G19).

The first step of the semantic layer, and the one that must never guess. A harvest is what
the enrichment proposers in K8 are handed, what feasibility in K11 subtracts against, and what
K6 diffs to notice a schema moving underneath a KPI. Every one of those inherits whatever this
records, so it reads the catalogue and reports what is there -- nothing inferred, nothing
completed from a pattern.

**Column-name collisions are recorded, because nothing else can see them (G19).** The
framework's own table description returns name, type, nullable and default, and nothing that
would tell two ``amount`` columns apart. A model handed that picks one and sounds equally
confident either way. So any column name appearing in more than one table is recorded here as
a collision, at harvest time, before anything downstream has a chance to guess.

**Column comments are harvested too**, which the package brief does not ask for. R4's DDL
carries real meaning in them -- ``silver.crm_contact.brand`` says *"Known-dirty per
LANDING_CONTRACT.md. Nullable so the row survives and the defect is reported"* -- and that is
exactly the kind of sentence an enrichment proposer should be reading rather than inventing.
Throwing it away and asking a model to re-derive it would be the definition of the wrong
trade.

Everything here is a read. Both variants exist per the repository rule; the async one runs the
catalogue queries off the event loop.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Tuple

__all__ = [
    "Collision",
    "HarvestedColumn",
    "HarvestedTable",
    "Harvest",
    "HarvestDiff",
    "aharvest_schema",
    "diff_harvests",
    "harvest_schema",
]

#: How many distinct values to sample per column. Bounded on purpose: sampled values exist to
#: show an enrichment proposer what a column *looks* like, and a hundred of them would be a
#: data extract rather than metadata. The graph holds types, not rows (D11).
SAMPLE_LIMIT = 5

#: Columns every silver table carries for lineage rather than business meaning. Sampling them
#: is noise -- a load id tells a proposer nothing about the concept -- and in the case of the
#: source path it is the one place a filesystem layout could leak into a prompt.
_LINEAGE_COLUMNS = frozenset(
    {"load_id", "source_path", "source_ingested_at", "conformed_at", "ingested_at"}
)


@dataclass(frozen=True)
class HarvestedColumn:
    """One column, as the catalogue describes it."""

    name: str
    data_type: str
    nullable: bool
    is_key: bool = False
    default: Optional[str] = None
    #: The ``COMMENT ON COLUMN`` text, where the schema author wrote one.
    comment: Optional[str] = None
    #: Distinct values actually present, capped at :data:`SAMPLE_LIMIT`. Empty for a lineage
    #: column, and empty for an empty table -- which are different facts, and the table's
    #: ``row_count`` is what tells them apart.
    sampled_values: Tuple[str, ...] = ()


@dataclass(frozen=True)
class HarvestedTable:
    """One table: its columns, its keys, and how many rows it holds."""

    schema: str
    name: str
    columns: Tuple[HarvestedColumn, ...]
    row_count: int = 0
    comment: Optional[str] = None

    @property
    def qualified(self) -> str:
        return f"{self.schema}.{self.name}"

    @property
    def column_names(self) -> FrozenSet[str]:
        return frozenset(column.name for column in self.columns)

    @property
    def key_columns(self) -> Tuple[str, ...]:
        return tuple(column.name for column in self.columns if column.is_key)

    def column(self, name: str) -> Optional[HarvestedColumn]:
        for column in self.columns:
            if column.name == name:
                return column
        return None


@dataclass(frozen=True)
class Collision:
    """One column name carried by more than one table (G19).

    Not a defect in the warehouse -- ``brand`` genuinely appears on several entities, and
    ``object_id`` is a deliberate convention. It is a defect in what a *model* can see: given
    only name and type, nothing distinguishes ``crm_deal.brand`` from ``analytics_event.brand``,
    and an agent asked about "brand" will pick one. Recording it is what lets K5 ask for an
    authoritative source (G20) and K8 propose per-table meanings rather than one.
    """

    column_name: str
    tables: Tuple[str, ...]
    data_types: Tuple[str, ...]
    #: Whether this name is loader lineage rather than business data. Every silver table
    #: carries ``load_id`` and ``source_path`` by design, so those collide in all five and
    #: mean the same thing in each -- a convention, not an ambiguity. Reporting them beside
    #: ``brand`` buries the one collision that actually needs disambiguating: on the client's
    #: real data, ``brand`` is the join key between analytics and CRM, and ``session_id`` is
    #: the other. Four of ten collisions here are lineage; a proposer handed all ten
    #: undifferentiated reads four rows of noise before the signal.
    is_lineage: bool = False

    @property
    def needs_disambiguation(self) -> bool:
        """Whether a person or a proposer has to decide what this name means per table.

        Lineage never does. A business name carried by two tables always does, and one whose
        types disagree is the urgent case.
        """
        return not self.is_lineage

    @property
    def types_agree(self) -> bool:
        """Whether every table types this column the same way.

        A collision where the types *disagree* is the sharper one: ``amount numeric`` and
        ``amount text`` are not the same measurement wearing one name, and a join or a union
        across them is a bug waiting for a nightly run.
        """
        return len(set(self.data_types)) == 1


@dataclass(frozen=True)
class Harvest:
    """Everything K4 read from one schema, at one moment."""

    schema: str
    tables: Tuple[HarvestedTable, ...]
    collisions: Tuple[Collision, ...] = ()
    harvested_at: str = ""
    #: The source the harvest was taken from, for provenance. Never a connection string --
    #: that carries credentials; the database name is enough to say which warehouse.
    source: str = ""

    @property
    def table_names(self) -> FrozenSet[str]:
        return frozenset(table.name for table in self.tables)

    def table(self, name: str) -> Optional[HarvestedTable]:
        for table in self.tables:
            if table.name == name or table.qualified == name:
                return table
        return None

    @property
    def column_count(self) -> int:
        return sum(len(table.columns) for table in self.tables)

    @property
    def business_collisions(self) -> Tuple[Collision, ...]:
        """Collisions a person or a proposer has to resolve. What G19 is actually about."""
        return tuple(c for c in self.collisions if c.needs_disambiguation)

    def collision(self, column_name: str) -> Optional[Collision]:
        for collision in self.collisions:
            if collision.column_name == column_name:
                return collision
        return None

    def to_dict(self) -> Dict[str, Any]:
        """A plain mapping, for the snapshot K6 versions and diffs."""
        return {
            "schema": self.schema,
            "source": self.source,
            "harvested_at": self.harvested_at,
            "tables": [
                {
                    "schema": table.schema,
                    "name": table.name,
                    "row_count": table.row_count,
                    "comment": table.comment,
                    "columns": [
                        {
                            "name": column.name,
                            "data_type": column.data_type,
                            "nullable": column.nullable,
                            "is_key": column.is_key,
                            "default": column.default,
                            "comment": column.comment,
                            "sampled_values": list(column.sampled_values),
                        }
                        for column in table.columns
                    ],
                }
                for table in self.tables
            ],
            "collisions": [
                {
                    "column_name": collision.column_name,
                    "tables": list(collision.tables),
                    "data_types": list(collision.data_types),
                    "types_agree": collision.types_agree,
                    "is_lineage": collision.is_lineage,
                }
                for collision in self.collisions
            ],
        }


# --- the catalogue queries ---------------------------------------------------------------
#
# `information_schema` for columns and `pg_catalog` for comments and keys. Both are read-only
# views the database maintains itself, which is what makes this deterministic: the harvest is
# whatever the database says it is, with no parsing of DDL text and no inference from names.

_TABLES_SQL = """
SELECT c.relname AS table_name,
       obj_description(c.oid) AS table_comment,
       COALESCE(s.n_live_tup, 0) AS approx_rows
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
WHERE n.nspname = %(schema)s AND c.relkind = 'r'
ORDER BY c.relname
"""

_COLUMNS_SQL = """
SELECT c.relname AS table_name,
       a.attname AS column_name,
       format_type(a.atttypid, a.atttypmod) AS data_type,
       NOT a.attnotnull AS nullable,
       pg_get_expr(d.adbin, d.adrelid) AS column_default,
       col_description(a.attrelid, a.attnum) AS column_comment,
       COALESCE(k.is_key, false) AS is_key
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
LEFT JOIN (
    SELECT con.conrelid, u.attnum, true AS is_key
    FROM pg_constraint con
    CROSS JOIN LATERAL unnest(con.conkey) AS u(attnum)
    WHERE con.contype = 'p'
) k ON k.conrelid = a.attrelid AND k.attnum = a.attnum
WHERE n.nspname = %(schema)s
  AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
  AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY c.relname, a.attnum
"""


def _sample_values(
    cursor: Any, schema: str, table: str, column: str
) -> Tuple[str, ...]:
    """Distinct non-null values from one column, capped.

    Identifiers are quoted through ``format`` rather than interpolated: they come from the
    catalogue rather than from a user, but a harvester that builds SQL by concatenation is one
    refactor away from being handed a name it did not read itself.
    """
    cursor.execute(
        "SELECT format('SELECT DISTINCT %%I::text FROM %%I.%%I WHERE %%I IS NOT NULL LIMIT %s',"
        " %%(col)s, %%(schema)s, %%(table)s, %%(col)s)" % SAMPLE_LIMIT,
        {"col": column, "schema": schema, "table": table},
    )
    row = cursor.fetchone()
    if not row:
        return ()
    cursor.execute(row[0])
    return tuple(str(value[0]) for value in cursor.fetchall())


def _find_collisions(tables: Sequence[HarvestedTable]) -> Tuple[Collision, ...]:
    """Column names carried by more than one table, with the types each gave them."""
    seen: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
    for table in tables:
        for column in table.columns:
            seen[column.name].append((table.qualified, column.data_type))
    collisions = [
        Collision(
            column_name=name,
            tables=tuple(entry[0] for entry in entries),
            data_types=tuple(entry[1] for entry in entries),
            is_lineage=name in _LINEAGE_COLUMNS,
        )
        for name, entries in seen.items()
        if len(entries) > 1
    ]
    # Business collisions first, then types-disagree within those -- a disagreeing type breaks
    # a join rather than merely confusing a proposer. Lineage last, since it needs no decision.
    # Then by table count and name, so a diff between two harvests is stable.
    return tuple(
        sorted(
            collisions,
            key=lambda c: (c.is_lineage, c.types_agree, -len(c.tables), c.column_name),
        )
    )


def harvest_schema(
    connection: Any,
    schema: str,
    *,
    source: str = "",
    sample: bool = True,
    exclude_tables: Sequence[str] = (),
) -> Harvest:
    """Read one schema into a harvest record.

    Args:
        connection: An open DB-API connection (psycopg). The caller owns its lifetime, so a
            harvest can run inside a load's transaction or on its own.
        schema: The schema to read -- ``silver`` for the semantic layer's purposes.
        source: What warehouse this came from, for provenance. A database name, never a DSN.
        sample: Whether to read sampled values. Off makes the harvest pure catalogue, which is
            what a drift check wants: K6 compares structure, and sampled values would make
            every diff noisy with ordinary data movement.
        exclude_tables: Table names to skip.
    """
    excluded = set(exclude_tables)
    tables: List[HarvestedTable] = []
    with connection.cursor() as cursor:
        cursor.execute(_TABLES_SQL, {"schema": schema})
        table_rows = [row for row in cursor.fetchall() if row[0] not in excluded]

        # One catalogue query for every column in the schema, grouped here, rather than one
        # per table. Two queries per table is fine at five tables and is 300 round trips at
        # 150; the count(*) below stays per table because it has to.
        cursor.execute(_COLUMNS_SQL, {"schema": schema})
        columns_by_table: Dict[str, List[Tuple[Any, ...]]] = defaultdict(list)
        for row in cursor.fetchall():
            columns_by_table[row[0]].append(row[1:])

        for table_name, table_comment, _approx in table_rows:
            column_rows = columns_by_table.get(table_name, [])

            # An exact count, not the planner's estimate. `pg_stat_user_tables.n_live_tup` is
            # zero until autovacuum has run, which on a freshly loaded warehouse is exactly
            # when a harvest is taken -- so the estimate reports an empty table that has just
            # been filled, and every downstream feasibility judgement inherits that.
            cursor.execute("SELECT count(*) FROM %s" % _quote_ident(schema, table_name))
            row_count = int(cursor.fetchone()[0])

            columns: List[HarvestedColumn] = []
            for name, data_type, nullable, default, comment, is_key in column_rows:
                values: Tuple[str, ...] = ()
                if sample and row_count and name not in _LINEAGE_COLUMNS:
                    values = _sample_values(cursor, schema, table_name, name)
                columns.append(
                    HarvestedColumn(
                        name=name,
                        data_type=data_type,
                        nullable=bool(nullable),
                        is_key=bool(is_key),
                        default=default,
                        comment=comment,
                        sampled_values=values,
                    )
                )
            tables.append(
                HarvestedTable(
                    schema=schema,
                    name=table_name,
                    columns=tuple(columns),
                    row_count=row_count,
                    comment=table_comment,
                )
            )

    return Harvest(
        schema=schema,
        tables=tuple(tables),
        collisions=_find_collisions(tables),
        harvested_at=datetime.now(timezone.utc).isoformat(),
        source=source,
    )


async def aharvest_schema(
    connection: Any,
    schema: str,
    *,
    source: str = "",
    sample: bool = True,
    exclude_tables: Sequence[str] = (),
) -> Harvest:
    """Async variant. The catalogue queries run off the event loop."""
    return await asyncio.to_thread(
        harvest_schema,
        connection,
        schema,
        source=source,
        sample=sample,
        exclude_tables=exclude_tables,
    )


def _quote_ident(schema: str, table: str) -> str:
    """A safely quoted ``schema.table``.

    Both halves come from the catalogue, so this is defence in depth rather than a live
    injection path -- but a count statement built by concatenation is the kind of line that
    gets copied somewhere the input is not from the catalogue.
    """
    return '"{}"."{}"'.format(schema.replace('"', '""'), table.replace('"', '""'))


@dataclass(frozen=True)
class HarvestDiff:
    """What changed between two harvests of the same schema.

    K4 owns the diff because K4 owns what a harvest *is*; K6 versions the snapshots and decides
    which KPIs a change puts into review. Kept structural on purpose -- a row count moving is
    the warehouse working, not the schema drifting, so counts are reported and never treated as
    a change.
    """

    added_tables: Tuple[str, ...] = ()
    removed_tables: Tuple[str, ...] = ()
    #: ``table.column`` for each, so a name appearing in two tables reads unambiguously here.
    added_columns: Tuple[str, ...] = ()
    removed_columns: Tuple[str, ...] = ()
    #: ``(table.column, before, after)`` -- the change that silently breaks a KPI's SQL.
    retyped_columns: Tuple[Tuple[str, str, str], ...] = ()
    #: Collisions that did not exist in the earlier harvest. A newly introduced one is a
    #: review item (G19): the schema just became ambiguous in a way it was not before.
    new_collisions: Tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not any(
            (
                self.added_tables,
                self.removed_tables,
                self.added_columns,
                self.removed_columns,
                self.retyped_columns,
                self.new_collisions,
            )
        )

    def describe(self) -> str:
        """One line per change, for a review screen or a test log."""
        if self.is_empty:
            return "no structural change"
        lines: List[str] = []
        for name in self.added_tables:
            lines.append(f"table added: {name}")
        for name in self.removed_tables:
            lines.append(f"table removed: {name}")
        for name in self.added_columns:
            lines.append(f"column added: {name}")
        for name in self.removed_columns:
            lines.append(f"column removed: {name}")
        for name, before, after in self.retyped_columns:
            lines.append(f"column retyped: {name} {before} -> {after}")
        for name in self.new_collisions:
            lines.append(f"new column-name collision: {name}")
        return "\n".join(lines)


def diff_harvests(before: Harvest, after: Harvest) -> HarvestDiff:
    """What changed structurally between two harvests.

    Deliberately silent about row counts and sampled values. A nightly load changes both every
    single night, and a diff that reports them would cry wolf until nobody read it -- at which
    point the renamed column it exists to catch goes through unnoticed.
    """
    before_tables = {table.name: table for table in before.tables}
    after_tables = {table.name: table for table in after.tables}

    added_columns: List[str] = []
    removed_columns: List[str] = []
    retyped: List[Tuple[str, str, str]] = []
    for name in sorted(set(before_tables) & set(after_tables)):
        old, new = before_tables[name], after_tables[name]
        old_columns = {column.name: column for column in old.columns}
        new_columns = {column.name: column for column in new.columns}
        added_columns += [
            f"{name}.{c}" for c in sorted(set(new_columns) - set(old_columns))
        ]
        removed_columns += [
            f"{name}.{c}" for c in sorted(set(old_columns) - set(new_columns))
        ]
        for column in sorted(set(old_columns) & set(new_columns)):
            if old_columns[column].data_type != new_columns[column].data_type:
                retyped.append(
                    (
                        f"{name}.{column}",
                        old_columns[column].data_type,
                        new_columns[column].data_type,
                    )
                )

    before_collisions = {c.column_name for c in before.collisions}
    new_collisions = tuple(
        sorted(
            c.column_name
            for c in after.business_collisions
            if c.column_name not in before_collisions
        )
    )

    return HarvestDiff(
        added_tables=tuple(sorted(set(after_tables) - set(before_tables))),
        removed_tables=tuple(sorted(set(before_tables) - set(after_tables))),
        added_columns=tuple(added_columns),
        removed_columns=tuple(removed_columns),
        retyped_columns=tuple(retyped),
        new_collisions=new_collisions,
    )
