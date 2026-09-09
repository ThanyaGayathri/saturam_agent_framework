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
    "EmptyHarvestRefused",
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
    ) -> None:
        self._driver = driver
        self._database = database
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
            "WHERE (t.schema + '.' + t.name) = $name OR t.name = $name "
            "RETURN t.schema AS schema, t.name AS name ORDER BY t.schema",
            name=name,
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

    def write_harvest(self, harvest: Harvest, *, snapshot_version: str = "") -> int:
        """Write a K4 harvest into the graph, returning how many tables were written.

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
    ) -> None:
        """The whole write in one transaction, so a failure part-way leaves the graph as it
        was rather than upserted-but-not-pruned, and the guard below reads inside the same
        transaction it protects.

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
            SET t.row_count = row.row_count,
                t.comment = row.comment,
                t.snapshot_version = $version,
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
        )
        # Prune what the warehouse no longer has. Columns first, so a column dropped from
        # a surviving table goes; then tables, taking their remaining columns and edges.
        tx.run(
            f"""
            MATCH (c:{COLUMN_LABEL})-[:OF_TABLE]->(t:{TABLE_LABEL} {{schema: $schema}})
            WHERE NOT (t.schema + '.' + t.name + '.' + c.name) IN $keep
            DETACH DELETE c
            """,
            schema=schema,
            keep=keep_columns,
        )
        tx.run(
            f"""
            MATCH (t:{TABLE_LABEL} {{schema: $schema}})
            WHERE NOT (t.schema + '.' + t.name) IN $keep
            OPTIONAL MATCH (c:{COLUMN_LABEL})-[:OF_TABLE]->(t)
            DETACH DELETE c, t
            """,
            schema=schema,
            keep=keep_tables,
        )
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
        self, harvest: Harvest, *, snapshot_version: str = ""
    ) -> int:
        """Async variant."""
        return await asyncio.to_thread(
            self.write_harvest, harvest, snapshot_version=snapshot_version
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
        """
        with self._driver.session(database=self._database) as session:
            session.run(
                f"""
                MERGE (c:{CONCEPT_LABEL} {{name: $name}})
                SET c.description = $description,
                    c.synonyms = $synonyms,
                    c.question_patterns = $question_patterns,
                    c.sql_filter = $sql_filter,
                    c.personal_data_fields = $personal_data_fields,
                    c.compliance_tags = $compliance_tags,
                    c.metrics = $metrics,
                    c.business_area = $business_area,
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
                metrics=list(concept.metrics),
                business_area=concept.business_area,
                status=concept.status.value,
            )
            for qualified in tables:
                schema, _, name = qualified.partition(".")
                session.run(
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

    def list_tables(self, *, include_proposed: bool = False) -> Tuple[TableRef, ...]:
        with self._driver.session(database=self._database) as session:
            result = session.run(
                f"MATCH (n:{TABLE_LABEL}) WHERE true{_visible(include_proposed)} "
                "RETURN n.schema AS schema, n.name AS name, n.row_count AS row_count "
                "ORDER BY n.name"
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
                "c.collides_with AS collides_with, c.status AS status "
                "ORDER BY c.name",
                schema=schema,
                name=bare,
            )
            concepts = session.run(
                f"MATCH (:{TABLE_LABEL} {{schema: $schema, name: $name}})"
                f"-[:INSTANTIATES]->(c:{CONCEPT_LABEL}) "
                "RETURN c.name AS name ORDER BY c.name",
                schema=schema,
                name=bare,
            )
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
                        is_personal_data=False,
                        description=c["comment"] or "",
                        sampled_values=tuple(c["sampled_values"] or ()),
                        status=ProposalStatus(c["status"] or "accepted"),
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
        """
        with self._driver.session(database=self._database) as session:
            ends = []
            for endpoint in (from_table, to_table):
                resolved = self._resolve_table(session, endpoint)
                if resolved is None:
                    known = session.run(
                        f"MATCH (t:{TABLE_LABEL}) RETURN t.name AS name ORDER BY name"
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
                RETURN [r IN relationships(path) |
                        {{on: r.on, cardinality: r.cardinality,
                          left: startNode(r).name, right: endNode(r).name}}] AS hops
                """,
                ls=ls,
                left=left,
                rs=rs,
                right=right,
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
        """Which table answers for this concept, or ``None`` if nobody has decided (G20)."""
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
        """Areas reachable from confirmed concept mappings. Empty means no source connected."""
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
