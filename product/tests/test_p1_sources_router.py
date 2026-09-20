"""#305 -- ``api/routers/sources.py`` against a real FastAPI app (``sources_module.router``
included for real, scopes enforced through FastAPI's own ``solve_dependencies`` -- the same
shape ``test_kpis_router.py``'s ``_health_app`` documents), with in-memory fakes standing in
for ``DataSourceStore``/``SecretStore`` so none of this needs Postgres. The real store and
secret store, against real Postgres, are proved by ``cookbook/94_retail/P0_data_source_registration.py``
(the store directly) and ``cookbook/94_retail/P1_sources_router.py`` (this router, wired for
real, including one live connection attempt to prove the SSRF guard fires end to end).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Dict, Optional

import pytest
from egpt_retail.api.routers import sources as sources_module
from egpt_retail.configure.secrets import SecretNotFoundError
from egpt_retail.configure.store import DataSourceConflictError
from egpt_retail.contracts.data_source import (
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


def _client(*, scopes) -> TestClient:
    app = FastAPI()
    app.include_router(sources_module.router)
    fake_store = _FakeDataSourceStore()
    fake_secrets = _FakeSecretStore()
    app.dependency_overrides[sources_module._source_store] = lambda: fake_store
    app.dependency_overrides[sources_module._secret_store] = lambda: fake_secrets

    @app.middleware("http")
    async def _fake_auth(request, call_next):
        request.state.authorization_enabled = True
        request.state.scopes = scopes
        request.state.admin_scope = None
        request.state.user_id = "test-user"
        return await call_next(request)

    client = TestClient(app)
    client.fake_store = fake_store  # type: ignore[attr-defined]
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


class TestRefusedHostReason:
    """Pure logic, no HTTP needed."""

    def test_loopback_is_refused(self):
        assert sources_module._refused_host_reason("127.0.0.1") is not None

    def test_localhost_hostname_is_refused(self):
        assert sources_module._refused_host_reason("localhost") is not None

    def test_link_local_is_refused(self):
        # 169.254.169.254 -- the AWS/Azure/GCP cloud metadata endpoint. The single highest-
        # value SSRF target this guard exists to close off.
        assert sources_module._refused_host_reason("169.254.169.254") is not None

    def test_a_private_rfc1918_address_is_not_refused(self):
        """Deliberately allowed -- see the module docstring. A customer's own warehouse
        legitimately lives here."""
        assert sources_module._refused_host_reason("10.0.5.20") is None

    def test_unresolvable_host_is_not_refused_here(self):
        """DNS failure surfaces as 'unreachable' once the real connection is attempted, not
        as a refusal -- the two are different failure modes with different causes."""
        assert (
            sources_module._refused_host_reason("this-does-not-resolve.invalid.example")
            is None
        )


class TestClassifyConnectionError:
    def test_auth_failure_is_classified(self):
        exc = Exception('FATAL: password authentication failed for user "reader"')
        assert (
            sources_module._classify_connection_error(exc) == "Authentication failed."
        )

    def test_timeout_is_classified(self):
        exc = Exception("connection timeout expired")
        assert sources_module._classify_connection_error(exc) == "Connection timed out."

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
