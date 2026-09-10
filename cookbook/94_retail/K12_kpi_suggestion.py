"""K12 -- candidate KPIs proposed from the connected data, with feasibility the model cannot fake.

The package's scenario, from the delivery plan: run against the sample deployment, check every
suggestion's feasibility against what the feasibility executor independently says, then run
twice on unchanged data.

Evidence: no suggestion carries a feasibility the executor disagrees with, because feasibility
is a set difference and never the model's opinion. Two runs propose substantially the same set;
wild variation on identical input means the prompt is underspecified.

**The guarantee here is structural, not a promise in a prompt.** The agent's schema has no
feasibility field -- `KpiSuggestionDraft` forbids extra keys, so it cannot even smuggle one --
and `assemble` calls `assess_feasibility` to stamp the verdict. There is no argument through
which a model's opinion could reach that function. A prompt that asked nicely would be one
prompt edit away from not asking.

**What reaches the screen is the contract's own type.** `assemble` returns
`egpt_retail.api.models.KpiSuggestion`, generated from the OpenAPI schema the discovery screen
renders, so a field the agent invents is a construction error here rather than a blank cell
in front of a customer.

Needs Azure credentials, the ontology from K3, the harvest from K4/K5, and Neo4j:

    set -a; . ./azure_key.env; set +a
    PYTHONPATH=product .venvs/demo/bin/python cookbook/94_retail/K12_kpi_suggestion.py
"""

import json
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "product"))

import psycopg  # noqa: E402
from egpt.knowledge.knowledge import Knowledge  # noqa: E402
from egpt.metrics import ProviderMode  # noqa: E402
from egpt.vectordb.pgvector import PgVector  # noqa: E402
from egpt.vectordb.search import SearchType  # noqa: E402
from egpt_retail.app.settings import RetailSettings  # noqa: E402
from egpt_retail.knowledge.embedding import resolve_knowledge_embedder  # noqa: E402
from egpt_retail.ontology.store import OntologyStore  # noqa: E402
from egpt_retail.suggestions import build_suggestion_agents, gather_context  # noqa: E402
from egpt_retail.suggestions.context import (  # noqa: E402
    render_context,
    samples_from_harvest,
)
from egpt_retail.contracts.kpi import assess_feasibility_with_joins  # noqa: E402
from egpt_retail.contracts.semantic import available_tables  # noqa: E402
from egpt_retail.kpi.store import KpiRegistryStore  # noqa: E402
from egpt_retail.semantic.harvest import harvest_schema  # noqa: E402
from egpt_retail.semantic.store import SemanticGraphStore  # noqa: E402
from egpt_retail.skills import kpi_suggestion as k12  # noqa: E402
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
CATALOGUE = Path("product/egpt_retail/kpi/retail_metrics.json")

print("K12 -- KPI suggestion")
print("=" * 78)

warehouse = psycopg.connect(WAREHOUSE)
warehouse.autocommit = True
harvest = harvest_schema(warehouse, "silver", source="egpt_warehouse", sample=True)
warehouse.close()

driver = GraphDatabase.driver(NEO4J, auth=AUTH)
graph = SemanticGraphStore(driver=driver)
graph.ensure_indexes()
graph.write_harvest(harvest, snapshot_version="k12-demo.1")

control_plane = psycopg.connect(CONTROL_PLANE)
registry = KpiRegistryStore(control_plane)
registry.ensure_schema()

tables = available_tables(graph)
snapshot = graph.snapshot().version
# The join rulings review has recorded, so a suggestion over two id spaces is blocked HERE
# and not only after adoption. Round 1 of PR 208 caught the suggestion-time stamp using the
# plain set difference while the registry used the join-aware one.
ORACLE = graph.unjoinable_oracle()
existing = [kpi.identifier for kpi in registry.list()]
catalogue = json.loads(CATALOGUE.read_text())["metrics"]

# --- 1. What the agent is given ---------------------------------------------------------------


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
            if column.sampled_values:
                bits.append(f"e.g. {', '.join(column.sampled_values[:3])}")
            lines.append(" ".join(bits))
    return "\n".join(lines)


# The ontology's own area per concept. K9 learned that an agent's answer here varies run to
# run -- three baseline runs placed WebSession in customer, operations and marketing -- while
# the ontology has one answer. The agent still proposes an area for a concept-less metric.
AREA_OF_CONCEPT = {
    name: concept.business_area
    for name in graph.list_concepts()
    if (concept := graph.get_concept(name)) and concept.business_area
}

concepts_text = "\n".join(
    f"- {name}: {(graph.get_concept(name).description if graph.get_concept(name) else '')}"
    for name in graph.list_concepts()
)

print()
print("1. Inputs")
print("-" * 78)
print(f"  tables in the semantic layer: {len(tables)}   snapshot: {snapshot}")
print(f"  concepts: {len(graph.list_concepts())}")
print(f"  curated catalogue: {len(catalogue)} metric definitions")
print(f"  already registered: {len(existing)}")


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
knowledge = Knowledge(
    vector_db=PgVector(
        table_name=settings.retail_knowledge_table,
        db_url=settings.retail_db_url,
        search_type=SearchType.hybrid,
        embedder=embedder.embedder,
    )
)
ontology = OntologyStore(driver=driver, embedder=embedder.embedder)
context = gather_context(
    graph,
    knowledge=knowledge,
    ontology=ontology,
    tenant_id="middleby",
    # Example values from the warehouse harvest above -- the graph never holds them.
    samples=samples_from_harvest(harvest),
)
agent, _ = build_suggestion_agents(
    settings,
    existing_kpis=existing,
    registry_kpis=registry.list(),
    catalogue=catalogue,
)
message = render_context(context, ask="Propose the KPIs this data supports.")
print(f"  domain prose passages retrieved through K10: {len(context.prose)}")
print(f"  routed through R8: {type(agent.model_provider).__name__}")


def run_once():
    drafts = agent.run(message).content
    return drafts, k12.assemble(
        drafts,
        available_tables=tables,
        snapshot_version=snapshot,
        known_identifiers=existing,
        area_of_concept=AREA_OF_CONCEPT,
        known_unjoinable=ORACLE,
    )


# --- 2. The suggestions -------------------------------------------------------------------------

first_drafts, first = run_once()
print()
print("2. Suggestions")
print("-" * 78)
for suggestion in first.suggestions:
    missing = ", ".join(suggestion.feasibility.missing_tables) or "-"
    badge = (
        "catalogue" if first.catalogue_source.get(suggestion.identifier) else "inferred"
    )
    print(
        f"  [{suggestion.feasibility.verdict.value:<7}] {suggestion.identifier:<34} "
        f"{suggestion.confidence.value:<7} {badge:<9} tables: "
        f"{', '.join(suggestion.derived_from_tables)}"
    )
    print(f"      {suggestion.rationale[:88]}")
    if suggestion.feasibility.verdict.value != "ready":
        print(f"      missing: {missing}")
if first.dropped:
    print("  dropped before assembly:")
    for reason in first.dropped:
        print(f"    - {reason}")
if first.notes:
    print(f"  notes: {first.notes}")

# --- 3. The verdict the model could not have written ---------------------------------------------

print()
print("3. Every verdict, checked against the executor independently")
print("-" * 78)
disagreements = []
for suggestion in first.suggestions:
    draft = next(
        d
        for d in first_drafts.suggestions
        if d.identifier.lower() == suggestion.identifier
    )
    independent = assess_feasibility_with_joins(
        k12._looks_like_a_definition(draft), tables, ORACLE
    )
    agrees = (
        suggestion.feasibility.verdict.value == independent.verdict.value
        and sorted(suggestion.feasibility.missing_tables)
        == sorted(independent.missing_tables)
    )
    if not agrees:
        disagreements.append(suggestion.identifier)
print(f"  suggestions checked: {len(first.suggestions)}")
print(f"  disagreements with the executor: {len(disagreements)}")
print(
    "  The draft schema has no feasibility field and forbids extra keys, so the model"
)
print("  could not have expressed a verdict even if the prompt had invited one.")

# --- 4. Three runs on unchanged data ----------------------------------------------------------------


def content_key(suggestion):
    """What a suggestion *is*, independent of what it was named.

    The plan's evidence line is that two runs "propose substantially the same set" -- a set of
    metrics, not of strings. An identifier is one rendering of a metric and a model renders it
    two ways on two runs; what must be stable is which metric, over which tables, at which
    shape and grain.

    Coarse on purpose, and worth stating: three readings of one column -- `rage_clicks`,
    `rage_clicks_per_session`, `sessions_with_rage_clicks` -- share a key and collapse to one.
    So this measures COVERAGE, which buckets got a metric, not how many per bucket. The
    cardinality line below is what shows the other half.
    """
    return (
        suggestion.concept or suggestion.business_area,
        tuple(sorted(suggestion.derived_from_tables)),
        suggestion.result_shape.value,
        suggestion.grain,
    )


# Three runs, not two. The plan's evidence line is a judgement -- "wild variation on
# identical input means the prompt is underspecified" -- and two samples cannot tell wild
# from ordinary. Three gives a *stable core*: the metrics every run proposed. Pairwise
# overlap between two runs of this agent measured 67%, 100% and 40% on three consecutive
# pairs, so a threshold on it would have been a coin toss dressed as a gate.
#
# What is asserted below is the guarantee (a non-empty stable core, the same areas covered).
# What is recorded is the spread, for K12_BASELINE.md, because how good the suggestions are
# is a judgement for the owner and not something this script can assert.
runs = [first]
run_drafts = [first_drafts]
for _ in range(2):
    drafts, report = run_once()
    runs.append(report)
    run_drafts.append(drafts)

id_sets = [{s.identifier for s in r.suggestions} for r in runs]
key_sets = [{content_key(s) for s in r.suggestions} for r in runs]
core_ids = set.intersection(*id_sets) if id_sets else set()
core_keys = set.intersection(*key_sets) if key_sets else set()
union_keys = set.union(*key_sets) if key_sets else set()
areas_per_run = [{s.business_area for s in r.suggestions} for r in runs]

print()
print("4. Three runs on identical input")
print("-" * 78)
for index, (ids, keys) in enumerate(zip(id_sets, key_sets), start=1):
    print(f"  run {index}: {len(ids)} suggestions, {len(keys)} distinct metric buckets")
print(f"  stable core (identifiers in all three): {len(core_ids)}")
print(f"    {', '.join(sorted(core_ids)) or '(none survived all three)'}")
union_ids = set.union(*id_sets) if id_sets else set()
print(f"  core / union of identifiers: {len(core_ids) / max(len(union_ids), 1):.0%}")
print(f"  buckets covered by all three: {len(core_keys)} of {len(union_keys)}")
# Same identifier, different grain or result_shape across runs. Sharper than a naming
# difference: grain decides what a number means, so one metric with two grains is two
# numbers wearing one name, and a customer comparing them over time sees neither.
drift = sorted(
    identifier
    for identifier in core_ids
    if len(
        {
            content_key(s)
            for run in runs
            for s in run.suggestions
            if s.identifier == identifier
        }
    )
    > 1
)
print(f"  same identifier, different grain/shape across runs: {len(drift)}")
if drift:
    print(f"    {', '.join(drift)}")
sizes = [len(ids) for ids in id_sets]
print(f"  cardinality: {sizes} ({max(sizes) / max(min(sizes), 1):.1f}x spread)")
print(f"  business areas per run: {[sorted(a) for a in areas_per_run]}")
print("  Areas are derived from the ontology, not proposed -- so they are identical by")
print(
    "  construction. Cardinality is not: the agent agrees on WHAT the data supports and"
)
print("  disagrees on how finely to slice it. That is this prompt's open weakness.")

# --- 5. Where the suggestions came from ------------------------------------------------------------

cited = [
    s
    for s in first.suggestions
    if any(
        d.catalogue_source
        for d in first_drafts.suggestions
        if d.identifier.lower() == s.identifier
    )
]
print()
print("5. Traceability")
print("-" * 78)
print(f"  citing a curated catalogue metric: {len(cited)} of {len(first.suggestions)}")
areas = Counter(s.business_area for s in first.suggestions)
print(f"  business areas covered: {dict(areas)}")
untraceable = [s.identifier for s in first.suggestions if not s.derived_from_tables]
print(f"  suggestions naming no table: {len(untraceable)}")
proposals = first.as_proposals()
print(
    f"  adoptable as KPI proposals (carry a formula): {len(proposals)} of "
    f"{len(first.suggestions)}; without a formula: {len(first.without_formula)}"
)
print("  Not filed here; K13_suggest_on_connect.py files and cleans up.")

control_plane.close()
driver.close()

# --- 6. Verdict ---------------------------------------------------------------------------------------

print()
print("=" * 78)
failures = []
if not first.suggestions:
    failures.append("the agent proposed nothing at all against a harvest with tables")
if disagreements:
    failures.append(
        f"{len(disagreements)} suggestion(s) carry a feasibility the executor disagrees with: "
        f"{disagreements}. Feasibility is a set difference and this is the one thing it "
        "cannot be."
    )
if untraceable:
    failures.append(f"suggestions traceable to no table: {untraceable}")
if any(s.identifier in existing for s in first.suggestions):
    failures.append("a suggestion duplicates a KPI already in the registry")
if any(s.proposed_by != k12.SKILL for s in first.suggestions):
    failures.append(
        "a suggestion is not stamped with the skill and version that made it"
    )
if any(s.feasibility.semantic_snapshot != snapshot for s in first.suggestions):
    failures.append("a verdict does not name the snapshot it was computed against")
if any(not r.suggestions for r in runs):
    failures.append("a run proposed nothing; the others may have been luck")
if not core_ids:
    failures.append(
        "three runs on identical input share no suggestion at all -- there is no stable core "
        "to baseline against, which is what K12's evidence line calls an underspecified prompt"
    )
if drift:
    failures.append(
        f"the same identifier carries a different grain or result_shape across runs: {drift}. "
        "Grain decides what a number means, so one metric with two grains is two numbers "
        "wearing one name."
    )
if len({frozenset(a) for a in areas_per_run}) != 1:
    failures.append(
        f"business areas differ across runs: {[sorted(a) for a in areas_per_run]}. They are "
        "derived from the ontology, so they cannot legitimately vary."
    )

if failures:
    print("K12 does NOT hold:")
    for failure in failures:
        print("  -", failure)
    raise SystemExit(1)
print(
    "Suggestion holds. Every verdict came from the executor and none disagrees with an"
)
print(
    "independent assessment; every suggestion names the tables it derives from; and two"
)
print("runs on identical input propose a stable core.")
print()
print(
    "What the suggestions are WORTH is the review above, not this line -- record it in"
)
print("K12_BASELINE.md the way K8's is recorded.")
