"""P2 verification -- the DB-free parts of what the review fixed.

Each test here pins a defect the verification found, so the fix cannot quietly regress.
The graph-side defects (a re-harvest wiping reviewed edges; a failed apply blocking the
queue) are proved by the cookbooks against real stores.
"""

from __future__ import annotations

from typing import Any, Dict

import pytest
from egpt_retail.app.settings import RetailSettings
from egpt_retail.contracts.semantic import JoinEdge
from egpt_retail.contracts.vocabulary import load_vocabulary_set
from egpt_retail.entrypoints.__main__ import COMMANDS, build_parser
from egpt_retail.semantic.harvest import HarvestDiff
from egpt_retail.semantic.proposals import (
    CARDINALITIES,
    Proposal,
    ApplyOutcome,
    PayloadError,
    ProposalKind,
    Refusal,
    validate_payload,
)
from egpt_retail.semantic.refresh import RefreshReport
from egpt_retail.semantic.snapshots import structural
from pydantic import ValidationError


class TestClosedSetsAtTheGate:
    def test_business_area_outside_the_vocabulary_is_refused(self) -> None:
        """'marketng' parses, builds a Concept, and once in the graph is an area no screen
        lists and no KPI matches. The proposal gate is where the closed set is enforced."""
        with pytest.raises(PayloadError, match="not a permitted business_area"):
            validate_payload(
                ProposalKind.ENRICHMENT,
                {"name": "Campaign", "business_area": "marketng"},
                vocabularies=load_vocabulary_set(),
            )

    def test_a_permitted_business_area_passes(self) -> None:
        vocabularies = load_vocabulary_set()
        permitted = next(iter(sorted(vocabularies["business_area"].values)))
        built = validate_payload(
            ProposalKind.ENRICHMENT,
            {"name": "Campaign", "business_area": permitted},
            vocabularies=vocabularies,
        )
        assert built.business_area == permitted

    @pytest.mark.parametrize("bad", ["1:n", "many", "MANY-TO-ONE", ""])
    def test_cardinality_outside_the_set_is_refused(self, bad: str) -> None:
        with pytest.raises(PayloadError, match="cardinality"):
            validate_payload(
                ProposalKind.JOIN,
                {
                    "left_table": "a",
                    "right_table": "b",
                    "on": "a.x = b.x",
                    "cardinality": bad,
                },
            )

    def test_every_cardinality_in_the_set_passes(self) -> None:
        for cardinality in CARDINALITIES:
            validate_payload(
                ProposalKind.JOIN,
                {
                    "left_table": "a",
                    "right_table": "b",
                    "on": "a.x = b.x",
                    "cardinality": cardinality,
                },
            )


class TestApplyOutcome:
    def test_reports_both_sides(self) -> None:
        outcome = ApplyOutcome(
            applied=("a",), failed=(Refusal("b", "no such concept"),)
        )
        assert str(outcome) == "1 applied, 1 failed"


class TestRowsStayInTheWarehouse:
    def test_structural_strips_sampled_values_and_nothing_else(self) -> None:
        payload: Dict[str, Any] = {
            "schema": "silver",
            "tables": [
                {
                    "name": "crm_contact",
                    "columns": [
                        {
                            "name": "email",
                            "data_type": "text",
                            "sampled_values": ["a@x", "b@y"],
                        },
                        {"name": "brand", "data_type": "text", "sampled_values": []},
                    ],
                }
            ],
        }
        stripped = structural(payload)
        columns = stripped["tables"][0]["columns"]
        assert all("sampled_values" not in column for column in columns)
        assert [c["name"] for c in columns] == ["email", "brand"]
        assert columns[0]["data_type"] == "text"
        # The input is not mutated: a caller still holding the harvest keeps its samples.
        assert payload["tables"][0]["columns"][0]["sampled_values"] == ["a@x", "b@y"]


class TestMemoryTableName:
    @pytest.mark.parametrize("bad", ["memory.vectors", "Memory", "drop table; --", ""])
    def test_a_non_identifier_is_refused_at_settings_time(self, bad: str) -> None:
        with pytest.raises(ValidationError, match="EGPT_RETAIL_MEMORY_TABLE"):
            RetailSettings(retail_memory_table=bad)

    def test_the_default_and_a_cookbook_name_pass(self) -> None:
        assert RetailSettings().retail_memory_table == "memory_vectors"
        assert (
            RetailSettings(
                retail_memory_table="memory_vectors_k1_cookbook"
            ).retail_memory_table
            == "memory_vectors_k1_cookbook"
        )


class TestHarvestEntrypoint:
    def test_harvest_is_a_command(self) -> None:
        assert "harvest" in COMMANDS
        args = build_parser().parse_args(
            ["harvest", "--schema", "gold", "--version", "v9"]
        )
        assert (args.command, args.schema, args.version) == ("harvest", "gold", "v9")

    def test_report_lines_say_what_happened(self) -> None:
        report = RefreshReport(
            version="v2",
            previous_version="v1",
            schema="silver",
            tables=5,
            columns=40,
            diff=HarvestDiff(removed_columns=("crm_contact.brand",)),
        )
        lines = report.lines()
        assert report.changed
        assert any("v2" in line and "v1" in line for line in lines)
        assert any(
            line.startswith("drift:") and "crm_contact.brand" in line for line in lines
        )

    def test_a_first_run_has_no_diff(self) -> None:
        report = RefreshReport("v1", None, "silver", 5, 40, diff=None)
        assert not report.changed
        assert any("no previous snapshot" in line for line in report.lines())


class TestCanonicalJoin:
    """A join is undirected in the graph; the stored edge runs from the smaller name."""

    def test_already_canonical_is_untouched(self) -> None:
        from egpt_retail.semantic.store import canonical_join

        edge = JoinEdge("a_table", "b_table", "a.x = b.x", "many-to-one")
        assert canonical_join(edge) is edge

    @pytest.mark.parametrize(
        "given,expected",
        [
            ("many-to-one", "one-to-many"),
            ("one-to-many", "many-to-one"),
            ("one-to-one", "one-to-one"),
            ("many-to-many", "many-to-many"),
        ],
    )
    def test_a_swapped_pair_inverts_cardinality_and_keeps_the_predicate(
        self, given: str, expected: str
    ) -> None:
        from egpt_retail.semantic.store import canonical_join

        edge = canonical_join(JoinEdge("b_table", "a_table", "b.x = a.x", given))
        assert (edge.left_table, edge.right_table) == ("a_table", "b_table")
        assert edge.cardinality == expected
        assert edge.on == "b.x = a.x"


# --- DB-free control-flow tests for the two headline fixes review round 2 found untested ---


class _RecordingCursor:
    def __init__(self, log: list) -> None:
        self.log = log
        self.rowcount = 1

    def __enter__(self) -> "_RecordingCursor":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, sql: str, params: object = None) -> None:
        self.log.append((" ".join(sql.split()), params))

    def fetchall(self) -> list:
        return []

    def fetchone(self) -> None:
        return None


class _RecordingConnection:
    def __init__(self) -> None:
        self.log: list = []
        self.commits = 0

    def cursor(self) -> _RecordingCursor:
        return _RecordingCursor(self.log)

    def commit(self) -> None:
        self.commits += 1


class _GraphThatRejectsOne:
    """A graph store where one concept's write fails and the rest succeed."""

    def __init__(self, failing: str) -> None:
        self.failing = failing
        self.written: list = []

    def write_concept(self, concept: object, *, proposal_id: str = "") -> None:
        name = getattr(concept, "name")
        if name == self.failing:
            raise RuntimeError(f"{name} cannot be written yet")
        self.written.append((name, proposal_id))


class TestApplyAcceptedControlFlow:
    """`apply_accepted` must keep going past a failure and record it on the row.

    Review round 2 measured that reverting this to raise-on-first-failure left every unit
    test green. These run against a recording connection, so they run in CI's unit job.
    """

    def _store(self, monkeypatch: pytest.MonkeyPatch, rows: list) -> tuple:
        from egpt_retail.semantic.proposals import ProposalStore

        connection = _RecordingConnection()
        store = ProposalStore(connection)
        monkeypatch.setattr(store, "list", lambda **_: tuple(rows))
        return store, connection

    def _accepted(self, name: str) -> "Proposal":
        from egpt_retail.semantic.proposals import Proposal, ProposalKind, ReviewState

        return Proposal(
            kind=ProposalKind.ENRICHMENT,
            target=name,
            payload={"name": name},
            proposer="agent",
            state=ReviewState.ACCEPTED,
            reviewer="reviewer",
        )

    def test_one_failure_does_not_stop_the_batch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stuck, fine = self._accepted("Stuck"), self._accepted("Fine")
        store, connection = self._store(monkeypatch, [stuck, fine])
        graph = _GraphThatRejectsOne("Stuck")

        outcome = store.apply_accepted(graph)

        assert outcome.applied == (fine.identifier,)
        assert [f.identifier for f in outcome.failed] == [stuck.identifier]
        assert "cannot be written yet" in outcome.failed[0].reason
        assert graph.written == [("Fine", fine.identifier)]

    def test_the_failure_is_recorded_and_the_success_stamped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stuck, fine = self._accepted("Stuck"), self._accepted("Fine")
        store, connection = self._store(monkeypatch, [stuck, fine])
        store.apply_accepted(_GraphThatRejectsOne("Stuck"))

        error_updates = [
            params for sql, params in connection.log if "SET apply_error = %s" in sql
        ]
        applied_updates = [
            params
            for sql, params in connection.log
            if "SET applied_at = now(), apply_error = NULL" in sql
        ]
        assert [p[1] for p in error_updates] == [stuck.identifier]
        assert [p[0] for p in applied_updates] == [fine.identifier]
        assert connection.commits == 2

    def test_a_retry_that_succeeds_clears_the_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stuck = self._accepted("Stuck")
        store, connection = self._store(monkeypatch, [stuck])
        store.apply_accepted(_GraphThatRejectsOne("Stuck"))
        store.apply_accepted(_GraphThatRejectsOne("SomethingElse"))
        clearing = [
            params
            for sql, params in connection.log
            if "apply_error = NULL" in sql and params[0] == stuck.identifier
        ]
        assert len(clearing) == 1

    def test_kinds_without_a_graph_target_are_left_alone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from egpt_retail.semantic.proposals import Proposal, ProposalKind, ReviewState

        kpi = Proposal(
            kind=ProposalKind.KPI,
            target="rfq_rate",
            payload={"identifier": "rfq_rate"},
            proposer="agent",
            state=ReviewState.ACCEPTED,
            reviewer="reviewer",
        )
        store, connection = self._store(monkeypatch, [kpi])
        outcome = store.apply_accepted(_GraphThatRejectsOne("none"))
        assert outcome.applied == () and outcome.failed == ()
        assert connection.log == []


class _FakeResult:
    def __init__(self, row: object) -> None:
        self._row = row

    def single(self) -> object:
        return self._row

    def data(self) -> list:
        return []


class _FakeTx:
    def __init__(self, held: int, log: list) -> None:
        self.held = held
        self.log = log

    def run(self, query: str, **params: object) -> _FakeResult:
        self.log.append((" ".join(query.split()), params))
        if "RETURN count(t) AS n" in query:
            return _FakeResult({"n": self.held})
        return _FakeResult(None)


class _FakeSession:
    def __init__(self, held: int, log: list) -> None:
        self.held = held
        self.log = log

    def __enter__(self) -> "_FakeSession":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute_write(self, fn: object, *args: object) -> object:
        return fn(_FakeTx(self.held, self.log), *args)  # type: ignore[operator]

    def run(self, query: str, **params: object) -> _FakeResult:
        return _FakeTx(self.held, self.log).run(query, **params)


class _FakeDriver:
    def __init__(self, held: int) -> None:
        self.held = held
        self.log: list = []

    def session(self, **_: object) -> _FakeSession:
        return _FakeSession(self.held, self.log)


class TestWriteHarvestGuardAndScope:
    """The empty-harvest refusal and the schema scoping, without a Neo4j."""

    def test_an_empty_harvest_is_refused_when_the_schema_is_held(self) -> None:
        from egpt_retail.semantic.harvest import Harvest
        from egpt_retail.semantic.store import EmptyHarvestRefused, SemanticGraphStore

        driver = _FakeDriver(held=5)
        with pytest.raises(EmptyHarvestRefused, match="drop_schema"):
            SemanticGraphStore(driver=driver).write_harvest(
                Harvest(schema="silver", tables=()), snapshot_version="v"
            )
        assert not any("DELETE" in sql for sql, _ in driver.log)

    def test_an_empty_harvest_of_a_schema_never_held_is_allowed(self) -> None:
        from egpt_retail.semantic.harvest import Harvest
        from egpt_retail.semantic.store import SemanticGraphStore

        driver = _FakeDriver(held=0)
        assert (
            SemanticGraphStore(driver=driver).write_harvest(
                Harvest(schema="brand_new", tables=()), snapshot_version="v"
            )
            == 0
        )

    def test_the_guard_counts_only_the_harvested_schema(self) -> None:
        from egpt_retail.semantic.harvest import Harvest
        from egpt_retail.semantic.store import SemanticGraphStore

        driver = _FakeDriver(held=0)
        SemanticGraphStore(driver=driver).write_harvest(
            Harvest(schema="gold", tables=()), snapshot_version="v"
        )
        count_queries = [
            (sql, params) for sql, params in driver.log if "RETURN count(t) AS n" in sql
        ]
        assert count_queries and all(
            p.get("schema") == "gold" for _, p in count_queries
        )

    def test_every_prune_is_bound_to_the_harvested_schema(self) -> None:
        """Round 2's headline: a prune over every SemanticTable removes other schemas."""
        from egpt_retail.semantic.harvest import (
            Harvest,
            HarvestedColumn,
            HarvestedTable,
        )
        from egpt_retail.semantic.store import SemanticGraphStore

        driver = _FakeDriver(held=0)
        harvest = Harvest(
            schema="gold",
            tables=(
                HarvestedTable(
                    schema="gold",
                    name="orders",
                    columns=(HarvestedColumn("id", "integer", False, True),),
                    row_count=1,
                ),
            ),
        )
        SemanticGraphStore(driver=driver).write_harvest(harvest, snapshot_version="v")
        prunes = [(sql, params) for sql, params in driver.log if "DETACH DELETE" in sql]
        assert len(prunes) == 2
        for sql, params in prunes:
            assert params.get("schema") == "gold", sql
            assert "{schema: $schema}" in sql, sql


class TestAsyncRefreshMatchesItsSyncTwin:
    """Round 1's Critical: the async variant defaulted `kpis` to `()` after the sync one moved
    to `None`, silently turning off the registry read for every async caller. Round 2 noted
    the fix had no PR-gating coverage — the live suite runs nightly. This is that coverage."""

    def test_the_two_signatures_agree_on_every_default(self) -> None:
        import inspect

        from egpt_retail.semantic.refresh import (
            arefresh_semantic_layer,
            refresh_semantic_layer,
        )

        sync = inspect.signature(refresh_semantic_layer).parameters
        asyn = inspect.signature(arefresh_semantic_layer).parameters
        assert set(sync) == set(asyn)
        for name, parameter in sync.items():
            assert parameter.default == asyn[name].default, name

    @pytest.mark.asyncio
    async def test_the_async_variant_forwards_every_argument_unchanged(self) -> None:
        """Not just the sentinel: any argument the wrapper forgets is a feature that exists
        on one path and not the other, with nothing failing."""
        from egpt_retail.semantic import refresh as refresh_module

        seen: Dict[str, Any] = {}

        def _capture(settings: Any = None, **kwargs: Any) -> str:
            seen.update(kwargs)
            seen["settings"] = settings
            return "report"

        original = refresh_module.refresh_semantic_layer
        refresh_module.refresh_semantic_layer = _capture  # type: ignore[assignment]
        try:
            result = await refresh_module.arefresh_semantic_layer(
                None, schema="gold", version="v9", kpis=None, warehouse="w"
            )
        finally:
            refresh_module.refresh_semantic_layer = original  # type: ignore[assignment]

        assert result == "report"
        assert seen["schema"] == "gold"
        assert seen["version"] == "v9"
        assert seen["kpis"] is None
        assert seen["warehouse"] == "w"
