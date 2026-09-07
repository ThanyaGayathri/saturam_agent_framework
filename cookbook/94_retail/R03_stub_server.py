"""R3 -- the API contract, running.

The delivery plan's verification for R3: stand the contract up as a stub server returning
example payloads, and point one prototype screen at it. This script is the first half. It
proves that the contract executes: every operation the bundle declares is mounted, every one
returns its declared example with its declared status, and the example selector the empty
and populated screens depend on works.

By default the app runs in-process. Set EGPT_STUB_URL to check a stub started with
``python -m egpt_retail.api.stub`` instead -- the same app, over a real socket.

Run:
    .venv/bin/python cookbook/94_retail/R03_stub_server.py
"""

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "product"))

import httpx  # noqa: E402
from egpt_retail.api.spec import load_bundle  # noqa: E402
from egpt_retail.api.stub import create_app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

METHODS = {"get", "post", "put", "patch", "delete"}
LIVE = {
    "getSemanticSnapshot",
    "listSemanticTables",
    "getSemanticTable",
    "listSemanticConcepts",
    "getSemanticConcept",
    "listSemanticBusinessAreas",
    "findSemanticJoinPath",
}

print("R3 -- stub server")
print("=" * 78)

bundle = load_bundle()
base_url = os.environ.get("EGPT_STUB_URL")
if base_url:
    client = httpx.Client(base_url=base_url, timeout=10)
    print("  against a running stub at", base_url)
else:
    client = TestClient(create_app())
    print("  in-process (set EGPT_STUB_URL to check a running stub)")


def concrete(route: str) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: f"{m.group(1)}_x", route)


def first_example(operation):
    for code, response in operation.get("responses", {}).items():
        if not (code.isdigit() and 200 <= int(code) < 300):
            continue
        media = (response.get("content") or {}).get("application/json") or {}
        for name, example in (media.get("examples") or {}).items():
            return int(code), name, example["value"]
    return None


# --- 1. Every operation is mounted ----------------------------------------------------------

print()
print("1. Operations mounted versus declared")
print("-" * 78)
declared = {
    (m.upper(), r) for r, it in bundle["paths"].items() for m in it if m in METHODS
}
mounted = {
    (op["method"], op["path"])
    for op in client.get("/stub/operations").json()["operations"]
}
print(
    "   declared:",
    len(declared),
    "| mounted:",
    len(mounted),
    "| equal:",
    declared == mounted,
)

# --- 2. Every operation serves its declared example -----------------------------------------

print()
print("2. Every operation returns its first declared example")
print("-" * 78)
mismatches = []
served = 0
for route, item in bundle["paths"].items():
    for method, operation in item.items():
        if method not in METHODS or operation.get("operationId") in LIVE:
            continue
        expected = first_example(operation)
        url = concrete(route)
        body = {} if method in {"post", "put", "patch"} else None
        response = client.request(method.upper(), url, json=body)
        if expected is None:
            if response.status_code != 204:
                mismatches.append(
                    f"{method.upper()} {route}: expected 204, got {response.status_code}"
                )
            continue
        status, _name, value = expected
        if response.status_code != status or response.json() != value:
            mismatches.append(f"{method.upper()} {route}: {response.status_code}")
        served += 1
print("   served:", served, "| mismatches:", len(mismatches))
for line in mismatches[:10]:
    print("     -", line)

# --- 3. The empty and populated states both render -------------------------------------------

print()
print("3. The example selector -- KpiRegistry has an empty state and a populated one")
print("-" * 78)
populated = client.get("/kpis").json()
empty = client.get("/kpis?example=empty").json()
header = client.get("/kpis", headers={"X-Example": "empty"}).json()
print("   GET /kpis                 ->", len(populated), "KPIs")
print("   GET /kpis?example=empty   ->", len(empty), "KPIs")
print("   X-Example: empty          ->", len(header), "KPIs")
unknown = client.get("/kpis?example=nope")
print(
    "   ?example=nope             ->", unknown.status_code, unknown.json().get("code")
)
badges = {k["identifier"]: k["feasibility"]["verdict"] for k in populated}
print("   feasibility badges        ->", badges)

problems = []
if declared != mounted:
    problems.append("mounted operations differ from the bundle")
if mismatches:
    problems.append(
        f"{len(mismatches)} operation(s) did not return their declared example"
    )
if not populated or empty != [] or header != []:
    problems.append("the example selector did not switch between populated and empty")
if unknown.status_code != 400:
    problems.append("an unknown example name was not refused")
if badges.get("product__avg_rating") != "partial":
    problems.append("the blocked-KPI badge is missing from the registry example")

print()
print("=" * 78)
if problems:
    print("Stub does NOT execute the contract:")
    for problem in problems:
        print("  -", problem)
    raise SystemExit(1)
print(
    f"Contract executes: {len(declared)} operations mounted, {served} serve their declared example."
)
