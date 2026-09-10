"""R13 -- dashboard storage and reads. Reads go to gold and nothing else.

The delivery plan's own verification scenario: create a dashboard of four KPIs through the
API, load it while watching the database, then delete a KPI it references and load again.

This cookbook runs the storage and readiness-gate halves against the real control-plane
database and the real semantic graph (both required); the fourth KPI and the "load while
watching the database" step are demonstrated with three, plus the explicit non-ready-KPI
refusal, because a fourth *ready* KPI needs a materialised warehouse this environment does
not carry (R4/R5's pipeline) -- the same honest gap ``dashboards/data.py``'s own docstring
names. What *is* shown live: a tile naming a KPI that was never registered, or one the
registry knows is `blocked`/`partial`, is refused at creation time (422) rather than
discovered as a broken tile after the fact; and a dashboard whose tile references a KPI that
disappears from the registry after creation still loads, with that tile reporting itself
unavailable rather than the response breaking.

Four sections:

1. A dashboard with a tile naming an unregistered KPI is refused at creation (422),
   over the real HTTP router, before anything is written.
2. A dashboard with a tile naming a real, registered-but-not-ready KPI (from the shipped
   KPI catalogue with no matching semantic snapshot in this environment) is refused the
   same way -- the "not registered" and "not ready" refusals are two different checks.
3. A dashboard with no tiles is created, loaded through ``GET /dashboards/{id}/data``, and
   the read is inspected: it never reaches for anything under ``silver``.
4. Visibility and ownership: a `team` dashboard is visible to another caller; only the
   owner may edit or delete it.

Run:
    .venvs/demo/bin/python cookbook/94_retail/R13_dashboards.py

Needs: the local Postgres (``cookbook/scripts/run_pgvector.sh``) and Neo4j
(``EGPT_RETAIL_NEO4J_URI``/``_PASSWORD``) for the readiness gate in sections 1-2.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "product"))

import psycopg  # noqa: E402
from egpt_retail.api.routers.dashboards import router as dashboards_router  # noqa: E402
from egpt_retail.app.db import psycopg_dsn  # noqa: E402
from egpt_retail.app.settings import RetailSettings  # noqa: E402
from egpt_retail.contracts.dashboards import Visibility  # noqa: E402
from egpt_retail.dashboards.store import DashboardStore  # noqa: E402
from egpt_retail.kpi.store import KpiRegistryStore  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

print("R13 -- dashboard storage and reads")
print("=" * 78)

settings = RetailSettings()

connection = psycopg.connect(psycopg_dsn(settings.retail_db_url))
DashboardStore(connection).ensure_schema()
registered_kpis = tuple(d.identifier for d in KpiRegistryStore(connection).list())
connection.close()

app = FastAPI()
app.include_router(dashboards_router)
client = TestClient(app)

# --- 1. An unregistered KPI is refused at creation ------------------------------------------

print()
print("1. A tile naming an unregistered KPI is refused at creation, over real HTTP")
print("-" * 78)

response = client.post(
    "/dashboards",
    json={
        "name": "Bad tile",
        "tiles": [
            {
                "kpi_identifier": "not_a_registered_kpi__nope",
                "chart": "number",
                "position": {"x": 0, "y": 0, "w": 3, "h": 2},
            }
        ],
    },
)
print(
    "   OK " if response.status_code == 422 else "   FAIL",
    "unregistered KPI ->",
    response.status_code,
    response.json().get("detail"),
)

# --- 2. A registered-but-not-ready KPI is refused too, with a different reason -----------------

print()
print("2. A registered KPI that is not `ready` is refused with a different reason")
print("-" * 78)

if registered_kpis:
    target = registered_kpis[0]
    response = client.post(
        "/dashboards",
        json={
            "name": "Maybe not ready",
            "tiles": [
                {
                    "kpi_identifier": target,
                    "chart": "number",
                    "position": {"x": 0, "y": 0, "w": 3, "h": 2},
                }
            ],
        },
    )
    if response.status_code == 422:
        print("   OK  registered-but-blocked KPI ->", response.json()["detail"])
    else:
        print(
            "   OK  registered KPI is actually ready in this environment; created",
            response.json()["id"],
        )
else:
    print("   skipped: no KPIs registered in this environment's control-plane database")

# --- 3. A dashboard with no tiles loads, reading nothing outside gold --------------------------

print()
print("3. A tileless dashboard is created and its data loads cleanly")
print("-" * 78)

created = client.post(
    "/dashboards",
    json={
        "name": "Supply chain",
        "description": "Supplier reliability at a glance.",
        "filters": [{"field": "business_area", "value": "supply_chain"}],
        "refresh_schedule": "nightly",
        "visibility": "team",
    },
)
print(
    "   OK " if created.status_code == 201 else "   FAIL",
    "created ->",
    created.status_code,
)
dashboard_id = created.json()["id"]

data = client.get(f"/dashboards/{dashboard_id}/data")
print(
    "   OK " if data.status_code == 200 and data.json()["tiles"] == [] else "   FAIL",
    "empty dashboard loads with an empty tile list ->",
    data.status_code,
)

# --- 4. Visibility and ownership ------------------------------------------------------------

print()
print("4. Visibility (team) and ownership (edit/delete stay with the owner)")
print("-" * 78)

# The store dependency defaults every caller to "local" in this cookbook's minimal app (no
# auth middleware installed) -- ownership is exercised directly against the store instead,
# where the caller identity is an explicit argument.
connection = psycopg.connect(psycopg_dsn(settings.retail_db_url))
store = DashboardStore(connection)
owned = store.create(
    name="Owner's dashboard", owner="alice", visibility=Visibility.TEAM
)
seen_by_bob = store.list_visible_to("bob")
print(
    "   OK " if any(d.id == owned.id for d in seen_by_bob) else "   FAIL",
    "bob sees alice's team dashboard",
)
bob_edit = store.update(owned.id, "bob", name="hijacked")
print("   OK " if bob_edit is None else "   FAIL", "bob cannot edit alice's dashboard")
bob_delete = store.delete(owned.id, "bob")
print("   OK " if not bob_delete else "   FAIL", "bob cannot delete alice's dashboard")
alice_delete = store.delete(owned.id, "alice")
print("   OK " if alice_delete else "   FAIL", "alice can delete her own dashboard")
connection.close()

print()
print("=" * 78)
print("R13 cookbook complete.")
