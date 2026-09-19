"""#306 -- ``harvest_source`` (``executors/data_source_harvest.py``), DB-free: every branch
of its own orchestration logic, with ``DataSourceStore``, the secret store, the Postgres
connection and the Neo4j graph all faked or monkeypatched. The real pieces, wired together
against real Postgres and Neo4j (including the SSRF guard refusing a real loopback host end
to end through the actual workflow step), are proved by
``cookbook/94_retail/P2_harvest_source.py``.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import pytest
from egpt.workflow.types import StepInput
from egpt_retail.contracts.data_source import (
    AuthMethod,
    Credential,
    DataSource,
    DataSourceStatus,
    DataSourceType,
    HarvestState,
    PermissionLevel,
)
from egpt_retail.executors import data_source_harvest as harvest_module
from egpt_retail.semantic.harvest import Harvest, HarvestedColumn, HarvestedTable


def _source(**overrides: Any) -> DataSource:
    now = datetime.now(timezone.utc)
    fields: Dict[str, Any] = dict(
        id="ds_1",
        name="Test source",
        source_type=DataSourceType.POSTGRES,
        host="warehouse.internal",
        database_name="analytics",
        auth_method=AuthMethod.USERNAME_PASSWORD,
        username="reader",
        secret_ref="fake:1",
        status=DataSourceStatus.REGISTERED,
        harvest_state=HarvestState.NOT_STARTED,
        created_by="tester",
        created_at=now,
        updated_at=now,
        schema_name="public",
        ssl_mode="require",
        permission_level=PermissionLevel.READ_ONLY,
    )
    fields.update(overrides)
    return DataSource(**fields)


def _harvest(**overrides: Any) -> Harvest:
    fields: Dict[str, Any] = dict(
        schema="public",
        tables=(
            HarvestedTable(
                schema="public",
                name="orders",
                columns=(
                    HarvestedColumn(name="id", data_type="integer", nullable=False),
                ),
                row_count=10,
            ),
        ),
        harvested_at="2026-01-01T00:00:00Z",
        source="Test source",
    )
    fields.update(overrides)
    return Harvest(**fields)


class _FakeDataSourceStore:
    def __init__(self, connection: Any, *, seed: Optional[DataSource] = None) -> None:
        self.rows: Dict[str, DataSource] = {seed.id: seed} if seed else {}
        self.harvest_state_calls: list = []

    def get(self, source_id: str) -> Optional[DataSource]:
        return self.rows.get(source_id)

    def set_harvest_state(
        self,
        source_id: str,
        state: HarvestState,
        *,
        run_id=None,
        mark_harvested_now=False,
    ) -> Optional[DataSource]:
        self.harvest_state_calls.append((state, run_id, mark_harvested_now))
        existing = self.rows.get(source_id)
        if existing is None:
            return None
        updated = replace(existing, harvest_state=state)
        self.rows[source_id] = updated
        return updated


class _FakeSecretStore:
    def __init__(self, credential: Credential) -> None:
        self._credential = credential

    def read(self, secret_ref: str) -> Credential:
        return self._credential


class _FakeGraphStore:
    def __init__(self, *, conflict: Optional[str] = None) -> None:
        self.conflict = conflict
        self.written: list = []
        self.ensure_indexes_called = False

    def ensure_indexes(self) -> None:
        self.ensure_indexes_called = True

    def schema_owned_by_other_source(
        self, schema: str, source_id: str
    ) -> Optional[str]:
        return self.conflict

    def write_harvest(
        self, harvest: Harvest, *, snapshot_version: str, source_id: str
    ) -> int:
        self.written.append((harvest, snapshot_version, source_id))
        return len(harvest.tables)


def _install_fakes(
    monkeypatch,
    *,
    source: Optional[DataSource],
    harvest: Optional[Harvest] = None,
    graph: Optional[_FakeGraphStore] = None,
    credential: Optional[Credential] = None,
    fail_connect: bool = False,
) -> _FakeDataSourceStore:
    store = _FakeDataSourceStore(None, seed=source)
    credential = credential or Credential(
        auth_method=AuthMethod.USERNAME_PASSWORD, username="reader", password="hunter2"
    )
    graph = graph or _FakeGraphStore()

    monkeypatch.setattr(
        harvest_module,
        "pooled_control_plane_connection",
        lambda settings: _FakeConnection(),
    )
    monkeypatch.setattr(harvest_module, "DataSourceStore", lambda connection: store)
    monkeypatch.setattr(
        harvest_module,
        "build_secret_store",
        lambda settings, connection: _FakeSecretStore(credential),
    )
    monkeypatch.setattr(
        harvest_module, "_neo4j_driver", lambda settings: _FakeConnection()
    )
    monkeypatch.setattr(
        harvest_module, "SemanticGraphStore", lambda driver, database: graph
    )

    if fail_connect:

        def _refuse_connect(source, credential):
            raise RuntimeError("connection refused")

        monkeypatch.setattr(harvest_module, "_open_source_connection", _refuse_connect)
    else:
        monkeypatch.setattr(
            harvest_module,
            "_open_source_connection",
            lambda source, credential: _FakeConnection(),
        )
    monkeypatch.setattr(
        harvest_module, "harvest_schema", lambda *a, **k: harvest or _harvest()
    )
    return store


class _FakeConnection:
    autocommit = False

    def close(self) -> None:
        pass


def _step_input(source_id: Optional[str] = "ds_1") -> StepInput:
    additional_data = {"source_id": source_id} if source_id else {}
    return StepInput(additional_data=additional_data)


@pytest.mark.asyncio
class TestHarvestSource:
    async def test_missing_source_id_is_refused(self, monkeypatch):
        _install_fakes(monkeypatch, source=_source())
        result = await harvest_module.harvest_source(_step_input(None))
        assert result.success is False
        assert "source_id is required" in result.error

    async def test_unknown_source_id_is_refused(self, monkeypatch):
        _install_fakes(monkeypatch, source=None)
        result = await harvest_module.harvest_source(_step_input("does-not-exist"))
        assert result.success is False
        assert "No such source" in result.error

    async def test_unharvestable_source_type_is_refused_and_state_set_to_error(
        self, monkeypatch
    ):
        store = _install_fakes(
            monkeypatch, source=_source(source_type=DataSourceType.SNOWFLAKE)
        )
        result = await harvest_module.harvest_source(_step_input())
        assert result.success is False
        assert "No harvest driver" in result.error
        assert store.rows["ds_1"].harvest_state is HarvestState.ERROR

    async def test_read_write_permission_is_refused(self, monkeypatch):
        store = _install_fakes(
            monkeypatch, source=_source(permission_level=PermissionLevel.READ_WRITE)
        )
        result = await harvest_module.harvest_source(_step_input())
        assert result.success is False
        assert "read_write" in result.error
        assert store.rows["ds_1"].harvest_state is HarvestState.ERROR

    async def test_loopback_host_is_refused_and_state_set_to_error(self, monkeypatch):
        store = _install_fakes(monkeypatch, source=_source(host="localhost"))
        result = await harvest_module.harvest_source(_step_input())
        assert result.success is False
        assert "loopback" in result.error or "link-local" in result.error
        assert store.rows["ds_1"].harvest_state is HarvestState.ERROR

    async def test_connection_failure_is_refused_and_state_set_to_error(
        self, monkeypatch
    ):
        store = _install_fakes(monkeypatch, source=_source(), fail_connect=True)
        result = await harvest_module.harvest_source(_step_input())
        assert result.success is False
        assert "Could not connect" in result.error
        assert store.rows["ds_1"].harvest_state is HarvestState.ERROR

    async def test_empty_harvest_is_refused_and_state_set_to_error(self, monkeypatch):
        empty = _harvest(tables=())
        store = _install_fakes(monkeypatch, source=_source(), harvest=empty)
        result = await harvest_module.harvest_source(_step_input())
        assert result.success is False
        assert "harvested as empty" in result.error
        assert store.rows["ds_1"].harvest_state is HarvestState.ERROR

    async def test_schema_owned_by_another_source_is_refused_as_needs_review(
        self, monkeypatch
    ):
        graph = _FakeGraphStore(conflict="ds_other")
        store = _install_fakes(monkeypatch, source=_source(), graph=graph)
        result = await harvest_module.harvest_source(_step_input())
        assert result.success is False
        assert "ds_other" in result.error
        assert store.rows["ds_1"].harvest_state is HarvestState.NEEDS_REVIEW
        assert graph.written == []  # never reached write_harvest

    async def test_successful_harvest_writes_the_graph_and_marks_modelled(
        self, monkeypatch
    ):
        graph = _FakeGraphStore()
        harvest = _harvest()
        store = _install_fakes(
            monkeypatch, source=_source(), harvest=harvest, graph=graph
        )

        result = await harvest_module.harvest_source(_step_input())

        assert result.success is not False
        assert result.content["ok"] is True
        assert result.content["tables"] == 1
        assert store.rows["ds_1"].harvest_state is HarvestState.MODELLED
        assert len(graph.written) == 1
        written_harvest, version, source_id = graph.written[0]
        assert written_harvest is harvest
        assert source_id == "ds_1"
        assert version  # a non-empty snapshot version was generated

    async def test_the_sequence_of_harvest_states_is_building_then_terminal(
        self, monkeypatch
    ):
        """Proves BUILDING is set before the connection is even attempted -- a caller polling
        mid-run sees 'in progress', not a stale 'not_started' right up until completion."""
        store = _install_fakes(monkeypatch, source=_source())
        await harvest_module.harvest_source(_step_input())
        states = [call[0] for call in store.harvest_state_calls]
        assert states[0] is HarvestState.BUILDING
        assert states[-1] is HarvestState.MODELLED
