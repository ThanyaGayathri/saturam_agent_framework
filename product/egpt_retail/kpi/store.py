"""K11 -- the KPI registry, authoritative in Postgres and mirrored into the graph.

Three things live here, and the split between them is the whole point of the package.

**The registry is data in Postgres.** A KPI definition is a governed record: someone wrote
the formula, someone accepted it, and someone will read the number it produces months later
and compare it against last quarter. That belongs in a relational table with a primary key,
not in a graph that a nightly harvest rewrites.

**The graph copy is a mirror, rebuilt from Postgres and never authored there.** It exists so
a concept reaches its metrics by traversal -- ``(:SemanticConcept)-[:MEASURED_BY]->(:SemanticKpi)``
-- and so a reviewer can ask which KPIs read a table that is about to change
(``(:SemanticKpi)-[:READS]->(:SemanticTable)``). Both are questions about edges, which is
what a graph is for. Neither is the source of truth, and :meth:`KpiRegistryStore.mirror`
drops what it does not find in Postgres, so drift resolves toward the table.

**Feasibility is never stored.** It is
``formula.source_tables - available_tables(reader)`` evaluated at the moment of asking, and
the moment it is cached it starts lying: a harvest drops a table and every stored verdict is
stale while still rendering as a confident badge. What *is* stored is
``validated_against_snapshot`` -- which snapshot the definition was last checked against --
because that is a fact about the past rather than a claim about the present.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from egpt_retail.contracts.kpi import (
    Dialect,
    Feasibility,
    KpiDefinition,
    KpiLevel,
    KpiOrigin,
    KpiParameter,
    KpiRegistry,
    ParameterKind,
    ResultShape,
    TimeWindow,
    TimeWindowUnit,
    assess_feasibility_with_joins,
)
from egpt_retail.contracts.semantic import SemanticRead, available_tables
from egpt_retail.contracts.vocabulary import VocabularySet, load_vocabulary_set

__all__ = [
    "KPI_LABEL",
    "KPI_SCHEMA",
    "KpiConcurrentUpdateError",
    "KpiDeleteResult",
    "KpiRegistryStore",
    "RevalidationReport",
    "row_to_definition",
]

#: The same control-plane schema K6's snapshots and K7's proposals use.
KPI_SCHEMA = "semantic"

#: Prefixed like K5's labels, so it cannot collide with memory's fixed three
#: (``Episode`` / ``Fact`` / ``Entity``) or with K3's ``OntologyConcept``.
KPI_LABEL = "SemanticKpi"

# Imported, not restated. `semantic/store.py` owns these labels; a second copy here drifts
# the first time one is renamed, and the symptom is a mirror that silently matches nothing.
# Same reasoning, one schema over: `dashboards/store.py` owns the dashboards schema name and
# the dashboard table's shape. `delete_if_unreferenced` below is the one place this store
# reaches outside its own table -- a plain check-then-delete across two round trips is exactly
# the race it exists to close, so the reference check has to run as part of the same DELETE.
from egpt_retail.dashboards.store import (  # noqa: E402
    DASHBOARDS_SCHEMA as _DASHBOARDS_SCHEMA,
)
from egpt_retail.dashboards.store import (  # noqa: E402
    DashboardStore as _DashboardStore,
)
from egpt_retail.semantic.store import (  # noqa: E402
    CONCEPT_LABEL as _CONCEPT_LABEL,
)
from egpt_retail.semantic.store import (  # noqa: E402
    TABLE_LABEL as _TABLE_LABEL,
)


def _ddl(schema: str) -> Tuple[str, ...]:
    """The table's DDL, bound to the schema this store was actually given.

    The constant it replaced hardcoded :data:`KPI_SCHEMA`, so a store constructed with a
    different schema created its table in one place and read it from another -- silently, and
    only for a caller that passed the argument the constructor advertises.
    """
    return (
        f"CREATE SCHEMA IF NOT EXISTS {schema}",
        f"""CREATE TABLE IF NOT EXISTS {schema}.kpi_definition (
        identifier                 text PRIMARY KEY,
        name                       text        NOT NULL,
        description                text        NOT NULL DEFAULT '',
        sql                        text        NOT NULL,
        grain                      text        NOT NULL,
        business_area              text        NOT NULL,
        source_tables              jsonb       NOT NULL,
        result_shape               text        NOT NULL,
        dialect                    text        NOT NULL,
        parameters                 jsonb       NOT NULL DEFAULT '[]'::jsonb,
        time_window                jsonb,
        validated_against_snapshot text,
        concept                    text,
        created_at                 timestamptz NOT NULL DEFAULT now(),
        updated_at                 timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT kpi_source_tables_not_empty
            CHECK (jsonb_array_length(source_tables) > 0)
    )""",
        # B2 (issue #231): presentation metadata added after the table's original 13 columns.
        # Idempotent, like the rest -- for a table created before these existed.
        f"ALTER TABLE {schema}.kpi_definition ADD COLUMN IF NOT EXISTS subject_area text",
        f"ALTER TABLE {schema}.kpi_definition ADD COLUMN IF NOT EXISTS level text",
        f"ALTER TABLE {schema}.kpi_definition ADD COLUMN IF NOT EXISTS unit text",
        f"ALTER TABLE {schema}.kpi_definition ADD COLUMN IF NOT EXISTS industry_benchmark text",
        f"""ALTER TABLE {schema}.kpi_definition
        ADD COLUMN IF NOT EXISTS active boolean NOT NULL DEFAULT true""",
        f"""ALTER TABLE {schema}.kpi_definition
        ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'customer'""",
        f"""ALTER TABLE {schema}.kpi_definition
        ADD COLUMN IF NOT EXISTS dataset_ids jsonb NOT NULL DEFAULT '[]'::jsonb""",
        f"""ALTER TABLE {schema}.kpi_definition
        ADD COLUMN IF NOT EXISTS process_ids jsonb NOT NULL DEFAULT '[]'::jsonb""",
        f"""CREATE INDEX IF NOT EXISTS kpi_definition_area
        ON {schema}.kpi_definition (business_area)""",
        f"""CREATE INDEX IF NOT EXISTS kpi_definition_concept
        ON {schema}.kpi_definition (concept)""",
        f"""COMMENT ON TABLE {schema}.kpi_definition IS
        'The KPI registry (K11). Authoritative; the graph holds a mirror rebuilt from here. '
        'Feasibility is deliberately absent -- it is a set difference computed at read time, '
        'and a cached verdict goes stale the moment a harvest runs. '
        'kpi_source_tables_not_empty makes a definition feasibility cannot be computed for '
        'unrepresentable rather than merely discouraged.'""",
    )


class KpiConcurrentUpdateError(Exception):
    """Raised by :meth:`KpiRegistryStore.save_if_unchanged`/``asave_if_unchanged`` when the
    row did not match the ``updated_at`` the caller read it at -- either another writer
    updated it, or it was deleted, in the gap between that read and this call. Either way the
    definition the caller is holding is stale; the router turns this into a 409 rather than
    letting the write silently overwrite whatever landed in between.
    """

    def __init__(self, identifier: str) -> None:
        super().__init__(
            f"{identifier} was modified or removed since it was last read; re-fetch before "
            f"retrying."
        )
        self.identifier = identifier


@dataclass(frozen=True)
class KpiDeleteResult:
    """What :meth:`KpiRegistryStore.delete_if_unreferenced` decided."""

    #: Whether the row was actually removed.
    removed: bool
    #: Dashboard names still referencing the KPI, when ``removed`` is ``False`` because of
    #: that -- narrowed to whatever ``delete_if_unreferenced``'s ``caller`` argument may see
    #: (all of them, when ``caller`` was omitted). Empty when ``removed`` is ``False`` because
    #: the identifier did not exist, or when ``removed`` is ``True``.
    referenced_by: Tuple[str, ...] = ()
    #: How many more dashboards reference the KPI beyond ``referenced_by``, that ``caller``
    #: cannot see (e.g. another owner's private dashboard) -- the block decision itself does
    #: not care about visibility, but the message a caller receives should not name a
    #: dashboard they have no visibility into (#240 review, round 2). Always ``0`` when
    #: ``caller`` was omitted.
    hidden_reference_count: int = 0


@dataclass(frozen=True)
class RevalidationReport:
    """What one revalidation pass decided, and against which snapshot."""

    snapshot_version: str
    #: Identifiers whose every source table is present, now stamped with the snapshot.
    validated: Tuple[str, ...] = ()
    #: Identifiers with at least one table missing. Deliberately **not** stamped: a stamp
    #: means "checked and computable here", and stamping a blocked KPI would tell K6 the
    #: definition had been validated against a snapshot that cannot run it.
    unvalidated: Tuple[str, ...] = ()

    def __str__(self) -> str:
        return (
            f"{len(self.validated)} validated against {self.snapshot_version}, "
            f"{len(self.unvalidated)} left unstamped"
        )


def _definition_to_row(kpi: KpiDefinition) -> Tuple[Any, ...]:
    return (
        kpi.identifier,
        kpi.name,
        kpi.description,
        kpi.sql,
        kpi.grain,
        kpi.business_area,
        json.dumps(sorted(kpi.source_tables)),
        kpi.result_shape.value,
        kpi.dialect.value,
        json.dumps(
            [
                {"name": p.name, "kind": p.kind.value, "description": p.description}
                for p in kpi.parameters
            ]
        ),
        json.dumps(
            {"length": kpi.time_window.length, "unit": kpi.time_window.unit.value}
            if kpi.time_window
            else None
        ),
        kpi.validated_against_snapshot,
        kpi.concept,
        # B2 (issue #231): presentation metadata. `updated_at` is deliberately absent here --
        # the column defaults to `now()` on insert and is stamped explicitly by the
        # `ON CONFLICT ... DO UPDATE SET updated_at = now()` clause on update; it is never a
        # value this store writes on the caller's behalf.
        kpi.subject_area,
        kpi.level.value if kpi.level else None,
        kpi.unit,
        kpi.industry_benchmark,
        kpi.active,
        kpi.source.value,
        json.dumps(list(kpi.dataset_ids)),
        json.dumps(list(kpi.process_ids)),
    )


def row_to_definition(row: Sequence[Any]) -> KpiDefinition:
    """Rebuild a definition from its stored row.

    Goes through ``KpiDefinition.__post_init__`` like any other construction, so a row that
    somehow drifted out of shape -- a formula edited in place to read a table the row does
    not list -- fails loudly on read rather than feeding feasibility a definition whose
    ``source_tables`` no longer matches its SQL.
    """
    window = row[10]
    if isinstance(window, str):
        window = json.loads(window)
    parameters = row[9]
    if isinstance(parameters, str):
        parameters = json.loads(parameters)
    tables = row[6]
    if isinstance(tables, str):
        tables = json.loads(tables)
    dataset_ids = row[19]
    if isinstance(dataset_ids, str):
        dataset_ids = json.loads(dataset_ids)
    process_ids = row[20]
    if isinstance(process_ids, str):
        process_ids = json.loads(process_ids)
    return KpiDefinition(
        identifier=row[0],
        name=row[1],
        description=row[2] or "",
        sql=row[3],
        grain=row[4],
        business_area=row[5],
        source_tables=frozenset(tables),
        result_shape=ResultShape(row[7]),
        dialect=Dialect(row[8]),
        parameters=tuple(
            KpiParameter(
                name=p["name"],
                kind=ParameterKind(p["kind"]),
                description=p.get("description", ""),
            )
            for p in (parameters or ())
        ),
        time_window=(
            TimeWindow(length=window["length"], unit=TimeWindowUnit(window["unit"]))
            if window
            else None
        ),
        validated_against_snapshot=row[11],
        concept=row[12],
        subject_area=row[13],
        level=KpiLevel(row[14]) if row[14] else None,
        unit=row[15],
        industry_benchmark=row[16],
        active=row[17],
        source=KpiOrigin(row[18]),
        dataset_ids=tuple(dataset_ids or ()),
        process_ids=tuple(process_ids or ()),
        updated_at=row[21],
    )


class KpiRegistryStore:
    """The KPI registry in the control-plane database, with a graph mirror.

    The connection is injected; the caller owns its lifetime. ``vocabularies`` defaults to
    the shipped closed sets -- never ``None``, because a store that skipped term validation
    would accept a business area no screen lists and nothing would say so.
    """

    def __init__(
        self,
        connection: Any,
        schema: str = KPI_SCHEMA,
        *,
        vocabularies: Optional[VocabularySet] = None,
    ) -> None:
        self._connection = connection
        self._schema = schema
        self._vocabularies = vocabularies or load_vocabulary_set()

    # --- schema ------------------------------------------------------------------------

    def ensure_schema(self) -> None:
        """Create the table if absent. Idempotent, and safe from several processes.

        Self-provisioning for the reason given in ``semantic/proposals.py``: the framework's
        ``MigrationManager`` tracks framework-owned tables through a fixed ``BaseDb``
        attribute map, and teaching it a retail table would ship a retail need in the wheel.
        The advisory lock is transaction-scoped and serialises concurrent creators.
        """
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                ("egpt_retail.semantic.ddl",),
            )
            for statement in _ddl(self._schema):
                cursor.execute(statement)
        self._connection.commit()

    async def aensure_schema(self) -> None:
        """Async variant."""
        await asyncio.to_thread(self.ensure_schema)

    # --- writing -----------------------------------------------------------------------

    def save(self, kpi: KpiDefinition) -> None:
        """Insert or replace one definition."""
        self.save_many((kpi,))

    async def asave(self, kpi: KpiDefinition) -> None:
        """Async variant."""
        await asyncio.to_thread(self.save, kpi)

    def create(self, kpi: KpiDefinition) -> Optional[KpiDefinition]:
        """Insert one definition, refusing to overwrite an existing identifier.

        This -- ``INSERT ... ON CONFLICT (identifier) DO NOTHING`` -- is the actual 409 guard
        for a duplicate identifier, not a caller's ``get(identifier) is None`` pre-check
        (worth keeping only as a fast-path optimization, never as the sole source of truth).
        Two concurrent creates for the same identifier both pass a pre-check like that; only
        one of the two ``INSERT``s here can land a row, and the loser gets ``None`` back
        rather than silently overwriting the winner's fields the way :meth:`save`'s plain
        upsert would.
        """
        registry = KpiRegistry.build((kpi,), self._vocabularies)
        (validated_kpi,) = tuple(registry)
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""INSERT INTO {self._schema}.kpi_definition
                    (identifier, name, description, sql, grain, business_area,
                     source_tables, result_shape, dialect, parameters, time_window,
                     validated_against_snapshot, concept, subject_area, level, unit,
                     industry_benchmark, active, source, dataset_ids, process_ids)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (identifier) DO NOTHING
                    RETURNING identifier""",
                _definition_to_row(validated_kpi),
            )
            inserted = cursor.fetchone() is not None
        self._connection.commit()
        if not inserted:
            return None
        saved = self.get(kpi.identifier)
        if saved is None:
            # Extremely narrow: this commit made the row visible to every other session,
            # including one that deletes it before this connection's own follow-up SELECT
            # runs. A bare `assert` here would be stripped under `python -O`, silently
            # returning `None` against this method's declared `KpiDefinition` return type.
            raise RuntimeError(
                f"{kpi.identifier} vanished immediately after being inserted."
            )
        return saved

    async def acreate(self, kpi: KpiDefinition) -> Optional[KpiDefinition]:
        """Async variant."""
        return await asyncio.to_thread(self.create, kpi)

    def save_if_unchanged(
        self, kpi: KpiDefinition, expected_updated_at: Optional[datetime]
    ) -> KpiDefinition:
        """Update one definition, conditioned on ``updated_at`` still matching what the
        caller read (optimistic concurrency).

        This closes the lost-update race a plain upsert-by-identifier leaves open: two
        concurrent ``PATCH``es both read the same row, both compute their own new version
        from it, and :meth:`save`'s plain ``ON CONFLICT DO UPDATE`` lets whichever commits
        last silently and completely overwrite the other's write with no record either
        happened. Mirrors ``evals/rehearsal.py``'s ``RehearsalReviewStore._decide``: the
        ``WHERE ... AND updated_at = %s`` clause is the entire enforcement -- if the row moved
        (or vanished) since the read, the ``UPDATE`` matches zero rows and the caller, not
        this store, decides what to do about it.

        Raises :class:`KpiConcurrentUpdateError` when no row matched: the definition the
        caller read at ``expected_updated_at`` is stale (either something else updated it, or
        deleted it) and must be re-fetched before deciding whether to retry.
        """
        registry = KpiRegistry.build((kpi,), self._vocabularies)
        (validated_kpi,) = tuple(registry)
        row = _definition_to_row(validated_kpi)
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""UPDATE {self._schema}.kpi_definition SET
                        name = %s, description = %s, sql = %s, grain = %s,
                        business_area = %s, source_tables = %s, result_shape = %s,
                        dialect = %s, parameters = %s, time_window = %s,
                        validated_against_snapshot = %s, concept = %s, subject_area = %s,
                        level = %s, unit = %s, industry_benchmark = %s, active = %s,
                        source = %s, dataset_ids = %s, process_ids = %s, updated_at = now()
                    WHERE identifier = %s AND updated_at = %s""",
                row[1:] + (kpi.identifier, expected_updated_at),
            )
            matched = cursor.rowcount == 1
        if not matched:
            self._connection.rollback()
            raise KpiConcurrentUpdateError(kpi.identifier)
        self._connection.commit()
        saved = self.get(kpi.identifier)
        if saved is None:
            # Extremely narrow: the commit above made the row visible to every other
            # session, including one that deletes it before this connection's own follow-up
            # SELECT runs -- so this is a fresh read after the transaction closed, not "the
            # same transaction". A bare `assert` here would be stripped under `python -O`,
            # silently returning `None` against this method's declared `KpiDefinition`
            # return type.
            raise RuntimeError(
                f"{kpi.identifier} vanished immediately after being updated."
            )
        return saved

    async def asave_if_unchanged(
        self, kpi: KpiDefinition, expected_updated_at: Optional[datetime]
    ) -> KpiDefinition:
        """Async variant."""
        return await asyncio.to_thread(self.save_if_unchanged, kpi, expected_updated_at)

    def save_many(self, kpis: Sequence[KpiDefinition]) -> int:
        """Insert or replace several definitions in one transaction. Returns how many.

        Every definition passes through :class:`KpiRegistry` first, so ``grain`` and
        ``business_area`` are checked against this deployment's vocabularies and duplicate
        identifiers are refused *before* anything is written. Validating per row inside the
        loop would leave the first half of a batch committed behind a bad second half.
        """
        registry = KpiRegistry.build(kpis, self._vocabularies)
        with self._connection.cursor() as cursor:
            for kpi in registry:
                cursor.execute(
                    f"""INSERT INTO {self._schema}.kpi_definition
                        (identifier, name, description, sql, grain, business_area,
                         source_tables, result_shape, dialect, parameters, time_window,
                         validated_against_snapshot, concept, subject_area, level, unit,
                         industry_benchmark, active, source, dataset_ids, process_ids)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (identifier) DO UPDATE SET
                            name = EXCLUDED.name,
                            description = EXCLUDED.description,
                            sql = EXCLUDED.sql,
                            grain = EXCLUDED.grain,
                            business_area = EXCLUDED.business_area,
                            source_tables = EXCLUDED.source_tables,
                            result_shape = EXCLUDED.result_shape,
                            dialect = EXCLUDED.dialect,
                            parameters = EXCLUDED.parameters,
                            time_window = EXCLUDED.time_window,
                            validated_against_snapshot =
                                EXCLUDED.validated_against_snapshot,
                            concept = EXCLUDED.concept,
                            subject_area = EXCLUDED.subject_area,
                            level = EXCLUDED.level,
                            unit = EXCLUDED.unit,
                            industry_benchmark = EXCLUDED.industry_benchmark,
                            active = EXCLUDED.active,
                            source = EXCLUDED.source,
                            dataset_ids = EXCLUDED.dataset_ids,
                            process_ids = EXCLUDED.process_ids,
                            updated_at = now()""",
                    _definition_to_row(kpi),
                )
        self._connection.commit()
        return len(registry)

    async def asave_many(self, kpis: Sequence[KpiDefinition]) -> int:
        """Async variant."""
        return await asyncio.to_thread(self.save_many, kpis)

    def delete(self, identifier: str) -> bool:
        """Remove one definition. Returns whether it was there."""
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"DELETE FROM {self._schema}.kpi_definition WHERE identifier = %s",
                (identifier,),
            )
            removed = bool(cursor.rowcount)
        self._connection.commit()
        return removed

    async def adelete(self, identifier: str) -> bool:
        """Async variant."""
        return await asyncio.to_thread(self.delete, identifier)

    def delete_if_unreferenced(
        self, identifier: str, *, caller: Optional[str] = None
    ) -> KpiDeleteResult:
        """Delete one definition, refusing if any dashboard still has a tile naming it --
        guard and delete as a single statement, not a check followed by a separate
        :meth:`delete`.

        ``caller``, when given, narrows :attr:`KpiDeleteResult.referenced_by` to dashboard
        names ``caller`` can actually see (:class:`DashboardStore`'s own
        ``list_visible_to``), moving anything else into :attr:`KpiDeleteResult.
        hidden_reference_count` instead -- reusing this same connection, not a second pooled
        one. This only affects what a caller is told; the block decision two paragraphs down
        still checks every dashboard regardless of visibility. Omitted (the default), every
        referencing name is reported, visible or not -- e.g. for a caller with no per-user
        identity to scope by.

        A dashboard tile created for this KPI after a plain existence/reference check but
        before the actual ``DELETE`` would otherwise be left pointing at a KPI that no longer
        exists. The ``NOT EXISTS`` subquery below runs as part of the same statement the row
        is removed in, which shrinks that window from a full HTTP round trip to a single SQL
        statement's own execution -- a huge practical improvement, but not a mathematical
        guarantee: under Postgres's default READ COMMITTED isolation, this statement still
        takes its own snapshot when it starts, so a dashboard-tile ``INSERT`` that commits
        after this ``DELETE`` has begun but before it finishes is invisible to the subquery
        and can still land a tile pointing at the now-deleted KPI. Closing that residual,
        microsecond-scale window fully would need ``SERIALIZABLE`` isolation or an explicit
        lock (e.g. ``SELECT ... FOR SHARE`` on the referencing dashboard rows first); the
        reverse guard in ``dashboards/store.py``'s ``DashboardStore.create``/``update`` (the
        write side of this exact race, #240 review round 2) has the identical residual window
        for the same reason.

        The subquery scans every dashboard in ``dashboards.dashboard`` regardless of owner or
        visibility -- deliberately not ``DashboardStore.list_visible_to``, which answers "what
        can this one caller see" and would let a KPI referenced only by another user's private
        dashboard be deleted out from under it. Reaching into the ``dashboards`` schema by
        name/shape is the one exception to this store touching only its own table: the
        alternative is a check-then-delete across two round trips, which is exactly the race
        this method exists to close.
        """
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""DELETE FROM {self._schema}.kpi_definition
                    WHERE identifier = %s
                      AND NOT EXISTS (
                          SELECT 1
                          FROM {_DASHBOARDS_SCHEMA}.dashboard AS d,
                               jsonb_array_elements(d.tiles) AS tile
                          WHERE tile ->> 'kpi_identifier' = %s
                      )
                    RETURNING identifier""",
                (identifier, identifier),
            )
            removed = cursor.fetchone() is not None
        self._connection.commit()
        if removed:
            return KpiDeleteResult(removed=True)

        # The DELETE matched nothing: either there is no such KPI, or it is referenced.
        # Read-only from here (no separate pooled connection -- this store's own connection
        # already holds everything needed), and only ever the rare failure path.
        if self.get(identifier) is None:
            return KpiDeleteResult(removed=False)
        all_names = tuple(
            sorted(
                d.name
                for d in _DashboardStore(self._connection).list_referencing(identifier)
            )
        )
        if caller is None:
            return KpiDeleteResult(removed=False, referenced_by=all_names)
        visible_names = {
            d.name for d in _DashboardStore(self._connection).list_visible_to(caller)
        }
        referenced_by = tuple(n for n in all_names if n in visible_names)
        return KpiDeleteResult(
            removed=False,
            referenced_by=referenced_by,
            hidden_reference_count=len(all_names) - len(referenced_by),
        )

    async def adelete_if_unreferenced(
        self, identifier: str, *, caller: Optional[str] = None
    ) -> KpiDeleteResult:
        """Async variant."""
        return await asyncio.to_thread(
            self.delete_if_unreferenced, identifier, caller=caller
        )

    # --- reading -----------------------------------------------------------------------

    def get(self, identifier: str) -> Optional[KpiDefinition]:
        """One definition by identifier."""
        rows = self._select("WHERE identifier = %s", (identifier,))
        return rows[0] if rows else None

    async def aget(self, identifier: str) -> Optional[KpiDefinition]:
        """Async variant."""
        return await asyncio.to_thread(self.get, identifier)

    def list(
        self,
        *,
        business_area: Optional[str] = None,
        concept: Optional[str] = None,
    ) -> Tuple[KpiDefinition, ...]:
        """Definitions, optionally narrowed to a business area or a concept."""
        clauses: List[str] = []
        params: List[Any] = []
        if business_area is not None:
            self._vocabularies.validate("business_area", business_area)
            clauses.append("business_area = %s")
            params.append(business_area)
        if concept is not None:
            clauses.append("concept = %s")
            params.append(concept)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return tuple(self._select(where, tuple(params)))

    async def alist(
        self,
        *,
        business_area: Optional[str] = None,
        concept: Optional[str] = None,
    ) -> Tuple[KpiDefinition, ...]:
        """Async variant."""
        return await asyncio.to_thread(
            self.list, business_area=business_area, concept=concept
        )

    def registry(self) -> KpiRegistry:
        """The whole registry, revalidated against this deployment's vocabularies."""
        return KpiRegistry.build(self.list(), self._vocabularies)

    async def aregistry(self) -> KpiRegistry:
        """Async variant."""
        return await asyncio.to_thread(self.registry)

    # --- adoption: the registry is the consumer of accepted KPI proposals (K7) ------------

    def adopt_accepted(self, proposals: Any) -> Tuple[str, ...]:
        """Take every accepted, unadopted ``KPI`` proposal into the registry. Returns identifiers.

        K7 left ``KPI`` outside ``GRAPH_KINDS`` because its consumer is this table, not the
        graph -- and until this existed there was no consumer, so an accepted KPI proposal
        stayed accepted and unapplied forever. The payload was validated into a full
        ``KpiDefinition`` at propose time, so adoption has nothing left to decide; it saves
        and stamps ``applied_at``, the same contract ``apply_accepted`` keeps for the graph.
        """
        from egpt_retail.semantic.proposals import (
            ProposalKind,
            ReviewState,
            validate_payload,
        )

        pending = [
            proposal
            for proposal in proposals.list(
                state=ReviewState.ACCEPTED, kind=ProposalKind.KPI
            )
            if proposal.applied_at is None
        ]
        # Validate the whole batch before writing any of it. Validating inside the loop meant
        # a bad second payload -- a business_area the deployment does not have -- left the
        # first adopted, the third never looked at, and the exception carrying no hint that
        # the run was half-applied. A batch that cannot all be adopted adopts none.
        built = [
            (proposal, validate_payload(ProposalKind.KPI, proposal.payload))
            for proposal in pending
        ]

        adopted: List[str] = []
        for proposal, definition in built:
            self.save(definition)
            proposals.mark_applied(proposal.identifier)
            adopted.append(definition.identifier)
        return tuple(adopted)

    async def aadopt_accepted(self, proposals: Any) -> Tuple[str, ...]:
        """Async variant."""
        return await asyncio.to_thread(self.adopt_accepted, proposals)

    # --- feasibility --------------------------------------------------------------------

    def feasibility(
        self,
        reader: SemanticRead,
        *,
        kpis: Optional[Sequence[KpiDefinition]] = None,
    ) -> Mapping[str, Feasibility]:
        """Feasibility for the given (or, by default, every stored) KPI against what the
        semantic layer holds now.

        Computed, never read back from a column. ``assess_registry`` is a set difference over
        two frozensets: it takes data and returns data, has no model, no client and no
        callable to be persuaded by, and that is the entire guarantee behind the ready and
        blocked badges a customer sees.

        ``kpis``, when given, is used instead of a fresh :meth:`list`: a caller that already
        holds the exact definition(s) it cares about -- e.g. the row a create/update route
        just wrote on this same connection -- gets feasibility derived from that, rather than
        from a second, independent read of the whole table that a concurrent delete could
        race (the TOCTOU the #240 review flagged at both call sites in ``api/routers/
        kpis.py``). ``None`` (the default) keeps the previous whole-registry behavior, for
        ``list``/``get`` style callers.
        """
        tables = available_tables(reader)
        # Part of the SemanticRead contract, not an optional extra: a reader with no join
        # rulings answers False for every pair, and a reader that has them gets asked. The
        # first version probed with getattr and fell back to the plain set difference for the
        # stub -- silently weaker than production, with nothing to say so.
        oracle = reader.unjoinable_oracle()
        definitions: Sequence[KpiDefinition] = self.list() if kpis is None else kpis
        return {
            kpi.identifier: assess_feasibility_with_joins(kpi, tables, oracle)
            for kpi in definitions
        }

    async def afeasibility(
        self,
        reader: SemanticRead,
        *,
        kpis: Optional[Sequence[KpiDefinition]] = None,
    ) -> Mapping[str, Feasibility]:
        """Async variant."""
        return await asyncio.to_thread(self.feasibility, reader, kpis=kpis)

    def revalidate(
        self, reader: SemanticRead, snapshot_version: str = ""
    ) -> RevalidationReport:
        """Stamp every computable definition with the snapshot it was checked against (K6).

        This is the writer of ``validated_against_snapshot``. K6 reads it to tell a reviewer
        how far behind a KPI is when a column it reads changes; until this existed, the field
        was only ever read and never written, so every review item said ``None``.

        A definition whose tables are not all present is left unstamped rather than stamped
        with a verdict. "Validated against snapshot X" has to mean the formula could run
        against X, or K6's review screen inherits a claim nobody checked.
        """
        version = snapshot_version or reader.snapshot().version
        have = available_tables(reader)
        validated: List[str] = []
        unvalidated: List[str] = []
        for kpi in self.list():
            if kpi.source_tables <= have:
                validated.append(kpi.identifier)
            else:
                unvalidated.append(kpi.identifier)
        if validated:
            with self._connection.cursor() as cursor:
                cursor.execute(
                    f"""UPDATE {self._schema}.kpi_definition
                        SET validated_against_snapshot = %s, updated_at = now()
                        WHERE identifier = ANY(%s)""",
                    (version, validated),
                )
            self._connection.commit()
        return RevalidationReport(
            snapshot_version=version,
            validated=tuple(sorted(validated)),
            unvalidated=tuple(sorted(unvalidated)),
        )

    def stamp_validated(self, identifiers: Sequence[str], snapshot_version: str) -> int:
        """Stamp definitions a caller has already decided are computable and not in review.

        The refresh is that caller: it knows the diff, so it knows which KPIs a change
        touched and must not stamp. One statement for the batch, matching ``revalidate``'s
        own shape -- the first version took a single identifier and committed per call, so a
        hundred-KPI registry meant a hundred round trips and a hundred fsyncs.
        """
        wanted = list(dict.fromkeys(identifiers))
        if not wanted:
            return 0
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""UPDATE {self._schema}.kpi_definition
                    SET validated_against_snapshot = %s, updated_at = now()
                    WHERE identifier = ANY(%s)""",
                (snapshot_version, wanted),
            )
        self._connection.commit()
        return len(wanted)

    async def astamp_validated(
        self, identifiers: Sequence[str], snapshot_version: str
    ) -> int:
        """Async variant."""
        return await asyncio.to_thread(
            self.stamp_validated, identifiers, snapshot_version
        )

    async def arevalidate(
        self, reader: SemanticRead, snapshot_version: str = ""
    ) -> RevalidationReport:
        """Async variant."""
        return await asyncio.to_thread(self.revalidate, reader, snapshot_version)

    # --- the graph mirror ----------------------------------------------------------------

    def mirror(self, driver: Any, database: str = "neo4j") -> int:
        """Rebuild the graph's copy of the registry from Postgres. Returns KPIs written.

        Upsert-and-prune, scoped to this label: a KPI deleted from the table loses its node,
        and one renamed loses the old node rather than leaving two. The prune is bound to
        ``SemanticKpi`` and touches nothing else -- the P2 review found what a label-blind
        prune costs, twice.

        Edges go both ways from the KPI: ``MEASURED_BY`` from the concept it hangs off, so a
        concept reaches its metrics by traversal, and ``READS`` to each source table present
        in the graph, so a reviewer can ask which KPIs a changing table breaks.

        **Both edges are matched, never merged into existence.** A source table the graph does
        not hold gets no edge -- the absence *is* the feasibility gap, and a placeholder node
        would put a table in the semantic layer that no harvest ever saw. The same holds for
        the concept, and it is the sharper case: concepts enter the graph through an accepted
        proposal and nowhere else (D10), so a mirror that created one would let registering a
        KPI invent a concept nobody reviewed. The first version of this method merged the
        concept and did exactly that -- K5's cookbook caught it, because the four concepts it
        conjured made the real store and the stub disagree about ``list_concepts()``.
        """
        rows = [
            {
                "identifier": kpi.identifier,
                "name": kpi.name,
                "description": kpi.description,
                "grain": kpi.grain,
                "business_area": kpi.business_area,
                "concept": kpi.concept,
                "source_tables": sorted(kpi.source_tables),
                "validated_against_snapshot": kpi.validated_against_snapshot,
            }
            for kpi in self.list()
        ]
        with driver.session(database=database) as session:
            session.execute_write(self._mirror_tx, rows)
        return len(rows)

    async def amirror(self, driver: Any, database: str = "neo4j") -> int:
        """Async variant."""
        return await asyncio.to_thread(self.mirror, driver, database)

    @staticmethod
    def _mirror_tx(tx: Any, rows: List[Dict[str, Any]]) -> None:
        """The whole mirror in one transaction, so a failure part-way leaves the previous
        copy rather than a half-written one."""
        tx.run(
            f"""
            UNWIND $rows AS row
            MERGE (k:{KPI_LABEL} {{identifier: row.identifier}})
            SET k.name = row.name,
                k.description = row.description,
                k.grain = row.grain,
                k.business_area = row.business_area,
                k.concept = row.concept,
                k.source_tables = row.source_tables,
                k.validated_against_snapshot = row.validated_against_snapshot,
                k.status = 'accepted'
            WITH k, row
            // Rebuild this KPI's edges from scratch: a formula that stopped reading a table
            // must stop showing an edge to it, or the review screen over-reports forever.
            OPTIONAL MATCH (k)-[old:READS]->(:{_TABLE_LABEL})
            DELETE old
            WITH k, row
            OPTIONAL MATCH (:{_CONCEPT_LABEL})-[oldm:MEASURED_BY]->(k)
            DELETE oldm
            WITH k, row
            CALL (k, row) {{
                UNWIND row.source_tables AS table_name
                // Match on the qualified name where the KPI gave one. Nodes are keyed
                // {{schema, name}}, so a bare match linked a KPI to every schema's table of
                // that name -- harmless while one schema is harvested, a wrong answer to
                // "which KPIs break if this table changes" the moment two are.
                MATCH (t:{_TABLE_LABEL})
                WHERE t.name = table_name
                   OR (t.schema + '.' + t.name) = table_name
                MERGE (k)-[:READS]->(t)
                RETURN count(*) AS _reads
            }}
            WITH k, row, _reads
            CALL (k, row) {{
                MATCH (c:{_CONCEPT_LABEL} {{name: row.concept}})
                MERGE (c)-[:MEASURED_BY]->(k)
                RETURN count(*) AS _measures
            }}
            RETURN count(*) AS _done
            """,
            rows=rows,
        )
        tx.run(
            f"MATCH (k:{KPI_LABEL}) WHERE NOT k.identifier IN $keep DETACH DELETE k",
            keep=[row["identifier"] for row in rows],
        )
        # `Concept.metrics` is derived from the mirror, so a concept reaches its KPIs by
        # traversal and by field alike, and the two cannot disagree. Runs after the prune --
        # the first version ran before it and a deleted KPI stayed on its concept's list,
        # which the live test caught -- and clears a concept that lost its last edge.
        # Only concepts this mirror can affect: the ones a KPI names, plus any that still
        # carry a metrics list. Rewriting every concept in the graph nightly touched nodes no
        # KPI has ever mentioned -- write amplification on a shared store, and a lock on
        # concepts another writer may be enriching at the same moment.
        tx.run(
            f"""
            MATCH (c:{_CONCEPT_LABEL})
            WHERE c.name IN $named OR (c.metrics IS NOT NULL AND size(c.metrics) > 0)
            OPTIONAL MATCH (c)-[:MEASURED_BY]->(k:{KPI_LABEL})
            WITH c, [x IN collect(k.identifier) WHERE x IS NOT NULL] AS ids
            SET c.metrics = ids
            """,
            named=sorted({row["concept"] for row in rows if row["concept"]}),
        )

    # --- internals ------------------------------------------------------------------------

    def _select(self, where: str, params: Tuple[Any, ...]) -> List[KpiDefinition]:
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""SELECT identifier, name, description, sql, grain, business_area,
                           source_tables, result_shape, dialect, parameters, time_window,
                           validated_against_snapshot, concept, subject_area, level, unit,
                           industry_benchmark, active, source, dataset_ids, process_ids,
                           updated_at
                    FROM {self._schema}.kpi_definition {where} ORDER BY identifier""",
                params,
            )
            rows = cursor.fetchall()
        return [row_to_definition(row) for row in rows]
