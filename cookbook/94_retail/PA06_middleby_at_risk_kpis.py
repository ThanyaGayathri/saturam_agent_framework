"""PA06 -- the at-risk pipeline KPI set, registered and computed (K26, phase 1).

The KPIs the at-risk use case reads and the KPI screen must show under *Ready to measure*
before the story holds: the sales cycle length per category (the 90th percentile of days
to close -- a quote past it is "stalled"), stalled quotes and their value, quote conversion
within that usual range, the 60-day customer churn rate and the at-risk accounts behind
it, the RFM segment distribution, and the average account recency, frequency and monetary
value. Every one is a definition over the silver table PA04 loaded, in the shape the KPI
screen writes, registered through the same ``POST /kpis`` the screen calls and computed
into gold through ``POST /kpis/{id}/compute``.

    .venv/bin/python cookbook/94_retail/PA06_middleby_at_risk_kpis.py [--api http://localhost:7777] [--no-compute]

Idempotent: a KPI already registered is left as it is. Nothing here is a model feature:
per-quote age and per-account win rate are computed by the scoring step at run time, not
registered -- a KPI is something a business user would put on a dashboard.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List

AS_OF = [{"name": "as_of", "kind": "scalar", "description": "The run's business date."}]
Q = "silver.dc_rfq_quotes"
#: The usual time to close per category: the 90th percentile of days to close over every
#: quote closed up to the run's date. A quote open longer than this is stalled.
TYPICAL = (
    f"(SELECT pim_category, PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY days_to_close) "
    f"AS usual_days FROM {Q} WHERE days_to_close IS NOT NULL AND closed_date <= "
    f"CAST({{as_of}} AS date) GROUP BY pim_category)"
)
STALLED = (
    f"FROM {Q} q JOIN {TYPICAL} t ON t.pim_category = q.pim_category WHERE "
    f"q.pipeline_stage NOT IN ('Closed Won', 'Closed Lost') AND q.quote_date <= "
    f"CAST({{as_of}} AS date) AND CAST({{as_of}} AS date) - q.quote_date > t.usual_days"
)
ACTIVE_ACCOUNTS = (
    f"(SELECT customer_id, MAX(customer_segment) AS customer_segment, MAX(quote_date) AS "
    f"last_quote FROM {Q} WHERE quote_date > CAST({{as_of}} AS date) - 240 AND quote_date "
    f"<= CAST({{as_of}} AS date) GROUP BY customer_id)"
)

AT_RISK_KPIS: List[Dict[str, Any]] = [
    {
        "identifier": "rfq__sales_cycle_length_by_category",
        "name": "Sales cycle length by category",
        "description": "How long a quote usually takes to close, per product category: the 90th percentile of days to close over quotes closed in the trailing 180 days. A quote open longer than this is stalled.",
        "grain": "product_category",
        "sql": f"SELECT pim_category, ROUND(CAST(PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY days_to_close) AS numeric), 0) AS value FROM {Q} WHERE days_to_close IS NOT NULL AND closed_date > CAST({{as_of}} AS date) - 180 AND closed_date <= CAST({{as_of}} AS date) GROUP BY pim_category ORDER BY value DESC",
        "unit": "days",
        "time_window": {"length": 180, "unit": "day"},
    },
    {
        "identifier": "rfq__stalled_quotes",
        "name": "Stalled quotes",
        "description": "Open quotes that have been open longer than their category's usual time to close -- the aged pipeline.",
        "grain": "overall",
        "sql": f"SELECT COUNT(*) AS value {STALLED}",
        "unit": "quotes",
        "time_window": None,
    },
    {
        "identifier": "rfq__stalled_quotes_by_territory",
        "name": "Stalled quotes by territory",
        "description": "Open quotes past their category's usual time to close, one row per sales territory.",
        "grain": "sales_territory",
        "sql": f"SELECT q.sales_rep_territory, COUNT(*) AS value {STALLED} GROUP BY q.sales_rep_territory ORDER BY value DESC",
        "unit": "quotes",
        "time_window": None,
    },
    {
        "identifier": "rfq__stalled_pipeline_value",
        "name": "Stalled pipeline value",
        "description": "The quoted value sitting in stalled quotes -- open longer than the category's usual time to close -- in USD.",
        "grain": "overall",
        "sql": f"SELECT ROUND(CAST(COALESCE(SUM(q.deal_value_usd), 0) AS numeric), 2) AS value {STALLED}",
        "unit": "USD",
        "time_window": None,
    },
    {
        "identifier": "rfq__quote_conversion_within_usual",
        "name": "Quote conversion within the usual range",
        "description": "Of quotes created 30 to 120 days ago, the share that closed won within their category's usual time to close, in percent. The rate the at-risk score predicts.",
        "grain": "overall",
        "sql": f"SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE q.pipeline_stage = 'Closed Won' AND q.days_to_close <= t.usual_days) / NULLIF(COUNT(*), 0), 1) AS value FROM {Q} q JOIN {TYPICAL} t ON t.pim_category = q.pim_category WHERE q.quote_date > CAST({{as_of}} AS date) - 120 AND q.quote_date <= CAST({{as_of}} AS date) - 30",
        "unit": "%",
        "time_window": {"length": 90, "unit": "day"},
    },
    {
        "identifier": "rfq__customer_churn_rate_60d",
        "name": "Customer churn rate (60 days)",
        "description": "Of accounts that quoted in the trailing 240 days, the share with no quote in the last 60 days, in percent.",
        "grain": "overall",
        "sql": f"SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE last_quote <= CAST({{as_of}} AS date) - 60) / NULLIF(COUNT(*), 0), 1) AS value FROM {ACTIVE_ACCOUNTS} a",
        "unit": "%",
        "time_window": {"length": 60, "unit": "day"},
    },
    {
        "identifier": "rfq__at_risk_accounts",
        "name": "At-risk accounts",
        "description": "Accounts that quoted in the trailing 240 days but not in the last 60: the accounts behind the churn rate.",
        "grain": "overall",
        "sql": f"SELECT COUNT(*) AS value FROM {ACTIVE_ACCOUNTS} a WHERE last_quote <= CAST({{as_of}} AS date) - 60",
        "unit": "accounts",
        "time_window": {"length": 60, "unit": "day"},
    },
    {
        "identifier": "rfq__at_risk_accounts_by_segment",
        "name": "At-risk accounts by segment",
        "description": "At-risk accounts (no quote in 60 days after quoting in the 240 before), one row per customer segment.",
        "grain": "customer_segment",
        "sql": f"SELECT customer_segment, COUNT(*) AS value FROM {ACTIVE_ACCOUNTS} a WHERE last_quote <= CAST({{as_of}} AS date) - 60 GROUP BY customer_segment ORDER BY value DESC",
        "unit": "accounts",
        "time_window": {"length": 60, "unit": "day"},
    },
    {
        "identifier": "rfq__rfm_segments",
        "name": "Accounts by RFM segment",
        "description": "Accounts in each RFM segment -- recency, frequency and monetary quintiles over the trailing year: Champions, Loyal, New, Potential, At risk, Cannot lose them, Hibernating, Lost.",
        "grain": "rfm_segment",
        "sql": (
            "SELECT segment AS rfm_segment, COUNT(*) AS value FROM (SELECT CASE "
            "WHEN r >= 4 AND f >= 4 THEN 'Champions' WHEN r >= 3 AND f >= 3 THEN 'Loyal' "
            "WHEN r >= 4 AND f <= 2 THEN 'New' WHEN r <= 2 AND f >= 4 AND m >= 4 THEN 'Cannot lose them' "
            "WHEN r <= 2 AND f >= 3 THEN 'At risk' WHEN r <= 2 AND f <= 2 AND m >= 3 THEN 'Hibernating' "
            "WHEN r <= 1 THEN 'Lost' ELSE 'Potential' END AS segment FROM (SELECT customer_id, "
            f"NTILE(5) OVER (ORDER BY (CAST({{as_of}} AS date) - MAX(quote_date)) DESC) AS r, "
            "NTILE(5) OVER (ORDER BY COUNT(*)) AS f, "
            "NTILE(5) OVER (ORDER BY COALESCE(SUM(deal_value_usd) FILTER (WHERE pipeline_stage = 'Closed Won'), 0)) AS m "
            f"FROM {Q} WHERE quote_date > CAST({{as_of}} AS date) - 365 AND quote_date <= CAST({{as_of}} AS date) "
            "GROUP BY customer_id) s) x GROUP BY segment ORDER BY value DESC"
        ),
        "unit": "accounts",
        "time_window": {"length": 365, "unit": "day"},
    },
    {
        "identifier": "rfq__avg_account_recency",
        "name": "Average account recency",
        "description": "Days since the last quote, averaged over accounts that quoted in the trailing year (RFM: recency).",
        "grain": "overall",
        "sql": f"SELECT ROUND(CAST(AVG(CAST({{as_of}} AS date) - last_quote) AS numeric), 0) AS value FROM (SELECT customer_id, MAX(quote_date) AS last_quote FROM {Q} WHERE quote_date > CAST({{as_of}} AS date) - 365 AND quote_date <= CAST({{as_of}} AS date) GROUP BY customer_id) a",
        "unit": "days",
        "time_window": {"length": 365, "unit": "day"},
    },
    {
        "identifier": "rfq__avg_account_frequency_180d",
        "name": "Average quotes per account (180 days)",
        "description": "Quotes per active account over the trailing 180 days (RFM: frequency).",
        "grain": "overall",
        "sql": f"SELECT ROUND(CAST(AVG(n) AS numeric), 2) AS value FROM (SELECT customer_id, COUNT(*) AS n FROM {Q} WHERE quote_date > CAST({{as_of}} AS date) - 180 AND quote_date <= CAST({{as_of}} AS date) GROUP BY customer_id) a",
        "unit": "quotes",
        "time_window": {"length": 180, "unit": "day"},
    },
    {
        "identifier": "rfq__avg_account_monetary_180d",
        "name": "Average won value per account (180 days)",
        "description": "Closed-won value per account over the trailing 180 days, in USD (RFM: monetary).",
        "grain": "overall",
        "sql": f"SELECT ROUND(CAST(AVG(v) AS numeric), 0) AS value FROM (SELECT customer_id, SUM(deal_value_usd) AS v FROM {Q} WHERE pipeline_stage = 'Closed Won' AND closed_date > CAST({{as_of}} AS date) - 180 AND closed_date <= CAST({{as_of}} AS date) GROUP BY customer_id) a",
        "unit": "USD",
        "time_window": {"length": 180, "unit": "day"},
    },
]


def _call(api: str, method: str, path: str, body: Any = None) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        api + path,
        method=method,
        data=data,
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.loads(response.read().decode() or "null")


def register(api: str) -> List[str]:
    created: List[str] = []
    for kpi in AT_RISK_KPIS:
        body = {
            "identifier": kpi["identifier"],
            "name": kpi["name"],
            "description": kpi["description"],
            "business_area": "sales",
            "subject_area": "Sales Pipeline",
            "concept": "Quote",
            "grain": kpi["grain"],
            "result_shape": "scalar",
            "dialect": "postgres",
            "sql": kpi["sql"],
            "source_tables": ["dc_rfq_quotes"],
            "parameters": AS_OF,
            "time_window": kpi["time_window"],
            "level": "operational",
            "unit": kpi["unit"],
            "active": True,
        }
        try:
            _call(api, "POST", "/kpis", body)
            created.append(kpi["identifier"])
            print(f"  registered {kpi['identifier']}")
        except urllib.error.HTTPError as error:
            if error.code == 409:
                print(f"  already registered {kpi['identifier']}")
            else:
                print(
                    f"  REFUSED {kpi['identifier']}: {error.code} {error.read().decode()[:300]}"
                )
    return created


def compute(api: str) -> None:
    for kpi in AT_RISK_KPIS:
        identifier = kpi["identifier"]
        try:
            out = _call(api, "POST", f"/kpis/{identifier}/compute", {})
        except urllib.error.HTTPError as error:
            print(f"  FAILED {identifier}: {error.code} {error.read().decode()[:300]}")
            continue
        status = out.get("status") or out.get("verdict") or out
        rows = out.get("rows_written") or out.get("row_count") or out.get("rows")
        print(f"  computed {identifier:<40} {status} rows={rows}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api", default="http://localhost:7777")
    parser.add_argument("--no-compute", action="store_true")
    args = parser.parse_args()
    print("Registering the at-risk pipeline KPIs")
    register(args.api)
    if not args.no_compute:
        print("Computing them into gold")
        compute(args.api)
    return 0


if __name__ == "__main__":
    sys.exit(main())
