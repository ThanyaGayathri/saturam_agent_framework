"""R7 -- preflight against a use case referencing a blocked KPI.

The package's Verify line's second scenario: "run preflight against a use case referencing a
blocked KPI." `check_preflight` needs no workflow, no database and no live
semantic layer -- `assess_feasibility` is a set difference, so the blocked case is
constructible by hand. Reproduces the API contract's own worked example
(`product/egpt_retail/api/openapi/paths/use-cases.yaml`, `runUseCasePreflight`) verbatim.

Needs no services.

Run:
    .venv/bin/python cookbook/94_retail/R07_preflight_blocked_kpi.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "product"))

from egpt.workflow.types import StepInput  # noqa: E402
from egpt_retail.contracts.kpi import KpiDefinition  # noqa: E402
from egpt_retail.executors.preflight import check_preflight  # noqa: E402

print("R7 -- preflight against a use case referencing a blocked KPI (G4)")
print("=" * 78)

# The contract's own blocked example: product__avg_rating needs product_reviews and
# web_sessions; only product_reviews is exposed by this snapshot.
blocked_kpi = KpiDefinition(
    identifier="product__avg_rating",
    name="Average product rating",
    description="Average customer rating per product.",
    sql=(
        "SELECT avg(rating) FROM product_reviews "
        "JOIN web_sessions ON product_reviews.session_id = web_sessions.id"
    ),
    grain="product",
    business_area="product",
    source_tables=frozenset({"product_reviews", "web_sessions"}),
)

print("\n1. A use case referencing one blocked KPI")
print("-" * 78)
step_input = StepInput(
    additional_data={
        "use_case_id": "uc_0003",
        "kpis": [blocked_kpi],
        "available_tables": {"product_reviews"},  # web_sessions not exposed
    }
)
result = asyncio.run(check_preflight(step_input))

print(f"  allowed:    {result.content['allowed']}")
print(f"  blocked_by: {result.content['blocked_by']}")
print("  checks:")
for check in result.content["checks"]:
    mark = "PASS" if check["passed"] else "FAIL"
    print(f"    {mark}  {check['name']:<16} {check['reason']}")

assert result.content["allowed"] is False
# `kpis_ready` must block. `gold_fresh` may also block, and legitimately does once a
# warehouse is configured and nothing has been materialised into gold yet -- it stopped being
# a placeholder when R5 landed. Pinning the whole list made this cookbook assert the absence
# of a check that now exists, so it passed only where the warehouse was unconfigured.
assert "kpis_ready" in result.content["blocked_by"], result.content["blocked_by"]
assert set(result.content["blocked_by"]) <= {"kpis_ready", "gold_fresh"}, (
    result.content["blocked_by"]
)
assert result.stop is True
assert "web_sessions" in result.error

print("\n2. The same use case, now with every table exposed")
print("-" * 78)
step_input_ready = StepInput(
    additional_data={
        "use_case_id": "uc_0003",
        "kpis": [blocked_kpi],
        "available_tables": {"product_reviews", "web_sessions"},
    }
)
result_ready = asyncio.run(check_preflight(step_input_ready))
print(f"  allowed:    {result_ready.content['allowed']}")
print(f"  blocked_by: {result_ready.content['blocked_by']}")
# Same reasoning as scenario 1: `kpis_ready` is what this scenario is about, and once a
# warehouse is configured `gold_fresh` legitimately blocks until something is materialised.
# Asserting `allowed is True` outright made this cookbook depend on R5's state.
_ready_blocks = set(result_ready.content["blocked_by"])
assert "kpis_ready" not in _ready_blocks, _ready_blocks
assert _ready_blocks <= {"gold_fresh"}, _ready_blocks
assert result_ready.content["allowed"] is (not _ready_blocks)
assert result_ready.stop is bool(_ready_blocks)

print()
print(
    "Preflight blocked and named the reason -- 'kpis_ready', with the missing table --"
)
print("then, once web_sessions is exposed, the same use case is allowed.")
