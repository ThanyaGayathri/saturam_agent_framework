"""K11 -- the registry's DB-free guarantees: round-tripping, ordering, and what is never stored.

The live path (real Postgres, real Neo4j) is in ``test_semantic_live.py``. What is here is
everything provable without a database, because that is what CI gates today.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple, cast

import pytest
from egpt_retail.contracts.kpi import (
    Dialect,
    FeasibilityVerdict,
    KpiDefinition,
    KpiParameter,
    ParameterKind,
    ResultShape,
    TimeWindow,
    TimeWindowUnit,
    assess_registry,
)
from egpt_retail.contracts.semantic import SemanticRead, Snapshot, TableRef
from egpt_retail.executors.kpi import feasibility_summary, verdict_rows
from egpt_retail.kpi.store import KPI_LABEL, KpiRegistryStore, row_to_definition


def _kpi(identifier: str = "deal__won_value", **overrides: Any) -> KpiDefinition:
    fields: Dict[str, Any] = {
        "identifier": identifier,
        "name": "won value",
        "description": "Value of closed-won deals.",
        "sql": "SELECT sum(amount) FROM crm_deal WHERE {scope}",
        "grain": "overall",
        "business_area": "sales",
        "source_tables": frozenset({"crm_deal"}),
        "parameters": (
            KpiParameter("scope", ParameterKind.PREDICATE, "Concept scoping filter."),
        ),
        "concept": "Deal",
    }
    fields.update(overrides)
    return KpiDefinition(**fields)


class _Cursor:
    def __init__(self, log: List[Tuple[str, Any]], rows: List[Any]) -> None:
        self.log = log
        self._rows = rows
        self.rowcount = 1

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        self.log.append((" ".join(sql.split()), params))

    def fetchall(self) -> List[Any]:
        return list(self._rows)

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None


class _Connection:
    def __init__(self, rows: List[Any] | None = None) -> None:
        self.log: List[Tuple[str, Any]] = []
        self.rows = rows or []
        self.commits = 0

    def cursor(self) -> _Cursor:
        return _Cursor(self.log, self.rows)

    def commit(self) -> None:
        self.commits += 1


class _Reader:
    """The narrow slice of ``SemanticRead`` feasibility actually uses.

    Cast at the call sites below rather than fleshed out to the full protocol: the point of
    these tests is that feasibility reads exactly two things -- the table names and the
    snapshot version -- and a fake implementing the other dozen methods would obscure that.
    """

    def __init__(self, *tables: str, version: str = "snap.1") -> None:
        self._tables = tables
        self._version = version

    def list_tables(self, *, include_proposed: bool = False) -> Tuple[TableRef, ...]:
        return tuple(TableRef(name=t, schema="silver") for t in self._tables)

    def snapshot(self) -> Snapshot:
        return Snapshot(version=self._version, harvested_at="", source="test")


class TestRoundTrip:
    def test_a_definition_survives_the_row_it_is_stored_as(self) -> None:
        original = _kpi(
            time_window=TimeWindow(length=90, unit=TimeWindowUnit.DAY),
            result_shape=ResultShape.SCALAR,
            dialect=Dialect.POSTGRES,
            validated_against_snapshot="snap.7",
        )
        connection = _Connection()
        KpiRegistryStore(connection).save(original)
        insert = next(p for sql, p in connection.log if "INSERT INTO" in sql)
        # Postgres would hand jsonb back as objects; psycopg may hand back strings. The
        # reader accepts both, so the round trip is checked through the string form -- the
        # one that would break silently if only the object form were ever tested.
        rebuilt = row_to_definition(list(insert))
        assert rebuilt == original

    def test_json_columns_come_back_as_objects_too(self) -> None:
        row = list(
            (
                "a__b",
                "n",
                "d",
                "SELECT 1 FROM crm_deal",
                "overall",
                "sales",
                json.loads('["crm_deal"]'),
                "scalar",
                "postgres",
                [],
                None,
                None,
                None,
            )
        )
        assert row_to_definition(row).source_tables == frozenset({"crm_deal"})

    def test_a_row_whose_sql_drifted_from_its_tables_fails_on_read(self) -> None:
        """The registry's own invariant, re-checked on the way out: if a formula is edited
        to read a table the row does not list, feasibility would call it ready while the
        query is missing a table."""
        row = [
            "a__b",
            "n",
            "d",
            "SELECT 1 FROM crm_deal JOIN crm_contact ON true",
            "overall",
            "sales",
            ["crm_deal"],
            "scalar",
            "postgres",
            [],
            None,
            None,
            None,
        ]
        with pytest.raises(ValueError, match="crm_contact"):
            row_to_definition(row)


class TestVocabularyGate:
    def test_a_bad_term_writes_nothing_at_all(self) -> None:
        """Validation happens before the loop, so a bad second definition cannot leave the
        first one committed."""
        connection = _Connection()
        store = KpiRegistryStore(connection)
        good = _kpi("deal__good")
        bad = _kpi("deal__bad", business_area="marketng")
        with pytest.raises(ValueError, match="not a permitted business_area"):
            store.save_many([good, bad])
        assert not any("INSERT INTO" in sql for sql, _ in connection.log)
        assert connection.commits == 0

    def test_a_duplicate_identifier_is_refused(self) -> None:
        connection = _Connection()
        with pytest.raises(ValueError, match="duplicate KPI identifier"):
            KpiRegistryStore(connection).save_many([_kpi(), _kpi()])
        assert connection.commits == 0


class TestFeasibilityIsNeverStored:
    def test_no_write_carries_a_verdict(self) -> None:
        """A stored verdict goes stale the moment a harvest runs. The column must not exist,
        and no write may invent one."""
        connection = _Connection()
        KpiRegistryStore(connection).save(_kpi())
        for sql, _ in connection.log:
            assert "verdict" not in sql.lower()
            assert "feasib" not in sql.lower()

    def test_the_table_definition_has_no_verdict_column(self) -> None:
        connection = _Connection()
        KpiRegistryStore(connection).ensure_schema()
        create = next(
            sql
            for sql, _ in connection.log
            if sql.startswith("CREATE TABLE") and "kpi_definition" in sql
        )
        # The CREATE only -- the table's COMMENT explains why there is no verdict column,
        # so it says the word on purpose and a whole-DDL grep would flag its own rationale.
        assert "verdict" not in create.lower()
        assert "feasib" not in create.lower()

    def test_ddl_refuses_a_definition_with_no_source_tables(self) -> None:
        connection = _Connection()
        KpiRegistryStore(connection).ensure_schema()
        ddl = " ".join(sql for sql, _ in connection.log)
        assert "jsonb_array_length(source_tables) > 0" in ddl


class TestRevalidation:
    def test_only_computable_definitions_are_stamped(self) -> None:
        """A stamp means "checked and computable against this snapshot". Stamping a blocked
        KPI would tell K6's review screen the definition had been validated against a
        snapshot that cannot run it."""
        ready = _kpi("deal__ready")
        blocked = _kpi(
            "deal__blocked",
            sql="SELECT count(*) FROM absent_table",
            source_tables=frozenset({"absent_table"}),
            parameters=(),
        )
        rows = [
            (
                k.identifier,
                k.name,
                k.description,
                k.sql,
                k.grain,
                k.business_area,
                sorted(k.source_tables),
                k.result_shape.value,
                k.dialect.value,
                [
                    {"name": p.name, "kind": p.kind.value, "description": p.description}
                    for p in k.parameters
                ],
                None,
                None,
                k.concept,
            )
            for k in (ready, blocked)
        ]
        connection = _Connection(rows=rows)
        report = KpiRegistryStore(connection).revalidate(
            cast(SemanticRead, _Reader("crm_deal")), "snap.9"
        )

        assert report.validated == ("deal__ready",)
        assert report.unvalidated == ("deal__blocked",)
        update = next(
            p for sql, p in connection.log if "validated_against_snapshot = %s" in sql
        )
        assert update == ("snap.9", ["deal__ready"])

    def test_the_snapshot_version_defaults_to_the_readers(self) -> None:
        connection = _Connection(rows=[])
        report = KpiRegistryStore(connection).revalidate(
            cast(SemanticRead, _Reader("crm_deal", version="snap.42"))
        )
        assert report.snapshot_version == "snap.42"

    def test_nothing_computable_writes_nothing(self) -> None:
        connection = _Connection(rows=[])
        KpiRegistryStore(connection).revalidate(cast(SemanticRead, _Reader("crm_deal")))
        assert not any("UPDATE" in sql for sql, _ in connection.log)


class TestMirrorIsLabelScoped:
    def test_the_prune_names_only_the_kpi_label(self) -> None:
        """The P2 review found what a label-blind prune costs, twice. This one is bound to
        SemanticKpi and must stay that way."""
        log: List[Tuple[str, Any]] = []

        class _Tx:
            def run(self, query: str, **params: Any) -> None:
                log.append((" ".join(query.split()), params))

        class _Session:
            def __enter__(self) -> "_Session":
                return self

            def __exit__(self, *_: object) -> None:
                return None

            def execute_write(self, fn: Any, *args: Any) -> Any:
                return fn(_Tx(), *args)

        class _Driver:
            def session(self, **_: Any) -> _Session:
                return _Session()

        connection = _Connection(rows=[])
        written = KpiRegistryStore(connection).mirror(_Driver())
        assert written == 0
        prunes = [(sql, p) for sql, p in log if "DETACH DELETE" in sql]
        assert len(prunes) == 1
        assert f"MATCH (k:{KPI_LABEL})" in prunes[0][0]
        assert prunes[0][1] == {"keep": []}


class TestDiscoveryRows:
    def test_blocked_comes_first_and_ready_last(self) -> None:
        """A screen that leads with what works buries the thing to act on."""
        kpis = [
            _kpi("a__ready"),
            _kpi(
                "b__blocked",
                sql="SELECT count(*) FROM absent",
                source_tables=frozenset({"absent"}),
                parameters=(),
            ),
            _kpi(
                "c__partial",
                sql="SELECT count(*) FROM crm_deal JOIN absent ON true",
                source_tables=frozenset({"crm_deal", "absent"}),
                parameters=(),
            ),
        ]
        rows = verdict_rows(assess_registry(kpis, frozenset({"crm_deal"})))
        assert [r["verdict"] for r in rows] == ["blocked", "partial", "ready"]

    def test_every_verdict_is_counted_even_at_zero(self) -> None:
        counts = feasibility_summary(assess_registry([_kpi()], frozenset({"crm_deal"})))
        assert counts == {"ready": 1, "partial": 0, "blocked": 0}
        assert set(counts) == {v.value for v in FeasibilityVerdict}

    def test_a_blocked_kpi_names_every_missing_table_not_just_the_first(self) -> None:
        """The plan's own evidence line: the two-missing case names both."""
        two_missing = _kpi(
            "x__two",
            sql="SELECT count(*) FROM absent_one JOIN absent_two ON true",
            source_tables=frozenset({"absent_one", "absent_two"}),
            parameters=(),
        )
        rows = verdict_rows(assess_registry([two_missing], frozenset({"crm_deal"})))
        assert rows[0]["missing_tables"] == ["absent_one", "absent_two"]
        assert "absent_one, absent_two" in rows[0]["rationale"]


class TestMirrorWithRealRows:
    """Round 2: the mirror was tested only with `rows=[]`, so its statement *order* and its
    edge matching were unexercised — the two places findings #9 and #18 lived."""

    class _Tx:
        def __init__(self, log: List[Tuple[str, Any]]) -> None:
            self.log = log

        def run(self, query: str, **params: Any) -> Any:
            self.log.append((" ".join(query.split()), params))

            class _R:
                @staticmethod
                def data() -> List[Any]:
                    return []

                @staticmethod
                def single() -> Any:
                    return None

            return _R()

    class _Session:
        def __init__(self, log: List[Tuple[str, Any]]) -> None:
            self.log = log

        def __enter__(self) -> "TestMirrorWithRealRows._Session":
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def execute_write(self, fn: Any, *args: Any) -> Any:
            return fn(TestMirrorWithRealRows._Tx(self.log), *args)

    class _Driver:
        def __init__(self) -> None:
            self.log: List[Tuple[str, Any]] = []

        def session(self, **_: Any) -> "TestMirrorWithRealRows._Session":
            return TestMirrorWithRealRows._Session(self.log)

    def _store_with_one_kpi(self) -> Tuple[Any, "_Connection"]:
        kpi = _kpi(concept="Deal")
        row = (
            kpi.identifier,
            kpi.name,
            kpi.description,
            kpi.sql,
            kpi.grain,
            kpi.business_area,
            sorted(kpi.source_tables),
            kpi.result_shape.value,
            kpi.dialect.value,
            [
                {"name": p.name, "kind": p.kind.value, "description": p.description}
                for p in kpi.parameters
            ],
            None,
            "snap.3",
            kpi.concept,
        )
        connection = _Connection(rows=[row])
        return KpiRegistryStore(connection), connection

    def test_metrics_are_recomputed_after_the_prune_not_before(self) -> None:
        """A KPI deleted from Postgres must lose its concept's metrics entry. Recomputing
        before the prune leaves the deleted identifier on the concept, and the live test that
        caught this has no PR-gating job — so it is pinned here too."""
        store, _ = self._store_with_one_kpi()
        driver = self._Driver()
        store.mirror(driver)

        statements = [sql for sql, _ in driver.log]
        prune = next(i for i, sql in enumerate(statements) if "DETACH DELETE k" in sql)
        metrics = next(i for i, sql in enumerate(statements) if "SET c.metrics" in sql)
        assert prune < metrics, (
            "metrics recomputed before the prune; a deleted KPI lingers"
        )

    def test_reads_edges_accept_a_qualified_table_name(self) -> None:
        """Nodes are keyed {schema, name}; matching on bare name alone linked a KPI to every
        schema's table of that name."""
        store, _ = self._store_with_one_kpi()
        driver = self._Driver()
        store.mirror(driver)
        upsert = next(sql for sql, _ in driver.log if "MERGE (k:SemanticKpi" in sql)
        assert "t.schema + '.' + t.name" in upsert

    def test_the_metrics_recompute_is_scoped_not_graph_wide(self) -> None:
        """Rewriting every concept nightly is write amplification on a shared store."""
        store, _ = self._store_with_one_kpi()
        driver = self._Driver()
        store.mirror(driver)
        sql, params = next((s, p) for s, p in driver.log if "SET c.metrics" in s)
        assert "c.name IN $named" in sql
        assert params["named"] == ["Deal"]

    def test_one_kpi_reaches_the_upsert_with_its_stamp(self) -> None:
        store, _ = self._store_with_one_kpi()
        driver = self._Driver()
        assert store.mirror(driver) == 1
        _, params = next((s, p) for s, p in driver.log if "MERGE (k:SemanticKpi" in s)
        [row] = params["rows"]
        assert row["identifier"] == "deal__won_value"
        assert row["validated_against_snapshot"] == "snap.3"
