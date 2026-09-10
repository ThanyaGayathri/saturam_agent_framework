"""K11 -- the KPI registry, its graph mirror, and feasibility as a set difference.

The package's scenario, from the delivery plan: run feasibility over the whole registry,
remove one table from the snapshot and re-run, then construct a KPI needing two tables that
are both missing.

Evidence: counts of ready, partial and blocked; every blocked KPI names its missing tables;
and the two-missing case names both rather than only the first found.

**The point of the middle step** is the distinction between partial and blocked. Removing
one table produces both at once -- a KPI reading only that table is blocked, one reading it
alongside a table that survived is partial -- and that is the difference a customer deciding
what to connect next actually needs. "Blocked" says this subject area is unavailable;
"partial" says you are one table away.

Nothing here asks a model anything. Feasibility is `source_tables - available_tables`, and
the moment that becomes a judgment the ready and blocked badges stop meaning anything.

Needs the warehouse from K4, a Neo4j, and the control-plane Postgres:

    PYTHONPATH=product .venvs/demo/bin/python cookbook/94_retail/K11_kpi_registry_and_feasibility.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "product"))

import psycopg  # noqa: E402
from egpt_retail.contracts.kpi import (  # noqa: E402
    FeasibilityVerdict,
    assess_feasibility,
    assess_feasibility_with_joins,
    KpiDefinition,
    KpiParameter,
    ParameterKind,
)
from egpt_retail.contracts.semantic import Concept, available_tables  # noqa: E402
from egpt_retail.executors.kpi import feasibility_summary, verdict_rows  # noqa: E402
from egpt_retail.kpi.store import KpiRegistryStore  # noqa: E402
from egpt_retail.semantic.harvest import harvest_schema  # noqa: E402
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

print("K11 -- KPI registry and feasibility")
print("=" * 78)

warehouse = psycopg.connect(WAREHOUSE)
warehouse.autocommit = True
driver = GraphDatabase.driver(NEO4J, auth=AUTH)
graph = SemanticGraphStore(driver=driver)
graph.ensure_indexes()

full_harvest = harvest_schema(
    warehouse, "silver", source="egpt_warehouse", sample=False
)
graph.write_harvest(full_harvest, snapshot_version="k11-full.1")

control_plane = psycopg.connect(CONTROL_PLANE)
store = KpiRegistryStore(control_plane)
store.ensure_schema()

SCOPE = KpiParameter("scope", ParameterKind.PREDICATE, "Concept scoping filter.")

# Six definitions over the real silver schema. The last one names two tables this warehouse
# has never held, which is the plan's two-missing case.
KPIS = [
    KpiDefinition(
        identifier="k11_deal__won_value",
        name="Won deal value",
        description="Total value of deals in a closed-won stage.",
        sql="SELECT sum(amount) FROM crm_deal WHERE {scope}",
        grain="overall",
        business_area="sales",
        source_tables=frozenset({"crm_deal"}),
        parameters=(SCOPE,),
        concept="K11Deal",
    ),
    KpiDefinition(
        identifier="k11_contact__reachable",
        name="Reachable contacts",
        description="Contacts holding an email address.",
        sql="SELECT count(*) FROM crm_contact WHERE email IS NOT NULL",
        grain="overall",
        business_area="customer",
        source_tables=frozenset({"crm_contact"}),
        concept="K11Contact",
    ),
    KpiDefinition(
        identifier="k11_deal__stage_velocity",
        name="Stage velocity",
        description="Average days a deal spends in each stage.",
        sql=(
            "SELECT avg(h.days_in_stage) FROM crm_deal d "
            "JOIN crm_deal_stage_history h ON h.deal_id = d.deal_id"
        ),
        grain="overall",
        business_area="sales",
        source_tables=frozenset({"crm_deal", "crm_deal_stage_history"}),
        concept="K11Deal",
    ),
    KpiDefinition(
        identifier="k11_session__replay_coverage",
        name="Replay coverage",
        description="Share of analytics sessions that have a replay recorded.",
        sql=(
            "SELECT count(*) FROM analytics_event e "
            "JOIN session_replay_session s ON s.session_id = e.session_id"
        ),
        grain="overall",
        business_area="marketing",
        source_tables=frozenset({"analytics_event", "session_replay_session"}),
        concept="K11Session",
    ),
    # The partial case once crm_contact goes: one table survives, one does not.
    KpiDefinition(
        identifier="k11_deal__pipeline_by_contact",
        name="Pipeline by contact",
        description="Open pipeline attributed to each contact.",
        sql=(
            "SELECT sum(d.amount) FROM crm_deal d "
            "JOIN crm_contact c ON c.object_id = d.object_id"
        ),
        grain="customer",
        business_area="sales",
        source_tables=frozenset({"crm_deal", "crm_contact"}),
        concept="K11Deal",
    ),
    # The two-missing case. Neither table has ever been in this warehouse.
    KpiDefinition(
        identifier="k11_order__fulfilment_rate",
        name="Fulfilment rate",
        description="Orders shipped complete, over orders placed.",
        sql=(
            "SELECT count(*) FROM orders o "
            "JOIN order_items i ON i.order_id = o.order_id"
        ),
        grain="overall",
        business_area="sales",
        source_tables=frozenset({"orders", "order_items"}),
        concept="K11Order",
    ),
]

with control_plane.cursor() as cursor:
    cursor.execute("DELETE FROM semantic.kpi_definition WHERE identifier LIKE 'k11_%'")
control_plane.commit()

# --- 1. The registry, and feasibility over all of it ---------------------------------------

saved = store.save_many(KPIS)
mine = [k for k in store.list() if k.identifier.startswith("k11_")]
first = {k: v for k, v in store.feasibility(graph).items() if k.startswith("k11_")}

print()
print("1. The whole registry, assessed")
print("-" * 78)
print(f"  definitions stored: {saved}; read back: {len(mine)}")
print(f"  tables in the semantic layer: {len(available_tables(graph))}")
print(f"  summary: {feasibility_summary(first)}")
for row in verdict_rows(first):
    missing = ", ".join(row["missing_tables"]) or "-"
    print(f"    {row['verdict']:<8} {row['kpi_identifier']:<32} missing: {missing}")

# --- 2. Every blocked KPI names its missing tables ------------------------------------------

two_missing = first["k11_order__fulfilment_rate"]
print()
print("2. The two-missing case")
print("-" * 78)
print(f"  {two_missing.kpi_identifier}: {two_missing.verdict.value}")
print(f"  missing: {sorted(two_missing.missing_tables)}")
print(f"  rationale: {two_missing.rationale}")
print("  Both names, not the first one found -- it is a set difference, not a search.")

# --- 3. Stamp what is computable against this snapshot (K6's missing writer) -----------------

report = store.revalidate(graph)
stamped = {
    k.identifier: k.validated_against_snapshot
    for k in store.list()
    if k.identifier.startswith("k11_")
}
print()
print("3. Revalidation -- who was checked against which snapshot")
print("-" * 78)
print(f"  {report}")
print(f"  blocked KPI left unstamped: {stamped['k11_order__fulfilment_rate'] is None}")
print(f"  ready KPI carries the version: {stamped['k11_deal__won_value']}")

# --- 4. The graph mirror: a concept reaches its metrics by traversal -------------------------

# The concepts these KPIs hang off, as an accepted enrichment proposal would have written
# them (K7). `Order` is deliberately left out: nothing has proposed it, so the KPI naming it
# must get no edge. A mirror that created the concept would let registering a KPI invent one
# nobody reviewed -- which is what the first version of this did, and K5's cookbook caught it
# by finding the real store and the stub disagreeing about list_concepts().
# K11-prefixed names, not the ones K05's cookbook writes. A cookbook that reuses another's
# fixture names mutates it: this one first wrote `WebSession`, overwriting K05's description
# of it in the shared graph, and the sweep failed downstream in a cookbook that had nothing
# to do with KPIs.
for concept, area in (
    ("K11Deal", "sales"),
    ("K11Contact", "customer"),
    ("K11Session", "marketing"),
):
    graph.write_concept(
        Concept(name=concept, description=f"{concept}s, for K11.", business_area=area)
    )

mirrored = store.mirror(driver)
with driver.session() as session:
    by_concept = session.run(
        "MATCH (c:SemanticConcept)-[:MEASURED_BY]->(k:SemanticKpi) "
        "WHERE k.identifier STARTS WITH 'k11_' "
        "RETURN c.name AS concept, collect(k.name) AS metrics ORDER BY concept"
    ).data()
    unreviewed = session.run(
        "MATCH (k:SemanticKpi {identifier: 'k11_order__fulfilment_rate'}) "
        "OPTIONAL MATCH (c:SemanticConcept)-[:MEASURED_BY]->(k) "
        "RETURN count(c) AS edges"
    ).single()["edges"]
    invented = session.run(
        "MATCH (c:SemanticConcept {name: 'K11Order'}) RETURN count(c) AS n"
    ).single()["n"]
    reads_contact = session.run(
        "MATCH (k:SemanticKpi)-[:READS]->(t:SemanticTable {name: 'crm_contact'}) "
        "RETURN collect(k.identifier) AS kpis"
    ).single()["kpis"]
print()
print("4. The mirror, and what it makes traversable")
print("-" * 78)
print(f"  KPIs mirrored: {mirrored}")
for row in by_concept:
    print(f"    {row['concept']:<14} -> {', '.join(sorted(row['metrics']))}")
print(f"  KPIs reading crm_contact (K6's review question): {sorted(reads_contact)}")
print(
    f"  edges from an unproposed concept: {unreviewed}; concept nodes invented: {invented}"
)
print(
    "  A KPI cannot conjure the concept it names. Concepts enter through review (D10)."
)

# --- 4b. Present, and still not computable (K12.5) -----------------------------------------

# The case the set difference cannot see. Both tables are in the snapshot, so
# `source_tables - available_tables` is empty and feasibility said `ready` -- and the formula
# would then join two id spaces the harvest's own comment says are not joinable, returning a
# cross product rather than an error. Wrong numbers, no failure, nothing upstream to catch it.
CROSS_SOURCE = KpiDefinition(
    identifier="k11_session__replay_coverage_joined",
    name="Replay coverage, joined",
    description="Analytics sessions that also have a replay, which needs the two joined.",
    sql=(
        "SELECT count(*) FROM analytics_event e "
        "JOIN session_replay_session s ON s.session_id = e.session_id"
    ),
    grain="overall",
    business_area="marketing",
    source_tables=frozenset({"analytics_event", "session_replay_session"}),
    concept="K11Session",
)
# The ruling itself, as accepted review writes it. End of a chain: K4 detects that
# `session_id` is carried by two tables, K8's mapping agent resolves each side and marks it
# not joinable from the harvest's own column comment, a person accepts that, and only then
# can feasibility see it. Written directly because K8 is a separate package; what matters is
# that the ruling reached the graph through review and not through a formula's assertion.
for _table in ("silver.analytics_event", "silver.session_replay_session"):
    graph.write_column_meaning(
        _table,
        "session_id",
        concept="K11Session",
        meaning=f"{_table.split('.')[-1]}'s own session id, in its own id space",
        joinable=False,
    )

tables_only = assess_feasibility(CROSS_SOURCE, available_tables(graph))
with_joins = assess_feasibility_with_joins(
    CROSS_SOURCE, available_tables(graph), graph.unjoinable_oracle()
)
print()
print("4b. Present, and still not computable")
print("-" * 78)
print(f"  tables alone:      {tables_only.verdict.value} ({tables_only.reason.value})")
print(f"  with the join:     {with_joins.verdict.value} ({with_joins.reason.value})")
print(f"  {with_joins.rationale}")
print(
    "  A missing table needs a connector. This needs that pair resolved. Rendering them"
)
print("  the same way makes the cheap fix look as expensive as the dear one.")
print(
    "  Note what had to be true: only an ACCEPTED column meaning makes this visible. A"
)
print(
    "  pair nobody ruled on stays ready -- absence of a proposal is not a prohibition."
)

# This scenario's ruling, removed. It is a fact about the shared graph, and the sections
# below measure the same registry against it -- leaving it would fail section 6's restore
# check on a change this section made deliberately.
with driver.session() as _session:
    _session.run(
        "MATCH (c:SemanticColumn {name: 'session_id'}) "
        "REMOVE c.concept, c.meaning, c.joinable"
    )

# --- 5. Remove one table from the snapshot, and re-run --------------------------------------

reduced = harvest_schema(
    warehouse,
    "silver",
    source="egpt_warehouse",
    sample=False,
    exclude_tables=("crm_contact",),
)
graph.write_harvest(reduced, snapshot_version="k11-reduced.1")
second = {k: v for k, v in store.feasibility(graph).items() if k.startswith("k11_")}

print()
print("5. crm_contact removed from the snapshot")
print("-" * 78)
print(f"  tables now: {len(available_tables(graph))}")
print(f"  summary: {feasibility_summary(second)}")
for identifier in sorted(second):
    before, after = first[identifier].verdict, second[identifier].verdict
    if before is not after:
        print(
            f"    {identifier:<32} {before.value} -> {after.value} "
            f"(missing: {', '.join(sorted(second[identifier].missing_tables))})"
        )
print(
    "  One removal, two different answers: the KPI reading only that table is blocked,"
)
print(
    "  the one reading it beside a surviving table is partial -- one table away, not lost."
)

# --- 6. Restore, and clean up ----------------------------------------------------------------

graph.write_harvest(full_harvest, snapshot_version="k11-full.2")
restored = {k: v for k, v in store.feasibility(graph).items() if k.startswith("k11_")}

with control_plane.cursor() as cursor:
    cursor.execute("DELETE FROM semantic.kpi_definition WHERE identifier LIKE 'k11_%'")
control_plane.commit()
store.mirror(driver)
with driver.session() as session:
    left_behind = session.run(
        "MATCH (k:SemanticKpi) WHERE k.identifier STARTS WITH 'k11_' RETURN count(k) AS n"
    ).single()["n"]
    session.run(
        "MATCH (c:SemanticColumn {name: 'session_id'}) "
        "REMOVE c.concept, c.meaning, c.joinable"
    )
    session.run(
        "MATCH (c:SemanticConcept) WHERE c.name STARTS WITH 'K11' DETACH DELETE c"
    )
control_plane.close()
warehouse.close()
driver.close()

print()
print("6. Restored and cleaned up")
print("-" * 78)
print(f"  summary after restore: {feasibility_summary(restored)}")
print(f"  KPI nodes left behind: {left_behind}")

# --- 7. Verdict -------------------------------------------------------------------------------

print()
print("=" * 78)
failures = []
if saved != len(KPIS) or len(mine) != len(KPIS):
    failures.append(f"expected {len(KPIS)} definitions stored, got {saved}/{len(mine)}")
if feasibility_summary(first) != {"ready": 5, "partial": 0, "blocked": 1}:
    failures.append(f"unexpected first assessment: {feasibility_summary(first)}")
if two_missing.verdict is not FeasibilityVerdict.BLOCKED:
    failures.append("a KPI whose every table is absent was not blocked")
if sorted(two_missing.missing_tables) != ["order_items", "orders"]:
    failures.append(
        f"the two-missing case named {sorted(two_missing.missing_tables)}, not both tables"
    )
if verdict_rows(first)[0]["verdict"] != "blocked":
    failures.append("the discovery rows do not lead with what is blocked")
if report.validated and "k11_order__fulfilment_rate" in report.validated:
    failures.append("a blocked KPI was stamped as validated against the snapshot")
if stamped["k11_deal__won_value"] != "k11-full.1":
    failures.append(
        f"a computable KPI was not stamped with the snapshot: {stamped['k11_deal__won_value']}"
    )
if mirrored != len(KPIS):
    failures.append(f"expected {len(KPIS)} KPIs mirrored, got {mirrored}")
if not any(r["concept"] == "K11Deal" and len(r["metrics"]) == 3 for r in by_concept):
    failures.append(
        f"a concept did not reach its three metrics by traversal: {by_concept}"
    )
if sorted(reads_contact) != ["k11_contact__reachable", "k11_deal__pipeline_by_contact"]:
    failures.append(
        f"READS edges do not answer which KPIs a table breaks: {reads_contact}"
    )
if second["k11_contact__reachable"].verdict is not FeasibilityVerdict.BLOCKED:
    failures.append("a KPI whose only table was removed was not blocked")
if second["k11_deal__pipeline_by_contact"].verdict is not FeasibilityVerdict.PARTIAL:
    failures.append(
        "a KPI keeping one table and losing another was not partial -- the distinction a "
        "customer needs to decide what to connect next is gone"
    )
if second["k11_deal__won_value"].verdict is not FeasibilityVerdict.READY:
    failures.append("a KPI untouched by the removal changed verdict")
if tables_only.verdict is not FeasibilityVerdict.READY:
    failures.append(
        "the fixture for the join case is wrong: both tables should be present so the set "
        "difference alone says ready"
    )
if with_joins.verdict is not FeasibilityVerdict.BLOCKED:
    failures.append(
        "two present but unjoinable tables were not blocked; the formula would return a "
        "cross product at run time with nothing reporting it (K12.5)"
    )
if not with_joins.unjoinable:
    failures.append("a no-join-path verdict did not name the pair it could not join")
if tables_only.rationale == with_joins.rationale:
    failures.append(
        "a missing table and an unjoinable pair render identically -- the distinction K12.5 "
        "exists to draw"
    )
if restored != first:
    failures.append("restoring the snapshot did not restore the original assessment")
if left_behind:
    failures.append(f"{left_behind} KPI nodes survived deletion from the registry")
if unreviewed:
    failures.append(
        f"a KPI naming an unproposed concept carries {unreviewed} MEASURED_BY edge(s)"
    )
if invented:
    failures.append(
        "the mirror created a SemanticConcept nobody proposed -- registering a KPI must "
        "not be able to invent a concept (D10)"
    )

if failures:
    print("K11 does NOT hold:")
    for failure in failures:
        print("  -", failure)
    raise SystemExit(1)
print(
    "Registry holds. Feasibility is a set difference over the live snapshot, never stored;"
)
print(
    "a blocked KPI names every missing table; removing one table produces both a blocked"
)
print("and a partial; and a concept reaches its metrics by traversal in the mirror.")
