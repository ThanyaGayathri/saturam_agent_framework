"""Writing the ontology to Neo4j, and searching it.

The ontology gets **its own label**, ``OntologyConcept``. The memory substrate's labels are
fixed at ``Episode``, ``Fact`` and ``Entity`` (``cypher.py:80-83``), every write there demands
a memory tier and a five-slot scope, and populating a query filter is refused outright -- so
the ontology cannot live in that substrate even if it wanted to. It is written straight to
Neo4j under a label of its own, the same reasoning D11 applies to the semantic layer, and the
distinct prefix keeps it from ever colliding with the semantic layer's labels at P2.

Three indexes, because the three retrieval legs need different things:

* a **vector** index, so a question reaches a concept by meaning;
* a **full-text** index, so an exact term or synonym reaches it by name;
* a **uniqueness constraint** on ``name``, which also serves as the property lookup.

The vector index is the one that earns the package. Without embedding each node over its name,
description *and* synonyms, the ontology is reachable only by exact match and the synonym
behaviour disappears -- which the plan flags as the usual mistake.

Every public method has a synchronous and an asynchronous variant, per the repository rule.
These are genuine I/O against a database, so a sync-only surface would block the event loop
while looking correct.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from egpt_retail.contracts.vocabulary import TermOrigin
from egpt_retail.ontology.model import Concept, Ontology

__all__ = [
    "CONCEPT_LABEL",
    "ConceptHit",
    "OntologyStore",
]

#: The ontology's own node label. Deliberately prefixed: ``Concept`` alone would collide with
#: whatever the semantic layer calls its own nodes at P2, and the two are different layers with
#: different accountability -- ours is shipped, theirs is read from a customer warehouse.
CONCEPT_LABEL = "OntologyConcept"

_VECTOR_INDEX = "ontology_concept_embedding"
_FULLTEXT_INDEX = "ontology_concept_text"
_NAME_CONSTRAINT = "ontology_concept_name"


@dataclass(frozen=True)
class ConceptHit:
    """One search result, with where it came from and how well it matched."""

    concept: Concept
    score: float
    #: Which leg produced it -- ``vector`` or ``lexical``. Carried so a caller can say why a
    #: concept was reached, which is what the provenance record needs.
    leg: str


def _concept_from_node(node: Any) -> Concept:
    return Concept(
        name=node["name"],
        description=node["description"],
        business_area=node["business_area"],
        synonyms=tuple(node.get("synonyms") or ()),
        question_patterns=tuple(node.get("question_patterns") or ()),
        sources=tuple(node.get("sources") or ()),
        origin=TermOrigin(node.get("origin", "core")),
    )


class OntologyStore:
    """Reads and writes the shipped ontology in Neo4j.

    The driver is injected rather than constructed here, so a caller owns its lifetime and the
    same store can be pointed at a test database. The embedder is injected for the same reason,
    and because which model produces the vectors is a deployment decision -- what matters to
    this module is only that the dimensions match the index.
    """

    def __init__(
        self,
        driver: Any,
        embedder: Any,
        database: str = "neo4j",
        embedding_version: str = "",
    ) -> None:
        self._driver = driver
        self._embedder = embedder
        self._database = database
        #: Stamped on every concept, matching what memory stamps on every memory vector. A
        #: re-embedding migration keys on this; an empty tag means the caller did not resolve
        #: through ``resolve_knowledge_embedder`` and the store cannot say what produced its
        #: vectors.
        self._embedding_version = embedding_version

    # --- schema ------------------------------------------------------------------------

    def _index_statements(self) -> List[str]:
        dimensions = getattr(self._embedder, "dimensions", 384)
        return [
            f"CREATE CONSTRAINT {_NAME_CONSTRAINT} IF NOT EXISTS "
            f"FOR (c:{CONCEPT_LABEL}) REQUIRE c.name IS UNIQUE",
            f"CREATE VECTOR INDEX {_VECTOR_INDEX} IF NOT EXISTS "
            f"FOR (c:{CONCEPT_LABEL}) ON (c.embedding) "
            f"OPTIONS {{indexConfig: {{`vector.dimensions`: {dimensions}, "
            f"`vector.similarity_function`: 'cosine'}}}}",
            f"CREATE FULLTEXT INDEX {_FULLTEXT_INDEX} IF NOT EXISTS "
            f"FOR (c:{CONCEPT_LABEL}) ON EACH [c.name, c.lexical_text]",
        ]

    def index_dimensions(self) -> Optional[int]:
        """The width the existing vector index was built at, or ``None`` if there is none."""
        with self._driver.session(database=self._database) as session:
            record = session.run(
                "SHOW VECTOR INDEXES YIELD name, options "
                "WHERE name = $name RETURN options AS options",
                name=_VECTOR_INDEX,
            ).single()
        if record is None:
            return None
        config = (record["options"] or {}).get("indexConfig", {})
        value = config.get("vector.dimensions")
        return int(value) if value is not None else None

    async def aindex_dimensions(self) -> Optional[int]:
        """Async variant."""
        return await asyncio.to_thread(self.index_dimensions)

    def ensure_indexes(self, *, rebuild_on_dimension_change: bool = False) -> None:
        """Create the three indexes if they are absent. Idempotent.

        **Refuses a silent dimension mismatch.** ``CREATE VECTOR INDEX IF NOT EXISTS`` does
        nothing when an index of that name already exists, whatever width it was built at. So
        moving a deployment from Mode A (768) to Mode B (1024) against an existing store leaves
        the old index in place, and every subsequent write embeds at a width the index cannot
        serve. Nothing errors; retrieval just gets worse, which is the hardest kind of failure
        to attribute later.

        Pass ``rebuild_on_dimension_change`` to drop and recreate. That discards the index, not
        the concepts -- but every stored vector was produced by the old model, so a re-write
        must follow, which is what the loader entrypoint does.
        """
        wanted = int(getattr(self._embedder, "dimensions", 384))
        existing = self.index_dimensions()
        if existing is not None and existing != wanted:
            if not rebuild_on_dimension_change:
                raise RuntimeError(
                    f"vector index {_VECTOR_INDEX!r} exists at {existing} dimensions but the "
                    f"configured embedder produces {wanted}. Re-run with "
                    "rebuild_on_dimension_change=True to drop and rebuild it, and re-write the "
                    "ontology afterwards -- the stored vectors came from the previous model and "
                    "are not comparable with the new one."
                )
            with self._driver.session(database=self._database) as session:
                session.run(f"DROP INDEX {_VECTOR_INDEX} IF EXISTS")
        with self._driver.session(database=self._database) as session:
            for statement in self._index_statements():
                session.run(statement)
        self.await_indexes()

    def await_indexes(self, timeout_seconds: float = 120.0) -> None:
        """Block until every ontology index is ONLINE.

        Neo4j builds a vector index **asynchronously**: ``CREATE VECTOR INDEX`` returns at
        once and population continues in the background. A query issued before population
        finishes does not fail -- it searches whatever fraction has been indexed and returns
        a confident, wrong answer. Found the hard way: seconds after a rebuild, *"how much did
        that promo lift sales"* ranked Order and OrderLine above Promotion; minutes later,
        Promotion was top by a clear margin. Nothing in between had changed but the index.

        So a rebuild is not finished when the statement returns; it is finished when the index
        says ONLINE, and this is what the loader waits on before it reports success.
        """
        import time

        deadline = time.monotonic() + timeout_seconds
        with self._driver.session(database=self._database) as session:
            while True:
                rows = session.run(
                    "SHOW INDEXES YIELD name, state, populationPercent "
                    "WHERE name IN $names RETURN name, state, populationPercent",
                    names=[_VECTOR_INDEX, _FULLTEXT_INDEX],
                ).data()
                pending = [r for r in rows if r["state"] != "ONLINE"]
                if not pending:
                    return
                if time.monotonic() > deadline:
                    detail = ", ".join(
                        f"{r['name']}={r['state']}@{r['populationPercent']:.0f}%"
                        for r in pending
                    )
                    raise RuntimeError(
                        f"ontology indexes not ONLINE after {timeout_seconds:.0f}s: {detail}. "
                        "Queries against a populating index return silently wrong results, "
                        "so the load is not reported complete."
                    )
                time.sleep(0.25)

    async def aawait_indexes(self, timeout_seconds: float = 120.0) -> None:
        """Async variant."""
        await asyncio.to_thread(self.await_indexes, timeout_seconds)

    async def aensure_indexes(
        self, *, rebuild_on_dimension_change: bool = False
    ) -> None:
        """Async variant."""
        await asyncio.to_thread(
            self.ensure_indexes, rebuild_on_dimension_change=rebuild_on_dimension_change
        )

    # --- writes ------------------------------------------------------------------------

    def _rows(self, ontology: Ontology) -> List[Dict[str, Any]]:
        """Concept rows with their vectors, embedded in one batch.

        One call for every concept rather than one per concept: the embedder declares
        ``accepts_text_batches``, and embedding twenty concepts one at a time is twenty model
        invocations for no benefit.
        """
        concepts = list(ontology.concepts)
        if not concepts:
            return []
        texts = [concept.embedding_text for concept in concepts]
        vectors = self._embedder.get_embedding(texts)
        if len(vectors) != len(concepts):
            raise RuntimeError(
                f"embedder returned {len(vectors)} vectors for {len(concepts)} concepts. "
                "Every concept must be embedded, or the ones missed become reachable only by "
                "exact name and nothing reports it."
            )
        return [
            {
                "name": concept.name,
                "description": concept.description,
                "business_area": concept.business_area,
                "synonyms": list(concept.synonyms),
                "question_patterns": list(concept.question_patterns),
                "sources": list(concept.sources),
                "lexical_text": concept.lexical_text,
                "origin": concept.origin.value,
                "version": ontology.version,
                "embedding_version": self._embedding_version,
                "embedding": vector,
            }
            for concept, vector in zip(concepts, vectors)
        ]

    def write(self, ontology: Ontology, *, replace_existing: bool = True) -> int:
        """Write the ontology, returning how many concepts were written.

        ``replace_existing`` clears the ontology's own label first. It never touches anything
        else in the database: the delete is scoped to ``OntologyConcept``, so memory's
        ``Episode``/``Fact``/``Entity`` rows and the semantic layer's future labels are
        untouched by construction rather than by care.
        """
        # The boundary where a dangling edge does damage: MERGE on a missing endpoint
        # invents an empty concept rather than failing.
        ontology.validate_relations()
        rows = self._rows(ontology)
        with self._driver.session(database=self._database) as session:
            if replace_existing:
                session.run(f"MATCH (c:{CONCEPT_LABEL}) DETACH DELETE c")
            session.run(
                f"""
                UNWIND $rows AS row
                MERGE (c:{CONCEPT_LABEL} {{name: row.name}})
                SET c.description = row.description,
                    c.business_area = row.business_area,
                    c.synonyms = row.synonyms,
                    c.question_patterns = row.question_patterns,
                    c.sources = row.sources,
                    c.lexical_text = row.lexical_text,
                    c.origin = row.origin,
                    c.version = row.version,
                    c.embedding_version = row.embedding_version,
                    c.embedding = row.embedding
                """,
                rows=rows,
            )
            for relation in ontology.relations:
                # The relationship type cannot be a bound parameter, so it is interpolated --
                # which is safe only because `Relation.__post_init__` requires an identifier.
                session.run(
                    f"""
                    MATCH (a:{CONCEPT_LABEL} {{name: $source}})
                    MATCH (b:{CONCEPT_LABEL} {{name: $target}})
                    MERGE (a)-[r:{relation.type}]->(b)
                    SET r.description = $description,
                        r.cardinality = $cardinality,
                        r.origin = $origin
                    """,
                    source=relation.source,
                    target=relation.target,
                    description=relation.description,
                    cardinality=relation.cardinality,
                    origin=relation.origin.value,
                )
        # New vectors go into the index asynchronously too. Do not report the write done
        # until the index has caught up, or the first queries answer from a partial one.
        self.await_indexes()
        return len(rows)

    async def awrite(self, ontology: Ontology, *, replace_existing: bool = True) -> int:
        """Async variant."""
        return await asyncio.to_thread(
            self.write, ontology, replace_existing=replace_existing
        )

    def upgrade_core(self, upgraded: Ontology) -> int:
        """Replace the shipped core, re-applying whatever the customer overlaid.

        Called with the ontology already merged -- new core plus the same overlay -- because
        merging is where the "add and annotate, never redefine" rule lives, and doing it here
        would put that rule in two places. What this guarantees is the second half of G10: the
        overlay is still present after the core moves.
        """
        return self.write(upgraded, replace_existing=True)

    async def aupgrade_core(self, upgraded: Ontology) -> int:
        """Async variant."""
        return await asyncio.to_thread(self.upgrade_core, upgraded)

    # --- reads -------------------------------------------------------------------------

    def count(self) -> int:
        """How many concepts are stored."""
        with self._driver.session(database=self._database) as session:
            record = session.run(
                f"MATCH (c:{CONCEPT_LABEL}) RETURN count(c) AS n"
            ).single()
            return int(record["n"]) if record else 0

    async def acount(self) -> int:
        """Async variant."""
        return await asyncio.to_thread(self.count)

    def get_concept(self, name: str) -> Optional[Concept]:
        """One concept by exact name, or ``None``. The property-index leg."""
        with self._driver.session(database=self._database) as session:
            record = session.run(
                f"MATCH (c:{CONCEPT_LABEL} {{name: $name}}) RETURN c", name=name
            ).single()
            return _concept_from_node(record["c"]) if record else None

    async def aget_concept(self, name: str) -> Optional[Concept]:
        """Async variant."""
        return await asyncio.to_thread(self.get_concept, name)

    def search_vector(self, query: str, limit: int = 5) -> Tuple[ConceptHit, ...]:
        """Concepts reached by meaning. This is the leg that makes synonyms work."""
        vector = self._embedder.get_embedding(query)
        with self._driver.session(database=self._database) as session:
            result = session.run(
                f"CALL db.index.vector.queryNodes('{_VECTOR_INDEX}', $k, $vector) "
                "YIELD node, score RETURN node, score",
                k=limit,
                vector=vector,
            )
            return tuple(
                ConceptHit(
                    concept=_concept_from_node(row["node"]),
                    score=float(row["score"]),
                    leg="vector",
                )
                for row in result
            )

    async def asearch_vector(
        self, query: str, limit: int = 5
    ) -> Tuple[ConceptHit, ...]:
        """Async variant."""
        return await asyncio.to_thread(self.search_vector, query, limit)

    def search_lexical(self, query: str, limit: int = 5) -> Tuple[ConceptHit, ...]:
        """Concepts reached by exact term. Complements the vector leg rather than duplicating
        it: a term of art a practitioner types verbatim should not depend on a nearest
        neighbour being close enough."""
        with self._driver.session(database=self._database) as session:
            result = session.run(
                f"CALL db.index.fulltext.queryNodes('{_FULLTEXT_INDEX}', $q) "
                "YIELD node, score RETURN node, score LIMIT $k",
                q=query,
                k=limit,
            )
            return tuple(
                ConceptHit(
                    concept=_concept_from_node(row["node"]),
                    score=float(row["score"]),
                    leg="lexical",
                )
                for row in result
            )

    async def asearch_lexical(
        self, query: str, limit: int = 5
    ) -> Tuple[ConceptHit, ...]:
        """Async variant."""
        return await asyncio.to_thread(self.search_lexical, query, limit)

    def relations_of(self, name: str) -> Tuple[Dict[str, Any], ...]:
        """The edges leaving and entering one concept, with their metadata."""
        with self._driver.session(database=self._database) as session:
            result = session.run(
                f"""
                MATCH (c:{CONCEPT_LABEL} {{name: $name}})-[r]-(other:{CONCEPT_LABEL})
                RETURN type(r) AS type, startNode(r).name AS source, endNode(r).name AS target,
                       r.cardinality AS cardinality, r.description AS description, r.origin AS origin
                ORDER BY type, target
                """,
                name=name,
            )
            return tuple(dict(row) for row in result)

    async def arelations_of(self, name: str) -> Tuple[Dict[str, Any], ...]:
        """Async variant."""
        return await asyncio.to_thread(self.relations_of, name)

    def concepts_of_origin(self, origin: TermOrigin) -> Tuple[str, ...]:
        """Names of the concepts contributed by the core, or by the overlay."""
        with self._driver.session(database=self._database) as session:
            result = session.run(
                f"MATCH (c:{CONCEPT_LABEL} {{origin: $origin}}) RETURN c.name AS name ORDER BY name",
                origin=origin.value,
            )
            return tuple(row["name"] for row in result)

    async def aconcepts_of_origin(self, origin: TermOrigin) -> Tuple[str, ...]:
        """Async variant."""
        return await asyncio.to_thread(self.concepts_of_origin, origin)

    def stored_embedding_version(self) -> Optional[str]:
        """The embedding tag on the stored concepts, or ``None`` if nothing is stored.

        Compared against the configured tag before a write. A width match is necessary but not
        sufficient: two models can share a dimension and produce vectors that are not
        comparable, and the tag is what distinguishes them.
        """
        with self._driver.session(database=self._database) as session:
            record = session.run(
                f"MATCH (c:{CONCEPT_LABEL}) WHERE c.embedding_version IS NOT NULL "
                "RETURN c.embedding_version AS v LIMIT 1"
            ).single()
            return record["v"] if record else None

    async def astored_embedding_version(self) -> Optional[str]:
        """Async variant."""
        return await asyncio.to_thread(self.stored_embedding_version)

    def stored_version(self) -> Optional[str]:
        """The ontology version currently in the graph."""
        with self._driver.session(database=self._database) as session:
            record = session.run(
                f"MATCH (c:{CONCEPT_LABEL} {{origin: 'core'}}) RETURN c.version AS v LIMIT 1"
            ).single()
            return record["v"] if record else None

    async def astored_version(self) -> Optional[str]:
        """Async variant."""
        return await asyncio.to_thread(self.stored_version)

    def concepts_without_embedding(self) -> Tuple[str, ...]:
        """Concept names stored with no vector.

        The failure this catches is silent. A concept written without an embedding is still
        returned by an exact-name lookup, so the ontology looks complete while the
        meaning-based leg quietly stops finding it -- which is the one thing this package
        exists to make work.
        """
        with self._driver.session(database=self._database) as session:
            result = session.run(
                f"MATCH (c:{CONCEPT_LABEL}) WHERE c.embedding IS NULL RETURN c.name AS name ORDER BY name"
            )
            return tuple(row["name"] for row in result)

    async def aconcepts_without_embedding(self) -> Tuple[str, ...]:
        """Async variant."""
        return await asyncio.to_thread(self.concepts_without_embedding)
