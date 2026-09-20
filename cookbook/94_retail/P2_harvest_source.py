"""#306 -- ``harvest_source``, against real Postgres (both the control plane and a real
harvest target) and real Neo4j.

Two things this cookbook cannot demonstrate through the actual ``harvest_source(step_input)``
entry point in this environment, and why: a genuinely *successful* end-to-end run needs a
target host that both resolves and is not loopback/link-local -- the SSRF guard
(``_resolve_allowed_addresses``) correctly refuses ``localhost``, which is the only reachable
Postgres this local Docker Desktop setup offers from the host machine. So this cookbook
proves every real piece the entry point composes individually, against real infrastructure,
and separately proves the *refusal* path for real through the actual entry point.
``test_p2_harvest_source.py``'s ``test_successful_harvest_writes_the_graph_and_marks_modelled``
is what proves the full orchestration succeeds, with the connection step faked.

Needs the control-plane Postgres, the ``egpt_warehouse`` database (created by
``warehouse-migrate``, already run for this environment), and Neo4j -- all already up
locally:

    PYTHONPATH=product .venvs/demo/bin/python cookbook/94_retail/P2_harvest_source.py
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "product"))

import psycopg  # noqa: E402
from cryptography.fernet import Fernet  # noqa: E402
from egpt.workflow.types import StepInput  # noqa: E402
from egpt_retail.configure.secrets import LocalEncryptedSecretStore  # noqa: E402
from egpt_retail.configure.store import DataSourceStore  # noqa: E402
from egpt_retail.contracts.data_source import (  # noqa: E402
    AuthMethod,
    Credential,
    DataSourceCreate,
    DataSourceType,
    HarvestState,
)
from egpt_retail.executors.data_source_harvest import harvest_source  # noqa: E402
from egpt_retail.semantic.harvest import harvest_schema  # noqa: E402
from egpt_retail.semantic.harvest import Harvest, HarvestedTable  # noqa: E402
from egpt_retail.semantic.store import (  # noqa: E402
    SchemaOwnedByOtherSource,
    SemanticGraphStore,
)
from neo4j import GraphDatabase  # noqa: E402

CONTROL_PLANE = os.environ.get(
    "EGPT_RETAIL_CP_DSN", "postgresql://ai:ai@localhost:5532/ai"
)
WAREHOUSE = os.environ.get(
    "EGPT_RETAIL_WAREHOUSE_DSN", "postgresql://ai:ai@localhost:5532/egpt_warehouse"
)
NEO4J = os.environ.get("EGPT_ONTOLOGY_NEO4J_URI", "bolt://localhost:7687")
NEO4J_AUTH = ("neo4j", os.environ.get("EGPT_RETAIL_NEO4J_PASSWORD", "password"))
CREATED_BY = "p2_cookbook"
SCHEMA = "p2_cookbook"

print("P2 -- harvest_source, against real Postgres and real Neo4j")
print("=" * 78)

# --- setup: a real, throwaway schema in the real warehouse database to harvest ------------

warehouse = psycopg.connect(WAREHOUSE)
warehouse.autocommit = True
with warehouse.cursor() as cursor:
    cursor.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
    cursor.execute(f"CREATE SCHEMA {SCHEMA}")
    cursor.execute(
        f"CREATE TABLE {SCHEMA}.widget (id integer PRIMARY KEY, name text NOT NULL)"
    )
    cursor.execute(f"INSERT INTO {SCHEMA}.widget VALUES (1, 'sprocket'), (2, 'gizmo')")

control_plane = psycopg.connect(CONTROL_PLANE)
# harvest_source() builds its own SecretStore from RetailSettings() -- the encryption key
# must be the one it will read from the environment, or it opens a *different* local store
# and cannot decrypt what this cookbook writes here.
encryption_key = os.environ.setdefault(
    "EGPT_RETAIL_SECRET_ENCRYPTION_KEY", Fernet.generate_key().decode("utf-8")
)
secrets = LocalEncryptedSecretStore(control_plane, encryption_key)
secrets.ensure_schema()
store = DataSourceStore(control_plane)
store.ensure_schema()
with control_plane.cursor() as cursor:
    cursor.execute(
        "DELETE FROM configure.data_source WHERE created_by = %s", (CREATED_BY,)
    )
control_plane.commit()

driver = GraphDatabase.driver(NEO4J, auth=NEO4J_AUTH)
graph = SemanticGraphStore(driver=driver)
graph.ensure_indexes()
# A clean slate for this cookbook's own schema only -- never 'silver', which real KPIs and
# other cookbooks depend on.
graph.drop_schema(SCHEMA)

source = store.create(
    DataSourceCreate(
        name="P2 cookbook warehouse",
        source_type=DataSourceType.POSTGRES,
        host="localhost",
        port=5532,
        database_name="egpt_warehouse",
        schema_name=SCHEMA,
        credential=Credential(
            auth_method=AuthMethod.USERNAME_PASSWORD, username="ai", password="ai"
        ),
        created_by=CREATED_BY,
    ),
    secrets,
)
print(f"registered: {source.id}")

# --- the real pieces, proved individually against real infrastructure ---------------------

harvest = harvest_schema(warehouse, SCHEMA, source=source.name, sample=False)
assert harvest.tables and harvest.tables[0].name == "widget"
print(
    f"PASS: harvest_schema() read {len(harvest.tables)} real table(s) from {SCHEMA!r}"
)

conflict = graph.schema_owned_by_other_source(SCHEMA, source.id)
assert conflict is None
print("PASS: schema_owned_by_other_source() finds no conflict for an unclaimed schema")

tables_written = graph.write_harvest(
    harvest, snapshot_version="p2-demo.1", source_id=source.id
)
assert tables_written == 1
with driver.session() as session:
    record = session.run(
        "MATCH (t:SemanticTable {schema: $schema, name: 'widget'}) RETURN t.source_id AS sid",
        schema=SCHEMA,
    ).single()
assert record is not None and record["sid"] == source.id
print(
    f"PASS: write_harvest() tagged the real node with source_id={source.id!r}, verified by a direct read"
)

# The collision guard, against a schema real KPIs actually depend on: 'silver' already holds
# tables from the operator's own unscoped harvest (source_id=""), so a *different* source_id
# claiming it must be refused -- by write_harvest() itself, inside its own transaction, not by
# a check the caller has to remember to make first.
silver_conflict = graph.schema_owned_by_other_source("silver", source.id)
if silver_conflict is not None:
    print(
        f"PASS: schema_owned_by_other_source('silver', ...) reports the owner {silver_conflict!r}"
    )
    probe_schema = "silver"
    probe = Harvest(
        schema=probe_schema,
        tables=(
            HarvestedTable(
                schema=probe_schema,
                name="p2_cookbook_probe",
                columns=harvest.tables[0].columns,
                row_count=0,
            ),
        ),
        source=source.name,
    )
    try:
        try:
            graph.write_harvest(
                probe, snapshot_version="p2-demo.2", source_id=source.id
            )
        except SchemaOwnedByOtherSource as refusal:
            assert refusal.owner == silver_conflict
            print(
                f"PASS: write_harvest() itself refused the claim on 'silver': {refusal}"
            )
        else:
            raise AssertionError(
                "write_harvest() wrote into a schema another source holds"
            )
        with driver.session() as session:
            leaked = session.run(
                "MATCH (t:SemanticTable {schema: 'silver', name: 'p2_cookbook_probe'}) "
                "RETURN count(t) AS n"
            ).single()["n"]
        assert leaked == 0
        print("PASS: the refused write rolled back -- no probe table left in 'silver'")
    finally:
        with driver.session() as session:
            session.run(
                "MATCH (t:SemanticTable {schema: 'silver', name: 'p2_cookbook_probe'}) "
                "OPTIONAL MATCH (c:SemanticColumn)-[:OF_TABLE]->(t) DETACH DELETE c, t"
            )
else:
    print(
        "SKIPPED: 'silver' holds no tables in this graph yet (no prior K4/K7 cookbook run) -- "
        "the collision guard has nothing to conflict with here, proved instead by "
        "TestSourceScopedHarvest in test_semantic_live.py"
    )

store.set_harvest_state(source.id, HarvestState.MODELLED, mark_harvested_now=True)
after = store.get(source.id)
assert after is not None and after.harvest_state is HarvestState.MODELLED
print("PASS: DataSourceStore.set_harvest_state() persisted MODELLED")

# --- the real entry point, refusing a real loopback host end to end -----------------------

loopback_source = store.create(
    DataSourceCreate(
        name="P2 cookbook loopback probe",
        source_type=DataSourceType.POSTGRES,
        host="localhost",
        port=5532,
        database_name="egpt_warehouse",
        schema_name=f"{SCHEMA}_unreached",
        credential=Credential(
            auth_method=AuthMethod.USERNAME_PASSWORD, username="ai", password="ai"
        ),
        created_by=CREATED_BY,
    ),
    secrets,
)
result = asyncio.run(
    harvest_source(StepInput(additional_data={"source_id": loopback_source.id}))
)
assert result.success is False
assert "loopback" in result.error or "link-local" in result.error
after_loopback = store.get(loopback_source.id)
assert after_loopback is not None and after_loopback.harvest_state is HarvestState.ERROR
print(
    f"PASS: harvest_source() refused a real loopback host end to end: {result.error!r}"
)

# --- cleanup -------------------------------------------------------------------------------

store.delete(source.id, secrets)
store.delete(loopback_source.id, secrets)
graph.drop_schema(SCHEMA)
with warehouse.cursor() as cursor:
    cursor.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
warehouse.close()
control_plane.close()
driver.close()

print("=" * 78)
print("P2 evidence complete.")
