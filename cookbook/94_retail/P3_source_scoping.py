"""#307 -- a self-serve source's tables and the operator's share one graph without seeing
each other, proved over real HTTP against real Postgres and real Neo4j.

The case that matters is a shared bare name: the operator's ``silver.crm_contact`` (real, from
the warehouse's own harvest) and a registered source's ``crm_contact``. Before scoping, the
second harvest turned every bare ``crm_contact`` lookup ambiguous and let a customer's table
make an operator KPI look ready. Here:

- ``GET /semantic/tables`` (no ``source_id``) never lists the source's tables, and a bare
  ``crm_contact`` still resolves to silver.
- ``GET /semantic/tables?source_id=`` lists only the source's, with its own snapshot.
- An unregistered ``source_id`` is a 404, not an empty list.
- The source's read guard allows its own table and refuses silver's.

Needs the control-plane Postgres, a Neo4j that already holds the ``silver`` harvest (any
earlier K4/K6/K7 cookbook run), all already up locally:

    EGPT_RETAIL_NEO4J_PASSWORD=password PYTHONPATH=product \
        .venvs/demo/bin/python cookbook/94_retail/P3_source_scoping.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "product"))

import psycopg  # noqa: E402
from cryptography.fernet import Fernet  # noqa: E402
from egpt.exceptions import SQLGuardRefusal  # noqa: E402
from egpt_retail.api.routers import semantic as semantic_module  # noqa: E402
from egpt_retail.app.sql_guard import build_source_read_guard  # noqa: E402
from egpt_retail.configure.secrets import LocalEncryptedSecretStore  # noqa: E402
from egpt_retail.configure.store import DataSourceStore  # noqa: E402
from egpt_retail.contracts.data_source import (  # noqa: E402
    AuthMethod,
    Credential,
    DataSourceCreate,
    DataSourceType,
    HarvestState,
)
from egpt_retail.semantic.harvest import (  # noqa: E402
    Harvest,
    HarvestedColumn,
    HarvestedTable,
)
from egpt_retail.semantic.store import SemanticGraphStore  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from neo4j import GraphDatabase  # noqa: E402

CONTROL_PLANE = os.environ.get(
    "EGPT_RETAIL_CP_DSN", "postgresql://ai:ai@localhost:5532/ai"
)
NEO4J = os.environ.get("EGPT_RETAIL_NEO4J_URI", "bolt://localhost:7687")
NEO4J_AUTH = ("neo4j", os.environ.get("EGPT_RETAIL_NEO4J_PASSWORD", "password"))
os.environ.setdefault("EGPT_RETAIL_NEO4J_URI", NEO4J)
os.environ.setdefault(
    "EGPT_RETAIL_DB_URL", "postgresql+psycopg://ai:ai@localhost:5532/ai"
)
CREATED_BY = "p3_cookbook"
SCHEMA = "p3_cookbook"

print("P3 -- source-scoped semantic reads, over HTTP, against real Postgres and Neo4j")
print("=" * 78)

control_plane = psycopg.connect(CONTROL_PLANE)
key = os.environ.setdefault(
    "EGPT_RETAIL_SECRET_ENCRYPTION_KEY", Fernet.generate_key().decode()
)
secrets = LocalEncryptedSecretStore(control_plane, key)
secrets.ensure_schema()
store = DataSourceStore(control_plane)
store.ensure_schema()
with control_plane.cursor() as cursor:
    cursor.execute(
        "DELETE FROM configure.data_source WHERE created_by = %s", (CREATED_BY,)
    )
control_plane.commit()

driver = GraphDatabase.driver(NEO4J, auth=NEO4J_AUTH)
operator = SemanticGraphStore(driver=driver)
operator.ensure_indexes()
operator.drop_schema(SCHEMA)
assert operator.get_table("silver.crm_contact") is not None, (
    "this cookbook needs the real silver harvest in the graph -- run K06_refresh_entrypoint.py"
)
operator_tables = {(r.schema, r.name) for r in operator.list_tables()}

source = store.create(
    DataSourceCreate(
        name="P3 cookbook source",
        source_type=DataSourceType.POSTGRES,
        host="db.example.internal",
        database_name="crm",
        schema_name=SCHEMA,
        credential=Credential(
            auth_method=AuthMethod.USERNAME_PASSWORD, username="ro", password="x"
        ),
        created_by=CREATED_BY,
    ),
    secrets,
)
operator.write_harvest(
    Harvest(
        schema=SCHEMA,
        tables=(
            HarvestedTable(
                schema=SCHEMA,
                name="crm_contact",  # the same bare name silver has, on purpose
                columns=(HarvestedColumn("p3_only_col", "integer", False, True),),
                row_count=3,
            ),
        ),
        source="p3",
    ),
    snapshot_version="p3-demo.1",
    source_id=source.id,
)
store.set_harvest_state(source.id, HarvestState.MODELLED, mark_harvested_now=True)

app = FastAPI()
app.include_router(semantic_module.router)


@app.middleware("http")
async def _auth(request, call_next):
    request.state.authorization_enabled = True
    request.state.scopes = ["semantic:read"]
    request.state.admin_scope = None
    return await call_next(request)


client = TestClient(app)
try:
    default = client.get("/semantic/tables").json()
    default_tables = {(t["schema"], t["name"]) for t in default["tables"]}
    assert (SCHEMA, "crm_contact") not in default_tables
    assert default_tables == operator_tables
    print(
        f"PASS: GET /semantic/tables lists {len(default_tables)} operator tables, none of the source's"
    )

    assert operator.get_table("crm_contact").ref.schema == "silver"
    print(
        "PASS: a bare 'crm_contact' still resolves to silver (was ambiguous before scoping)"
    )

    scoped = client.get("/semantic/tables", params={"source_id": source.id}).json()
    assert [(t["schema"], t["name"]) for t in scoped["tables"]] == [
        (SCHEMA, "crm_contact")
    ]
    assert scoped["snapshot"]["version"] == "p3-demo.1"
    assert scoped["snapshot"]["source"] == "P3 cookbook source"
    print(
        f"PASS: ?source_id= lists only the source's table, snapshot {scoped['snapshot']}"
    )

    missing = client.get("/semantic/tables", params={"source_id": "ds_nope"})
    assert missing.status_code == 404
    print("PASS: an unregistered source_id is 404, not an empty list")

    assert operator.snapshot().version != "p3-demo.1"
    print(
        "PASS: the operator's own snapshot pointer was not overwritten by the source's harvest"
    )

    source_reader = SemanticGraphStore(driver=driver, source_id=source.id)
    guard = build_source_read_guard(source_reader, schema=SCHEMA)
    guard.check(f"SELECT p3_only_col FROM {SCHEMA}.crm_contact")
    try:
        guard.check("SELECT * FROM silver.crm_contact")
        raise AssertionError("the source's guard let silver.crm_contact through")
    except SQLGuardRefusal:
        pass
    print("PASS: the source's read guard allows its own table and refuses silver's")
finally:
    store.delete(source.id, secrets)
    operator.drop_schema(SCHEMA)
    control_plane.close()
    driver.close()

print("=" * 78)
print("P3 evidence complete.")
