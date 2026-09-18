"""K13 -- use case candidates across all four shapes, with the shape gated by configuration.

The package's scenario, from the delivery plan: run it, take the top suggestion, and trace by
hand which data made it feasible. Plus the check the plan does not ask for and this package
needs most -- **no suggestion is ready to Act in a deployment with nowhere to write.** An
unattended write is the one shape where a plausible-but-wrong suggestion is expensive, because
it reads exactly like the other three and accepting it changes a customer's systems with
nobody watching.

The gate is a set difference over configured capabilities, run here against two deployment
profiles: a bare one that can only show someone a number, and a fully configured one. The same
suggestions are gated differently, which is the proof that the verdict comes from the
deployment rather than from the model's confidence.

**Two known incompletenesses, both deliberate and both recorded in TEST_LOG.md.** The API
contract's `UseCaseSuggestion` has no field for shape or autonomy (open item R-12), so the
assembled list carries the caveat in `notes` rather than dropping them silently. And the
capability profile below is a literal in this script: which setting supplies a write target is
Lane R's to define, and this needs only to know whether one exists.

Needs Azure credentials, the ontology from K3, the harvest from K4/K5, the registry from K11,
and Neo4j:

    set -a; . ./azure_key.env; set +a
    PYTHONPATH=product .venvs/demo/bin/python cookbook/94_retail/K13_use_case_suggestion.py
"""

import atexit
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "product"))
# Run by hand against compose Postgres; the deployment default is Azure AI Search (AZ-C), so
# choose pgvector here unless an Azure AI Search endpoint is actually configured.
if not os.environ.get("EGPT_RETAIL_KNOWLEDGE_SEARCH_ENDPOINT"):
    os.environ.setdefault("EGPT_RETAIL_KNOWLEDGE_BACKEND", "pgvector")

import psycopg  # noqa: E402
from egpt.knowledge.knowledge import Knowledge  # noqa: E402
from egpt.metrics import ProviderMode  # noqa: E402
from egpt_retail.app.settings import RetailSettings  # noqa: E402
from egpt_retail.contracts.kpi import KpiDefinition  # noqa: E402
from egpt_retail.contracts.semantic import available_tables  # noqa: E402
from egpt_retail.knowledge.embedding import resolve_knowledge_embedder  # noqa: E402
from egpt_retail.knowledge.vector import build_knowledge_vector_db  # noqa: E402
from egpt_retail.kpi.store import KpiRegistryStore  # noqa: E402
from egpt_retail.ontology.store import OntologyStore  # noqa: E402
from egpt_retail.semantic.harvest import harvest_schema  # noqa: E402
from egpt_retail.semantic.store import SemanticGraphStore  # noqa: E402
from egpt_retail.skills import use_case_suggestion as k13  # noqa: E402
from egpt_retail.suggestions import (  # noqa: E402
    build_suggestion_agents,
    gather_context,
)
from egpt_retail.suggestions.context import (  # noqa: E402
    render_context,
    samples_from_harvest,
)
from neo4j import GraphDatabase  # noqa: E402

if not os.environ.get("AZURE_OPENAI_API_KEY"):
    raise SystemExit(
        "AZURE_OPENAI_API_KEY is not set. Source azure_key.env and re-run."
    )

WAREHOUSE = os.environ.get(
    "EGPT_RETAIL_WAREHOUSE_DSN", "postgresql://ai:ai@localhost:5532/egpt_warehouse"
)
CONTROL_PLANE = os.environ.get(
    "EGPT_RETAIL_CP_DSN", "postgresql://ai:ai@localhost:5532/ai"
)
NEO4J = os.environ.get("EGPT_ONTOLOGY_NEO4J_URI", "neo4j://localhost:7701")
AUTH = ("neo4j", os.environ.get("EGPT_RETAIL_NEO4J_PASSWORD", "egptretail"))

# Two deployment profiles. Not a fixture detail -- the whole argument of this cookbook is that
# the same suggestions get different verdicts from these two, so the verdict is a property of
# the deployment and not of the model's enthusiasm.
BARE = frozenset({"delivery_channel"})
CONFIGURED = frozenset(
    {
        "delivery_channel",
        "alert_threshold",
        "approver",
        "write_target",
        "guardrails",
        "per_run_cap",
        "rollback_point",
    }
)

print("K13 -- use case suggestion")
print("=" * 78)

warehouse = psycopg.connect(WAREHOUSE)
warehouse.autocommit = True
harvest = harvest_schema(warehouse, "silver", source="egpt_warehouse", sample=True)
warehouse.close()

driver = GraphDatabase.driver(NEO4J, auth=AUTH)
graph = SemanticGraphStore(driver=driver)
graph.ensure_indexes()
graph.write_harvest(harvest, snapshot_version="k13-demo.1")

control_plane = psycopg.connect(CONTROL_PLANE)
registry = KpiRegistryStore(control_plane)
registry.ensure_schema()

# K13 sits downstream of K11: a use case is a KPI plus what happens when it moves, so an empty
# registry means there is nothing for a use case to watch. The first live run of this script
# proved that the hard way -- the agent refused everything and named, correctly, the eight
# metrics it would have needed. So this seeds four KPIs over the real silver schema.
#
# The registry is a SHARED dev store, and a cookbook that leaves rows behind breaks the next
# one. Removal is registered with atexit rather than written at the bottom, because a failing
# assertion below exits before the bottom is reached -- the exact way K05 and R05 left rows
# behind and cost three debugging sessions.
SEEDED = [
    KpiDefinition(
        identifier="k13_deal__open_value",
        name="Open pipeline value",
        description="Total value of deals not yet in a closed stage.",
        sql="SELECT sum(amount) FROM crm_deal WHERE stage NOT LIKE 'closed%'",
        grain="overall",
        business_area="sales",
        source_tables=frozenset({"crm_deal"}),
        concept="QuoteRequest",
    ),
    KpiDefinition(
        identifier="k13_deal__stage_ageing_days",
        name="Days in current stage",
        description="How long each deal has sat in the stage it is in.",
        sql=(
            "SELECT d.deal_id, max(h.days_in_stage) FROM crm_deal d "
            "JOIN crm_deal_stage_history h ON h.deal_id = d.deal_id GROUP BY d.deal_id"
        ),
        grain="overall",
        business_area="sales",
        source_tables=frozenset({"crm_deal", "crm_deal_stage_history"}),
        concept="QuoteRequest",
    ),
    KpiDefinition(
        identifier="k13_session__rage_click_rate",
        name="Rage click rate",
        description="Share of replayed sessions containing a rage click.",
        sql=(
            "SELECT avg(CASE WHEN rage_click_count > 0 THEN 1.0 ELSE 0 END) "
            "FROM session_replay_session"
        ),
        grain="overall",
        business_area="marketing",
        source_tables=frozenset({"session_replay_session"}),
        concept="WebSession",
    ),
    KpiDefinition(
        identifier="k13_contact__reachable",
        name="Reachable contacts",
        description="Contacts holding an email address.",
        sql="SELECT count(*) FROM crm_contact WHERE email IS NOT NULL",
        grain="overall",
        business_area="customer",
        source_tables=frozenset({"crm_contact"}),
        concept=None,
    ),
]
registry.save_many(SEEDED)


@atexit.register
def _remove_seeded_kpis():
    if control_plane.closed:
        return
    for kpi in SEEDED:
        registry.delete(kpi.identifier)
    control_plane.close()


tables = available_tables(graph)
snapshot = graph.snapshot().version
kpis = [kpi for kpi in registry.list() if kpi.identifier.startswith("k13_")]

# --- 1. What the agent is given ---------------------------------------------------------


def harvest_text():
    lines = []
    for table in harvest.tables:
        lines.append(f"{table.qualified} ({table.row_count} rows)")
        for column in table.columns:
            bits = [f"    {column.name} {column.data_type}"]
            if column.is_key:
                bits.append("[key]")
            if column.comment:
                bits.append(f"-- {column.comment}")
            lines.append(" ".join(bits))
    return "\n".join(lines)


concepts_text = "\n".join(
    f"- {name}: {(graph.get_concept(name).description if graph.get_concept(name) else '')}"
    for name in graph.list_concepts()
)

print()
print("1. Inputs")
print("-" * 78)
print(f"  tables in the semantic layer: {len(tables)}   snapshot: {snapshot}")
print(f"  registered KPIs: {len(kpis)}")
print(f"  concepts: {len(graph.list_concepts())}")
print(f"  bare deployment has: {', '.join(sorted(BARE))}")
print(f"  configured deployment has: {len(CONFIGURED)} capabilities")


# The stores the suggestion pass reads, built the way the product builds them: agents routed
# through R8 (no pinned model), and domain prose through K10 rather than pasted in by hand.
settings = RetailSettings(
    retail_customer_slug="middleby",
    retail_db_url=CONTROL_PLANE.replace("postgresql://", "postgresql+psycopg://", 1),
    retail_neo4j_uri=NEO4J,
    retail_neo4j_password=AUTH[1],
    retail_provider_mode=ProviderMode.LOCAL,
    # The corpus K02 loaded for cookbooks -- the table K10 reads. A deployment's
    # `load-knowledge` fills the settings default instead.
    retail_knowledge_table=os.environ.get(
        "EGPT_RETAIL_KNOWLEDGE_TABLE", "egpt_retail_domain_prose_bge"
    ),
    retail_azure_deployment=os.environ["AZURE_OPENAI_DEPLOYMENT"],
    retail_azure_small_deployment=os.environ["AZURE_OPENAI_DEPLOYMENT"],
)
embedder = resolve_knowledge_embedder(mode=ProviderMode.LOCAL)
knowledge = Knowledge(vector_db=build_knowledge_vector_db(settings, embedder.embedder))
ontology = OntologyStore(driver=driver, embedder=embedder.embedder)
context = gather_context(
    graph,
    knowledge=knowledge,
    ontology=ontology,
    tenant_id="middleby",
    # Example values from the warehouse harvest above -- the graph never holds them.
    samples=samples_from_harvest(harvest),
)
_, agent = build_suggestion_agents(settings, registry_kpis=kpis)
message = render_context(context, ask="Propose the use cases this data supports.")
by_identifier = {kpi.identifier: kpi for kpi in kpis}
print(f"  domain prose passages retrieved through K10: {len(context.prose)}")
print(f"  routed through R8: {type(agent.model_provider).__name__}")

drafts = agent.run(message).content
known = [kpi.identifier for kpi in kpis]

# The same drafts, assembled twice against two deployments. One agent run, so any difference
# in the verdicts is the deployment's doing and provably not the model's.
against_bare = k13.assemble(
    drafts,
    configured=BARE,
    known_kpis=known,
    available_tables=tables,
    registry=by_identifier,
)
against_configured = k13.assemble(
    drafts,
    configured=CONFIGURED,
    known_kpis=known,
    available_tables=tables,
    registry=by_identifier,
)

# --- 2. The suggestions ------------------------------------------------------------------

print()
print("2. Suggestions, on the bare deployment")
print("-" * 78)
for line, feasibility in zip(
    k13.readiness_lines(against_bare), against_bare.feasibility
):
    print(f"  {line}")
    print(f"      data: {feasibility.label}")
for table, titles in sorted(against_bare.unlocks.items()):
    print(f"  connect {table} to unlock {len(titles)}: {', '.join(titles)}")
if against_bare.dropped:
    print("  dropped before assembly:")
    for reason in against_bare.dropped:
        print(f"    - {reason}")
if against_bare.notes:
    print(f"  notes: {against_bare.notes}")

shapes = Counter(r.shape.value for r in against_bare.readiness)
print()
print(f"  shapes proposed: {dict(shapes)}")
print(f"  ready on bare: {sum(1 for r in against_bare.readiness if r.ready)}")
print(
    f"  ready on configured: {sum(1 for r in against_configured.readiness if r.ready)}"
)

# --- 3. The trace the plan asks for -------------------------------------------------------

print()
print("3. The top suggestion, traced to the data that made it possible")
print("-" * 78)
by_table = {table.qualified.split(".")[-1].lower(): table for table in harvest.tables}
kpis_by_id = {kpi.identifier.lower(): kpi for kpi in kpis}
if against_bare.suggestions:
    top = against_bare.suggestions[0]
    verdict = against_bare.readiness[0]
    print(f"  {top.title}  [{verdict.shape.value}]")
    print(f"    {top.description}")
    print(f"    rationale: {top.rationale}")
    print("    reads:")
    for name in top.derived_from_tables:
        table = by_table.get(name)
        detail = (
            f"{len(table.columns)} columns, {table.row_count} rows"
            if table
            else "NOT IN THE HARVEST"
        )
        print(f"      {name}: {detail}")
    print("    uses:")
    for identifier in top.kpi_identifiers or ["(no registered KPI)"]:
        kpi = kpis_by_id.get(identifier)
        source = ", ".join(sorted(kpi.source_tables)) if kpi else "NOT IN THE REGISTRY"
        print(f"      {identifier}: over {source}")
    print(f"    shape needs: {', '.join(k13.SHAPE_REQUIREMENTS[verdict.shape])}")
    print(f"    on this deployment: {verdict.rationale}")

# --- 4. The check that matters: no unattended write into an empty deployment ---------------

print()
print("4. Act, on a deployment with nowhere to write")
print("-" * 78)
acts = [
    (suggestion, verdict)
    for suggestion, verdict in zip(against_bare.suggestions, against_bare.readiness)
    if verdict.shape is k13.UseCaseShape.ACT
]
ready_acts = [suggestion.title for suggestion, verdict in acts if verdict.ready]
print(f"  Act suggestions: {len(acts)}")
print(f"  of which ready on the bare deployment: {len(ready_acts)}")
for suggestion, verdict in acts:
    print(
        f"    {suggestion.title}: needs {', '.join(verdict.missing) or 'nothing more'}"
    )
print("  The gate subtracts a configured set from a required one. No prompt asked the")
print("  model whether an unattended write was safe here, and its schema has no field")
print("  in which it could have said so.")

# A check over zero Act suggestions passes for the wrong reason, and a check that cannot
# fail is worth nothing -- so where the agent proposed no Act (which it may legitimately
# never do; section 5 of the prompt tells it to claim the lowest true shape), the same
# assembly path is driven with a control built from the run's OWN top suggestion, promoted
# to Act. It is the real draft with one field changed, so it exercises the real path.
control_verdicts = None
if not acts and against_bare.suggestions:
    seed = drafts.suggestions[0]
    control = k13.UseCaseSuggestionDraft(
        **{**seed.model_dump(), "title": "CONTROL: " + seed.title, "shape": "act"}
    )
    control_set = k13.UseCaseSuggestionDraftSet(suggestions=[control])
    control_bare = k13.assemble(
        control_set, configured=BARE, known_kpis=known, available_tables=tables
    )
    control_configured = k13.assemble(
        control_set, configured=CONFIGURED, known_kpis=known, available_tables=tables
    )
    print()
    print("  The agent proposed no Act, so the check above passed over an empty set.")
    print("  Control -- the same draft, promoted to Act, through the same assembly:")
    for label, report in (("bare", control_bare), ("configured", control_configured)):
        verdict = report.readiness[0]
        print(
            f"    on {label:<11} ready={verdict.ready}  "
            f"missing: {', '.join(verdict.missing) or '-'}"
        )
    control_verdicts = (control_bare.readiness[0], control_configured.readiness[0])

# The gate must actually bite. If the bare deployment is ready for every shape proposed,
# this section proved nothing and the profiles above are wrong.
gate_bit = sum(1 for r in against_bare.readiness if not r.ready)

driver.close()

# --- 5. Verdict ---------------------------------------------------------------------------

print()
print("=" * 78)
failures = []
if not against_bare.suggestions:
    failures.append("the agent proposed nothing at all against a harvest with tables")
if ready_acts:
    failures.append(
        f"{len(ready_acts)} Act suggestion(s) are ready on a deployment with no write "
        f"target, cap or rollback point: {ready_acts}. This is the one verdict that must "
        "never be wrong -- accepting it writes to a customer's systems unattended."
    )
if control_verdicts is not None:
    on_bare, on_configured = control_verdicts
    if on_bare.ready:
        failures.append(
            "the control -- a real draft promoted to Act -- is ready on a deployment with "
            "no write target, cap or rollback point. The gate is not gating."
        )
    if not on_configured.ready:
        failures.append(
            "the control Act is blocked even on the fully configured deployment, needing "
            f"{on_configured.missing}. The requirements table asks for something the "
            "profile does not name, so the gate blocks everything and proves nothing."
        )
if any(not s.derived_from_tables for s in against_bare.suggestions):
    failures.append("a suggestion is traceable to no table")
unknown_kpis = sorted(
    {
        identifier
        for s in against_bare.suggestions
        for identifier in s.kpi_identifiers
        if identifier not in kpis_by_id
    }
)
if unknown_kpis:
    failures.append(
        f"suggestions cite KPIs that are not registered: {unknown_kpis}. Assembly should "
        "have dropped these, so this is a defect in assemble() rather than in the model."
    )
if [s.title for s in against_bare.suggestions] != [
    s.title for s in against_configured.suggestions
]:
    failures.append(
        "the two deployments kept different suggestions. Readiness gates the verdict, not "
        "the list -- a blocked suggestion still tells a customer what configuring would buy."
    )
if gate_bit == 0 and shapes.keys() - {"inform"}:
    failures.append(
        "every suggestion is ready on a deployment configured with one capability, while "
        "shapes above Inform were proposed. The gate did not bite, so section 4 proved "
        "nothing."
    )
if (
    against_bare.suggestions
    and "R-12" not in against_bare.as_contract_list(snapshot).notes
):
    failures.append(
        "the assembled list drops shape and autonomy without saying so. A reader cannot "
        "tell an Alert from an Act, and nothing on the response admits it."
    )

if failures:
    print("K13 does NOT hold:")
    for failure in failures:
        print("  -", failure)
    raise SystemExit(1)
print(
    "Use case suggestion holds. Every suggestion names the tables and registered KPIs it"
)
print(
    "derives from; the shape gate blocked on the bare deployment what it passed on the"
)
print("configured one; and no Act is ready where there is nowhere to write.")
print()
print(f"Shapes this run: {dict(shapes)}.")
if not acts:
    print("No Act was proposed, so that half of section 4 rested on the control above.")
print()
print(
    "What the suggestions are WORTH -- and whether the shapes are honestly claimed -- is"
)
print("the review in sections 2 and 3, not this line. Record it in K13_BASELINE.md.")
