"""#306 -- ``harvest_source`` (``executors/data_source_harvest.py``), DB-free: every branch
of its own orchestration logic, with ``DataSourceStore``, the secret store, the Postgres
connection and the Neo4j graph all faked or monkeypatched. The real pieces, wired together
against real Postgres and Neo4j (including the SSRF guard refusing a real loopback host end
to end through the actual workflow step), are proved by
``cookbook/94_retail/P2_harvest_source.py``.
"""

from __future__ import annotations

from dataclasses import replace
import socket
import threading
import time
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
from egpt_retail.configure import host_guard
from egpt_retail.configure.host_guard import HostCheck
from egpt_retail.executors import data_source_harvest as harvest_module
from egpt_retail.semantic.harvest import Harvest, HarvestedColumn, HarvestedTable
from egpt_retail.semantic.store import SchemaOwnedByOtherSource


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
    def __init__(
        self,
        *,
        conflict: Optional[str] = None,
        write_error: Optional[Exception] = None,
        index_error: Optional[Exception] = None,
    ) -> None:
        self.conflict = conflict
        self.write_error = write_error
        self.index_error = index_error
        self.written: list = []
        self.ensure_indexes_called = False

    def ensure_indexes(self) -> None:
        self.ensure_indexes_called = True
        if self.index_error is not None:
            raise self.index_error

    def write_harvest(
        self, harvest: Harvest, *, snapshot_version: str, source_id: str
    ) -> int:
        if self.conflict is not None:
            raise SchemaOwnedByOtherSource(harvest.schema, self.conflict)
        if self.write_error is not None:
            raise self.write_error
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
    harvest_error: Optional[Exception] = None,
    driver_error: Optional[Exception] = None,
    addresses: tuple = ("10.0.0.5",),
    real_resolver: bool = False,
    connect_calls: Optional[list] = None,
    driver: Optional["_FakeConnection"] = None,
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
    fake_driver = driver or _FakeConnection()

    def _driver(settings):
        if driver_error is not None:
            raise driver_error
        return fake_driver

    monkeypatch.setattr(harvest_module, "_neo4j_driver", _driver)
    # The default source host is not a real name; resolution is the one network step every
    # test would otherwise depend on the machine's DNS for. The tests of the guard itself
    # (``TestTheSharedHostGuard``) pass ``real_resolver=True`` and stub ``socket.getaddrinfo``.
    if not real_resolver:
        monkeypatch.setattr(
            harvest_module,
            "check_host",
            lambda host, port=None, **kwargs: HostCheck(addresses=tuple(addresses)),
        )
    monkeypatch.setattr(
        harvest_module, "SemanticGraphStore", lambda driver, database: graph
    )

    if fail_connect:

        def _refuse_connect(source, credential, addresses):
            raise RuntimeError("connection refused")

        monkeypatch.setattr(harvest_module, "_open_source_connection", _refuse_connect)
    else:

        def _connect(source, credential, addresses):
            if connect_calls is not None:
                connect_calls.append(addresses)
            return _FakeConnection()

        monkeypatch.setattr(harvest_module, "_open_source_connection", _connect)

    def _read_schema(*a, **k):
        if harvest_error is not None:
            raise harvest_error
        return harvest or _harvest()

    monkeypatch.setattr(harvest_module, "harvest_schema", _read_schema)
    return store


class _FakeConnection:
    autocommit = False

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


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
        store = _install_fakes(
            monkeypatch, source=_source(host="localhost"), real_resolver=True
        )
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
        assert graph.written == []  # write_harvest refused before recording anything

    async def test_schema_held_by_the_operators_own_harvest_is_named_as_such(
        self, monkeypatch
    ):
        graph = _FakeGraphStore(conflict="")
        _install_fakes(monkeypatch, source=_source(), graph=graph)
        result = await harvest_module.harvest_source(_step_input())
        assert result.success is False
        assert "deployment's own warehouse harvest" in result.error

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

    async def test_connection_is_made_to_the_vetted_addresses(self, monkeypatch):
        """The addresses that passed the SSRF check are the ones handed to the driver --
        nothing is resolved a second time."""
        calls: list = []
        _install_fakes(
            monkeypatch,
            source=_source(),
            addresses=("10.0.0.5", "10.0.0.6"),
            connect_calls=calls,
        )
        await harvest_module.harvest_source(_step_input())
        assert calls == [("10.0.0.5", "10.0.0.6")]


@pytest.mark.asyncio
class TestFailuresNeverStrandBuilding:
    """Review round 2: an unexpected error after ``building`` was set used to escape as an
    unhandled exception and leave the source showing ``building`` forever. Every one of these
    must end in a terminal state and a clean, non-raising ``StepOutput``."""

    async def _run(self, monkeypatch, **fakes):
        store = _install_fakes(monkeypatch, source=_source(), **fakes)
        result = await harvest_module.harvest_source(_step_input())
        states = [call[0] for call in store.harvest_state_calls]
        assert states[0] is HarvestState.BUILDING
        assert states[-1] is not HarvestState.BUILDING
        assert store.rows["ds_1"].harvest_state is HarvestState.ERROR
        assert result.success is False
        return result

    async def test_a_failure_reading_the_schema(self, monkeypatch):
        result = await self._run(
            monkeypatch, harvest_error=RuntimeError("permission denied for schema")
        )
        assert "Could not read schema 'public'" in result.error
        assert "permission denied" in result.error

    async def test_the_warehouse_connection_is_closed_when_the_read_fails(
        self, monkeypatch
    ):
        opened: list = []

        def _connect(source, credential, addresses):
            connection = _FakeConnection()
            opened.append(connection)
            return connection

        _install_fakes(
            monkeypatch,
            source=_source(),
            harvest_error=RuntimeError("boom"),
        )
        monkeypatch.setattr(harvest_module, "_open_source_connection", _connect)
        await harvest_module.harvest_source(_step_input())
        assert [c.closed for c in opened] == [True]

    async def test_no_graph_driver_available(self, monkeypatch):
        result = await self._run(
            monkeypatch,
            driver_error=RuntimeError("EGPT_RETAIL_NEO4J_PASSWORD is not set"),
        )
        assert "Could not reach the semantic graph" in result.error
        assert "EGPT_RETAIL_NEO4J_PASSWORD" in result.error

    async def test_index_setup_fails(self, monkeypatch):
        graph = _FakeGraphStore(index_error=ConnectionError("neo4j is down"))
        result = await self._run(monkeypatch, graph=graph)
        assert "Could not write the harvest to the semantic graph" in result.error
        assert graph.written == []

    async def test_the_graph_write_fails(self, monkeypatch):
        graph = _FakeGraphStore(write_error=RuntimeError("cypher exploded"))
        result = await self._run(monkeypatch, graph=graph)
        assert "cypher exploded" in result.error

    async def test_the_graph_driver_is_closed_on_a_write_failure(self, monkeypatch):
        driver = _FakeConnection()
        graph = _FakeGraphStore(write_error=RuntimeError("cypher exploded"))
        _install_fakes(monkeypatch, source=_source(), graph=graph, driver=driver)
        await harvest_module.harvest_source(_step_input())
        assert driver.closed is True


def _answer(*addresses: str) -> list:
    """What ``socket.getaddrinfo`` returns for ``addresses``."""
    return [
        (
            socket.AF_INET6 if ":" in a else socket.AF_INET,
            socket.SOCK_STREAM,
            6,
            "",
            (a, 5432, 0, 0) if ":" in a else (a, 5432),
        )
        for a in addresses
    ]


@pytest.mark.asyncio
class TestTheSharedHostGuard:
    """The executor's host check is ``configure/host_guard.py``'s -- the one the
    test-connection route uses -- not a copy of it. It used to be a copy, and the copy had no
    ``is_unspecified`` (``0.0.0.0`` reads as loopback on some kernels) and no bound on name
    resolution. These run the real guard through ``harvest_source`` with only the resolver
    stubbed, so what is asserted is what the executor does with the guard's answer."""

    @staticmethod
    def _resolves_to(monkeypatch, *addresses: str) -> None:
        monkeypatch.setattr(
            socket, "getaddrinfo", lambda host, port, **kw: _answer(*addresses)
        )

    @staticmethod
    async def _harvest(monkeypatch, **fakes):
        calls: list = []
        store = _install_fakes(
            monkeypatch,
            source=_source(host="db.customer.example"),
            real_resolver=True,
            connect_calls=calls,
            **fakes,
        )
        result = await harvest_module.harvest_source(_step_input())
        return result, store, calls

    async def test_the_executor_uses_the_shared_guard_and_carries_no_copy_of_it(
        self,
    ):
        assert harvest_module.check_host is host_guard.check_host
        assert not hasattr(harvest_module, "_resolve_allowed_addresses")

    @pytest.mark.parametrize(
        "address",
        [
            "127.0.0.1",
            "169.254.169.254",  # the cloud metadata endpoint
            "::1",
            "fe80::1",
            "::ffff:127.0.0.1",  # IPv4 loopback wearing an IPv6 mapping
            "::ffff:169.254.169.254",
            "0.0.0.0",  # unspecified: the kernel connects to it as loopback
            "::",
            "::ffff:0.0.0.0",
        ],
    )
    async def test_a_refused_address_ends_the_harvest_in_error_and_is_never_dialled(
        self, monkeypatch, address
    ):
        self._resolves_to(monkeypatch, address)
        result, store, calls = await self._harvest(monkeypatch)
        assert result.success is False
        assert "loopback or link-local" in result.error
        assert store.rows["ds_1"].harvest_state is HarvestState.ERROR
        assert calls == []

    async def test_one_bad_address_among_good_ones_refuses_the_host(self, monkeypatch):
        """A host answering with a public and a loopback address is refused outright, not
        connected to at the public one."""
        self._resolves_to(monkeypatch, "10.1.2.3", "127.0.0.1")
        result, store, calls = await self._harvest(monkeypatch)
        assert result.success is False
        assert store.rows["ds_1"].harvest_state is HarvestState.ERROR
        assert calls == []

    async def test_a_host_that_does_not_resolve_is_refused(self, monkeypatch):
        """Letting it through would make the driver resolve it a second time, which is the
        rebinding window the pinning exists to close."""

        def _fail(host, port, **kwargs):
            raise socket.gaierror("no such host")

        monkeypatch.setattr(socket, "getaddrinfo", _fail)
        result, store, calls = await self._harvest(monkeypatch)
        assert result.success is False
        assert result.error == "The host name could not be resolved."
        assert store.rows["ds_1"].harvest_state is HarvestState.ERROR
        assert calls == []

    async def test_a_resolver_that_never_answers_cannot_stall_the_harvest(
        self, monkeypatch
    ):
        release = threading.Event()

        def _hang(host, port, **kwargs):
            release.wait(30)
            return _answer("10.1.2.3")

        monkeypatch.setattr(socket, "getaddrinfo", _hang)
        monkeypatch.setattr(host_guard, "RESOLVE_TIMEOUT_SECONDS", 0.1)
        started = time.monotonic()
        try:
            result, store, calls = await self._harvest(monkeypatch)
        finally:
            release.set()

        assert time.monotonic() - started < 3
        assert result.success is False
        assert result.error == "Connection timed out."
        assert store.rows["ds_1"].harvest_state is HarvestState.ERROR
        assert calls == []

    async def test_every_vetted_address_is_handed_to_the_driver_in_resolver_order(
        self, monkeypatch
    ):
        """Duplicates collapse; the driver tries each in turn, as it would have resolving
        the name itself."""
        self._resolves_to(monkeypatch, "10.1.2.3", "10.1.2.4", "10.1.2.3")
        result, store, calls = await self._harvest(monkeypatch)
        assert result.success is True
        assert calls == [("10.1.2.3", "10.1.2.4")]

    async def test_the_sources_port_reaches_the_resolver(self, monkeypatch):
        seen: list = []

        def _record(host, port, **kwargs):
            seen.append((host, port))
            return _answer("10.1.2.3")

        monkeypatch.setattr(socket, "getaddrinfo", _record)
        _install_fakes(
            monkeypatch,
            source=_source(host="db.customer.example", port=6543),
            real_resolver=True,
        )
        await harvest_module.harvest_source(_step_input())
        assert seen == [("db.customer.example", 6543)]


class TestOpenSourceConnection:
    def _credential(self) -> Credential:
        return Credential(
            auth_method=AuthMethod.USERNAME_PASSWORD, username="reader", password="pw"
        )

    def test_connects_by_address_while_keeping_the_host_name(self, monkeypatch):
        psycopg = pytest.importorskip("psycopg", reason="needs the postgres driver")
        seen: list = []

        def _connect(**kwargs):
            seen.append(kwargs)
            return "connection"

        monkeypatch.setattr(psycopg, "connect", _connect)
        result = harvest_module._open_source_connection(
            _source(), self._credential(), ("10.0.0.5",)
        )
        assert result == "connection"
        assert seen[0]["hostaddr"] == "10.0.0.5"
        assert seen[0]["host"] == "warehouse.internal"  # verify-full checks the name

    def test_falls_through_to_the_next_vetted_address(self, monkeypatch):
        psycopg = pytest.importorskip("psycopg", reason="needs the postgres driver")
        tried: list = []

        def _connect(**kwargs):
            tried.append(kwargs["hostaddr"])
            if kwargs["hostaddr"] == "10.0.0.5":
                raise psycopg.OperationalError("unreachable")
            return "connection"

        monkeypatch.setattr(psycopg, "connect", _connect)
        result = harvest_module._open_source_connection(
            _source(), self._credential(), ("10.0.0.5", "10.0.0.6")
        )
        assert result == "connection"
        assert tried == ["10.0.0.5", "10.0.0.6"]

    def test_raises_the_last_error_when_every_address_fails(self, monkeypatch):
        psycopg = pytest.importorskip("psycopg", reason="needs the postgres driver")

        def _connect(**kwargs):
            raise psycopg.OperationalError(f"unreachable {kwargs['hostaddr']}")

        monkeypatch.setattr(psycopg, "connect", _connect)
        with pytest.raises(psycopg.OperationalError, match="10.0.0.6"):
            harvest_module._open_source_connection(
                _source(), self._credential(), ("10.0.0.5", "10.0.0.6")
            )


class TestHarvestableTracksTheDriver:
    """Review round 2: ``DataSource.harvestable`` said Snowflake could be harvested while
    the executor refused it, so a UI gating on the property would offer a doomed run."""

    def test_postgres_is_harvestable(self):
        assert _source(source_type=DataSourceType.POSTGRES).harvestable is True

    def test_snowflake_is_not_until_it_has_a_driver(self):
        assert _source(source_type=DataSourceType.SNOWFLAKE).harvestable is False

    def test_the_executor_refuses_exactly_what_the_property_reports(self):
        from egpt_retail.contracts.data_source import (
            HARVEST_DRIVER_SOURCE_TYPES,
            HARVESTABLE_SOURCE_TYPES,
        )

        assert HARVEST_DRIVER_SOURCE_TYPES <= HARVESTABLE_SOURCE_TYPES
        for source_type in DataSourceType:
            assert _source(source_type=source_type).harvestable is (
                source_type in HARVEST_DRIVER_SOURCE_TYPES
            )
