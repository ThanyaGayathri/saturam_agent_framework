"""Every operation in the contract, called against the stub.

The parametrisation is the bundle itself, so a path added to the YAML is tested without a
test being written -- and a path whose example does not survive the trip through the stub
(and, for live routes, through the generated ``response_model``) fails by name.
"""

from __future__ import annotations

import re

import pytest

from egpt_retail.api.spec import load_bundle

from .conftest import bearer

_BUNDLE = load_bundle()
_METHODS = {"get", "post", "put", "patch", "delete"}
_LIVE = {
    "getSemanticSnapshot",
    "listSemanticTables",
    "getSemanticTable",
    "listSemanticConcepts",
    "getSemanticConcept",
    "listSemanticBusinessAreas",
    "findSemanticJoinPath",
}


def _operations():
    for route, item in _BUNDLE["paths"].items():
        for method, operation in item.items():
            if method in _METHODS:
                yield pytest.param(
                    route, method, operation, id=f"{method.upper()} {route}"
                )


def _first_example(operation):
    for code, response in operation.get("responses", {}).items():
        if not (code.isdigit() and 200 <= int(code) < 300):
            continue
        media = (response.get("content") or {}).get("application/json") or {}
        for name, example in (media.get("examples") or {}).items():
            return int(code), name, example["value"]
    return None


def _concrete(route: str) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: f"{m.group(1)}_x", route)


@pytest.mark.parametrize("route,method,operation", list(_operations()))
def test_every_operation_serves_its_declared_example(client, route, method, operation):
    """Example-served routes return exactly the first 2xx example with its status code."""
    if operation.get("operationId") in _LIVE:
        pytest.skip("live semantic route; covered by test_semantic_reads_are_live")
    url = _concrete(route)
    if route == "/semantic/join-path":
        url += "?from=customers&to=products"
    response = client.request(
        method.upper(), url, json={} if method in {"post", "put", "patch"} else None
    )
    expected = _first_example(operation)
    if expected is None:
        assert response.status_code == 204, (
            f"{method.upper()} {route}: no example and not 204"
        )
        return
    status, _name, value = expected
    assert response.status_code == status, response.text
    assert response.json() == value


def test_every_named_example_is_selectable(client):
    """``?example=<name>`` returns that example with the status code it was declared under."""
    checked = 0
    for route, item in _BUNDLE["paths"].items():
        for method, operation in item.items():
            if method not in _METHODS or operation.get("operationId") in _LIVE:
                continue
            for code, response in operation.get("responses", {}).items():
                media = (response.get("content") or {}).get("application/json") or {}
                for name, example in (media.get("examples") or {}).items():
                    r = client.request(
                        method.upper(), _concrete(route) + f"?example={name}", json={}
                    )
                    assert r.status_code == int(code), (
                        f"{method.upper()} {route} example {name!r}: {r.status_code}"
                    )
                    assert r.json() == example["value"]
                    checked += 1
    assert checked > 200


def test_unknown_example_is_a_400_naming_the_available_ones(client):
    r = client.get("/kpis?example=nope")
    assert r.status_code == 400
    assert r.json()["code"] == "unknown_example"
    assert "default" in r.json()["detail"]


def test_stub_mounts_every_operation_in_the_bundle(client):
    mounted = {
        (op["method"], op["path"])
        for op in client.get("/stub/operations").json()["operations"]
    }
    declared = {
        (m.upper(), r)
        for r, it in _BUNDLE["paths"].items()
        for m in it
        if m in _METHODS
    }
    assert mounted == declared


# --- the semantic reads are live, not examples -----------------------------------------


def test_semantic_reads_are_live(client):
    tables = client.get("/semantic/tables").json()
    assert {t["name"] for t in tables["tables"]} == {
        "orders",
        "order_items",
        "customers",
        "products",
        "inventory",
        "supplier_deliveries",
        "product_reviews",
    }
    assert tables["snapshot"]["version"] == "r3-contract.1"

    with_proposed = client.get("/semantic/tables?include_proposed=true").json()
    assert "web_sessions" in {t["name"] for t in with_proposed["tables"]}

    customers = client.get("/semantic/tables/customers").json()
    assert "email" not in {c["name"] for c in customers["columns"]}, (
        "a proposed column leaked into an accepted table"
    )
    customers_all = client.get(
        "/semantic/tables/customers?include_proposed=true"
    ).json()
    assert "email" in {c["name"] for c in customers_all["columns"]}

    assert client.get("/semantic/tables/shipments").status_code == 404
    concept = client.get("/semantic/concepts/StockLevel").json()
    assert concept["business_area"] == "inventory"
    assert "stocklevel__days_of_stock" in concept["metrics"]
    assert client.get("/semantic/business-areas").json()["business_areas"] == [
        "customer",
        "inventory",
        "product",
        "sales",
        "supply_chain",
    ]


def test_join_path_three_outcomes(client, unloaded_client):
    found = client.get("/semantic/join-path?from=customers&to=products")
    assert found.status_code == 200
    assert found.json()["path"]["hops"] == 3
    assert found.json()["path"]["intermediate_tables"] == ["orders", "order_items"]

    none = client.get("/semantic/join-path?from=supplier_deliveries&to=product_reviews")
    assert none.status_code == 200, "no path is an answer, not an error"
    assert none.json()["path"] is None

    typo = client.get("/semantic/join-path?from=customers&to=shipments")
    assert typo.status_code == 400
    assert typo.json()["code"] == "unknown_table"

    unavailable = unloaded_client.get("/semantic/join-path?from=customers&to=products")
    assert unavailable.status_code == 503, "cannot answer is not an answer"
    assert unavailable.json()["code"] == "semantic_unavailable"
    assert unloaded_client.get("/semantic/tables").status_code == 503


def test_live_semantic_responses_match_the_contract_examples(client):
    """The contract's semantic examples were drawn from the fixture the stub serves. Prove it."""
    example = _BUNDLE["paths"]["/semantic/tables"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["examples"]
    assert client.get("/semantic/tables").json() == example["default"]["value"]
    assert (
        client.get("/semantic/tables?include_proposed=true").json()
        == example["with_proposed"]["value"]
    )
    concept_example = _BUNDLE["paths"]["/semantic/concepts/{concept_name}"]["get"][
        "responses"
    ]["200"]["content"]["application/json"]["examples"]["default"]["value"]
    assert client.get("/semantic/concepts/StockLevel").json() == concept_example
    join_example = _BUNDLE["paths"]["/semantic/join-path"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["examples"]
    assert (
        client.get("/semantic/join-path?from=customers&to=products").json()
        == join_example["found"]["value"]
    )
    assert (
        client.get(
            "/semantic/join-path?from=supplier_deliveries&to=product_reviews"
        ).json()
        == join_example["no_path"]["value"]
    )


# --- scope gating ------------------------------------------------------------------------


def test_scope_gating_200_versus_403(gated_client):
    assert gated_client.get("/kpis").status_code == 401, "no token at all"
    assert gated_client.get("/kpis", headers=bearer("agents:read")).status_code == 403
    assert gated_client.get("/kpis", headers=bearer("kpis:read")).status_code == 200


def test_include_proposed_needs_its_own_scope(gated_client):
    reader = bearer("semantic:read")
    assert gated_client.get("/semantic/tables", headers=reader).status_code == 200
    denied = gated_client.get("/semantic/tables?include_proposed=true", headers=reader)
    assert denied.status_code == 403, (
        "unreviewed content must not be a silently filtered 200"
    )
    assert "semantic:read_proposed" in denied.json()["detail"]
    reviewer = bearer("semantic:read", "semantic:read_proposed")
    allowed = gated_client.get(
        "/semantic/tables?include_proposed=true", headers=reviewer
    )
    assert allowed.status_code == 200
    assert "web_sessions" in {t["name"] for t in allowed.json()["tables"]}


def test_activation_level_three_needs_admin_scope_is_documented(bundle):
    """The contract says it; the stub serves the example. The real check is R1/R2's."""
    op = bundle["paths"]["/use-cases/{use_case_id}/activate"]["post"]
    assert "use-cases:activate" in op["description"]
    assert "403" in op["responses"]
