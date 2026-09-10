"""R10 -- the RFQ chain end to end (D12), against a real warehouse and semantic layer.

The package's Verify line: "Run end to end on the sample data. Check every number in the
explanation against gold. Then break the friction join and run again." Its Evidence line:
"Every number traces to a gold row and none appears that is absent from the provenance
record. With the join broken, the run reports the gap rather than explaining a funnel it
could not see."

This script runs that scenario in the order the package's own risk is greatest first:

  0. Seed -- warehouse DDL, load C1's sample fixtures through R4, harvest silver, register
     the RFQ template's two KPIs (`kpi/rfq.py`).
  1. Materialise gold for a synthetic "yesterday", then hand-edit that gold so "today"'s
     real materialisation has something to have moved against -- the same technique R05 uses
     to force a broken formula, applied here to force a real mover.
  2. `read_gold` then `sample_rows`, by hand: movers checked against a direct SQL query,
     friction checked honest-unavailable (the sample data carries no `identity_key`, per
     `silver.analytics_event`'s own column comment).
  3. Populate `identity_key` on two matching rows and re-run `sample_rows`: friction becomes
     available and its counts match a direct SQL query. Then remove it again and re-run: the
     honest-unavailable state returns -- the join was never "broken" by a code path, only by
     whether the identity hash exists yet, which is exactly the point.
  4. The registered `rfq-funnel` workflow, driven with `arun` end to end. The model step runs
     for real when Azure credentials are configured; otherwise this section says so and
     exercises the rest of the chain with a hand-built explanation, matching the "Offline
     half PASS" shape R09's own cookbook already established.

Needs the compose stack's Postgres (control plane migrated, egpt_warehouse) and Neo4j:

    docker compose -f deploy/compose.yaml --profile full up -d postgres neo4j
    EGPT_RETAIL_DB_URL=postgresql+psycopg://ai:ai@localhost:5532/ai \\
      .venv/bin/python -m egpt_retail.entrypoints migrate
    EGPT_RETAIL_WAREHOUSE_DB_URL=postgresql+psycopg://ai:ai@localhost:5532/egpt_warehouse \\
      .venv/bin/python -m egpt_retail.entrypoints warehouse-migrate
    .venv/bin/python cookbook/94_retail/R10_rfq_workflow.py
"""

import asyncio
import logging
import os
import shutil
import sys
import tempfile
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
os.environ.setdefault("EGPT_RETAIL_INTERNAL_SERVICE_TOKEN", "r10-cookbook-token")
os.environ.setdefault("EGPT_RETAIL_CUSTOMER_SLUG", "r10")

import psycopg  # noqa: E402
from egpt.workflow.types import StepInput  # noqa: E402
from egpt_retail.app.components import (  # noqa: E402
    NIGHTLY_REFRESH_WORKFLOW_ID,
    RFQ_WORKFLOW_ID,
    build_agents,
    build_workflows,
)
from egpt_retail.app.db import (  # noqa: E402
    build_control_plane_db,
    build_warehouse_engine,
    psycopg_dsn,
)
from egpt_retail.app.semantic import open_semantic_reader  # noqa: E402
from egpt_retail.app.settings import RetailSettings  # noqa: E402
from egpt_retail.executors.delivery import deliver_in_app  # noqa: E402
from egpt_retail.executors.gold import read_gold  # noqa: E402
from egpt_retail.executors.provenance import record_provenance  # noqa: E402
from egpt_retail.executors.rfq import (  # noqa: E402
    check_explanation_grounding,
    sample_rows,
)
from egpt_retail.kpi.rfq import RFQ_KPI_DEFINITIONS  # noqa: E402
from egpt_retail.kpi.store import KpiRegistryStore  # noqa: E402
from egpt_retail.semantic.harvest import harvest_schema  # noqa: E402
from egpt_retail.skills.funnel_explanation import (  # noqa: E402
    BrandMovement,
    Direction,
    FunnelExplanation,
    citations_within_input,
)
from egpt_retail.warehouse.ddl import apply_warehouse_ddl  # noqa: E402
from sqlalchemy import text  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(message)s")
logging.getLogger("egpt_retail").setLevel(logging.WARNING)

# --- The raw drop: a working copy of C1's fixtures, as R04/R05 do ------------------------------
FIXTURES_ROOT = Path(
    os.environ.get("EGPT_LANDING_FIXTURES_ROOT")
    or REPO_ROOT / "product" / "egpt_retail" / "contracts" / "_fixtures" / "landing"
)
os.environ["EGPT_RETAIL_STORAGE_PROVIDER"] = "local"
WORK_ROOT = Path(tempfile.mkdtemp(prefix="r10-raw-"))
shutil.copytree(FIXTURES_ROOT / "raw", WORK_ROOT / "raw")
os.environ["EGPT_RETAIL_STORAGE_LOCAL_ROOT"] = str(WORK_ROOT)

SETTINGS = RetailSettings()
WAREHOUSE = build_warehouse_engine(SETTINGS)
TODAY = date.today()
YESTERDAY = TODAY - timedelta(days=1)


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
    """One run of nightly-refresh, driven with `arun` (its executors are async)."""
    options.setdefault("trigger", "cookbook")
    workflow = next(
        w
        for w in build_workflows(SETTINGS, build_control_plane_db(SETTINGS))
        if w.id == NIGHTLY_REFRESH_WORKFLOW_ID
    )
    return asyncio.run(workflow.arun(input="nightly", additional_data=options))


checks = {}

print("R10 -- the RFQ chain end to end (D12)")
print("=" * 78)
print(f"warehouse:     {SETTINGS.retail_warehouse_db_url}")
print(f"control plane: {SETTINGS.retail_db_url}")
print(f"semantic:      {SETTINGS.retail_neo4j_uri}")
print()

# --- 0. Seed: schema, semantic layer, the RFQ template's two KPIs -----------------------------
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
    # Silver's *tables and columns* exist from the DDL applied above even with no rows
    # loaded yet, so the harvest that gives materialise_gold a snapshot to stamp gold with
    # can run now, the same order R05's cookbook uses.
    reader.write_harvest(
        harvest_schema(raw, "silver", source="egpt_warehouse", sample=False),
        snapshot_version="r10.1",
    )
    snapshot_version = reader.snapshot().version
raw.close()
print(f"  semantic snapshot {snapshot_version} harvested")

control_plane = psycopg.connect(psycopg_dsn(SETTINGS.retail_db_url))
store = KpiRegistryStore(control_plane)
store.ensure_schema()
with control_plane.cursor() as cursor:
    cursor.execute("DELETE FROM semantic.kpi_definition WHERE identifier LIKE 'rfq__%'")
    # This cookbook's own fixed run ids, cleared so a re-run does not collide with the
    # previous one's row in egpt_answer_provenance (its primary key is run_id alone) --
    # the same "a cookbook must remove everything it writes to a store another run reads"
    # rule R05's TEST_LOG names, applied to a rerun of the same script rather than a sibling.
    cursor.execute(
        "DELETE FROM egpt_answer_provenance WHERE run_id IN "
        "('r10-cookbook-e2e', 'r10-cookbook-offline')"
    )
    cursor.execute(
        "DELETE FROM retail_deliveries WHERE run_id IN "
        "('r10-cookbook-e2e', 'r10-cookbook-offline')"
    )
control_plane.commit()
print(f"  registry: {store.save_many(RFQ_KPI_DEFINITIONS)} definitions saved")
print()

# --- 1. Materialise "yesterday", hand-edit it, then materialise "today" for real ---------------
print(
    "1. nightly-refresh for yesterday, hand-edit that gold, then nightly-refresh for today"
)
print("-" * 78)
first = run_nightly(as_of=YESTERDAY.isoformat())
print(f"  yesterday run {first.run_id}: {first.status.value}")

yesterday_rows = query(
    "SELECT dimension->>'brand' AS brand, value FROM gold.kpi_value "
    "WHERE kpi_identifier = 'rfq__quote_requests_by_brand' AND as_of = :as_of",
    as_of=YESTERDAY,
)
print(f"  yesterday's real quote_requests_by_brand: {yesterday_rows}")
checks["yesterday materialised at least one brand"] = bool(yesterday_rows)

# Hand-edit yesterday's gold so today's real materialisation has something to have moved
# against -- the fixture data does not change day to day, so without this the movers
# comparison would (correctly) report every brand as unchanged, which proves nothing about
# the arithmetic. R05's section 3 uses the same technique (a deliberately altered value) to
# force a scenario the unmodified sample data cannot reach on its own.
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
    print(
        f"  hand-edited yesterday's {edited_brand!r} from {edited_value} to "
        f"{edited_value * 3 + 5} to force a real mover"
    )

second = run_nightly(as_of=TODAY.isoformat())
print(f"  today run {second.run_id}: {second.status.value}")
checks["today's real run materialised the RFQ KPIs"] = (
    second.status.value == "COMPLETED"
)
print()

# --- 1b. quote_to_deal_rate: gold must aggregate distinct deals, not joined stage-history rows -
print("1b. quote_to_deal_rate -- gold matches a distinct-deal direct SQL computation")
print("-" * 78)
gold_deal_rate = query(
    "SELECT dimension->>'brand' AS brand, value FROM gold.kpi_value "
    "WHERE kpi_identifier = 'rfq__quote_to_deal_rate' AND as_of = :as_of",
    as_of=TODAY,
)
direct_deal_rate = query(
    "SELECT d.brand AS brand, "
    "(count(DISTINCT d.object_id) FILTER (WHERE h.deal_id IS NOT NULL))::numeric "
    "/ NULLIF(count(DISTINCT d.object_id), 0) AS value "
    "FROM silver.crm_deal d LEFT JOIN silver.crm_deal_stage_history h "
    "ON h.deal_id = d.object_id "
    "WHERE d.quote_request_source_url IS NOT NULL GROUP BY d.brand"
)
direct_deal_rate_by_brand = {r["brand"]: float(r["value"]) for r in direct_deal_rate}
print(f"  gold: {gold_deal_rate}")
print(f"  direct SQL (distinct deals): {direct_deal_rate_by_brand}")
checks["quote_to_deal_rate matches a distinct-deal direct SQL computation"] = bool(
    gold_deal_rate
) and all(
    abs(float(r["value"]) - direct_deal_rate_by_brand[r["brand"]]) < 1e-9
    for r in gold_deal_rate
)

# --- 2. read_gold then sample_rows: movers against direct SQL, friction honestly unavailable ---
print("2. read_gold then sample_rows -- movers checked against direct SQL")
print("-" * 78)
identifiers = [kpi.identifier for kpi in RFQ_KPI_DEFINITIONS]
read_input = StepInput(
    additional_data={"kpi_identifiers": identifiers, "as_of": TODAY.isoformat()}
)
read_output = asyncio.run(read_gold(read_input))
print(
    f"  read_gold: kpis={list(read_output.content['kpis'])} missing={read_output.content['missing']}"
)
checks["read_gold returned both RFQ KPIs"] = set(read_output.content["kpis"]) == set(
    identifiers
)

sample_input = StepInput(
    additional_data={"kpi_identifiers": identifiers},
    previous_step_content=read_output.content,
)
sample_output = asyncio.run(sample_rows(sample_input))
movers = sample_output.content["movers"]
friction = sample_output.content["friction"]
print(f"  movers: {movers}")
print(f"  friction: {friction}")
print(
    f"  kpi_identifiers: {sample_output.content['kpi_identifiers']} "
    f"snapshot_version: {sample_output.content['snapshot_version']!r}"
)
checks["sample_rows forwards read_gold's kpi_identifiers and snapshot_version"] = (
    sample_output.content["kpi_identifiers"] == sorted(identifiers)
    and bool(sample_output.content["snapshot_version"])
)
checks["sample_rows' movers are bounded to mover_sample_size, not unbounded"] = all(
    len(rows) <= 5 for rows in movers.values()
)

direct_prior = query(
    "SELECT dimension->>'brand' AS brand, value FROM gold.kpi_value "
    "WHERE kpi_identifier = 'rfq__quote_requests_by_brand' AND as_of = :as_of",
    as_of=YESTERDAY,
)
direct_current = query(
    "SELECT dimension->>'brand' AS brand, value FROM gold.kpi_value "
    "WHERE kpi_identifier = 'rfq__quote_requests_by_brand' AND as_of = :as_of",
    as_of=TODAY,
)
direct_prior_by_brand = {r["brand"]: float(r["value"]) for r in direct_prior}
top_mover = movers["rfq__quote_requests_by_brand"][0]
expected_delta = float(
    next(r["value"] for r in direct_current if r["brand"] == top_mover["brand"])
) - direct_prior_by_brand.get(top_mover["brand"], 0.0)
print(
    f"  top mover: {top_mover['brand']} delta={top_mover['delta']} "
    f"(direct SQL: {expected_delta})"
)
checks["the top mover's delta matches a direct SQL computation"] = (
    top_mover["delta"] is not None
    and abs(float(top_mover["delta"]) - expected_delta) < 1e-6
)
checks[
    "friction is honestly unavailable -- the sample data carries no identity_key"
] = friction["available"] is False and "identity_key" in friction["reason"]
print()

# --- 3. Populate identity_key on two matching rows: friction becomes available, then reverts ---
print(
    "3. Populate identity_key on two rows: friction becomes available, then remove it again"
)
print("-" * 78)
matching = query(
    "SELECT e.user_id, e.session_id, e.event_name FROM silver.analytics_event e LIMIT 1"
)
replay_session = query("SELECT session_id FROM silver.session_replay_session LIMIT 1")
if matching and replay_session:
    shared_identity = "cookbook-shared-identity"
    execute(
        "UPDATE silver.analytics_event SET identity_key = :k "
        "WHERE user_id = :u AND session_id = :s AND event_name = :e",
        k=shared_identity,
        u=matching[0]["user_id"],
        s=matching[0]["session_id"],
        e=matching[0]["event_name"],
    )
    execute(
        "UPDATE silver.session_replay_session SET identity_key = :k WHERE session_id = :s",
        k=shared_identity,
        s=replay_session[0]["session_id"],
    )
    populated = asyncio.run(
        sample_rows(
            StepInput(
                additional_data={"kpi_identifiers": identifiers},
                previous_step_content=read_output.content,
            )
        )
    ).content["friction"]
    print(f"  with identity_key populated: {populated}")
    direct_friction = query(
        "SELECT count(*) AS n FROM silver.analytics_event e "
        "JOIN silver.session_replay_session s ON s.identity_key = e.identity_key "
        "WHERE e.identity_key IS NOT NULL AND s.identity_key IS NOT NULL"
    )[0]["n"]
    checks["friction becomes available once a shared identity_key exists"] = (
        populated["available"] is True
        and sum(r["sessions"] for r in populated["rows"]) == direct_friction
    )

    # A second analytics_event row sharing the identity *and* the event name must not
    # re-add the identity's already-aggregated click counts a second time -- the fan-out
    # FRICTION_QUERY's own review comment named (distinct from the sessions-per-identity
    # fan-out section 3 already covers above).
    event_name = matching[0]["event_name"]
    dup_row = query(
        "SELECT user_id, session_id, event_name FROM silver.analytics_event "
        "WHERE NOT (user_id = :u AND session_id = :s AND event_name = :e) LIMIT 1",
        u=matching[0]["user_id"],
        s=matching[0]["session_id"],
        e=event_name,
    )
    if dup_row:
        original_event_name = dup_row[0]["event_name"]
        execute(
            "UPDATE silver.analytics_event SET identity_key = :k, event_name = :e "
            "WHERE user_id = :u AND session_id = :s",
            k=shared_identity,
            e=event_name,
            u=dup_row[0]["user_id"],
            s=dup_row[0]["session_id"],
        )
        duplicated = asyncio.run(
            sample_rows(
                StepInput(
                    additional_data={"kpi_identifiers": identifiers},
                    previous_step_content=read_output.content,
                )
            )
        ).content["friction"]
        print(
            f"  with a duplicate {event_name!r} event on the same identity: {duplicated}"
        )
        dup_event_row = next(
            (r for r in duplicated["rows"] if r["event_name"] == event_name), None
        )
        populated_event_row = next(
            (r for r in populated["rows"] if r["event_name"] == event_name), None
        )
        checks[
            "a duplicate event of the same name on one identity does not inflate friction counts"
        ] = (
            duplicated["available"] is True
            and dup_event_row is not None
            and dup_event_row["sessions"] == 1
            and populated_event_row is not None
            and dup_event_row["rage_clicks"] == populated_event_row["rage_clicks"]
        )
        # Revert the event name; the blanket identity_key revert below covers the rest.
        execute(
            "UPDATE silver.analytics_event SET event_name = :e "
            "WHERE user_id = :u AND session_id = :s",
            e=original_event_name,
            u=dup_row[0]["user_id"],
            s=dup_row[0]["session_id"],
        )
    else:
        print("  skipped: no second analytics_event row available to duplicate")

    execute("UPDATE silver.analytics_event SET identity_key = NULL")
    execute("UPDATE silver.session_replay_session SET identity_key = NULL")
    reverted = asyncio.run(
        sample_rows(
            StepInput(
                additional_data={"kpi_identifiers": identifiers},
                previous_step_content=read_output.content,
            )
        )
    ).content["friction"]
    print(f"  identity_key removed again: {reverted}")
    checks[
        "removing identity_key reports the gap again rather than caching the old answer"
    ] = reverted["available"] is False
else:
    print(
        "  skipped: no analytics_event/session_replay_session rows in the sample data"
    )
print()

# --- 4. The registered rfq-funnel workflow, end to end -----------------------------------------
# Whether the model step can run for real is known upfront (Azure OpenAI reads
# AZURE_OPENAI_* at the first request, per routing/providers.py's own docstring) --
# branching on that rather than on whatever exception a failed call happens to raise keeps
# the two halves from sharing one run_id and colliding in egpt_answer_provenance.
has_credentials = bool(os.environ.get("AZURE_OPENAI_API_KEY"))
print(
    f"4. rfq-funnel, driven with arun end to end (model credentials: {has_credentials})"
)
print("-" * 78)
run_options = {
    "trigger": "cookbook",
    "use_case_id": "uc_rfq_0001",
    "kpi_identifiers": identifiers,
    "kpis": [],
    "available_tables": set(),
}

if has_credentials:
    agents = build_agents(SETTINGS, build_control_plane_db(SETTINGS))
    workflow = next(
        w
        for w in build_workflows(SETTINGS, build_control_plane_db(SETTINGS), agents)
        if w.id == RFQ_WORKFLOW_ID
    )
    output = asyncio.run(
        workflow.arun(
            input="rfq", additional_data={**run_options, "run_id": "r10-cookbook-e2e"}
        )
    )
    step_names = [s.step_name for s in output.step_results]
    print(f"  run {output.run_id}: {output.status.value}; steps: {step_names}")
    explanation_step = next(
        s for s in output.step_results if s.step_name == "funnel-explanation"
    )
    explanation = FunnelExplanation.model_validate(explanation_step.content)
    print(f"  explanation.summary: {explanation.summary}")
    checks["the end-to-end run completed"] = output.status.value == "COMPLETED"
    checks["the live explanation cites only what the input carried"] = (
        citations_within_input(explanation)
    )
else:
    print("  skipped: AZURE_OPENAI_API_KEY not set on this machine")
    print(
        "  exercising the deterministic prefix and tail by hand, with a hand-built explanation"
    )
    offline_options = {**run_options, "run_id": "r10-cookbook-offline"}
    read = asyncio.run(read_gold(StepInput(additional_data=offline_options)))
    sampled = asyncio.run(
        sample_rows(
            StepInput(
                additional_data=offline_options, previous_step_content=read.content
            )
        )
    )
    top_movers = [
        BrandMovement(
            kpi_identifier=identifier,
            brand=row["brand"],
            current_value=row.get("current_value"),
            prior_value=row.get("prior_value"),
            delta=row.get("delta"),
            direction=Direction(row["direction"]),
        )
        for identifier, rows in sampled.content["movers"].items()
        for row in rows
    ]
    cited_brands = sorted({m.brand for m in top_movers})
    summary_prefix = (
        "Offline half: deterministic chain only, no model credentials configured."
    )
    hand_built = FunnelExplanation(
        kpi_identifiers=sorted(identifiers),
        top_movers=top_movers,
        friction_available=sampled.content["friction"]["available"],
        friction=[],
        brands_cited=cited_brands,
        summary=(
            f"{summary_prefix} {', '.join(cited_brands)} moved this period."
            if cited_brands
            else (
                f"{summary_prefix} No gold rows were available for "
                f"{', '.join(sorted(identifiers))} this run."
            )
        ),
        likely_drivers=[],
        snapshot_version=sampled.content["snapshot_version"] or "r10-cookbook-offline",
    )

    # check_explanation_grounding, by hand: the grounded hand-built explanation passes ...
    grounding_input = StepInput(
        additional_data=offline_options,
        previous_step_content=hand_built.model_dump(),
        previous_step_outputs={"sample-rows": sampled},
    )
    grounded = asyncio.run(check_explanation_grounding(grounding_input))
    print(
        f"  check_explanation_grounding (grounded hand-built explanation): "
        f"success={grounded.success}"
    )
    checks["check_explanation_grounding passes a grounded hand-built explanation"] = (
        grounded.success is True and not grounded.stop
    )

    # ... and a citation naming a brand absent from top_movers stops the chain.
    tampered = hand_built.model_copy(
        update={"brands_cited": [*cited_brands, "phantom-brand-not-in-movers"]}
    )
    tampered_input = StepInput(
        additional_data=offline_options,
        previous_step_content=tampered.model_dump(),
        previous_step_outputs={"sample-rows": sampled},
    )
    stopped = asyncio.run(check_explanation_grounding(tampered_input))
    print(
        f"  check_explanation_grounding (an uncited brand): "
        f"success={stopped.success} stop={stopped.stop}"
    )
    checks["check_explanation_grounding stops the chain on an uncited brand"] = (
        stopped.success is False and stopped.stop is True
    )

    prov_input = StepInput(
        additional_data={**offline_options, "explanation": hand_built.model_dump()},
        previous_step_content=hand_built.model_dump(),
    )
    provenance = asyncio.run(record_provenance(prov_input))
    delivery = asyncio.run(
        deliver_in_app(
            StepInput(
                additional_data=prov_input.additional_data,
                previous_step_content=provenance.content,
            )
        )
    )
    print(f"  provenance_ref: {provenance.content['provenance_ref']}")
    print(f"  delivery: {delivery.content['status']}")
    checks["the deterministic prefix and tail run without a model"] = (
        bool(provenance.content.get("provenance_ref"))
        and delivery.content.get("status") == "delivered"
    )

control_plane.close()
WAREHOUSE.dispose()

print()
print("=" * 78)
for name, ok in checks.items():
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
print("Overall:", "PASS" if all(checks.values()) else "FAIL")
raise SystemExit(0 if all(checks.values()) else 1)
