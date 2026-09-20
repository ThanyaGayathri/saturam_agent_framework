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
from fastapi import FastAPI
from fastapi.testclient import TestClient

from egpt_retail.api.routers import kpis as kpis_module
from egpt_retail.api.routers import semantic as semantic_module
from egpt_retail.contracts.semantic import (
    AuthoritativeSource,
    Concept,
    JoinEdge,
    SemanticReadError,
)
from egpt_retail.kpi.store import KpiRegistryStore
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


class TestKpiRegistryLive:
    """K11's graph-side guarantees, which the DB-free tests cannot reach."""

    IDENT = "livetest_kpi__contacts"

    @pytest.fixture
    def registry(self, control_plane: Any) -> Any:
        from egpt_retail.kpi.store import KpiRegistryStore

        store = KpiRegistryStore(control_plane)
        store.ensure_schema()
        with control_plane.cursor() as cur:
            cur.execute(
                "DELETE FROM semantic.kpi_definition WHERE identifier LIKE 'livetest%'"
            )
        control_plane.commit()
        yield store
        with control_plane.cursor() as cur:
            cur.execute(
                "DELETE FROM semantic.kpi_definition WHERE identifier LIKE 'livetest%'"
            )
        control_plane.commit()

    def _definition(self, **overrides: Any) -> Any:
        from egpt_retail.contracts.kpi import KpiDefinition

        fields: dict = {
            "identifier": self.IDENT,
            "name": "Livetest contacts",
            "description": "Contacts on file.",
            "sql": "SELECT count(*) FROM crm_contact",
            "grain": "overall",
            "business_area": "customer",
            "source_tables": frozenset({"crm_contact"}),
            "concept": "LivetestMetricConcept",
        }
        fields.update(overrides)
        return KpiDefinition(**fields)

    def test_a_definition_survives_postgres(self, registry: Any) -> None:
        original = self._definition()
        registry.save(original)
        assert registry.get(self.IDENT) == original

    def test_the_mirror_makes_a_concept_reach_its_metrics(
        self, registry: Any, graph: SemanticGraphStore, driver: Any
    ) -> None:
        # The concept has to be there first: it enters through an accepted proposal (D10),
        # never through registering a KPI that names it.
        graph.write_concept(Concept(name="LivetestMetricConcept", description="x"))
        registry.save(self._definition())
        assert registry.mirror(driver) >= 1
        with driver.session() as s:
            metrics = s.run(
                "MATCH (c:SemanticConcept {name: 'LivetestMetricConcept'})"
                "-[:MEASURED_BY]->(k:SemanticKpi) RETURN collect(k.identifier) AS ids"
            ).single()["ids"]
            reads = s.run(
                "MATCH (k:SemanticKpi {identifier: $i})-[:READS]->(t:SemanticTable) "
                "RETURN collect(t.name) AS tables",
                i=self.IDENT,
            ).single()["tables"]
        try:
            assert metrics == [self.IDENT]
            assert reads == ["crm_contact"]
        finally:
            with driver.session() as s:
                s.run(
                    "MATCH (c:SemanticConcept {name: 'LivetestMetricConcept'}) DETACH DELETE c"
                )

    def test_the_mirror_cannot_invent_the_concept_a_kpi_names(
        self, registry: Any, graph: SemanticGraphStore, driver: Any
    ) -> None:
        """Registering a KPI must not put a concept in the graph nobody reviewed (D10).

        The first version merged the concept into existence, and K5's cookbook caught it:
        the four concepts it conjured made the real store and the stub disagree about
        ``list_concepts()``.
        """
        registry.save(self._definition(concept="LivetestNeverProposed"))
        registry.mirror(driver)
        with driver.session() as s:
            invented = s.run(
                "MATCH (c:SemanticConcept {name: 'LivetestNeverProposed'}) RETURN count(c) AS n"
            ).single()["n"]
            edges = s.run(
                "MATCH (k:SemanticKpi {identifier: $i}) "
                "OPTIONAL MATCH (:SemanticConcept)-[:MEASURED_BY]->(k) RETURN count(*) AS n",
                i=self.IDENT,
            ).single()["n"]
        assert invented == 0
        assert edges == 1  # the OPTIONAL MATCH row, with no concept on it
        assert "LivetestNeverProposed" not in graph.list_concepts()

    def test_the_mirror_prunes_what_postgres_no_longer_holds(
        self, registry: Any, graph: SemanticGraphStore, driver: Any
    ) -> None:
        registry.save(self._definition())
        registry.mirror(driver)
        registry.delete(self.IDENT)
        registry.mirror(driver)
        with driver.session() as s:
            left = s.run(
                "MATCH (k:SemanticKpi {identifier: $i}) RETURN count(k) AS n",
                i=self.IDENT,
            ).single()["n"]
            tables = s.run("MATCH (t:SemanticTable) RETURN count(t) AS n").single()["n"]
        assert left == 0
        # The prune is bound to SemanticKpi: the tables it read are untouched.
        assert tables > 0

    def test_a_formula_that_stops_reading_a_table_loses_the_edge(
        self, registry: Any, graph: SemanticGraphStore, driver: Any
    ) -> None:
        """A stale READS edge would over-report which KPIs a changing table breaks."""
        graph.write_concept(Concept(name="LivetestMetricConcept", description="x"))
        registry.save(self._definition())
        registry.mirror(driver)
        registry.save(
            self._definition(
                sql="SELECT count(*) FROM crm_deal",
                source_tables=frozenset({"crm_deal"}),
            )
        )
        registry.mirror(driver)
        with driver.session() as s:
            reads = s.run(
                "MATCH (k:SemanticKpi {identifier: $i})-[:READS]->(t) "
                "RETURN collect(t.name) AS tables",
                i=self.IDENT,
            ).single()["tables"]
        try:
            assert reads == ["crm_deal"]
        finally:
            with driver.session() as s:
                s.run(
                    "MATCH (c:SemanticConcept {name: 'LivetestMetricConcept'}) DETACH DELETE c"
                )

    def test_feasibility_follows_the_live_snapshot(
        self, registry: Any, graph: SemanticGraphStore
    ) -> None:
        """Never stored, so it changes with the layer rather than with a cached row."""
        from egpt_retail.contracts.kpi import FeasibilityVerdict

        registry.save(self._definition())
        blocked = self._definition(
            identifier="livetest_kpi__absent",
            sql="SELECT count(*) FROM livetest_absent_one JOIN livetest_absent_two ON true",
            source_tables=frozenset({"livetest_absent_one", "livetest_absent_two"}),
            concept=None,
        )
        registry.save(blocked)
        results = registry.feasibility(graph)
        assert results[self.IDENT].verdict is FeasibilityVerdict.READY
        assert results["livetest_kpi__absent"].verdict is FeasibilityVerdict.BLOCKED
        assert sorted(results["livetest_kpi__absent"].missing_tables) == [
            "livetest_absent_one",
            "livetest_absent_two",
        ]

    def test_revalidate_stamps_only_what_is_computable(
        self, registry: Any, graph: SemanticGraphStore
    ) -> None:
        registry.save(self._definition())
        registry.save(
            self._definition(
                identifier="livetest_kpi__absent",
                sql="SELECT count(*) FROM livetest_absent",
                source_tables=frozenset({"livetest_absent"}),
                concept=None,
            )
        )
        report = registry.revalidate(graph)
        assert self.IDENT in report.validated
        assert "livetest_kpi__absent" in report.unvalidated
        assert (
            registry.get(self.IDENT).validated_against_snapshot
            == report.snapshot_version
        )
        assert registry.get("livetest_kpi__absent").validated_against_snapshot is None


def _stored_definition(registry: Any, identifier: str) -> Any:
    """A stored definition that must exist; a test asserting on a missing one should say so."""
    definition = registry.get(identifier)
    assert definition is not None, identifier
    return definition


class TestP3Verification:
    """The graph-side findings of the P3 verification, pinned."""

    def test_accepting_a_partial_enrichment_does_not_wipe_accepted_fields(
        self, graph: SemanticGraphStore
    ) -> None:
        """A K8 proposal carrying synonyms and nothing else erased the description and the
        sql_filter a person had accepted -- the P3 verification's first finding."""
        graph.write_concept(
            Concept(
                name="LivetestMerged",
                description="A thing.",
                synonyms=("first",),
                sql_filter="x.kind = 'thing'",
                compliance_tags=("pii",),
            )
        )
        graph.write_concept(
            Concept(name="LivetestMerged", synonyms=("second",), description="")
        )
        merged = graph.get_concept("LivetestMerged")
        assert merged is not None
        assert merged.description == "A thing."
        assert merged.sql_filter == "x.kind = 'thing'"
        assert set(merged.synonyms) == {"first", "second"}
        assert merged.compliance_tags == ("pii",)

    def test_a_column_meaning_lands_on_the_column(
        self, graph: SemanticGraphStore
    ) -> None:
        # The concept has to exist: `Column.concept` is part of the 1.2.0 read surface, so a
        # meaning naming one that does not would publish a dangling reference.
        graph.write_concept(
            Concept(name="LivetestSession", description="a web session")
        )
        graph.write_column_meaning(
            "silver.analytics_event",
            "session_id",
            concept="LivetestSession",
            meaning="the analytics vendor's session id",
            joinable=False,
        )
        try:
            table = graph.get_table("silver.analytics_event")
            assert table is not None
            column = next(c for c in table.columns if c.name == "session_id")
            assert column.concept == "LivetestSession"
            assert column.meaning == "the analytics vendor's session id"
            assert column.joinable is False
        finally:
            with graph._driver.session() as s:  # noqa: SLF001 -- test cleanup
                s.run(
                    "MATCH (c:SemanticColumn {name:'session_id'})-[:OF_TABLE]->"
                    "(:SemanticTable {name:'analytics_event'}) "
                    "REMOVE c.concept, c.meaning, c.joinable"
                )
                s.run(
                    "MATCH (c:SemanticConcept {name:'LivetestSession'}) DETACH DELETE c"
                )

    def test_a_meaning_for_a_missing_column_raises(
        self, graph: SemanticGraphStore
    ) -> None:
        graph.write_concept(
            Concept(name="LivetestSession", description="a web session")
        )
        try:
            with pytest.raises(SemanticReadError, match="not in the graph"):
                graph.write_column_meaning(
                    "silver.analytics_event",
                    "livetest_absent",
                    concept="LivetestSession",
                    meaning="y",
                )
        finally:
            with graph._driver.session() as s:  # noqa: SLF001 -- test cleanup
                s.run(
                    "MATCH (c:SemanticConcept {name:'LivetestSession'}) DETACH DELETE c"
                )

    def test_a_meaning_naming_an_unknown_concept_raises(
        self, graph: SemanticGraphStore
    ) -> None:
        """Round 2: without this, `Column.concept` could point at nothing."""
        with pytest.raises(SemanticReadError, match="not in the graph"):
            graph.write_column_meaning(
                "silver.analytics_event",
                "session_id",
                concept="LivetestNeverDefined",
                meaning="y",
            )

    def test_the_mirror_owns_concept_metrics(
        self, graph: SemanticGraphStore, control_plane: Any, driver: Any
    ) -> None:
        from egpt_retail.contracts.kpi import KpiDefinition
        from egpt_retail.kpi.store import KpiRegistryStore

        graph.write_concept(Concept(name="LivetestMetricOwner", description="x"))
        registry = KpiRegistryStore(control_plane)
        registry.ensure_schema()
        registry.save(
            KpiDefinition(
                identifier="livetest_kpi__owned",
                name="n",
                description="d",
                sql="SELECT count(*) FROM crm_contact",
                grain="overall",
                business_area="customer",
                source_tables=frozenset({"crm_contact"}),
                concept="LivetestMetricOwner",
            )
        )
        try:
            registry.mirror(driver)
            concept = graph.get_concept("LivetestMetricOwner")
            assert concept is not None and concept.metrics == ("livetest_kpi__owned",)
            registry.delete("livetest_kpi__owned")
            registry.mirror(driver)
            concept = graph.get_concept("LivetestMetricOwner")
            assert concept is not None and concept.metrics == ()
        finally:
            registry.delete("livetest_kpi__owned")
            with driver.session() as s:
                s.run(
                    "MATCH (c:SemanticConcept {name:'LivetestMetricOwner'}) DETACH DELETE c"
                )

    def test_an_accepted_kpi_proposal_is_adopted_into_the_registry(
        self, proposals: ProposalStore, control_plane: Any
    ) -> None:
        from egpt_retail.kpi.store import KpiRegistryStore

        registry = KpiRegistryStore(control_plane)
        registry.ensure_schema()
        proposal = Proposal(
            kind=ProposalKind.KPI,
            target="livetest_kpi__adopted",
            payload={
                "identifier": "livetest_kpi__adopted",
                "name": "Adopted",
                "sql": "SELECT count(*) FROM crm_contact",
                "grain": "overall",
                "business_area": "customer",
                "source_tables": ["crm_contact"],
            },
            proposer=PROPOSER,
        )
        proposals.propose(proposal)
        proposals.accept([proposal.identifier], "livetest_reviewer")
        try:
            adopted = registry.adopt_accepted(proposals)
            assert adopted == ("livetest_kpi__adopted",)
            assert registry.get("livetest_kpi__adopted") is not None
            row = proposals.get(proposal.identifier)
            assert row is not None and row.applied_at is not None
            # Idempotent: a second pass adopts nothing.
            assert registry.adopt_accepted(proposals) == ()
        finally:
            registry.delete("livetest_kpi__adopted")

    def test_one_bad_kpi_proposal_does_not_block_another_from_adopting(
        self, proposals: ProposalStore, control_plane: Any
    ) -> None:
        """Round-2 review finding: a whole-batch validation used to raise for every
        accepted-and-unapplied KPI proposal over one bad payload, misattributing the
        failure to whatever proposal a later, unrelated accept happened to decide.
        ``adopt_accepted`` now isolates the failure to the row that actually caused it."""
        from egpt_retail.kpi.store import KpiRegistryStore

        registry = KpiRegistryStore(control_plane)
        registry.ensure_schema()
        bad = Proposal(
            kind=ProposalKind.KPI,
            target="livetest_kpi__bad_area",
            payload={
                "identifier": "livetest_kpi__bad_area",
                "name": "Bad area",
                "sql": "SELECT count(*) FROM crm_contact",
                "grain": "overall",
                "business_area": "not_a_real_business_area",
                "source_tables": ["crm_contact"],
            },
            proposer=PROPOSER,
        )
        good = Proposal(
            kind=ProposalKind.KPI,
            target="livetest_kpi__good_area",
            payload={
                "identifier": "livetest_kpi__good_area",
                "name": "Good area",
                "sql": "SELECT count(*) FROM crm_contact",
                "grain": "overall",
                "business_area": "customer",
                "source_tables": ["crm_contact"],
            },
            proposer=PROPOSER,
        )
        proposals.propose_many([bad, good])
        proposals.accept([bad.identifier, good.identifier], "livetest_reviewer")
        try:
            adopted = registry.adopt_accepted(proposals)
            assert adopted == ("livetest_kpi__good_area",)
            assert registry.get("livetest_kpi__good_area") is not None
            assert registry.get("livetest_kpi__bad_area") is None

            good_row = proposals.get(good.identifier)
            assert good_row is not None
            assert good_row.applied_at is not None
            assert good_row.apply_error is None

            bad_row = proposals.get(bad.identifier)
            assert bad_row is not None
            assert bad_row.applied_at is None
            assert bad_row.apply_error is not None
            assert "not_a_real_business_area" in bad_row.apply_error
        finally:
            registry.delete("livetest_kpi__good_area")

    def test_the_bad_proposal_is_isolated_regardless_of_pending_order(
        self, proposals: ProposalStore, control_plane: Any
    ) -> None:
        """Same isolation as above, ``[good, bad]`` this time -- pins per-proposal isolation
        rather than a mutation that only ever attributes to ``pending[0]``."""
        from egpt_retail.kpi.store import KpiRegistryStore

        registry = KpiRegistryStore(control_plane)
        registry.ensure_schema()
        good = Proposal(
            kind=ProposalKind.KPI,
            target="livetest_kpi__good_area_2",
            payload={
                "identifier": "livetest_kpi__good_area_2",
                "name": "Good area",
                "sql": "SELECT count(*) FROM crm_contact",
                "grain": "overall",
                "business_area": "customer",
                "source_tables": ["crm_contact"],
            },
            proposer=PROPOSER,
        )
        bad = Proposal(
            kind=ProposalKind.KPI,
            target="livetest_kpi__bad_area_2",
            payload={
                "identifier": "livetest_kpi__bad_area_2",
                "name": "Bad area",
                "sql": "SELECT count(*) FROM crm_contact",
                "grain": "overall",
                "business_area": "not_a_real_business_area",
                "source_tables": ["crm_contact"],
            },
            proposer=PROPOSER,
        )
        proposals.propose_many([good, bad])
        proposals.accept([good.identifier, bad.identifier], "livetest_reviewer")
        try:
            adopted = registry.adopt_accepted(proposals)
            assert adopted == ("livetest_kpi__good_area_2",)

            good_row = proposals.get(good.identifier)
            assert good_row is not None
            assert good_row.applied_at is not None
            assert good_row.apply_error is None

            bad_row = proposals.get(bad.identifier)
            assert bad_row is not None
            assert bad_row.applied_at is None
            assert bad_row.apply_error is not None
        finally:
            registry.delete("livetest_kpi__good_area_2")

    def test_two_bad_kpi_proposals_each_get_their_own_apply_error(
        self, proposals: ProposalStore, control_plane: Any
    ) -> None:
        """Two distinct bad payloads must not be conflated into one generic failure -- each
        row's ``apply_error`` names its own actual problem."""
        from egpt_retail.kpi.store import KpiRegistryStore

        registry = KpiRegistryStore(control_plane)
        registry.ensure_schema()
        bad_area = Proposal(
            kind=ProposalKind.KPI,
            target="livetest_kpi__bad_area_3",
            payload={
                "identifier": "livetest_kpi__bad_area_3",
                "name": "Bad area",
                "sql": "SELECT count(*) FROM crm_contact",
                "grain": "overall",
                "business_area": "not_a_real_business_area",
                "source_tables": ["crm_contact"],
            },
            proposer=PROPOSER,
        )
        bad_grain = Proposal(
            kind=ProposalKind.KPI,
            target="livetest_kpi__bad_grain_3",
            payload={
                "identifier": "livetest_kpi__bad_grain_3",
                "name": "Bad grain",
                "sql": "SELECT count(*) FROM crm_contact",
                "grain": "not_a_real_grain",
                "business_area": "customer",
                "source_tables": ["crm_contact"],
            },
            proposer=PROPOSER,
        )
        proposals.propose_many([bad_area, bad_grain])
        proposals.accept(
            [bad_area.identifier, bad_grain.identifier], "livetest_reviewer"
        )
        adopted = registry.adopt_accepted(proposals)
        assert adopted == ()

        area_row = proposals.get(bad_area.identifier)
        assert area_row is not None and area_row.apply_error is not None
        assert "not_a_real_business_area" in area_row.apply_error

        grain_row = proposals.get(bad_grain.identifier)
        assert grain_row is not None and grain_row.apply_error is not None
        assert "not_a_real_grain" in grain_row.apply_error
        assert grain_row.apply_error != area_row.apply_error

    def test_record_apply_error_recovers_from_an_aborted_transaction(
        self, proposals: ProposalStore, control_plane: Any
    ) -> None:
        """Round-3 review's Critical finding: a write failure inside ``adopt_accepted``'s
        per-proposal loop (e.g. ``mark_applied`` itself raising on a connection blip, not a
        bad payload) leaves Postgres refusing every further statement on that same connection
        -- including the one recording that very failure -- until the aborted transaction is
        rolled back. Reproduces the aborted state directly (a runtime error inside a query,
        the same way a real write failure would leave it) rather than mocking a DB failure."""
        proposal = Proposal(
            kind=ProposalKind.KPI,
            target="livetest_kpi__poisoned",
            payload={
                "identifier": "livetest_kpi__poisoned",
                "name": "Poisoned",
                "sql": "SELECT count(*) FROM crm_contact",
                "grain": "overall",
                "business_area": "customer",
                "source_tables": ["crm_contact"],
            },
            proposer=PROPOSER,
        )
        proposals.propose(proposal)
        proposals.accept([proposal.identifier], "livetest_reviewer")

        with pytest.raises(Exception):
            with control_plane.cursor() as cursor:
                cursor.execute("SELECT 1/0")

        # Without record_apply_error's own rollback, this statement would itself raise
        # InFailedSqlTransaction rather than recording anything.
        proposals.record_apply_error(proposal.identifier, "simulated DB failure")

        row = proposals.get(proposal.identifier)
        assert row is not None
        assert row.apply_error == "simulated DB failure"
        assert row.applied_at is None

    def test_refresh_puts_a_registered_kpi_into_review_and_stamps_the_rest(
        self, control_plane: Any, driver: Any
    ) -> None:
        """K6 and K11 were each half of this; the refresh now joins them."""
        from egpt_retail.contracts.kpi import KpiDefinition
        from egpt_retail.kpi.store import KpiRegistryStore

        psycopg = pytest.importorskip("psycopg")
        warehouse = psycopg.connect(WAREHOUSE)
        warehouse.autocommit = True
        registry = KpiRegistryStore(control_plane)
        registry.ensure_schema()
        touched = KpiDefinition(
            identifier="livetest_kpi__touched",
            name="t",
            description="d",
            sql="SELECT count(*) FROM crm_deal WHERE livetest_col IS NOT NULL",
            grain="overall",
            business_area="sales",
            source_tables=frozenset({"crm_deal"}),
        )
        untouched = KpiDefinition(
            identifier="livetest_kpi__untouched",
            name="u",
            description="d",
            sql="SELECT count(*) FROM crm_contact",
            grain="overall",
            business_area="customer",
            source_tables=frozenset({"crm_contact"}),
        )
        try:
            with warehouse.cursor() as cur:
                cur.execute(
                    "ALTER TABLE silver.crm_deal ADD COLUMN IF NOT EXISTS livetest_col text"
                )
            refresh_semantic_layer(
                version="livetest.rv1",
                warehouse=warehouse,
                control_plane=control_plane,
                driver=driver,
            )
            registry.save_many([touched, untouched])
            with warehouse.cursor() as cur:
                cur.execute(
                    "ALTER TABLE silver.crm_deal DROP COLUMN IF EXISTS livetest_col"
                )
            report = refresh_semantic_layer(
                version="livetest.rv2",
                warehouse=warehouse,
                control_plane=control_plane,
                driver=driver,
            )
            assert {i.kpi_identifier for i in report.review} == {
                "livetest_kpi__touched"
            }
            assert (
                _stored_definition(
                    registry, "livetest_kpi__untouched"
                ).validated_against_snapshot
                == "livetest.rv2"
            )
            # The one in review keeps its old stamp, so K6's screen can say how far behind it is.
            assert (
                _stored_definition(
                    registry, "livetest_kpi__touched"
                ).validated_against_snapshot
                != "livetest.rv2"
            )
        finally:
            registry.delete("livetest_kpi__touched")
            registry.delete("livetest_kpi__untouched")
            with warehouse.cursor() as cur:
                cur.execute(
                    "ALTER TABLE silver.crm_deal DROP COLUMN IF EXISTS livetest_col"
                )
            refresh_semantic_layer(
                version="livetest.rv3",
                warehouse=warehouse,
                control_plane=control_plane,
                driver=driver,
            )
            warehouse.close()


class TestSemanticProposalsHttp:
    """#235/F2's HTTP path: propose a KPI or use-case proposal directly via `ProposalStore`
    (simulating what `egpt-retail suggest` already does), then drive the review entirely
    through the mounted `api/routers/semantic.py` -- and, for the KPI case, prove the loop
    actually closes by reading the adopted definition back through the real `kpis.py` router
    a caller would use, the same "prove it end to end" bar C1's dashboard test set.

    A bare `FastAPI()` with just these two routers mounted, dependency-overridden onto the
    live `control_plane` connection this module's other tests already share -- real Postgres,
    real FastAPI request dispatch, real route functions -- without needing the full
    `build_agent_os` assembly (agents, memory provider, JWKS) that this router's own logic
    does not touch. Authorization is off (no `AuthMiddleware` on this bare app), so every
    decision here is attributed to `"local"`, matching every other DB-free/live test in this
    suite that does not itself exercise G1's auth path.
    """

    @pytest.fixture
    def client(self, control_plane: Any) -> TestClient:
        app = FastAPI()
        app.include_router(semantic_module.router)
        app.include_router(kpis_module.router)

        def _proposals() -> ProposalStore:
            return ProposalStore(control_plane)

        def _kpi_store() -> KpiRegistryStore:
            return KpiRegistryStore(control_plane)

        app.dependency_overrides[semantic_module._proposal_store] = _proposals
        app.dependency_overrides[semantic_module._kpi_store] = _kpi_store
        app.dependency_overrides[kpis_module._kpi_store] = _kpi_store
        return TestClient(app)

    def test_the_review_loop_closes_from_a_kpi_proposal_to_a_real_get_kpis(
        self, client: TestClient, proposals: ProposalStore, control_plane: Any
    ) -> None:
        registry = KpiRegistryStore(control_plane)
        registry.ensure_schema()
        proposal = Proposal(
            kind=ProposalKind.KPI,
            target="livetest_kpi__http_adopted",
            payload={
                "identifier": "livetest_kpi__http_adopted",
                "name": "HTTP Adopted",
                "sql": "SELECT count(*) FROM crm_contact",
                "grain": "overall",
                "business_area": "customer",
                "source_tables": ["crm_contact"],
                "rationale": "Livetest proof of the review loop.",
            },
            proposer=PROPOSER,
        )
        proposals.propose(proposal)
        try:
            listed = client.get(
                "/semantic/proposals", params={"state": "pending", "kind": "kpi"}
            )
            assert listed.status_code == 200
            ids = [p["id"] for p in listed.json()["proposals"]]
            assert proposal.identifier in ids

            accepted = client.post(
                f"/semantic/proposals/{proposal.identifier}/accept",
                json={"note": "Looks right."},
            )
            assert accepted.status_code == 200
            body = accepted.json()
            assert body["state"] == "accepted"
            assert body["applied_at"] is not None, (
                "accept must adopt a KPI in the same call"
            )

            # The loop: `GET /kpis/{id}` through the real kpis router now finds it for real.
            got = client.get("/kpis/livetest_kpi__http_adopted")
            assert got.status_code == 200
            assert got.json()["identifier"] == "livetest_kpi__http_adopted"

            # A second accept of the same, now-decided id is refused, not silently re-applied.
            again = client.post(f"/semantic/proposals/{proposal.identifier}/accept")
            assert again.status_code == 409
        finally:
            registry.delete("livetest_kpi__http_adopted")

    def test_a_use_case_proposal_accepts_but_stays_unapplied_no_consumer_exists(
        self, client: TestClient, proposals: ProposalStore
    ) -> None:
        proposal = Proposal(
            kind=ProposalKind.USE_CASE,
            target="livetest-use-case-http",
            payload={"question": "Livetest question?"},
            proposer=PROPOSER,
        )
        proposals.propose(proposal)
        accepted = client.post(f"/semantic/proposals/{proposal.identifier}/accept")
        assert accepted.status_code == 200
        body = accepted.json()
        assert body["state"] == "accepted"
        assert body["applied_at"] is None, (
            "no consumer exists for an accepted use case yet"
        )

    def test_reject_then_a_later_accept_attempt_409s(
        self, client: TestClient, proposals: ProposalStore
    ) -> None:
        proposal = Proposal(
            kind=ProposalKind.USE_CASE,
            target="livetest-use-case-reject",
            payload={"question": "Livetest reject question?"},
            proposer=PROPOSER,
        )
        proposals.propose(proposal)
        rejected = client.post(f"/semantic/proposals/{proposal.identifier}/reject")
        assert rejected.status_code == 200
        assert rejected.json()["state"] == "rejected"

        again = client.post(f"/semantic/proposals/{proposal.identifier}/accept")
        assert again.status_code == 409

    def test_bulk_accept_partially_commits_when_one_id_is_already_decided(
        self, client: TestClient, proposals: ProposalStore, control_plane: Any
    ) -> None:
        registry = KpiRegistryStore(control_plane)
        registry.ensure_schema()
        live = Proposal(
            kind=ProposalKind.KPI,
            target="livetest_kpi__bulk_http",
            payload={
                "identifier": "livetest_kpi__bulk_http",
                "name": "Bulk HTTP",
                "sql": "SELECT count(*) FROM crm_contact",
                "grain": "overall",
                "business_area": "customer",
                "source_tables": ["crm_contact"],
            },
            proposer=PROPOSER,
        )
        already_rejected = Proposal(
            kind=ProposalKind.USE_CASE,
            target="livetest-use-case-bulk-http",
            payload={"question": "Already decided?"},
            proposer=PROPOSER,
        )
        proposals.propose(live)
        proposals.propose(already_rejected)
        proposals.reject([already_rejected.identifier], "livetest_other_reviewer")
        try:
            response = client.post(
                "/semantic/proposals/accept",
                json={"ids": [live.identifier, already_rejected.identifier]},
            )
            assert response.status_code == 200
            body = response.json()
            assert [p["id"] for p in body["proposals"]] == [live.identifier]
            assert [r["id"] for r in body["refused"]] == [already_rejected.identifier]
            assert body["refused"][0]["decided_by"] == "livetest_other_reviewer"
            # the live id still adopted despite the other id in the same request refusing.
            assert registry.get("livetest_kpi__bulk_http") is not None
        finally:
            registry.delete("livetest_kpi__bulk_http")


class TestSourceScoping:
    """#307: a self-serve source's tables share the graph with the operator's, and reads are
    scoped by ``source_id`` so neither sees the other. The synthetic schema deliberately reuses
    a bare table name silver already has -- the exact case that used to make a bare-name lookup
    ambiguous the moment a customer harvested a table called ``crm_contact``."""

    SCHEMA = "livetest_src"
    SOURCE = "livetest_ds_1"

    def _source_harvest(self) -> Harvest:
        from egpt_retail.semantic.harvest import HarvestedColumn, HarvestedTable

        return Harvest(
            schema=self.SCHEMA,
            tables=(
                HarvestedTable(
                    schema=self.SCHEMA,
                    name="crm_contact",
                    columns=(
                        HarvestedColumn("livetest_only_col", "integer", False, True),
                        HarvestedColumn("livetest_join_key", "integer", False, True),
                    ),
                    row_count=3,
                ),
                HarvestedTable(
                    schema=self.SCHEMA,
                    name="livetest_widget",
                    columns=(
                        HarvestedColumn("id", "integer", False, True),
                        HarvestedColumn("livetest_join_key", "integer", False, True),
                    ),
                    row_count=1,
                ),
            ),
            source="livetest",
        )

    @pytest.fixture
    def source_graph(self, graph: SemanticGraphStore, driver: Any) -> Iterator[Any]:
        graph.write_harvest(
            self._source_harvest(),
            snapshot_version="livetest.src",
            source_id=self.SOURCE,
        )
        try:
            yield SemanticGraphStore(driver=driver, source_id=self.SOURCE)
        finally:
            graph.drop_schema(self.SCHEMA)

    def test_the_default_scope_never_lists_a_sources_tables(
        self, graph: SemanticGraphStore, source_graph: Any
    ) -> None:
        schemas = {ref.schema for ref in graph.list_tables()}
        assert self.SCHEMA not in schemas
        assert "livetest_widget" not in {ref.name for ref in graph.list_tables()}

    def test_a_sources_scope_lists_only_its_own_tables(
        self, graph: SemanticGraphStore, source_graph: Any
    ) -> None:
        refs = source_graph.list_tables()
        assert {(r.schema, r.name) for r in refs} == {
            (self.SCHEMA, "crm_contact"),
            (self.SCHEMA, "livetest_widget"),
        }

    def test_a_shared_bare_name_is_not_ambiguous_in_either_scope(
        self, graph: SemanticGraphStore, source_graph: Any
    ) -> None:
        """Before scoping this raised 'names a table in more than one schema'."""
        operator = graph.get_table("crm_contact")
        source = source_graph.get_table("crm_contact")
        assert operator is not None and operator.ref.schema == "silver"
        assert source is not None and source.ref.schema == self.SCHEMA
        assert "livetest_only_col" in {c.name for c in source.columns}
        assert "livetest_only_col" not in {c.name for c in operator.columns}

    def test_column_index_and_feasibility_inputs_do_not_cross(
        self, graph: SemanticGraphStore, source_graph: Any
    ) -> None:
        assert not any(k.startswith(f"{self.SCHEMA}.") for k in graph.column_index())
        assert set(source_graph.column_index()) == {
            f"{self.SCHEMA}.crm_contact",
            f"{self.SCHEMA}.livetest_widget",
        }
        operator_names = {ref.name for ref in graph.list_tables()}
        assert "livetest_widget" not in operator_names

    def test_an_operator_table_is_unknown_to_a_sources_scope(
        self, source_graph: Any
    ) -> None:
        assert source_graph.get_table("silver.crm_deal") is None
        with pytest.raises(SemanticReadError, match="not a table"):
            source_graph.find_join_path("crm_deal", "livetest_widget")

    def test_a_sources_harvest_leaves_the_operators_snapshot_pointer_alone(
        self, graph: SemanticGraphStore, driver: Any
    ) -> None:
        before = graph.snapshot()
        graph.write_harvest(
            self._source_harvest(),
            snapshot_version="livetest.src2",
            source_id=self.SOURCE,
        )
        try:
            assert graph.snapshot() == before
        finally:
            graph.drop_schema(self.SCHEMA)

    def test_source_snapshot_version_reads_off_its_own_tables(
        self, graph: SemanticGraphStore, source_graph: Any
    ) -> None:
        assert source_graph.source_snapshot_version() == "livetest.src"
        empty = SemanticGraphStore(
            driver=source_graph._driver, source_id="livetest_none"
        )
        assert empty.source_snapshot_version() is None

    def test_the_collision_guard_names_the_owner_and_spares_the_owner(
        self, graph: SemanticGraphStore, source_graph: Any
    ) -> None:
        assert graph.schema_owned_by_other_source(self.SCHEMA, self.SOURCE) is None
        assert (
            graph.schema_owned_by_other_source(self.SCHEMA, "livetest_ds_other")
            == self.SOURCE
        )

    @pytest.fixture
    def mapped_scopes(
        self, graph: SemanticGraphStore, source_graph: Any, driver: Any
    ) -> Iterator[None]:
        """Concepts mapped so each scope holds something the other must not see: one concept
        mapped in *both* (a ``crm_contact`` in silver and one in the source's schema), plus one
        mapped only in each. A filter on the concept side rather than the table side would pass
        the only-in-one cases and fail the shared one."""
        graph.write_concept(
            Concept(
                name="LivetestScopeShared",
                description="x",
                business_area="livetest_area_shared",
            ),
            tables=("silver.crm_contact", f"{self.SCHEMA}.crm_contact"),
        )
        graph.write_concept(
            Concept(
                name="LivetestScopeOnlyOp",
                description="x",
                business_area="livetest_area_op",
            ),
            tables=("silver.crm_deal",),
        )
        graph.write_concept(
            Concept(
                name="LivetestScopeOnlySrc",
                description="x",
                business_area="livetest_area_src",
            ),
            tables=(f"{self.SCHEMA}.livetest_widget",),
        )
        try:
            yield
        finally:
            with driver.session() as s:
                s.run(
                    "MATCH (c:SemanticConcept) WHERE c.name STARTS WITH 'LivetestScope' "
                    "DETACH DELETE c"
                )

    def test_mapped_concepts_do_not_cross(
        self, graph: SemanticGraphStore, source_graph: Any, mapped_scopes: None
    ) -> None:
        operator = graph.mapped_concepts()
        source = source_graph.mapped_concepts()
        assert operator["LivetestScopeShared"] == ["silver.crm_contact"]
        assert source["LivetestScopeShared"] == [f"{self.SCHEMA}.crm_contact"]
        assert "LivetestScopeOnlyOp" in operator and "LivetestScopeOnlyOp" not in source
        assert (
            "LivetestScopeOnlySrc" in source and "LivetestScopeOnlySrc" not in operator
        )

    def test_business_area_evidence_does_not_cross(
        self, graph: SemanticGraphStore, source_graph: Any, mapped_scopes: None
    ) -> None:
        """The K9 path: an area is 'supported' by a mapped table, so a customer's mapped table
        must not make an operator area look supported."""
        operator = graph.business_area_evidence()
        source = source_graph.business_area_evidence()
        assert operator["livetest_area_shared"]["tables"] == ["silver.crm_contact"]
        assert source["livetest_area_shared"]["tables"] == [
            f"{self.SCHEMA}.crm_contact"
        ]
        assert "livetest_area_op" in operator and "livetest_area_op" not in source
        assert "livetest_area_src" in source and "livetest_area_src" not in operator

    def test_unjoinable_oracle_does_not_cross(
        self, graph: SemanticGraphStore, source_graph: Any, driver: Any
    ) -> None:
        """A pair review ruled out in one scope is not ruled out, or in, the other. The oracle
        is keyed on bare table names, so the source's ``crm_contact`` and silver's would be one
        pair if the scopes leaked."""
        graph.write_concept(Concept(name="LivetestScopeOracle", description="x"))
        try:
            for store, tables, column in (
                (graph, ("silver.crm_contact", "silver.crm_deal"), "load_id"),
                (
                    source_graph,
                    (f"{self.SCHEMA}.crm_contact", f"{self.SCHEMA}.livetest_widget"),
                    "livetest_join_key",
                ),
            ):
                for table in tables:
                    store.write_column_meaning(
                        table,
                        column,
                        concept="LivetestScopeOracle",
                        meaning="ruled out by review",
                        joinable=False,
                    )
            operator = graph.unjoinable_oracle()
            source = source_graph.unjoinable_oracle()
            assert operator("crm_contact", "crm_deal") is True
            assert source("crm_contact", "livetest_widget") is True
            # Each pair exists only in the other scope, so a leak is the only way to answer True.
            assert source("crm_contact", "crm_deal") is False
            assert operator("crm_contact", "livetest_widget") is False
        finally:
            with driver.session() as s:
                s.run(
                    "MATCH (c:SemanticColumn)-[:OF_TABLE]->(t:SemanticTable) "
                    "WHERE c.concept = 'LivetestScopeOracle' "
                    "REMOVE c.concept, c.meaning, c.joinable"
                )
                s.run(
                    "MATCH (c:SemanticConcept {name:'LivetestScopeOracle'}) DETACH DELETE c"
                )

    def test_find_join_path_never_crosses_scopes_mid_path(
        self, graph: SemanticGraphStore, source_graph: Any, driver: Any
    ) -> None:
        """Both endpoints in scope, the only short route through a table that is not.

        ``write_join`` cannot author such an edge (it resolves both ends in its own scope), so
        it is written with raw Cypher: the point is that the read holds even if a write-side
        slip ever produced one. The second half adds a longer route that stays in scope and
        expects it to be found, so the filter must apply during the search rather than
        discarding a shortest path afterwards and reporting 'no path'.
        """
        cross = "livetest_cross"

        def connect(session: Any, a: str, b: str, tag: str) -> None:
            session.run(
                "MATCH (x:SemanticTable {schema: $sa, name: $na}), "
                "(y:SemanticTable {schema: $sb, name: $nb}) "
                "CREATE (x)-[:JOINS_TO {on: $on, cardinality: 'many-to-one', "
                "status: 'accepted'}]->(y)",
                sa=a.split(".")[0],
                na=a.split(".")[1],
                sb=b.split(".")[0],
                nb=b.split(".")[1],
                on=f"{cross}_{tag}",
            )

        foreign = f"{self.SCHEMA}.livetest_widget"
        try:
            with driver.session() as s:
                connect(s, "silver.analytics_event", foreign, "a")
                connect(s, foreign, "silver.session_replay_session", "b")
                # The source's mirror image: silver.crm_deal sits between two of its tables.
                connect(s, f"{self.SCHEMA}.crm_contact", "silver.crm_deal", "c")
                connect(s, "silver.crm_deal", foreign, "d")

            assert (
                graph.find_join_path("analytics_event", "session_replay_session")
                is None
            )
            assert source_graph.find_join_path("crm_contact", "livetest_widget") is None

            for left, right, tag in (
                ("analytics_event", "crm_contact", "e"),
                ("crm_contact", "crm_deal", "f"),
                ("crm_deal", "session_replay_session", "g"),
            ):
                graph.write_join(JoinEdge(left, right, f"{cross}_{tag}.x = y.x"))
            path = graph.find_join_path("analytics_event", "session_replay_session")
            assert path is not None
            assert [(e.left_table, e.right_table) for e in path.edges] == [
                ("analytics_event", "crm_contact"),
                ("crm_contact", "crm_deal"),
                ("crm_deal", "session_replay_session"),
            ]
            assert "livetest_widget" not in {
                t for e in path.edges for t in (e.left_table, e.right_table)
            }
        finally:
            with driver.session() as s:
                s.run(
                    "MATCH ()-[j:JOINS_TO]->() WHERE j.on CONTAINS $p DELETE j",
                    p=cross,
                )
