"""K6 -- versioned snapshots, drift, and the KPIs a change puts into review (G9, G19).

The package's scenario, from the delivery plan: take a snapshot, rename a column the sample
KPIs depend on, harvest again, and look at those KPIs.

Evidence: the diff names the renamed column; dependent KPIs enter review **before** the
nightly run rather than failing during it at two in the morning; and a newly introduced
column-name collision appears as a review item (G19).

A rename is the right scenario because it is the quiet one. The formula still parses. The
nightly job still fires. The statement either errors at 02:00 or -- worse -- still runs and
means something else. Nothing about it announces itself, which is why the check has to happen
at harvest time and not at execution time.

Needs the warehouse from K4 and the control-plane Postgres:

    PYTHONPATH=product .venvs/demo/bin/python cookbook/94_retail/K06_snapshots_and_drift.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "product"))

import psycopg  # noqa: E402
from egpt_retail.contracts.kpi import (  # noqa: E402
    KpiDefinition,
)
from egpt_retail.semantic.harvest import harvest_schema  # noqa: E402
from egpt_retail.semantic.snapshots import (  # noqa: E402
    ReviewReason,
    SnapshotStore,
    kpis_needing_review,
)

WAREHOUSE = os.environ.get(
    "EGPT_RETAIL_WAREHOUSE_DSN", "postgresql://ai:ai@localhost:5532/egpt_warehouse"
)
CONTROL_PLANE = os.environ.get(
    "EGPT_RETAIL_CP_DSN", "postgresql://ai:ai@localhost:5532/ai"
)
SCHEMA = "silver"

print("K6 -- semantic snapshots and drift")
print("=" * 78)

warehouse = psycopg.connect(WAREHOUSE)
warehouse.autocommit = True
control_plane = psycopg.connect(CONTROL_PLANE)
store = SnapshotStore(control_plane)
store.ensure_schema()

# Three KPIs over the real silver schema. The middle one is the sharp control: `brand` exists
# on BOTH crm_contact and analytics_event, so renaming crm_contact.brand must flag the first
# KPI and not the second -- even though both formulas contain the word `brand`. A check that
# matched on column name alone would flag both, and a reviewer who sees two items where one is
# wrong learns to skim the queue.
KPIS = [
    KpiDefinition(
        identifier="contacts_by_brand",
        name="contacts_by_brand",
        description="Distinct contacts per brand.",
        sql="SELECT brand, count(DISTINCT email) FROM crm_contact GROUP BY brand",
        grain="brand",
        business_area="customer",
        source_tables=frozenset({"crm_contact"}),
        validated_against_snapshot="k6-before",
    ),
    KpiDefinition(
        identifier="events_by_brand",
        name="events_by_brand",
        description="Analytics events per brand -- a different table's column of the same name.",
        sql="SELECT brand, count(*) FROM analytics_event GROUP BY brand",
        grain="brand",
        business_area="marketing",
        source_tables=frozenset({"analytics_event"}),
        validated_against_snapshot="k6-before",
    ),
    KpiDefinition(
        identifier="sessions_total",
        name="sessions_total",
        description="Replay sessions recorded.",
        sql="SELECT count(*) FROM session_replay_session",
        grain="overall",
        business_area="marketing",
        source_tables=frozenset({"session_replay_session"}),
        validated_against_snapshot="k6-before",
    ),
]

# --- 1. Snapshot the schema as it stands -------------------------------------------------

before = harvest_schema(warehouse, SCHEMA, source="egpt_warehouse", sample=False)
before_version = store.save(before, "k6-before")
print()
print("1. A versioned snapshot")
print("-" * 78)
print(f"  {before_version}: {len(before.tables)} tables, {before.column_count} columns")
print(f"  stored versions: {', '.join(store.versions())}")
print(
    f"  KPIs validated against: {', '.join(sorted({k.validated_against_snapshot for k in KPIS}))}"
)

# --- 2. Rename a column two of the KPIs depend on ----------------------------------------

print()
print("2. Rename crm_contact.brand -- the quiet failure")
print("-" * 78)
with warehouse.cursor() as cursor:
    cursor.execute(
        f"ALTER TABLE {SCHEMA}.crm_contact RENAME COLUMN brand TO brand_name"
    )

after = harvest_schema(warehouse, SCHEMA, source="egpt_warehouse", sample=False)
after_version = store.save(after, "k6-after")
drift = store.diff(before_version, after_version)
print("  diff, recomputed from the two stored snapshots:")
for line in drift.describe().splitlines():
    print(f"    {line}")

review = kpis_needing_review(drift, KPIS, new_version=after_version)
print()
print(
    f"  KPIs moved into review: {len({item.kpi_identifier for item in review})} of {len(KPIS)}"
)
for item in review:
    print(f"    {item.kpi_identifier:<24} {item.reason:<22} {item.subject}")
    print(f"      {item.detail}")
    print(f"      last validated against: {item.validated_against}")

flagged = {item.kpi_identifier for item in review}
print()
print(
    f"  contacts_by_brand flagged (reads crm_contact.brand): {'contacts_by_brand' in flagged}"
)
print(
    f"  events_by_brand NOT flagged (reads analytics_event.brand, same name, untouched table): {'events_by_brand' not in flagged}"
)
print(
    f"  sessions_total NOT flagged (touches neither): {'sessions_total' not in flagged}"
)

# --- 3. An added column is not a review item ---------------------------------------------

print()
print("3. An added column is not a review item")
print("-" * 78)
with warehouse.cursor() as cursor:
    cursor.execute(
        f"ALTER TABLE {SCHEMA}.crm_contact ADD COLUMN IF NOT EXISTS k6_note text"
    )
added_harvest = harvest_schema(warehouse, SCHEMA, source="egpt_warehouse", sample=False)
added_version = store.save(added_harvest, "k6-added")
added_diff = store.diff(after_version, added_version)
added_review = kpis_needing_review(added_diff, KPIS)
print(f"  diff: {added_diff.describe()}")
print(f"  KPIs moved into review: {len(added_review)}")
print(
    "  A new column beside the ones a formula reads changes nothing about that formula."
)
print(
    "  Flagging every addition is how a review queue becomes noise and then gets ignored."
)

# --- 4. A new collision is its own review item (G19) --------------------------------------

print()
print("4. A newly ambiguous column name (G19)")
print("-" * 78)
with warehouse.cursor() as cursor:
    # `email` exists only on crm_contact today. Putting it on crm_deal makes the name mean
    # two things, and contacts_by_brand reads `email`.
    cursor.execute(f"ALTER TABLE {SCHEMA}.crm_deal ADD COLUMN IF NOT EXISTS email text")
collided_harvest = harvest_schema(
    warehouse, SCHEMA, source="egpt_warehouse", sample=False
)
collided_version = store.save(collided_harvest, "k6-collided")
collided_diff = store.diff(added_version, collided_version)
collided_review = kpis_needing_review(collided_diff, KPIS)
print(
    f"  new collisions in the diff: {', '.join(collided_diff.new_collisions) or '(none)'}"
)
for item in collided_review:
    print(f"    {item.kpi_identifier:<24} {item.reason:<28} {item.subject}")
collision_flagged = any(
    item.reason == ReviewReason.NEW_COLLISION for item in collided_review
)

# --- 5. Restore the schema ----------------------------------------------------------------

with warehouse.cursor() as cursor:
    cursor.execute(
        f"ALTER TABLE {SCHEMA}.crm_contact RENAME COLUMN brand_name TO brand"
    )
    cursor.execute(f"ALTER TABLE {SCHEMA}.crm_contact DROP COLUMN IF EXISTS k6_note")
    cursor.execute(f"ALTER TABLE {SCHEMA}.crm_deal DROP COLUMN IF EXISTS email")
restored = harvest_schema(warehouse, SCHEMA, source="egpt_warehouse", sample=False)
back_to_start = store.diff(before_version, store.save(restored, "k6-restored")).is_empty
print()
print("5. Schema restored")
print("-" * 78)
print(f"  structurally identical to the first snapshot: {back_to_start}")

with control_plane.cursor() as cursor:
    cursor.execute(
        "DELETE FROM semantic.harvest_snapshot WHERE version LIKE 'k6-%' OR version LIKE 'k6%'"
    )
control_plane.commit()
warehouse.close()
control_plane.close()

# --- 6. Verdict ---------------------------------------------------------------------------

print()
print("=" * 78)
failures = []
if "crm_contact.brand" not in drift.removed_columns:
    failures.append("the diff did not name the renamed column as removed")
if "crm_contact.brand_name" not in drift.added_columns:
    failures.append("the diff did not name the renamed column's new name")
if "contacts_by_brand" not in flagged:
    failures.append("the KPI reading the renamed column was not moved into review")
if "events_by_brand" in flagged:
    failures.append(
        "a KPI reading a same-named column on an untouched table was flagged -- the check "
        "is matching on column name rather than on table and column together"
    )
if "sessions_total" in flagged:
    failures.append("a KPI touching neither table was flagged")
if not all(item.validated_against for item in review):
    failures.append(
        "a review item does not say which snapshot the KPI was validated against"
    )
if added_review:
    failures.append(
        "an added column produced a review item; the queue would fill with noise"
    )
if "email" not in collided_diff.new_collisions:
    failures.append("a newly introduced collision was not reported in the diff")
if not collision_flagged:
    failures.append("a new collision did not produce a review item (G19)")
if not back_to_start:
    failures.append(
        "restoring the schema did not return the snapshot to its original shape"
    )

if failures:
    print("K6 does NOT hold:")
    for failure in failures:
        print("  -", failure)
    raise SystemExit(1)
print(
    "Drift holds. A rename is caught at harvest time, the KPIs that read the column enter"
)
print(
    "review with the snapshot they were validated against, and the ones that do not are"
)
print("left alone.")
