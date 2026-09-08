"""Every operation in the contract, called against the stub.

The parametrisation is the bundle itself, so a path added to the YAML is tested without a
test being written -- and a path whose example does not survive the trip through the stub
(and, for live routes, through the generated ``response_model``) fails by name.
"""

from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

import egpt_retail.api.stub.server as stub_server
from egpt_retail.api.spec import load_bundle
from egpt_retail.api.stub import create_app

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


def test_stub_operations_reflects_a_route_that_failed_to_mount(monkeypatch, tmp_path):
    """Regression: `/stub/operations` must introspect FastAPI's own routing table, not
    replay the bundle it was built from. A version that just replayed the bundle reported
    an operation as mounted even when the mount loop skipped it -- indistinguishable from
    a route that genuinely mounted, and unable to ever fail."""
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(
        json.dumps(
            {
                "info": {"title": "t", "version": "0"},
                "paths": {
                    "/a": {
                        "get": {
                            "operationId": "opA",
                            "responses": {
                                "200": {
                                    "content": {
                                        "application/json": {
                                            "examples": {"default": {"value": {}}}
                                        }
                                    }
                                }
                            },
                        }
                    },
                    "/b": {
                        "get": {
                            "operationId": "opB",
                            "responses": {
                                "200": {
                                    "content": {
                                        "application/json": {
                                            "examples": {"default": {"value": {}}}
                                        }
                                    }
                                }
                            },
                        }
                    },
                },
            }
        )
    )

    real_mount = stub_server._mount

    def _skip_op_b(app, route, method, operation, live, jwt_secret):
        if operation.get("operationId") == "opB":
            return
        real_mount(app, route, method, operation, live, jwt_secret)

    monkeypatch.setattr(stub_server, "_mount", _skip_op_b)
    app = stub_server.create_app(bundle_path=bundle_path, fixture_path=None)
    reported = {
        op["operationId"]
        for op in TestClient(app).get("/stub/operations").json()["operations"]
    }
    assert reported == {"opA"}, (
        "a route that failed to mount must not be reported as mounted"
    )


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


def test_include_proposed_scope_check_only_applies_where_the_operation_declares_it(
    gated_client, bundle
):
    """Regression: a caller correctly scoped for an operation that has no
    `include_proposed` parameter must not be blocked by passing that query string anyway.
    `/kpis` never declares it -- confirm the contract still agrees, then prove it."""
    op = bundle["paths"]["/kpis"]["get"]
    assert not any(
        p.get("name") == "include_proposed" for p in op.get("parameters", [])
    )
    response = gated_client.get(
        "/kpis?include_proposed=true", headers=bearer("kpis:read")
    )
    assert response.status_code == 200


def test_activation_level_three_needs_admin_scope(gated_client, bundle):
    """Level >= 3 needs `use-cases:activate` in addition to `use-cases:write` (G11) --
    separation of duties enforced by role, not just documented."""
    op = bundle["paths"]["/use-cases/{use_case_id}/activate"]["post"]
    assert "use-cases:activate" in op["description"]
    assert "403" in op["responses"]

    writer = bearer("use-cases:write")
    admin = bearer("use-cases:write", "use-cases:activate")

    low_level = gated_client.post(
        "/use-cases/uc_0002/activate", json={"level": 1}, headers=writer
    )
    assert low_level.status_code == 200

    high_level_denied = gated_client.post(
        "/use-cases/uc_0002/activate", json={"level": 3}, headers=writer
    )
    assert high_level_denied.status_code == 403
    assert "use-cases:activate" in high_level_denied.json()["detail"]

    high_level_allowed = gated_client.post(
        "/use-cases/uc_0002/activate", json={"level": 3}, headers=admin
    )
    assert high_level_allowed.status_code == 200

    no_level = gated_client.post("/use-cases/uc_0002/activate", json={}, headers=writer)
    assert no_level.status_code == 200, (
        "an omitted level must not require the admin scope"
    )


@pytest.mark.parametrize("level", [3, 3.0, "3", 5.0, "5"])
def test_activation_level_gate_survives_non_int_encodings(gated_client, level):
    """Regression: the G11 gate used to compare `isinstance(level, int)`, so a JSON-legal
    float (`3.0`) or numeric string (`"3"`) skipped it entirely -- verified live on this
    branch before the fix. Every encoding of the same value must be gated alike."""
    writer = bearer("use-cases:write")
    denied = gated_client.post(
        "/use-cases/uc_0002/activate", json={"level": level}, headers=writer
    )
    assert denied.status_code == 403, (
        f"level={level!r} ({type(level).__name__}) must be gated the same as level=3 (int)"
    )


def test_activation_level_gate_not_defeated_by_an_unrelated_bad_field(gated_client):
    """Regression: validating the whole body against `ActivationRequest` (rather than just
    the `level` value) fails closed on an unrelated malformed field -- e.g. a bogus
    `autonomy` -- which resolves to "no level" and would have reopened the bypass this gate
    exists to close."""
    writer = bearer("use-cases:write")
    denied = gated_client.post(
        "/use-cases/uc_0002/activate",
        json={"level": 3, "autonomy": "not-a-real-autonomy"},
        headers=writer,
    )
    assert denied.status_code == 403


def test_activation_level_gate_ignores_an_unparsable_level(gated_client):
    """A `level` the schema cannot coerce to int (e.g. a non-numeric string) is treated as
    no level, the same as an omitted one -- an admin gate can only widen who is let
    through, never narrow it further than the schema already does."""
    writer = bearer("use-cases:write")
    allowed = gated_client.post(
        "/use-cases/uc_0002/activate", json={"level": "not-a-number"}, headers=writer
    )
    assert allowed.status_code == 200


def test_scope_check_requires_every_scope_not_just_one(tmp_path):
    """Regression: `set(required_scopes) <= granted` is an AND across every declared
    scope. No shipped operation currently declares more than one, so nothing in the real
    contract would catch a regression to OR-semantics -- this locks the rule in directly
    against a synthetic multi-scope operation."""
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(
        json.dumps(
            {
                "info": {"title": "t", "version": "0"},
                "paths": {
                    "/multi-scope": {
                        "get": {
                            "operationId": "multiScopeOp",
                            "x-egpt-scopes": ["scope-a", "scope-b"],
                            "responses": {
                                "200": {
                                    "content": {
                                        "application/json": {
                                            "examples": {
                                                "default": {"value": {"ok": True}}
                                            }
                                        }
                                    }
                                }
                            },
                        }
                    }
                },
            }
        )
    )
    secret = "multi-scope-tests-only"
    app = create_app(bundle_path=bundle_path, fixture_path=None, jwt_secret=secret)
    gated = TestClient(app)

    def token(*scopes):
        from egpt_retail.api.stub import mint_token

        return {"Authorization": f"Bearer {mint_token(secret, scopes)}"}

    assert gated.get("/multi-scope", headers=token("scope-a")).status_code == 403
    assert gated.get("/multi-scope", headers=token("scope-b")).status_code == 403
    assert (
        gated.get("/multi-scope", headers=token("scope-a", "scope-b")).status_code
        == 200
    )


def test_served_example_is_validated_against_its_generated_model(tmp_path):
    """Regression: an operation's served example is checked against the model its response
    schema names, even when the operation is not one of the seven live semantic reads --
    previously only those seven got `response_model` at all, so a corrupted or drifted
    example on any of the other ~98 operations passed through unnoticed."""
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(
        json.dumps(
            {
                "info": {"title": "t", "version": "0"},
                "paths": {
                    "/broken": {
                        "get": {
                            "operationId": "brokenOp",
                            "responses": {
                                "200": {
                                    "content": {
                                        "application/json": {
                                            "schema": {
                                                "$ref": "#/components/schemas/Kpi"
                                            },
                                            "examples": {
                                                "default": {
                                                    "value": {
                                                        "name": "missing required fields"
                                                    }
                                                }
                                            },
                                        }
                                    }
                                }
                            },
                        }
                    }
                },
            }
        )
    )
    app = create_app(bundle_path=bundle_path, fixture_path=None)
    response = TestClient(app).get("/broken")
    assert response.status_code == 500
    assert response.json()["code"] == "example_schema_drift"


def test_servers_list_names_the_stub_s_actual_port():
    """Regression: `servers` named only 7777 (the real EgptOS/R1 port) and its description
    claimed it also covered "the phase-zero stub server", but the stub's own default
    (`create_app`/`serve`'s `port: int = 7778`, `__main__.py`'s `EGPT_STUB_PORT` default) is
    7778 -- a different port, deliberately, so it does not collide with R1. Any generated
    client or "Try it out" against the old document defaulted to the wrong port for the
    stub."""
    import inspect

    from egpt_retail.api.stub.server import serve

    stub_default_port = inspect.signature(serve).parameters["port"].default
    server_urls = {entry["url"] for entry in _BUNDLE["servers"]}
    assert f"http://127.0.0.1:{stub_default_port}" in server_urls


def test_bundle_matches_committed_has_an_async_twin():
    """CLAUDE.md: every public I/O-bound method needs both sync and async variants.
    Regression: `bundle_matches_committed` had no `abundle_matches_committed`, unlike every
    sibling in this module (`bundle`/`abundle`, `load_bundle`/`aload_bundle`,
    `write_bundle`/`awrite_bundle`)."""
    import asyncio

    from egpt_retail.api.spec import abundle_matches_committed, bundle_matches_committed

    assert asyncio.run(abundle_matches_committed()) == bundle_matches_committed()


def test_fixture_matches_committed_has_an_async_twin():
    """Same regression as `bundle_matches_committed`, for its sibling in fixture_builder.py.
    `fixture_matches_committed` is even in `__all__`, unlike its now-added async twin's
    absence before this fix."""
    import asyncio

    from egpt_retail.contracts.fixture_builder import (
        afixture_matches_committed,
        fixture_matches_committed,
    )

    assert asyncio.run(afixture_matches_committed()) == fixture_matches_committed()


def test_model_for_resolves_through_generated_module_not_the_reexport_list(monkeypatch):
    """Regression: `_model_for` used to resolve a schema's model via
    `egpt_retail.api.models`, whose `__init__.py` re-exports classes through a hand-written
    import list rather than deriving it from `_generated.py`. A schema that gains a class in
    `_generated.py` before someone remembers to add it to that list must still resolve --
    simulated here by removing the name from the re-export namespace, which is exactly what
    an incomplete `__init__.py` looks like."""
    from egpt_retail.api import models as generated_models

    assert hasattr(generated_models, "ActivationRequest")
    monkeypatch.delattr(generated_models, "ActivationRequest")
    resolved = stub_server._model_for(
        {"$ref": "#/components/schemas/ActivationRequest"}
    )
    assert resolved is not None
    assert resolved.__name__ == "ActivationRequest"


def test_concept_personal_data_fields_never_names_a_column_still_proposed():
    """Regression: `Customer.personal_data_fields` named `email` unconditionally even
    though `customers.email` is a deliberately proposed column (fixture_builder.py's
    `_TABLES["customers"]`), so a default (accepted-only) read of
    `/semantic/concepts/Customer` leaked the existence of an unreviewed personal-data
    column -- exactly what "nothing unconfirmed leaks into an answer"
    (contracts/semantic.py) exists to prevent. Checked for every concept and every table,
    not just Customer/email, since the invariant is general: an accepted concept must never
    name a column that is itself still proposed."""
    from egpt_retail.contracts.fixture_builder import build_contract_fixture

    fixture = build_contract_fixture()
    proposed_columns = {
        (table["name"], column["name"])
        for table in fixture["tables"]
        for column in table["columns"]
        if column.get("status") == "proposed"
    }
    for concept in fixture["concepts"]:
        if concept.get("status") == "proposed":
            continue
        for field in concept["personal_data_fields"]:
            named = {
                (table["name"], field)
                for table in fixture["tables"]
                if field in {c["name"] for c in table["columns"]}
            }
            assert not (named & proposed_columns), (
                f"accepted concept {concept['name']!r} names column {field!r}, which is "
                "still proposed on at least one table"
            )
