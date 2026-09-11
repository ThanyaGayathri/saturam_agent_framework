"""R12 -- alerts (G7), against a real warehouse and control plane.

The package's Verify line: "Set a threshold below the current value so it must fire, and run.
Then set it above so it must not, and run again." Its Evidence line: "The first fires and
delivers. The second stays silent but still records that it ran and did not fire, so a quiet
week is visibly quiet rather than indistinguishable from broken."

This script runs that scenario directly against the real ``evaluate_alerts`` executor and the
``alert-evaluation`` workflow (``evaluate_alerts`` then R7's ``record_provenance``), the same
"drive it with `.arun()`, no HTTP router" pattern R07/R10/R05 use:

  0. Seed -- warehouse DDL, load C1's sample fixtures through R4, harvest silver, register one
     real scalar KPI, materialise gold for it via ``nightly-refresh``.
  1. Refuse a rowset KPI at alert-creation time -- the contract's own 422 worked example
     (``kpi_not_scalar``), reproduced against ``AlertDefinitionStore.create`` directly.
  2. Create one enabled alert definition with its threshold set *below* the KPI's real gold
     value (``gt``, so the current value must cross it), then run ``alert-evaluation``: it
     must fire, write one ``alert_events`` row, deliver through R7's own
     ``retail_deliveries`` table, and bump both ``last_evaluated_at`` and ``last_fired_at``.
  3. Edit the threshold to sit *above* the current value and run again: it must not fire --
     no new event, no new delivery -- but ``last_evaluated_at`` still advances and the run's
     own content still names the alert as evaluated, which is what makes a quiet week
     distinguishable from a broken one.
  4. The same two runs again, this time through ``evaluate_alert_condition`` and
     ``write_alert`` called directly with ``read_gold``'s own output, the shape a future
     single-alert compiled Alert-shape workflow (K15) would call them with.

Needs the compose stack's Postgres and Neo4j (this repo's isolated ports, per the R12
worktree's own local-infra override -- see cookbook/94_retail/README.md):

    docker compose -f deploy/compose.yaml -f deploy/local-r12.override.yaml \\
      -p egpt-retail-r12 --profile full up -d postgres neo4j
    EGPT_RETAIL_DB_URL=postgresql+psycopg://ai:ai@localhost:5633/ai \\
      .venv/bin/python -m egpt_retail.entrypoints migrate
    EGPT_RETAIL_WAREHOUSE_DB_URL=postgresql+psycopg://ai:ai@localhost:5633/egpt_warehouse \\
      .venv/bin/python -m egpt_retail.entrypoints warehouse-migrate
    .venv/bin/python cookbook/94_retail/R12_alerts.py
"""

import asyncio
import logging
import os
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "product"))

os.environ.setdefault(
    "EGPT_RETAIL_DB_URL", "postgresql+psycopg://ai:ai@localhost:5633/ai"
)
os.environ.setdefault(
    "EGPT_RETAIL_WAREHOUSE_DB_URL",
    "postgresql+psycopg://ai:ai@localhost:5633/egpt_warehouse",
)
os.environ.setdefault(
    "EGPT_RETAIL_WAREHOUSE_READONLY_DB_URL",
    "postgresql+psycopg://ai:ai@localhost:5633/egpt_warehouse",
)
os.environ.setdefault("EGPT_RETAIL_NEO4J_URI", "bolt://localhost:7692")
os.environ.setdefault("EGPT_RETAIL_NEO4J_PASSWORD", "password")
os.environ.setdefault("EGPT_RETAIL_ROLE", "pipelines")
os.environ.setdefault("EGPT_RETAIL_INTERNAL_SERVICE_TOKEN", "r12-cookbook-token")
os.environ.setdefault("EGPT_RETAIL_CUSTOMER_SLUG", "r12")

import psycopg  # noqa: E402
from egpt.workflow.types import StepInput  # noqa: E402
from egpt_retail.alerts import (  # noqa: E402
    AlertDefinitionRejected,
    AlertDefinitionStore,
    AlertEventStore,
)
from egpt_retail.alerts.condition import condition_met  # noqa: E402
from egpt_retail.app.components import (  # noqa: E402
    ALERT_EVALUATION_WORKFLOW_ID,
    NIGHTLY_REFRESH_WORKFLOW_ID,
    build_workflows,
)
from egpt_retail.app.db import (  # noqa: E402
    build_control_plane_db,
    build_control_plane_engine,
    build_warehouse_engine,
    psycopg_dsn,
)
from egpt_retail.app.semantic import open_semantic_reader  # noqa: E402
from egpt_retail.app.settings import RetailSettings  # noqa: E402
from egpt_retail.contracts.kpi import KpiDefinition, ResultShape  # noqa: E402
from egpt_retail.executors.alerts import (  # noqa: E402
    evaluate_alert_condition,
    write_alert,
)
from egpt_retail.executors.gold import read_gold  # noqa: E402
from egpt_retail.kpi.store import KpiRegistryStore  # noqa: E402
from egpt_retail.semantic.harvest import harvest_schema  # noqa: E402
from egpt_retail.warehouse.ddl import apply_warehouse_ddl  # noqa: E402
from sqlalchemy import text  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(message)s")
logging.getLogger("egpt_retail").setLevel(logging.WARNING)

# --- The raw drop: a working copy of C1's fixtures, as R05/R10 do ------------------------------
FIXTURES_ROOT = Path(
    os.environ.get("EGPT_LANDING_FIXTURES_ROOT")
    or REPO_ROOT / "product" / "egpt_retail" / "contracts" / "_fixtures" / "landing"
)
os.environ["EGPT_RETAIL_STORAGE_PROVIDER"] = "local"
WORK_ROOT = Path(tempfile.mkdtemp(prefix="r12-raw-"))
shutil.copytree(FIXTURES_ROOT / "raw", WORK_ROOT / "raw")
os.environ["EGPT_RETAIL_STORAGE_LOCAL_ROOT"] = str(WORK_ROOT)

SETTINGS = RetailSettings()
WAREHOUSE = build_warehouse_engine(SETTINGS)
CONTROL_PLANE_ENGINE = build_control_plane_engine(SETTINGS)
TODAY = date.today()

#: A real, scalar, grain="overall" KPI -- the same proven-working formula
#: R05_gold_materialisation.py registers as k11_contact__reachable, under a name namespaced
#: to this package so a shared control-plane database never sees a collision with R05's own
#: registry row.
REACHABLE_CONTACTS = KpiDefinition(
    identifier="r12__reachable_contacts",
    name="Reachable contacts",
    description="Contacts holding an email address, for R12's alert threshold.",
    sql="SELECT count(*) FROM crm_contact WHERE email IS NOT NULL",
    grain="overall",
    business_area="customer",
    source_tables=frozenset({"crm_contact"}),
)

#: A rowset KPI, never materialised -- exists only so section 1 can reproduce the contract's
#: own 422 worked example against a real (if uncomputed) definition.
REORDER_URGENCY_LIST = KpiDefinition(
    identifier="r12__reorder_urgency_list",
    name="Reorder urgency list",
    description="SKUs ranked by reorder urgency -- a listing, not a metric.",
    sql="SELECT sku, urgency FROM crm_deal",
    grain="product",
    business_area="inventory",
    source_tables=frozenset({"crm_deal"}),
    result_shape=ResultShape.ROWSET,
)

ALERT_ID = "alr_r12_cookbook"


def query(sql, **params):
    with WAREHOUSE.begin() as connection:
        return [dict(row) for row in connection.execute(text(sql), params).mappings()]


def cp_query(sql, **params):
    """Like :func:`query`, but against the control-plane database -- ``retail_deliveries``,
    ``egpt_answer_provenance`` and both alert tables live there, not in the warehouse."""
    with CONTROL_PLANE_ENGINE.begin() as connection:
        return [dict(row) for row in connection.execute(text(sql), params).mappings()]


def reset_warehouse():
    with WAREHOUSE.begin() as connection:
        connection.exec_driver_sql(
            "TRUNCATE landing.raw_row, quality.load_run CASCADE; "
            "TRUNCATE silver.analytics_event, silver.crm_contact, silver.crm_deal, "
            "silver.crm_deal_stage_history, silver.session_replay_session; "
            "TRUNCATE gold.kpi_value, gold.kpi_run"
        )


def run_nightly(**options):
    """One run of nightly-refresh, driven with `arun` (its executors are async)."""
    options.setdefault("trigger", "cookbook")
    options.setdefault("as_of", TODAY.isoformat())
    workflow = next(
        w
        for w in build_workflows(SETTINGS, build_control_plane_db(SETTINGS))
        if w.id == NIGHTLY_REFRESH_WORKFLOW_ID
    )
    return asyncio.run(workflow.arun(input="nightly", additional_data=options))


def run_alert_evaluation(run_id, **options):
    """One run of alert-evaluation: `evaluate_alerts` then `record_provenance`."""
    options.setdefault("trigger", "cookbook")
    workflow = next(
        w
        for w in build_workflows(SETTINGS, build_control_plane_db(SETTINGS))
        if w.id == ALERT_EVALUATION_WORKFLOW_ID
    )
    return asyncio.run(
        workflow.arun(
            input="alert-evaluation",
            run_id=run_id,
            additional_data={"run_id": run_id, **options},
        )
    )


def evaluate_step(output, name):
    return next(s for s in output.step_results if s.step_name == name)


checks = {}

print("R12 -- alerts (G7)")
print("=" * 78)
print(f"warehouse:     {SETTINGS.retail_warehouse_db_url}")
print(f"control plane: {SETTINGS.retail_db_url}")
print(f"semantic:      {SETTINGS.retail_neo4j_uri}")
print()

# --- 0. Seed: warehouse DDL, silver harvest, registry, gold for one real KPI --------------------
print("0. Seed -- warehouse DDL, harvest silver into the graph, one real scalar KPI")
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
        snapshot_version="r12.1",
    )
    snapshot_version = reader.snapshot().version
raw.close()
print(f"  semantic snapshot {snapshot_version} harvested")

control_plane = psycopg.connect(psycopg_dsn(SETTINGS.retail_db_url))
kpi_store = KpiRegistryStore(control_plane)
kpi_store.ensure_schema()
with control_plane.cursor() as cursor:
    cursor.execute("DELETE FROM semantic.kpi_definition WHERE identifier LIKE 'r12__%'")
    # A re-run of this script must not collide with its own previous run's rows -- the same
    # "a cookbook must remove everything it writes to a store another run reads" rule R05/R10
    # both follow, applied here to alert_definitions/alert_events/deliveries/provenance.
    cursor.execute("DELETE FROM alert_events WHERE alert_id LIKE 'alr_r12%'")
    cursor.execute("DELETE FROM alert_definitions WHERE id LIKE 'alr_r12%'")
    cursor.execute(
        "DELETE FROM egpt_answer_provenance WHERE run_id IN "
        "('r12-cookbook-fires', 'r12-cookbook-fires-again', 'r12-cookbook-silent', "
        "'r12-cookbook-standalone', 'r12-cookbook-isolation')"
    )
    cursor.execute(
        "DELETE FROM retail_deliveries WHERE run_id IN "
        "('r12-cookbook-fires', 'r12-cookbook-fires-again', 'r12-cookbook-silent', "
        "'r12-cookbook-standalone', 'r12-cookbook-isolation')"
    )
control_plane.commit()
print(
    f"  registry: {kpi_store.save_many([REACHABLE_CONTACTS, REORDER_URGENCY_LIST])} "
    "definitions saved"
)

nightly = run_nightly()
print(f"  nightly-refresh run {nightly.run_id}: {nightly.status.value}")
checks["nightly-refresh materialised the alert's KPI"] = (
    nightly.status.value == "COMPLETED"
)

current_value = float(
    query(
        "SELECT value FROM gold.kpi_value WHERE kpi_identifier = :kpi AND as_of = :as_of",
        kpi=REACHABLE_CONTACTS.identifier,
        as_of=TODAY,
    )[0]["value"]
)
print(f"  {REACHABLE_CONTACTS.identifier} materialised as {current_value} at {TODAY}")
print()

# --- 1. Refuse a rowset KPI at creation time, the contract's own 422 example --------------------
print("1. A rowset KPI is refused at creation time -- kpi_not_scalar (422)")
print("-" * 78)
control_plane_engine = CONTROL_PLANE_ENGINE
definition_store = AlertDefinitionStore(control_plane_engine)
event_store = AlertEventStore(control_plane_engine)

try:
    definition_store.create(
        kpi=REORDER_URGENCY_LIST,
        name="Should never be created",
        kpi_identifier=REORDER_URGENCY_LIST.identifier,
        operator="gt",
        threshold=0,
        severity="warning",
        recipients=[{"channel": "in_app", "address": ""}],
    )
    rejected = None
except AlertDefinitionRejected as exc:
    rejected = exc
print(f"  refused: code={getattr(rejected, 'code', None)!r} message={rejected}")
checks["a rowset KPI is refused with code=kpi_not_scalar"] = (
    rejected is not None and rejected.code == "kpi_not_scalar"
)
print()

# --- 2. Threshold below the current value: it must fire and deliver -----------------------------
print("2. Threshold below the current value -- it must fire and deliver")
print("-" * 78)
fire_threshold = current_value - 1
definition = definition_store.create(
    id=ALERT_ID,
    kpi=REACHABLE_CONTACTS,
    name="Reachable contacts above floor (R12 cookbook)",
    kpi_identifier=REACHABLE_CONTACTS.identifier,
    operator="gt",
    threshold=fire_threshold,
    severity="warning",
    recipients=[
        {"channel": "in_app", "address": ""},
        {"channel": "email", "address": "ops@example.test"},
    ],
    recommended_actions=["Check the contact import for the affected brand."],
    use_case_id=None,
    process_id=None,
)
print(
    f"  created {definition['id']}: {REACHABLE_CONTACTS.identifier} > {fire_threshold}"
)
checks["last_evaluated_at and last_fired_at start unset"] = (
    definition["last_evaluated_at"] is None and definition["last_fired_at"] is None
)

fire_run_id = "r12-cookbook-fires"
fire_output = run_alert_evaluation(fire_run_id)
evaluate_output = evaluate_step(fire_output, "evaluate-alerts")
print(f"  run {fire_output.run_id}: {fire_output.status.value}")
print(f"  evaluate_alerts content: {evaluate_output.content}")
checks["the run completed through record_provenance"] = [
    s.step_name for s in fire_output.step_results
] == ["evaluate-alerts", "record-provenance"]
checks["the alert is reported as fired"] = any(
    e["alert_id"] == ALERT_ID and e.get("fired")
    for e in evaluate_output.content["evaluated"]
)
checks["evaluate_alerts returned the new event"] = (
    len(evaluate_output.content["events"]) == 1
)

after_fire = definition_store.get(ALERT_ID)
print(
    f"  definition after firing: last_evaluated_at={after_fire['last_evaluated_at']} "
    f"last_fired_at={after_fire['last_fired_at']}"
)
checks["last_evaluated_at and last_fired_at are both stamped after firing"] = (
    after_fire["last_evaluated_at"] is not None
    and after_fire["last_fired_at"] is not None
)

events = event_store.list(alert_id=ALERT_ID)
print(f"  alert_events rows for {ALERT_ID}: {len(events)}")
checks["exactly one alert_events row was written"] = len(events) == 1
if events:
    event = events[0]
    print(f"    id={event['id']} title={event['title']!r} rule={event['rule']!r}")
    print(f"    kpi={event['kpi']}")
    checks["the event's value matches the real gold value"] = (
        abs(event["kpi"]["value"] - current_value) < 1e-9
    )
    checks["the event carries this run's id as both run_id and provenance_ref"] = (
        event["run_id"] == fire_run_id and event["provenance_ref"] == fire_run_id
    )

deliveries = cp_query(
    "SELECT channel, dry_run, content FROM retail_deliveries WHERE run_id = :run_id "
    "ORDER BY channel",
    run_id=fire_run_id,
)
print(f"  retail_deliveries rows for this run: {[d['channel'] for d in deliveries]}")
checks["delivery went through R7's own retail_deliveries table, once per recipient"] = {
    d["channel"] for d in deliveries
} == {"in_app", "email"}

provenance_row = cp_query(
    "SELECT run_id, datasets_read FROM egpt_answer_provenance WHERE run_id = :run_id",
    run_id=fire_run_id,
)
print(f"  provenance row: {provenance_row}")
checks["record_provenance wrote one row naming the gold dataset read_gold notes"] = (
    bool(provenance_row)
    and any(
        "kpi_value" in str(d) or "r12__reachable_contacts" in str(d)
        for d in provenance_row[0]["datasets_read"]
    )
)
print()

# --- 2b. The same still-breaching definition, evaluated again: no duplicate fire ---------------
print(
    "2b. Evaluated again while still breaching -- no second event, recipients not paged "
    "twice (round 2 review, PR #210)"
)
print("-" * 78)
refire_run_id = "r12-cookbook-fires-again"
refire_output = run_alert_evaluation(refire_run_id)
refire_evaluate = evaluate_step(refire_output, "evaluate-alerts")
print(f"  run {refire_output.run_id}: {refire_output.status.value}")
print(f"  evaluate_alerts content: {refire_evaluate.content}")
refire_entry = next(
    e for e in refire_evaluate.content["evaluated"] if e["alert_id"] == ALERT_ID
)
checks["a repeat evaluation still honestly reports the condition as fired"] = (
    refire_entry["fired"] is True
)
checks["but this pass created no new event"] = (
    len(refire_evaluate.content["events"]) == 0
)
events_after_refire = event_store.list(alert_id=ALERT_ID)
checks["alert_events still holds exactly the one row from the first firing"] = (
    len(events_after_refire) == 1 and events_after_refire[0]["id"] == event["id"]
)
refire_deliveries = cp_query(
    "SELECT channel FROM retail_deliveries WHERE run_id = :run_id", run_id=refire_run_id
)
checks["recipients are not paged a second time for the same window"] = (
    len(refire_deliveries) == 0
)
print(
    "  -> the UNIQUE(alert_id, as_of) index on alert_events (alerts/ddl.py) makes this a "
    "database guarantee, not just an in-process check"
)
# Refreshed: this pass still fired (honestly), so last_fired_at moved again even though no
# new event was written -- section 3's "did NOT advance on the silent run" comparison must
# be against this run's stamp, the most recent actual firing, not section 2's earlier one.
after_fire = definition_store.get(ALERT_ID)
print()

# --- 3. Threshold above the current value: it must stay silent, but still record the run -------
print(
    "3. Threshold above the current value -- it must stay silent, but the run is recorded"
)
print("-" * 78)
silent_threshold = current_value + 1
updated_definition = definition_store.update(ALERT_ID, threshold=silent_threshold)
print(
    f"  updated threshold to {silent_threshold} via AlertDefinitionStore.update "
    f"({REACHABLE_CONTACTS.identifier} > {silent_threshold})"
)
checks["AlertDefinitionStore.update wrote the new threshold"] = (
    updated_definition is not None
    and updated_definition["threshold"] == silent_threshold
)

try:
    definition_store.update(
        ALERT_ID,
        kpi_identifier=REORDER_URGENCY_LIST.identifier,
        kpi=REORDER_URGENCY_LIST,
    )
    retarget_rejected = None
except AlertDefinitionRejected as exc:
    retarget_rejected = exc
print(
    f"  retargeting to a rowset KPI refused: code={getattr(retarget_rejected, 'code', None)!r} "
    f"message={retarget_rejected}"
)
checks["update() re-validates and refuses retargeting to a rowset KPI"] = (
    retarget_rejected is not None and retarget_rejected.code == "kpi_not_scalar"
)
checks["the refused retarget did not change the definition's kpi_identifier"] = (
    definition_store.get(ALERT_ID)["kpi_identifier"] == REACHABLE_CONTACTS.identifier
)

silent_run_id = "r12-cookbook-silent"
silent_output = run_alert_evaluation(silent_run_id)
silent_evaluate = evaluate_step(silent_output, "evaluate-alerts")
print(f"  run {silent_output.run_id}: {silent_output.status.value}")
print(f"  evaluate_alerts content: {silent_evaluate.content}")
this_alert = next(
    e for e in silent_evaluate.content["evaluated"] if e["alert_id"] == ALERT_ID
)
checks["the alert is reported as evaluated but not fired"] = (
    this_alert["fired"] is False
)
checks["no new event was created on the silent run"] = (
    len(silent_evaluate.content["events"]) == 0
)

after_silent = definition_store.get(ALERT_ID)
print(
    f"  definition after the silent run: last_evaluated_at={after_silent['last_evaluated_at']} "
    f"last_fired_at={after_silent['last_fired_at']} (unchanged from the firing run)"
)
checks["last_evaluated_at advanced on the silent run"] = (
    after_silent["last_evaluated_at"] > after_fire["last_evaluated_at"]
)
checks["last_fired_at did NOT advance on the silent run"] = (
    after_silent["last_fired_at"] == after_fire["last_fired_at"]
)

events_after_silent = event_store.list(alert_id=ALERT_ID)
checks["alert_events still holds exactly the one event from the firing run"] = (
    len(events_after_silent) == 1
)

silent_provenance = cp_query(
    "SELECT run_id FROM egpt_answer_provenance WHERE run_id = :run_id",
    run_id=silent_run_id,
)
checks["the silent run still wrote its own provenance record"] = bool(silent_provenance)
print(
    "  -> a quiet week is visibly quiet: last_evaluated_at moved, the run's own content "
    "names the alert as checked-and-not-fired, and a provenance row proves the pass ran, "
    "with no new alert_events row and no new delivery."
)
print()

# --- 4. The same two cases again, through evaluate_alert_condition/write_alert directly ---------
print(
    "4. The same two cases through evaluate_alert_condition/write_alert directly, "
    "read_gold's own output as previous_step_content"
)
print("-" * 78)
read_output = asyncio.run(
    read_gold(
        StepInput(
            additional_data={
                "kpi_identifiers": [REACHABLE_CONTACTS.identifier],
                "as_of": TODAY.isoformat(),
            }
        )
    )
)

fires_condition = asyncio.run(
    evaluate_alert_condition(
        StepInput(
            additional_data={
                "kpi_identifier": REACHABLE_CONTACTS.identifier,
                "operator": "gt",
                "threshold": fire_threshold,
            },
            previous_step_content=read_output.content,
        )
    )
)
print(
    f"  standalone evaluate_alert_condition (threshold below): {fires_condition.content}"
)
checks["standalone evaluate_alert_condition fires and does not stop the chain"] = (
    fires_condition.content["fired"] is True and fires_condition.stop is False
)

standalone_write = asyncio.run(
    write_alert(
        StepInput(
            additional_data={
                "alert_id": "alr_r12_standalone",
                "alert_name": "Standalone evaluate_alert_condition/write_alert",
                "kpi_name": REACHABLE_CONTACTS.name,
                "severity": "info",
                "run_id": "r12-cookbook-standalone",
            },
            previous_step_content=fires_condition.content,
        )
    )
)
print(f"  standalone write_alert wrote: {standalone_write.content['id']}")
checks["standalone write_alert wrote a real alert_events row"] = (
    event_store.get(standalone_write.content["id"]) is not None
)
with control_plane_engine.begin() as connection:
    connection.execute(
        text("DELETE FROM alert_events WHERE id = :id"),
        {"id": standalone_write.content["id"]},
    )

silent_condition = asyncio.run(
    evaluate_alert_condition(
        StepInput(
            additional_data={
                "kpi_identifier": REACHABLE_CONTACTS.identifier,
                "operator": "gt",
                "threshold": silent_threshold,
            },
            previous_step_content=read_output.content,
        )
    )
)
print(
    f"  standalone evaluate_alert_condition (threshold above): {silent_condition.content}"
)
checks["standalone evaluate_alert_condition does not fire and stops the chain"] = (
    silent_condition.content["fired"] is False and silent_condition.stop is True
)

silent_write = asyncio.run(
    write_alert(
        StepInput(
            additional_data={"alert_id": "alr_r12_standalone", "severity": "info"},
            previous_step_content=silent_condition.content,
        )
    )
)
print(f"  standalone write_alert, not reached: {silent_write.content}")
checks["write_alert is a documented no-op when the condition did not fire"] = (
    silent_write.content.get("skipped") == "condition did not fire"
)
print()

# --- 4b. scope.grain_column disambiguates a KPI whose rows carry two dimension columns ---------
print(
    "4b. scope.grain_column picks the named column, not a coincidental match on another "
    "(round 2 review, PR #210)"
)
print("-" * 78)
ambiguous_content = {
    "kpis": {
        "r12__two_dimension_kpi": {
            "rows": [
                {
                    "as_of": TODAY.isoformat(),
                    "value": 42.0,
                    "computed_at": f"{TODAY.isoformat()}T00:00:00Z",
                    "source_snapshot": "s1",
                    "run_id": "seed",
                    "region": "5",
                    "channel": "9",
                },
                {
                    "as_of": TODAY.isoformat(),
                    "value": 99.0,
                    "computed_at": f"{TODAY.isoformat()}T00:00:00Z",
                    "source_snapshot": "s1",
                    "run_id": "seed",
                    "region": "9",
                    "channel": "5",
                },
            ]
        }
    },
    "missing": [],
}


def _condition_for(scope):
    return asyncio.run(
        evaluate_alert_condition(
            StepInput(
                additional_data={
                    "kpi_identifier": "r12__two_dimension_kpi",
                    "operator": "gt",
                    "threshold": 0,
                    "scope": scope,
                },
                previous_step_content=ambiguous_content,
            )
        )
    )


no_column = _condition_for({"grain_value": "5"})
print(f"  no grain_column, both region and channel could mean '5': {no_column.content}")
checks["no grain_column: an ambiguous two-column match refuses rather than guesses"] = (
    no_column.content["value"] is None and "no gold row" in no_column.content["reason"]
)

by_region = _condition_for({"grain_value": "5", "grain_column": "region"})
print(f"  grain_column=region: {by_region.content}")
checks["grain_column=region picks the row where region=5 (value 42), not channel=5"] = (
    by_region.content["value"] == 42.0
)

by_channel = _condition_for({"grain_value": "5", "grain_column": "channel"})
print(f"  grain_column=channel: {by_channel.content}")
checks[
    "grain_column=channel picks the row where channel=5 (value 99), not region=5"
] = by_channel.content["value"] == 99.0
print()

# --- 4c. eq tolerates float()-cast noise from a NUMERIC column ---------------------------------
print(
    "4c. eq compares as Decimal with a small tolerance, so a realistic continuous KPI's "
    "eq alert actually fires (round 2 review, PR #210)"
)
print("-" * 78)
float_noise = condition_met("eq", 0.3, 0.1 + 0.2)
print(f"  condition_met('eq', 0.3, 0.1 + 0.2) -> {float_noise}")
checks["eq fires when two float casts represent the same decimal (0.1+0.2 == 0.3)"] = (
    float_noise.met is True
)
real_difference = condition_met("eq", 0.9, 0.87)
print(f"  condition_met('eq', 0.9, 0.87) -> {real_difference}")
checks["eq still refuses a real difference, not just float noise"] = (
    real_difference.met is False
)
print()

# --- 5. A malformed recipient never aborts the batch --------------------------------------------
print(
    "5. A malformed recipient (missing channel) on one definition never stops the "
    "definition after it from evaluating and firing (round 2 review, PR #210)"
)
print("-" * 78)
BAD_RECIPIENT_ID = "alr_r12_bad_recipient"
GOOD_RECIPIENT_ID = "alr_r12_good_recipient"
definition_store.create(
    id=BAD_RECIPIENT_ID,
    kpi=REACHABLE_CONTACTS,
    name="Bad recipient (R12 cookbook)",
    kpi_identifier=REACHABLE_CONTACTS.identifier,
    operator="gt",
    threshold=fire_threshold,
    severity="warning",
    recipients=[{"address": "no-channel-here"}],  # missing "channel" -- malformed
)
definition_store.create(
    id=GOOD_RECIPIENT_ID,
    kpi=REACHABLE_CONTACTS,
    name="Good recipient after a bad one (R12 cookbook)",
    kpi_identifier=REACHABLE_CONTACTS.identifier,
    operator="gt",
    threshold=fire_threshold,
    severity="warning",
    recipients=[{"channel": "in_app", "address": ""}],
)

isolation_run_id = "r12-cookbook-isolation"
isolation_output = run_alert_evaluation(
    isolation_run_id, alert_ids=[BAD_RECIPIENT_ID, GOOD_RECIPIENT_ID]
)
isolation_evaluate = evaluate_step(isolation_output, "evaluate-alerts")
print(f"  run {isolation_output.run_id}: {isolation_output.status.value}")
print(f"  evaluate_alerts content: {isolation_evaluate.content}")
checks["the run completes even though one definition has a malformed recipient"] = (
    isolation_output.status.value == "COMPLETED"
)
isolation_fired = {
    e["alert_id"] for e in isolation_evaluate.content["evaluated"] if e["fired"]
}
checks["both the bad-recipient and good-recipient definitions fired"] = (
    isolation_fired
    == {
        BAD_RECIPIENT_ID,
        GOOD_RECIPIENT_ID,
    }
)
isolation_deliveries = cp_query(
    "SELECT channel FROM retail_deliveries WHERE run_id = :run_id",
    run_id=isolation_run_id,
)
print(
    f"  retail_deliveries for this run: {[d['channel'] for d in isolation_deliveries]}"
)
checks["only the good definition's recipient was actually delivered to"] = (
    len(isolation_deliveries) == 1 and isolation_deliveries[0]["channel"] == "in_app"
)
bad_after = definition_store.get(BAD_RECIPIENT_ID)
checks["the bad-recipient definition's last_evaluated_at still advanced"] = (
    bad_after["last_evaluated_at"] is not None
)
print()

# --- Summary -------------------------------------------------------------------------------
print("=" * 78)
print("Checks")
print("-" * 78)
failed = 0
for description, passed in checks.items():
    status = "PASS" if passed else "FAIL"
    if not passed:
        failed += 1
    print(f"  [{status}] {description}")
print("-" * 78)
print(f"{len(checks) - failed}/{len(checks)} checks passed")

# --- Cleanup ---------------------------------------------------------------------------------
with control_plane.cursor() as cursor:
    cursor.execute("DELETE FROM alert_events WHERE alert_id LIKE 'alr_r12%'")
    cursor.execute("DELETE FROM alert_definitions WHERE id LIKE 'alr_r12%'")
    cursor.execute("DELETE FROM semantic.kpi_definition WHERE identifier LIKE 'r12__%'")
    cursor.execute(
        "DELETE FROM egpt_answer_provenance WHERE run_id IN "
        "('r12-cookbook-fires', 'r12-cookbook-fires-again', 'r12-cookbook-silent', "
        "'r12-cookbook-standalone', 'r12-cookbook-isolation')"
    )
    cursor.execute(
        "DELETE FROM retail_deliveries WHERE run_id IN "
        "('r12-cookbook-fires', 'r12-cookbook-fires-again', 'r12-cookbook-silent', "
        "'r12-cookbook-standalone', 'r12-cookbook-isolation')"
    )
control_plane.commit()
control_plane.close()
control_plane_engine.dispose()
shutil.rmtree(WORK_ROOT, ignore_errors=True)

if failed:
    sys.exit(1)
