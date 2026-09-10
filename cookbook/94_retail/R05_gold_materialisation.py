"""R5 -- gold materialisation and the nightly schedule (D6, D7), against a real warehouse.

The package's Verify line, end to end: trigger the schedule by hand; query gold for one KPI
and compute the same number directly in SQL; then break one formula deliberately and let the
run proceed. Plus the shape nobody wrote down but everybody will read: a blocked night.

Its Evidence line, asserted: the two numbers match and every gold row carries its stamp; the
broken KPI fails alone while the rest materialise, and the failure is readable from
`gold.kpi_run` and the step's own content without opening a log.

The registry corpus is K11's six definitions (copied from K11_kpi_registry_and_feasibility.py
-- that script runs at import, so it cannot be imported for its list). They matter because
three of them reference columns silver does not have: they pass table-level feasibility and
fail at compile, which is exactly the "broken formula" the Verify line asks for, three times
over, before this script breaks a fourth on purpose.

Needs the compose stack's Postgres (control plane migrated, egpt_warehouse) and Neo4j:

    docker compose -f deploy/compose.yaml --profile full up -d postgres neo4j
    EGPT_RETAIL_DB_URL=postgresql+psycopg://ai:ai@localhost:5532/ai \\
      .venv/bin/python -m egpt_retail.entrypoints migrate
    EGPT_RETAIL_WAREHOUSE_DB_URL=postgresql+psycopg://ai:ai@localhost:5532/egpt_warehouse \\
      .venv/bin/python -m egpt_retail.entrypoints warehouse-migrate
    .venv/bin/python cookbook/94_retail/R05_gold_materialisation.py
"""

import asyncio
import json
import logging
import atexit
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
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
os.environ.setdefault("EGPT_RETAIL_NEO4J_URI", "bolt://localhost:7687")
os.environ.setdefault("EGPT_RETAIL_NEO4J_PASSWORD", "password")
os.environ.setdefault("EGPT_RETAIL_ROLE", "pipelines")
os.environ.setdefault("EGPT_RETAIL_INTERNAL_SERVICE_TOKEN", "r05-cookbook-token")
os.environ.setdefault("EGPT_RETAIL_CUSTOMER_SLUG", "r05")

import httpx  # noqa: E402
import psycopg  # noqa: E402
from egpt.scheduler.executor import ScheduleExecutor  # noqa: E402
from egpt.scheduler.manager import ScheduleManager  # noqa: E402
from egpt.workflow.types import StepInput  # noqa: E402
from egpt_retail.app.components import (  # noqa: E402
    NIGHTLY_REFRESH_WORKFLOW_ID,
    build_workflows,
)
from egpt_retail.app.db import (  # noqa: E402
    build_control_plane_db,
    build_warehouse_engine,
    psycopg_dsn,
)
from egpt_retail.app.factory import build_agent_os  # noqa: E402
from egpt_retail.app.semantic import open_semantic_reader  # noqa: E402
from neo4j import GraphDatabase  # noqa: E402
from egpt_retail.app.settings import RetailSettings  # noqa: E402
from egpt_retail.contracts.kpi import (  # noqa: E402
    KpiDefinition,
    KpiParameter,
    ParameterKind,
)
from egpt_retail.contracts.semantic import Concept, available_tables  # noqa: E402
from egpt_retail.entrypoints.schedule_nightly import (  # noqa: E402
    NIGHTLY_SCHEDULE_NAME,
)
from egpt_retail.entrypoints.schedule_nightly import (  # noqa: E402
    main as schedule_nightly,
)
from egpt_retail.executors.gold import materialise_gold, read_gold  # noqa: E402
from egpt_retail.kpi.store import KpiRegistryStore  # noqa: E402
from egpt_retail.semantic.harvest import harvest_schema  # noqa: E402
from egpt_retail.semantic.store import SemanticGraphStore  # noqa: E402
from egpt_retail.warehouse.ddl import apply_warehouse_ddl  # noqa: E402
from sqlalchemy import text  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(message)s")
logging.getLogger("egpt_retail").setLevel(logging.WARNING)

# --- The raw drop: a working copy of C1's fixtures, as R04 does ---------------------------------
FIXTURES_ROOT = Path(
    os.environ.get("EGPT_LANDING_FIXTURES_ROOT")
    or REPO_ROOT / "product" / "egpt_retail" / "contracts" / "_fixtures" / "landing"
)
os.environ["EGPT_RETAIL_STORAGE_PROVIDER"] = "local"
WORK_ROOT = Path(tempfile.mkdtemp(prefix="r05-raw-"))
shutil.copytree(FIXTURES_ROOT / "raw", WORK_ROOT / "raw")
os.environ["EGPT_RETAIL_STORAGE_LOCAL_ROOT"] = str(WORK_ROOT)

SETTINGS = RetailSettings()


@atexit.register
def _remove_this_cookbooks_concepts() -> None:
    """Remove the K11-named concepts this cookbook writes into the shared graph.

    Registered at exit rather than run at the end, because the end is not reached when the
    cookbook fails -- and a failed run is exactly when the next person re-runs it. Leaving
    them there broke two Lane K cookbooks that read the same graph: K05 compares the real
    store against the stub and saw three concepts the stub does not have, and K10's retrieval
    ranked `K11Deal` above the tables its question was actually about.
    """
    try:
        driver = GraphDatabase.driver(
            SETTINGS.retail_neo4j_uri,
            auth=(SETTINGS.retail_neo4j_user, SETTINGS.retail_neo4j_password or ""),
        )
        try:
            with driver.session() as session:
                session.run(
                    "MATCH (c:SemanticConcept) WHERE c.name STARTS WITH 'K11' "
                    "DETACH DELETE c"
                )
        finally:
            driver.close()
    except Exception:  # noqa: BLE001 -- cleanup must never mask the real failure
        pass


WAREHOUSE = build_warehouse_engine(SETTINGS)
TODAY = datetime.now(timezone.utc).date()

SCOPE = KpiParameter("scope", ParameterKind.PREDICATE, "Concept scoping filter.")
# K11's six, verbatim. Two compile against R4's silver, three reference columns it lacks
# (crm_deal.amount, crm_deal.deal_id, crm_deal_stage_history.days_in_stage), one is blocked.
KPIS = [
    KpiDefinition(
        identifier="k11_deal__won_value",
        name="Won deal value",
        description="Total value of deals in a closed-won stage.",
        sql="SELECT sum(amount) FROM crm_deal WHERE {scope}",
        grain="overall",
        business_area="sales",
        source_tables=frozenset({"crm_deal"}),
        parameters=(SCOPE,),
        concept="K11Deal",
    ),
    KpiDefinition(
        identifier="k11_contact__reachable",
        name="Reachable contacts",
        description="Contacts holding an email address.",
        sql="SELECT count(*) FROM crm_contact WHERE email IS NOT NULL",
        grain="overall",
        business_area="customer",
        source_tables=frozenset({"crm_contact"}),
        concept="K11Contact",
    ),
    KpiDefinition(
        identifier="k11_deal__stage_velocity",
        name="Stage velocity",
        description="Average days a deal spends in each stage.",
        sql="SELECT avg(h.days_in_stage) FROM crm_deal d JOIN crm_deal_stage_history h ON h.deal_id = d.deal_id",
        grain="overall",
        business_area="sales",
        source_tables=frozenset({"crm_deal", "crm_deal_stage_history"}),
        concept="K11Deal",
    ),
    KpiDefinition(
        identifier="k11_session__replay_coverage",
        name="Replay coverage",
        description="Share of analytics sessions that have a replay recorded.",
        sql="SELECT count(*) FROM analytics_event e JOIN session_replay_session s ON s.session_id = e.session_id",
        grain="overall",
        business_area="marketing",
        source_tables=frozenset({"analytics_event", "session_replay_session"}),
        concept="K11Session",
    ),
    KpiDefinition(
        identifier="k11_deal__pipeline_by_contact",
        name="Pipeline by contact",
        description="Open pipeline attributed to each contact.",
        sql="SELECT sum(d.amount) FROM crm_deal d JOIN crm_contact c ON c.object_id = d.object_id",
        grain="customer",
        business_area="sales",
        source_tables=frozenset({"crm_deal", "crm_contact"}),
        concept="K11Deal",
    ),
    KpiDefinition(
        identifier="k11_order__fulfilment_rate",
        name="Fulfilment rate",
        description="Orders shipped complete, over orders placed.",
        sql="SELECT count(*) FROM orders o JOIN order_items i ON i.order_id = o.order_id",
        grain="overall",
        business_area="sales",
        source_tables=frozenset({"orders", "order_items"}),
        concept="K11Order",
    ),
]
EXPECT_MATERIALISED = {"k11_contact__reachable", "k11_session__replay_coverage"}
EXPECT_FAILED = {
    "k11_deal__won_value",
    "k11_deal__stage_velocity",
    "k11_deal__pipeline_by_contact",
}
EXPECT_SKIPPED = {"k11_order__fulfilment_rate"}


def query(sql, **params):
    with WAREHOUSE.begin() as connection:
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


def step_names(output):
    return [step.step_name for step in output.step_results]


def gold_step(output):
    return next(s for s in output.step_results if s.step_name == "materialise-gold")


def kpi_runs(run_id):
    return {
        row["kpi_identifier"]: row
        for row in query(
            "SELECT kpi_identifier, status, error, reason, rows_written, grain_keys "
            "FROM gold.kpi_run WHERE run_id = :run_id",
            run_id=run_id,
        )
    }


def publish(extra_name, rows):
    path = WORK_ROOT / f"raw/crm/dt=2026-09-01/{extra_name}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


checks = {}

print("R5 -- gold materialisation and the nightly schedule (D6, D7)")
print("=" * 78)
print(f"warehouse:     {SETTINGS.retail_warehouse_db_url}")
print(f"control plane: {SETTINGS.retail_db_url}")
print(f"semantic:      {SETTINGS.retail_neo4j_uri}")
print()

# --- 0. Seed: schema, semantic layer, registry ------------------------------------------------
print(
    "0. Seed -- warehouse DDL, harvest silver into the graph, K11's six in the registry"
)
print("-" * 78)
applied = apply_warehouse_ddl(WAREHOUSE)
print(
    f"  {len(applied)} DDL scripts applied (gold: {[s for s in applied if 'gold' in s]})"
)
reset_warehouse()

raw = psycopg.connect(psycopg_dsn(str(SETTINGS.retail_warehouse_db_url)))
raw.autocommit = True
with open_semantic_reader(SETTINGS) as reader:
    graph = reader
    assert isinstance(graph, SemanticGraphStore)
    graph.ensure_indexes()
    graph.write_harvest(
        harvest_schema(raw, "silver", source="egpt_warehouse", sample=False),
        snapshot_version="r05.1",
    )
    for concept, area in (
        ("K11Deal", "sales"),
        ("K11Contact", "customer"),
        ("K11Session", "marketing"),
    ):
        graph.write_concept(
            Concept(
                name=concept, description=f"{concept}s, for R05.", business_area=area
            )
        )
    tables = sorted(available_tables(graph))
    snapshot_version = graph.snapshot().version
raw.close()
print(f"  semantic snapshot {snapshot_version}: {len(tables)} silver tables exposed")

control_plane = psycopg.connect(psycopg_dsn(SETTINGS.retail_db_url))
store = KpiRegistryStore(control_plane)
store.ensure_schema()
with control_plane.cursor() as cursor:
    cursor.execute("DELETE FROM semantic.kpi_definition WHERE identifier LIKE 'k11_%'")
control_plane.commit()
print(f"  registry: {store.save_many(KPIS)} definitions saved")
print()

# --- 1. The nightly chain, by hand ------------------------------------------------------------
print("1. nightly-refresh, driven by hand: R4's six steps, then materialise-gold")
print("-" * 78)
first = run_nightly()
names = step_names(first)
print(f"  run {first.run_id}: {first.status.value}; steps: {names}")
gold = gold_step(first).content
runs = kpi_runs(gold["run_id"])
for identifier, row in sorted(runs.items()):
    detail = row["error"] or row["reason"] or ""
    print(f"    {row['status']:<12} {identifier:<32} {detail[:80]}")
checks["run completed with the gold step"] = (
    first.status.value == "COMPLETED" and names[-1] == "materialise-gold"
)
checks["two materialised, three failed alone, one skipped"] = (
    set(gold["materialised"]) == EXPECT_MATERIALISED
    and set(gold["failed"]) == EXPECT_FAILED
    and set(gold["skipped"]) == EXPECT_SKIPPED
)
checks["each failure carries Postgres's error verbatim"] = all(
    "does not exist" in (runs[i]["error"] or "") for i in EXPECT_FAILED
)
checks["the skip names the missing tables"] = "order_items" in (
    runs["k11_order__fulfilment_rate"]["reason"] or ""
)
for name in list(checks)[-4:]:
    print("   ", "OK " if checks[name] else "FAIL", name)
print()

# --- 2. Query gold for one KPI and compute the same number directly in SQL --------------------
print("2. Gold versus SQL, and the stamp on every row")
print("-" * 78)
gold_value = query(
    "SELECT value, computed_at, source_snapshot, run_id FROM gold.kpi_k11_contact__reachable "
    "WHERE as_of = :as_of",
    as_of=TODAY,
)
direct = query("SELECT count(*) AS n FROM silver.crm_contact WHERE email IS NOT NULL")[
    0
]["n"]
print(
    f"  gold.kpi_k11_contact__reachable[{TODAY}] = {gold_value[0]['value'] if gold_value else None}"
)
print(f"  SELECT count(*) FROM silver.crm_contact WHERE email IS NOT NULL = {direct}")
stamps = query(
    "SELECT count(*) AS rows, "
    "count(*) FILTER (WHERE source_snapshot IS NULL OR computed_at IS NULL OR run_id IS NULL "
    "OR formula_sha256 IS NULL OR cardinality(source_tables) = 0) AS unstamped "
    "FROM gold.kpi_value"
)[0]
print(f"  gold.kpi_value rows: {stamps['rows']}, unstamped: {stamps['unstamped']}")
checks["the two numbers match"] = bool(gold_value) and int(
    gold_value[0]["value"]
) == int(direct)
checks["every gold row is stamped"] = stamps["rows"] >= 2 and stamps["unstamped"] == 0
checks["the stamp names this snapshot"] = (
    bool(gold_value) and gold_value[0]["source_snapshot"] == snapshot_version
)
for name in list(checks)[-3:]:
    print("   ", "OK " if checks[name] else "FAIL", name)

replay = query(
    "SELECT value FROM gold.kpi_k11_session__replay_coverage WHERE as_of = :as_of",
    as_of=TODAY,
)
replay_direct = query(
    "SELECT count(*) AS n FROM silver.analytics_event e "
    "JOIN silver.session_replay_session s ON s.session_id = e.session_id"
)[0]["n"]
checks["the second KPI matches too"] = bool(replay) and int(replay[0]["value"]) == int(
    replay_direct
)
print(
    "   ",
    "OK " if checks["the second KPI matches too"] else "FAIL",
    f"replay coverage {replay[0]['value'] if replay else None} == {replay_direct}",
)
print()

# --- 3. Break one good formula deliberately and let the run proceed --------------------------
print(
    "3. Break k11_contact__reachable (a missing column) and re-run the gold step alone"
)
print("-" * 78)
broken = KpiDefinition(
    identifier="k11_contact__reachable",
    name="Reachable contacts",
    description="Broken on purpose: phone_number is not a silver column.",
    sql="SELECT count(*) FROM crm_contact WHERE phone_number IS NOT NULL",
    grain="overall",
    business_area="customer",
    source_tables=frozenset({"crm_contact"}),
    concept="K11Contact",
)
store.save(broken)
before = query(
    "SELECT max(computed_at) AS t FROM gold.kpi_value WHERE kpi_identifier = 'k11_contact__reachable'"
)[0]["t"]
step_input = StepInput(
    additional_data={
        "trigger": "cookbook",
        "as_of": TODAY.isoformat(),
        "run_id": "r05-broken",
    }
)
alone = asyncio.run(materialise_gold(step_input)).content
runs3 = kpi_runs("r05-broken")
latest = {
    row["kpi_identifier"]: row
    for row in query(
        "SELECT kpi_identifier, status, error, run_id FROM gold.kpi_latest_run"
    )
}
print(
    f"  step content: materialised={alone['materialised']} failed={sorted(alone['failed'])}"
)
print(
    f"  gold.kpi_latest_run[k11_contact__reachable] = {latest['k11_contact__reachable']['status']}: "
    f"{(latest['k11_contact__reachable']['error'] or '')[:70]}"
)
after = query(
    "SELECT max(computed_at) AS t FROM gold.kpi_value WHERE kpi_identifier = 'k11_contact__reachable'"
)[0]["t"]
checks["the broken KPI fails alone, the good one still materialises"] = (
    "k11_contact__reachable" in alone["failed"]
    and alone["materialised"] == ["k11_session__replay_coverage"]
)
checks["the failure is in run history with the database's own words"] = (
    latest["k11_contact__reachable"]["status"] == "failed"
    and "phone_number" in (latest["k11_contact__reachable"]["error"] or "")
    and latest["k11_contact__reachable"]["run_id"] == "r05-broken"
)
checks["the last good value survives a failed run"] = (
    before == after and before is not None
)
checks["a same-night re-run overwrote rather than duplicated"] = (
    query(
        "SELECT count(*) AS n FROM gold.kpi_value WHERE kpi_identifier = 'k11_session__replay_coverage' AND as_of = :as_of",
        as_of=TODAY,
    )[0]["n"]
    == 1
)
for name in list(checks)[-4:]:
    print("   ", "OK " if checks[name] else "FAIL", name)
prov = step_input.additional_data.get("prov", {})
print(
    f"  R7 bag: {len(prov.get('datasets_read', []))} datasets, {len(prov.get('sql_executed', []))} statements, {len(prov.get('freshness', []))} freshness stamps"
)
checks["the executor populated R7's provenance bag"] = bool(
    prov.get("sql_executed")
) and bool(prov.get("freshness"))
store.save(next(k for k in KPIS if k.identifier == "k11_contact__reachable"))  # restore
print()

# --- 4. read_gold: the contract's own reader ----------------------------------------------------
print("4. read_gold -- through the guard, by the per-KPI view, latest as_of by default")
print("-" * 78)
read_input = StepInput(
    additional_data={
        "kpi_identifiers": [
            "k11_session__replay_coverage",
            "k11_order__fulfilment_rate",
        ]
    }
)
read = asyncio.run(read_gold(read_input)).content
got = read["kpis"].get("k11_session__replay_coverage", {})
print(
    f"  k11_session__replay_coverage: as_of={got.get('as_of')} rows={len(got.get('rows', []))} value={got.get('rows', [{}])[0].get('value')}"
)
print(f"  missing: {read['missing']}")
checks["read_gold returns the materialised KPI and names the missing one"] = (
    got.get("rows")
    and float(got["rows"][0]["value"]) == float(replay_direct)
    and any(
        m["kpi_identifier"] == "k11_order__fulfilment_rate" for m in read["missing"]
    )
)
print("   ", "OK " if checks[list(checks)[-1]] else "FAIL", list(checks)[-1])
print()

# --- 5. A blocked night ---------------------------------------------------------------------
print("5. A blocked night: a nulled key column stops the chain before gold")
print("-" * 78)
gold_before = query("SELECT count(*) AS n, max(computed_at) AS t FROM gold.kpi_value")[
    0
]
publish(
    "crm-20260901T235959Z.ndjson",
    [
        {
            "record_type": "contact",
            "object_id": None,
            "last_modified_at": "2026-09-01T10:00:00Z",
            "email": "x@example.com",
            "brand": "acme",
        },
    ],
)
blocked = run_nightly()
verdict = query(
    "SELECT verdict, reason FROM quality.load_run ORDER BY started_at DESC LIMIT 1"
)[0]
gold_after = query("SELECT count(*) AS n, max(computed_at) AS t FROM gold.kpi_value")[0]
print(f"  run {blocked.run_id}: {blocked.status.value}; steps: {step_names(blocked)}")
print(f"  quality.load_run: {verdict['verdict']} -- {(verdict['reason'] or '')[:90]}")
checks["a blocked night is a COMPLETED run with no gold step"] = (
    blocked.status.value == "COMPLETED"
    and "materialise-gold" not in step_names(blocked)
)
checks["the verdict is in quality.load_run"] = verdict["verdict"] == "blocked"
checks["gold is untouched on a blocked night"] = gold_before == gold_after
for name in list(checks)[-3:]:
    print("   ", "OK " if checks[name] else "FAIL", name)
refused = asyncio.run(
    materialise_gold(
        StepInput(additional_data={"trigger": "cookbook", "run_id": "r05-after-block"})
    )
).content
checks["a hand run of the gold step after a blocked load refuses"] = (
    "refused" in refused and "blocked" in refused["refused"]
)
print(
    "   ",
    "OK " if checks[list(checks)[-1]] else "FAIL",
    f"{list(checks)[-1]}: {refused.get('refused', '')[:70]}",
)
print()

# --- 6. The schedule, triggered by hand through the scheduler's own executor ---------------------
print(
    "6. schedule-nightly, then a trigger through ScheduleExecutor against the in-process app"
)
print("-" * 78)
os.remove(WORK_ROOT / "raw/crm/dt=2026-09-01/crm-20260901T235959Z.ndjson")
# A clean warehouse first. R4's ungated-rows rule keeps a blocked night visible: the bad row
# section 5 landed entered silver ungated, and every later load is blocked until it is dealt
# with -- which is R4 working, not a bug, and not what this section is about.
reset_warehouse()
schedule_nightly([])
db = build_control_plane_db(SETTINGS)
manager = ScheduleManager(db)
schedule = (
    manager.get_by_name(NIGHTLY_SCHEDULE_NAME)
    if hasattr(manager, "get_by_name")
    else None
)
if schedule is None:
    schedule = next(s for s in manager.list() if s.name == NIGHTLY_SCHEDULE_NAME)
print(
    f"  schedule {schedule.name}: cron={schedule.cron_expr!r} endpoint={schedule.endpoint} payload={json.dumps(schedule.payload)}"
)

app = build_agent_os(SETTINGS).get_app()
executor = ScheduleExecutor(
    base_url="http://testserver",
    internal_service_token=SETTINGS.retail_internal_service_token,
)


async def trigger():
    executor._client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        timeout=300,
    )
    try:
        return await executor.execute(schedule, db)
    finally:
        await executor.close()


run = asyncio.run(trigger())
print(
    f"  ScheduleRun: status={run.get('status')} run_id={run.get('run_id')} error={run.get('error')}"
)
# The ScheduleRun's `output` is the workflow's final step output -- materialise-gold's content
# on a clean night, decide-load-verdict's on a blocked one. That is the run record the
# delivery plan means by "visible in run history".
final = (run.get("output") or {}).get("content") or {}
scheduled_load = query(
    "SELECT trigger, verdict FROM quality.load_run ORDER BY started_at DESC LIMIT 1"
)[0]
print(
    f"  latest load: trigger={scheduled_load['trigger']!r} verdict={scheduled_load['verdict']!r}"
)
print(
    f"  final step content: materialised={final.get('materialised')} failed={sorted(final.get('failed', []))} run_id={final.get('run_id')}"
)
scheduled_runs = query(
    "SELECT status, count(*) AS n FROM gold.kpi_run WHERE run_id = :run_id GROUP BY status ORDER BY status",
    run_id=str(final.get("run_id")),
)
print(f"  gold.kpi_run for that run: {scheduled_runs}")
checks[
    "the schedule's additional_data arrived as a dict (the load ran as trigger=schedule)"
] = scheduled_load["trigger"] == "schedule" and scheduled_load["verdict"] == "passed"
checks["the triggered schedule succeeded and its run materialised gold"] = (
    run.get("status") == "success"
    and bool(run.get("run_id"))
    and set(final.get("materialised", [])) == EXPECT_MATERIALISED
    and any(r["status"] == "materialised" and r["n"] == 2 for r in scheduled_runs)
)
for name in list(checks)[-2:]:
    print("   ", "OK " if checks[name] else "FAIL", name)
manager.close()
control_plane.close()
WAREHOUSE.dispose()

print()
print("=" * 78)
for name, ok in checks.items():
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
print("Overall:", "PASS" if all(checks.values()) else "FAIL")
raise SystemExit(0 if all(checks.values()) else 1)
