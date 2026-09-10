"""R5 -- KPI health, assembled from the registry and gold, validating against the contract.

Service-free. `warehouse/health.py` classifies one KPI from two rows -- its latest run of any
status and its latest run that wrote values -- into the contract's `KpiHealth`, whose five
statuses include two (`never_computed`, `not_scheduled`) that describe registry KPIs gold has
never usefully seen. A warehouse view alone cannot produce those, which is why this is Python.

Every row printed here validates against the generated `KpiHealth` model, including the
registry-only KPI.

    .venv/bin/python cookbook/94_retail/R05_gold_health.py
"""

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "product"))

from egpt_retail.api.models._generated import KpiHealth  # noqa: E402
from egpt_retail.contracts.kpi import KpiDefinition  # noqa: E402
from egpt_retail.executors.preflight import gold_fresh_check  # noqa: E402
from egpt_retail.warehouse.health import classify_health  # noqa: E402

NOW = datetime(2026, 9, 9, 9, 0, tzinfo=timezone.utc)
AS_OF = date(2026, 9, 8)


def kpi(identifier):
    return KpiDefinition(
        identifier=identifier,
        name=identifier,
        description="",
        sql="SELECT 1 FROM crm_deal",
        grain="overall",
        business_area="sales",
        source_tables=frozenset({"crm_deal"}),
    )


def run(status, hours_ago, run_id, error=None, reason=None):
    return {
        "run_id": run_id,
        "as_of": AS_OF,
        "status": status,
        "error": error,
        "reason": reason,
        "finished_at": NOW - timedelta(hours=hours_ago),
    }


scenarios = [
    (
        "fresh: materialised 7 h ago",
        kpi("supplier__on_time_rate_30d"),
        run("materialised", 7, "run_0041"),
        run("materialised", 7, "run_0041"),
    ),
    (
        "stale: materialised 40 h ago",
        kpi("product__return_rate"),
        run("materialised", 40, "run_0031"),
        run("materialised", 40, "run_0031"),
    ),
    (
        "failed: last run failed, a value from the night before survives",
        kpi("deal__won_value"),
        run("failed", 7, "run_0041", error='column "amount" does not exist'),
        run("materialised", 31, "run_0031"),
    ),
    (
        "not_scheduled: blocked by feasibility, skipped every night",
        kpi("order__fulfilment_rate"),
        run(
            "skipped",
            7,
            "run_0041",
            reason="blocked: missing from the semantic snapshot: order_items, orders",
        ),
        None,
    ),
    (
        "never_computed: in the registry, never in gold",
        kpi("product__avg_rating"),
        None,
        None,
    ),
]

print("R5 -- KPI health, five statuses, every row validated against the contract")
print("=" * 78)
rows = []
for title, definition, latest, materialised in scenarios:
    row = classify_health(definition, latest, materialised, now=NOW)
    KpiHealth.model_validate(row)
    rows.append((definition, row))
    print(
        f"  {row['status']:<15} {row['identifier']:<28} as_of={row['as_of']} staleness={row['staleness_hours']} last_run={row['last_run_id']}"
    )
    if row["last_error"]:
        print(f"                  last_error: {row['last_error'][:70]}")
    print(f"                  ({title})")

print()
print("gold_fresh, as check_preflight computes it over those rows")
print("-" * 78)
health = {row["identifier"]: row for _, row in rows}
check = gold_fresh_check([d for d, _ in rows], health)
print(f"  passed={check['passed']}")
for sentence in check["reason"].split(". "):
    print(f"    {sentence.strip()}")
fresh_only = gold_fresh_check([rows[0][0]], health)
print(
    f"  over the fresh KPI alone: passed={fresh_only['passed']} -- {fresh_only['reason']}"
)

ok = (
    [r["status"] for _, r in rows]
    == ["fresh", "stale", "failed", "not_scheduled", "never_computed"]
    and check["passed"] is False
    and fresh_only["passed"] is True
    and rows[2][1]["last_computed_at"] is not None
)
print()
print("Overall:", "PASS" if ok else "FAIL")
raise SystemExit(0 if ok else 1)
