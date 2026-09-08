"""R3 -- the semantic read API over HTTP, with the two contract decisions Lane R made.

Lane K's `SemanticRead` distinguishes three outcomes on a join path: found, absent, and
could-not-answer. Over HTTP those become 200 with a path, 200 with `path: null`, and 503 --
never 404 for "no path", because every client collapses 404 into "empty". And
`include_proposed=true`, harmless in-process, is an attacker-controlled query parameter over
HTTP, so it requires the `semantic:read_proposed` scope.

This script proves both against the stub, plus that the seven reads answer live from the
contract fixture rather than from canned examples.

Run:
    .venv/bin/python cookbook/94_retail/R03_semantic_over_http.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "product"))

from egpt_retail.api.stub import create_app, mint_token  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

SECRET = "cookbook-only"

print("R3 -- semantic read API over HTTP")
print("=" * 78)

open_client = TestClient(create_app())
gated_client = TestClient(create_app(jwt_secret=SECRET))
unloaded_client = TestClient(create_app(fixture_path=None))


def bearer(*scopes: str) -> dict:
    return {"Authorization": f"Bearer {mint_token(SECRET, scopes)}"}


# --- 1. Seven reads, live -----------------------------------------------------------------

print()
print("1. The seven reads answer from the fixture")
print("-" * 78)
snapshot = open_client.get("/semantic/snapshot").json()
tables = open_client.get("/semantic/tables").json()["tables"]
table = open_client.get("/semantic/tables/orders").json()
concepts = open_client.get("/semantic/concepts").json()["concepts"]
concept = open_client.get("/semantic/concepts/StockLevel").json()
areas = open_client.get("/semantic/business-areas").json()["business_areas"]
print("   snapshot       :", snapshot["version"])
print("   tables         :", len(tables), "->", ", ".join(t["name"] for t in tables))
print("   orders columns :", ", ".join(c["name"] for c in table["columns"]))
print("   concepts       :", ", ".join(concepts))
print(
    "   StockLevel     :", concept["business_area"], "|", ", ".join(concept["metrics"])
)
print("   business areas :", ", ".join(areas))
missing_table = open_client.get("/semantic/tables/shipments")
print("   unknown table  :", missing_table.status_code, missing_table.json()["code"])

# --- 2. Join path: three outcomes on the wire -------------------------------------------------

print()
print("2. Join path -- three outcomes, three different responses")
print("-" * 78)
found = open_client.get("/semantic/join-path?from=customers&to=products")
absent = open_client.get(
    "/semantic/join-path?from=supplier_deliveries&to=product_reviews"
)
typo = open_client.get("/semantic/join-path?from=customers&to=shipments")
unavailable = unloaded_client.get("/semantic/join-path?from=customers&to=products")
print(
    f"   found       -> {found.status_code}  path.hops={found.json()['path']['hops']} via {found.json()['path']['intermediate_tables']}"
)
print(
    f"   no path     -> {absent.status_code}  path={absent.json()['path']}  (an answer)"
)
print(f"   unknown tbl -> {typo.status_code}  {typo.json()['code']}")
print(
    f"   unavailable -> {unavailable.status_code}  {unavailable.json()['code']}  (not an answer)"
)

# --- 3. include_proposed needs its own scope --------------------------------------------------

print()
print("3. include_proposed=true is scope-gated over HTTP")
print("-" * 78)
no_token = gated_client.get("/semantic/tables")
reader_ok = gated_client.get("/semantic/tables", headers=bearer("semantic:read"))
reader_denied = gated_client.get(
    "/semantic/tables?include_proposed=true", headers=bearer("semantic:read")
)
reviewer = gated_client.get(
    "/semantic/tables?include_proposed=true",
    headers=bearer("semantic:read", "semantic:read_proposed"),
)
wrong_scope = gated_client.get("/semantic/tables", headers=bearer("agents:read"))
print("   no token                          ->", no_token.status_code)
print(
    "   semantic:read                     ->",
    reader_ok.status_code,
    f"({len(reader_ok.json()['tables'])} tables)",
)
print(
    "   semantic:read + include_proposed  ->",
    reader_denied.status_code,
    reader_denied.json().get("code"),
)
print(
    "   + semantic:read_proposed          ->",
    reviewer.status_code,
    f"({len(reviewer.json()['tables'])} tables)",
)
print("   agents:read only                  ->", wrong_scope.status_code)

problems = []
if len(tables) != 7 or "web_sessions" in {t["name"] for t in tables}:
    problems.append("the default table list is wrong or leaked a proposed table")
if missing_table.status_code != 404:
    problems.append("an unknown table was not a 404")
if found.status_code != 200 or found.json()["path"]["hops"] != 3:
    problems.append("the found path did not come back as 200 with three hops")
if absent.status_code != 200 or absent.json()["path"] is not None:
    problems.append("no path did not come back as 200 with path null")
if typo.status_code != 400:
    problems.append("an unknown endpoint table was not a 400")
if unavailable.status_code != 503:
    problems.append("an unloaded reader was not a 503")
if (
    no_token.status_code,
    reader_ok.status_code,
    reader_denied.status_code,
    reviewer.status_code,
    wrong_scope.status_code,
) != (
    401,
    200,
    403,
    200,
    403,
):
    problems.append("scope gating did not produce 401 / 200 / 403 / 200 / 403")
if "web_sessions" not in {t["name"] for t in reviewer.json()["tables"]}:
    problems.append("the reviewer did not receive the proposed table")

print()
print("=" * 78)
if problems:
    print("Semantic read over HTTP does NOT honour the contract:")
    for problem in problems:
        print("  -", problem)
    raise SystemExit(1)
print(
    "Seven reads live; three join-path outcomes distinct on the wire; unreviewed content gated."
)
