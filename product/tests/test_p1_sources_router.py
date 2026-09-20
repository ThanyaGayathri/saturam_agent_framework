"""#305 -- ``api/routers/sources.py`` against a real FastAPI app (``sources_module.router``
included for real, scopes enforced through FastAPI's own ``solve_dependencies`` -- the same
shape ``test_kpis_router.py``'s ``_health_app`` documents), with in-memory fakes standing in
for ``DataSourceStore``/``SecretStore`` so none of this needs Postgres. The real store and
secret store, against real Postgres, are proved by ``cookbook/94_retail/P0_data_source_registration.py``
(the store directly) and ``cookbook/94_retail/P1_sources_router.py`` (this router, wired for
real, including one live connection attempt to prove the SSRF guard fires end to end).
"""

from __future__ import annotations

import socket
import threading
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pytest
from egpt_retail.api.routers import sources as sources_module
from egpt_retail.configure.host_guard import HostCheck
from egpt_retail.configure.secrets import SecretNotFoundError
from egpt_retail.configure.store import DataSourceConflictError
from egpt_retail.contracts.data_source import (
    AuthMethod,
    ConnectionTestResult,
    Credential,
    DataSource,
    DataSourceCreate,
    DataSourceStatus,
    DataSourceType,
    DataSourceUpdate,
    HarvestState,
    validate_create,
    validate_update,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient


# --- in-memory fakes, satisfying the same surface the real classes do ----------------------


class _FakeSecretStore:
    def __init__(self) -> None:
        self._by_ref: Dict[str, Credential] = {}
        self._next = 0

    def write(self, name_hint: str, credential: Credential) -> str:
        self._next += 1
        ref = f"fake:{self._next}"
        self._by_ref[ref] = credential
        return ref

    def read(self, secret_ref: str) -> Credential:
        try:
            return self._by_ref[secret_ref]
        except KeyError:
            raise SecretNotFoundError(secret_ref) from None

    def delete(self, secret_ref: str) -> None:
        self._by_ref.pop(secret_ref, None)


class _FakeDataSourceStore:
    """Reimplements only what ``DataSourceStore`` promises, in a dict, so the router's own
    mapping/validation/scope logic is what's under test -- not a real Postgres round trip,
    which the store's own tests already cover."""

    def __init__(self) -> None:
        self._rows: Dict[str, DataSource] = {}
        self._next = 0
        self.conflict = False  # set to make update() lose its rotation race

    def create(self, create: DataSourceCreate, secret_store) -> DataSource:
        validate_create(create)
        self._next += 1
        source_id = f"ds_{self._next}"
        secret_ref = secret_store.write(create.name, create.credential)
        now = datetime.now(timezone.utc)
        source = DataSource(
            id=source_id,
            name=create.name,
            source_type=create.source_type,
            host=create.host,
            database_name=create.database_name,
            auth_method=create.credential.auth_method,
            username=create.credential.username or "",
            secret_ref=secret_ref,
            status=DataSourceStatus.REGISTERED,
            harvest_state=HarvestState.NOT_STARTED,
            created_by=create.created_by,
            created_at=now,
            updated_at=now,
            port=create.port,
            schema_name=create.schema_name,
            ssl_mode=create.ssl_mode,
            permission_level=create.permission_level,
        )
        self._rows[source_id] = source
        return source

    def get(self, source_id: str) -> Optional[DataSource]:
        return self._rows.get(source_id)

    def list(self, created_by: Optional[str] = None):
        rows = self._rows.values()
        if created_by is not None:
            rows = [r for r in rows if r.created_by == created_by]
        return tuple(sorted(rows, key=lambda r: r.created_at, reverse=True))

    def update(
        self, source_id: str, patch: DataSourceUpdate, secret_store
    ) -> Optional[DataSource]:
        existing = self._rows.get(source_id)
        if existing is None:
            return None
        if self.conflict:
            raise DataSourceConflictError(source_id)
        validate_update(existing, patch)
        changes: Dict[str, object] = {}
        if patch.name is not None:
            changes["name"] = patch.name
        if patch.host is not None:
            changes["host"] = patch.host
        if patch.port is not None:
            changes["port"] = patch.port
        if patch.database_name is not None:
            changes["database_name"] = patch.database_name
        if patch.schema_name is not None:
            changes["schema_name"] = patch.schema_name
        if patch.ssl_mode is not None:
            changes["ssl_mode"] = patch.ssl_mode
        if patch.permission_level is not None:
            changes["permission_level"] = patch.permission_level
        if patch.credential is not None:
            changes["auth_method"] = patch.credential.auth_method
            changes["username"] = patch.credential.username or ""
            changes["secret_ref"] = secret_store.write(existing.name, patch.credential)
        updated = replace(existing, updated_at=datetime.now(timezone.utc), **changes)
        self._rows[source_id] = updated
        if patch.credential is not None:
            secret_store.delete(
                existing.secret_ref
            )  # as the real store does on rotation
        return updated

    def delete(self, source_id: str, secret_store) -> bool:
        existing = self._rows.pop(source_id, None)
        if existing is None:
            return False
        secret_store.delete(existing.secret_ref)
        return True

    def record_test_result(
        self, source_id: str, result: ConnectionTestResult
    ) -> Optional[DataSource]:
        existing = self._rows.get(source_id)
        if existing is None:
            return None
        status = DataSourceStatus.CONNECTED if result.ok else DataSourceStatus.ERROR
        updated = replace(
            existing,
            status=status,
            last_tested_at=result.tested_at,
            last_test_ok=result.ok,
            last_test_message=result.message,
        )
        self._rows[source_id] = updated
        return updated


def _client(*, scopes, user_id: str = "test-user", like: Any = None) -> TestClient:
    """``like`` reuses another client's stores, for a second caller of the same deployment."""
    app = FastAPI()
    app.include_router(sources_module.router)
    fake_store = like.fake_store if like is not None else _FakeDataSourceStore()
    fake_secrets = like.fake_secrets if like is not None else _FakeSecretStore()
    app.dependency_overrides[sources_module._source_store] = lambda: fake_store
    app.dependency_overrides[sources_module._secret_store] = lambda: fake_secrets

    @app.middleware("http")
    async def _fake_auth(request, call_next):
        request.state.authorization_enabled = True
        request.state.scopes = scopes
        request.state.admin_scope = None
        request.state.user_id = user_id
        return await call_next(request)

    client = TestClient(app)
    client.fake_store = fake_store  # type: ignore[attr-defined]
    client.fake_secrets = fake_secrets  # type: ignore[attr-defined]
    return client


_WRITE_BODY = {
    "source_type": "postgres",
    "name": "Warehouse",
    "host": "warehouse.internal",
    "database_name": "analytics",
    "schema": "silver",
    "auth_method": "username_password",
    "username": "egpt_reader",
    "password": "hunter2",
}


class TestScopes:
    def test_get_requires_config_read(self):
        client = _client(scopes=[])
        assert client.get("/configure/sources").status_code == 403

    def test_post_requires_configure_write(self):
        client = _client(scopes=["config:read"])
        assert client.post("/configure/sources", json=_WRITE_BODY).status_code == 403

    def test_post_with_configure_write_succeeds(self):
        client = _client(scopes=["configure:write"])
        assert client.post("/configure/sources", json=_WRITE_BODY).status_code == 201


class TestCreate:
    def test_created_source_never_carries_a_credential_field(self):
        client = _client(scopes=["configure:write"])
        body = client.post("/configure/sources", json=_WRITE_BODY).json()
        assert "password" not in body
        assert "api_key" not in body
        assert body["has_credentials"] is True
        assert body["username"] == "egpt_reader"

    def test_defaults_match_the_wire_contract(self):
        client = _client(scopes=["configure:write"])
        body = client.post("/configure/sources", json=_WRITE_BODY).json()
        assert body["ssl_mode"] == "require"
        assert body["permission_level"] == "read_only"
        # Contract 1.2.8 (#308): the card's readiness state travels on the wire, so a
        # freshly registered source reads as not yet modelled and never synced -- not as
        # an absent field a client has to guess the meaning of.
        assert body["harvest_state"] == "not_started"
        assert body["last_harvest_at"] is None
        assert body["last_harvest_id"] is None

    def test_invalid_credential_is_422(self):
        client = _client(scopes=["configure:write"])
        bad = dict(_WRITE_BODY)
        del bad["password"]
        response = client.post("/configure/sources", json=bad)
        assert response.status_code == 422


class TestReadUpdateDelete:
    def test_get_list_update_delete_round_trip(self):
        client = _client(scopes=["config:read", "configure:write"])
        created = client.post("/configure/sources", json=_WRITE_BODY).json()
        source_id = created["id"]

        fetched = client.get(f"/configure/sources/{source_id}").json()
        assert fetched == created

        listing = client.get("/configure/sources").json()
        assert [row["id"] for row in listing] == [source_id]

        patched = client.patch(
            f"/configure/sources/{source_id}", json={"name": "Renamed"}
        ).json()
        assert patched["name"] == "Renamed"
        assert patched["host"] == created["host"]  # untouched fields survive a patch

        assert client.delete(f"/configure/sources/{source_id}").status_code == 204
        assert client.get(f"/configure/sources/{source_id}").status_code == 404

    def test_get_unknown_id_is_404(self):
        client = _client(scopes=["config:read"])
        assert client.get("/configure/sources/does-not-exist").status_code == 404

    @pytest.mark.parametrize(
        "patch",
        [
            {"name": ""},
            {"host": "  "},
            {"port": 99999},
            {"ssl_mode": "requires"},
            {"ssl_mode": "verify_full"},
        ],
    )
    def test_patch_a_create_would_refuse_is_422(self, patch):
        client = _client(scopes=["config:read", "configure:write"])
        created = client.post("/configure/sources", json=_WRITE_BODY).json()
        response = client.patch(f"/configure/sources/{created['id']}", json=patch)
        assert response.status_code == 422
        assert client.get(f"/configure/sources/{created['id']}").json() == created

    def test_create_with_an_ssl_mode_typo_is_422(self):
        client = _client(scopes=["configure:write"])
        response = client.post(
            "/configure/sources", json={**_WRITE_BODY, "ssl_mode": "requires"}
        )
        assert response.status_code == 422
        assert "ssl_mode" in response.json()["detail"]

    def test_a_credential_rotation_that_lost_its_race_is_409(self):
        client = _client(scopes=["config:read", "configure:write"])
        created = client.post("/configure/sources", json=_WRITE_BODY).json()
        client.fake_store.conflict = True
        response = client.patch(
            f"/configure/sources/{created['id']}", json={"password": "new-password"}
        )
        assert response.status_code == 409
        assert "new-password" not in response.text

    def test_patch_unknown_id_is_404(self):
        client = _client(scopes=["configure:write"])
        response = client.patch("/configure/sources/does-not-exist", json={"name": "x"})
        assert response.status_code == 404

    def test_delete_unknown_id_is_404(self):
        client = _client(scopes=["configure:write"])
        assert client.delete("/configure/sources/does-not-exist").status_code == 404


class TestTestConnection:
    def test_unknown_id_is_404(self):
        client = _client(scopes=["configure:write"])
        assert client.post("/configure/sources/does-not-exist/test").status_code == 404

    def test_loopback_host_is_refused_before_any_connection_attempt(self):
        """The real end-to-end proof (an actual TCP attempt refused before it starts) is
        ``cookbook/94_retail/P1_sources_router.py``; this checks the HTTP contract shape --
        200 with ``ok: false``, never a 5xx, matching the spec's own
        '"a failure is a 200 with ok: false"' description."""
        client = _client(scopes=["configure:write"])
        write_body = dict(_WRITE_BODY, host="localhost")
        created = client.post("/configure/sources", json=write_body).json()
        response = client.post(f"/configure/sources/{created['id']}/test")
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert "loopback" in body["detail"] or "link-local" in body["detail"]
        # ... and the result is recorded on the row, not only returned.
        row = client.fake_store.get(created["id"])
        assert row.status is DataSourceStatus.ERROR
        assert row.last_test_ok is False
        assert row.last_test_message == body["detail"]

    def test_unsupported_source_type_is_a_clean_refusal_not_a_500(self):
        client = _client(scopes=["configure:write"])
        write_body = dict(
            _WRITE_BODY, source_type="snowflake", host="not-a-real-host.example"
        )
        created = client.post("/configure/sources", json=write_body).json()
        response = client.post(f"/configure/sources/{created['id']}/test")
        assert response.status_code == 200
        assert response.json()["ok"] is False
        assert "not implemented" in response.json()["detail"]


class _FakeCursor:
    def __init__(self, connection: "_FakeConnection") -> None:
        self._connection = connection

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, statement: str, params: Any = None) -> None:
        self._connection.executed.append(statement)
        if self._connection.on_execute is not None:
            self._connection.on_execute(statement)

    def fetchone(self) -> Any:
        return (7,)


class _FakeConnection:
    def __init__(self, on_execute: Any = None) -> None:
        self.executed: List[str] = []
        self.on_execute = on_execute
        self.closed = False

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_psycopg(monkeypatch: pytest.MonkeyPatch):
    """Replaces ``psycopg.connect`` and records how it was called."""
    psycopg = pytest.importorskip("psycopg")

    class Recorder:
        def __init__(self) -> None:
            self.calls: List[Dict[str, Any]] = []
            self.connection = _FakeConnection()
            self.error: Optional[BaseException] = None
            self.block: Optional[threading.Event] = None

        def connect(self, **kwargs: Any) -> _FakeConnection:
            self.calls.append(kwargs)
            if self.block is not None:
                self.block.wait(30)
            if self.error is not None:
                raise self.error
            return self.connection

    recorder = Recorder()
    monkeypatch.setattr(psycopg, "connect", recorder.connect)
    return recorder


def _public(monkeypatch: pytest.MonkeyPatch, address: str = "93.184.216.34") -> None:
    """Makes every hostname resolve to ``address``."""
    monkeypatch.setattr(
        sources_module,
        "check_host",
        lambda host, port=None, **kw: HostCheck(address=address),
    )


def _register(client: TestClient, **overrides: Any) -> Dict[str, Any]:
    return client.post("/configure/sources", json=dict(_WRITE_BODY, **overrides)).json()


class TestProbeConnectsToTheCheckedAddress:
    """DNS rebinding: the guard used to resolve a name to check it and let libpq resolve it
    again to connect, so the address checked and the address dialled could differ."""

    def test_the_connection_is_pinned_to_the_address_the_guard_checked(
        self, monkeypatch, fake_psycopg
    ):
        _public(monkeypatch, "10.0.5.20")
        client = _client(scopes=["configure:write"])
        created = _register(client, host="warehouse.customer.example", port=5433)

        body = client.post(f"/configure/sources/{created['id']}/test").json()

        assert body["ok"] is True
        (call,) = fake_psycopg.calls
        assert call["hostaddr"] == "10.0.5.20"
        # The name is still passed, so TLS verification and SNI keep using it.
        assert call["host"] == "warehouse.customer.example"
        assert call["port"] == 5433

    def test_a_name_that_rebinds_between_lookups_is_dialled_at_the_checked_address(
        self, monkeypatch, fake_psycopg
    ):
        lookups: List[str] = []

        def rebinding(host: str, *args: Any, **kwargs: Any):
            lookups.append(host)
            address = "93.184.216.34" if len(lookups) == 1 else "169.254.169.254"
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 5432))]

        monkeypatch.setattr(socket, "getaddrinfo", rebinding)
        client = _client(scopes=["configure:write"])
        created = _register(client, host="rebind.attacker.example")

        client.post(f"/configure/sources/{created['id']}/test")

        assert len(lookups) == 1  # resolved once; nothing left to rebind
        (call,) = fake_psycopg.calls
        assert call["hostaddr"] == "93.184.216.34"

    def test_a_refused_address_never_reaches_the_driver(self, fake_psycopg):
        client = _client(scopes=["configure:write"])
        created = _register(client, host="169.254.169.254")
        body = client.post(f"/configure/sources/{created['id']}/test").json()
        assert body["ok"] is False
        assert fake_psycopg.calls == []

    def test_an_unresolvable_host_is_a_clean_result_not_a_500(self, fake_psycopg):
        client = _client(scopes=["configure:write"])
        created = _register(client, host="this-does-not-resolve.invalid")
        response = client.post(f"/configure/sources/{created['id']}/test")
        assert response.status_code == 200
        assert response.json()["ok"] is False
        assert fake_psycopg.calls == []

    @pytest.mark.parametrize("host", ["a" * 300 + ".example", "bad\x00host"])
    def test_a_malformed_host_is_a_clean_result_not_a_500(self, fake_psycopg, host):
        client = _client(scopes=["configure:write"])
        created = _register(client, host=host)
        response = client.post(f"/configure/sources/{created['id']}/test")
        assert response.status_code == 200
        assert response.json()["ok"] is False


class TestProbeAlwaysReturnsAResult:
    def test_a_failure_after_the_handshake_is_a_recorded_result_not_a_500(
        self, monkeypatch, fake_psycopg
    ):
        """The verification query used to sit outside the try block that classified errors,
        so a server dropping the connection mid-query escaped as a raw 500 and the row's test
        status was never updated."""
        _public(monkeypatch)

        def drop(statement: str) -> None:
            if "information_schema" in statement:
                raise RuntimeError("server closed the connection unexpectedly")

        fake_psycopg.connection.on_execute = drop
        client = _client(scopes=["configure:write"])
        created = _register(client)

        response = client.post(f"/configure/sources/{created['id']}/test")

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert body["detail"] == "Could not connect."
        assert "closed the connection" not in response.text  # never the raw driver text
        row = client.fake_store.get(created["id"])
        assert row.status is DataSourceStatus.ERROR
        assert row.last_test_ok is False
        assert fake_psycopg.connection.closed is True

    def test_a_server_that_stalls_is_cut_off_by_the_deadline(
        self, monkeypatch, fake_psycopg
    ):
        _public(monkeypatch)
        monkeypatch.setattr(sources_module, "_PROBE_DEADLINE_SECONDS", 0.1)
        fake_psycopg.block = threading.Event()
        client = _client(scopes=["configure:write"])
        created = _register(client)
        try:
            response = client.post(f"/configure/sources/{created['id']}/test")
        finally:
            fake_psycopg.block.set()
        assert response.status_code == 200
        assert response.json() == {
            "ok": False,
            "detail": "Connection timed out.",
            "tested_at": response.json()["tested_at"],
        }
        assert client.fake_store.get(created["id"]).status is DataSourceStatus.ERROR

    def test_the_verification_query_runs_under_a_statement_timeout(
        self, monkeypatch, fake_psycopg
    ):
        _public(monkeypatch)
        client = _client(scopes=["configure:write"])
        created = _register(client)
        client.post(f"/configure/sources/{created['id']}/test")
        first = fake_psycopg.connection.executed[0]
        assert first.startswith("SET statement_timeout")

    def test_a_successful_probe_is_recorded_on_the_row(self, monkeypatch, fake_psycopg):
        _public(monkeypatch)
        client = _client(scopes=["configure:write"])
        created = _register(client)
        body = client.post(f"/configure/sources/{created['id']}/test").json()
        assert body["ok"] is True
        row = client.fake_store.get(created["id"])
        assert row.status is DataSourceStatus.CONNECTED
        assert row.last_test_ok is True

    def test_a_missing_stored_secret_is_a_clean_result_not_a_500(
        self, monkeypatch, fake_psycopg
    ):
        _public(monkeypatch)
        client = _client(scopes=["configure:write"])
        created = _register(client)
        client.fake_secrets._by_ref.clear()
        response = client.post(f"/configure/sources/{created['id']}/test")
        assert response.status_code == 200
        assert response.json()["ok"] is False
        assert fake_psycopg.calls == []

    def test_an_unsupported_type_makes_no_connection_and_needs_no_resolution(
        self, fake_psycopg
    ):
        client = _client(scopes=["configure:write"])
        created = _register(client, source_type="snowflake", host="not-a-hostname")
        response = client.post(f"/configure/sources/{created['id']}/test")
        assert "not implemented" in response.json()["detail"]
        assert fake_psycopg.calls == []


class TestPatchCredentialFields:
    """``InternalSourcePatch`` says omitted fields are unchanged, and ``username`` is
    independently settable -- but a username-only patch used to be a silent no-op."""

    def _stored(self, client: TestClient, source_id: str) -> Credential:
        row = client.fake_store.get(source_id)
        return client.fake_secrets.read(row.secret_ref)

    def test_a_username_only_patch_changes_the_username_and_keeps_the_password(self):
        client = _client(scopes=["config:read", "configure:write"])
        created = _register(client)
        old_ref = client.fake_store.get(created["id"]).secret_ref

        response = client.patch(
            f"/configure/sources/{created['id']}", json={"username": "new_reader"}
        )

        assert response.status_code == 200
        assert response.json()["username"] == "new_reader"
        stored = self._stored(client, created["id"])
        assert stored.username == "new_reader"
        assert stored.password == "hunter2"
        # It is a real rotation: the secret was rewritten and the row points at the new one.
        assert client.fake_store.get(created["id"]).secret_ref != old_ref
        assert old_ref not in client.fake_secrets._by_ref

    def test_a_password_only_patch_changes_the_password_and_keeps_the_username(self):
        client = _client(scopes=["config:read", "configure:write"])
        created = _register(client)

        response = client.patch(
            f"/configure/sources/{created['id']}", json={"password": "rotated"}
        )

        assert response.status_code == 200
        stored = self._stored(client, created["id"])
        assert (stored.username, stored.password) == ("egpt_reader", "rotated")

    def test_a_patch_touching_no_credential_field_does_not_read_the_secret(self):
        client = _client(scopes=["config:read", "configure:write"])
        created = _register(client)
        client.fake_secrets._by_ref.clear()  # any read would now raise
        response = client.patch(
            f"/configure/sources/{created['id']}", json={"name": "Renamed"}
        )
        assert response.status_code == 200

    def test_changing_the_auth_method_carries_nothing_over(self):
        client = _client(scopes=["config:read", "configure:write"])
        created = _register(client)
        response = client.patch(
            f"/configure/sources/{created['id']}", json={"auth_method": "api_key"}
        )
        assert response.status_code == 422  # the old password is not an api key
        assert self._stored(client, created["id"]).password == "hunter2"

    def test_changing_the_auth_method_with_the_new_material_succeeds(self):
        client = _client(scopes=["config:read", "configure:write"])
        created = _register(client)
        response = client.patch(
            f"/configure/sources/{created['id']}",
            json={"auth_method": "api_key", "api_key": "sk-new"},
        )
        assert response.status_code == 200
        stored = self._stored(client, created["id"])
        assert stored.auth_method is AuthMethod.API_KEY
        assert stored.api_key == "sk-new"
        assert stored.password is None

    def test_a_partial_patch_with_the_stored_secret_gone_is_a_422_not_a_500(self):
        client = _client(scopes=["config:read", "configure:write"])
        created = _register(client)
        client.fake_secrets._by_ref.clear()
        response = client.patch(
            f"/configure/sources/{created['id']}", json={"username": "x"}
        )
        assert response.status_code == 422
        assert "send the full credential" in response.json()["detail"]


class TestSourcesAreDeploymentWide:
    """A deliberate decision (see the router docstring), pinned so a change to it is a
    conscious one: no route scopes to, or checks, the registering user."""

    def test_another_user_sees_and_can_change_a_source_they_did_not_register(self):
        registrar = _client(scopes=["configure:write"], user_id="registrar")
        created = _register(registrar)
        assert registrar.fake_store.get(created["id"]).created_by == "registrar"

        other = _client(
            scopes=["config:read", "configure:write"],
            user_id="someone-else",
            like=registrar,
        )

        listing = other.get("/configure/sources").json()
        assert [row["id"] for row in listing] == [created["id"]]
        renamed = other.patch(
            f"/configure/sources/{created['id']}", json={"name": "Renamed"}
        )
        assert renamed.json()["name"] == "Renamed"
        assert other.delete(f"/configure/sources/{created['id']}").status_code == 204


class TestClassifyConnectionError:
    def test_auth_failure_is_classified(self):
        exc = Exception('FATAL: password authentication failed for user "reader"')
        assert (
            sources_module._classify_connection_error(exc) == "Authentication failed."
        )

    def test_server_without_ssl_is_classified_as_an_ssl_mode_problem(self):
        # Seen live (#308): the form's default ssl_mode is ``require`` and a warehouse
        # without SSL fails before authentication -- the fix is the source's SSL mode,
        # so the message must point there rather than at the host.
        exc = RuntimeError(
            'connection to server at "10.0.0.5", port 5432 failed: server does not '
            "support SSL, but SSL was required"
        )
        assert "SSL mode" in sources_module._classify_connection_error(exc)

    def test_timeout_is_classified(self):
        exc = Exception("connection timeout expired")
        assert sources_module._classify_connection_error(exc) == "Connection timed out."

    def test_a_missing_role_is_an_authentication_failure(self):
        exc = Exception('FATAL: role "nobody" does not exist')
        assert (
            sources_module._classify_connection_error(exc) == "Authentication failed."
        )

    def test_a_missing_database_is_not_an_authentication_failure(self):
        exc = Exception('FATAL: database "nope" does not exist')
        assert (
            sources_module._classify_connection_error(exc)
            == "The named database does not exist."
        )

    def test_unknown_error_never_leaks_the_raw_exception_text(self):
        exc = Exception(
            "some internal driver detail mentioning warehouse.internal:5432"
        )
        message = sources_module._classify_connection_error(exc)
        assert "warehouse.internal" not in message
        assert message == "Could not connect."


@pytest.mark.parametrize(
    "source_type", ["snowflake", "sap_s4hana", "salesforce", "mysql"]
)
def test_only_postgres_is_in_the_testable_set(source_type: str) -> None:
    assert DataSourceType(source_type) not in sources_module._TESTABLE_SOURCE_TYPES
