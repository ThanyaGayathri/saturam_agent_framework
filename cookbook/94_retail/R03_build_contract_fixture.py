"""R3 -- the contract fixture, derived from what is ours.

Lane K's semantic read stub ships with no fixture, deliberately. The phase-zero stub server
still has to serve something, and client sample data is PII that was not vendored. So the
fixture is built from the shipped vocabularies and the vendored KPI corpus -- product nouns,
never a customer's -- and every example payload in the API contract is drawn from it.

This script proves three things:

1. The committed fixture is exactly what the builder produces (it is generated, not edited).
2. Loaded into Lane K's stub, it exhibits every behaviour the contract's examples claim:
   the three-hop path, the null path, a hidden proposed table and a hidden proposed column.
3. The contract's KPI examples are feasible against it by construction -- the same
   `available_tables(reader)` seam K05 proved, now against the fixture the stub serves.

Run:
    .venv/bin/python cookbook/94_retail/R03_build_contract_fixture.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "product"))

from egpt_retail.contracts.fixture_builder import (  # noqa: E402
    CONTRACT_FIXTURE_PATH,
    build_contract_fixture,
    fixture_matches_committed,
)
from egpt_retail.contracts.kpi import (  # noqa: E402
    KpiDefinition,
    KpiParameter,
    ParameterKind,
    TimeWindow,
    TimeWindowUnit,
    assess_feasibility,
)
from egpt_retail.contracts.semantic import (  # noqa: E402
    available_tables,
    describe_reader,
)
from egpt_retail.contracts.semantic_stub import StubSemanticRead  # noqa: E402

print("R3 -- contract fixture")
print("=" * 78)

# --- 1. The committed fixture is what the builder produces --------------------------------

print()
print("1. Committed fixture matches a fresh build")
print("-" * 78)
ok, why = fixture_matches_committed()
print(
    "  ", "OK " if ok else "FAIL", CONTRACT_FIXTURE_PATH.name, "" if ok else f"-- {why}"
)

fixture = build_contract_fixture()
print("   header:", fixture["_comment"][0])
print(
    "   snapshot:",
    fixture["snapshot"]["version"],
    "| vocabulary:",
    fixture["vocabulary_version"],
)

# --- 2. Loaded into the stub, it behaves as the contract examples claim -------------------

print()
print("2. Behaviour through Lane K's stub")
print("-" * 78)
reader = StubSemanticRead.from_payload(fixture)
print("  ", describe_reader(reader))
default_tables = {t.name for t in reader.list_tables()}
proposed_tables = {
    t.name for t in reader.list_tables(include_proposed=True)
} - default_tables
print("   accepted tables :", ", ".join(sorted(default_tables)))
print("   proposed tables :", ", ".join(sorted(proposed_tables)))
customers = reader.get_table("customers")
customers_all = reader.get_table("customers", include_proposed=True)
assert customers is not None and customers_all is not None
hidden_columns = customers_all.column_names - customers.column_names
print("   proposed columns hidden on customers:", ", ".join(sorted(hidden_columns)))
print("   business areas  :", ", ".join(reader.list_business_areas()))

path = reader.find_join_path("customers", "products")
assert path is not None
print(
    f"   customers -> products: {path.hops} hops via {list(path.intermediate_tables)}"
)
none = reader.find_join_path("supplier_deliveries", "product_reviews")
print("   supplier_deliveries -> product_reviews:", none, "(an answer, not an error)")

# --- 3. The contract's KPI examples are feasible by construction --------------------------

print()
print("3. Feasibility of the contract's KPI examples against this fixture")
print("-" * 78)
available = available_tables(reader)
examples = [
    KpiDefinition(
        identifier="stocklevel__days_of_stock",
        name="Days of Stock Remaining",
        description="Estimated days until stockout at current sell-through.",
        sql=(
            "SELECT i.product_id, p.name FROM inventory i JOIN products p ON i.product_id = p.product_id "
            "LEFT JOIN order_items oi ON i.product_id = oi.product_id LEFT JOIN orders o ON oi.order_id = o.order_id "
            "WHERE i.stock_level = {stock_level_name} GROUP BY i.product_id"
        ),
        grain="product",
        business_area="inventory",
        source_tables=frozenset({"inventory", "order_items", "orders", "products"}),
        parameters=(KpiParameter(name="stock_level_name", kind=ParameterKind.SCALAR),),
        time_window=TimeWindow(length=30, unit=TimeWindowUnit.DAY),
    ),
    KpiDefinition(
        identifier="supplier__on_time_rate_30d",
        name="Supplier On-Time Rate (30d)",
        description="Fraction of deliveries on time in the last 30 days.",
        sql=(
            "SELECT COUNT(CASE WHEN status = 'on_time' THEN 1 END) * 1.0 / NULLIF(COUNT(*), 0) "
            "FROM supplier_deliveries WHERE supplier_id = {supplier_id}"
        ),
        grain="supplier",
        business_area="supply_chain",
        source_tables=frozenset({"supplier_deliveries"}),
        parameters=(KpiParameter(name="supplier_id", kind=ParameterKind.SCALAR),),
        time_window=TimeWindow(length=30, unit=TimeWindowUnit.DAY),
    ),
    KpiDefinition(
        identifier="product__avg_rating",
        name="Average Product Rating",
        description="Average star rating from reviews, joined to sessions.",
        sql=(
            "SELECT AVG(rating) FROM product_reviews JOIN web_sessions ON web_sessions.customer_id = "
            "product_reviews.product_id WHERE product_id = {product_id}"
        ),
        grain="product",
        business_area="product",
        source_tables=frozenset({"product_reviews", "web_sessions"}),
        parameters=(KpiParameter(name="product_id", kind=ParameterKind.SCALAR),),
    ),
]
verdicts = {}
for kpi in examples:
    verdict = assess_feasibility(kpi, available)
    verdicts[kpi.identifier] = verdict.verdict.value
    print(f"   {kpi.identifier:<32} {verdict.verdict.value:<8} {verdict.rationale}")

# Assert, do not merely print. These cookbooks are this lane's only tests.
problems = []
if not ok:
    problems.append(f"committed fixture is stale: {why}")
if proposed_tables != {"web_sessions"}:
    problems.append("the proposed table did not stay hidden by default")
if hidden_columns != {"email"}:
    problems.append("the proposed column did not stay hidden by default")
if path.hops != 3 or path.intermediate_tables != ("orders", "order_items"):
    problems.append("the three-hop path in the contract examples did not resolve")
if none is not None:
    problems.append("the pair the contract calls unjoinable resolved to a path")
if verdicts != {
    "stocklevel__days_of_stock": "ready",
    "supplier__on_time_rate_30d": "ready",
    "product__avg_rating": "partial",
}:
    problems.append(f"feasibility disagreed with the contract examples: {verdicts}")

print()
print("=" * 78)
if problems:
    print("Fixture does NOT support the contract:")
    for problem in problems:
        print("  -", problem)
    raise SystemExit(1)
print("Fixture supports every behaviour the contract's examples claim.")
