"""R8 -- model routing (G6): which tier serves which agent, and escalation in the run record.

Two Azure OpenAI deployments, small and frontier, on one code path. The routing table names
the tier per agent; small-tier agents are composed through the framework's escalation
provider, so a request the small deployment provably cannot take moves to the frontier
*before* the call -- and the move is written into ``RunOutput.metadata`` by the per-run hook,
which is what "visible in the run record" means here.

Four sections. The first two need no credential and are the proof of the pre-call decision;
the last two run the same agent on both tiers for real.

1. The table covers every registered agent; an unrouted agent refuses assembly, by name.
2. Offline: an input over the small tier's declared window escalates before the small tier is
   asked -- counted on the providers -- and the escalation lands on the RunOutput.
3. Live: `join_path_explainer` on the small tier, then the frontier, same input. The RunOutput
   names each deployment; both outputs validate against JoinExplanation.
4. Live: the same agent with a deliberately small declared window and an input past it. The
   run escalates, `metadata["egpt_escalation"]` says why, and the frontier served it.

Run:
    .venv/bin/python cookbook/94_retail/R08_model_routing.py            # sections 1-2
    set -a && source azure_key.env && set +a
    EGPT_RETAIL_AZURE_SMALL_DEPLOYMENT=<small deployment> \\
      .venv/bin/python cookbook/94_retail/R08_model_routing.py          # all four
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "product"))

from egpt.agent import Agent  # noqa: E402
from egpt.agent._run import ESCALATION_METADATA_KEY, apply_run_model  # noqa: E402
from egpt.run import RunContext  # noqa: E402
from egpt.run.agent import RunOutput  # noqa: E402
from egpt_retail.app.components import (  # noqa: E402
    JOIN_PATH_EXPLAINER_ID,
    build_agents,
)
from egpt_retail.app.db import build_control_plane_db  # noqa: E402
from egpt_retail.app.settings import RetailSettings  # noqa: E402
from egpt_retail.contracts.fixture_builder import CONTRACT_FIXTURE_PATH  # noqa: E402
from egpt_retail.contracts.semantic_stub import StubSemanticRead  # noqa: E402
from egpt_retail.routing import ROUTING_TABLE, UnroutedAgentError  # noqa: E402
from egpt_retail.routing.providers import (  # noqa: E402
    TierModelProvider,
    build_tier_providers,
    provider_for,
    route_agents,
)
from egpt_retail.routing.table import Tier  # noqa: E402
from egpt_retail.skills.join_path_explainer import (  # noqa: E402
    JoinExplanation,
    verdict_for_path,
)

SKILLS_DIR = Path(__file__).resolve().parents[2] / "product" / "egpt_retail" / "skills"
PROMPT = (SKILLS_DIR / "join_path_explainer.md").read_text()

# A control-plane URL nothing connects to: build_agents constructs a PostgresDb and never
# opens it, and the agents run here are built without one.
DEAD_DB = "postgresql+psycopg://nobody:nope@127.0.0.1:1/none"

print("R8 -- model routing: the table, the pre-call decision, the run record")
print("=" * 78)

# --- 1. The table ----------------------------------------------------------------------------

print()
print("1. The routing table, and what happens to an agent that is not in it")
print("-" * 78)
for agent_id, tier in ROUTING_TABLE.items():
    print(f"  {agent_id:<22} {tier.value}")

settings = RetailSettings(retail_db_url=DEAD_DB)
registered = build_agents(settings, build_control_plane_db(settings))
print()
for agent in registered:
    provider = agent.model_provider
    print(
        f"  registered {agent.id:<22} model={agent.model!r:<6} provider={provider.name}"
    )
routed_ok = all(
    agent.model is None and agent.id in ROUTING_TABLE for agent in registered
)
print(
    "   ",
    "OK " if routed_ok else "FAIL",
    "every registered agent pins no model and has a row",
)


class _Ghost:
    id = "ghost_agent"
    model = None


try:
    route_agents([_Ghost()], build_tier_providers(settings))
    refused = False
except UnroutedAgentError as exc:
    refused = "ghost_agent" in str(exc)
    print(f"  refusal: {str(exc)[:96]}...")
print(
    "   ",
    "OK " if refused else "FAIL",
    "an unrouted agent refuses assembly and names itself",
)

# --- 2. Offline: the pre-call decision -----------------------------------------------------

print()
print(
    "2. An input over the small tier's declared window escalates before the call (offline)"
)
print("-" * 78)
WINDOW = 1_000
providers = build_tier_providers(
    RetailSettings(retail_db_url=DEAD_DB, retail_small_context_window=WINDOW)
)
resolved_on: dict = {"small": 0, "frontier": 0}
_original = TierModelProvider.resolve_detailed


def _counting(self, request):  # noqa: ANN001
    resolved_on[self.tier.value] += 1
    return _original(self, request)


TierModelProvider.resolve_detailed = _counting  # type: ignore[method-assign]

offline_agent = Agent(
    id=JOIN_PATH_EXPLAINER_ID,
    name=JOIN_PATH_EXPLAINER_ID,
    instructions=PROMPT,
    output_schema=JoinExplanation,
)
route_agents([offline_agent], providers)

within = RunOutput()
apply_run_model(
    offline_agent,
    RunContext(run_id="r-within", session_id="s-1", user_id="u-1"),
    tools=None,
    run_response=within,
    estimated_input_tokens=WINDOW - 1,
)
over = RunOutput()
apply_run_model(
    offline_agent,
    RunContext(run_id="r-over", session_id="s-1", user_id="u-1"),
    tools=None,
    run_response=over,
    estimated_input_tokens=WINDOW + 1,
)
TierModelProvider.resolve_detailed = _original  # type: ignore[method-assign]

record = (over.metadata or {}).get(ESCALATION_METADATA_KEY)
print(
    f"  {WINDOW - 1} tokens: model={within.model} escalation={(within.metadata or {}).get(ESCALATION_METADATA_KEY)}"
)
print(f"  {WINDOW + 1} tokens: model={over.model}")
print(f"           escalation={json.dumps(record)}")
print(f"  resolutions by tier: {resolved_on}")
within_ok = within.model == providers.small.model_id and not (within.metadata or {})
over_ok = (
    record is not None
    and record["reason"] == "input_exceeds_context_window"
    and record["escalated_from"] == "azure-openai"
    and record["model_id"] == over.model == providers.frontier.model_id
    and record["agent_id"] == JOIN_PATH_EXPLAINER_ID
)
before_call_ok = resolved_on == {"small": 1, "frontier": 1}
print(
    "   ",
    "OK " if within_ok else "FAIL",
    "within the window: small deployment, no escalation key",
)
print(
    "   ",
    "OK " if over_ok else "FAIL",
    "over the window: frontier deployment, reason in the run record",
)
print(
    "   ",
    "OK " if before_call_ok else "FAIL",
    "the over-window request never resolved the small tier -- decided before the call",
)

offline_checks = {
    "every agent routed": routed_ok,
    "unrouted agent refused": refused,
    "within window stays small": within_ok,
    "over window escalates into the record": over_ok,
    "escalation is pre-call": before_call_ok,
}

# --- Live half ------------------------------------------------------------------------------

if not os.environ.get("AZURE_OPENAI_API_KEY"):
    print()
    print("=" * 78)
    print("Offline half:", "PASS" if all(offline_checks.values()) else "FAIL")
    print(
        "AZURE_OPENAI_API_KEY is not set; sections 3-4 (live) skipped. Source azure_key.env"
    )
    print("and set EGPT_RETAIL_AZURE_SMALL_DEPLOYMENT to run them.")
    raise SystemExit(0 if all(offline_checks.values()) else 1)

# The frontier deployment: EGPT_RETAIL_AZURE_DEPLOYMENT, else the AZURE_OPENAI_DEPLOYMENT the
# K14 cookbooks use, else the setting's default. The small deployment has no such fallback --
# there is nothing in azure_key.env that names a second deployment.
if "EGPT_RETAIL_AZURE_DEPLOYMENT" not in os.environ and os.environ.get(
    "AZURE_OPENAI_DEPLOYMENT"
):
    os.environ["EGPT_RETAIL_AZURE_DEPLOYMENT"] = os.environ["AZURE_OPENAI_DEPLOYMENT"]
if not os.environ.get("EGPT_RETAIL_AZURE_SMALL_DEPLOYMENT"):
    print()
    print(
        "EGPT_RETAIL_AZURE_SMALL_DEPLOYMENT is not set; sections 3-4 need a second deployment."
    )
    raise SystemExit(1)

live_settings = RetailSettings(retail_db_url=DEAD_DB)
SMALL_DEPLOYMENT = live_settings.retail_azure_small_deployment
FRONTIER_DEPLOYMENT = live_settings.retail_azure_deployment
print()
print(f"  small tier    -> deployment {SMALL_DEPLOYMENT}")
print(f"  frontier tier -> deployment {FRONTIER_DEPLOYMENT}")

reader = StubSemanticRead.from_fixture(CONTRACT_FIXTURE_PATH)
snapshot = reader.snapshot()


def join_path_payload(from_table: str, to_table: str) -> dict:
    path = reader.find_join_path(from_table, to_table)
    if path is None:
        return {"path": None}
    return {
        "path": {
            "from_table": path.from_table,
            "to_table": path.to_table,
            "hops": path.hops,
            "intermediate_tables": list(path.intermediate_tables),
            "edges": [
                {
                    "left_table": e.left_table,
                    "right_table": e.right_table,
                    "on": e.on,
                    "cardinality": e.cardinality,
                }
                for e in path.edges
            ],
        }
    }


def prompt_for(
    from_table: str, to_table: str, join_path: object, pad_lines: int = 0
) -> str:
    padding = "".join(
        f"\ncontext line {i}: harmless filler the explainer is told to ignore."
        for i in range(pad_lines)
    )
    return (
        f"from_table: {from_table}\n"
        f"to_table: {to_table}\n"
        f"snapshot: {json.dumps({'version': snapshot.version})}\n"
        f"join_path: {json.dumps(join_path)}"
        + (f"\nappendix (ignore): {padding}" if pad_lines else "")
    )


def explainer(tier: Tier, providers_for_run) -> Agent:  # noqa: ANN001
    # One agent per tier, built once each; never constructed inside a loop.
    agent = Agent(
        id=JOIN_PATH_EXPLAINER_ID,
        name=JOIN_PATH_EXPLAINER_ID,
        instructions=PROMPT,
        output_schema=JoinExplanation,
    )
    setattr(agent, "model_provider", provider_for(tier, providers_for_run))  # noqa: B010
    return agent


live_providers = build_tier_providers(live_settings)
found = join_path_payload("customers", "products")
expected = verdict_for_path(found["path"])

# --- 3. One agent, both tiers --------------------------------------------------------------

print()
print("3. join_path_explainer on the small tier, then the frontier, same input")
print("-" * 78)
tier_results = {}
for tier in (Tier.SMALL, Tier.FRONTIER):
    agent = explainer(tier, live_providers)
    response = agent.run(prompt_for("customers", "products", found))
    result = response.content
    assert isinstance(result, JoinExplanation), f"{tier.value}: got {type(result)}"
    tier_results[tier] = (response, result)
    print(
        f"  {tier.value:<8} served by {response.model:<20} verdict={result.verdict.value} "
        f"route={result.route}"
    )
    print(
        f"           escalation={(response.metadata or {}).get(ESCALATION_METADATA_KEY)}"
    )

small_response, small_result = tier_results[Tier.SMALL]
frontier_response, frontier_result = tier_results[Tier.FRONTIER]
names_ok = (
    small_response.model == SMALL_DEPLOYMENT
    and frontier_response.model == FRONTIER_DEPLOYMENT
)
verdicts_ok = small_result.verdict is expected and frontier_result.verdict is expected
no_escalation_ok = not any(
    ESCALATION_METADATA_KEY in (r.metadata or {}) for r, _ in tier_results.values()
)
print(
    "   ",
    "OK " if names_ok else "FAIL",
    "RunOutput.model names the tier's deployment, both tiers",
)
print(
    "   ",
    "OK " if verdicts_ok else "FAIL",
    "both outputs validate and carry the deterministic verdict",
)
print("   ", "OK " if no_escalation_ok else "FAIL", "neither run escalated")

# --- 4. Live escalation past a small declared window -------------------------------------

print()
print(
    "4. The same agent, small tier, with a 600-token declared window and an input past it"
)
print("-" * 78)
tight = build_tier_providers(
    RetailSettings(retail_db_url=DEAD_DB, retail_small_context_window=600)
)
tight_agent = explainer(Tier.SMALL, tight)
escalated = tight_agent.run(prompt_for("customers", "products", found, pad_lines=80))
escalated_result = escalated.content
assert isinstance(escalated_result, JoinExplanation)
live_record = (escalated.metadata or {}).get(ESCALATION_METADATA_KEY)
print(f"  served by {escalated.model} verdict={escalated_result.verdict.value}")
print(f"  escalation={json.dumps(live_record)}")
live_escalated_ok = (
    live_record is not None
    and live_record["reason"] == "input_exceeds_context_window"
    and escalated.model == FRONTIER_DEPLOYMENT
    and live_record["model_id"] == FRONTIER_DEPLOYMENT
    and escalated_result.verdict is expected
)
print(
    "   ",
    "OK " if live_escalated_ok else "FAIL",
    "escalated before the call, frontier served it, and the run record says why",
)

print()
print("=" * 78)
checks = {
    **offline_checks,
    "both tiers named on RunOutput": names_ok,
    "both tiers valid and deterministic": verdicts_ok,
    "no escalation within window (live)": no_escalation_ok,
    "escalation past the window (live)": live_escalated_ok,
}
for name, ok in checks.items():
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
print("Overall:", "PASS" if all(checks.values()) else "FAIL")
raise SystemExit(0 if all(checks.values()) else 1)
