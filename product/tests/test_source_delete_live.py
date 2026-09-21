"""Deleting a data source removes what its harvest left behind -- against real Neo4j and Postgres.

``SemanticGraphStore.delete_source_tables`` is Cypher and ``SuggestionRunStore.delete_for_source``
is SQL, and what matters about both is what they do *not* touch: another source's tables, the
operator's own (untagged) tables, and the shared ontology concepts. That is only proved against
the real stores, so it is proved here; the route's ordering and failure handling is
``test_p1_sources_router.py``, and the executor's reclaim of a stale claim is
``test_p2_harvest_source.py``, both DB-free.

Gated like ``test_semantic_live.py``: ``EGPT_INTEGRATION_POSTGRES`` and ``EGPT_INTEGRATION_NEO4J``,
skipped otherwise. Everything written is synthetic and prefixed ``livetest_del`` -- schemas,
source ids, run rows, the concept -- and removed at the end of each test. **It touches no
registered source and none of the operator's real tables**, so it is safe against a shared dev
graph: the operator's table here is a synthetic untagged one written with raw Cypher (a
``write_harvest`` with ``source_id=""`` would also move the deployment's snapshot pointer, which
this must not do).

**Do not mutation-test ``delete_source_tables`` against a shared graph.** The operator's real
tables are stored with ``source_id = ''``, so the empty-id refusal is all that stands between
this method and the deployment's whole semantic layer; deleting that refusal to see these tests
fail is exactly what would wipe it. (It happened once, on a shared dev graph: the operator's tables
and reviewed edges had to be rebuilt from the warehouse and the proposals table, which is why the
Cypher now carries its own ``$source_id <> ''`` guard as well.) Mutate it against a scratch Neo4j.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Iterator

import pytest

from egpt_retail.semantic.harvest import Harvest, HarvestedColumn, HarvestedTable
from egpt_retail.semantic.store import SchemaOwnedByOtherSource, SemanticGraphStore
from egpt_retail.suggestions.runs import SuggestionRunStore

_LIVE = os.environ.get("EGPT_INTEGRATION_POSTGRES") and os.environ.get(
    "EGPT_INTEGRATION_NEO4J"
)
pytestmark = pytest.mark.skipif(
    not _LIVE,
    reason="needs EGPT_INTEGRATION_POSTGRES=1 and EGPT_INTEGRATION_NEO4J=1 with live stores",
)

CONTROL_PLANE = os.environ.get(
    "EGPT_RETAIL_CP_DSN", "postgresql://ai:ai@localhost:5532/ai"
)
NEO4J = os.environ.get("EGPT_ONTOLOGY_NEO4J_URI", "neo4j://localhost:7701")
NEO4J_AUTH = ("neo4j", os.environ.get("EGPT_RETAIL_NEO4J_PASSWORD", "egptretail"))

SOURCE_A = "livetest_del_A"
SOURCE_B = "livetest_del_B"
SCHEMA_A = "livetest_del_a"
SCHEMA_B = "livetest_del_b"
SCHEMA_OPERATOR = "livetest_del_op"
CONCEPT = "LivetestDelConcept"


def _harvest(schema: str, *tables: str) -> Harvest:
    return Harvest(
        schema=schema,
        tables=tuple(
            HarvestedTable(
                schema=schema,
                name=name,
                columns=(
                    HarvestedColumn("id", "integer", False, True),
                    HarvestedColumn("value", "text", True, False),
                ),
                row_count=2,
            )
            for name in tables
        ),
        source="livetest",
    )


@pytest.fixture
def driver() -> Iterator[Any]:
    neo4j = pytest.importorskip("neo4j")
    d = neo4j.GraphDatabase.driver(NEO4J, auth=NEO4J_AUTH)
    yield d
    d.close()


@pytest.fixture
def graph(driver: Any) -> Iterator[SemanticGraphStore]:
    store = SemanticGraphStore(driver=driver)
    store.ensure_indexes()
    yield store
    for schema in (SCHEMA_A, SCHEMA_B, SCHEMA_OPERATOR):
        store.drop_schema(schema)
    with driver.session() as session:
        session.run(f"MATCH (c:SemanticConcept {{name: '{CONCEPT}'}}) DETACH DELETE c")


def _count(driver: Any, query: str, **params: Any) -> int:
    with driver.session() as session:
        return int(session.run(query, **params).single()["n"])


def _tables(driver: Any, schema: str) -> int:
    return _count(
        driver,
        "MATCH (t:SemanticTable {schema: $s}) RETURN count(t) AS n",
        s=schema,
    )


def _columns(driver: Any, schema: str) -> int:
    return _count(
        driver,
        "MATCH (c:SemanticColumn)-[:OF_TABLE]->(t:SemanticTable {schema: $s}) "
        "RETURN count(c) AS n",
        s=schema,
    )


def _concept_exists(driver: Any) -> int:
    return _count(
        driver,
        "MATCH (c:SemanticConcept {name: $n}) RETURN count(c) AS n",
        n=CONCEPT,
    )


def _wire_up(driver: Any) -> None:
    """A concept, an INSTANTIATES edge from each source's first table to it, a JOINS_TO edge
    inside source A, and one synthetic *untagged* (operator) table with a column."""
    with driver.session() as session:
        session.run(f"MERGE (c:SemanticConcept {{name: '{CONCEPT}'}})")
        for schema, first in ((SCHEMA_A, "a_orders"), (SCHEMA_B, "b_orders")):
            session.run(
                "MATCH (t:SemanticTable {schema: $s, name: $n}), "
                f"(c:SemanticConcept {{name: '{CONCEPT}'}}) MERGE (t)-[:INSTANTIATES]->(c)",
                s=schema,
                n=first,
            )
        session.run(
            "MATCH (l:SemanticTable {schema: $s, name: 'a_orders'}), "
            "(r:SemanticTable {schema: $s, name: 'a_items'}) "
            "MERGE (l)-[:JOINS_TO {on: 'livetest'}]->(r)",
            s=SCHEMA_A,
        )
        session.run(
            "MERGE (t:SemanticTable {schema: $s, name: 'op_table'}) "
            "SET t.status = 'accepted' "
            "MERGE (c:SemanticColumn {name: 'id'})-[:OF_TABLE]->(t) SET c.status = 'accepted'",
            s=SCHEMA_OPERATOR,
        )


@pytest.fixture
def two_sources_and_the_operator(graph: SemanticGraphStore, driver: Any) -> None:
    graph.write_harvest(
        _harvest(SCHEMA_A, "a_orders", "a_items"),
        snapshot_version="livetest.a",
        source_id=SOURCE_A,
    )
    graph.write_harvest(
        _harvest(SCHEMA_B, "b_orders"),
        snapshot_version="livetest.b",
        source_id=SOURCE_B,
    )
    _wire_up(driver)


class TestDeleteSourceTables:
    def test_a_sources_tables_and_columns_are_removed(
        self, graph: SemanticGraphStore, driver: Any, two_sources_and_the_operator: None
    ) -> None:
        assert (_tables(driver, SCHEMA_A), _columns(driver, SCHEMA_A)) == (2, 4)

        removed = graph.delete_source_tables(SOURCE_A)

        assert removed == 2
        assert _tables(driver, SCHEMA_A) == 0
        assert _columns(driver, SCHEMA_A) == 0

    def test_another_sources_tables_are_untouched(
        self, graph: SemanticGraphStore, driver: Any, two_sources_and_the_operator: None
    ) -> None:
        graph.delete_source_tables(SOURCE_A)
        assert (_tables(driver, SCHEMA_B), _columns(driver, SCHEMA_B)) == (1, 2)

    def test_the_operators_own_untagged_tables_are_untouched(
        self, graph: SemanticGraphStore, driver: Any, two_sources_and_the_operator: None
    ) -> None:
        graph.delete_source_tables(SOURCE_A)
        graph.delete_source_tables(SOURCE_B)
        assert (
            _tables(driver, SCHEMA_OPERATOR),
            _columns(driver, SCHEMA_OPERATOR),
        ) == (
            1,
            1,
        )

    def test_concept_links_go_with_the_tables_but_the_shared_concept_stays(
        self, graph: SemanticGraphStore, driver: Any, two_sources_and_the_operator: None
    ) -> None:
        links = (
            "MATCH (:SemanticTable {schema: $s})-[r:INSTANTIATES]->"
            f"(:SemanticConcept {{name: '{CONCEPT}'}}) RETURN count(r) AS n"
        )
        assert _count(driver, links, s=SCHEMA_A) == 1
        assert (
            _count(
                driver,
                "MATCH (:SemanticTable {schema: $s})-[r:JOINS_TO]->() RETURN count(r) AS n",
                s=SCHEMA_A,
            )
            == 1
        )

        graph.delete_source_tables(SOURCE_A)

        assert _count(driver, links, s=SCHEMA_A) == 0
        # the concept is the shared ontology, and another source still instantiates it
        assert _concept_exists(driver) == 1
        assert _count(driver, links, s=SCHEMA_B) == 1

    def test_it_is_idempotent(
        self, graph: SemanticGraphStore, two_sources_and_the_operator: None
    ) -> None:
        assert graph.delete_source_tables(SOURCE_A) == 2
        assert graph.delete_source_tables(SOURCE_A) == 0

    def test_an_unknown_source_removes_nothing(
        self, graph: SemanticGraphStore, driver: Any, two_sources_and_the_operator: None
    ) -> None:
        assert graph.delete_source_tables("livetest_del_nobody") == 0
        assert _tables(driver, SCHEMA_A) == 2

    @pytest.mark.parametrize("empty", ["", None])
    def test_the_operators_own_warehouse_is_never_removable_through_it(
        self,
        graph: SemanticGraphStore,
        driver: Any,
        two_sources_and_the_operator: None,
        empty: Any,
    ) -> None:
        with pytest.raises(ValueError):
            graph.delete_source_tables(empty)
        # nothing at all was touched, the untagged operator table included
        assert _tables(driver, SCHEMA_A) == 2
        assert _tables(driver, SCHEMA_B) == 1
        assert _tables(driver, SCHEMA_OPERATOR) == 1

    def test_the_async_twin(
        self, graph: SemanticGraphStore, driver: Any, two_sources_and_the_operator: None
    ) -> None:
        assert asyncio.run(graph.adelete_source_tables(SOURCE_A)) == 2
        assert _tables(driver, SCHEMA_A) == 0
        with pytest.raises(ValueError):
            asyncio.run(graph.adelete_source_tables(""))


class TestTheBugThisFixes:
    """Register schema X, harvest, delete the source, register another with schema X, harvest:
    it used to be refused by the collision guard on behalf of the source that was gone."""

    def test_before_the_cleanup_the_deleted_sources_tables_still_claim_the_schema(
        self, graph: SemanticGraphStore, two_sources_and_the_operator: None
    ) -> None:
        with pytest.raises(SchemaOwnedByOtherSource) as refused:
            graph.write_harvest(
                _harvest(SCHEMA_A, "a_orders"),
                snapshot_version="livetest.new",
                source_id=SOURCE_B,
            )
        assert refused.value.owner == SOURCE_A

    def test_after_the_cleanup_a_new_source_can_take_the_schema(
        self, graph: SemanticGraphStore, driver: Any, two_sources_and_the_operator: None
    ) -> None:
        graph.delete_source_tables(SOURCE_A)

        written = graph.write_harvest(
            _harvest(SCHEMA_A, "a_orders", "a_items"),
            snapshot_version="livetest.new",
            source_id=SOURCE_B,
        )

        assert written == 2
        assert graph.schema_owned_by_other_source(SCHEMA_A, SOURCE_B) is None
        assert _tables(driver, SCHEMA_A) == 2


class TestDeleteSuggestionRunsForSource:
    @pytest.fixture
    def runs(self) -> Iterator[SuggestionRunStore]:
        psycopg = pytest.importorskip("psycopg")
        connection = psycopg.connect(CONTROL_PLANE)
        store = SuggestionRunStore(connection)
        store.ensure_schema()
        yield store
        connection.rollback()
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM semantic.kpi_suggestion_run "
                "WHERE source_id LIKE 'livetest_del_%' OR ask = 'livetest_del'"
            )
        connection.commit()
        connection.close()

    def _make(self, runs: SuggestionRunStore, source_id: Any, outcome: str) -> Any:
        record = runs.create(None, (), "livetest_del", source_id)
        if outcome == "succeeded":
            runs.mark_running(record.identifier)
            runs.mark_succeeded(record.identifier, {"suggestions": [], "notes": ""})
        elif outcome == "failed":
            runs.mark_running(record.identifier)
            runs.mark_failed(record.identifier, "no model")
        return record

    def test_every_run_of_the_source_is_removed_whatever_its_status(
        self, runs: SuggestionRunStore
    ) -> None:
        made = [
            self._make(runs, SOURCE_A, outcome)
            for outcome in ("succeeded", "failed", "queued")
        ]

        assert runs.delete_for_source(SOURCE_A) == 3
        assert all(runs.get(record.identifier) is None for record in made)

    def test_another_sources_runs_and_the_operators_are_untouched(
        self, runs: SuggestionRunStore
    ) -> None:
        mine = self._make(runs, SOURCE_A, "succeeded")
        theirs = self._make(runs, SOURCE_B, "succeeded")
        operators = self._make(
            runs, None, "succeeded"
        )  # no source: the deployment's own

        runs.delete_for_source(SOURCE_A)

        assert runs.get(mine.identifier) is None
        assert runs.get(theirs.identifier) is not None
        assert runs.get(operators.identifier) is not None

    def test_the_deleted_sources_suggestions_leave_the_standing_list(
        self, runs: SuggestionRunStore
    ) -> None:
        self._make(runs, SOURCE_A, "succeeded")
        assert any(r.source_id == SOURCE_A for r in runs.current().succeeded)

        runs.delete_for_source(SOURCE_A)

        assert not any(r.source_id == SOURCE_A for r in runs.current().succeeded)

    def test_it_is_idempotent(self, runs: SuggestionRunStore) -> None:
        self._make(runs, SOURCE_A, "succeeded")
        assert runs.delete_for_source(SOURCE_A) == 1
        assert runs.delete_for_source(SOURCE_A) == 0

    @pytest.mark.parametrize("empty", ["", None])
    def test_an_empty_id_is_a_caller_mistake_not_no_runs(
        self, runs: SuggestionRunStore, empty: Any
    ) -> None:
        operators = self._make(runs, None, "succeeded")
        with pytest.raises(ValueError):
            runs.delete_for_source(empty)
        assert runs.get(operators.identifier) is not None

    def test_the_async_twin(self, runs: SuggestionRunStore) -> None:
        self._make(runs, SOURCE_A, "succeeded")
        assert asyncio.run(runs.adelete_for_source(SOURCE_A)) == 1
