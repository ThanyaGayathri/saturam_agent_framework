"""K7 -- the proposals table, and the review between an agent and the graph (G5, D10).

The package's scenario, from the delivery plan: insert twenty proposals of two kinds, accept
twelve in one action, reject the rest, query the graph, then try to accept an already-rejected
proposal.

Evidence: exactly twelve appear in the graph and the eight rejected appear nowhere; and
re-accepting a rejected proposal is refused rather than silently applied.

Two checks beyond the plan's, because both are failure modes that would make the review
theatre rather than control:

- The graph is queried **before** any review, not only after. If proposing wrote to the graph,
  a test that only looks afterwards would pass anyway and D10 would be decorative.
- Every proposer is re-run at the end. An agent has no memory of a rejection; without
  content-derived ids the nightly pass would raise all eight rejected proposals again and the
  reviewer's decision would last exactly one day.

The proposals carry recognisable names (``K7Concept01``, a ``k7_probe`` marker on join
predicates) so the cookbook can remove exactly what it added and prove the graph is back where
it started. Deltas are asserted too, so a stray leftover is caught either way.

Needs the warehouse from K4, a Neo4j, and the control-plane Postgres:

    PYTHONPATH=product .venvs/demo/bin/python cookbook/94_retail/K07_proposals_and_review.py
"""

import itertools
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "product"))

import psycopg  # noqa: E402
from egpt_retail.semantic.harvest import harvest_schema  # noqa: E402
from egpt_retail.semantic.proposals import (  # noqa: E402
    PayloadError,
    Proposal,
    ProposalKind,
    ProposalStore,
    ReviewState,
)
from egpt_retail.semantic.store import SemanticGraphStore  # noqa: E402
from neo4j import GraphDatabase  # noqa: E402

WAREHOUSE = os.environ.get(
    "EGPT_RETAIL_WAREHOUSE_DSN", "postgresql://ai:ai@localhost:5532/egpt_warehouse"
)
CONTROL_PLANE = os.environ.get(
    "EGPT_RETAIL_CP_DSN", "postgresql://ai:ai@localhost:5532/ai"
)
NEO4J = os.environ.get("EGPT_ONTOLOGY_NEO4J_URI", "neo4j://localhost:7701")
AUTH = ("neo4j", os.environ.get("EGPT_RETAIL_NEO4J_PASSWORD", "egptretail"))

PROPOSER = "k7_cookbook_enricher"
REVIEWER = "kiruthiga"
MARKER = "k7_probe"

print("K7 -- proposals, review, and the one path into the graph")
print("=" * 78)

# --- setup: a harvest in the graph so joins have real tables to connect -------------------

warehouse = psycopg.connect(WAREHOUSE)
warehouse.autocommit = True
harvest = harvest_schema(warehouse, "silver", source="egpt_warehouse", sample=False)
warehouse.close()

driver = GraphDatabase.driver(NEO4J, auth=AUTH)
graph = SemanticGraphStore(driver=driver)
graph.ensure_indexes()
graph.write_harvest(harvest, snapshot_version="k7-demo.1")

control_plane = psycopg.connect(CONTROL_PLANE)
proposals = ProposalStore(control_plane)
proposals.ensure_schema()

# A clean slate for this cookbook only -- never the whole table, which may hold other work.
with control_plane.cursor() as cursor:
    cursor.execute("DELETE FROM semantic.proposal WHERE proposer = %s", (PROPOSER,))
control_plane.commit()


def count_in_graph():
    """The two things this cookbook can add to the graph, counted."""
    with driver.session() as session:
        concepts = session.run(
            "MATCH (c:SemanticConcept) WHERE c.name STARTS WITH 'K7Concept' "
            "RETURN count(c) AS n"
        ).single()["n"]
        joins = session.run(
            "MATCH ()-[j:JOINS_TO]->() WHERE j.on CONTAINS $marker RETURN count(j) AS n",
            marker=MARKER,
        ).single()["n"]
    return concepts, joins


TABLES = sorted({t.name for t in harvest.tables})

# --- 1. Twenty proposals, two kinds ------------------------------------------------------

batch = []
for index in range(12):
    batch.append(
        Proposal(
            kind=ProposalKind.ENRICHMENT,
            target=f"K7Concept{index:02d}",
            payload={
                "name": f"K7Concept{index:02d}",
                "description": f"A concept proposed by an agent, number {index}.",
                "synonyms": [f"k7 synonym {index}", f"k7 alias {index}"],
                "business_area": "customer" if index % 2 else "marketing",
            },
            proposer=PROPOSER,
        )
    )
# Distinct table pairs, so "four accepted joins" means four edges without the reader having
# to reason about which pairs coincide. There are only five silver tables, so a rotating
# window would reuse pairs -- which is how this cookbook found that `write_join` merged on the
# pair alone and silently collapsed two accepted proposals into one edge.
PAIRS = list(itertools.combinations(TABLES, 2))
for index in range(8):
    left, right = PAIRS[index]
    batch.append(
        Proposal(
            kind=ProposalKind.JOIN,
            target=f"{left}->{right}",
            payload={
                "left_table": left,
                "right_table": right,
                "on": f"{left}.{MARKER}_{index} = {right}.{MARKER}_{index}",
                "cardinality": "many-to-one",
            },
            proposer=PROPOSER,
        )
    )

new_count = proposals.propose_many(batch)
pending = proposals.pending()
mine = [p for p in pending if p.proposer == PROPOSER]

print()
print("1. Twenty proposals of two kinds")
print("-" * 78)
print(f"  inserted: {new_count}")
print(f"  pending, this proposer: {len(mine)}")
by_kind = {}
for proposal in mine:
    by_kind[proposal.kind.value] = by_kind.get(proposal.kind.value, 0) + 1
for kind, count in sorted(by_kind.items()):
    print(f"    {kind:<22} {count}")

# --- 2. The graph before anyone reviews anything ------------------------------------------

before_concepts, before_joins = count_in_graph()
print()
print("2. The graph, before any review")
print("-" * 78)
print(f"  proposed concepts present: {before_concepts}")
print(f"  proposed joins present:    {before_joins}")
print(
    "  Proposing is not writing. This is the check that makes D10 more than a comment."
)

# --- 3. Accept twelve in one action, reject eight in another ------------------------------

enrichment = [p.identifier for p in mine if p.kind is ProposalKind.ENRICHMENT]
joins = [p.identifier for p in mine if p.kind is ProposalKind.JOIN]

# Mixed on purpose: eight enrichments and four joins accepted, four of each rejected. If the
# split followed kind, a store that decided by kind rather than by decision would still pass.
accepted_ids = enrichment[:8] + joins[:4]
rejected_ids = enrichment[8:] + joins[4:]

accept_outcome = proposals.accept(accepted_ids, REVIEWER, note="reviewed in one pass")
reject_outcome = proposals.reject(
    rejected_ids, REVIEWER, note="not this customer's language"
)

print()
print("3. One accept action, one reject action")
print("-" * 78)
print(f"  accept: {accept_outcome}")
print(f"  reject: {reject_outcome}")

# --- 4. Only accepted rows reach the graph -------------------------------------------------

applied = proposals.apply_accepted(graph)
after_concepts, after_joins = count_in_graph()

print()
print("4. Applying, and what landed")
print("-" * 78)
print(f"  proposals applied: {len(applied)}")
print(f"  concepts in graph: {after_concepts} (accepted enrichments: 8)")
print(f"  joins in graph:    {after_joins} (accepted joins: 4)")
print(f"  total in graph:    {after_concepts + after_joins} of {len(batch)} proposed")

rejected_targets = {
    p.target
    for p in proposals.list(state=ReviewState.REJECTED)
    if p.proposer == PROPOSER
}
with driver.session() as session:
    leaked = session.run(
        "MATCH (c:SemanticConcept) WHERE c.name IN $names RETURN count(c) AS n",
        names=sorted(rejected_targets),
    ).single()["n"]
print(f"  rejected proposals findable in the graph: {leaked}")

# --- 5. Re-accepting a rejected proposal ---------------------------------------------------

already_rejected = rejected_ids[0]
retry = proposals.accept([already_rejected], "someone_else")
print()
print("5. Trying to accept an already-rejected proposal")
print("-" * 78)
print(f"  outcome: {retry}")
for refusal in retry.refused:
    print(f"    {refusal}")
    print(f"      current state: {refusal.current_state.value}")

after_retry = proposals.apply_accepted(graph)
retry_concepts, retry_joins = count_in_graph()
print(f"  proposals applied on the follow-up run: {len(after_retry)}")
print(
    f"  graph unchanged: {(retry_concepts, retry_joins) == (after_concepts, after_joins)}"
)

# --- 6. The nightly re-run ------------------------------------------------------------------

second_run = proposals.propose_many(batch)
still_rejected = len(
    [p for p in proposals.list(state=ReviewState.REJECTED) if p.proposer == PROPOSER]
)
print()
print("6. The proposer runs again, as it will every night")
print("-" * 78)
print(f"  proposals inserted on the second run: {second_run}")
print(f"  still rejected:                       {still_rejected}")
print(
    "  An id derived from content means a rejection survives the agent that forgot it."
)

# --- 7. What the store refuses outright -----------------------------------------------------

print()
print("7. Refused outright")
print("-" * 78)
refusals = []
try:
    proposals.accept([enrichment[0]], "   ")
except PayloadError as error:
    refusals.append("blank reviewer")
    print(f"  blank reviewer: {str(error)[:72]}...")
try:
    Proposal(
        kind=ProposalKind.ENRICHMENT, target="x", payload={"name": "x"}, proposer=""
    )
except PayloadError:
    refusals.append("blank proposer")
    print("  blank proposer: refused at construction")
try:
    proposals.propose(
        Proposal(
            kind=ProposalKind.JOIN,
            target="a->b",
            payload={"left_table": "a"},
            proposer=PROPOSER,
        )
    )
except PayloadError as error:
    refusals.append("malformed payload")
    print(f"  malformed payload: {str(error)[:72]}...")

# --- 8. Clean up ------------------------------------------------------------------------------

with driver.session() as session:
    session.run(
        "MATCH (c:SemanticConcept) WHERE c.name STARTS WITH 'K7Concept' DETACH DELETE c"
    )
    session.run(
        "MATCH ()-[j:JOINS_TO]->() WHERE j.on CONTAINS $marker DELETE j", marker=MARKER
    )
with control_plane.cursor() as cursor:
    cursor.execute("DELETE FROM semantic.proposal WHERE proposer = %s", (PROPOSER,))
control_plane.commit()
cleaned = count_in_graph()
control_plane.close()
driver.close()

print()
print("8. Cleaned up")
print("-" * 78)
print(f"  concepts and joins left behind: {cleaned}")

# --- 9. Verdict --------------------------------------------------------------------------------

print()
print("=" * 78)
failures = []
if new_count != 20:
    failures.append(f"expected 20 proposals inserted, got {new_count}")
if len(mine) != 20:
    failures.append(f"expected 20 pending, got {len(mine)}")
if (before_concepts, before_joins) != (0, 0):
    failures.append(
        "a proposal reached the graph before anyone reviewed it -- D10 is not enforced"
    )
if len(accept_outcome.decided) != 12 or not accept_outcome.all_applied:
    failures.append(f"the bulk accept did not decide exactly twelve: {accept_outcome}")
if len(reject_outcome.decided) != 8 or not reject_outcome.all_applied:
    failures.append(f"the bulk reject did not decide exactly eight: {reject_outcome}")
if len(applied) != 12:
    failures.append(f"expected twelve proposals applied, got {len(applied)}")
if (after_concepts, after_joins) != (8, 4):
    failures.append(
        f"expected 8 concepts and 4 joins in the graph, got {after_concepts} and {after_joins}"
    )
if leaked:
    failures.append(f"{leaked} rejected proposals are in the graph and must not be")
if retry.decided:
    failures.append("an already-rejected proposal was accepted on a second try")
if not retry.refused or retry.refused[0].current_state is not ReviewState.REJECTED:
    failures.append("the refusal did not say the proposal was already rejected")
if retry.refused and not retry.refused[0].decided_by:
    failures.append("the refusal did not name who made the standing decision")
if after_retry:
    failures.append(
        "the follow-up apply wrote something; a refused accept still reached the graph"
    )
if (retry_concepts, retry_joins) != (after_concepts, after_joins):
    failures.append("the graph changed after a refused acceptance")
if second_run != 0:
    failures.append(
        f"re-running the proposer inserted {second_run} duplicates; a rejection would be "
        "resurrected every night"
    )
if still_rejected != 8:
    failures.append(f"expected 8 still rejected after the re-run, got {still_rejected}")
if len(refusals) != 3:
    failures.append(f"expected three outright refusals, got {refusals}")
if cleaned != (0, 0):
    failures.append(f"the cookbook left {cleaned} behind in the graph")

if failures:
    print("K7 does NOT hold:")
    for failure in failures:
        print("  -", failure)
    raise SystemExit(1)
print("Review holds. Twenty proposed, none in the graph until a person decided; twelve")
print("accepted and applied, eight rejected and absent; a rejected proposal cannot be")
print(
    "accepted on a second try, and the proposer re-running cannot undo the rejection."
)
