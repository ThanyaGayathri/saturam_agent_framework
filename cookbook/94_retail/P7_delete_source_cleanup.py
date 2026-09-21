"""#302 -- deleting a data source removes what its harvest left behind, so a new source can take
the same schema. Through the product's own entry points, against real Postgres and real Neo4j.

The bug: ``DELETE /configure/sources/{id}`` removed the row and the secret and nothing else. The
tables the source's harvest wrote into the semantic graph stayed, still tagged with its id and
still claiming their schema, so registering a new source with the same schema was refused by the
schema-collision guard -- on behalf of a source that no longer existed -- and the new source
stopped in ``needs_review`` with no way out.

Scenarios, each asserted (not just printed):

1. **The regression.** Register schema X, harvest (``modelled``), DELETE, register a new source on
   X, harvest: ``modelled``, not ``needs_review``. The delete also removed the source's tables from
   the graph and its saved KPI suggestion runs.
2. **A graph orphaned before the fix heals itself.** Tables tagged with an id no source has claim
   the schema; a new source on that schema reclaims it and is ``modelled``.
3. **A real conflict is still a conflict.** A second source on a schema a *registered* source holds
   stops at ``needs_review`` and the message names the holder; nothing of the holder's is touched.
   Deleting the holder and retrying then works.
4. **A graph that cannot be reached keeps the source.** The delete answers 503 and the source, its
   secret and its tables are all still there; the same delete succeeds once the graph is back.
5. **A source that was never harvested** is deleted without needing the graph at all.

And throughout: every *other* registered source, and the operator's own untagged tables, are
exactly as they were -- the script lists them before and after.

Uses a throwaway ``p7_delete`` schema in ``egpt_warehouse`` and touches only the sources it
registers (removed by id, never by listing). The success paths need a Postgres reachable on a
NON-loopback address (this machine's LAN address for the Docker one, detected automatically;
override with ``EGPT_P7_SOURCE_HOST``) -- the SSRF guard refuses ``localhost`` by design -- and
report SKIPPED, not faked, when nothing answers there.

Needs the control-plane Postgres (``ai``), the ``egpt_warehouse`` database and Neo4j, all already
up locally:

    EGPT_RETAIL_NEO4J_PASSWORD=password PYTHONPATH=product \\
      .venvs/demo/bin/python cookbook/94_retail/P7_delete_source_cleanup.py
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
SCHEMA = "p7_delete"

# The executor and the router build RetailSettings() from the environment: same control plane,
# same graph, and the same secret key as the store below.
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

from egpt_retail.api.routers import sources as sources_module  # noqa: E402
from egpt_retail.app.settings import RetailSettings  # noqa: E402
from egpt_retail.configure.secrets import (  # noqa: E402
    LocalEncryptedSecretStore,
    SecretNotFoundError,
)
from egpt_retail.configure.store import DataSourceStore  # noqa: E402
from egpt_retail.executors.data_source_harvest import harvest_source  # noqa: E402
from egpt_retail.semantic.harvest import (  # noqa: E402
    Harvest,
    HarvestedColumn,
    HarvestedTable,
)
from egpt_retail.semantic.store import SemanticGraphStore  # noqa: E402
from egpt_retail.suggestions.runs import SuggestionRunStore  # noqa: E402

print("P7 -- deleting a data source removes its tables, so the schema can be reused")
print("=" * 78)


def lan_address() -> str:
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


host = os.environ.get("EGPT_P7_SOURCE_HOST") or lan_address()
if not reachable_postgres(host):
    print(f"SKIPPED: no Postgres answers on {host}:5432 -- nothing to harvest.")
    sys.exit(0)

control_plane = psycopg.connect(CONTROL_PLANE)
secrets = LocalEncryptedSecretStore(control_plane, KEY)
secrets.ensure_schema()
store = DataSourceStore(control_plane)
store.ensure_schema()
runs = SuggestionRunStore(control_plane)
runs.ensure_schema()
driver = GraphDatabase.driver(NEO4J, auth=("neo4j", NEO4J_PASSWORD))
graph = SemanticGraphStore(driver=driver)
graph.ensure_indexes()

app = FastAPI()
app.include_router(sources_module.router)
app.dependency_overrides[sources_module._source_store] = lambda: store
app.dependency_overrides[sources_module._secret_store] = lambda: secrets


@app.middleware("http")
async def _fake_auth(request, call_next):
    request.state.authorization_enabled = True
    request.state.scopes = ["config:read", "configure:write"]
    request.state.admin_scope = None
    request.state.user_id = "p7_cookbook"
    return await call_next(request)


client = TestClient(app)
created = []


def graph_tables(schema: str = SCHEMA):
    with driver.session() as session:
        return session.run(
            "MATCH (t:SemanticTable {schema: $s}) "
            "RETURN coalesce(t.source_id, '') AS sid, count(*) AS n",
            s=schema,
        ).data()


def operator_tables() -> int:
    with driver.session() as session:
        return session.run(
            "MATCH (t:SemanticTable) WHERE t.source_id IS NULL OR t.source_id = '' "
            "RETURN count(t) AS n"
        ).single()["n"]


def registered_ids() -> list:
    with control_plane.cursor() as cursor:
        cursor.execute("SELECT id FROM configure.data_source ORDER BY id")
        rows = [row[0] for row in cursor.fetchall()]
    control_plane.rollback()
    return rows


def register(name: str, source_host: str = host) -> str:
    response = client.post(
        "/configure/sources",
        json={
            "source_type": "postgres",
            "name": name,
            "host": source_host,
            "port": 5532,
            "database_name": "egpt_warehouse",
            "schema": SCHEMA,
            "auth_method": "username_password",
            "username": "ai",
            "password": "ai",
            "ssl_mode": "prefer",
        },
    )
    assert response.status_code == 201, response.text
    created.append(response.json()["id"])
    return response.json()["id"]


def harvest(source_id: str):
    result = asyncio.run(
        harvest_source(StepInput(additional_data={"source_id": source_id}))
    )
    return result, store.get(source_id).harvest_state.value


def delete(source_id: str):
    response = client.delete(f"/configure/sources/{source_id}")
    if response.status_code == 204:
        created.remove(source_id)
    return response


def one_run_for(source_id: str) -> None:
    record = runs.create(None, (), "p7_cookbook", source_id)
    runs.mark_running(record.identifier)
    runs.mark_succeeded(record.identifier, {"suggestions": [], "notes": ""})


def runs_for(source_id: str) -> int:
    with control_plane.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM semantic.kpi_suggestion_run WHERE source_id = %s",
            (source_id,),
        )
        n = cursor.fetchone()[0]
    control_plane.rollback()
    return n


with psycopg.connect(WAREHOUSE, autocommit=True) as warehouse:
    warehouse.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    warehouse.execute(
        f"CREATE TABLE IF NOT EXISTS {SCHEMA}.shop (id int primary key, name text)"
    )
    warehouse.execute(
        f"CREATE TABLE IF NOT EXISTS {SCHEMA}.sale (id int primary key, shop_id int, amount numeric)"
    )

graph.drop_schema(SCHEMA)
others_before = registered_ids()
operator_before = operator_tables()
print(
    f"before: {len(others_before)} other registered source(s), {operator_before} operator table(s)"
)

try:
    # --- 1. the regression --------------------------------------------------------------------
    a = register("P7 first")
    _, state = harvest(a)
    assert state == "modelled", state
    assert graph_tables() == [{"sid": a, "n": 2}], graph_tables()
    one_run_for(a)
    assert runs_for(a) == 1
    secret_ref = store.get(a).secret_ref
    print(
        f"1. registered {a} on schema {SCHEMA}: modelled, 2 graph tables, 1 suggestion run"
    )

    assert delete(a).status_code == 204
    assert graph_tables() == [], graph_tables()
    assert runs_for(a) == 0
    assert store.get(a) is None
    try:
        secrets.read(secret_ref)
        raise AssertionError("the secret should have been deleted with the source")
    except SecretNotFoundError:
        pass
    print(
        "   PASS: DELETE removed its graph tables, its suggestion runs, its row and its secret"
    )

    b = register("P7 second")
    result, state = harvest(b)
    assert state == "modelled", (state, result.error)
    assert graph_tables() == [{"sid": b, "n": 2}], graph_tables()
    print(
        f"   PASS: a new source ({b}) on the same schema harvested to 'modelled', not 'needs_review'"
    )
    assert delete(b).status_code == 204

    # --- 2. a graph orphaned before the fix heals itself ------------------------------------
    orphan = "ds_p7_orphan"
    graph.write_harvest(
        Harvest(
            schema=SCHEMA,
            tables=(
                HarvestedTable(
                    schema=SCHEMA,
                    name="shop",
                    columns=(HarvestedColumn("id", "integer", False, True),),
                    row_count=1,
                ),
            ),
            source="p7",
        ),
        snapshot_version="p7.orphan",
        source_id=orphan,
    )
    assert graph_tables() == [{"sid": orphan, "n": 1}]
    c = register("P7 third")
    result, state = harvest(c)
    assert state == "modelled", (state, result.error)
    assert graph_tables() == [{"sid": c, "n": 2}], graph_tables()
    print(
        f"2. PASS: tables left by a source no one has ({orphan}) were reclaimed; {c} is 'modelled'"
    )

    # --- 3. a real conflict is still a conflict -----------------------------------------------
    d = register("P7 rival")
    result, state = harvest(d)
    assert state == "needs_review", state
    assert "P7 third" in result.error and c in result.error, result.error
    assert graph_tables() == [{"sid": c, "n": 2}], (
        "the holder's tables must be untouched"
    )
    print(
        f"3. PASS: a registered holder is respected -- {d} is 'needs_review': {result.error}"
    )
    assert delete(c).status_code == 204
    result, state = harvest(d)
    assert state == "modelled", (state, result.error)
    print(
        "   PASS: once the holder is deleted, retrying the rival is enough -- 'modelled'"
    )

    # --- 4. a graph that cannot be reached keeps the source ----------------------------------
    real_cleaner = sources_module._graph_cleaner
    dead = RetailSettings(retail_neo4j_uri="bolt://127.0.0.1:1")
    app.dependency_overrides[sources_module._graph_cleaner] = lambda: (
        lambda sid: sources_module._forget_source_tables(dead, sid)
    )
    ref = store.get(d).secret_ref
    refused = client.delete(f"/configure/sources/{d}")
    assert refused.status_code == 503, refused.status_code
    assert "not deleted" in refused.json()["detail"]
    assert store.get(d) is not None and secrets.read(ref) is not None
    assert graph_tables() == [{"sid": d, "n": 2}]
    print(
        f"4. PASS: graph unreachable -> {refused.status_code} ({refused.json()['detail']})"
    )
    print("   PASS: the source, its secret and its tables are all still there")
    app.dependency_overrides.pop(sources_module._graph_cleaner)
    assert delete(d).status_code == 204
    assert graph_tables() == []
    print("   PASS: the same delete succeeds once the graph is back")

    # --- 5. never harvested: no graph needed --------------------------------------------------
    e = register("P7 never harvested")
    app.dependency_overrides[sources_module._graph_cleaner] = lambda: (
        lambda sid: sources_module._forget_source_tables(dead, sid)
    )
    assert delete(e).status_code == 204
    app.dependency_overrides.pop(sources_module._graph_cleaner)
    print("5. PASS: a source that was never harvested deletes with the graph down")

    # --- throughout: nothing else was touched -------------------------------------------------
    assert registered_ids() == others_before, (registered_ids(), others_before)
    assert operator_tables() == operator_before
    print(
        f"throughout: PASS -- the {len(others_before)} other registered source(s) and the "
        f"{operator_before} operator table(s) are exactly as they were"
    )
finally:
    for source_id in list(created):
        client.delete(f"/configure/sources/{source_id}")
    graph.drop_schema(SCHEMA)
    with control_plane.cursor() as cursor:
        cursor.execute(
            "DELETE FROM semantic.kpi_suggestion_run WHERE ask = 'p7_cookbook'"
        )
    control_plane.commit()
    with psycopg.connect(WAREHOUSE, autocommit=True) as warehouse:
        warehouse.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
    driver.close()
    control_plane.close()

print("=" * 78)
print("P7 evidence complete.")
