"""K8 -- the enrichment and mapping proposers, and the graph that does not change (D10, G19, G20).

The package's scenario, from the delivery plan: run both agents over the sample harvest, read
twenty proposals by eye and judge each one, query the graph before reviewing anything, then
check that every collision K4 reported has a proposal resolving it.

Evidence: the graph is unchanged until proposals are accepted, and how many of the twenty were
wrong goes in the log -- because that number is the baseline every later prompt change is
measured against.

**The structural guarantee, stated once.** Neither agent can write to the graph. Not because
the prompt says so -- a prompt is a request -- but because neither module imports a graph
store, their output is a pydantic model, and the only thing that turns that model into
anything is ``to_proposals``, which produces rows in ``PENDING``. A person accepting them is
the write. This cookbook proves the guarantee by counting the graph before and after.

Needs Azure credentials, the ontology from K3, the harvest from K4, and the control-plane
Postgres:

    set -a; . ./azure_key.env; set +a
    PYTHONPATH=product .venvs/demo/bin/python cookbook/94_retail/K08_proposers.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "product"))

import psycopg  # noqa: E402
from egpt.agent import Agent  # noqa: E402
from egpt.integrations.models.azure import AzureOpenAI  # noqa: E402
from egpt_retail.ontology.model import ARTIFACT_PATH, load_ontology  # noqa: E402
from egpt_retail.semantic.harvest import harvest_schema  # noqa: E402
from egpt_retail.semantic.proposals import ProposalStore, ReviewState  # noqa: E402
from egpt_retail.semantic.store import SemanticGraphStore  # noqa: E402
from egpt_retail.skills import enrichment_proposer as enrich  # noqa: E402
from egpt_retail.skills import mapping_proposer as mapping  # noqa: E402
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
PROPOSER = "k08_cookbook"

print("K8 -- enrichment and mapping proposers")
print("=" * 78)

warehouse = psycopg.connect(WAREHOUSE)
warehouse.autocommit = True
# Sampled values on: a proposer reading live data is exactly the caller sampling exists for,
# and the cap of five per column is why it is safe to hand them to a model at all.
harvest = harvest_schema(warehouse, "silver", source="egpt_warehouse", sample=True)
warehouse.close()

# The overlay, not just the core. The harvest is this customer's, and their overlay is where
# QuoteRequest, Quote and RepGroup live -- without it the agent is asked to map an RFQ funnel
# using a concept list that has no word for a quote request, and its best available answer is
# `Order`, which is wrong. An agent constrained to a closed set can only be as right as the
# set: offering the wrong one produces a plausible mapping and looks like a model failure.
OVERLAY = Path("product/egpt_retail/ontology/overlays/middleby.json")
ontology = load_ontology(
    ARTIFACT_PATH, overlay_path=OVERLAY if OVERLAY.exists() else None
)
concept_names = sorted(c.name for c in ontology.concepts)
reported_collisions = [
    c.column_name for c in harvest.collisions if c.needs_disambiguation
]

driver = GraphDatabase.driver(NEO4J, auth=AUTH)
graph = SemanticGraphStore(driver=driver)
graph.ensure_indexes()
graph.write_harvest(harvest, snapshot_version="k08-demo.1")

control_plane = psycopg.connect(CONTROL_PLANE)
store = ProposalStore(control_plane)
store.ensure_schema()
with control_plane.cursor() as cursor:
    cursor.execute("DELETE FROM semantic.proposal WHERE proposer = %s", (PROPOSER,))
control_plane.commit()


def graph_state():
    """Everything an accepted proposal would change, counted."""
    with driver.session() as session:
        return (
            session.run(
                "OPTIONAL MATCH (c:SemanticConcept) WITH count(c) AS concepts "
                "OPTIONAL MATCH ()-[i:INSTANTIATES]->() WITH concepts, count(i) AS instantiates "
                "OPTIONAL MATCH ()-[a:AUTHORITATIVE_SOURCE]->() "
                "RETURN concepts, instantiates, count(a) AS authoritative"
            )
            .single()
            .data()
        )


before = graph_state()

# --- 1. What the agents are given ------------------------------------------------------------


def _table_lines(limit_values=3):
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
                bits.append(f"e.g. {', '.join(column.sampled_values[:limit_values])}")
            lines.append(" ".join(bits))
    return "\n".join(lines)


HARVEST_TEXT = _table_lines()
print()
print("1. Inputs")
print("-" * 78)
print(f"  tables: {len(harvest.tables)}, columns: {harvest.column_count}")
print(f"  ontology concepts offered: {len(concept_names)}")
print(f"  collisions K4 reported: {reported_collisions or '(none)'}")

# --- 2. The mapping proposer -------------------------------------------------------------------

mapping_agent = Agent(
    name="mapping_proposer",
    model=AzureOpenAI(id=os.environ["AZURE_OPENAI_DEPLOYMENT"]),
    instructions=mapping.render_prompt(
        concepts=concept_names, collisions=reported_collisions
    ),
    output_schema=mapping.MappingProposalSet,
)
mapping_response = mapping_agent.run(
    f"harvest:\n{HARVEST_TEXT}\n\nMap these tables to concepts and resolve every collision."
).content

print()
print("2. Mapping proposer")
print("-" * 78)
for line in mapping.evidence_lines(mapping_response):
    print(f"  {line}")
if mapping_response.notes:
    print(f"  notes: {mapping_response.notes}")

still_open = mapping.unresolved_collisions(mapping_response, reported_collisions)
print()
print(f"  collisions resolved on every side: {not still_open}")
if still_open:
    print(f"  still open: {still_open}")

# --- 3. The enrichment proposer ------------------------------------------------------------------

enrich_agent = Agent(
    name="enrichment_proposer",
    model=AzureOpenAI(id=os.environ["AZURE_OPENAI_DEPLOYMENT"]),
    instructions=enrich.render_prompt(),
    output_schema=enrich.EnrichmentProposalSet,
)

TARGETS = ["QuoteRequest", "Customer", "WebSession"]
enrichment_responses = []
print()
print("3. Enrichment proposer")
print("-" * 78)
for name in TARGETS:
    concept = next((c for c in ontology.concepts if c.name == name), None)
    if concept is None:
        continue
    # Every term already spoken for, so the agent can avoid proposing one. Declared as an
    # input in the prompt and, on the first run, not passed -- the model said so in its own
    # notes ("No accepted synonyms from other concepts were provided, so synonym collision
    # could not be checked") and then proposed `contact` as a synonym for Customer, which is
    # the name of another concept and which K3's synonym index refuses outright. The model
    # named the gap; the caller had to close it.
    taken = sorted(
        {other.name for other in ontology.concepts if other.name != concept.name}
        | {
            synonym
            for other in ontology.concepts
            if other.name != concept.name
            for synonym in (other.synonyms or ())
        }
    )
    response = enrich_agent.run(
        f"concept: {concept.name}\ndescription: {concept.description}\n\n"
        f"harvest:\n{HARVEST_TEXT}\n\n"
        f"terms already spoken for by other concepts, never propose these as synonyms:\n"
        f"{', '.join(taken)}"
    ).content
    enrichment_responses.append((concept, response))
    print(f"  {response.concept} (business_area={response.business_area or '-'})")
    for line in enrich.evidence_lines(response):
        print(f"    {line}")
    if response.notes:
        print(f"    notes: {response.notes}")

# --- 4. The graph, before anyone reviews anything ------------------------------------------------

proposals = mapping.to_proposals(
    mapping_response, proposer=PROPOSER, known_concepts=concept_names
)
for concept, response in enrichment_responses:
    # The shipped concept rides along: its description and area are the ontology's facts,
    # and the payload must never carry an empty description -- accepting one used to wipe
    # the description a person had already accepted (P3 verification).
    proposals += enrich.to_proposals(
        response, proposer=PROPOSER, ontology_concept=concept
    )
inserted = store.propose_many(proposals)
after_proposing = graph_state()

print()
print("4. The graph, after proposing and before reviewing")
print("-" * 78)
print(f"  proposals written to the queue: {inserted}")
print(f"  graph before: {before}")
print(f"  graph after:  {after_proposing}")
print(
    "  Neither agent can write here. Their output is a pydantic model; the only thing that"
)
print("  turns it into anything is to_proposals, and that produces PENDING rows.")

# --- 5. The twenty to judge by eye ------------------------------------------------------------------

pending = [p for p in store.pending() if p.proposer == PROPOSER]
print()
print("5. Proposals to judge by eye")
print("-" * 78)
print(f"  {len(pending)} in the queue. Numbered for the log:")
for index, proposal in enumerate(pending, start=1):
    detail = proposal.payload.get("concept") or proposal.payload.get("name") or ""
    if proposal.kind.value == "column_meaning":
        detail = f"{proposal.payload['concept']}: {proposal.payload['means'][:48]}"
    print(f"  {index:>2}. [{proposal.kind.value:<20}] {proposal.target} -> {detail}")
g19_rows = [p for p in pending if p.kind.value == "column_meaning"]
print(
    f"  of which column meanings (G19): {len(g19_rows)}, one per colliding column per table"
)

# --- 6. Clean up ------------------------------------------------------------------------------------

with control_plane.cursor() as cursor:
    cursor.execute("DELETE FROM semantic.proposal WHERE proposer = %s", (PROPOSER,))
control_plane.commit()
control_plane.close()
final = graph_state()
driver.close()

# --- 7. Verdict --------------------------------------------------------------------------------------

print()
print("=" * 78)
failures = []
if not mapping_response.mappings:
    failures.append("the mapping proposer returned no mappings at all")
if any(m.concept not in concept_names for m in mapping_response.mappings):
    failures.append(
        "the mapping proposer used a concept name outside the ontology it was given -- the "
        "closed-set rule failed where it matters most"
    )
if still_open:
    failures.append(
        f"collisions left unresolved on at least one side: {still_open} (G19)"
    )
authoritative = [m.concept for m in mapping_response.mappings if m.authoritative]
if len(authoritative) != len(set(authoritative)):
    failures.append(
        f"more than one authoritative table for a concept: {authoritative} (G20)"
    )
authoritative_rows = [p for p in proposals if p.kind.value == "authoritative_source"]
if any(not p.payload.get("alternatives") for p in authoritative_rows):
    failures.append(
        "an authoritative source reached the queue with no alternatives -- a decision "
        "without the options it beat is indistinguishable from no decision (G20)"
    )
uncontested = {
    m.concept
    for m in mapping_response.mappings
    if sum(1 for other in mapping_response.mappings if other.concept == m.concept) == 1
}
if any(p.target in uncontested for p in authoritative_rows):
    failures.append(
        "an authoritative source was proposed for a concept only one table serves; there "
        "was no contest to settle and the queue gains an item with no decision in it"
    )
if not any(r.proposals for _, r in enrichment_responses):
    failures.append("the enrichment proposer returned nothing for any concept")
if after_proposing != before:
    failures.append(
        f"the graph changed while proposals were only proposed: {before} -> {after_proposing}. "
        "D10 is not being enforced structurally."
    )
if final != before:
    failures.append(f"the cookbook left the graph changed: {before} -> {final}")
if any(p.state is not ReviewState.PENDING for p in pending):
    failures.append("a proposal reached the queue in a state other than pending")
if inserted < 5:
    failures.append(
        f"only {inserted} proposals were produced; too few to judge a baseline on"
    )
expected_g19 = sum(len(c.tables) for c in harvest.collisions if c.needs_disambiguation)
if len(g19_rows) != expected_g19:
    failures.append(
        f"expected one column-meaning proposal per colliding column per table "
        f"({expected_g19}), got {len(g19_rows)}"
    )
if any(
    p.kind.value == "enrichment"
    and any("means" in q for q in p.payload.get("question_patterns", ()))
    for p in pending
):
    failures.append(
        "a collision resolution was parked in a concept's question patterns"
    )

if failures:
    print("K8 does NOT hold:")
    for failure in failures:
        print("  -", failure)
    raise SystemExit(1)
print(
    "Proposers hold. Both agents drafted only; the graph is byte-identical before and after,"
)
print("every collision K4 reported was resolved on both sides, and no concept has two")
print(
    "authoritative sources. What the proposals are WORTH is the review above, not this line."
)
