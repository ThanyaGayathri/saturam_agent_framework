"""K6 (P2 verification) -- the harvest entrypoint: warehouse -> snapshot -> graph, one version.

The plan says "every harvest produces a versioned snapshot". Until the P2 verification, that
was true of the cookbooks and not of the product: K4, K5 and K6 each did their part, every
cookbook wired them together by hand, and the graph's snapshot version and the Postgres
snapshot version were chosen separately by whoever called them. `refresh_semantic_layer`
and `egpt-retail harvest` are the missing orchestration.

Evidence: one call does all three steps under one version; the same schema twice gives an
empty diff; a DDL change shows up in the next run's report; and the real CLI entrypoint runs
end to end from environment variables alone.

    PYTHONPATH=product .venvs/demo/bin/python cookbook/94_retail/K06_refresh_entrypoint.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "product"))

WAREHOUSE = os.environ.setdefault(
    "EGPT_RETAIL_WAREHOUSE_DB_URL",
    "postgresql+psycopg://ai:ai@localhost:5532/egpt_warehouse",
)
os.environ.setdefault(
    "EGPT_RETAIL_DB_URL", "postgresql+psycopg://ai:ai@localhost:5532/ai"
)
os.environ.setdefault("EGPT_RETAIL_NEO4J_PASSWORD", "egptretail")

import psycopg  # noqa: E402
from egpt_retail.app.db import psycopg_dsn  # noqa: E402
from egpt_retail.entrypoints.__main__ import main as egpt_retail_main  # noqa: E402
from egpt_retail.semantic.refresh import refresh_semantic_layer  # noqa: E402
from egpt_retail.semantic.snapshots import SnapshotStore  # noqa: E402
from egpt_retail.semantic.store import SemanticGraphStore  # noqa: E402
from neo4j import GraphDatabase  # noqa: E402

print("K6 -- the harvest entrypoint")
print("=" * 78)

driver = GraphDatabase.driver("neo4j://localhost:7701", auth=("neo4j", "egptretail"))
graph = SemanticGraphStore(driver=driver)
control_plane = psycopg.connect(psycopg_dsn(os.environ["EGPT_RETAIL_DB_URL"]))
snapshots = SnapshotStore(control_plane)
snapshots.ensure_schema()
warehouse = psycopg.connect(psycopg_dsn(WAREHOUSE))
warehouse.autocommit = True

# --- 1. One call, three stores, one version ------------------------------------------------

first = refresh_semantic_layer(version="k6r-1")
print()
print("1. One call: harvest, snapshot, graph")
print("-" * 78)
for line in first.lines():
    print(f"  {line}")
graph_version = graph.snapshot().version
newest_snapshot = snapshots.versions()[0]
print(f"  graph says snapshot:    {graph_version}")
print(f"  snapshot table newest:  {newest_snapshot}")

# --- 2. The same schema again --------------------------------------------------------------

second = refresh_semantic_layer(version="k6r-2")
print()
print("2. The same schema, harvested again")
print("-" * 78)
print(f"  previous: {second.previous_version}   diff empty: {not second.changed}")

# --- 3. A change shows up in the next run's report ---------------------------------------------

with warehouse.cursor() as cursor:
    cursor.execute(
        "ALTER TABLE silver.crm_deal ADD COLUMN IF NOT EXISTS k6r_probe text"
    )
third = refresh_semantic_layer(version="k6r-3")
with warehouse.cursor() as cursor:
    cursor.execute("ALTER TABLE silver.crm_deal DROP COLUMN IF EXISTS k6r_probe")
print()
print("3. A column added between runs")
print("-" * 78)
for line in third.lines():
    if line.startswith("drift"):
        print(f"  {line}")
graph_sees_it = "k6r_probe" in (graph.get_table("crm_deal").column_names)

# --- 4. The real CLI, from environment alone -----------------------------------------------

print()
print("4. egpt-retail harvest --version k6r-4")
print("-" * 78)
exit_code = egpt_retail_main(["harvest", "--version", "k6r-4"])
cli_version = graph.snapshot().version
print(f"  exit code: {exit_code}   graph now at: {cli_version}")

# --- 5. Restore ----------------------------------------------------------------------------

restored = refresh_semantic_layer(version="k6r-5")
with control_plane.cursor() as cursor:
    cursor.execute("DELETE FROM semantic.harvest_snapshot WHERE version LIKE 'k6r-%'")
control_plane.commit()
control_plane.close()
warehouse.close()
driver.close()

print()
print("=" * 78)
failures = []
if graph_version != "k6r-1" or newest_snapshot != "k6r-1":
    failures.append(
        f"the graph ({graph_version}) and the snapshot table ({newest_snapshot}) disagree "
        "about which version was just written"
    )
if second.previous_version != "k6r-1" or second.changed:
    failures.append("harvesting an unchanged schema reported drift")
if third.diff is None or "crm_deal.k6r_probe" not in third.diff.added_columns:
    failures.append("a column added between runs was not reported as drift")
if not graph_sees_it:
    failures.append("the added column reached the snapshot but not the graph")
if exit_code != 0 or cli_version != "k6r-4":
    failures.append(
        f"the CLI entrypoint did not run end to end (exit {exit_code}, {cli_version})"
    )
if restored.changed:
    failures.append("restoring the schema left the layer changed")
if failures:
    print("K6 refresh does NOT hold:")
    for failure in failures:
        print("  -", failure)
    raise SystemExit(1)
print(
    "Refresh holds. One call harvests, versions and writes the graph under one version,"
)
print(
    "an unchanged schema reports no drift, a changed one reports it, and the CLI runs."
)
