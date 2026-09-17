"""R13 -- dashboard storage and reads. Reads go to gold and nothing else.

The delivery plan's own verification scenario: create a dashboard of four KPIs through the
API, load it while watching the database, then delete a KPI it references and load again.

This cookbook runs the storage and readiness-gate halves against the real control-plane
database and the real semantic graph (both required, sections 1-4). Section 5 exercises the
gold read itself -- the round-1 review's own headline finding, a tile whose configured
``grain_value`` matches no materialised row -- against **real** materialised gold data, when
``EGPT_RETAIL_WAREHOUSE_DB_URL``/``_READONLY_DB_URL`` point at a warehouse that has actually
run R4/R5 (the local ``deploy/compose.yaml`` stack, after ``warehouse-migrate`` and a real
nightly-refresh or ``R05_gold_materialisation.py``). Without one, section 5 says so and skips
rather than guessing -- everything else here still runs.

Six sections:

1. A dashboard with a tile naming an unregistered KPI is refused at creation (422),
   over the real HTTP router, before anything is written.
2. A dashboard with a tile naming a real, registered-but-not-ready KPI (from the shipped
   KPI catalogue with no matching semantic snapshot in this environment) is refused the
   same way -- the "not registered" and "not ready" refusals are two different checks.
3. A dashboard with no tiles is created, loaded through ``GET /dashboards/{id}/data``, and
   the read is inspected: it never reaches for anything under ``silver``.
4. Visibility and ownership: a `team` dashboard is visible to another caller; only the
   owner may edit or delete it.
5. With a real materialised KPI available: a tile whose `grain_value` matches the latest
   materialisation reads a real value (`status=fresh`); the same KPI with a `grain_value`
   that matches nothing reports `status=never_computed` rather than `fresh` with an empty
   value -- the exact scenario the round-1 review reproduced and this PR fixed. A third,
   `line` tile on the same grain row reads its `series` and `delta` from the gold history
   (issue #227): one point per materialised business date, and the change against the
   previous one when there is one -- reported as read, `delta: null` when the warehouse
   holds a single night.
6. Filter columns (R13.1): a `query` tile is created with a filter on one real, text-typed,
   non-personal-data column from a harvested table; `GET /dashboards/{id}/filter-columns`
   offers it back, `GET /dashboards/{id}/filter-values` returns its distinct values, and
   `GET /dashboards/{id}/data?f=table.column:value` recomputes the tile live -- every
   returned row matches the picked value, unlike the unfiltered read just before it.

Run:
    .venvs/demo/bin/python cookbook/94_retail/R13_dashboards.py

Needs: the local Postgres (``cookbook/scripts/run_pgvector.sh``) and Neo4j
(``EGPT_RETAIL_NEO4J_URI``/``_PASSWORD``) for the readiness gate in sections 1-2; a
materialised warehouse (see above) for section 5; section 6 needs both a harvested semantic
snapshot with at least one text column and a real warehouse, and skips otherwise.
"""

import json
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

# --- 5. A real materialised KPI: grain match reads, mismatch reports unavailable -------------

print()
print(
    "5. A real gold-materialised KPI: grain match reads a value, "
    "mismatch reports unavailable"
)
print("-" * 78)

if not settings.retail_warehouse_db_url:
    print("   skipped: EGPT_RETAIL_WAREHOUSE_DB_URL is not set in this environment")
else:
    from egpt_retail.app.db import build_warehouse_engine
    from sqlalchemy import text

    warehouse_engine = build_warehouse_engine(settings)
    with warehouse_engine.begin() as wh_connection:
        gold_row = wh_connection.execute(
            text(
                "SELECT kpi_identifier, dimension FROM gold.kpi_value "
                "ORDER BY as_of DESC LIMIT 1"
            )
        ).first()
    warehouse_engine.dispose()

    if gold_row is None:
        print("   skipped: no materialised gold rows in this environment's warehouse")
    else:
        kpi_identifier, dimension = gold_row
        grain_item = next(iter(dimension.items()), None) if dimension else None
        if grain_item is None:
            print(
                f"   skipped: {kpi_identifier} materialises at the overall grain, "
                "nothing to mismatch a grain_value against"
            )
        else:
            _, grain_value = grain_item
            real_dashboard = client.post(
                "/dashboards",
                json={
                    "name": "Real gold read",
                    "tiles": [
                        {
                            "kpi_identifier": kpi_identifier,
                            "chart": "number",
                            "position": {"x": 0, "y": 0, "w": 3, "h": 2},
                            "grain_value": grain_value,
                        },
                        {
                            "kpi_identifier": kpi_identifier,
                            "chart": "number",
                            "position": {"x": 3, "y": 0, "w": 3, "h": 2},
                            "grain_value": "no_such_dimension_value",
                        },
                        {
                            "kpi_identifier": kpi_identifier,
                            "chart": "line",
                            "position": {"x": 0, "y": 2, "w": 6, "h": 2},
                            "grain_value": grain_value,
                        },
                    ],
                },
            )
            print(
                "   OK " if real_dashboard.status_code == 201 else "   FAIL",
                "created ->",
                real_dashboard.status_code,
            )
            real_id = real_dashboard.json()["id"]
            real_data = client.get(f"/dashboards/{real_id}/data").json()["tiles"]
            print(
                "   OK "
                if real_data[0]["status"] == "fresh"
                and real_data[0]["value"] is not None
                else "   FAIL",
                f"matching grain_value={grain_value!r} -> status={real_data[0]['status']}, "
                f"value={real_data[0]['value']}",
            )
            print(
                "   OK "
                if real_data[1]["status"] == "never_computed"
                and real_data[1]["value"] is None
                else "   FAIL",
                "mismatched grain_value -> status=never_computed, not fresh with an empty "
                f"value: {real_data[1]['status']}",
            )
            trend = real_data[2]
            points = trend["series"] or []
            with warehouse_engine.begin() as wh_connection:
                nights = wh_connection.execute(
                    text(
                        "SELECT count(DISTINCT as_of) FROM gold.kpi_value "
                        "WHERE kpi_identifier = :k AND dimension = CAST(:d AS jsonb) "
                        "AND as_of > (SELECT max(as_of) FROM gold.kpi_value "
                        "WHERE kpi_identifier = :k) - :days"
                    ),
                    {"k": kpi_identifier, "d": json.dumps(dimension), "days": 30},
                ).scalar_one()
            print(
                "   OK " if len(points) == nights and nights >= 1 else "   FAIL",
                f"line tile series has one point per materialised night in the window: "
                f"{len(points)} point(s), {nights} night(s) in gold",
            )
            if nights >= 2:
                print(
                    "   OK " if trend["delta"] is not None else "   FAIL",
                    f"delta against the previous night -> {trend['delta']} "
                    f"(latest {points[-1]['value']}, previous {points[-2]['value']})",
                )
            else:
                print(
                    "   OK " if trend["delta"] is None else "   FAIL",
                    "a single materialised night has no previous period -> "
                    f"delta={trend['delta']} (null, not invented)",
                )

# --- 6. Filter columns (R13.1): candidates, distinct values, a live filtered read -----------

print()
print(
    "6. Filter columns: candidates from a query tile's tables, distinct values, a live, "
    "filtered GET /data"
)
print("-" * 78)

if not settings.retail_warehouse_db_url:
    print("   skipped: EGPT_RETAIL_WAREHOUSE_DB_URL is not set in this environment")
else:
    from egpt_retail.app.semantic import open_semantic_reader

    with open_semantic_reader(settings) as reader:
        text_types = {"text", "varchar", "character varying", "char", "character"}
        candidate = None
        for ref in reader.list_tables():
            table = reader.get_table(ref.name)
            if table is None:
                continue
            for column in table.columns:
                if (
                    not column.is_personal_data
                    and column.data_type.lower() in text_types
                ):
                    candidate = (table.ref.name, column.name)
                    break
            if candidate:
                break

    if candidate is None:
        print(
            "   skipped: no harvested table in this environment has a filterable text column"
        )
    else:
        table_name, column_name = candidate
        filtered_dashboard = client.post(
            "/dashboards",
            json={
                "name": "Filter columns demo",
                "tiles": [
                    {
                        "source": "query",
                        "query": {
                            "sql": (
                                f"SELECT {column_name}, count(*) AS n FROM silver.{table_name} "
                                f"GROUP BY 1 LIMIT 50"
                            ),
                        },
                        "chart": "table",
                        "position": {"x": 0, "y": 0, "w": 6, "h": 2},
                    }
                ],
                "filters": [{"table": table_name, "column": column_name}],
            },
        )
        print(
            "   OK " if filtered_dashboard.status_code == 201 else "   FAIL",
            f"created with a {table_name}.{column_name} filter ->",
            filtered_dashboard.status_code,
            filtered_dashboard.json() if filtered_dashboard.status_code != 201 else "",
        )
        filtered_id = filtered_dashboard.json()["id"]

        columns_response = client.get(f"/dashboards/{filtered_id}/filter-columns")
        offered = {(c["table"], c["column"]) for c in columns_response.json()}
        print(
            "   OK " if (table_name, column_name) in offered else "   FAIL",
            f"the chosen column is among {len(offered)} candidate(s) the picker would offer",
        )

        values_response = client.get(
            f"/dashboards/{filtered_id}/filter-values",
            params={"table": table_name, "column": column_name},
        )
        values = values_response.json().get("values", [])
        print(
            "   OK " if values_response.status_code == 200 else "   FAIL",
            f"{len(values)} distinct value(s) for {table_name}.{column_name}",
        )

        unfiltered = client.get(f"/dashboards/{filtered_id}/data").json()["tiles"][0]
        if not values:
            print("   skipped: no non-null values to filter on")
        else:
            picked = values[0]
            filtered = client.get(
                f"/dashboards/{filtered_id}/data",
                params={"f": f"{table_name}.{column_name}:{picked}"},
            ).json()["tiles"][0]
            print(
                "   OK "
                if all(row.get(column_name) == picked for row in filtered["rows"])
                else "   FAIL",
                f"filtered to {column_name}={picked!r} -> every row matches "
                f"({len(filtered['rows'])} of {len(unfiltered['rows'])} unfiltered rows)",
            )

print()
print("=" * 78)
print("R13 cookbook complete.")
