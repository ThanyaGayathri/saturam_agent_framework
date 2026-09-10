"""K5 -- the semantic graph store and the real read API (D11, G20).

The package's scenarios, from the delivery plan:

1. Write a harvest into the graph; ask for the columns of one table.
2. Ask for the join path between two tables from different sources.
3. Delete the semantic memory copy and re-run promotion.
4. Confirm the phase-zero stub can be swapped out without changing any caller.

Scenario 4 is the one the whole P0 contract existed for, so it is tested by running the *same
function* over both implementations and requiring identical answers. Anything weaker would be
checking that two classes have the same method names.

Needs the warehouse from K4, a Neo4j, and Postgres for the promoted copy:

    PYTHONPATH=product .venvs/demo/bin/python cookbook/94_retail/K04_metadata_harvest.py
    PYTHONPATH=product .venvs/demo/bin/python cookbook/94_retail/K05_semantic_graph.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "product"))
os.environ.setdefault("EGPT_RETAIL_PROVIDER_MODE", "local")
# Its own memory table, not the deployment's. The table is created at the embedder's
# dimension and the framework refuses to reuse one of a different width, so a cookbook
# running Mode A (768) would leave the deployment's Mode B (1024) table unusable.
os.environ.setdefault("EGPT_RETAIL_MEMORY_TABLE", "memory_vectors_k5_cookbook")

import psycopg  # noqa: E402
from egpt.memory.types import Scope  # noqa: E402
from egpt_retail.app.components import build_memory_provider  # noqa: E402
from egpt_retail.app.settings import RetailSettings  # noqa: E402
from egpt_retail.contracts.semantic import (  # noqa: E402
    AuthoritativeSource,
    Concept,
    JoinEdge,
    SemanticRead,
    SemanticReadError,
)
from egpt_retail.contracts.semantic_stub import StubSemanticRead  # noqa: E402
from egpt_retail.memory.scopes import SYSTEM_USER_ID  # noqa: E402
from egpt_retail.semantic.harvest import harvest_schema  # noqa: E402
from egpt_retail.semantic.promotion import (  # noqa: E402
    PROMOTED_TOPIC,
    promotable_text,
    promote_concepts,
    promoted_event_id,
)
from egpt_retail.semantic.store import SemanticGraphStore  # noqa: E402
from neo4j import GraphDatabase  # noqa: E402

WAREHOUSE = os.environ.get(
    "EGPT_RETAIL_WAREHOUSE_DSN", "postgresql://ai:ai@localhost:5532/egpt_warehouse"
)
NEO4J = os.environ.get("EGPT_ONTOLOGY_NEO4J_URI", "neo4j://localhost:7701")
AUTH = ("neo4j", os.environ.get("EGPT_RETAIL_NEO4J_PASSWORD", "egptretail"))
SLUG = os.environ.get("EGPT_RETAIL_CUSTOMER_SLUG", "middleby")
MEMORY_TABLE = os.environ["EGPT_RETAIL_MEMORY_TABLE"]

print("K5 -- semantic graph store and read API")
print("=" * 78)

# --- 1. Harvest in, columns out ----------------------------------------------------------

warehouse = psycopg.connect(WAREHOUSE)
warehouse.autocommit = True
harvest = harvest_schema(warehouse, "silver", source="egpt_warehouse")
warehouse.close()

driver = GraphDatabase.driver(NEO4J, auth=AUTH)
store = SemanticGraphStore(driver=driver)
store.ensure_indexes()
written = store.write_harvest(harvest, snapshot_version="k5-demo.1")

print()
print("1. A harvest written to the graph, read back through the contract")
print("-" * 78)
print(f"  {written} tables written; snapshot {store.snapshot().version}")
print(f"  list_tables(): {', '.join(t.name for t in store.list_tables())}")

contact = store.get_table("crm_contact")
assert contact is not None
harvested = harvest.table("crm_contact")
assert harvested is not None
matches = contact.column_names == harvested.column_names
print(f"  crm_contact columns from the graph: {len(contact.columns)}")
print(f"  identical to the harvest: {matches}")
print(f"    {', '.join(sorted(contact.column_names))}")

# The collision K4 found travels with the column, so a reader sees the ambiguity in place.
session_id = contact.column("email")
brand_col = store.get_table("analytics_event")
brand = brand_col.column("brand") if brand_col else None
print()
print("  K4's collisions are reachable from a read, not re-derived:")
with driver.session() as s:
    rows = s.run(
        "MATCH (c:SemanticColumn) WHERE size(c.collides_with) > 0 "
        "RETURN c.name AS name, c.collides_with AS with ORDER BY name LIMIT 4"
    ).data()
for row in rows:
    print(f"    {row['name']:<18} also in {', '.join(row['with'])}")

# --- 2. Concepts, joins, and the authoritative source (G20) ------------------------------

print()
print("2. Concepts, a join across sources, and who is authoritative (G20)")
print("-" * 78)

store.write_concept(
    Concept(
        name="QuoteRequest",
        description="A request from a prospective buyer for a price on a product.",
        synonyms=("RFQ", "quote request"),
        sql_filter="d.record_type = 'deal'",
        personal_data_fields=("email",),
        compliance_tags=("pii",),
        business_area="sales",
    ),
    tables=("silver.crm_deal", "silver.crm_contact"),
)
store.write_concept(
    Concept(
        name="WebSession",
        description="A single visit to the web store by one person.",
        synonyms=("session", "visit"),
        business_area="marketing",
    ),
    tables=("silver.analytics_event", "silver.session_replay_session"),
)

# A real join: CRM contact to CRM deal, on the multiplexed object id.
store.write_join(
    JoinEdge(
        left_table="crm_contact",
        right_table="crm_deal",
        on="crm_contact.object_id = crm_deal.object_id",
        cardinality="one-to-many",
    )
)

print(f"  concepts: {', '.join(store.list_concepts())}")
print(f"  business areas: {', '.join(store.list_business_areas())}")

# G20, and the case is real. Two tables both carry `session_id` and both could claim to
# answer "what happened in a session" -- but R4's own DDL comment says they are NOT joinable.
# That is a decision a person makes and records; a model choosing would be exactly the failure.
store.write_authoritative_source(
    AuthoritativeSource(
        concept="WebSession",
        table="analytics_event",
        alternatives=("session_replay_session",),
        rationale=(
            "Both carry session_id, but silver.analytics_event.session_id is the analytics "
            "vendor's id space and is NOT joinable to session_replay_session (see the column "
            "comment in R4's DDL). Analytics is authoritative for session counts until a "
            "bridge exists."
        ),
    )
)
decided = store.authoritative_source("WebSession")
undecided = store.authoritative_source("QuoteRequest")
print()
print(f"  WebSession -> {decided.table}, beating {', '.join(decided.alternatives)}")
print(f"    because: {decided.rationale[:88]}...")
print(
    f"  QuoteRequest -> {undecided} (nobody has had to decide; that is a real answer)"
)

# --- 3. Join paths: found, absent, refused -----------------------------------------------

print()
print("3. Join paths -- three outcomes, never a guess")
print("-" * 78)
path = store.find_join_path("crm_contact", "crm_deal")
print(f"  crm_contact -> crm_deal: {path.hops} hop(s)")
for edge in path.edges:
    print(f"    {edge.left_table} -> {edge.right_table} ON {edge.on}")

absent = store.find_join_path("crm_contact", "analytics_event")
print(
    f"  crm_contact -> analytics_event: {absent} (no declared join; an answer, not an error)"
)

refused = False
try:
    store.find_join_path("crm_contact", "not_a_table")
except SemanticReadError as exc:
    refused = True
    print(f"  unknown table raises: {str(exc).split('.')[0]}")

# --- 4. The stub swaps out without changing a caller -------------------------------------

print()
print("4. Lane R's callers work unchanged (the point of the P0 contract)")
print("-" * 78)


def describe(reader: SemanticRead) -> dict:
    """A caller. Knows the contract and nothing about which implementation it holds."""
    return {
        "tables": sorted(t.name for t in reader.list_tables()),
        "concepts": sorted(reader.list_concepts()),
        "areas": sorted(reader.list_business_areas()),
        "contact_columns": sorted(
            (reader.get_table("crm_contact") or _Empty()).column_names
        ),
        "authoritative_websession": getattr(
            reader.authoritative_source("WebSession"), "table", None
        ),
    }


class _Empty:
    column_names: frozenset = frozenset()


# The same fixture the graph holds, expressed for the stub.
stub = StubSemanticRead.from_payload(
    {
        "snapshot": {"version": "k5-demo.1", "harvested_at": harvest.harvested_at},
        "tables": [
            {
                "name": t.name,
                "schema": t.schema,
                "row_count": t.row_count,
                "columns": [
                    {
                        "name": c.name,
                        "data_type": c.data_type,
                        "nullable": c.nullable,
                        "is_key": c.is_key,
                    }
                    for c in t.columns
                ],
            }
            for t in harvest.tables
        ],
        "concepts": [
            {
                "name": "QuoteRequest",
                "description": "A request from a prospective buyer for a price on a product.",
                "business_area": "sales",
                "synonyms": ["RFQ", "quote request"],
            },
            {
                "name": "WebSession",
                "description": "A single visit to the web store by one person.",
                "business_area": "marketing",
                "synonyms": ["session", "visit"],
            },
        ],
        "authoritative_sources": [
            {
                "concept": "WebSession",
                "table": "analytics_event",
                "alternatives": ["session_replay_session"],
                "rationale": "as above",
            }
        ],
    }
)

from_graph = describe(store)
from_stub = describe(stub)
same = from_graph == from_stub
print(
    f"  both satisfy the protocol: {isinstance(store, SemanticRead)} / {isinstance(stub, SemanticRead)}"
)
for key in from_graph:
    mark = "same" if from_graph[key] == from_stub[key] else "DIFFERS"
    print(f"    {key:<24} {mark}")
print(f"  one caller, two implementations, identical answers: {same}")

# --- 5. Promotion, and the rebuild that proves the graph is authoritative -----------------

print()
print("5. Promotion into semantic memory, and rebuilding it from the graph")
print("-" * 78)
settings = RetailSettings(retail_customer_slug=SLUG)
provider = build_memory_provider(settings)
scope = Scope(tenant_id=SLUG, agent_id="semantic_layer", user_id=SYSTEM_USER_ID)


def promoted_rows():
    with psycopg.connect(
        settings.retail_db_url.replace("postgresql+psycopg://", "postgresql://")
    ) as conn:
        return {
            row[0]: row[1]
            for row in conn.execute(
                # topics is jsonb, not a Postgres array. `jsonb_exists` rather than the `?`
                # operator: `?` collides with psycopg's own placeholder syntax, and the `??`
                # escape is a driver convention this call style does not apply.
                f"select id, content from ai.{MEMORY_TABLE} "
                "where tenant_id = %s and jsonb_exists(topics, %s) order by id",
                (SLUG, PROMOTED_TOPIC),
            ).fetchall()
        }


# A clean slate before the baseline. The scenario deletes the copy and rebuilds it, then
# compares -- so a row left by an earlier run with a different concept set makes the baseline
# something the rebuild cannot reproduce, and the cookbook fails on residue rather than on
# behaviour. It self-corrected on a second run, which is the worst kind of flake: it looks
# like a transient and is a missing precondition.
def clear_promoted_copy():
    """Remove every row this scenario's promotion writes, by tenant and topic."""
    with psycopg.connect(
        settings.retail_db_url.replace("postgresql+psycopg://", "postgresql://")
    ) as conn:
        conn.execute(
            f"delete from ai.{MEMORY_TABLE} "
            "where tenant_id = %s and jsonb_exists(topics, %s)",
            (SLUG, PROMOTED_TOPIC),
        )
        conn.commit()


clear_promoted_copy()

first = promote_concepts(store, provider, scope)
print(f"  {first} under {scope.key()}")


after_first = promoted_rows()
print(f"  rows in semantic memory: {len(after_first)}")

# Running it again must overwrite, not accumulate -- promotion runs after every harvest.
promote_concepts(store, provider, scope)
after_second = promoted_rows()
idempotent = after_first == after_second
print(f"  promoting again changes nothing (idempotent): {idempotent}")
stable_ids = set(after_first) == {promoted_event_id(n) for n in store.list_concepts()}
print(f"  each row carries its stable derived id: {stable_ids}")

# Now the scenario: delete the copy entirely and rebuild it from the graph.
clear_promoted_copy()
emptied = promoted_rows()
promote_concepts(store, provider, scope)
rebuilt = promoted_rows()
identical = rebuilt == after_first
print(f"  deleted the copy: {len(emptied)} rows remain")
print(
    f"  rebuilt from the graph: {len(rebuilt)} rows, identical to before: {identical}"
)
print(f"    {list(rebuilt.values())[0][:88]}...")

# What is deliberately NOT promoted: anything that governs a query.
quote = store.get_concept("QuoteRequest")
promoted_text = promotable_text(quote)
withheld = quote.sql_filter not in promoted_text and "pii" not in promoted_text
print(f"  the scoping filter and compliance tags stay in the graph: {withheld}")

provider.close()
# --- 6. The nightly re-harvest must not erase what a person reviewed ------------------------

print()
print("6. Re-harvest, and what survives it")
print("-" * 78)


def reviewed_edges():
    with driver.session() as s:
        return {
            kind: s.run(f"MATCH ()-[r:{kind}]->() RETURN count(r) AS n").single()["n"]
            for kind in ("INSTANTIATES", "JOINS_TO", "AUTHORITATIVE_SOURCE")
        }


edges_before = reviewed_edges()
store.write_harvest(harvest, snapshot_version="k5-demo.2")
edges_after = reviewed_edges()
with driver.session() as s:
    samples_in_graph = s.run(
        "MATCH (c:SemanticColumn) WHERE c.sampled_values IS NOT NULL RETURN count(c) AS n"
    ).single()["n"]
print(f"  reviewed edges before: {edges_before}")
print(f"  reviewed edges after:  {edges_after}")
print(f"  columns carrying sampled row values: {samples_in_graph}")
print(
    "  The first version of write_harvest wiped both labels and rewrote them, taking every"
)
print(
    "  reviewed edge with it. A nightly harvest would have erased the review every night."
)

driver.close()

# --- 7. Verdict ---------------------------------------------------------------------------

print()
print("=" * 78)
failures = []
if edges_after != edges_before or not all(edges_before.values()):
    failures.append(
        f"a re-harvest changed the reviewed edges: {edges_before} -> {edges_after}. "
        "The nightly harvest would erase every accepted mapping, join and source."
    )
if samples_in_graph:
    failures.append(
        f"{samples_in_graph} columns carry sampled row values; rows stay in the warehouse"
    )
if not matches:
    failures.append("the column list from the graph does not match the harvest")
if not rows:
    failures.append("K4's collisions did not survive the write into the graph")
if path is None or path.hops != 1:
    failures.append(
        "the declared join between crm_contact and crm_deal did not resolve"
    )
if absent is not None:
    failures.append(
        "a join path was returned where none is declared -- that is a guess"
    )
if not refused:
    failures.append("an unknown table did not raise")
if decided is None or decided.table != "analytics_event" or not decided.was_contested:
    failures.append(
        "the authoritative source was not recorded with its alternatives (G20)"
    )
if undecided is not None:
    failures.append("an uncontested concept reported an authoritative source")
if not same:
    failures.append("the graph store and the stub gave a caller different answers")
if not idempotent:
    failures.append(
        "promoting twice changed the memory copy -- it would accumulate per harvest"
    )
if emptied:
    failures.append("deleting the promoted copy left rows behind")
if not identical:
    failures.append(
        "the rebuilt copy differs from the original; the graph is not authoritative"
    )
if not withheld:
    failures.append("a governing value was promoted into a decaying store")

if failures:
    print("K5 does NOT hold:")
    for failure in failures:
        print("  -", failure)
    raise SystemExit(1)
print(
    "Semantic layer holds. A harvest reads back through the contract unchanged, the stub"
)
print(
    "swaps out without touching a caller, and the memory copy rebuilds from the graph."
)
