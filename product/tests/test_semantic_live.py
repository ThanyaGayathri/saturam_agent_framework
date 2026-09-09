"""Live regression tests for the semantic layer, against real Postgres and Neo4j.

Review round 1 on the P2 PR was right that the graph-side fixes had no automated protection:
they were proved by cookbooks nobody's CI runs. These are the same proofs as tests, gated the
way ``integration.yml`` gates its own suites -- ``EGPT_INTEGRATION_POSTGRES`` and
``EGPT_INTEGRATION_NEO4J`` -- so wiring them into that job is one line, and skipped
otherwise so the unit run stays database-free.

Locally:

    EGPT_INTEGRATION_POSTGRES=1 EGPT_INTEGRATION_NEO4J=1 pytest product/tests/test_semantic_live.py

Everything written here is prefixed ``livetest_`` and removed at the end of the module.
"""

from __future__ import annotations

import os
from typing import Any, Iterator

import pytest
from egpt_retail.contracts.semantic import (
    AuthoritativeSource,
    Concept,
    JoinEdge,
    SemanticReadError,
)
from egpt_retail.semantic.harvest import Harvest, harvest_schema
from egpt_retail.semantic.proposals import (
    Proposal,
    ProposalKind,
    ProposalStore,
    ReviewState,
)
from egpt_retail.semantic.refresh import refresh_semantic_layer
from egpt_retail.semantic.snapshots import SnapshotStore
from egpt_retail.semantic.store import EmptyHarvestRefused, SemanticGraphStore

_LIVE = os.environ.get("EGPT_INTEGRATION_POSTGRES") and os.environ.get(
    "EGPT_INTEGRATION_NEO4J"
)
pytestmark = pytest.mark.skipif(
    not _LIVE,
    reason="needs EGPT_INTEGRATION_POSTGRES=1 and EGPT_INTEGRATION_NEO4J=1 with live stores",
)

WAREHOUSE = os.environ.get(
    "EGPT_RETAIL_WAREHOUSE_DSN", "postgresql://ai:ai@localhost:5532/egpt_warehouse"
)
CONTROL_PLANE = os.environ.get(
    "EGPT_RETAIL_CP_DSN", "postgresql://ai:ai@localhost:5532/ai"
)
NEO4J = os.environ.get("EGPT_ONTOLOGY_NEO4J_URI", "neo4j://localhost:7701")
NEO4J_AUTH = ("neo4j", os.environ.get("EGPT_RETAIL_NEO4J_PASSWORD", "egptretail"))
PROPOSER = "livetest_proposer"
MARKER = "livetest"


@pytest.fixture(scope="module")
def driver() -> Iterator[Any]:
    neo4j = pytest.importorskip("neo4j")
    d = neo4j.GraphDatabase.driver(NEO4J, auth=NEO4J_AUTH)
    yield d
    with d.session() as s:
        s.run(
            "MATCH (c:SemanticConcept) WHERE c.name STARTS WITH 'Livetest' DETACH DELETE c"
        )
        s.run("MATCH ()-[j:JOINS_TO]->() WHERE j.on CONTAINS $m DELETE j", m=MARKER)
    d.close()


@pytest.fixture(scope="module")
def control_plane() -> Iterator[Any]:
    psycopg = pytest.importorskip("psycopg")
    conn = psycopg.connect(CONTROL_PLANE)
    yield conn
    with conn.cursor() as cur:
        cur.execute("DELETE FROM semantic.proposal WHERE proposer = %s", (PROPOSER,))
        cur.execute(
            "DELETE FROM semantic.harvest_snapshot WHERE version LIKE 'livetest%'"
        )
    conn.commit()
    conn.close()


@pytest.fixture(scope="module")
def harvest() -> Harvest:
    psycopg = pytest.importorskip("psycopg")
    conn = psycopg.connect(WAREHOUSE)
    conn.autocommit = True
    try:
        return harvest_schema(conn, "silver", source="livetest", sample=False)
    finally:
        conn.close()


@pytest.fixture(scope="module")
def graph(driver: Any, harvest: Harvest) -> SemanticGraphStore:
    store = SemanticGraphStore(driver=driver)
    store.ensure_indexes()
    store.write_harvest(harvest, snapshot_version="livetest.0")
    return store


@pytest.fixture
def proposals(control_plane: Any) -> ProposalStore:
    store = ProposalStore(control_plane)
    store.ensure_schema()
    return store


def _edges(driver: Any) -> dict:
    with driver.session() as s:
        return {
            k: s.run(f"MATCH ()-[r:{k}]->() RETURN count(r) AS n").single()["n"]
            for k in ("INSTANTIATES", "JOINS_TO", "AUTHORITATIVE_SOURCE")
        }


def _table_count(driver: Any) -> int:
    with driver.session() as s:
        return s.run("MATCH (t:SemanticTable) RETURN count(t) AS n").single()["n"]


class TestEmptyHarvest:
    def test_is_refused_when_the_graph_holds_tables(
        self, graph: SemanticGraphStore, driver: Any
    ) -> None:
        """Critical from review round 1: `WHERE NOT x IN []` is true for everything."""
        before = _table_count(driver)
        assert before > 0
        with pytest.raises(EmptyHarvestRefused, match="every reviewed edge"):
            graph.write_harvest(
                Harvest(schema="silver", tables=()), snapshot_version="x"
            )
        assert _table_count(driver) == before

    def test_refresh_refuses_before_touching_either_store(
        self, control_plane: Any, driver: Any
    ) -> None:
        psycopg = pytest.importorskip("psycopg")
        warehouse = psycopg.connect(WAREHOUSE)
        snapshots = SnapshotStore(control_plane)
        snapshots.ensure_schema()
        try:
            with pytest.raises(EmptyHarvestRefused):
                refresh_semantic_layer(
                    schema="no_such_schema_livetest",
                    version="livetest.empty",
                    warehouse=warehouse,
                    control_plane=control_plane,
                    driver=driver,
                )
        finally:
            warehouse.close()
        assert "livetest.empty" not in snapshots.versions()


class TestReharvestPreservesReview:
    def test_reviewed_edges_survive(
        self, graph: SemanticGraphStore, driver: Any, harvest: Harvest
    ) -> None:
        graph.write_concept(
            Concept(name="LivetestContact", description="x"),
            tables=("silver.crm_contact",),
        )
        graph.write_join(
            JoinEdge(
                "crm_contact",
                "crm_deal",
                f"crm_contact.{MARKER}_id = crm_deal.{MARKER}_id",
            )
        )
        graph.write_authoritative_source(
            AuthoritativeSource(concept="LivetestContact", table="crm_contact")
        )
        before = _edges(driver)
        assert all(before.values())
        graph.write_harvest(harvest, snapshot_version="livetest.1")
        assert _edges(driver) == before

    def test_no_row_values_in_the_graph(
        self, graph: SemanticGraphStore, driver: Any
    ) -> None:
        with driver.session() as s:
            n = s.run(
                "MATCH (c:SemanticColumn) WHERE c.sampled_values IS NOT NULL RETURN count(c) AS n"
            ).single()["n"]
        assert n == 0


class TestWritesRefuseMissingEndpoints:
    def test_join_to_a_missing_table_raises(self, graph: SemanticGraphStore) -> None:
        with pytest.raises(SemanticReadError, match="not in the graph"):
            graph.write_join(JoinEdge("crm_contact", "livetest_missing", "a.x = b.x"))

    def test_authoritative_source_for_a_missing_concept_raises(
        self, graph: SemanticGraphStore
    ) -> None:
        with pytest.raises(SemanticReadError, match="concept is not in the graph"):
            graph.write_authoritative_source(
                AuthoritativeSource(concept="LivetestNobody", table="crm_contact")
            )

    def test_apply_records_the_failure_instead_of_marking_applied(
        self, graph: SemanticGraphStore, proposals: ProposalStore
    ) -> None:
        """Major from review round 1: a silent no-op became a permanent false 'applied'."""
        bad = Proposal(
            kind=ProposalKind.JOIN,
            target="crm_contact->livetest_missing",
            payload={
                "left_table": "crm_contact",
                "right_table": "livetest_missing",
                "on": f"crm_contact.{MARKER}_x = livetest_missing.x",
            },
            proposer=PROPOSER,
        )
        proposals.propose(bad)
        proposals.accept([bad.identifier], "livetest_reviewer")
        outcome = proposals.apply_accepted(graph)
        assert bad.identifier not in outcome.applied
        assert any(f.identifier == bad.identifier for f in outcome.failed)
        row = proposals.get(bad.identifier)
        assert row is not None
        assert row.state is ReviewState.ACCEPTED
        assert row.applied_at is None
        assert row.apply_error and "not in the graph" in row.apply_error


class TestJoinIdentity:
    def test_a_reversed_join_updates_the_same_edge(
        self, graph: SemanticGraphStore, driver: Any
    ) -> None:
        on = f"crm_deal.{MARKER}_rev = crm_contact.{MARKER}_rev"
        graph.write_join(
            JoinEdge("crm_deal", "crm_contact", on, cardinality="many-to-one")
        )
        graph.write_join(
            JoinEdge("crm_contact", "crm_deal", on, cardinality="one-to-many")
        )
        with driver.session() as s:
            rows = s.run(
                "MATCH (a)-[j:JOINS_TO {on: $on}]->(b) "
                "RETURN a.name AS a, b.name AS b, j.cardinality AS c",
                on=on,
            ).data()
        assert len(rows) == 1
        assert (rows[0]["a"], rows[0]["b"]) == ("crm_contact", "crm_deal")
        assert rows[0]["c"] == "one-to-many"

    def test_two_predicates_are_two_edges(
        self, graph: SemanticGraphStore, driver: Any
    ) -> None:
        graph.write_join(JoinEdge("crm_contact", "crm_deal", f"x.{MARKER}_p1 = y.p1"))
        graph.write_join(JoinEdge("crm_contact", "crm_deal", f"x.{MARKER}_p2 = y.p2"))
        with driver.session() as s:
            n = s.run(
                "MATCH (a {name:'crm_contact'})-[j:JOINS_TO]->(b {name:'crm_deal'}) "
                "WHERE j.on CONTAINS $m RETURN count(j) AS n",
                m=f"{MARKER}_p",
            ).single()["n"]
        assert n == 2


class TestSchemaAwareLookups:
    def test_qualified_and_bare_names_resolve_to_the_same_table(
        self, graph: SemanticGraphStore
    ) -> None:
        bare = graph.get_table("crm_contact")
        qualified = graph.get_table("silver.crm_contact")
        assert bare is not None and qualified is not None
        assert bare.ref == qualified.ref
        assert bare.ref.schema == "silver"

    def test_an_unknown_table_is_none_not_an_error(
        self, graph: SemanticGraphStore
    ) -> None:
        assert graph.get_table("livetest_missing") is None


class TestRefreshOrdering:
    def test_a_failed_graph_write_leaves_no_snapshot(self, control_plane: Any) -> None:
        """Minor from review round 1: the snapshot committed before the graph write."""

        class _BrokenDriver:
            def session(self, **_: Any) -> Any:
                raise RuntimeError("neo4j is down")

        psycopg = pytest.importorskip("psycopg")
        warehouse = psycopg.connect(WAREHOUSE)
        snapshots = SnapshotStore(control_plane)
        snapshots.ensure_schema()
        try:
            with pytest.raises(RuntimeError, match="neo4j is down"):
                refresh_semantic_layer(
                    version="livetest.broken",
                    warehouse=warehouse,
                    control_plane=control_plane,
                    driver=_BrokenDriver(),
                )
        finally:
            warehouse.close()
        assert "livetest.broken" not in snapshots.versions()


class TestSchemaIsolation:
    """Round 2's headline: one graph, two schemas, and a harvest of one must not touch the
    other. Uses a synthetic second schema so nothing depends on the warehouse having one."""

    GOLD = "livetest_gold"

    def _gold(self) -> Harvest:
        from egpt_retail.semantic.harvest import HarvestedColumn, HarvestedTable

        return Harvest(
            schema=self.GOLD,
            tables=(
                HarvestedTable(
                    schema=self.GOLD,
                    name="crm_contact",  # the same bare name silver has, on purpose
                    columns=(HarvestedColumn("contact_id", "integer", False, True),),
                    row_count=0,
                ),
            ),
            source="livetest",
        )

    def test_a_second_schema_leaves_the_first_intact(
        self, graph: SemanticGraphStore, driver: Any
    ) -> None:
        graph.write_concept(Concept(name="LivetestIsolated", description="x"))
        graph.write_join(
            JoinEdge(
                "crm_contact",
                "crm_deal",
                f"crm_contact.{MARKER}_iso = crm_deal.{MARKER}_iso",
            )
        )
        graph.write_authoritative_source(
            AuthoritativeSource(concept="LivetestIsolated", table="silver.crm_contact")
        )
        silver_tables = _table_count(driver)
        edges = _edges(driver)
        try:
            written = graph.write_harvest(
                self._gold(), snapshot_version="livetest.gold"
            )
            assert written == 1
            assert _table_count(driver) == silver_tables + 1
            assert _edges(driver) == edges
            # And the other way: re-harvesting silver leaves gold alone.
            with driver.session() as s:
                gold_before = s.run(
                    "MATCH (t:SemanticTable {schema: $s}) RETURN count(t) AS n",
                    s=self.GOLD,
                ).single()["n"]
            assert gold_before == 1
        finally:
            removed = graph.drop_schema(self.GOLD)
        assert removed == 1
        assert _table_count(driver) == silver_tables

    def test_a_bare_name_held_in_two_schemas_is_refused(
        self, graph: SemanticGraphStore
    ) -> None:
        graph.write_harvest(self._gold(), snapshot_version="livetest.gold")
        try:
            with pytest.raises(SemanticReadError, match="more than one schema"):
                graph.get_table("crm_contact")
            assert graph.get_table("silver.crm_contact") is not None
            assert graph.get_table(f"{self.GOLD}.crm_contact") is not None
        finally:
            graph.drop_schema(self.GOLD)

    def test_an_empty_harvest_of_an_unheld_schema_writes_nothing_and_removes_nothing(
        self, graph: SemanticGraphStore, driver: Any
    ) -> None:
        before = _table_count(driver)
        assert (
            graph.write_harvest(
                Harvest(schema="livetest_never", tables=()), snapshot_version="v"
            )
            == 0
        )
        assert _table_count(driver) == before


class TestRefreshEdges:
    def test_a_reused_version_still_diffs_against_the_run_before(
        self, control_plane: Any, driver: Any
    ) -> None:
        """Round 2 minor: a constant --version made every re-run report no drift."""
        psycopg = pytest.importorskip("psycopg")
        warehouse = psycopg.connect(WAREHOUSE)
        warehouse.autocommit = True
        try:
            refresh_semantic_layer(
                version="livetest.same",
                warehouse=warehouse,
                control_plane=control_plane,
                driver=driver,
            )
            with warehouse.cursor() as cur:
                cur.execute(
                    "ALTER TABLE silver.crm_deal ADD COLUMN IF NOT EXISTS livetest_col text"
                )
            try:
                again = refresh_semantic_layer(
                    version="livetest.same",
                    warehouse=warehouse,
                    control_plane=control_plane,
                    driver=driver,
                )
            finally:
                with warehouse.cursor() as cur:
                    cur.execute(
                        "ALTER TABLE silver.crm_deal DROP COLUMN IF EXISTS livetest_col"
                    )
                refresh_semantic_layer(
                    version="livetest.restore",
                    warehouse=warehouse,
                    control_plane=control_plane,
                    driver=driver,
                )
        finally:
            warehouse.close()
        # The only stored version was the one being replaced, so the diff is against what
        # that row held before the overwrite -- there is no other row to diff against.
        assert again.previous_version == "livetest.same"
        assert (
            again.diff is not None
            and "crm_deal.livetest_col" in again.diff.added_columns
        )

    def test_previous_is_scoped_to_the_schema(self, control_plane: Any) -> None:
        """Round 2 major: harvesting gold after silver diffed gold against silver."""
        snapshots = SnapshotStore(control_plane)
        snapshots.ensure_schema()
        snapshots.save(
            Harvest(schema="livetest_a", tables=(), source="x"), "livetest.a1"
        )
        snapshots.save(
            Harvest(schema="livetest_b", tables=(), source="x"), "livetest.b1"
        )
        assert snapshots.versions("livetest_a") == ("livetest.a1",)
        assert snapshots.versions("livetest_b") == ("livetest.b1",)
        assert (
            "livetest.a1" in snapshots.versions()
            and "livetest.b1" in snapshots.versions()
        )

    def test_a_failed_snapshot_leaves_the_graph_one_ahead_and_the_next_run_corrects(
        self, control_plane: Any, driver: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Round 2 minor: the reverse of the graph-fails case, pinned."""
        psycopg = pytest.importorskip("psycopg")
        warehouse = psycopg.connect(WAREHOUSE)
        graph = SemanticGraphStore(driver=driver)
        snapshots = SnapshotStore(control_plane)

        def _explode(self: Any, *a: Any, **k: Any) -> str:
            raise RuntimeError("postgres is down")

        try:
            monkeypatch.setattr(SnapshotStore, "save", _explode)
            with pytest.raises(RuntimeError, match="postgres is down"):
                refresh_semantic_layer(
                    version="livetest.ahead",
                    warehouse=warehouse,
                    control_plane=control_plane,
                    driver=driver,
                )
            monkeypatch.undo()
            assert graph.snapshot().version == "livetest.ahead"
            assert "livetest.ahead" not in snapshots.versions("silver")
            corrected = refresh_semantic_layer(
                version="livetest.corrected",
                warehouse=warehouse,
                control_plane=control_plane,
                driver=driver,
            )
        finally:
            warehouse.close()
        assert graph.snapshot().version == "livetest.corrected"
        assert corrected.version in snapshots.versions("silver")


class TestAuditTrailReachesTheGraph:
    def test_an_applied_proposal_stamps_the_artifact(
        self, graph: SemanticGraphStore, proposals: ProposalStore, driver: Any
    ) -> None:
        proposal = Proposal(
            kind=ProposalKind.ENRICHMENT,
            target="LivetestStamped",
            payload={"name": "LivetestStamped", "description": "stamped"},
            proposer=PROPOSER,
        )
        proposals.propose(proposal)
        proposals.accept([proposal.identifier], "livetest_reviewer")
        outcome = proposals.apply_accepted(graph)
        assert proposal.identifier in outcome.applied
        with driver.session() as s:
            stamp = s.run(
                "MATCH (c:SemanticConcept {name: 'LivetestStamped'}) RETURN c.applied_by_proposal AS p"
            ).single()["p"]
        assert stamp == proposal.identifier

    def test_a_pending_mapping_shows_on_the_table_only_when_asked(
        self, graph: SemanticGraphStore, proposals: ProposalStore, driver: Any
    ) -> None:
        graph.write_concept(Concept(name="LivetestMapped", description="x"))
        reading = SemanticGraphStore(driver=driver, proposals=proposals)
        pending = Proposal(
            kind=ProposalKind.MAPPING,
            target="silver.crm_deal",
            payload={"table": "silver.crm_deal", "concept": "LivetestMapped"},
            proposer=PROPOSER,
        )
        proposals.propose(pending)
        plain = reading.get_table("crm_deal")
        shown = reading.get_table("crm_deal", include_proposed=True)
        assert plain is not None and shown is not None
        assert "LivetestMapped" not in plain.concepts
        assert "LivetestMapped" in shown.concepts
