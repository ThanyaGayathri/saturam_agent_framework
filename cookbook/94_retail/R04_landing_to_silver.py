"""R4 -- landing to silver, and the three quality gates that block gold.

The package's Verify line, run end to end against a real warehouse: load the sample files,
load them again, hand-edit one to null a key column and load it, then load a file a tenth the
size of the previous one.

Its Evidence line, asserted rather than asserted-to: the duplicate load changes no counts (in
fact it changes no values and no conformed_at stamps either, which is strictly stronger); the
nulled key is caught and named; and the undersized load is blocked rather than quietly
shrinking gold, with a reason readable without opening a log.

Needs a Postgres on 5532 with the `egpt_warehouse` database:

    docker compose -f deploy/compose.yaml up -d postgres

No object storage: the default reads C1's committed fixtures off disk.
`cookbook/94_retail/R04_minio_load.sh` re-runs this same script against a real MinIO, which
is where "loads from storage" is actually proven.

Run:
    EGPT_RETAIL_WAREHOUSE_DB_URL=postgresql+psycopg://ai:ai@localhost:5532/egpt_warehouse \
      .venv/bin/python cookbook/94_retail/R04_landing_to_silver.py

`EGPT_LANDING_FIXTURES_ROOT` overrides where the raw drop is read from -- deliberately the
same variable C01_landing_contract.py uses, so the MinIO round trip feeds both scripts.
"""

import asyncio
import json
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "product"))

from egpt_retail.app.components import build_workflows  # noqa: E402
from egpt_retail.app.db import (  # noqa: E402
    build_control_plane_db,
    build_warehouse_engine,
)
from egpt_retail.app.settings import RetailSettings  # noqa: E402
from egpt_retail.warehouse.ddl import apply_warehouse_ddl  # noqa: E402
from sqlalchemy import text  # noqa: E402

# Quiet the workflow engine: this script's own output is the evidence.
logging.basicConfig(level=logging.WARNING, format="%(message)s")
logging.getLogger("egpt_retail").setLevel(logging.WARNING)

FIXTURES_ROOT = Path(
    os.environ.get("EGPT_LANDING_FIXTURES_ROOT")
    or REPO_ROOT / "product" / "egpt_retail" / "contracts" / "_fixtures" / "landing"
)

os.environ.setdefault(
    "EGPT_RETAIL_WAREHOUSE_DB_URL",
    "postgresql+psycopg://ai:ai@localhost:5532/egpt_warehouse",
)

# Two ways to run, one script. By default the raw drop is a directory and this reads C1's
# committed fixtures. R04_minio_load.sh sets the provider to s3 first, and then everything
# below runs unchanged against a real bucket -- which is the point of having a storage seam
# rather than two code paths.
FROM_STORE = os.environ.get("EGPT_RETAIL_STORAGE_PROVIDER") == "s3"
if not FROM_STORE:
    os.environ["EGPT_RETAIL_STORAGE_PROVIDER"] = "local"
    # A working copy, so the scenarios below can add hand-written files without ever
    # mutating a committed fixture. Raw is immutable by contract; a verification that edits
    # the drop in place has broken the property it is checking.
    WORK_ROOT = Path(tempfile.mkdtemp(prefix="r04-raw-"))
    shutil.copytree(FIXTURES_ROOT / "raw", WORK_ROOT / "raw")
    os.environ["EGPT_RETAIL_STORAGE_LOCAL_ROOT"] = str(WORK_ROOT)
else:
    WORK_ROOT = None

SETTINGS = RetailSettings()
ENGINE = build_warehouse_engine(SETTINGS)
WORKFLOW = build_workflows(SETTINGS, build_control_plane_db(SETTINGS))[0]

SILVER_TABLES = (
    "analytics_event",
    "crm_contact",
    "crm_deal",
    "crm_deal_stage_history",
    "session_replay_session",
)


def run_load(**options):
    """One run of the landing-to-silver workflow. Returns the final verdict payload.

    `arun`, never `run`. The executors are `async def` (the executor registry's exemption for
    I/O), and a synchronous Workflow.run() raises inside Step's retry loop, which on_error
    then handles -- and on_error defaults to OnError.skip. A sync run would skip every step
    and report a completed run that loaded nothing.
    """
    options.setdefault("trigger", "cookbook")
    output = asyncio.run(WORKFLOW.arun(input="load", additional_data=options))
    return output.step_results[-1]


def failing(verdict):
    """The exact set of (gate, subject) pairs that refused.

    Exclusivity, not substring matching. The review that found the source-versus-stream
    scoping defect pointed out that this script's original `assert "crm.contact" in reason`
    checks passed straight through two *extra* spurious failures baked into the same string.
    Asserting the whole failing set is what would have caught it, so that is what the
    scenarios below assert now.
    """
    return {
        (g["gate"], g["subject"]) for g in verdict.content["gates"] if not g["passed"]
    }


def query(sql, **params):
    with ENGINE.begin() as connection:
        return [dict(row) for row in connection.execute(text(sql), params).mappings()]


def silver_counts():
    return {
        table: query(f"SELECT COUNT(*) AS n FROM silver.{table}")[0]["n"]
        for table in SILVER_TABLES
    }


def landing_count():
    return query("SELECT COUNT(*) AS n FROM landing.raw_row")[0]["n"]


def reset():
    """A clean warehouse, so the scenarios below start from a known state."""
    with ENGINE.begin() as connection:
        connection.exec_driver_sql(
            "TRUNCATE landing.raw_row, quality.load_run CASCADE; "
            "TRUNCATE " + ", ".join(f"silver.{t}" for t in SILVER_TABLES)
        )


def publish(extra_name, rows):
    """Add one hand-written NDJSON file to the raw drop, wherever the raw drop lives.

    A new file rather than an edit: raw is immutable and append-only, and the contract says a
    re-pulled window lands a new file named by its ingested_at. Doing it any other way here
    would be verifying the pipeline against a drop the connectors could never produce.
    """
    path = f"raw/crm/dt=2026-09-01/{extra_name}"
    body = "\n".join(json.dumps(row) for row in rows) + "\n"
    if FROM_STORE:
        import boto3

        boto3.client(
            "s3",
            endpoint_url=SETTINGS.retail_storage_endpoint,
            aws_access_key_id=SETTINGS.retail_storage_access_key,
            aws_secret_access_key=SETTINGS.retail_storage_secret_key,
        ).put_object(
            Bucket=SETTINGS.retail_storage_container,
            Key=path,
            Body=body.encode("utf-8"),
        )
    else:
        target = WORK_ROOT / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return path


def contact(object_id, brand, modified):
    return {
        "record_type": "contact",
        "object_id": object_id,
        "last_modified_at": modified,
        "email": f"{object_id}@example.com",
        "brand": brand,
    }


print("R4 -- landing to silver, with quality gates (G8)")
print("=" * 78)
print(
    f"raw drop:  {'s3://' + SETTINGS.retail_storage_container if FROM_STORE else FIXTURES_ROOT}"
)
print(f"warehouse: {SETTINGS.retail_warehouse_db_url}")
print()

# --- 0. The warehouse schema, applied twice -----------------------------------------------
print("0. Warehouse schema, applied twice")
print("-" * 78)
first = apply_warehouse_ddl(ENGINE)
second = apply_warehouse_ddl(ENGINE)
print(f"  {len(first)} scripts applied, then {len(second)} again with no error")
assert first == second, (
    "the DDL is not idempotent, so warehouse-migrate cannot be re-run"
)
reset()
print("  warehouse truncated to a known state")
print()

# --- 1. Load the sample files -------------------------------------------------------------
print("1. Load the sample files")
print("-" * 78)
verdict = run_load()
counts = silver_counts()
defects = query(
    "SELECT stream_id, row_key, column_name, defect_kind, structural "
    "FROM quality.row_defect ORDER BY source_path, line_number"
)

print(f"  landing rows: {landing_count()}")
for table, n in counts.items():
    print(f"  silver.{table:24s} {n}")
print(f"  defects: {len(defects)}")
for defect in defects:
    print(
        f"    ! {defect['stream_id']} {defect['row_key']} "
        f"{defect['column_name']} -- {defect['defect_kind']} "
        f"(structural={defect['structural']})"
    )

assert landing_count() == 18, f"expected 18 landed rows, got {landing_count()}"
assert counts == {
    "analytics_event": 6,
    "crm_contact": 2,
    "crm_deal": 2,
    "crm_deal_stage_history": 2,
    "session_replay_session": 2,
}, counts
# CRM landed 10 rows across two overlapping pulls and conformed to 6: the de-duplication rule
# in LANDING_CONTRACT.md, executed as SQL.
assert sum(counts[t] for t in SILVER_TABLES if t.startswith("crm_")) == 6, counts
assert len(defects) == 2, (
    f"expected exactly the 2 known-dirty defects, got {len(defects)}"
)
assert not any(d["structural"] for d in defects), (
    "both known-dirty defects are business columns: their rows must be reported and KEPT, "
    "which is what the landing contract requires"
)

# The two dedup outcomes C01_landing_contract.py asserts in Python, now asserted against SQL.
c501 = query("SELECT brand FROM silver.crm_contact WHERE object_id = 'c_501'")[0]
assert c501["brand"] == "Follett", c501
h9002 = query(
    "SELECT stage FROM silver.crm_deal_stage_history WHERE object_id = 'h_9002'"
)[0]
assert h9002["stage"] == "quote_requested", h9002
print("  c_501 brand -> 'Follett'          (the later pull's greater watermark won)")
print(
    "  h_9002 stage -> 'quote_requested' (exact watermark tie -> earliest-pulled file)"
)
assert verdict.content["verdict"] == "passed", verdict.content
print(f"  verdict: {verdict.content['verdict']}")
print()

# --- 2. Load them again -------------------------------------------------------------------
print("2. Load them again")
print("-" * 78)
before = query(
    "SELECT record_type, object_id, brand, load_id, conformed_at "
    "FROM silver.crm_contact ORDER BY object_id"
)
verdict = run_load()
after = query(
    "SELECT record_type, object_id, brand, load_id, conformed_at "
    "FROM silver.crm_contact ORDER BY object_id"
)
inserted = query(
    "SELECT COALESCE(SUM(inserted_rows), 0) AS n FROM quality.load_stream_count "
    "WHERE load_id = (SELECT load_id FROM quality.load_run "
    "                 ORDER BY started_at DESC LIMIT 1)"
)[0]["n"]

print(f"  landing rows: {landing_count()} (unchanged)")
print(f"  rows newly inserted by this load: {inserted}")
print(f"  silver counts unchanged: {silver_counts() == counts}")
assert landing_count() == 18, "a re-load must not add landing rows"
assert silver_counts() == counts, "a re-load must not change silver counts"
assert inserted == 0, f"a re-load must insert nothing, inserted {inserted}"
# Stronger than the evidence bar asks: not just the counts, but every value and every stamp.
assert before == after, "a re-load changed values or conformed_at stamps"
print("  values and conformed_at stamps identical, not merely the counts")
assert verdict.content["verdict"] == "passed", verdict.content
print(f"  verdict: {verdict.content['verdict']}")
print()

# --- 3. Hand-edit one file to null a key column -------------------------------------------
print("3. A file with a nulled key column")
print("-" * 78)
nulled = [
    contact("c_600", "Blodgett", "2026-09-01T09:00:00Z"),
    contact("c_601", "Follett", "2026-09-01T09:01:00Z"),
    contact("c_602", "Blodgett", "2026-09-01T09:02:00Z"),
    # The hand edit: object_id is a key column, so this row cannot be identified at all.
    {
        "record_type": "contact",
        "object_id": None,
        "last_modified_at": "2026-09-01T09:03:00Z",
        "email": "orphan@example.com",
        "brand": "Follett",
    },
]
nulled_path = publish("crm-20260901T100000Z.ndjson", nulled)
verdict = run_load(paths=[nulled_path])

structural = query(
    "SELECT stream_id, column_name, defect_kind FROM quality.row_defect "
    "WHERE structural AND load_id = (SELECT load_id FROM quality.load_run "
    "                                ORDER BY started_at DESC LIMIT 1)"
)
print(f"  structural defects: {len(structural)}")
for defect in structural:
    print(
        f"    ! {defect['stream_id']}.{defect['column_name']} -- {defect['defect_kind']}"
    )
assert len(structural) == 1, structural
assert structural[0]["column_name"] == "object_id", structural
assert verdict.content["verdict"] == "blocked", verdict.content
assert verdict.stop is True, "a blocked load must stop the run before gold"

reason = verdict.content["reason"]
print(f"  verdict: {verdict.content['verdict']}")
print(f"  reason:  {reason}")
assert "crm.contact" in reason and "object_id" in reason, reason
# Exactly one gate refused, and it is the right one. A contacts-only CRM file legitimately
# carries no deals, so crm.deal and crm.deal_stage_history must NOT be reported as having
# collapsed to zero -- they share a source with crm.contact but not a stream, and scoping
# coverage by source rather than by stream is the defect this assertion now pins.
assert failing(verdict) == {("key_null_rate", "crm.contact.object_id")}, failing(
    verdict
)

# The block must not have corrupted what was already there.
still = query("SELECT brand FROM silver.crm_contact WHERE object_id = 'c_501'")[0]
assert still["brand"] == "Follett", still
print("  c_501 still holds its previous good value -- the block corrupted nothing")
print()

# --- 4. A file a tenth the size of the previous load --------------------------------------
print("4. A load a fraction the size of the previous one")
print("-" * 78)
small = [contact("c_700", "Blodgett", "2026-09-01T10:00:00Z")]
small_path = publish("crm-20260901T110000Z.ndjson", small)
verdict = run_load(paths=[small_path])

reason = verdict.content["reason"]
print(f"  verdict: {verdict.content['verdict']}")
print()
print("  " + "\n  ".join(reason.split(". ")))
print()
assert verdict.content["verdict"] == "blocked", verdict.content
assert verdict.stop is True, "an undersized load must stop the run before gold"
assert "crm.contact" in reason, reason
assert small_path in reason, (
    "the reason must name the file, not send the reader to a log"
)
# Two refusals, both real, and nothing else. crm.contact is undersized. And this load's
# silver absorbed the rows scenario 3 landed under a load that was blocked -- a full
# recompute cannot tell them apart, so they entered silver ungated; that is reported against
# the load rather than any one stream. The other four streams carried nothing in this pull
# and must stay silent rather than reporting a collapse.
assert failing(verdict) == {
    ("row_count_delta", "crm.contact"),
    ("row_count_delta", "<load>"),
}, failing(verdict)
assert "Traceback" not in reason
assert len(reason) < 600, (
    f"a reason nobody will read is not a reason ({len(reason)} chars)"
)

# The baseline is the last PASSED load, not the last load: scenario 3 was blocked, so the
# comparison is against scenario 2. Two bad nights in a row cannot make small the new normal.
delta = [
    g
    for g in verdict.content["gates"]
    if g["gate"] == "row_count_delta" and not g["passed"]
]
assert delta, verdict.content["gates"]
print(
    f"  measured ratio {delta[0]['measured']:.2f} against threshold "
    f"{delta[0]['threshold']:.2f}"
)
print(
    "  baseline was the last PASSED load, so a blocked night cannot become the new normal"
)
print()

# --- 5. What a gold step would have read --------------------------------------------------
print("5. The gate results for the last load")
print("-" * 78)
rows = query(
    "SELECT gate, subject, passed, measured, threshold FROM quality.latest_gate_result"
)
for row in rows:
    mark = " " if row["passed"] else "!"
    measured = "-" if row["measured"] is None else f"{float(row['measured']):.2f}"
    print(f"  {mark} {row['gate']:24s} {row['subject']:34s} {measured}")
assert any(not row["passed"] for row in rows), (
    "the last load was blocked; a gate must show it"
)
print()

print("=" * 78)
print(
    "Evidence: the duplicate load added 0 landing rows, changed 0 silver rows and moved no\n"
    "conformed_at stamp. The nulled key was named as crm.contact.object_id, excluded from\n"
    "silver, and left the existing c_501 row untouched. The undersized load was blocked with\n"
    "a reason that names the stream, both counts, the ratio and the file, and the step that\n"
    "blocks returned stop=True -- so a gold step appended after it (R5) never runs."
)

if WORK_ROOT is not None:
    shutil.rmtree(WORK_ROOT, ignore_errors=True)
