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

__all__ = [
    "COLUMN_LABEL",
    "CONCEPT_LABEL",
    "TABLE_LABEL",
    "SemanticGraphStore",
]

TABLE_LABEL = "SemanticTable"
COLUMN_LABEL = "SemanticColumn"
CONCEPT_LABEL = "SemanticConcept"

_SNAPSHOT_LABEL = "SemanticSnapshot"


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

    def __init__(self, driver: Any, database: str = "neo4j") -> None:
        self._driver = driver
        self._database = database

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

    # --- writes ------------------------------------------------------------------------

    def write_harvest(self, harvest: Harvest, *, snapshot_version: str = "") -> int:
        """Write a K4 harvest into the graph, returning how many tables were written.

        Replaces the semantic layer's own labels and nothing else. Memory's ``Episode`` /
        ``Fact`` / ``Entity`` rows and K3's ``OntologyConcept`` nodes are untouched by
        construction rather than by care — the delete names the labels this class owns.

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
                            "sampled_values": list(column.sampled_values),
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

        with self._driver.session(database=self._database) as session:
            session.run(f"MATCH (n:{TABLE_LABEL}) DETACH DELETE n")
            session.run(f"MATCH (n:{COLUMN_LABEL}) DETACH DELETE n")
            session.run(
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
                    c.sampled_values = col.sampled_values,
                    c.collides_with = col.collides_with,
                    c.status = 'accepted'
                """,
                rows=rows,
                version=version,
            )
            session.run(
                f"MERGE (s:{_SNAPSHOT_LABEL} {{singleton: true}}) "
                "SET s.version = $version, s.harvested_at = $harvested_at, s.source = $source",
                version=version,
                harvested_at=harvest.harvested_at,
                source=harvest.source,
            )
        return len(rows)

    async def awrite_harvest(
        self, harvest: Harvest, *, snapshot_version: str = ""
    ) -> int:
        """Async variant."""
        return await asyncio.to_thread(
            self.write_harvest, harvest, snapshot_version=snapshot_version
        )

    def write_concept(self, concept: Concept, tables: Tuple[str, ...] = ()) -> None:
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
                    c.status = $status
                """,
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
        self, concept: Concept, tables: Tuple[str, ...] = ()
    ) -> None:
        """Async variant."""
        await asyncio.to_thread(self.write_concept, concept, tables)

    def link_table_to_concept(self, qualified_table: str, concept: str) -> None:
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
                MERGE (t)-[:INSTANTIATES]->(c)
                RETURN c.name AS linked
                """,
                schema=schema,
                name=name,
                concept=concept,
            ).single()
        if record is None:
            raise SemanticReadError(
                f"cannot map {qualified_table!r} to {concept!r}: one of them is not in the "
                "graph. A mapping is an edge between two things that exist."
            )

    async def alink_table_to_concept(self, qualified_table: str, concept: str) -> None:
        """Async variant."""
        await asyncio.to_thread(self.link_table_to_concept, qualified_table, concept)

    def write_join(self, edge: JoinEdge) -> None:
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
        with self._driver.session(database=self._database) as session:
            session.run(
                f"""
                MATCH (a:{TABLE_LABEL} {{name: $left}})
                MATCH (b:{TABLE_LABEL} {{name: $right}})
                MERGE (a)-[j:JOINS_TO {{on: $on}}]->(b)
                SET j.cardinality = $cardinality, j.status = 'accepted'
                """,
                left=edge.left_table,
                right=edge.right_table,
                on=edge.on,
                cardinality=edge.cardinality,
            )

    async def awrite_join(self, edge: JoinEdge) -> None:
        """Async variant."""
        await asyncio.to_thread(self.write_join, edge)

    def write_authoritative_source(self, source: AuthoritativeSource) -> None:
        """Record which table answers for a concept, and what it beat (G20)."""
        with self._driver.session(database=self._database) as session:
            session.run(
                f"""
                MATCH (c:{CONCEPT_LABEL} {{name: $concept}})
                MATCH (t:{TABLE_LABEL} {{name: $table}})
                MERGE (c)-[a:AUTHORITATIVE_SOURCE]->(t)
                SET a.alternatives = $alternatives,
                    a.rationale = $rationale,
                    a.status = $status
                """,
                concept=source.concept,
                table=source.table,
                alternatives=list(source.alternatives),
                rationale=source.rationale,
                status=source.status.value,
            )

    async def awrite_authoritative_source(self, source: AuthoritativeSource) -> None:
        """Async variant."""
        await asyncio.to_thread(self.write_authoritative_source, source)

    # --- reads: the SemanticRead contract ----------------------------------------------

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
        bare = name.split(".")[-1]
        with self._driver.session(database=self._database) as session:
            record = session.run(
                f"MATCH (n:{TABLE_LABEL} {{name: $name}}) WHERE true{_visible(include_proposed)} "
                "RETURN n.schema AS schema, n.name AS name, n.row_count AS row_count, "
                "n.comment AS comment",
                name=bare,
            ).single()
            if record is None:
                return None
            # A proposed column inside an accepted table is the case that leaks: the caller
            # asked for a table it is entitled to see, so the filter has to reach the columns
            # too, not only the table.
            columns = session.run(
                f"MATCH (c:{COLUMN_LABEL})-[:OF_TABLE]->(:{TABLE_LABEL} {{name: $name}}) "
                f"WHERE true{_visible(include_proposed).replace('n.', 'c.')} "
                "RETURN c.name AS name, c.data_type AS data_type, c.nullable AS nullable, "
                "c.is_key AS is_key, c.comment AS comment, c.sampled_values AS sampled_values, "
                "c.collides_with AS collides_with, c.status AS status "
                "ORDER BY c.name",
                name=bare,
            )
            concepts = session.run(
                f"MATCH (:{TABLE_LABEL} {{name: $name}})-[:INSTANTIATES]->(c:{CONCEPT_LABEL}) "
                "RETURN c.name AS name ORDER BY c.name",
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
                concepts=tuple(r["name"] for r in concepts),
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
            return tuple(r["name"] for r in result)

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
        left, right = from_table.split(".")[-1], to_table.split(".")[-1]
        with self._driver.session(database=self._database) as session:
            for endpoint in (left, right):
                exists = session.run(
                    f"MATCH (t:{TABLE_LABEL} {{name: $name}}) RETURN count(t) AS n",
                    name=endpoint,
                ).single()
                if not exists or exists["n"] == 0:
                    known = session.run(
                        f"MATCH (t:{TABLE_LABEL}) RETURN t.name AS name ORDER BY name"
                    )
                    raise SemanticReadError(
                        f"find_join_path: {endpoint!r} is not a table the semantic layer "
                        f"exposes. Known tables: {', '.join(r['name'] for r in known)}."
                    )
            if left == right:
                return JoinPath(from_table=left, to_table=right, edges=())
            record = session.run(
                f"""
                MATCH (a:{TABLE_LABEL} {{name: $left}}), (b:{TABLE_LABEL} {{name: $right}})
                MATCH path = shortestPath((a)-[:JOINS_TO*..6]-(b))
                RETURN [r IN relationships(path) |
                        {{on: r.on, cardinality: r.cardinality,
                          left: startNode(r).name, right: endNode(r).name}}] AS hops
                """,
                left=left,
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
            return tuple(r["area"] for r in result)

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
