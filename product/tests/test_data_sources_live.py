"""Live regression tests for the self-serve data-source pipeline (#302, #309), against real
Postgres and Neo4j, through the product's own entry points: the ``/configure/sources`` and
``/semantic`` routers over HTTP, ``harvest_source`` (the ``harvest-source`` workflow's step)
and ``egpt-retail harvest --source-id``.

Gated the way ``test_semantic_live.py`` is -- ``EGPT_INTEGRATION_POSTGRES`` and
``EGPT_INTEGRATION_NEO4J`` -- so ``integration.yml`` can pick it up and the unit run stays
database-free. The *successful* harvest additionally needs a Postgres reachable on a
non-loopback address, because the SSRF guard refuses loopback by design: set
``EGPT_INTEGRATION_SOURCE_HOST`` to one (locally, the machine's LAN IP for the Docker
Postgres; in a compose network, the service name). Without it the refusal path still runs
and the success path is skipped, not faked.

Locally:

    EGPT_INTEGRATION_POSTGRES=1 EGPT_INTEGRATION_NEO4J=1 \\
    EGPT_ONTOLOGY_NEO4J_URI=bolt://localhost:7687 EGPT_RETAIL_NEO4J_PASSWORD=password \\
    EGPT_INTEGRATION_SOURCE_HOST=$(ipconfig getifaddr en0) \\
    pytest product/tests/test_data_sources_live.py

Everything written here is prefixed ``livetest_`` and removed at the end of the module.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Iterator
from urllib.parse import urlparse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from egpt_retail.api.routers import semantic as semantic_module
from egpt_retail.api.routers import sources as sources_module
from egpt_retail.configure.secrets import LocalEncryptedSecretStore
from egpt_retail.configure.store import DataSourceStore
from egpt_retail.entrypoints import harvest as harvest_cli
from egpt_retail.semantic.store import SemanticGraphStore

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
NEO4J_PASSWORD = os.environ.get("EGPT_RETAIL_NEO4J_PASSWORD", "egptretail")
SOURCE_HOST = os.environ.get("EGPT_INTEGRATION_SOURCE_HOST", "")
MARKER = "livetest"
SCHEMA = "livetest_p5"
SCOPES = ["config:read", "configure:write", "semantic:read"]


def _warehouse_parts() -> Any:
    return urlparse(WAREHOUSE)


@pytest.fixture(scope="module")
def executor_env() -> Iterator[None]:
    """``harvest_source`` and the CLI build ``RetailSettings()`` from the environment, so the
    executor must see the same control plane, graph and *secret key* this module's stores use
    -- a different key opens a different local secret store and cannot decrypt what the
    router stored."""
    from cryptography.fernet import Fernet

    cp = urlparse(CONTROL_PLANE)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv(
            "EGPT_RETAIL_DB_URL",
            f"postgresql+psycopg://{cp.username}:{cp.password}@{cp.hostname}:{cp.port or 5432}{cp.path}",
        )
        mp.setenv("EGPT_RETAIL_NEO4J_URI", NEO4J)
        mp.setenv("EGPT_RETAIL_NEO4J_PASSWORD", NEO4J_PASSWORD)
        mp.setenv("EGPT_RETAIL_SECRET_STORE_PROVIDER", "local_encrypted")
        mp.setenv("EGPT_RETAIL_SECRET_ENCRYPTION_KEY", Fernet.generate_key().decode())
        yield


@pytest.fixture(scope="module")
def control_plane(executor_env: None) -> Iterator[Any]:
    psycopg = pytest.importorskip("psycopg")
    conn = psycopg.connect(CONTROL_PLANE)
    yield conn
    conn.close()


@pytest.fixture(scope="module")
def driver() -> Iterator[Any]:
    neo4j = pytest.importorskip("neo4j")
    d = neo4j.GraphDatabase.driver(NEO4J, auth=("neo4j", NEO4J_PASSWORD))
    yield d
    SemanticGraphStore(driver=d).drop_schema(SCHEMA)
    d.close()


@pytest.fixture(scope="module")
def client(control_plane: Any) -> Iterator[TestClient]:
    secrets = LocalEncryptedSecretStore(
        control_plane, os.environ["EGPT_RETAIL_SECRET_ENCRYPTION_KEY"]
    )
    secrets.ensure_schema()
    store = DataSourceStore(control_plane)
    store.ensure_schema()

    app = FastAPI()
    app.include_router(sources_module.router)
    app.include_router(semantic_module.router)
    app.dependency_overrides[sources_module._source_store] = lambda: store
    app.dependency_overrides[sources_module._secret_store] = lambda: secrets

    @app.middleware("http")
    async def _auth(request, call_next):  # type: ignore[no-untyped-def]
        request.state.authorization_enabled = True
        request.state.scopes = SCOPES
        request.state.admin_scope = None
        request.state.user_id = MARKER
        return await call_next(request)

    with TestClient(app) as c:
        yield c
        # Through the API, so the stored secrets go with the rows.
        for row in c.get("/configure/sources").json():
            if row["name"].startswith(MARKER):
                c.delete(f"/configure/sources/{row['id']}")


def _register(client: TestClient, name: str, host: str, schema: str) -> dict:
    parts = _warehouse_parts()
    response = client.post(
        "/configure/sources",
        json={
            "source_type": "postgres",
            "name": name,
            "host": host,
            "port": parts.port or 5432,
            "database_name": parts.path.lstrip("/"),
            "schema": schema,
            "auth_method": "username_password",
            "username": parts.username,
            "password": parts.password,
            # The compose Postgres has no SSL; a real warehouse keeps `require`.
            "ssl_mode": "prefer",
            "permission_level": "read_only",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _run_harvest(source_id: str) -> Any:
    from egpt.workflow.types import StepInput

    from egpt_retail.executors.data_source_harvest import harvest_source

    return asyncio.run(
        harvest_source(StepInput(additional_data={"source_id": source_id}))
    )


class TestRefusalPath:
    """What the product does with a source it must not connect to -- the part that runs in
    every environment, because loopback is always there to be refused."""

    def test_loopback_is_refused_at_test_harvest_and_listing(
        self, client: TestClient
    ) -> None:
        source = _register(client, f"{MARKER} loopback", "127.0.0.1", SCHEMA)
        assert source["harvest_state"] == "not_started"
        assert source["last_harvest_at"] is None

        test = client.post(f"/configure/sources/{source['id']}/test").json()
        assert test["ok"] is False
        assert "loopback" in test["detail"]

        output = _run_harvest(source["id"])
        assert output.success is False
        assert "loopback" in (output.error or "")

        after = client.get(f"/configure/sources/{source['id']}").json()
        assert after["harvest_state"] == "error"
        assert after["last_harvest_at"] is None

        listing = client.get(f"/semantic/tables?source_id={source['id']}")
        assert listing.status_code == 200
        assert listing.json()["tables"] == []
        assert listing.json()["snapshot"]["version"] == "unharvested"

        # And the CLI is the same executor with an exit status.
        assert harvest_cli.main(["--source-id", source["id"]]) == 1

    def test_an_unregistered_source_is_a_404_not_an_empty_listing(
        self, client: TestClient
    ) -> None:
        assert client.get("/semantic/tables?source_id=livetest_nope").status_code == 404


@pytest.fixture(scope="module")
def warehouse_schema() -> Iterator[None]:
    """A throwaway schema in the real warehouse for the success path to harvest."""
    psycopg = pytest.importorskip("psycopg")
    conn = psycopg.connect(WAREHOUSE)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        cur.execute(f"CREATE SCHEMA {SCHEMA}")
        cur.execute(
            f"CREATE TABLE {SCHEMA}.livetest_store (code text PRIMARY KEY, region text NOT NULL)"
        )
        cur.execute(f"INSERT INTO {SCHEMA}.livetest_store VALUES ('S1', 'north')")
    try:
        yield
    finally:
        with conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        conn.close()


@pytest.mark.skipif(
    not SOURCE_HOST,
    reason="EGPT_INTEGRATION_SOURCE_HOST unset: no non-loopback Postgres to harvest for real",
)
class TestSuccessPath:
    """Register -> test -> harvest -> read back, for real, against a throwaway schema."""

    def test_register_test_harvest_read_back_and_remove(
        self, client: TestClient, driver: Any, warehouse_schema: None
    ) -> None:
        SemanticGraphStore(driver=driver).drop_schema(SCHEMA)
        source = _register(client, f"{MARKER} warehouse", SOURCE_HOST, SCHEMA)

        test = client.post(f"/configure/sources/{source['id']}/test").json()
        assert test["ok"] is True, test
        assert "1 table(s)" in test["detail"]

        output = _run_harvest(source["id"])
        assert output.success is True, output.error
        assert output.content["tables"] == 1

        after = client.get(f"/configure/sources/{source['id']}").json()
        assert after["harvest_state"] == "modelled"
        assert after["last_harvest_at"] is not None
        assert after["last_harvest_id"] is None  # no run id outside a workflow run

        listing = client.get(f"/semantic/tables?source_id={source['id']}").json()
        assert [(t["schema"], t["name"]) for t in listing["tables"]] == [
            (SCHEMA, "livetest_store")
        ]
        assert listing["snapshot"]["version"] == output.content["snapshot_version"]
        assert listing["snapshot"]["source"] == source["name"]

        # The operator's own listing never sees it (#307).
        operator = client.get("/semantic/tables").json()
        assert SCHEMA not in {t["schema"] for t in operator["tables"]}

        # Re-harvesting from the shell is the same executor, idempotent, exit 0.
        assert harvest_cli.main(["--source-id", source["id"]]) == 0
        again = client.get(f"/semantic/tables?source_id={source['id']}").json()
        assert len(again["tables"]) == 1

        # A second source claiming the same schema stops at needs_review, never overwriting.
        rival = _register(client, f"{MARKER} rival", SOURCE_HOST, SCHEMA)
        rival_output = _run_harvest(rival["id"])
        assert rival_output.success is False
        assert (
            client.get(f"/configure/sources/{rival['id']}").json()["harvest_state"]
            == "needs_review"
        )

        assert client.delete(f"/configure/sources/{source['id']}").status_code == 204
        assert (
            client.get(f"/semantic/tables?source_id={source['id']}").status_code == 404
        )
