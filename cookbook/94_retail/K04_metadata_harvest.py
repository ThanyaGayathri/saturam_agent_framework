"""K4 -- metadata harvest, against R4's real silver schema (G19).

The package's three scenarios, from the delivery plan:

1. Run against the silver schema and compare to the schema by eye.
2. Add a column to one table, re-run, and diff the two harvests.
3. Add a column whose name already exists in a different table.

Evidence: every table and column present with correct types; the diff names exactly the added
column and nothing else; the duplicated name is reported as a collision rather than landing
silently, and it names both tables.

Deterministic throughout -- no model is called. What this reads is what the database's own
catalogue says, so a harvest can be trusted by everything downstream that cannot check.

Needs a warehouse with R4's DDL applied:

    createdb -U ai egpt_warehouse
    for f in product/egpt_retail/warehouse/sql/ddl/*.sql; do psql -U ai -d egpt_warehouse -f "$f"; done

Run:
    PYTHONPATH=product .venvs/demo/bin/python cookbook/94_retail/K04_metadata_harvest.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "product"))

import psycopg  # noqa: E402
from egpt_retail.semantic.harvest import (  # noqa: E402
    diff_harvests,
    harvest_schema,
)

DSN = os.environ.get(
    "EGPT_RETAIL_WAREHOUSE_DSN", "postgresql://ai:ai@localhost:5532/egpt_warehouse"
)
SCHEMA = os.environ.get("EGPT_RETAIL_SILVER_SCHEMA", "silver")

print("K4 -- metadata harvest")
print("=" * 78)

connection = psycopg.connect(DSN)
connection.autocommit = True

# --- 1. Harvest the silver schema --------------------------------------------------------

first = harvest_schema(connection, SCHEMA, source="egpt_warehouse")
print()
print("1. The silver schema as the catalogue describes it")
print("-" * 78)
print(f"  {len(first.tables)} tables, {first.column_count} columns")
for table in first.tables:
    keys = ", ".join(table.key_columns) or "(none)"
    print(
        f"    {table.qualified:<36} {len(table.columns):>2} cols  rows={table.row_count:<6} key: {keys}"
    )

# Types come from the catalogue, not from a guess. Spot-check one against the DDL by eye.
contact = first.table("crm_contact")
if contact is not None:
    print()
    print("  silver.crm_contact, column by column:")
    for column in contact.columns:
        flag = "PK" if column.is_key else ("  " if column.nullable else "NN")
        print(f"    {flag} {column.name:<20} {column.data_type}")

# Comments carry meaning the schema author wrote down. Harvesting them means an enrichment
# proposer reads what is true rather than re-deriving it from a column name.
commented = [
    (t.name, c.name, c.comment) for t in first.tables for c in t.columns if c.comment
]
print()
print(f"  column comments harvested: {len(commented)}")
for table_name, column_name, comment in commented[:2]:
    print(f"    {table_name}.{column_name}: {comment[:72]}...")

# --- 2. Collisions, and which of them need a decision ------------------------------------

print()
print("2. Column-name collisions (G19)")
print("-" * 78)
print(
    f"  {len(first.collisions)} collisions, {len(first.business_collisions)} needing a decision"
)
print()
print(
    "  Need a decision -- a proposer cannot tell these apart from name and type alone:"
)
for collision in first.business_collisions:
    tables = ", ".join(t.split(".")[1] for t in collision.tables)
    warn = "" if collision.types_agree else "   TYPES DISAGREE"
    print(f"    {collision.column_name:<20} {tables}{warn}")
print()
lineage = [c for c in first.collisions if not c.needs_disambiguation]
print(
    f"  Lineage, reported separately -- every table carries these by design ({len(lineage)}):"
)
print(f"    {', '.join(c.column_name for c in lineage)}")

# --- 3. Add a column; the diff must name it and nothing else -----------------------------

print()
print("3. Add a column, re-harvest, diff")
print("-" * 78)
with connection.cursor() as cursor:
    cursor.execute(
        f"ALTER TABLE {SCHEMA}.crm_deal ADD COLUMN IF NOT EXISTS k4_probe_note text"
    )
second = harvest_schema(connection, SCHEMA, source="egpt_warehouse")
added = diff_harvests(first, second)
print("  diff:")
for line in added.describe().splitlines():
    print(f"    {line}")
names_only_the_new_column = added.added_columns == (
    "crm_deal.k4_probe_note",
) and not any(
    (
        added.added_tables,
        added.removed_tables,
        added.removed_columns,
        added.retyped_columns,
    )
)
print(f"  names exactly the added column and nothing else: {names_only_the_new_column}")

# --- 4. Add a name that already exists elsewhere; it must surface as a NEW collision ------

print()
print("4. Add a column whose name already exists in another table")
print("-" * 78)
with connection.cursor() as cursor:
    # `email` is on crm_contact already. Putting it on session_replay_session makes the schema
    # ambiguous in a way it was not a moment ago -- which is the case G19 exists for.
    cursor.execute(
        f"ALTER TABLE {SCHEMA}.session_replay_session ADD COLUMN IF NOT EXISTS email text"
    )
third = harvest_schema(connection, SCHEMA, source="egpt_warehouse")
collided = diff_harvests(second, third)
print("  diff:")
for line in collided.describe().splitlines():
    print(f"    {line}")

email = third.collision("email")
reports_both_tables = email is not None and len(email.tables) == 2
print()
if email is not None:
    print(f"  'email' now names {len(email.tables)} tables: {', '.join(email.tables)}")
    print(f"  flagged as needing a decision: {email.needs_disambiguation}")
print(
    f"  reported as a NEW collision in the diff: {'email' in collided.new_collisions}"
)

# --- 5. Clean up the probe columns --------------------------------------------------------

with connection.cursor() as cursor:
    cursor.execute(f"ALTER TABLE {SCHEMA}.crm_deal DROP COLUMN IF EXISTS k4_probe_note")
    cursor.execute(
        f"ALTER TABLE {SCHEMA}.session_replay_session DROP COLUMN IF EXISTS email"
    )
restored = harvest_schema(connection, SCHEMA, source="egpt_warehouse")
back_to_start = diff_harvests(first, restored).is_empty
print()
print("5. Probe columns dropped")
print("-" * 78)
print(f"  schema is structurally identical to the first harvest: {back_to_start}")

connection.close()

# --- 6. Verdict ---------------------------------------------------------------------------

print()
print("=" * 78)
failures = []
if len(first.tables) != 5:
    failures.append(
        f"expected R4's five silver entities, harvested {len(first.tables)}"
    )
if contact is None or "email" not in contact.column_names:
    failures.append("crm_contact was not harvested with its columns")
if not commented:
    failures.append(
        "no column comments harvested -- the DDL carries them, so they were dropped"
    )
if not first.business_collisions:
    failures.append(
        "no business collisions found; brand and session_id both collide in this schema"
    )
if any(c.needs_disambiguation for c in lineage):
    failures.append("a lineage column was reported as needing a decision")
if not names_only_the_new_column:
    failures.append("the diff did not name exactly the added column")
if not reports_both_tables:
    failures.append("the new collision did not name both tables")
if "email" not in collided.new_collisions:
    failures.append("a newly introduced collision was not reported as new")
if not back_to_start:
    failures.append("dropping the probe columns did not restore the original structure")

if failures:
    print("K4 does NOT hold:")
    for failure in failures:
        print("  -", failure)
    raise SystemExit(1)
print("Harvest holds. Every table and column read from the catalogue, comments kept,")
print(
    "collisions separated into those needing a decision and those that are convention,"
)
print("and a diff that names exactly what changed.")
