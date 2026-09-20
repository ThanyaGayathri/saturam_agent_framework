"""#309 -- the self-serve data-source pipeline end to end, through the product's own entry
points, against real Postgres and real Neo4j.

    register (POST /configure/sources)  ->  test (POST .../test)
      ->  harvest (the harvest-source workflow's executor)  ->  read back
      (GET /semantic/tables?source_id=)  ->  re-harvest from the shell
      (egpt-retail harvest --source-id)  ->  remove (DELETE)

P0-P4 each proved a phase; this is the whole thing in one sitting, over HTTP where the product
speaks HTTP. Two paths run:

* The **refusal path** always: a source on ``127.0.0.1`` is refused by the SSRF guard at test
  time, at harvest time (the row moves to ``harvest_state=error``), and its listing is honestly
  empty (``snapshot.version == "unharvested"``).
* The **success path** when a Postgres is reachable on a NON-loopback address. Locally that is
  this machine's LAN address for the Docker Postgres, detected automatically (override with
  ``EGPT_P5_SOURCE_HOST``); if nothing answers there the path is reported SKIPPED, not faked.
  It registers a throwaway ``p5_cookbook`` schema, tests it (``ok: true``), harvests it for
  real, reads its tables back scoped to the source, shows the operator's listing never sees
  them, re-harvests from the CLI (exit 0, idempotent), lets a rival source claiming the same
  schema stop at ``needs_review``, and removes the source (the listing turns 404).

Needs the control-plane Postgres (``ai``), the ``egpt_warehouse`` database and Neo4j, all
already up locally:

    EGPT_RETAIL_NEO4J_PASSWORD=password PYTHONPATH=product \\
      .venvs/demo/bin/python cookbook/94_retail/P5_self_serve_source_end_to_end.py
"""

import asyncio
import os
import socket
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "product"))

import psycopg  # noqa: E402
from cryptography.fernet import Fernet  # noqa: E402
from egpt.workflow.types import StepInput  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from neo4j import GraphDatabase  # noqa: E402

CONTROL_PLANE = os.environ.get(
    "EGPT_RETAIL_CP_DSN", "postgresql://ai:ai@localhost:5532/ai"
)
WAREHOUSE = os.environ.get(
    "EGPT_RETAIL_WAREHOUSE_DSN", "postgresql://ai:ai@localhost:5532/egpt_warehouse"
)
NEO4J = os.environ.get("EGPT_RETAIL_NEO4J_URI", "bolt://localhost:7687")
NEO4J_PASSWORD = os.environ.get("EGPT_RETAIL_NEO4J_PASSWORD", "password")
CREATED_BY = "p5_cookbook"
SCHEMA = "p5_cookbook"

# The executor and the CLI build RetailSettings() from the environment: same control plane,
# same graph, and -- critically -- the same secret key as the router's store below, or they
# open a different local secret store and cannot decrypt what the router wrote.
cp = urlparse(CONTROL_PLANE)
os.environ.setdefault(
    "EGPT_RETAIL_DB_URL",
    f"postgresql+psycopg://{cp.username}:{cp.password}@{cp.hostname}:{cp.port or 5432}{cp.path}",
)
os.environ.setdefault("EGPT_RETAIL_NEO4J_URI", NEO4J)
os.environ.setdefault("EGPT_RETAIL_NEO4J_PASSWORD", NEO4J_PASSWORD)
os.environ.setdefault("EGPT_RETAIL_SECRET_STORE_PROVIDER", "local_encrypted")
KEY = os.environ.setdefault(
    "EGPT_RETAIL_SECRET_ENCRYPTION_KEY", Fernet.generate_key().decode()
)

from egpt_retail.api.routers import semantic as semantic_module  # noqa: E402
from egpt_retail.api.routers import sources as sources_module  # noqa: E402
from egpt_retail.configure.secrets import LocalEncryptedSecretStore  # noqa: E402
from egpt_retail.configure.store import DataSourceStore  # noqa: E402
from egpt_retail.entrypoints import harvest as harvest_cli  # noqa: E402
from egpt_retail.executors.data_source_harvest import harvest_source  # noqa: E402
from egpt_retail.semantic.store import SemanticGraphStore  # noqa: E402

print(
    "P5 -- self-serve data source, end to end, over HTTP against real Postgres and Neo4j"
)
print("=" * 78)


def lan_address() -> str:
    """The address other hosts reach this machine on -- what the Docker Postgres, published on
    0.0.0.0, also answers on. No packet is sent; connect() on a UDP socket only picks the
    route."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        return probe.getsockname()[0]
    finally:
        probe.close()


def reachable_postgres(host: str) -> bool:
    parts = urlparse(WAREHOUSE)
    try:
        conn = psycopg.connect(
            host=host,
            port=parts.port or 5432,
            dbname=parts.path.lstrip("/"),
            user=parts.username,
            password=parts.password,
            connect_timeout=3,
            sslmode="prefer",
        )
        conn.close()
        return True
    except Exception:
        return False


# --- the real routers on one app, backed by the real stores --------------------------------

control_plane = psycopg.connect(CONTROL_PLANE)
secrets = LocalEncryptedSecretStore(control_plane, KEY)
secrets.ensure_schema()
store = DataSourceStore(control_plane)
store.ensure_schema()
with control_plane.cursor() as cursor:
    cursor.execute(
        "DELETE FROM configure.data_source WHERE created_by = %s", (CREATED_BY,)
    )
control_plane.commit()

driver = GraphDatabase.driver(NEO4J, auth=("neo4j", NEO4J_PASSWORD))
graph = SemanticGraphStore(driver=driver)
graph.ensure_indexes()
graph.drop_schema(SCHEMA)

app = FastAPI()
app.include_router(sources_module.router)
app.include_router(semantic_module.router)
app.dependency_overrides[sources_module._source_store] = lambda: store
app.dependency_overrides[sources_module._secret_store] = lambda: secrets


@app.middleware("http")
async def _auth(request, call_next):
    request.state.authorization_enabled = True
    request.state.scopes = ["config:read", "configure:write", "semantic:read"]
    request.state.admin_scope = None
    request.state.user_id = CREATED_BY
    return await call_next(request)


client = TestClient(app)
warehouse_parts = urlparse(WAREHOUSE)


def register(name: str, host: str) -> dict:
    response = client.post(
        "/configure/sources",
        json={
            "source_type": "postgres",
            "name": name,
            "host": host,
            "port": warehouse_parts.port or 5432,
            "database_name": warehouse_parts.path.lstrip("/"),
            "schema": SCHEMA,
            "auth_method": "username_password",
            "username": warehouse_parts.username,
            "password": warehouse_parts.password,
            "ssl_mode": "prefer",  # the Docker Postgres has no SSL; a warehouse keeps require
            "permission_level": "read_only",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert "password" not in body and body["has_credentials"] is True
    assert body["harvest_state"] == "not_started"
    return body


def run_harvest(source_id: str):
    return asyncio.run(
        harvest_source(StepInput(additional_data={"source_id": source_id}))
    )


try:
    # --- refusal path: always ---------------------------------------------------------------
    print("\n[refusal path] a source on 127.0.0.1")
    loopback = register("P5 loopback probe", "127.0.0.1")
    print(f"PASS: registered {loopback['id']} (201, no credential in the body)")

    test = client.post(f"/configure/sources/{loopback['id']}/test").json()
    assert test["ok"] is False and "loopback" in test["detail"]
    print(f"PASS: test refused before connecting: {test['detail']!r}")

    output = run_harvest(loopback["id"])
    assert output.success is False and "loopback" in (output.error or "")
    state = client.get(f"/configure/sources/{loopback['id']}").json()["harvest_state"]
    assert state == "error"
    print(f"PASS: harvest refused, harvest_state={state!r}")

    listing = client.get(f"/semantic/tables?source_id={loopback['id']}").json()
    assert listing["tables"] == [] and listing["snapshot"]["version"] == "unharvested"
    print("PASS: its listing is honestly empty (snapshot 'unharvested')")

    assert harvest_cli.main(["--source-id", loopback["id"]]) == 1
    print("PASS: `egpt-retail harvest --source-id` exits 1 on the same refusal")

    assert client.get("/semantic/tables?source_id=p5_no_such_source").status_code == 404
    print("PASS: an unregistered source_id is a 404, not an empty list")

    # --- success path: when a non-loopback Postgres answers ---------------------------------
    host = os.environ.get("EGPT_P5_SOURCE_HOST") or lan_address()
    print(f"\n[success path] a source on {host}")
    if not reachable_postgres(host):
        print(
            f"SKIPPED: no Postgres answers on {host}:{warehouse_parts.port or 5432}. "
            "Set EGPT_P5_SOURCE_HOST to a non-loopback address the warehouse listens on."
        )
    else:
        warehouse = psycopg.connect(WAREHOUSE)
        warehouse.autocommit = True
        with warehouse.cursor() as cursor:
            cursor.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
            cursor.execute(f"CREATE SCHEMA {SCHEMA}")
            cursor.execute(
                f"CREATE TABLE {SCHEMA}.store (code text PRIMARY KEY, region text NOT NULL)"
            )
            cursor.execute(
                f"CREATE TABLE {SCHEMA}.store_visit (id integer PRIMARY KEY, code text NOT NULL, basket numeric(10,2))"
            )
            cursor.execute(f"INSERT INTO {SCHEMA}.store VALUES ('S1', 'north')")
            cursor.execute(f"INSERT INTO {SCHEMA}.store_visit VALUES (1, 'S1', 42.10)")
        try:
            source = register("P5 cookbook warehouse", host)
            print(f"PASS: registered {source['id']}")

            test = client.post(f"/configure/sources/{source['id']}/test").json()
            assert test["ok"] is True, test
            print(f"PASS: test connected: {test['detail']!r}")

            output = run_harvest(source["id"])
            assert output.success is True, output.error
            after = client.get(f"/configure/sources/{source['id']}").json()
            assert after["harvest_state"] == "modelled" and after["last_harvest_at"]
            print(
                f"PASS: harvested {output.content['tables']} tables / "
                f"{output.content['columns']} columns; harvest_state='modelled', "
                f"last_harvest_at={after['last_harvest_at']}"
            )

            listing = client.get(f"/semantic/tables?source_id={source['id']}").json()
            names = sorted(t["name"] for t in listing["tables"])
            assert names == ["store", "store_visit"]
            assert listing["snapshot"]["version"] == output.content["snapshot_version"]
            assert listing["snapshot"]["source"] == source["name"]
            print(
                f"PASS: GET /semantic/tables?source_id= lists {names} under snapshot "
                f"{listing['snapshot']['version']!r}"
            )

            operator = client.get("/semantic/tables").json()
            assert SCHEMA not in {t["schema"] for t in operator["tables"]}
            print("PASS: the operator's listing never sees the source's tables (#307)")

            assert harvest_cli.main(["--source-id", source["id"]]) == 0
            again = client.get(f"/semantic/tables?source_id={source['id']}").json()
            assert len(again["tables"]) == 2
            print(
                "PASS: `egpt-retail harvest --source-id` re-harvested (exit 0), still 2 tables"
            )

            rival = register("P5 rival claiming the same schema", host)
            rival_output = run_harvest(rival["id"])
            rival_state = client.get(f"/configure/sources/{rival['id']}").json()[
                "harvest_state"
            ]
            assert rival_output.success is False and rival_state == "needs_review"
            print(
                f"PASS: a rival source claiming {SCHEMA!r} stopped at 'needs_review': "
                f"{rival_output.error!r}"
            )

            assert (
                client.delete(f"/configure/sources/{source['id']}").status_code == 204
            )
            assert (
                client.get(f"/semantic/tables?source_id={source['id']}").status_code
                == 404
            )
            print("PASS: removed (204); its listing is now a 404")
        finally:
            with warehouse.cursor() as cursor:
                cursor.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
            warehouse.close()
finally:
    for row in client.get("/configure/sources").json():
        if row["name"].startswith("P5 "):
            client.delete(f"/configure/sources/{row['id']}")
    graph.drop_schema(SCHEMA)
    control_plane.close()
    driver.close()

print("=" * 78)
print("P5 evidence complete.")
