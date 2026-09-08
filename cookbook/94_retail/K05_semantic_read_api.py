"""Semantic read API -- the P0 contract stub, exercised.

The contract Lane R builds preflight, the playground and the spec screen against, before the
real graph exists. This script proves the four things a caller depends on:

1. An unloaded stub raises rather than returning empty. "No fixture wired" and "this
   deployment has no tables" must not look the same, or a screen renders zero rows and
   appears to work.
2. Proposals are invisible by default. A caller has to ask for unreviewed content by name.
3. A join path is found, absent, or an error -- three outcomes, never a guess.
4. Feasibility reads its table set from this API, so the KPI contract and the semantic
   contract meet in exactly one place.

The fixture below is a small illustrative one written for this script. It is not the client's
data and is not shipped: the stub itself ships empty, on purpose. See
`product/egpt_retail/contracts/_fixtures/README.md`.

Run:
    .venv/bin/python cookbook/94_retail/K05_semantic_read_api.py
"""

import asyncio
import sys
from pathlib import Path
from typing import Any, Dict, List, cast

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "product"))

from egpt_retail.contracts.kpi import (  # noqa: E402
    KpiDefinition,
    KpiParameter,
    ParameterKind,
    assess_feasibility,
)
from egpt_retail.contracts.semantic import (  # noqa: E402
    SemanticRead,
    SemanticReadError,
    available_tables,
    describe_reader,
)
from egpt_retail.contracts.semantic_stub import (  # noqa: E402
    FixtureNotLoaded,
    StubSemanticRead,
)

print("Semantic read API -- P0 stub")
print("=" * 78)

# --- 1. An unloaded stub refuses, and says how to fix it -------------------------------

print()
print("1. The stub ships with no fixture")
print("-" * 78)
empty = StubSemanticRead()
print("  Satisfies the SemanticRead protocol:", isinstance(empty, SemanticRead))
try:
    empty.list_tables()
    print("  FAIL: returned instead of raising")
except FixtureNotLoaded as exc:
    print("  list_tables() raises FixtureNotLoaded:")
    print("   ", str(exc)[:96], "...")

# --- 2. A loaded fixture ----------------------------------------------------------------

FIXTURE = {
    "snapshot": {"version": "demo.1", "harvested_at": "2026-09-07T02:00:00Z"},
    "tables": [
        {
            "name": "contacts",
            "row_count": 310,
            "concepts": ["Contact"],
            "columns": [
                {"name": "contact_id", "data_type": "text", "is_key": True},
                {"name": "email", "data_type": "text", "is_personal_data": True},
                {"name": "company_name", "data_type": "text"},
            ],
        },
        {
            "name": "quote_requests",
            "row_count": 310,
            "concepts": ["QuoteRequest"],
            "columns": [
                {"name": "quote_id", "data_type": "text", "is_key": True},
                {"name": "contact_id", "data_type": "text"},
                {"name": "brand", "data_type": "text"},
                # An unreviewed enrichment proposal, sitting inside an accepted table.
                {"name": "lead_score", "data_type": "numeric", "status": "proposed"},
            ],
        },
        {
            "name": "brand_traffic",
            "row_count": 45,
            "concepts": ["Brand"],
            "columns": [
                {"name": "brand", "data_type": "text", "is_key": True},
                {"name": "sessions", "data_type": "integer"},
            ],
        },
        # A whole table still awaiting review.
        {
            "name": "session_replay",
            "status": "proposed",
            "columns": [{"name": "session_id", "data_type": "text"}],
        },
    ],
    "concepts": [
        {
            "name": "QuoteRequest",
            "description": "A request from a prospective buyer for a price on a product.",
            "synonyms": ["RFQ", "quote request"],
            "sql_filter": "qr.status = 'submitted'",
            "personal_data_fields": ["email"],
            "compliance_tags": ["pii"],
            "business_area": "sales",
        },
        {
            "name": "Brand",
            "description": "A manufacturer brand under which products are sold.",
            "business_area": "marketing",
        },
        {
            "name": "Contact",
            "description": "A person who requested a quote.",
            "business_area": "customer",
        },
    ],
    "join_edges": [
        {
            "left_table": "quote_requests",
            "right_table": "contacts",
            "on": "quote_requests.contact_id = contacts.contact_id",
        },
        {
            "left_table": "quote_requests",
            "right_table": "brand_traffic",
            "on": "quote_requests.brand = brand_traffic.brand",
            "cardinality": "many-to-one",
        },
    ],
}

reader = StubSemanticRead.from_payload(FIXTURE)
print()
print("2. Reads")
print("-" * 78)
print(" ", describe_reader(reader))
print("  tables:", ", ".join(t.name for t in reader.list_tables()))
concept = reader.get_concept("QuoteRequest")
assert concept is not None
print("  concept QuoteRequest:")
print("    synonyms   :", ", ".join(concept.synonyms))
print("    sql_filter :", concept.sql_filter)
print(
    "    pii        :",
    ", ".join(concept.personal_data_fields),
    "| tags:",
    ", ".join(concept.compliance_tags),
)
print("  business areas supported:", ", ".join(reader.list_business_areas()))

# --- 3. Proposals are invisible unless asked for ----------------------------------------

print()
print("3. Unreviewed content is opt-in")
print("-" * 78)
default_tables = {t.name for t in reader.list_tables()}
with_proposed = {t.name for t in reader.list_tables(include_proposed=True)}
print("  default          :", len(default_tables), "tables")
print(
    "  include_proposed :",
    len(with_proposed),
    "tables ->",
    sorted(with_proposed - default_tables),
    "appears",
)

qr = reader.get_table("quote_requests")
qr_proposed = reader.get_table("quote_requests", include_proposed=True)
assert qr is not None and qr_proposed is not None
print(
    "  quote_requests columns, default          :", ", ".join(sorted(qr.column_names))
)
print(
    "  quote_requests columns, include_proposed :",
    ", ".join(sorted(qr_proposed.column_names)),
)
print(
    "  a proposed column nested in an accepted table stays hidden:",
    "lead_score" not in qr.column_names,
)
print(
    "  personal-data columns on contacts:",
    [c.name for c in (reader.get_table("contacts") or qr).personal_data_columns],
)

# --- 4. Join paths: found, absent, error ------------------------------------------------

print()
print("4. Join paths -- three outcomes, never a guess")
print("-" * 78)
path = reader.find_join_path("contacts", "brand_traffic")
if path is None:
    print("  contacts -> brand_traffic: no path")
else:
    print(
        f"  contacts -> brand_traffic: {path.hops} hops via {list(path.intermediate_tables)}"
    )
    for edge in path.edges:
        print(f"      {edge.left_table} -> {edge.right_table} ON {edge.on}")

isolated = dict(FIXTURE)
isolated["join_edges"] = []
no_edges = StubSemanticRead.from_payload(isolated)
print(
    "  with no edges declared:",
    no_edges.find_join_path("contacts", "brand_traffic"),
    "(an answer, not an error)",
)

try:
    reader.find_join_path("contacts", "nonexistent_table")
    print("  FAIL: an unknown table returned instead of raising")
except SemanticReadError as exc:
    print("  unknown table raises:", str(exc).split(".")[0])

# --- 5. Where the two contracts meet ----------------------------------------------------

print()
print("5. Feasibility reads its table set from this API")
print("-" * 78)
kpi = KpiDefinition(
    identifier="quote_requests_by_brand",
    name="quote_requests_by_brand",
    description="Quote requests received, by brand, joined to site traffic.",
    sql=(
        "SELECT COUNT(*) FROM quote_requests "
        "JOIN brand_traffic ON quote_requests.brand = brand_traffic.brand WHERE {brand_filter}"
    ),
    grain="brand",
    business_area="sales",
    source_tables=frozenset({"quote_requests", "brand_traffic"}),
    parameters=(KpiParameter(name="brand_filter", kind=ParameterKind.PREDICATE),),
)
verdict = assess_feasibility(kpi, available_tables(reader))
print(f"  {kpi.identifier}: {verdict.verdict.value} -- {verdict.rationale}")

# Genuinely missing from the reader's own data, not reconstructed by subtracting from its
# output -- a bug that makes available_tables() over-report a table (a stale cache, a
# hardcoded fallback, a TableSet.from_reader bug) would stay invisible to a check built the
# other way, since the subtraction removes the table regardless of what the reader actually
# said.
without_brand_traffic: Dict[str, Any] = dict(FIXTURE)
fixture_tables = cast(List[Dict[str, Any]], FIXTURE["tables"])
without_brand_traffic["tables"] = [
    t for t in fixture_tables if t["name"] != "brand_traffic"
]
missing_table_reader = StubSemanticRead.from_payload(without_brand_traffic)
blocked = assess_feasibility(kpi, available_tables(missing_table_reader))
print(f"  with brand_traffic dropped: {blocked.verdict.value} -- {blocked.rationale}")

# --- 6. Async variants exist and agree --------------------------------------------------


async def _check_async() -> bool:
    tables = await reader.alist_tables()
    concepts = await reader.alist_concepts()
    apath = await reader.afind_join_path("contacts", "brand_traffic")
    return (
        {t.name for t in tables} == default_tables
        and set(concepts) == set(reader.list_concepts())
        and (apath is None) == (path is None)
    )


print()
print("6. Async variants")
print("-" * 78)
print("  every async read agrees with its sync twin:", asyncio.run(_check_async()))

# Assert, do not merely print. These cookbooks are this lane's only tests.
problems = []
if "lead_score" in qr.column_names:
    problems.append("a proposed column leaked into a default get_table")
if "session_replay" in default_tables:
    problems.append("a proposed table leaked into a default list_tables")
if "session_replay" not in with_proposed:
    problems.append("include_proposed=True did not reveal the proposed table")
if path is None or path.hops != 2 or path.intermediate_tables != ("quote_requests",):
    problems.append("the two-hop join path did not resolve as expected")
if no_edges.find_join_path("contacts", "brand_traffic") is not None:
    problems.append("a reader with no edges returned a path")
if verdict.verdict.value != "ready" or blocked.verdict.value != "partial":
    problems.append("feasibility through the read API did not agree with the fixture")
if not asyncio.run(_check_async()):
    problems.append("an async read disagreed with its sync twin")

print()
print("=" * 78)
if problems:
    print("Stub does NOT implement the contract:")
    for problem in problems:
        print("  -", problem)
    raise SystemExit(1)
print("Stub implements the contract. Lane R can wire against these signatures today.")
