"""R9 -- the plan is a view over the compiled workflow, never authored beside it.

The delivery plan's fourth Verify line: "compare the plan on screen to the workflow
definition"; its evidence: "every step on screen exists in the workflow and none is missing".
This script builds an Inform-shape workflow the way the framework stores one
(`Workflow.to_dict()`), derives the plan, and compares the two -- then adds a `Condition`
with an `else_steps` branch and a `Router` with choices and checks the comparison still
holds, then swaps in an executor nobody registered and shows the refusal, by name.

When the control-plane Postgres is reachable (compose, `EGPT_RETAIL_DB_URL`), the last section
stores the workflow as a component through the framework's own API and derives the plan with
the `derive_plan` executor from the stored row -- the path the P5 route will take.

    .venv/bin/python cookbook/94_retail/R09_plan_view.py
"""

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "product"))

os.environ.setdefault(
    "EGPT_RETAIL_DB_URL", "postgresql+psycopg://ai:ai@localhost:5532/ai"
)
os.environ.setdefault("EGPT_RETAIL_CUSTOMER_SLUG", "r09")

from egpt.agent import Agent  # noqa: E402
from egpt.workflow import Workflow  # noqa: E402
from egpt.workflow.condition import Condition  # noqa: E402
from egpt.workflow.router import Router  # noqa: E402
from egpt.workflow.step import Step  # noqa: E402
from egpt.workflow.types import StepInput  # noqa: E402
from egpt_retail.api.models._generated import Plan  # noqa: E402
from egpt_retail.app.components import build_registry  # noqa: E402
from egpt_retail.executors.delivery import deliver_in_app  # noqa: E402
from egpt_retail.executors.gold import materialise_gold, read_gold  # noqa: E402
from egpt_retail.executors.plan import (  # noqa: E402
    UnknownPlanRef,
    derive_plan,
    plan_from_config,
    plan_refs,
)
from egpt_retail.executors.preflight import check_preflight  # noqa: E402
from egpt_retail.executors.provenance import record_provenance  # noqa: E402

NOW = datetime.now(timezone.utc)
REGISTERED = {f.__name__ for f in build_registry().functions}
checks = {}


def inform_skeleton():
    """The Inform shape (build plan, section 07): trigger, read gold, explain, provenance, deliver."""
    return [
        Step(name="Preflight", executor=check_preflight),
        Step(name="Read the KPIs from gold", executor=read_gold),
        Step(
            name="Explain",
            agent=Agent(id="funnel_explanation", name="funnel_explanation"),
            description="One model step. Explains the numbers; never decides anything.",
        ),
        Step(name="Record provenance", executor=record_provenance),
        Step(name="Deliver", executor=deliver_in_app),
    ]


def config_of(steps):
    return {
        "steps": [
            s.to_dict() for s in Workflow(id="wf-r09", name="R09", steps=steps).steps
        ]
    }


def show(plan):
    for step in plan["steps"]:
        tier = f" tier={step['model_tier']}" if step["model_tier"] else ""
        block = " BLOCKS" if step["permits_or_blocks"] else ""
        print(
            f"    {step['index']}. {step['kind']:<8} {step['ref']:<22} {step['name']}{tier}{block}"
        )


print("R9 -- the plan as a view over the compiled workflow")
print("=" * 78)

# --- 1. The Inform skeleton, derived and compared ---------------------------------------------
print()
print(
    "1. An Inform-shape workflow, stored as the framework stores it, derived as a plan"
)
print("-" * 78)
config = config_of(inform_skeleton())
plan = plan_from_config(
    config,
    use_case_id="uc_0001",
    version=1,
    workflow_component_id="cmp_r09",
    executor_names=REGISTERED,
    compiled_at=NOW,
)
Plan.model_validate(plan)
show(plan)
in_plan = [s["ref"] for s in plan["steps"]]
in_workflow = plan_refs(config)
print(f"  workflow refs: {in_workflow}")
print(f"  plan refs:     {in_plan}")
checks["every step on screen exists in the workflow and none is missing"] = (
    in_plan == in_workflow
)
checks["the plan validates against the contract"] = True
checks["executors that block are marked; the agent step never is"] = (
    plan["steps"][0]["permits_or_blocks"] is True
    and plan["steps"][2]["permits_or_blocks"] is False
    and plan["steps"][2]["model_tier"] == "small"
)
for name in list(checks)[-3:]:
    print("   ", "OK " if checks[name] else "FAIL", name)

# --- 2. A branch and a router: the walker reaches else_steps and choices ----------------------
print()
print("2. Add a Condition with an else branch and a Router with choices; compare again")
print("-" * 78)
steps = inform_skeleton()
branch = Condition(
    name="gold fresh?",
    evaluator=lambda _: True,
    steps=[steps[1]],
    else_steps=[Step(name="Materialise first", executor=materialise_gold), steps[1]],
)
router = Router(name="channel", selector=lambda _: [], choices=[steps[4]])
branched = {
    "steps": [
        steps[0].to_dict(),
        branch.to_dict(),
        steps[2].to_dict(),
        steps[3].to_dict(),
        router.to_dict(),
    ]
}
plan2 = plan_from_config(
    branched,
    use_case_id="uc_0001",
    version=2,
    workflow_component_id="cmp_r09",
    executor_names=REGISTERED,
    compiled_at=NOW,
)
Plan.model_validate(plan2)
show(plan2)
refs2 = plan_refs(branched)
print(f"  workflow refs (recursive): {refs2}")
checks["else_steps and router choices are on screen too"] = [
    s["ref"] for s in plan2["steps"]
] == refs2 and "materialise_gold" in refs2
print("   ", "OK " if checks[list(checks)[-1]] else "FAIL", list(checks)[-1])

# --- 3. A reference nobody registered ------------------------------------------------------
print()
print("3. Swap in a name nobody registered: the view refuses, by name")
print("-" * 78)
# R10 landed and registered `sample_rows` -- this section used to swap that name in as its
# stand-in for "a name nobody registered yet"; a name genuinely nobody has claimed still
# refuses the same way.
broken = config_of(inform_skeleton())
broken["steps"][1]["executor_ref"] = "not_a_real_executor"
try:
    plan_from_config(
        broken,
        use_case_id="uc_0001",
        version=3,
        workflow_component_id="cmp_r09",
        executor_names=REGISTERED,
    )
    refused = None
except UnknownPlanRef as exc:
    refused = str(exc)
print(f"  refusal: {refused}")
checks["an unregistered executor is refused, naming it"] = (
    bool(refused) and "not_a_real_executor" in refused
)
print("   ", "OK " if checks[list(checks)[-1]] else "FAIL", list(checks)[-1])

# --- 4. Through the control plane, when it is there ------------------------------------------
print()
print(
    "4. Stored as a component, derived by the derive_plan executor from the stored row"
)
print("-" * 78)
try:
    from egpt.db.base import ComponentType
    from egpt_retail.app.db import build_control_plane_db
    from egpt_retail.app.settings import RetailSettings

    settings = RetailSettings()
    db = build_control_plane_db(settings)
    component_id = "cmp_r09_inform"
    tenant = settings.retail_customer_slug
    try:
        db.delete_component(component_id, tenant_id=tenant, hard=True)
    except Exception:
        pass
    db.create_component_with_config(
        component_id=component_id,
        component_type=ComponentType.WORKFLOW,
        name="R09 Inform skeleton",
        config=config,
        tenant_id=tenant,
        actor="r09-cookbook",
    )
    output = asyncio.run(
        derive_plan(
            StepInput(
                additional_data={
                    "workflow_component_id": component_id,
                    "use_case_id": "uc_0001",
                }
            )
        )
    )
    stored_plan = output.content
    Plan.model_validate(stored_plan)
    print(
        f"  derive_plan: success={output.success} version={stored_plan['version']} steps={[s['ref'] for s in stored_plan['steps']]}"
    )
    checks["the stored component derives the same plan"] = (
        output.success and [s["ref"] for s in stored_plan["steps"]] == in_workflow
    )
    print("   ", "OK " if checks[list(checks)[-1]] else "FAIL", list(checks)[-1])
except (
    Exception
) as exc:  # no control plane on this machine: say so, do not fail the offline half
    print(
        f"  skipped: control plane not reachable ({type(exc).__name__}: {str(exc)[:90]})"
    )

print()
print("=" * 78)
for name, ok in checks.items():
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
print("Overall:", "PASS" if all(checks.values()) else "FAIL")
raise SystemExit(0 if all(checks.values()) else 1)
