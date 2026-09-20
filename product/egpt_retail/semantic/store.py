"""K5 -- the semantic layer in Neo4j, and the real read API behind the P0 contract (D11, G20).

What a customer's warehouse holds, expressed as a graph: tables, their columns, the concepts
they instantiate, and the joins between them. Written straight to Neo4j under **its own
labels**, outside the memory substrate, because that substrate cannot express this — its
labels are fixed at ``Episode``, ``Fact`` and ``Entity`` (``cypher.py:80-83``), every write
demands a memory tier and a five-slot scope, and populating a query filter is refused. D11 is
that constraint written down.

This class *is* the P0 contract. ``StubSemanticRead`` and ``SemanticGraphStore`` both satisfy
``SemanticRead``, so Lane R swaps one for the other by changing a construction site and
nothing else. That was the whole point of stubbing a running contract rather than writing a
document, and the cookbook proves it by driving both through the same code.

Three labels, and the prefix is deliberate. ``SemanticTable``, ``SemanticColumn`` and
``SemanticConcept`` cannot collide with memory's fixed three, and cannot collide with K3's
``OntologyConcept`` either — which matters more than it sounds, because the ontology and the
semantic layer hold different things with confusingly similar names. The ontology says what a
Quote *is*, once, shipped. The semantic layer says which of *this customer's* tables is one.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any, Dict, List, Optional, Tuple

from egpt_retail.contracts.semantic import (
    AuthoritativeSource,
    Column,
    Concept,
    JoinEdge,
    JoinPath,
    ProposalStatus,
    SemanticReadError,
    Snapshot,
    Table,
    TableRef,
)
from egpt_retail.semantic.harvest import Harvest
from egpt_retail.semantic.proposals import (
    ProposalKind,
    ProposalStore,
    ReviewState,
    validate_payload,
)

__all__ = [
    "COLUMN_LABEL",
    "CONCEPT_LABEL",
    "TABLE_LABEL",
    "OPERATOR_SOURCE_ID",
    "EmptyHarvestRefused",
    "SchemaOwnedByOtherSource",
    "SemanticGraphStore",
    "canonical_join",
]

TABLE_LABEL = "SemanticTable"
COLUMN_LABEL = "SemanticColumn"
CONCEPT_LABEL = "SemanticConcept"

_SNAPSHOT_LABEL = "SemanticSnapshot"

#: How a cardinality reads from the other end of the edge.
_INVERSE_CARDINALITY = {
    "one-to-many": "many-to-one",
    "many-to-one": "one-to-many",
    "one-to-one": "one-to-one",
    "many-to-many": "many-to-many",
}


class EmptyHarvestRefused(RuntimeError):
    """A harvest with no tables was offered to a graph that has some.

    An empty harvest is what a bad ``--schema``, a revoked grant or a warehouse outage
    mid-query all look like, and ``harvest_schema`` cannot tell them from a genuinely empty
    schema. Writing it would prune every table of that schema -- and every reviewed edge on
    them -- which is the wipe this store's upsert was written to prevent. So it refuses, and
    the operator decides: a schema that is genuinely gone is removed with ``drop_schema``,
    by name, on purpose. A first harvest of a schema the graph has never held is allowed.
    """


class SchemaOwnedByOtherSource(RuntimeError):
    """A harvest was offered for a schema whose tables belong to a different source.

    A table's identity is ``(schema, name)`` alone -- ``source_id`` is a tag that scopes
    reads (#307), not part of the key -- so writing a second source's tables into a claimed
    schema would overwrite the first source's rows and re-stamp their ownership.
    ``write_harvest`` refuses it inside its own transaction. ``owner`` is the other
    ``source_id`` -- ``""`` when the schema holds the operator's own warehouse harvest, which
    carries no source id.
    """

    def __init__(self, schema: str, owner: str) -> None:
        self.schema = schema
        self.owner = owner
        super().__init__(
            f"schema {schema!r} already holds tables from "
            + (
                f"source {owner!r}"
                if owner
                else "the deployment's own warehouse harvest"
            )
            + "; schema names must be unique across data sources"
        )


def canonical_join(edge: JoinEdge) -> JoinEdge:
    """The same join, expressed from the lexicographically smaller table.

    A join is undirected in the graph -- ``find_join_path`` traverses both ways -- so two
    proposals saying ``a->b`` and ``b->a`` with the same predicate are one join, and must
    land on one edge. The cardinality is inverted when the pair is swapped, because
    ``many-to-one`` is a statement about direction; the predicate text is left exactly as
    written, since it reads the same from either end.
    """
    if edge.left_table <= edge.right_table:
        return edge
    return JoinEdge(
        left_table=edge.right_table,
        right_table=edge.left_table,
        on=edge.on,
        cardinality=_INVERSE_CARDINALITY.get(edge.cardinality, edge.cardinality),
    )


#: The ``source_id`` of the operator's own warehouse -- the one nightly-refresh harvests, and
#: what every table written before #306 carries implicitly (no ``source_id`` property at all,
#: which every scoped read below treats as this value via ``coalesce``). No backfill exists
#: because none is needed: the empty string is what "untagged" already means.
OPERATOR_SOURCE_ID = ""


def _visible(include_proposed: bool) -> str:
    """The status predicate every read shares.

    Written once and interpolated, rather than repeated per query, because the rule it encodes
    is the one that must not vary: accepted rows unless a caller asks otherwise (D10, G5). A
    read that forgot the clause would return unreviewed content and look identical.
    """
    return (
        "" if include_proposed else " AND coalesce(n.status, 'accepted') = 'accepted'"
    )


class SemanticGraphStore:
    """Reads and writes the semantic layer in Neo4j. Satisfies ``SemanticRead``.

    The driver is injected so a caller owns its lifetime and a test can point it at a scratch
    database. No embedder: the semantic layer is looked up by name and traversed by edge, not
    searched by meaning — that is the ontology's job (K3), and K10 is what fuses the two.
    """

    def __init__(
        self,
        driver: Any,
        database: str = "neo4j",
        proposals: Optional[ProposalStore] = None,
        source_id: str = OPERATOR_SOURCE_ID,
    ) -> None:
        self._driver = driver
        self._database = database
        # Which data source's tables every table-enumerating *read* below sees (#307).
        # Defaults to the operator's warehouse, so an existing caller -- feasibility, the SQL
        # guard's allow-list, K9's area evidence -- is unchanged by a customer's harvest
        # landing in the same graph: a customer's ``orders`` cannot make an operator KPI
        # look ready, nor turn a bare ``orders`` lookup ambiguous. Writes are not scoped by
        # this; ``write_harvest`` takes its own ``source_id``.
        #
        # What this does *not* scope: the concept side of the graph. ``SemanticConcept``
        # nodes are the shared K3 ontology and carry no ``source_id``, so ``list_concepts``,
        # ``get_concept`` and ``list_business_areas`` answer the same for every scope, and
        # ``authoritative_source`` follows a concept's edge to whichever table it names
        # without checking that table's scope. Nothing routes a source-scoped reader through
        # them today; whoever extends ``build_source_read_guard``'s seam to customer
        # connections must not assume this class's whole read surface is isolated.
        self._source_id = source_id
        # Where ``include_proposed=True`` reads from. Proposals live in Postgres (K7), never
        # in the graph, so without this the flag is a no-op on the real store -- the contract
        # promises pending rows with ``status='proposed'`` and the store would silently return
        # none. The graph wins when both hold a concept: an accepted row is the truth, and a
        # newer proposal about it is a pending revision, not a replacement.
        self._proposals = proposals

    # --- schema ------------------------------------------------------------------------

    def ensure_indexes(self) -> None:
        """Uniqueness on the two things every read looks up by. Idempotent."""
        statements = [
            f"CREATE CONSTRAINT semantic_table_name IF NOT EXISTS "
            f"FOR (t:{TABLE_LABEL}) REQUIRE (t.schema, t.name) IS UNIQUE",
            f"CREATE CONSTRAINT semantic_concept_name IF NOT EXISTS "
            f"FOR (c:{CONCEPT_LABEL}) REQUIRE c.name IS UNIQUE",
            f"CREATE INDEX semantic_column_name IF NOT EXISTS FOR (c:{COLUMN_LABEL}) ON (c.name)",
        ]
        with self._driver.session(database=self._database) as session:
            for statement in statements:
                session.run(statement)

    async def aensure_indexes(self) -> None:
        """Async variant."""
        await asyncio.to_thread(self.ensure_indexes)

    def _resolve_table(self, session: Any, name: str) -> Optional[Tuple[str, str]]:
        """``(schema, table)`` for a bare or qualified name, or ``None`` if unknown.

        The contract passes bare names (``crm_contact``) and the graph keys on
        ``(schema, name)``. A bare name that matches in two schemas is ambiguous, and
        answering with whichever came first would report one schema's table as the other's.
        """
        rows = session.run(
            f"MATCH (t:{TABLE_LABEL}) "
            "WHERE ((t.schema + '.' + t.name) = $name OR t.name = $name) "
            "AND coalesce(t.source_id, '') = $source_id "
            "RETURN t.schema AS schema, t.name AS name ORDER BY t.schema",
            name=name,
            source_id=self._source_id,
        ).data()
        if not rows:
            return None
        if len(rows) > 1:
            raise SemanticReadError(
                f"{name!r} names a table in more than one schema: "
                f"{', '.join(r['schema'] + '.' + r['name'] for r in rows)}. Qualify it."
            )
        return rows[0]["schema"], rows[0]["name"]

    # --- writes ------------------------------------------------------------------------

    def write_harvest(
        self, harvest: Harvest, *, snapshot_version: str = "", source_id: str = ""
    ) -> int:
        """Write a K4 harvest into the graph, returning how many tables were written.

        ``source_id`` (#306) tags every written table with which registered data source
        produced it -- ``""`` (the default) for the operator's own nightly-refresh harvest,
        unchanged from before this parameter existed. **A tag, not part of a table's
        identity**: node uniqueness is still ``(schema, name)`` alone, and #307 deliberately
        left it there -- it scopes *reads* by this tag (see ``SemanticGraphStore.__init__``'s
        ``source_id``) rather than rekeying every ``MERGE`` and edge lookup in this class.
        The cost is that two sources sharing one ``schema`` name would silently overwrite
        each other's tables here, so the write itself refuses, raising
        :class:`SchemaOwnedByOtherSource`, when ``schema`` already holds tables of a different
        source (the operator's own harvest counts as one, with ``source_id=""``). The check
        runs inside the write transaction; see :meth:`_write_harvest_tx`. Schema names are
        therefore unique across the operator's warehouse and every registered source.

        **Upserts and prunes; never wipes.** Tables and columns are merged by identity and
        the ones the harvest no longer has are removed. The first version of this method did
        ``DETACH DELETE`` on both labels and rewrote them, which read as the obviously
        idempotent thing to do -- and destroyed every ``INSTANTIATES``, ``JOINS_TO`` and
        ``AUTHORITATIVE_SOURCE`` edge on every run. A nightly harvest would have erased every
        reviewed mapping every night, and ``applied_at`` in the proposals table would have
        gone on saying they were applied. The P2 verification found it by writing three
        reviewed edges, re-harvesting, and counting zero.

        A table the warehouse dropped takes its edges with it, which is correct: a join to a
        table that no longer exists is not a join, and K6 puts the KPIs that read it into
        review. Everything attached to a surviving table survives.

        **Sampled values do not come across.** The graph holds types and concepts; rows stay
        in the warehouse. A sample is row data -- five real email addresses, in the case of
        ``crm_contact.email`` -- and it belongs in the harvest a proposer reads transiently,
        not in a store that persists and is queried by name. The harvest still carries them.

        Only this class's labels are touched. Memory's ``Episode`` / ``Fact`` / ``Entity``
        rows and K3's ``OntologyConcept`` nodes are untouched by construction rather than
        by care.

        **Column-name collisions come across from the harvest.** A column carries
        ``collides_with``, the other tables sharing its name, so a caller reading one column
        can see the ambiguity without re-deriving it. K4 does the detection; carrying it here
        is what makes it reachable from a read.
        """
        version = snapshot_version or harvest.harvested_at
        collisions = {c.column_name: c for c in harvest.collisions}

        rows: List[Dict[str, Any]] = []
        for table in harvest.tables:
            rows.append(
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
                            "comment": column.comment,
                            "collides_with": [
                                other
                                for other in collisions.get(
                                    column.name, _NO_COLLISION
                                ).tables
                                if other != table.qualified
                            ]
                            if column.name in collisions
                            and collisions[column.name].needs_disambiguation
                            else [],
                        }
                        for column in table.columns
                    ],
                }
            )

        keep_tables = [f"{row['schema']}.{row['name']}" for row in rows]
        keep_columns = [
            f"{row['schema']}.{row['name']}.{col['name']}"
            for row in rows
            for col in row["columns"]
        ]
        with self._driver.session(database=self._database) as session:
            session.execute_write(
                self._write_harvest_tx,
                rows,
                version,
                keep_tables,
                keep_columns,
                harvest,
                source_id,
            )
        return len(rows)

    @staticmethod
    def _write_harvest_tx(
        tx: Any,
        rows: List[Dict[str, Any]],
        version: str,
        keep_tables: List[str],
        keep_columns: List[str],
        harvest: Harvest,
        source_id: str = "",
    ) -> None:
        """The whole write in one transaction, so a failure part-way leaves the graph as it
        was rather than upserted-but-not-pruned, and the guards below read inside the same
        transaction they protect.

        **The ownership guard cannot be a separate read.** A check in its own session, run
        before this transaction, lets two harvests of different sources into one unclaimed
        schema both pass it and then race. So the upsert never re-stamps a table another
        source owns (a foreign row is skipped, not overwritten), and the schema is checked
        for foreign owners *after* the upsert, in this transaction: a concurrent harvest's
        tables are either already committed and visible (the unique ``(schema, name)``
        constraint makes a competing MERGE of the same table wait for the first to commit),
        or the two sets are disjoint and nothing was overwritten. Raising rolls the whole
        write back.

        **Everything here is scoped to ``harvest.schema``.** One graph serves every schema
        a deployment harvests, and a prune that matched every ``SemanticTable`` would remove
        the other schemas' tables and reviewed edges on every run. Review round 2 on the P2
        PR found that; it was the round-1 wipe again, one schema over.
        """
        schema = harvest.schema
        if not rows:
            # `WHERE NOT x IN []` is true for every node: an empty keep-list prunes
            # everything in the schema. Refuse unless the graph has never held it.
            held = tx.run(
                f"MATCH (t:{TABLE_LABEL} {{schema: $schema}}) RETURN count(t) AS n",
                schema=schema,
            ).single()
            if held and held["n"]:
                raise EmptyHarvestRefused(
                    f"the harvest of {schema!r} has no tables and the graph holds "
                    f"{held['n']} for that schema. Writing it would remove every one of "
                    "them and every reviewed edge on them. Check the schema name and the "
                    "warehouse; to remove the schema on purpose, use drop_schema()."
                )
        tx.run(
            f"""
            UNWIND $rows AS row
            MERGE (t:{TABLE_LABEL} {{schema: row.schema, name: row.name}})
              ON CREATE SET t.source_id = $source_id
            WITH t, row
            WHERE coalesce(t.source_id, '') = $source_id
            SET t.row_count = row.row_count,
                t.comment = row.comment,
                t.snapshot_version = $version,
                t.source_id = $source_id,
                t.status = 'accepted'
            WITH t, row
            UNWIND row.columns AS col
            MERGE (c:{COLUMN_LABEL} {{name: col.name}})-[:OF_TABLE]->(t)
            SET c.data_type = col.data_type,
                c.nullable = col.nullable,
                c.is_key = col.is_key,
                c.comment = col.comment,
                c.collides_with = col.collides_with,
                c.status = 'accepted'
            """,
            rows=rows,
            version=version,
            source_id=source_id,
        )
        owner = SemanticGraphStore._foreign_owner(tx, schema, source_id)
        if owner is not None:
            raise SchemaOwnedByOtherSource(schema, owner)
        # Prune what the warehouse no longer has. Columns first, so a column dropped from
        # a surviving table goes; then tables, taking their remaining columns and edges.
        # ``coalesce(t.source_id, '') = $source_id`` is belt-and-suspenders: the ownership
        # check above has already refused a schema claimed by a different source, so this
        # should never actually exclude a row -- it is what would stop two sources pruning
        # each other if that check were ever weakened.
        tx.run(
            f"""
            MATCH (c:{COLUMN_LABEL})-[:OF_TABLE]->(t:{TABLE_LABEL} {{schema: $schema}})
            WHERE NOT (t.schema + '.' + t.name + '.' + c.name) IN $keep
              AND coalesce(t.source_id, '') = $source_id
            DETACH DELETE c
            """,
            schema=schema,
            keep=keep_columns,
            source_id=source_id,
        )
        tx.run(
            f"""
            MATCH (t:{TABLE_LABEL} {{schema: $schema}})
            WHERE NOT (t.schema + '.' + t.name) IN $keep
              AND coalesce(t.source_id, '') = $source_id
            OPTIONAL MATCH (c:{COLUMN_LABEL})-[:OF_TABLE]->(t)
            DETACH DELETE c, t
            """,
            schema=schema,
            keep=keep_tables,
            source_id=source_id,
        )
        if source_id:
            # Everything below is the operator's nightly refresh, not part of a self-serve
            # harvest:
            #
            # - The two whole-graph column sweeps are housekeeping, and an orphaned column has
            #   no schema of its own to scope them by, so on every on-demand customer harvest
            #   they would scan every column of every customer. The nightly refresh is the
            #   periodic sweep.
            # - The singleton Snapshot node is the *deployment's* last-harvest pointer -- the
            #   operator warehouse's, per the empty-string default every caller before #306
            #   used. A self-serve source's harvest must not overwrite it: doing so would
            #   make GET /semantic/snapshot report a customer's own database name and
            #   timestamp in place of the operator's.
            return
        # A column that lost its table some other way has no meaning; do not keep it.
        tx.run(f"MATCH (c:{COLUMN_LABEL}) WHERE NOT (c)-[:OF_TABLE]->() DELETE c")
        # Any lingering row values from before this rule existed.
        tx.run(f"MATCH (c:{COLUMN_LABEL}) REMOVE c.sampled_values")
        tx.run(
            f"MERGE (s:{_SNAPSHOT_LABEL} {{singleton: true}}) "
            "SET s.version = $version, s.harvested_at = $harvested_at, s.source = $source",
            version=version,
            harvested_at=harvest.harvested_at,
            source=harvest.source,
        )

    def drop_schema(self, schema: str) -> int:
        """Remove one schema's tables, columns and the reviewed edges on them. Returns the
        table count removed.

        The deliberate counterpart to ``write_harvest`` refusing an empty harvest: removal
        of a schema is an operator's decision, made by name, and never a side effect of a
        harvest that happened to return nothing.
        """
        with self._driver.session(database=self._database) as session:
            record = session.run(
                f"""
                MATCH (t:{TABLE_LABEL} {{schema: $schema}})
                OPTIONAL MATCH (c:{COLUMN_LABEL})-[:OF_TABLE]->(t)
                WITH collect(DISTINCT t) AS tables, collect(DISTINCT c) AS columns
                FOREACH (c IN columns | DETACH DELETE c)
                FOREACH (t IN tables | DETACH DELETE t)
                RETURN size(tables) AS n
                """,
                schema=schema,
            ).single()
        return int(record["n"]) if record else 0

    async def adrop_schema(self, schema: str) -> int:
        """Async variant."""
        return await asyncio.to_thread(self.drop_schema, schema)

    async def awrite_harvest(
        self, harvest: Harvest, *, snapshot_version: str = "", source_id: str = ""
    ) -> int:
        """Async variant."""
        return await asyncio.to_thread(
            self.write_harvest,
            harvest,
            snapshot_version=snapshot_version,
            source_id=source_id,
        )

    @staticmethod
    def _foreign_owner(runner: Any, schema: str, source_id: str) -> Optional[str]:
        """The other ``source_id`` holding tables in ``schema``, or ``None``. ``runner`` is
        anything with ``.run`` -- a session for the public read, the write transaction for
        the guard -- so the two cannot drift apart."""
        record = runner.run(
            f"""
            MATCH (t:{TABLE_LABEL} {{schema: $schema}})
            WHERE coalesce(t.source_id, '') <> $source_id
            RETURN DISTINCT coalesce(t.source_id, '') AS other
            LIMIT 1
            """,
            schema=schema,
            source_id=source_id,
        ).single()
        return record["other"] if record else None

    def schema_owned_by_other_source(
        self, schema: str, source_id: str
    ) -> Optional[str]:
        """The *other* ``source_id`` already using ``schema`` if one exists, or ``None`` if
        ``schema`` is unclaimed or already belongs to ``source_id`` itself. ``""`` means the
        operator's own harvest (or a table written before #306 tagged sources).

        A read for callers that want to know up front -- a UI, a cookbook. It is **not** the
        guard: :meth:`write_harvest` enforces the same rule inside its own transaction and
        raises :class:`SchemaOwnedByOtherSource`, because a check made in a separate session
        cannot stop two concurrent harvests from both passing it.
        """
        with self._driver.session(database=self._database) as session:
            return self._foreign_owner(session, schema, source_id)

    async def aschema_owned_by_other_source(
        self, schema: str, source_id: str
    ) -> Optional[str]:
        """Async variant."""
        return await asyncio.to_thread(
            self.schema_owned_by_other_source, schema, source_id
        )

    def write_concept(
        self,
        concept: Concept,
        tables: Tuple[str, ...] = (),
        *,
        proposal_id: str = "",
    ) -> None:
        """Write one enriched concept and the tables that instantiate it.

        Everything on a ``Concept`` — synonyms, the scoping filter, personal-data fields,
        compliance tags — arrives through an accepted proposal (D10, K7). This is the write
        that proposal acceptance performs; nothing calls it directly from an agent.

        **Enrichment accumulates.** Lists are unioned with what the node already holds and a
        scalar is replaced only when the incoming value is non-empty. The first version set
        every field unconditionally, and the P3 verification found what that meant: a K8
        enrichment proposal carrying synonyms and nothing else would, on acceptance, erase the
        concept's description and the ``sql_filter`` a person had accepted the week before.
        A reviewer accepting "add these synonyms" was not accepting "and forget the filter".
        Removing an accepted value is a separate decision with no proposal kind yet; until
        there is one, nothing here can take a value away.

        ``metrics`` is not written here at all. It is derived from the KPI registry's mirror
        (K11), which owns it; an enrichment carrying candidate metric names carries them as
        candidates, not as registry identifiers.
        """
        with self._driver.session(database=self._database) as session:
            session.execute_write(self._write_concept_tx, concept, tables, proposal_id)

    @staticmethod
    def _write_concept_tx(
        tx: Any, concept: Concept, tables: Tuple[str, ...], proposal_id: str
    ) -> None:
        """The concept and its INSTANTIATES edges in one transaction.

        They ran as separate auto-committed statements, so a failure between them left the
        concept written and its mappings absent -- a concept that exists and instantiates
        nothing, which reads on every screen as "mapped to no table" rather than as a partial
        write, and which K9 would then count as supporting nothing.
        """
        tx.run(
            f"""
            MERGE (c:{CONCEPT_LABEL} {{name: $name}})
            SET c.description = CASE
                    WHEN $description IS NULL OR $description = ''
                    THEN coalesce(c.description, '') ELSE $description END,
                c.synonyms = coalesce(c.synonyms, []) + [x IN $synonyms WHERE NOT x IN coalesce(c.synonyms, [])],
                c.question_patterns =
                    coalesce(c.question_patterns, []) + [x IN $question_patterns WHERE NOT x IN coalesce(c.question_patterns, [])],
                c.sql_filter = CASE
                    WHEN $sql_filter IS NULL OR $sql_filter = ''
                    THEN coalesce(c.sql_filter, '') ELSE $sql_filter END,
                c.personal_data_fields = coalesce(c.personal_data_fields, []) + [x IN $personal_data_fields WHERE NOT x IN coalesce(c.personal_data_fields, [])],
                c.compliance_tags =
                    coalesce(c.compliance_tags, []) + [x IN $compliance_tags WHERE NOT x IN coalesce(c.compliance_tags, [])],
                c.business_area = CASE
                    WHEN $business_area IS NULL OR $business_area = ''
                    THEN c.business_area ELSE $business_area END,
                c.status = $status,
                c.applied_by_proposal = CASE WHEN $proposal_id = ''
                    THEN c.applied_by_proposal ELSE $proposal_id END
            """,
            proposal_id=proposal_id,
            name=concept.name,
            description=concept.description,
            synonyms=list(concept.synonyms),
            question_patterns=list(concept.question_patterns),
            sql_filter=concept.sql_filter,
            personal_data_fields=list(concept.personal_data_fields),
            compliance_tags=list(concept.compliance_tags),
            business_area=concept.business_area,
            status=concept.status.value,
        )
        for qualified in tables:
            schema, _, name = qualified.partition(".")
            tx.run(
                f"""
                MATCH (t:{TABLE_LABEL} {{schema: $schema, name: $name}})
                MATCH (c:{CONCEPT_LABEL} {{name: $concept}})
                MERGE (t)-[:INSTANTIATES]->(c)
                """,
                schema=schema,
                name=name,
                concept=concept.name,
            )

    async def awrite_concept(
        self,
        concept: Concept,
        tables: Tuple[str, ...] = (),
        *,
        proposal_id: str = "",
    ) -> None:
        """Async variant."""
        await asyncio.to_thread(
            self.write_concept, concept, tables, proposal_id=proposal_id
        )

    def link_table_to_concept(
        self, qualified_table: str, concept: str, *, proposal_id: str = ""
    ) -> None:
        """Record that a table is an instance of a concept, and nothing else (K8's mapping).

        Narrower than ``write_concept`` on purpose. A mapping proposal says only *this table
        is a Contact*; routing it through ``write_concept`` would carry a bare ``Concept``
        alongside it and overwrite the synonyms and filter that enrichment had already put
        there. Mapping and enrichment are separate proposals reviewed separately, so they get
        separate writes.

        The concept must already exist. A mapping onto a concept nobody has defined is a
        mapping onto nothing, and silently creating the node would hide the ordering mistake.
        """
        with self._driver.session(database=self._database) as session:
            schema, _, name = qualified_table.partition(".")
            record = session.run(
                f"""
                MATCH (t:{TABLE_LABEL} {{schema: $schema, name: $name}})
                MATCH (c:{CONCEPT_LABEL} {{name: $concept}})
                MERGE (t)-[m:INSTANTIATES]->(c)
                SET m.applied_by_proposal = CASE WHEN $proposal_id = ''
                    THEN m.applied_by_proposal ELSE $proposal_id END
                RETURN c.name AS linked
                """,
                schema=schema,
                name=name,
                concept=concept,
                proposal_id=proposal_id,
            ).single()
        if record is None:
            raise SemanticReadError(
                f"cannot map {qualified_table!r} to {concept!r}: one of them is not in the "
                "graph. A mapping is an edge between two things that exist."
            )

    async def alink_table_to_concept(
        self, qualified_table: str, concept: str, *, proposal_id: str = ""
    ) -> None:
        """Async variant."""
        await asyncio.to_thread(
            self.link_table_to_concept,
            qualified_table,
            concept,
            proposal_id=proposal_id,
        )

    def write_column_meaning(
        self,
        qualified_table: str,
        column: str,
        *,
        concept: str,
        meaning: str,
        joinable: bool = False,
        proposal_id: str = "",
    ) -> None:
        """Record what one column means and which concept it belongs to (G19).

        The resolution of a collision, one side at a time: the column node gains a concept
        and a meaning, so two tables' ``session_id`` stop being one name. Written onto the
        column rather than the concept because it is a fact about the column -- and because
        the concept's own fields belong to the enrichment agent, which must not find its
        question patterns filling up with column definitions.

        Refuses a column the graph does not hold. A meaning attached to nothing is a claim
        about a warehouse this deployment has never seen.
        """
        with self._driver.session(database=self._database) as session:
            resolved = self._resolve_table(session, qualified_table)
            if resolved is None:
                raise SemanticReadError(
                    f"cannot record a meaning for {qualified_table}.{column}: the table is "
                    "not in the graph."
                )
            known = session.run(
                f"MATCH (c:{CONCEPT_LABEL} {{name: $name}}) RETURN count(c) AS n",
                name=concept,
            ).single()
            if not known or not known["n"]:
                # `Column.concept` is part of the 1.2.0 read surface, so a meaning naming a
                # concept that does not exist publishes a dangling reference through the
                # contract. Enrichment lands the concept first; this applies on retry, which
                # is the same shape as the mapping and authoritative-source writes.
                raise SemanticReadError(
                    f"cannot record a meaning for {qualified_table}.{column}: concept "
                    f"{concept!r} is not in the graph."
                )
            record = session.run(
                f"""
                MATCH (c:{COLUMN_LABEL} {{name: $column}})-[:OF_TABLE]->
                      (t:{TABLE_LABEL} {{schema: $schema, name: $table}})
                SET c.concept = $concept,
                    c.meaning = $meaning,
                    c.joinable = $joinable,
                    c.applied_by_proposal = CASE WHEN $proposal_id = ''
                        THEN c.applied_by_proposal ELSE $proposal_id END
                RETURN c.name AS written
                """,
                schema=resolved[0],
                table=resolved[1],
                column=column,
                concept=concept,
                meaning=meaning,
                joinable=joinable,
                proposal_id=proposal_id,
            ).single()
        if record is None:
            raise SemanticReadError(
                f"cannot record a meaning for {qualified_table}.{column}: the column is not "
                "in the graph. The harvest that reaches it lands first; this applies on retry."
            )

    async def awrite_column_meaning(
        self,
        qualified_table: str,
        column: str,
        *,
        concept: str,
        meaning: str,
        joinable: bool = False,
        proposal_id: str = "",
    ) -> None:
        """Async variant."""
        await asyncio.to_thread(
            self.write_column_meaning,
            qualified_table,
            column,
            concept=concept,
            meaning=meaning,
            joinable=joinable,
            proposal_id=proposal_id,
        )

    def write_join(self, edge: JoinEdge, *, proposal_id: str = "") -> None:
        """Record that two tables can be joined, and how.

        A join edge is a claim about the data, not about the schema — a foreign key implies
        one, but the interesting joins across sources have no constraint behind them. So it is
        written explicitly and, like everything else in this layer, comes from a confirmed
        proposal rather than from inference over matching column names. K4's own harvest is why:
        ``analytics_event.session_id`` and ``session_replay_session.session_id`` share a name
        and a type and are **not** joinable, which the schema comment says and no name-matching
        heuristic could.

        The predicate is part of the edge's identity, not a property set on it. Two tables can
        genuinely join two ways -- on an email hash and on a contact id, say, with different
        cardinality and different trust -- and merging on the pair alone would silently
        collapse them, keeping whichever was written last. Two accepted proposals must not
        become one edge; K7's cookbook found exactly that by accepting four joins and getting
        three.
        """
        edge = canonical_join(edge)
        with self._driver.session(database=self._database) as session:
            session.execute_write(self._write_join_tx, edge, proposal_id)

    def _write_join_tx(self, tx: Any, edge: JoinEdge, proposal_id: str) -> None:
        """Resolve both ends and write, in one transaction, so a prune landing between
        the two cannot make the write match nothing."""
        left = self._resolve_table(tx, edge.left_table)
        right = self._resolve_table(tx, edge.right_table)
        missing = [
            name
            for name, found in ((edge.left_table, left), (edge.right_table, right))
            if found is None
        ]
        if missing:
            raise SemanticReadError(
                f"cannot record a join: {', '.join(repr(m) for m in missing)} not in the "
                "graph. A join is an edge between two tables that exist; a MATCH that "
                "found nothing would have written nothing and reported success."
            )
        assert left is not None and right is not None
        record = tx.run(
            f"""
            MATCH (a:{TABLE_LABEL} {{schema: $ls, name: $ln}})
            MATCH (b:{TABLE_LABEL} {{schema: $rs, name: $rn}})
            MERGE (a)-[j:JOINS_TO {{on: $on}}]->(b)
            SET j.cardinality = $cardinality, j.status = 'accepted',
                j.applied_by_proposal = CASE WHEN $proposal_id = ''
                    THEN j.applied_by_proposal ELSE $proposal_id END
            RETURN j.on AS written
            """,
            ls=left[0],
            ln=left[1],
            rs=right[0],
            rn=right[1],
            on=edge.on,
            cardinality=edge.cardinality,
            proposal_id=proposal_id,
        ).single()
        if record is None:  # pragma: no cover -- both endpoints were just resolved
            raise SemanticReadError("the join write matched nothing after resolution")

    async def awrite_join(self, edge: JoinEdge, *, proposal_id: str = "") -> None:
        """Async variant."""
        await asyncio.to_thread(self.write_join, edge, proposal_id=proposal_id)

    def write_authoritative_source(
        self, source: AuthoritativeSource, *, proposal_id: str = ""
    ) -> None:
        """Record which table answers for a concept, and what it beat (G20)."""
        with self._driver.session(database=self._database) as session:
            session.execute_write(self._write_authoritative_tx, source, proposal_id)

    def _write_authoritative_tx(
        self, tx: Any, source: AuthoritativeSource, proposal_id: str
    ) -> None:
        table = self._resolve_table(tx, source.table)
        if table is None:
            raise SemanticReadError(
                f"cannot set {source.table!r} as authoritative for {source.concept!r}: "
                "the table is not in the graph."
            )
        record = tx.run(
            f"""
            MATCH (c:{CONCEPT_LABEL} {{name: $concept}})
            MATCH (t:{TABLE_LABEL} {{schema: $schema, name: $table}})
            MERGE (c)-[a:AUTHORITATIVE_SOURCE]->(t)
            SET a.alternatives = $alternatives,
                a.rationale = $rationale,
                a.status = $status,
                a.applied_by_proposal = CASE WHEN $proposal_id = ''
                    THEN a.applied_by_proposal ELSE $proposal_id END
            RETURN t.name AS written
            """,
            concept=source.concept,
            schema=table[0],
            table=table[1],
            alternatives=list(source.alternatives),
            rationale=source.rationale,
            status=source.status.value,
            proposal_id=proposal_id,
        ).single()
        if record is None:
            raise SemanticReadError(
                f"cannot set an authoritative source for {source.concept!r}: the concept is "
                "not in the graph. Enrichment lands first; this proposal will apply on retry."
            )

    async def awrite_authoritative_source(
        self, source: AuthoritativeSource, *, proposal_id: str = ""
    ) -> None:
        """Async variant."""
        await asyncio.to_thread(
            self.write_authoritative_source, source, proposal_id=proposal_id
        )

    def mapped_concepts(self) -> Dict[str, List[str]]:
        """Per accepted concept, the accepted tables mapped to it -- the graph half of K9.

        Deliberately says nothing about areas. Which area a concept is in is a fact about the
        shipped ontology, not something a deployment's graph node should be trusted to carry:
        the node's ``business_area`` arrives through an enrichment proposal an agent drafted
        and may have left empty, and K9 read it anyway, so a mapped concept with a blank area
        contributed to nothing. The ontology already knows; ask it.
        """
        with self._driver.session(database=self._database) as session:
            rows = session.run(
                f"""
                MATCH (t:{TABLE_LABEL})-[:INSTANTIATES]->(c:{CONCEPT_LABEL})
                WHERE coalesce(c.status, 'accepted') = 'accepted'
                  AND coalesce(t.status, 'accepted') = 'accepted'
                  AND coalesce(t.source_id, '') = $source_id
                RETURN c.name AS concept, t.schema + '.' + t.name AS table
                ORDER BY concept, table
                """,
                source_id=self._source_id,
            ).data()
        mapped: Dict[str, List[str]] = {}
        for row in rows:
            mapped.setdefault(row["concept"], []).append(row["table"])
        return mapped

    async def amapped_concepts(self) -> Dict[str, List[str]]:
        """Async variant."""
        return await asyncio.to_thread(self.mapped_concepts)

    def column_index(self) -> Dict[str, List[str]]:
        """Every accepted table's accepted column names, in one query.

        Retrieval asks "which tables have a column matching these terms", which is one
        question about the whole graph and was being answered with one round trip per table
        -- on the live query path, not at harvest. Round 1 on the P3 PR caught it.

        Keyed by qualified name, so a caller that harvested two schemas does not silently
        merge their tables.
        """
        with self._driver.session(database=self._database) as session:
            rows = session.run(
                f"""
                MATCH (c:{COLUMN_LABEL})-[:OF_TABLE]->(t:{TABLE_LABEL})
                WHERE coalesce(t.status, 'accepted') = 'accepted'
                  AND coalesce(c.status, 'accepted') = 'accepted'
                  AND coalesce(t.source_id, '') = $source_id
                RETURN t.schema + '.' + t.name AS table, collect(c.name) AS columns
                """,
                source_id=self._source_id,
            ).data()
        return {row["table"]: sorted(row["columns"]) for row in rows}

    async def acolumn_index(self) -> Dict[str, List[str]]:
        """Async variant."""
        return await asyncio.to_thread(self.column_index)

    def unjoinable_oracle(self) -> Any:
        """A ``(left, right) -> bool`` callable: has review ruled this pair out?

        True only where an accepted column meaning says so -- the two tables share a column
        name and K8's mapping review marked it ``joinable = false``, which is the
        ``session_id``-in-two-id-spaces case K4's collision detection surfaces. Everything
        else answers False, including a pair nobody has ruled on: absence of a proposal is
        not a prohibition, and a formula stating its own ``ON`` clause over such a pair is an
        ordinary hand-written join.

        Read once and memoised: feasibility over a registry asks about the same pairs
        repeatedly, and this is one query for the whole graph rather than one per pair.
        """
        with self._driver.session(database=self._database) as session:
            rows = session.run(
                f"""
                MATCH (c:{COLUMN_LABEL})-[:OF_TABLE]->(t:{TABLE_LABEL})
                WHERE c.joinable = false AND c.meaning IS NOT NULL
                  AND coalesce(t.source_id, '') = $source_id
                RETURN c.name AS column, collect(DISTINCT t.name) AS tables
                """,
                source_id=self._source_id,
            ).data()
        ruled_out: set = set()
        for row in rows:
            names = sorted(row["tables"])
            for index, left in enumerate(names):
                for right in names[index + 1 :]:
                    ruled_out.add((left.lower(), right.lower()))

        def known_unjoinable(left: str, right: str) -> bool:
            key = (left.lower(), right.lower())
            return (key if key[0] <= key[1] else (key[1], key[0])) in ruled_out

        return known_unjoinable

    async def aunjoinable_oracle(self) -> Any:
        """Async variant. One graph read, off the event loop."""
        return await asyncio.to_thread(self.unjoinable_oracle)

    def business_area_evidence(self) -> Dict[str, Dict[str, List[str]]]:
        """Per area, the accepted concepts that have a table mapped to them, and those tables.

        The graph query behind K9. An area is *supported* when some concept in it has at
        least one ``INSTANTIATES`` edge from a table -- confirmed, because only an accepted
        proposal writes that edge (D10). A concept nobody mapped contributes nothing however
        well described it is: there is no data behind it, and marking its area supported would
        promise a customer answers this deployment cannot compute.

        Evidence comes back with the answer rather than separately, because "inventory is
        supported" is not actionable and "inventory is supported, via StockLevel on
        silver.stock_level" is.
        """
        with self._driver.session(database=self._database) as session:
            rows = session.run(
                f"""
                MATCH (t:{TABLE_LABEL})-[:INSTANTIATES]->(c:{CONCEPT_LABEL})
                WHERE c.business_area IS NOT NULL
                  AND coalesce(c.status, 'accepted') = 'accepted'
                  AND coalesce(t.status, 'accepted') = 'accepted'
                  AND coalesce(t.source_id, '') = $source_id
                RETURN c.business_area AS area, c.name AS concept,
                       t.schema + '.' + t.name AS table
                ORDER BY area, concept, table
                """,
                source_id=self._source_id,
            ).data()
        evidence: Dict[str, Dict[str, List[str]]] = {}
        for row in rows:
            bucket = evidence.setdefault(row["area"], {"concepts": [], "tables": []})
            if row["concept"] not in bucket["concepts"]:
                bucket["concepts"].append(row["concept"])
            if row["table"] not in bucket["tables"]:
                bucket["tables"].append(row["table"])
        return evidence

    async def abusiness_area_evidence(self) -> Dict[str, Dict[str, List[str]]]:
        """Async variant."""
        return await asyncio.to_thread(self.business_area_evidence)

    # --- reads: the SemanticRead contract ----------------------------------------------

    def _pending(self, kind: ProposalKind) -> Tuple[Any, ...]:
        """Proposals of one kind awaiting review, or nothing when no store was given.

        What ``include_proposed=True`` overlays on the real store, and what it does not:
        pending **enrichments** appear through ``get_concept`` / ``list_concepts`` /
        ``list_business_areas``, and pending **mappings** through ``Table.concepts``. Pending
        joins are not traversed by ``find_join_path`` -- that method has no
        ``include_proposed`` in the contract, on purpose: a path through an unreviewed join
        is a query plan nobody approved, and a planner cannot be handed one. A review screen
        lists pending joins from the ``ProposalStore`` directly.
        """
        if self._proposals is None:
            return ()
        return self._proposals.list(state=ReviewState.PENDING, kind=kind)

    def _pending_enrichments(self) -> Tuple[Any, ...]:
        return self._pending(ProposalKind.ENRICHMENT)

    def snapshot(self) -> Snapshot:
        with self._driver.session(database=self._database) as session:
            record = session.run(
                f"MATCH (s:{_SNAPSHOT_LABEL}) RETURN s.version AS v, "
                "s.harvested_at AS h, s.source AS src LIMIT 1"
            ).single()
        if record is None:
            raise SemanticReadError(
                "the semantic layer holds no snapshot, so no harvest has been written. "
                "This is an unpopulated graph, not an empty warehouse."
            )
        return Snapshot(
            version=record["v"] or "unversioned",
            harvested_at=record["h"] or "unknown",
            source=record["src"] or "warehouse",
        )

    async def asnapshot(self) -> Snapshot:
        """Async variant."""
        return await asyncio.to_thread(self.snapshot)

    def source_snapshot_version(self) -> Optional[str]:
        """The newest snapshot version any of this scope's tables was written at, or ``None``
        when the scope holds no tables.

        What stands in for :meth:`snapshot` when this store is scoped to a self-serve source
        (#307): the graph's one ``SemanticSnapshot`` node is the *operator's* last-harvest
        pointer and a source's harvest deliberately never writes it (``write_harvest``), so a
        source's own version can only be read back off the tables it tagged.
        """
        with self._driver.session(database=self._database) as session:
            record = session.run(
                f"MATCH (t:{TABLE_LABEL}) WHERE coalesce(t.source_id, '') = $source_id "
                "RETURN max(t.snapshot_version) AS v",
                source_id=self._source_id,
            ).single()
        return record["v"] if record and record["v"] else None

    async def asource_snapshot_version(self) -> Optional[str]:
        """Async variant."""
        return await asyncio.to_thread(self.source_snapshot_version)

    def list_tables(self, *, include_proposed: bool = False) -> Tuple[TableRef, ...]:
        with self._driver.session(database=self._database) as session:
            result = session.run(
                f"MATCH (n:{TABLE_LABEL}) WHERE true{_visible(include_proposed)} "
                "AND coalesce(n.source_id, '') = $source_id "
                "RETURN n.schema AS schema, n.name AS name, n.row_count AS row_count "
                "ORDER BY n.name",
                source_id=self._source_id,
            )
            return tuple(
                TableRef(name=r["name"], schema=r["schema"], row_count=r["row_count"])
                for r in result
            )

    async def alist_tables(
        self, *, include_proposed: bool = False
    ) -> Tuple[TableRef, ...]:
        """Async variant."""
        return await asyncio.to_thread(
            self.list_tables, include_proposed=include_proposed
        )

    def get_table(
        self, name: str, *, include_proposed: bool = False
    ) -> Optional[Table]:
        with self._driver.session(database=self._database) as session:
            resolved = self._resolve_table(session, name)
            if resolved is None:
                return None
            schema, bare = resolved
            record = session.run(
                f"MATCH (n:{TABLE_LABEL} {{schema: $schema, name: $name}}) "
                f"WHERE true{_visible(include_proposed)} "
                "RETURN n.schema AS schema, n.name AS name, n.row_count AS row_count, "
                "n.comment AS comment",
                schema=schema,
                name=bare,
            ).single()
            if record is None:
                return None
            # A proposed column inside an accepted table is the case that leaks: the caller
            # asked for a table it is entitled to see, so the filter has to reach the columns
            # too, not only the table.
            columns = session.run(
                f"MATCH (c:{COLUMN_LABEL})-[:OF_TABLE]->"
                f"(:{TABLE_LABEL} {{schema: $schema, name: $name}}) "
                f"WHERE true{_visible(include_proposed).replace('n.', 'c.')} "
                "RETURN c.name AS name, c.data_type AS data_type, c.nullable AS nullable, "
                "c.is_key AS is_key, c.comment AS comment, c.sampled_values AS sampled_values, "
                "c.collides_with AS collides_with, c.status AS status, "
                "c.concept AS concept, c.meaning AS meaning, c.joinable AS joinable "
                "ORDER BY c.name",
                schema=schema,
                name=bare,
            )
            concepts = session.run(
                f"MATCH (:{TABLE_LABEL} {{schema: $schema, name: $name}})"
                f"-[:INSTANTIATES]->(c:{CONCEPT_LABEL}) "
                "RETURN c.name AS name, c.personal_data_fields AS pdf ORDER BY c.name",
                schema=schema,
                name=bare,
            ).data()
            # A column names personal data when a concept this table instantiates says so.
            # Accepted enrichment lists them as `table.column` or `schema.table.column`; the
            # flag was previously hardcoded False with a comment promising enrichment would
            # set it, and nothing ever did.
            # Only a field qualified with *this* table counts. An unqualified entry --
            # `email` rather than `crm_contact.email` -- names a column on some table and
            # cannot say which, so it tags nothing: the first version matched it against
            # every table's same-named column, over-tagging `analytics_event.email` because
            # a different table's concept declared one. Over-tagging is not a leak, but a
            # compliance flag that is wrong in the safe direction still teaches a reviewer
            # to stop reading it.
            personal = {
                field.split(".")[-1]
                for row in concepts
                for field in (row["pdf"] or ())
                if field.split(".")[-2:-1] == [bare]
            }
            return Table(
                ref=TableRef(
                    name=record["name"],
                    schema=record["schema"],
                    row_count=record["row_count"],
                ),
                columns=tuple(
                    Column(
                        name=c["name"],
                        data_type=c["data_type"],
                        nullable=bool(c["nullable"]),
                        is_key=bool(c["is_key"]),
                        # A column that names personal data is one whose concept declared it;
                        # the harvest cannot know, so this stays false until enrichment says so.
                        is_personal_data=c["name"] in personal,
                        description=c["comment"] or "",
                        sampled_values=tuple(c["sampled_values"] or ()),
                        status=ProposalStatus(c["status"] or "accepted"),
                        concept=c["concept"],
                        meaning=c["meaning"] or "",
                        joinable=bool(c["joinable"]),
                    )
                    for c in columns
                ),
                description=record["comment"] or "",
                concepts=tuple(
                    sorted(
                        {r["name"] for r in concepts}
                        | (
                            {
                                p.payload["concept"]
                                for p in self._pending(ProposalKind.MAPPING)
                                if p.payload.get("table", "").split(".")[-1] == bare
                            }
                            if include_proposed
                            else set()
                        )
                    )
                ),
            )

    async def aget_table(
        self, name: str, *, include_proposed: bool = False
    ) -> Optional[Table]:
        """Async variant."""
        return await asyncio.to_thread(
            self.get_table, name, include_proposed=include_proposed
        )

    def list_concepts(self, *, include_proposed: bool = False) -> Tuple[str, ...]:
        with self._driver.session(database=self._database) as session:
            result = session.run(
                f"MATCH (n:{CONCEPT_LABEL}) WHERE true{_visible(include_proposed)} "
                "RETURN n.name AS name ORDER BY name"
            )
            names = {r["name"] for r in result}
        if include_proposed:
            names.update(p.target for p in self._pending_enrichments())
        return tuple(sorted(names))

    async def alist_concepts(
        self, *, include_proposed: bool = False
    ) -> Tuple[str, ...]:
        """Async variant."""
        return await asyncio.to_thread(
            self.list_concepts, include_proposed=include_proposed
        )

    def get_concept(
        self, name: str, *, include_proposed: bool = False
    ) -> Optional[Concept]:
        with self._driver.session(database=self._database) as session:
            record = session.run(
                f"MATCH (n:{CONCEPT_LABEL} {{name: $name}}) WHERE true{_visible(include_proposed)} "
                "RETURN n",
                name=name,
            ).single()
        if record is None:
            if include_proposed:
                for pending in self._pending_enrichments():
                    if pending.target == name:
                        built = validate_payload(pending.kind, pending.payload)
                        return replace(built, status=ProposalStatus.PROPOSED)
            return None
        node = record["n"]
        return Concept(
            name=node["name"],
            description=node.get("description") or "",
            synonyms=tuple(node.get("synonyms") or ()),
            question_patterns=tuple(node.get("question_patterns") or ()),
            sql_filter=node.get("sql_filter") or "",
            personal_data_fields=tuple(node.get("personal_data_fields") or ()),
            compliance_tags=tuple(node.get("compliance_tags") or ()),
            metrics=tuple(node.get("metrics") or ()),
            business_area=node.get("business_area"),
            status=ProposalStatus(node.get("status") or "accepted"),
        )

    async def aget_concept(
        self, name: str, *, include_proposed: bool = False
    ) -> Optional[Concept]:
        """Async variant."""
        return await asyncio.to_thread(
            self.get_concept, name, include_proposed=include_proposed
        )

    def find_join_path(self, from_table: str, to_table: str) -> Optional[JoinPath]:
        """The shortest declared join path, or ``None`` when the graph holds none.

        ``None`` means "there is no path" and is a real answer. An endpoint the layer does not
        expose raises instead: "no path from a table I have never heard of" would report a typo
        as a fact about the data.

        Scoped end to end, not only at the endpoints: every table on the path, intermediate
        hops included, must belong to this store's ``source_id``. ``write_join`` already
        resolves both ends through the scoped ``_resolve_table``, so today a ``JOINS_TO`` edge
        cannot join two scopes -- but that invariant lives in the write path, and a read that
        relied on it alone would turn a future write-side slip into a cross-source leak of a
        foreign table's name. A path that exists only by crossing scopes is no path here.
        """
        with self._driver.session(database=self._database) as session:
            ends = []
            for endpoint in (from_table, to_table):
                resolved = self._resolve_table(session, endpoint)
                if resolved is None:
                    known = session.run(
                        f"MATCH (t:{TABLE_LABEL}) "
                        "WHERE coalesce(t.source_id, '') = $source_id "
                        "RETURN t.name AS name ORDER BY name",
                        source_id=self._source_id,
                    )
                    raise SemanticReadError(
                        f"find_join_path: {endpoint!r} is not a table the semantic layer "
                        f"exposes. Known tables: {', '.join(r['name'] for r in known)}."
                    )
                ends.append(resolved)
            (ls, left), (rs, right) = ends
            if (ls, left) == (rs, right):
                return JoinPath(from_table=left, to_table=right, edges=())
            record = session.run(
                f"""
                MATCH (a:{TABLE_LABEL} {{schema: $ls, name: $left}}),
                      (b:{TABLE_LABEL} {{schema: $rs, name: $right}})
                MATCH path = shortestPath((a)-[:JOINS_TO*..6]-(b))
                WHERE all(n IN nodes(path) WHERE coalesce(n.source_id, '') = $source_id)
                RETURN [r IN relationships(path) |
                        {{on: r.on, cardinality: r.cardinality,
                          left: startNode(r).name, right: endNode(r).name}}] AS hops
                """,
                ls=ls,
                left=left,
                rs=rs,
                right=right,
                source_id=self._source_id,
            ).single()
        if record is None:
            return None
        return JoinPath(
            from_table=left,
            to_table=right,
            edges=tuple(
                JoinEdge(
                    left_table=hop["left"],
                    right_table=hop["right"],
                    on=hop["on"],
                    cardinality=hop["cardinality"] or "many-to-one",
                )
                for hop in record["hops"]
            ),
        )

    async def afind_join_path(
        self, from_table: str, to_table: str
    ) -> Optional[JoinPath]:
        """Async variant."""
        return await asyncio.to_thread(self.find_join_path, from_table, to_table)

    def authoritative_source(self, concept: str) -> Optional[AuthoritativeSource]:
        """Which table answers for this concept, or ``None`` if nobody has decided (G20).

        **Not scoped by ``source_id``**, unlike the table-enumerating reads. A concept belongs
        to the shared ontology, not to a data source, and the edge is followed without a check
        on the table's scope, so the answer can name a table the store's own scope cannot
        otherwise see. No source-scoped reader calls this today; a caller that adds one must
        filter on the returned table itself.
        """
        with self._driver.session(database=self._database) as session:
            record = session.run(
                f"""
                MATCH (c:{CONCEPT_LABEL} {{name: $concept}})-[a:AUTHORITATIVE_SOURCE]->(t:{TABLE_LABEL})
                RETURN t.name AS table, a.alternatives AS alternatives,
                       a.rationale AS rationale, a.status AS status
                """,
                concept=concept,
            ).single()
        if record is None:
            return None
        return AuthoritativeSource(
            concept=concept,
            table=record["table"],
            alternatives=tuple(record["alternatives"] or ()),
            rationale=record["rationale"] or "",
            status=ProposalStatus(record["status"] or "accepted"),
        )

    async def aauthoritative_source(
        self, concept: str
    ) -> Optional[AuthoritativeSource]:
        """Async variant."""
        return await asyncio.to_thread(self.authoritative_source, concept)

    def list_business_areas(self, *, include_proposed: bool = False) -> Tuple[str, ...]:
        """Every area **named by** an accepted concept, whether or not a table backs it.

        Not the same question as "which areas can this deployment answer about", and the
        difference is K9's whole subject: a concept can carry ``business_area = 'inventory'``
        with no table mapped to it at all, and this method still returns ``inventory``. What
        it reports is which areas the *ontology* covers here, which is what a configuration
        screen needs before any source is connected.

        For the stricter question -- areas with a confirmed mapping behind them -- use
        :meth:`business_area_evidence`. This docstring previously claimed to answer that one
        and did not, which is the kind of error that survives review because the method name
        reads correct either way.
        """
        with self._driver.session(database=self._database) as session:
            result = session.run(
                f"MATCH (n:{CONCEPT_LABEL}) WHERE n.business_area IS NOT NULL"
                f"{_visible(include_proposed)} "
                "RETURN DISTINCT n.business_area AS area ORDER BY area"
            )
            areas = {r["area"] for r in result}
        if include_proposed:
            for pending in self._pending_enrichments():
                area = pending.payload.get("business_area")
                if area:
                    areas.add(area)
        return tuple(sorted(areas))

    async def alist_business_areas(
        self, *, include_proposed: bool = False
    ) -> Tuple[str, ...]:
        """Async variant."""
        return await asyncio.to_thread(
            self.list_business_areas, include_proposed=include_proposed
        )


class _NoCollision:
    tables: Tuple[str, ...] = ()


_NO_COLLISION = _NoCollision()
