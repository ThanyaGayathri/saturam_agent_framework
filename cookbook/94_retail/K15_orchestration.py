"""K15 -- a frozen spec compiled into a workflow, with the template and the parameters apart.

The package's scenario, from the delivery plan, is to diff the generated workflow against the
one R10 built by hand. **R10 has not landed**, so that diff is not available and this does the
three things that are:

  1. Diffs the generated Inform sequence against R9's hand-built skeleton, which is a real
     independently-authored target for one shape.
  2. Puts every generated definition through R9's `plan_from_config`, which raises on any
     executor the registry does not resolve -- so "composes what exists and cannot invent a
     deterministic step" is checked by another lane's code rather than asserted here.
  3. Reports, per shape, what this deployment still cannot build, as a set difference.

**Template and parameters are separate layers and the run shows both.** A template supplies
filled-in parameter values -- schedule, threshold, guardrail amounts -- and never steps: the
sequence follows from the shape, which is the build plan's whole tractability argument. What
this prints beside each parameter is where its value came from, because on an Act the
difference between an approver who set the per-run cap and one who accepted the number a
template shipped with is the difference between a considered limit and a default nobody read.

Needs nothing but the product. No warehouse, no Neo4j, no model:

    PYTHONPATH=product .venvs/demo/bin/python cookbook/94_retail/K15_orchestration.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "product"))

from egpt.agent import Agent  # noqa: E402
from egpt.workflow import Step, Workflow  # noqa: E402
from egpt_retail.app.components import build_registry  # noqa: E402
from egpt_retail.contracts.use_case import (  # noqa: E402
    SHAPE_PARAMETERS,
    UseCaseShape,
)
from egpt_retail.executors.delivery import deliver_in_app  # noqa: E402
from egpt_retail.executors.gold import read_gold  # noqa: E402
from egpt_retail.executors.plan import (  # noqa: E402
    UnknownPlanRef,
    plan_from_config,
)
from egpt_retail.executors.preflight import check_preflight  # noqa: E402
from egpt_retail.executors.provenance import record_provenance  # noqa: E402
from egpt_retail.orchestration import (  # noqa: E402
    capability_report,
    compile_workflow,
    diff_against,
    normalise_config,
)

print("K15 -- orchestration")
print("=" * 78)

REGISTERED = {f.__name__ for f in build_registry().functions}

# The spec a person would have filled in on the Inform form, frozen. The shape is on it: each
# shape has its own form, so the shape is chosen before the spec is filled.
SPEC = {
    "use_case_id": "weekly_pipeline",
    "shape": "inform",
    "question": "How did open pipeline move against the last four weeks?",
    "business_area": "sales",
    "kpi_identifiers": ["k13_deal__open_value"],
    "grain": "overall",
    "time_window": {"unit": "week", "count": 4},
}
DRAFT = {"schedule": "0 7 * * 1", "recipient": "sales@example.com"}

# --- 1. What this deployment can build ---------------------------------------------------

print()
print("1. Capability, per shape, against the registry as it stands")
print("-" * 78)
print(f"  registered executors: {len(REGISTERED)}")
report = capability_report(REGISTERED)
for shape, row in report.items():
    print(
        f"  {shape:<10} {row['buildable']:>2}/{row['stages']} stages buildable, "
        f"{row['model_steps']} model step(s)"
    )
    if row["satisfied_elsewhere"]:
        for stage, by in row["satisfied_elsewhere"].items():
            print(f"      {stage} -- satisfied by {by}, not missing")
    for stage, ref in row["missing"].items():
        print(f"      {stage} -- needs executor {ref}, which nobody has written")
print()
print(
    "  Every one of those is a set difference between the plan's section 07 sequence and"
)
print("  what build_registry() resolves. None of it is an estimate.")

# --- 2. The Inform shape, compiled -------------------------------------------------------

inform = compile_workflow(SPEC, registered_executors=REGISTERED, draft=DRAFT)
print()
print("2. The Inform spec, compiled")
print("-" * 78)
print(f"  shape: {inform.shape.value}   publishable: {inform.publishable}")
for step in normalise_config(inform.config):
    gate = "  [gate]" if step["gate"] else ""
    print(f"    {step['kind']:<8} {step['name']:<22} {step['ref']}{gate}")
if inform.notes:
    print(f"  {inform.notes}")

# --- 3. Template and parameters, kept apart ----------------------------------------------

# A shipped template for the Act shape: the parameters arrive filled in. Steps are NOT part of
# a template -- the sequence follows from the shape.
TEMPLATE = {
    "id": "tmpl_stalled_quote_chase",
    "title": "Chase stalled quote requests",
    "parameters": {
        "candidates": "deals with no stage change in 7 days",
        "guardrails": 5000,
        "write target": "crm.deal.stage",
        "per-run cap": 20,
        "rollback": "stage snapshot taken before the write",
        "schedule": "0 7 * * *",
    },
}
ACT_SPEC = {**SPEC, "shape": "act", "use_case_id": "chase_stalled_quotes"}
# The author changed the guardrail, and left everything else as the template shipped it.
ACT_DRAFT = {"guardrail_amount": 2000}

act = compile_workflow(
    ACT_SPEC, registered_executors=REGISTERED, draft=ACT_DRAFT, template=TEMPLATE
)
print()
print("3. An Act created from a template: what was templated, what was chosen")
print("-" * 78)
print(f"  template: {act.template_id}")
for slot in SHAPE_PARAMETERS[UseCaseShape.ACT]:
    if slot in act.parameters:
        origin = act.parameter_origins[slot].value
        marker = "  <-- a person chose this" if origin == "authored" else ""
        print(f"    {slot:<16} {str(act.parameters[slot])[:34]:<36} {origin}{marker}")
    else:
        print(f"    {slot:<16} {'(unfilled)':<36} -")
print()
print(
    "  On an Act, 'template' against per-run cap means nobody chose that number. It is"
)
print(
    "  the number the pattern shipped with, and the approver accepted the form as it came."
)

templated = sum(1 for o in act.parameter_origins.values() if o.value == "template")
authored = sum(1 for o in act.parameter_origins.values() if o.value == "authored")

# A template supplies values, never steps: same sequence with and without it.
without_template = compile_workflow(
    ACT_SPEC, registered_executors=REGISTERED, draft=ACT_DRAFT
)
template_changed_steps = normalise_config(act.config) != normalise_config(
    without_template.config
)

# --- 4. Against R9's hand-built Inform skeleton -------------------------------------------

# Written by Lane R in R09_plan_view.py from the same section 07 table, independently. The
# closest thing to R10's hand-built workflow that exists today.
hand_built = {
    "steps": [
        s.to_dict()
        for s in Workflow(
            id="wf-r09",
            name="R09",
            steps=[
                Step(name="Preflight", executor=check_preflight),
                Step(name="Read the KPIs from gold", executor=read_gold),
                Step(
                    name="Explain",
                    agent=Agent(id="funnel_explanation", name="funnel_explanation"),
                ),
                Step(name="Record provenance", executor=record_provenance),
                Step(name="Deliver", executor=deliver_in_app),
            ],
        ).steps
    ]
}
differences = diff_against(inform, hand_built)
print()
print("4. Generated vs R9's hand-built Inform skeleton")
print("-" * 78)
for line in differences or ["  (identical)"]:
    print(f"  {line}")
print()
print(
    "  The plan's evidence line allows differences where 'every difference is explainable"
)
print(
    "  and one of the two is wrong'. Read them in K15_BASELINE.md -- this script does not"
)
print("  judge which side is right, because that is the reviewer's call.")

# --- 5. Composes only what exists ---------------------------------------------------------

print()
print("5. Checked by R9's plan walker, which is not my code")
print("-" * 78)
walker_results = {}
for shape in UseCaseShape:
    spec = {**SPEC, "shape": shape.value}
    draft = {
        **DRAFT,
        "operator": ">=",
        "threshold": 10,
        "severity": "high",
        "guardrail_amount": 5000,
        "candidates": "c",
        "approver": "a",
        "write_target": "t",
        "timeout_behaviour": "hold",
        "per_run_cap": 20,
        "rollback": "r",
    }
    built = compile_workflow(spec, registered_executors=REGISTERED, draft=draft)
    if not built.config["steps"]:
        walker_results[shape.value] = "no steps emitted"
        continue
    try:
        plan_from_config(
            built.config,
            use_case_id=built.use_case_id,
            version=1,
            workflow_component_id=None,
            executor_names=REGISTERED,
            agent_ids=[m["agent_id"] for m in built.model_steps],
        )
        walker_results[shape.value] = "accepted"
    except UnknownPlanRef as error:
        walker_results[shape.value] = f"refused -- {error}"
for shape, result in walker_results.items():
    print(f"  {shape:<10} {result[:88]}")
print()
print(
    "  A generated model step has no row in R8's ROUTING_TABLE, which is a static literal"
)
print(
    "  keyed by agent id -- and K15 mints an agent id per use case. So the walker refuses"
)
print(
    "  it, correctly: an unrouted step has no model. The compiler already names the tier"
)
print(
    "  each one needs, so closing this is a lookup rather than a redesign. Open item K-9."
)
print()
for shape in (UseCaseShape.INFORM, UseCaseShape.ACT):
    built = compile_workflow(
        {**SPEC, "shape": shape.value}, registered_executors=REGISTERED, draft=DRAFT
    )
    print(f"  {shape.value:<10} needs routing rows: {built.required_routing_rows}")

# --- 6. Verdict ----------------------------------------------------------------------------

print()
print("=" * 78)
failures = []
if not inform.config["steps"]:
    failures.append("the Inform spec compiled to no steps at all")
if template_changed_steps:
    failures.append(
        "the template changed the step sequence. A template supplies parameter values and "
        "never steps -- one that could add a step would be a fifth shape wearing another "
        "name, and every guarantee resting on the four sequences would stop holding."
    )
if act.template_id != TEMPLATE["id"]:
    failures.append(
        "the compiled workflow does not name the template it was created from"
    )
if not templated or not authored:
    failures.append(
        f"parameter origins did not separate: {templated} templated, {authored} authored. "
        "If everything reads the same way, the activation screen cannot show which limits a "
        "person actually chose."
    )
if (
    act.parameter_origins.get("guardrails")
    and act.parameter_origins["guardrails"].value != "authored"
):
    failures.append("the author's edited guardrail was not recorded as authored")
if walker_results.get("alert") != "accepted":
    failures.append(
        f"the Alert shape, which is executors end to end, did not pass R9's walker: "
        f"{walker_results.get('alert')}"
    )
if any(row["buildable"] == row["stages"] for row in report.values()):
    failures.append(
        "a shape reports every stage buildable, which contradicts the registry: no executor "
        "exists for compare_to_baseline, write_to_target and the rest. The capability report "
        "is not subtracting."
    )

if failures:
    print("K15 does NOT hold:")
    for failure in failures:
        print("  -", failure)
    raise SystemExit(1)
print(
    "Orchestration holds. The shape fixes the steps and the template fixes the values;"
)
print(
    "every parameter carries where it came from; a template cannot change a sequence; and"
)
print("the one shape that is executors end to end passes R9's plan walker.")
print()
print(
    "NOT verified here: the step-for-step diff against R10's hand-built RFQ workflow, and"
)
print(
    "the second use case. R10 has not landed. Recorded in K15_BASELINE.md and TEST_LOG.md"
)
print("rather than implied by this line.")
