"""K16 -- the ``/use-cases`` router, driven the way the interface drives it.

Against a running local API (``./scripts/run_local_api.sh``): create the Order Creation
Automation use case, store the form's spec, run preflight, activate, then queue a rehearsal
run and poll it -- the same calls ``frontend/src/services/use-case-platform.js`` makes from
the Rehearse and Activate screens. Ends by printing the run's result the way the screen
renders it: what would have been created, what was held and why.

    ./scripts/run_local_api.sh &
    .venv/bin/python cookbook/94_retail/K16_use_case_api.py            # rehearsal (no side effects)
    .venv/bin/python cookbook/94_retail/K16_use_case_api.py --live     # the real mailbox and org

``--live`` polls the real inbox and creates Draft Orders in the real org: run it only after
``PA02_order_creation_pipeline.py --seed-mailbox`` has put the sample emails there.
"""

from __future__ import annotations

import argparse
import sys
import time

import httpx

API = "http://localhost:7777"

SPEC = {
    "question": "Turn purchase-order emails into Salesforce orders.",
    "business_area": "sales",
    "entities": [
        {"concept": "Customer", "role": "subject"},
        {"concept": "Product", "role": "subject"},
    ],
    "kpi_identifiers": [],
    "grain": "overall",
    "notes": "",
    "extracted_by": {"identifier": "interface.form", "version": "1"},
    "shape": "act",
    "template": "document_to_record",
    "parameters": {
        "source mailbox": {
            "id": "seed_gmail_orders",
            "sourceType": "gmail",
            "name": "Gmail - orders mailbox",
            "credentialRef": "env:gmail",
        },
        "message filter": {
            "unseen_only": False,
            "subject_contains": "Northwind sample",
            "since_days": 3,
        },
        "document type": "pdf",
        "extraction template": "purchase_order",
        "master data": {
            "customer": "Account",
            "contact": "Contact",
            "shipTo": "Address",
            "product": "Product2",
            "orderHistory": "Order",
        },
        "match thresholds": {
            "address": 0.75,
            "product": 0.85,
            "name": 0.8,
            "ambiguity": 0.05,
        },
        "guardrails": "all",
        "recipient": "kiruthiga@saturam.com",
        "write target": {
            "id": "seed_salesforce_orders",
            "sourceType": "salesforce",
            "name": "Salesforce - Sales Cloud (trial)",
            "credentialRef": "env:salesforce",
        },
        "target objects": "order_orderitem",
        "created as": "draft",
        "per-run cap": "20 records",
        "rollback": "Cancel the record and remove its lines",
        "schedule": "every_15_minutes",
    },
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--api", default=API)
    args = parser.parse_args()
    client = httpx.Client(base_url=args.api, timeout=60)

    created = client.post(
        "/use-cases",
        json={
            "title": "Order Creation Automation",
            "description": SPEC["question"],
            "business_area": "sales",
            "autonomy": "autopilot",
            "level": 4,
        },
    )
    created.raise_for_status()
    use_case_id = created.json()["id"]
    print(f"1. created {use_case_id} ({created.json()['status']})")

    spec = client.put(
        f"/use-cases/{use_case_id}/spec", json={**SPEC, "use_case_id": use_case_id}
    )
    spec.raise_for_status()
    print(
        f"2. spec stored: shape {spec.json()['shape']}, template {spec.json()['template']}, {len(spec.json()['parameters'])} parameters"
    )

    preflight = client.post(f"/use-cases/{use_case_id}/preflight").json()
    print(f"3. preflight allowed={preflight['allowed']}")
    for check in preflight["checks"]:
        print(
            f"     {'ok  ' if check['passed'] else 'FAIL'} {check['name']}: {check['reason']}"
        )

    activated = client.post(
        f"/use-cases/{use_case_id}/activate",
        json={"level": 4, "autonomy": "autopilot", "schedule": "every_15_minutes"},
    )
    if activated.status_code == 409:
        print(
            "4. activation refused:",
            [c["reason"] for c in activated.json()["checks"] if not c["passed"]],
        )
        return 1
    activated.raise_for_status()
    print(
        f"4. activated: version {activated.json()['version']}, status {activated.json()['status']}"
    )

    plan = client.get(f"/use-cases/{use_case_id}/plan").json()
    print("5. plan:", " -> ".join(s["ref"] for s in plan["steps"]))

    mode = "live" if args.live else "rehearse"
    run = client.post(f"/use-cases/{use_case_id}/runs", params={"mode": mode})
    run.raise_for_status()
    run_id = run.json()["id"]
    print(f"6. run {run_id} queued ({mode}); polling")
    started = time.time()
    while True:
        current = client.get(f"/use-cases/{use_case_id}/runs/{run_id}").json()
        if current["status"] in {"succeeded", "failed"}:
            break
        time.sleep(3)
    print(
        f"   {current['status']} after {time.time() - started:.0f}s: {current['summary'] or current.get('blocked_reason')}"
    )
    result = current.get("result") or {}
    for document in result.get("documents", []):
        written = document.get("written") or {}
        print(
            f"     {written.get('status'):<9} PO {document['extracted']['po_number']} for {document['resolved']['customer']['name']} {written.get('url') or ''}"
        )
    for row in result.get("exceptions", []):
        print(
            f"     exception {row['class']:<20} {row['subject']}: {row['detail'][:90]}"
        )
    if result.get("rollback", {}).get("points"):
        print(
            f"   rollback points: {result['rollback']['points']} (persisted: {result['rollback']['persisted']})"
        )
    return 0 if current["status"] == "succeeded" else 1


if __name__ == "__main__":
    sys.exit(main())
