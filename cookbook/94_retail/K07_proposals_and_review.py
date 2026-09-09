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
from egpt_retail.contracts.semantic import ProposalStatus  # noqa: E402
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
# Eight joins: two over the SAME pair with different predicates, then six distinct pairs.
# The same-pair two are the regression case for write_join keying on the predicate -- round
# 2 of the PR review noticed that all-distinct pairs could never have exercised it.
SAME_PAIR = PAIRS[0]
join_specs = [(SAME_PAIR, f"{MARKER}_email"), (SAME_PAIR, f"{MARKER}_contact_id")] + [
    (PAIRS[i], f"{MARKER}_{i}") for i in range(1, 7)
]
for (left, right), key in join_specs:
    batch.append(
        Proposal(
            kind=ProposalKind.JOIN,
            target=f"{left}->{right}",
            payload={
                "left_table": left,
                "right_table": right,
                "on": f"{left}.{key} = {right}.{key}",
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
same_pair = [
    p.identifier
    for p in mine
    if p.kind is ProposalKind.JOIN and p.target == f"{SAME_PAIR[0]}->{SAME_PAIR[1]}"
]
other_joins = [
    p.identifier
    for p in mine
    if p.kind is ProposalKind.JOIN and p.identifier not in same_pair
]

# Mixed on purpose: eight enrichments and four joins accepted, four of each rejected. If the
# split followed kind, a store that decided by kind rather than by decision would still pass.
# Both same-pair joins are among the accepted, so the graph must end with two edges between
# one pair of tables.
accepted_ids = enrichment[:8] + same_pair + other_joins[:2]
rejected_ids = enrichment[8:] + other_joins[2:]

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

applied = proposals.apply_accepted(graph).applied
after_concepts, after_joins = count_in_graph()

print()
print("4. Applying, and what landed")
print("-" * 78)
print(f"  proposals applied: {len(applied)}")
print(f"  concepts in graph: {after_concepts} (accepted enrichments: 8)")
print(f"  joins in graph:    {after_joins} (accepted joins: 4)")
with driver.session() as session:
    same_pair_edges = session.run(
        "MATCH (a:SemanticTable {name: $l})-[j:JOINS_TO]->(b:SemanticTable {name: $r}) "
        "WHERE j.on CONTAINS $marker RETURN count(j) AS n",
        l=SAME_PAIR[0],
        r=SAME_PAIR[1],
        marker=MARKER,
    ).single()["n"]
print(
    f"  edges between {SAME_PAIR[0]} and {SAME_PAIR[1]}: {same_pair_edges} (two predicates)"
)
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

after_retry = proposals.apply_accepted(graph).applied
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
try:
    proposals.propose(
        Proposal(
            kind=ProposalKind.ENRICHMENT,
            target="K7Concept98",
            payload={"name": "K7Concept98", "business_area": "marketng"},
            proposer=PROPOSER,
        )
    )
except PayloadError as error:
    refusals.append("business area outside the closed set")
    print(f"  business area outside the closed set: {str(error)[:60]}...")
try:
    proposals.propose(
        Proposal(
            kind=ProposalKind.JOIN,
            target="a->b",
            payload={
                "left_table": "a",
                "right_table": "b",
                "on": "a.x = b.x",
                "cardinality": "1:n",
            },
            proposer=PROPOSER,
        )
    )
except PayloadError as error:
    refusals.append("cardinality outside the closed set")
    print(f"  cardinality outside the closed set: {str(error)[:60]}...")

# --- 7b. One failing proposal does not block the queue ----------------------------------------

print()
print("7b. An accepted proposal that cannot be applied yet")
print("-" * 78)
# A mapping onto a concept nobody has defined, accepted alongside a perfectly good
# enrichment. The first version of apply_accepted raised on the mapping and never reached
# the enrichment -- and would have failed the same way every run, forever.
stuck = Proposal(
    kind=ProposalKind.MAPPING,
    target="silver.crm_contact",
    payload={"table": "silver.crm_contact", "concept": "K7NotYetDefined"},
    proposer=PROPOSER,
)
fine = Proposal(
    kind=ProposalKind.ENRICHMENT,
    target="K7Concept99",
    payload={"name": "K7Concept99", "description": "behind the stuck one in the queue"},
    proposer=PROPOSER,
)
proposals.propose_many([stuck, fine])
proposals.accept([stuck.identifier, fine.identifier], REVIEWER)
partial = proposals.apply_accepted(graph)
stuck_row = proposals.get(stuck.identifier)
print(f"  outcome: {partial}")
for failure in partial.failed:
    print(f"    failed: {str(failure)[:90]}")
print(f"  the good one behind it landed: {fine.identifier in partial.applied}")
print(f"  the stuck one is still accepted: {stuck_row.state is ReviewState.ACCEPTED}")
print(f"  and says why it has not landed: {bool(stuck_row.apply_error)}")

# --- 7c. include_proposed reads the queue, not the graph ---------------------------------------

print()
print("7c. include_proposed, on the real store")
print("-" * 78)
reading = SemanticGraphStore(driver=driver, proposals=proposals)
pending_one = Proposal(
    kind=ProposalKind.ENRICHMENT,
    target="K7ConceptPending",
    payload={
        "name": "K7ConceptPending",
        "description": "awaiting review",
        "business_area": "customer",
    },
    proposer=PROPOSER,
)
proposals.propose(pending_one)
hidden = reading.get_concept("K7ConceptPending")
shown = reading.get_concept("K7ConceptPending", include_proposed=True)
listed_default = "K7ConceptPending" in reading.list_concepts()
listed_proposed = "K7ConceptPending" in reading.list_concepts(include_proposed=True)
print(f"  get_concept() by default:            {hidden}")
print(
    f"  get_concept(include_proposed=True):  {shown.name if shown else None}, status={shown.status.value if shown else None}"
)
print(f"  in list_concepts() by default:       {listed_default}")
print(f"  in list_concepts(include_proposed):  {listed_proposed}")
print(
    "  Proposals live in Postgres, so without this the flag was a no-op on the real store."
)

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
if same_pair_edges != 2:
    failures.append(
        f"two accepted joins over one table pair produced {same_pair_edges} edge(s); "
        "write_join is not keying on the predicate"
    )
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
if len(refusals) != 5:
    failures.append(f"expected five outright refusals, got {refusals}")
if len(partial.failed) != 1 or fine.identifier not in partial.applied:
    failures.append(
        f"one unappliable proposal did not leave the queue moving: {partial}"
    )
if (
    stuck_row is None
    or stuck_row.state is not ReviewState.ACCEPTED
    or not stuck_row.apply_error
):
    failures.append("a failed apply did not stay accepted with its error recorded")
if hidden is not None or shown is None or shown.status is not ProposalStatus.PROPOSED:
    failures.append(
        "include_proposed did not surface a pending proposal as status=proposed"
    )
if listed_default or not listed_proposed:
    failures.append(
        "list_concepts did not respect include_proposed for pending proposals"
    )
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
