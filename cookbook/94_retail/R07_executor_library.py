"""R7 -- the executor library, composed into a throwaway workflow with no agent step.

The package's Verify line: "Compose a throwaway workflow from these executors alone, with no
agent step, and run it. Open the provenance record." This script does exactly that --
`check_preflight` -> `record_provenance` -> `deliver_in_app`, driven only by `.arun()` -- then
reads the provenance record back by `run_id`, satisfying the Evidence line: "The workflow
completes without a model. The provenance record lets someone else reconstruct where every
number came from."

A second run demonstrates the dry-run flag: `deliver_in_app` records what it would have sent
instead of sending it, and every other step still runs for real.

Needs a Postgres on 5532 with the control-plane database migrated (R7's two tables --
`egpt_answer_provenance`, `retail_deliveries` -- are created by `egpt-retail migrate`):

    docker compose -f deploy/compose.yaml up -d postgres
    EGPT_RETAIL_DB_URL=postgresql+psycopg://ai:ai@localhost:5532/ai \\
      .venv/bin/python -m egpt_retail.entrypoints migrate

Run:
    EGPT_RETAIL_DB_URL=postgresql+psycopg://ai:ai@localhost:5532/ai \\
      .venv/bin/python cookbook/94_retail/R07_executor_library.py
"""

import asyncio
import logging
import os
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "product"))

os.environ.setdefault(
    "EGPT_RETAIL_DB_URL", "postgresql+psycopg://ai:ai@localhost:5532/ai"
)

from egpt.answer_provenance import aread_provenance  # noqa: E402
from egpt.workflow.step import Step  # noqa: E402
from egpt.workflow.types import OnError  # noqa: E402
from egpt.workflow.workflow import Workflow  # noqa: E402
from egpt_retail.app.db import (  # noqa: E402
    build_control_plane_db,
    build_control_plane_engine,
)
from egpt_retail.app.settings import RetailSettings  # noqa: E402
from egpt_retail.contracts.kpi import KpiDefinition  # noqa: E402
from egpt_retail.executors.delivery import DRY_RUN_KEY, deliver_in_app  # noqa: E402
from egpt_retail.executors.preflight import check_preflight  # noqa: E402
from egpt_retail.executors.provenance import record_provenance  # noqa: E402
from egpt_retail.memory.scopes import SYSTEM_USER_ID  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(message)s")
logging.getLogger("egpt_retail").setLevel(logging.WARNING)

SETTINGS = RetailSettings()

# A ready KPI -- not the blocked scenario, which R07_preflight_blocked_kpi.py owns on its own.
# This run should complete allowed, all the way through delivery.
READY_KPI = KpiDefinition(
    identifier="orders__count",
    name="Order count",
    description="cookbook fixture",
    sql="SELECT count(*) FROM orders",
    grain="order",
    business_area="sales",
    source_tables=frozenset({"orders"}),
)


def build_workflow() -> Workflow:
    """The throwaway workflow: three R7 executors, no agent step. Must be driven with
    `.arun()` -- record_provenance/deliver_in_app are async (they do I/O), and a sync
    `.run()` would raise inside Step's retry loop, which on_error=OnError.skip (the default)
    would then silently swallow. on_error=OnError.fail below is what turns that into a loud
    failure instead, matching R4's own build_workflows precedent.
    """
    return Workflow(
        id="r7-verify",
        name="R7 executor library, verified",
        db=build_control_plane_db(SETTINGS),
        steps=[
            Step(
                name="check-preflight", executor=check_preflight, on_error=OnError.fail
            ),
            Step(
                name="record-provenance",
                executor=record_provenance,
                on_error=OnError.fail,
            ),
            Step(name="deliver-in-app", executor=deliver_in_app, on_error=OnError.fail),
        ],
    )


async def run_once(*, dry_run: bool):
    """Pins one id as both the workflow's own ``run_id`` and ``record_provenance``'s --
    ``StepInput`` carries no live reference to the enclosing run (confirmed: neither
    ``workflow_session`` nor ``additional_data`` gets one from the engine), so a composer that
    wants its provenance record to correlate with ``WorkflowRunOutput.run_id`` must generate
    the id itself and pass it to both places, exactly as here. Skipping this still works --
    ``record_provenance`` falls back to a fresh id of its own -- but that id would then have no
    relationship to the run that produced it, which is what this cookbook's assertion below
    guards against regressing.
    """
    run_id = str(uuid.uuid4())
    workflow = build_workflow()
    output = await workflow.arun(
        input="verify",
        run_id=run_id,
        additional_data={
            "run_id": run_id,
            "kpis": [READY_KPI],
            "available_tables": {"orders"},
            "use_case_id": "uc_r7_verify",
            "semantic_snapshot": "r7-cookbook.1",
            DRY_RUN_KEY: dry_run,
        },
        user_id=SYSTEM_USER_ID,
        tenant_id=SETTINGS.retail_customer_slug or "cookbook",
    )
    steps = output.step_results
    delivery = steps[-1].content
    provenance_ref = steps[-2].content["provenance_ref"]
    assert provenance_ref == output.run_id == run_id, (
        "the provenance record's id must correlate with the run that produced it"
    )
    return provenance_ref, delivery, output


async def main() -> None:
    print("R7 -- executor library (G4)")
    print("=" * 78)

    print("\n1. A throwaway workflow, three executors, no agent step, run for real")
    print("-" * 78)
    provenance_ref, delivery, output = await run_once(dry_run=False)
    print(f"  {len(output.step_results)} steps ran, zero of them an agent step")
    print(f"  provenance_ref: {provenance_ref}")
    print(f"  delivery:       {delivery}")

    print("\n2. Open the provenance record back by run_id")
    print("-" * 78)
    engine = build_control_plane_engine(SETTINGS)
    try:
        record = await aread_provenance(engine, provenance_ref)
    finally:
        engine.dispose()
    assert record is not None, (
        "every answer writes one -- a run without one is a defect"
    )
    print(f"  run_id:            {record.run_id}")
    print(f"  model_tier:        {record.model_tier}  (no model step ran -> 'none')")
    print(f"  semantic_snapshot: {record.semantic_snapshot}")
    print(f"  datasets_read:     {record.datasets_read}")
    assert record.model_tier == "none"

    print("\n3. The same workflow, dry_run=True -- only delivery changes")
    print("-" * 78)
    dry_provenance_ref, dry_delivery, _ = await run_once(dry_run=True)
    print(f"  real run's delivery:    {delivery}")
    print(f"  dry-run's delivery:     {dry_delivery}")
    assert delivery["status"] == "delivered" and delivery["dry_run"] is False
    assert dry_delivery["status"] == "previewed" and dry_delivery["dry_run"] is True

    print()
    print(
        "The workflow completed with no model step, and the provenance record it wrote is"
    )
    print(
        "readable back by run_id -- someone else can reconstruct where the answer came from."
    )


if __name__ == "__main__":
    asyncio.run(main())
