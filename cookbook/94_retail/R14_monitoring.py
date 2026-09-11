"""R14 -- execution health from run history, plus the quality/drift assessment pass (D14).

The package's Verify line: "Disable a schedule and wait past its window. Leave a run's
output unchanged but degrade its input data. Then let a run fail." Its Evidence line: "The
silent schedule is flagged, which failure monitoring alone would miss. The degraded input is
flagged even though the run succeeded, which is the failure mode execution monitoring cannot
see. The failure is flagged with its reason."

**Scope** (the build plan's own D14 text, quoted in full in the PR): "Monitoring is a
separate pipeline, and golden questions plus rehearsal gate publish. Execution health and
answer quality are measured separately." This package owns execution health (from real run
history) and the input-drift half of the quality assessment. It does not own golden question
sets or the publish gate -- that is R15, building concurrently in a sibling worktree.
`golden_pass_rate` is accepted here as an input from R15's pipeline (or left `None` when R15
has not run for a use case yet, its own honest `unknown` state), never computed.

This script runs the three Verify scenarios in the order that builds real evidence for each,
against a real, isolated Postgres:

  0. Seed -- warehouse DDL, load C1's sample fixtures through R4, harvest silver, register
     the RFQ template's two KPIs, the same seed R10's cookbook uses (real workflows need real
     data to have real run history over).
  1. Drive `nightly-refresh` to two real successes (yesterday, today -- R10's own
     hand-edit-yesterday's-gold technique, so today's real materialisation has a mover to
     report) and `rfq-funnel` to one real success, so execution health has real, non-empty
     run history to summarise for both use cases.
  2. **Let a run fail.** Hold the landing-to-silver advisory lock (the same one
     `load_landing_files` itself takes) from this process, then drive `nightly-refresh` again:
     it raises for a real reason (lock contention) inside the first step. **This surfaced a
     real framework finding**: the non-streaming `arun()` path sets
     `WorkflowRunOutput.status = RunStatus.error` on the run object and then re-raises,
     unwinding straight past the code that would persist the session -- so the failure never
     reaches `WorkflowSession`/`get_sessions` at all (confirmed against this exact database:
     the run does not appear). In production this failure would be recorded because the real
     poller (`egpt.scheduler.executor.ScheduleExecutor`) calls a scheduled workflow over HTTP
     and writes `ScheduleRun.status = "failed"` in its own `except` clause on exactly this
     exception -- no HTTP router exists yet to drive that path here (interface wiring is phase
     five), so this section drives the same two `PostgresDb` primitives the poller itself
     calls (`create_schedule_run`/`update_schedule_run`) around a real raised exception from a
     real run: everything is real except the network hop. `assess_execution_health` merges
     `ScheduleRun` history in for exactly this reason (see `schedule_run_records`'s own
     docstring) and must report the failure with `last_run_status: "failed"` and
     `failures_7d >= 1`.
  3. **Disable a schedule and wait past its window.** A real schedule, registered through the
     same `ScheduleManager` `schedule-nightly` uses, with a cadence whose most recent expected
     fire is months in the past and no `ScheduleRun` ever recorded against it -- "waiting past
     its window" without an actual wait, the same standing the whole cookbook series takes on
     hand-editing gold to force a scenario the unmodified sample data cannot reach on its own.
     `list_silent_schedules` flags it while enabled, and *still* flags it once disabled --
     the D14 point that a schedule's cadence does not stop implying an expectation just
     because it was switched off.
  4. **Degrade a run's input data, output unchanged.** The real movers `sample_rows` computed
     in step 1 stand in for a version's rehearsal baseline; a hand-corrupted copy (a severe
     value shift plus a null-rate spike on half the rows) stands in for the same version's
     next run over data that has quietly gone bad. One hand-built explanation string is
     delivered for *both* -- literally the same object -- so the point is inescapable: nothing
     about the delivered text reveals the degradation, and `assess_quality` still flags it
     because it looks at the rows, not the prose.

Needs the same Postgres (control plane migrated, egpt_warehouse migrated) R05/R10 use, on
the compose stack's default ports:

    docker compose -f deploy/compose.yaml --profile full up -d postgres neo4j
    EGPT_RETAIL_DB_URL=postgresql+psycopg://ai:ai@localhost:5532/ai \\
      .venv/bin/python -m egpt_retail.entrypoints migrate
    EGPT_RETAIL_WAREHOUSE_DB_URL=postgresql+psycopg://ai:ai@localhost:5532/egpt_warehouse \\
      .venv/bin/python -m egpt_retail.entrypoints warehouse-migrate
    .venv/bin/python cookbook/94_retail/R14_monitoring.py

Every ``EGPT_RETAIL_*`` default above is overridable the normal way (export it, or prefix the
command) -- this is how it was actually run while developing R14 concurrently with R12 and
R15 in their own worktrees, each against its own isolated compose ports/project name (this
package's own worktree used 5733/7693, ``deploy/local-r14.override.yaml``, uncommitted).
"""

import asyncio
import copy
import logging
import os
import shutil
import sys
import tempfile
import time
import uuid
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "product"))

os.environ.setdefault(
    "EGPT_RETAIL_DB_URL", "postgresql+psycopg://ai:ai@localhost:5532/ai"
)
os.environ.setdefault(
    "EGPT_RETAIL_WAREHOUSE_DB_URL",
    "postgresql+psycopg://ai:ai@localhost:5532/egpt_warehouse",
)
os.environ.setdefault(
    "EGPT_RETAIL_WAREHOUSE_READONLY_DB_URL",
    "postgresql+psycopg://ai:ai@localhost:5532/egpt_warehouse",
)
os.environ.setdefault("EGPT_RETAIL_NEO4J_URI", "bolt://localhost:7687")
os.environ.setdefault("EGPT_RETAIL_NEO4J_PASSWORD", "password")
os.environ.setdefault("EGPT_RETAIL_ROLE", "pipelines")
os.environ.setdefault("EGPT_RETAIL_INTERNAL_SERVICE_TOKEN", "r14-cookbook-token")
os.environ.setdefault("EGPT_RETAIL_CUSTOMER_SLUG", "r14")

import psycopg  # noqa: E402
from egpt.run.base import RunStatus  # noqa: E402
from egpt.scheduler.manager import ScheduleManager  # noqa: E402
from egpt.workflow.types import StepInput  # noqa: E402
from egpt_retail.app.components import (  # noqa: E402
    NIGHTLY_REFRESH_WORKFLOW_ID,
    RFQ_WORKFLOW_ID,
    build_workflows,
)
from egpt_retail.app.db import (  # noqa: E402
    build_control_plane_db,
    build_warehouse_engine,
    psycopg_dsn,
)
from egpt_retail.app.semantic import open_semantic_reader  # noqa: E402
from egpt_retail.app.settings import RetailSettings  # noqa: E402
from egpt_retail.entrypoints.schedule_nightly import (  # noqa: E402
    DEFAULT_NIGHTLY_CRON,
    NIGHTLY_SCHEDULE_NAME,
)
from egpt_retail.executors.gold import read_gold  # noqa: E402
from egpt_retail.executors.monitoring import (  # noqa: E402
    DRIFT_SCORE_THRESHOLD,
    assess_execution_health,
    assess_quality,
    list_silent_schedules,
)
from egpt_retail.executors.rfq import sample_rows  # noqa: E402
from egpt_retail.kpi.rfq import RFQ_KPI_DEFINITIONS  # noqa: E402
from egpt_retail.kpi.store import KpiRegistryStore  # noqa: E402
from egpt_retail.semantic.harvest import harvest_schema  # noqa: E402
from egpt_retail.warehouse.ddl import apply_warehouse_ddl  # noqa: E402
from egpt_retail.warehouse.loads import LoadLock  # noqa: E402
from sqlalchemy import text  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(message)s")
logging.getLogger("egpt_retail").setLevel(logging.WARNING)

FIXTURES_ROOT = Path(
    os.environ.get("EGPT_LANDING_FIXTURES_ROOT")
    or REPO_ROOT / "product" / "egpt_retail" / "contracts" / "_fixtures" / "landing"
)
os.environ["EGPT_RETAIL_STORAGE_PROVIDER"] = "local"
WORK_ROOT = Path(tempfile.mkdtemp(prefix="r14-raw-"))
shutil.copytree(FIXTURES_ROOT / "raw", WORK_ROOT / "raw")
os.environ["EGPT_RETAIL_STORAGE_LOCAL_ROOT"] = str(WORK_ROOT)

SETTINGS = RetailSettings()
WAREHOUSE = build_warehouse_engine(SETTINGS)
TODAY = date.today()
YESTERDAY = TODAY - timedelta(days=1)

SILENT_SCHEDULE_NAME = "r14-cookbook-silent-schedule"
#: Once a year, always in the past relative to any real clock this cookbook runs under --
#: "wait past its window" without an actual wait, the same standing R05/R10 take on
#: hand-editing gold to force a scenario the sample data would not reach on its own.
STALE_CRON = "0 0 1 1 *"


def query(sql, **params):
    with WAREHOUSE.begin() as connection:
        return [dict(row) for row in connection.execute(text(sql), params).mappings()]


def execute(sql, **params):
    with WAREHOUSE.begin() as connection:
        connection.execute(text(sql), params)


def reset_warehouse():
    with WAREHOUSE.begin() as connection:
        connection.exec_driver_sql(
            "TRUNCATE landing.raw_row, quality.load_run CASCADE; "
            "TRUNCATE silver.analytics_event, silver.crm_contact, silver.crm_deal, "
            "silver.crm_deal_stage_history, silver.session_replay_session; "
            "TRUNCATE gold.kpi_value, gold.kpi_run"
        )


def run_nightly(**options):
    options.setdefault("trigger", "cookbook")
    workflow = next(
        w
        for w in build_workflows(SETTINGS, build_control_plane_db(SETTINGS))
        if w.id == NIGHTLY_REFRESH_WORKFLOW_ID
    )
    return asyncio.run(workflow.arun(input="nightly", additional_data=options))


checks = {}

print("R14 -- execution health from run history, plus the quality/drift pass (D14)")
print("=" * 78)
print(f"warehouse:     {SETTINGS.retail_warehouse_db_url}")
print(f"control plane: {SETTINGS.retail_db_url}")
print(f"semantic:      {SETTINGS.retail_neo4j_uri}")
print()

# --- 0. Seed: schema, semantic layer, the RFQ template's two KPIs, a clean slate ---------------
print(
    "0. Seed -- warehouse DDL, harvest silver into the graph, the RFQ template's two KPIs"
)
print("-" * 78)
applied = apply_warehouse_ddl(WAREHOUSE)
print(f"  {len(applied)} DDL scripts applied")
reset_warehouse()

raw = psycopg.connect(psycopg_dsn(str(SETTINGS.retail_warehouse_db_url)))
raw.autocommit = True
with open_semantic_reader(SETTINGS) as reader:
    reader.ensure_indexes()
    reader.write_harvest(
        harvest_schema(raw, "silver", source="egpt_warehouse", sample=False),
        snapshot_version="r14.1",
    )
raw.close()

control_plane = psycopg.connect(psycopg_dsn(SETTINGS.retail_db_url))
store = KpiRegistryStore(control_plane)
store.ensure_schema()
with control_plane.cursor() as cursor:
    cursor.execute("DELETE FROM semantic.kpi_definition WHERE identifier LIKE 'rfq__%'")
control_plane.commit()
print(f"  registry: {store.save_many(RFQ_KPI_DEFINITIONS)} definitions saved")

CONTROL_PLANE_DB = build_control_plane_db(SETTINGS)
manager = ScheduleManager(CONTROL_PLANE_DB)
# Idempotent reruns, the same rule R10's cookbook states for its own fixed run ids: this
# cookbook must remove everything it wrote to a shared store before writing it again, or the
# equality checks below (an exact runs_7d/failures_7d count) drift upward on every rerun.
# `egpt_schedules`/`egpt_schedule_runs` are created lazily on first use (framework tables
# outside `MigrationManager`'s fixed set), so a first-ever run of this cookbook against a
# fresh database has neither yet -- caught and ignored rather than pre-checked, the same
# "ask forgiveness" the schedule tables' own lazy creation already commits to.
with control_plane.cursor() as cursor:
    cursor.execute(
        "DELETE FROM egpt_sessions WHERE workflow_id IN (%s, %s)",
        (NIGHTLY_REFRESH_WORKFLOW_ID, RFQ_WORKFLOW_ID),
    )
    control_plane.commit()
    try:
        cursor.execute(
            "DELETE FROM egpt_schedule_runs WHERE schedule_id IN "
            "(SELECT id FROM egpt_schedules WHERE name IN (%s, %s))",
            (NIGHTLY_SCHEDULE_NAME, SILENT_SCHEDULE_NAME),
        )
        cursor.execute(
            "DELETE FROM egpt_schedules WHERE name = %s", (SILENT_SCHEDULE_NAME,)
        )
        control_plane.commit()
    except psycopg.errors.UndefinedTable:
        control_plane.rollback()
print("  cleared this cookbook's own run/schedule history from any previous run")
print()

# --- 1. Real run history: nightly-refresh twice (a real mover forced by hand), rfq-funnel once -
print("1. Real run history -- nightly-refresh to two successes, rfq-funnel to one")
print("-" * 78)
first = run_nightly(as_of=YESTERDAY.isoformat())
print(f"  yesterday run {first.run_id}: {first.status.value}")
checks["nightly-refresh: yesterday's run completed"] = (
    first.status == RunStatus.completed
)

yesterday_rows = query(
    "SELECT dimension->>'brand' AS brand, value FROM gold.kpi_value "
    "WHERE kpi_identifier = 'rfq__quote_requests_by_brand' AND as_of = :as_of",
    as_of=YESTERDAY,
)
if yesterday_rows:
    edited_brand = yesterday_rows[0]["brand"]
    edited_value = float(yesterday_rows[0]["value"])
    execute(
        "UPDATE gold.kpi_value SET value = :v "
        "WHERE kpi_identifier = 'rfq__quote_requests_by_brand' AND as_of = :as_of "
        "AND dimension->>'brand' = :brand",
        v=edited_value * 3 + 5,
        as_of=YESTERDAY,
        brand=edited_brand,
    )
    print(f"  hand-edited yesterday's {edited_brand!r} to force a real mover")

second = run_nightly(as_of=TODAY.isoformat())
print(f"  today run {second.run_id}: {second.status.value}")
checks["nightly-refresh: today's run completed"] = second.status == RunStatus.completed

identifiers = [kpi.identifier for kpi in RFQ_KPI_DEFINITIONS]
read_output = asyncio.run(
    read_gold(
        StepInput(
            additional_data={"kpi_identifiers": identifiers, "as_of": TODAY.isoformat()}
        )
    )
)
sample_output = asyncio.run(
    sample_rows(
        StepInput(
            additional_data={"kpi_identifiers": identifiers},
            previous_step_content=read_output.content,
        )
    )
)
baseline_rows = sample_output.content["movers"].get("rfq__quote_requests_by_brand", [])
print(f"  sample_rows movers for rfq__quote_requests_by_brand: {baseline_rows}")
checks["real movers exist to build the drift scenario from"] = bool(baseline_rows)

has_credentials = bool(os.environ.get("AZURE_OPENAI_API_KEY"))
print(
    f"  rfq-funnel: model credentials {has_credentials} -- "
    + (
        "driving it end to end for real history"
        if has_credentials
        else "not driving workflow.arun() (its one model step would fail with no "
        "provider, the same reason R10's own cookbook keeps its offline half off "
        "workflow.arun() entirely); left with no run history, the same honest empty "
        "shape the API contract's own uc_0002 worked example shows"
    )
)
if has_credentials:
    from egpt_retail.app.components import build_agents

    agents = build_agents(SETTINGS, build_control_plane_db(SETTINGS))
    rfq_workflow = next(
        w
        for w in build_workflows(SETTINGS, build_control_plane_db(SETTINGS), agents)
        if w.id == RFQ_WORKFLOW_ID
    )
    rfq_run = asyncio.run(
        rfq_workflow.arun(
            input="rfq",
            additional_data={
                "trigger": "cookbook",
                "use_case_id": "uc_rfq_0001",
                "kpi_identifiers": identifiers,
                "kpis": [],
                "available_tables": set(),
                "run_id": "r14-cookbook-rfq-run",
            },
        )
    )
    print(f"  rfq-funnel run {rfq_run.run_id}: {rfq_run.status.value}")
    checks["rfq-funnel: a run was persisted to real history"] = (
        rfq_run.status == RunStatus.completed
    )
print()

# --- 2. Let a run fail: hold the load lock, drive nightly-refresh again -------------------------
print(
    "2. Let a run fail -- hold the landing-to-silver advisory lock, run nightly-refresh"
)
print("-" * 78)
nightly_schedule = manager.create(
    name=NIGHTLY_SCHEDULE_NAME,
    cron=DEFAULT_NIGHTLY_CRON,
    endpoint=f"/workflows/{NIGHTLY_REFRESH_WORKFLOW_ID}/runs",
    description="R14 cookbook: the real nightly-refresh schedule (schedule-nightly's own name/cron).",
    if_exists="update",
)
print(
    f"  registered {nightly_schedule.name!r} (id={nightly_schedule.id}, "
    f"cron={nightly_schedule.cron_expr!r})"
)

lock = LoadLock(psycopg_dsn(str(SETTINGS.retail_warehouse_db_url)))
held = lock.acquire()
checks["this cookbook holds the real advisory lock load_landing_files also takes"] = (
    held
)

# The real poller's own two primitives (`egpt.scheduler.executor.ScheduleExecutor`), driven
# by hand around a real raised exception -- see the module docstring's step 2 for why this,
# not `workflow.arun()`'s own return value, is where this failure is actually recorded.
run_record_id = str(uuid.uuid4())
triggered_at = int(time.time())
CONTROL_PLANE_DB.create_schedule_run(
    {
        "id": run_record_id,
        "schedule_id": nightly_schedule.id,
        "attempt": 1,
        "triggered_at": triggered_at,
        "completed_at": None,
        "status": "running",
        "status_code": None,
        "run_id": None,
        "session_id": None,
        "error": None,
        "input": None,
        "output": None,
        "requirements": None,
        "created_at": triggered_at,
    }
)
real_error = None
try:
    run_nightly(as_of=TODAY.isoformat())
    print("  unexpected: nightly-refresh completed despite the held lock")
except (
    Exception
) as exc:  # the real exception load_landing_files raises on lock contention
    real_error = str(exc)
finally:
    lock.release()
CONTROL_PLANE_DB.update_schedule_run(
    run_record_id,
    completed_at=int(time.time()),
    status="failed" if real_error else "success",
    error=real_error,
)
schedule_run_status = "failed" if real_error else "success"
print(f"  ScheduleRun {run_record_id}: status={schedule_run_status!r}")
print(f"  error: {real_error}")
checks[
    "a real lock contention raises, and is recorded as a real ScheduleRun failure with its reason"
] = bool(real_error) and "advisory lock" in real_error
print()

# --- 3. assess_execution_health, called for real, over both use cases' real run history ---------
print("3. assess_execution_health -- real numbers over the run history built above")
print("-" * 78)
health_output = asyncio.run(
    assess_execution_health(
        StepInput(
            additional_data={
                "use_cases": [
                    {
                        "use_case_id": "uc_nightly_refresh",
                        "title": "Nightly refresh",
                        "version": 1,
                        "workflow_id": NIGHTLY_REFRESH_WORKFLOW_ID,
                        "schedule_name": NIGHTLY_SCHEDULE_NAME,
                    },
                    {
                        "use_case_id": "uc_rfq_0001",
                        "title": "RFQ funnel -- weekly",
                        "version": 1,
                        "workflow_id": RFQ_WORKFLOW_ID,
                    },
                ]
            }
        )
    )
)
for use_case in health_output.content["use_cases"]:
    print(f"  {use_case}")
nightly_health = next(
    u
    for u in health_output.content["use_cases"]
    if u["use_case_id"] == "uc_nightly_refresh"
)
rfq_health = next(
    u for u in health_output.content["use_cases"] if u["use_case_id"] == "uc_rfq_0001"
)
checks[
    "execution health: the failure is flagged with runs_7d/failures_7d real counts"
] = nightly_health["runs_7d"] == 3 and nightly_health["failures_7d"] == 1
checks[
    "execution health: last_run_status reports the real failure, not the prior success"
] = nightly_health["last_run_status"] == "failed"
checks[
    "execution health: nightly-refresh is not silent -- it fired, even though the last fire failed"
] = nightly_health["silent"] is False
if has_credentials:
    checks["execution health: rfq-funnel's real run also shows up"] = (
        rfq_health["runs_7d"] >= 1
    )
else:
    checks[
        "execution health: with no model credentials, rfq-funnel honestly reports no runs"
    ] = rfq_health["runs_7d"] == 0 and rfq_health["last_run_status"] is None
print()

# --- 4. Disable a schedule and wait past its window ---------------------------------------------
print(
    "4. Disable a schedule and wait past its window -- list_silent_schedules, for real"
)
print("-" * 78)
schedule = manager.create(
    name=SILENT_SCHEDULE_NAME,
    cron=STALE_CRON,
    endpoint=f"/workflows/{NIGHTLY_REFRESH_WORKFLOW_ID}/runs",
    description="R14 cookbook: a cadence whose window closed long ago, with no run ever recorded.",
    if_exists="update",
)
print(f"  registered {schedule.name!r} (id={schedule.id}, cron={schedule.cron_expr!r})")

silent_while_enabled = asyncio.run(
    list_silent_schedules(
        StepInput(
            additional_data={
                "schedules": [{"name": SILENT_SCHEDULE_NAME, "use_case_id": "uc_0002"}]
            }
        )
    )
).content["silent_schedules"]
print(f"  while enabled: {silent_while_enabled}")
checks["a schedule past its window with no runs is flagged while enabled"] = bool(
    silent_while_enabled
)

manager.disable(schedule.id)
silent_while_disabled = asyncio.run(
    list_silent_schedules(
        StepInput(
            additional_data={
                "schedules": [{"name": SILENT_SCHEDULE_NAME, "use_case_id": "uc_0002"}]
            }
        )
    )
).content["silent_schedules"]
print(f"  after manager.disable(): {silent_while_disabled}")
checks[
    "the same schedule stays flagged once disabled -- silence does not depend on 'enabled'"
] = bool(silent_while_disabled) and silent_while_disabled == silent_while_enabled
print()

# --- 5. Degrade a run's input data, output unchanged ---------------------------------------------
print(
    "5. Degrade a run's input data, output unchanged -- assess_quality, for real rows"
)
print("-" * 78)
# The sample fixture data carries exactly one brand for this KPI (confirmed directly against
# gold.kpi_value above), so `baseline_rows` is scoped to the one field a version's rehearsal
# baseline actually cares about -- `current_value`, the measured metric itself -- rather than
# the whole mover row verbatim. Carrying `brand`/`direction`/`delta` along unchanged would
# only dilute a real shift in the metric with identical, irrelevant fields; a real deployment
# comparing two versions' input distributions would make the same choice of what the "input"
# to compare actually is, the same way K6 compares structure rather than a whole harvest.
baseline_rows = [{"current_value": row["current_value"]} for row in baseline_rows]
degraded_rows = copy.deepcopy(baseline_rows)
for index, row in enumerate(degraded_rows):
    if index % 2 == 0 and row.get("current_value") is not None:
        # A severe outlier on half the rows...
        row["current_value"] = str(float(row["current_value"]) * 200 + 500)
    else:
        # ...and a null-rate spike on the other half.
        row["current_value"] = None
print(f"  baseline rows: {baseline_rows}")
print(f"  degraded rows: {degraded_rows}")

# The same delivered text for both -- literally the same string object -- so nothing about
# what was actually shown to a person differs between the healthy and the degraded case.
delivered_summary = "Quote requests moved across brands this period; see the funnel detail for specifics."
print(f"  delivered explanation (identical both times): {delivered_summary!r}")

quality_output = asyncio.run(
    assess_quality(
        StepInput(
            additional_data={
                "use_cases": [
                    {
                        "use_case_id": "uc_rfq_0001",
                        "version": 4,
                        "baseline_rows": baseline_rows,
                        "current_rows": degraded_rows,
                    },
                    {
                        "use_case_id": "uc_rfq_0001",
                        "version": 3,
                        "baseline_rows": baseline_rows,
                        "current_rows": baseline_rows,
                    },
                ]
            }
        )
    )
).content["use_cases"]
degraded_quality, unchanged_quality = quality_output
print(f"  degraded input -> {degraded_quality}")
print(f"  unchanged input -> {unchanged_quality}")

checks["the degraded input is flagged even though the run itself succeeded"] = (
    degraded_quality["verdict"] == "degraded"
    and degraded_quality["drift_score"] is not None
    and degraded_quality["drift_score"] >= DRIFT_SCORE_THRESHOLD
    and degraded_quality["golden_pass_rate"] is None
)
checks["an unchanged input against itself is not flagged (drift_score == 0.0)"] = (
    unchanged_quality["verdict"] == "healthy"
    and unchanged_quality["drift_score"] == 0.0
)
checks["the degraded verdict names a reason, not a bare label"] = bool(
    degraded_quality["notes"]
)

manager.delete(schedule.id)
manager.close()
control_plane.close()
WAREHOUSE.dispose()

print()
print("=" * 78)
for name, ok in checks.items():
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
print("Overall:", "PASS" if all(checks.values()) else "FAIL")
raise SystemExit(0 if all(checks.values()) else 1)
