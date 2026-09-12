"""``/business-areas`` (issue #225, B3): the hierarchy module and the router, without a graph
or a database.

The hierarchy is a pure function over two JSON payloads, so its G10 rules are tested directly.
The router is exercised over a real FastAPI ``TestClient`` with the one graph call
(``_supported``) monkeypatched -- K9's detection itself is covered by
``test_k9_business_areas.py``, and the graph query by the live suite and the K09 cookbook.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from egpt_retail.api.routers import business_areas as router_module
from egpt_retail.contracts.vocabulary import CORE_PATH, OverlayConflict, TermOrigin
from egpt_retail.ontology.business_areas import (
    HIERARCHY_KEY,
    BusinessAreaNode,
    apply_overlay,
    build_hierarchy,
    default_label,
    load_business_area_hierarchy,
    read_overlay,
    write_overlay,
)

CORE = json.loads(CORE_PATH.read_text())
CORE_IDS = [term["value"] for term in CORE["vocabularies"]["business_area"]]


def _overlay_adding(area_id: str, description: str = "", **note) -> dict:
    payload = {
        "vocabularies": {
            "business_area": [{"value": area_id, "description": description}]
        }
    }
    if note:
        payload[HIERARCHY_KEY] = {area_id: note}
    return payload


# ---------------------------------------------------------------------------
# The hierarchy: core, overlay, and the G10 rules on the annotations
# ---------------------------------------------------------------------------


def test_core_hierarchy_carries_every_term_with_a_label_and_subject_areas():
    hierarchy = build_hierarchy(CORE)
    assert [area.id for area in hierarchy] == CORE_IDS
    assert hierarchy.version == CORE["version"]
    assert all(area.origin is TermOrigin.CORE for area in hierarchy)
    assert all(area.subject_areas for area in hierarchy), (
        "every core area has subject areas"
    )
    assert hierarchy.get("supply_chain").label == "Supply Chain"
    assert "Stock Health" in hierarchy.get("inventory").subject_areas
    assert hierarchy.get("sales").description == "Revenue, conversion and pipeline."


def test_the_shipped_file_loads_through_the_public_loader_with_no_overlay():
    assert load_business_area_hierarchy() == build_hierarchy(CORE)


def test_default_label_title_cases_the_id():
    assert default_label("store_ops") == "Store Ops"
    assert default_label("sales") == "Sales"


def test_an_overlay_adds_an_area_with_its_own_label_and_subject_areas():
    overlay = _overlay_adding(
        "store_ops",
        "In-store operations.",
        label="Store Ops",
        subject_areas=["Labor & Staffing", "Foot Traffic"],
    )
    hierarchy = build_hierarchy(CORE, overlay)
    assert [area.id for area in hierarchy] == CORE_IDS + ["store_ops"]
    added = hierarchy.get("store_ops")
    assert added.origin is TermOrigin.OVERLAY
    assert added.label == "Store Ops"
    assert added.description == "In-store operations."
    assert added.subject_areas == ("Labor & Staffing", "Foot Traffic")


def test_an_added_area_with_no_label_is_labelled_from_its_id():
    hierarchy = build_hierarchy(CORE, _overlay_adding("store_ops"))
    assert hierarchy.get("store_ops").label == "Store Ops"


def test_an_overlay_annotates_a_core_area_by_adding_subject_areas():
    """Annotation is additive and idempotent: core subject areas first, then the overlay's
    additions, a repeat of a core one contributing nothing."""
    core_sales = build_hierarchy(CORE).get("sales").subject_areas
    overlay = {
        HIERARCHY_KEY: {"sales": {"subject_areas": ["Revenue", "Quote to Deal"]}}
    }
    sales = build_hierarchy(CORE, overlay).get("sales")
    assert sales.subject_areas == core_sales + ("Quote to Deal",)
    assert sales.origin is TermOrigin.CORE


def test_an_overlay_may_not_redefine_a_core_description():
    overlay = _overlay_adding("sales", "Something else entirely.")
    with pytest.raises(OverlayConflict, match="redefines core term 'sales'"):
        build_hierarchy(CORE, overlay)


def test_an_overlay_may_not_relabel_a_core_area():
    overlay = {HIERARCHY_KEY: {"sales": {"label": "Revenue"}}}
    with pytest.raises(OverlayConflict, match="relabels core business area 'sales'"):
        build_hierarchy(CORE, overlay)


def test_an_annotation_of_an_unknown_area_is_malformed_not_a_conflict():
    overlay = {HIERARCHY_KEY: {"nope": {"label": "Nope"}}}
    with pytest.raises(ValueError, match="no business_area term defines") as excinfo:
        build_hierarchy(CORE, overlay)
    assert not isinstance(excinfo.value, OverlayConflict)


def test_underscore_keys_in_the_hierarchy_are_comments_not_areas():
    overlay = {HIERARCHY_KEY: {"_comment": "free text, not an area"}}
    assert build_hierarchy(CORE, overlay) == build_hierarchy(CORE)


# ---------------------------------------------------------------------------
# apply_overlay: what PUT writes
# ---------------------------------------------------------------------------


EXISTING_OVERLAY = {
    "version": "acme.1",
    "concepts": [
        {
            "name": "RepGroup",
            "description": "A group of sales reps.",
            "business_area": "sales",
        }
    ],
    "vocabularies": {
        "business_area": [{"value": "old_area", "description": "Added last time."}],
        "grain": [{"value": "rep_group", "description": "One row per rep group."}],
    },
    HIERARCHY_KEY: {"old_area": {"label": "Old"}},
}


def test_put_replaces_only_the_overlay_business_areas_and_keeps_everything_else():
    areas = [
        BusinessAreaNode(
            "store_ops", "Store Ops", "In-store operations.", ("Foot Traffic",)
        ),
        BusinessAreaNode("sales", "", "", ("Quote to Deal",)),
    ]
    payload = apply_overlay(EXISTING_OVERLAY, areas, CORE)

    assert payload["version"] == "acme.1"
    assert payload["concepts"] == EXISTING_OVERLAY["concepts"]
    assert payload["vocabularies"]["grain"] == EXISTING_OVERLAY["vocabularies"]["grain"]
    assert payload["vocabularies"]["business_area"] == [
        {"value": "store_ops", "description": "In-store operations."}
    ]
    assert payload[HIERARCHY_KEY] == {
        "store_ops": {"label": "Store Ops", "subject_areas": ["Foot Traffic"]},
        "sales": {"subject_areas": ["Quote to Deal"]},
    }
    # And what it wrote is what the loader reads back.
    hierarchy = build_hierarchy(CORE, payload)
    assert hierarchy.get("old_area") is None
    assert hierarchy.get("store_ops").origin is TermOrigin.OVERLAY
    assert "Quote to Deal" in hierarchy.get("sales").subject_areas


def test_put_with_no_areas_clears_the_overlay_contribution_and_nothing_else():
    payload = apply_overlay(EXISTING_OVERLAY, [], CORE)
    assert "business_area" not in payload["vocabularies"]
    assert HIERARCHY_KEY not in payload
    assert payload["vocabularies"]["grain"] == EXISTING_OVERLAY["vocabularies"]["grain"]
    assert payload["concepts"] == EXISTING_OVERLAY["concepts"]


def test_put_from_no_overlay_at_all_produces_a_minimal_payload():
    payload = apply_overlay(None, [BusinessAreaNode("store_ops", "", "")], CORE)
    assert payload == {
        "vocabularies": {"business_area": [{"value": "store_ops", "description": ""}]},
        HIERARCHY_KEY: {"store_ops": {"label": "Store Ops", "subject_areas": []}},
    }


def test_put_naming_a_core_area_with_its_own_description_and_label_is_a_no_op():
    core_sales = build_hierarchy(CORE).get("sales")
    areas = [
        BusinessAreaNode(
            "sales", core_sales.label, core_sales.description, core_sales.subject_areas
        )
    ]
    assert apply_overlay(None, areas, CORE) == {}


@pytest.mark.parametrize(
    "area, fragment",
    [
        (BusinessAreaNode("sales", "", "Different."), "redefines core term 'sales'"),
        (
            BusinessAreaNode("sales", "Revenue", ""),
            "relabels core business area 'sales'",
        ),
    ],
)
def test_put_refuses_a_core_redefinition_as_a_conflict(area, fragment):
    with pytest.raises(OverlayConflict, match=fragment):
        apply_overlay(None, [area], CORE)


@pytest.mark.parametrize(
    "areas, fragment",
    [
        ([BusinessAreaNode("Store-Ops", "", "")], "alphanumeric"),
        (
            [
                BusinessAreaNode("store_ops", "", ""),
                BusinessAreaNode("store_ops", "", ""),
            ],
            "listed twice",
        ),
    ],
)
def test_put_refuses_a_malformed_request_as_a_value_error_not_a_conflict(
    areas, fragment
):
    with pytest.raises(ValueError, match=fragment) as excinfo:
        apply_overlay(None, areas, CORE)
    assert not isinstance(excinfo.value, OverlayConflict)


def test_write_overlay_round_trips_and_leaves_no_temp_file(tmp_path: Path):
    target = tmp_path / "overlay.json"
    payload = apply_overlay(None, [BusinessAreaNode("store_ops", "", "")], CORE)
    write_overlay(target, payload)
    assert read_overlay(target) == payload
    assert [p.name for p in tmp_path.iterdir()] == ["overlay.json"]


def test_read_overlay_treats_a_missing_or_unconfigured_file_as_no_overlay(
    tmp_path: Path,
):
    assert read_overlay(None) is None
    assert read_overlay(tmp_path / "not-yet.json") is None


# ---------------------------------------------------------------------------
# The router, over HTTP, with the graph call replaced
# ---------------------------------------------------------------------------


def _client(*, authorization: bool = False, scopes=()) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _fake_auth(request: Request, call_next):
        request.state.authorization_enabled = authorization
        request.state.scopes = list(scopes)
        request.state.admin_scope = None
        return await call_next(request)

    app.include_router(router_module.router)
    return TestClient(app)


@pytest.fixture
def graph_says(monkeypatch):
    """Pin K9's answer: the areas a confirmed mapping backs."""

    def _pin(*area_ids: str):
        monkeypatch.setattr(
            router_module, "_supported", lambda settings: frozenset(area_ids)
        )

    _pin("sales")
    return _pin


@pytest.fixture
def overlay_file(tmp_path: Path, monkeypatch) -> Path:
    path = tmp_path / "overlay.json"
    monkeypatch.setenv("EGPT_RETAIL_ONTOLOGY_OVERLAY", str(path))
    return path


@pytest.fixture
def no_overlay(monkeypatch):
    monkeypatch.delenv("EGPT_RETAIL_ONTOLOGY_OVERLAY", raising=False)


def test_get_renders_every_area_with_supported_from_the_graph(graph_says, no_overlay):
    response = _client().get("/business-areas")
    assert response.status_code == 200
    body = response.json()
    assert body["vocabulary_version"] == CORE["version"]
    assert [area["id"] for area in body["areas"]] == CORE_IDS
    by_id = {area["id"]: area for area in body["areas"]}
    assert by_id["sales"]["supported"] is True
    assert all(not area["supported"] for area in body["areas"] if area["id"] != "sales")
    assert by_id["supply_chain"]["label"] == "Supply Chain"
    assert by_id["inventory"]["subject_areas"]
    assert by_id["sales"]["origin"] == "core"


def test_get_supported_true_filters_to_the_backed_areas(graph_says, no_overlay):
    graph_says("sales", "inventory")
    body = _client().get("/business-areas", params={"supported": "true"}).json()
    assert [area["id"] for area in body["areas"]] == ["inventory", "sales"]


def test_get_reflects_the_configured_overlay(graph_says, overlay_file):
    write_overlay(
        overlay_file,
        _overlay_adding("store_ops", "In-store operations.", label="Store Ops"),
    )
    body = _client().get("/business-areas").json()
    added = next(area for area in body["areas"] if area["id"] == "store_ops")
    assert added["origin"] == "overlay"
    assert added["supported"] is False


@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError("EGPT_RETAIL_NEO4J_URI is not set"),
        pytest.param(
            "service_unavailable",
            id="neo4j.exceptions.ServiceUnavailable",
        ),
    ],
)
def test_get_answers_503_when_the_graph_cannot_be_asked(
    monkeypatch, no_overlay, failure
):
    """An unreachable graph is not the same fact as "nothing is supported", and must not
    be rendered as one."""
    if failure == "service_unavailable":
        from neo4j.exceptions import ServiceUnavailable

        failure = ServiceUnavailable("connection refused")

    def _down(settings):
        raise failure

    monkeypatch.setattr(router_module, "semantic_driver", _down)
    response = _client().get("/business-areas")
    assert response.status_code == 503
    assert "semantic layer" in response.json()["detail"]


def test_get_answers_503_for_a_broken_overlay_file(graph_says, overlay_file):
    overlay_file.write_text(json.dumps({HIERARCHY_KEY: {"nope": {"label": "x"}}}))
    response = _client().get("/business-areas")
    assert response.status_code == 503
    assert "cannot be read" in response.json()["detail"]


def test_put_writes_the_overlay_and_the_next_get_sees_it(graph_says, overlay_file):
    client = _client()
    response = client.put(
        "/business-areas",
        json={
            "areas": [
                {
                    "id": "store_ops",
                    "label": "Store Ops",
                    "subject_areas": ["Labor & Staffing", "Foot Traffic"],
                    "description": "In-store operations.",
                }
            ]
        },
    )
    assert response.status_code == 200, response.text
    added = next(a for a in response.json()["areas"] if a["id"] == "store_ops")
    assert added["origin"] == "overlay"
    assert added["subject_areas"] == ["Labor & Staffing", "Foot Traffic"]
    assert added["supported"] is False

    assert read_overlay(overlay_file)["vocabularies"]["business_area"] == [
        {"value": "store_ops", "description": "In-store operations."}
    ]
    assert [
        a["id"] for a in client.get("/business-areas").json()["areas"]
    ] == CORE_IDS + ["store_ops"]


def test_put_keeps_the_overlay_concepts_it_did_not_touch(graph_says, overlay_file):
    write_overlay(overlay_file, EXISTING_OVERLAY)
    response = _client().put("/business-areas", json={"areas": []})
    assert response.status_code == 200, response.text
    after = read_overlay(overlay_file)
    assert after["concepts"] == EXISTING_OVERLAY["concepts"]
    assert "business_area" not in after["vocabularies"]
    assert [a["id"] for a in response.json()["areas"]] == CORE_IDS


def test_put_refuses_a_core_redefinition_with_409_and_writes_nothing(
    graph_says, overlay_file
):
    response = _client().put(
        "/business-areas",
        json={
            "areas": [{"id": "sales", "label": "Sales", "description": "Different."}]
        },
    )
    assert response.status_code == 409
    assert "G10" in response.json()["detail"]
    assert not overlay_file.exists()


def test_put_refuses_a_malformed_id_with_422(graph_says, overlay_file):
    response = _client().put(
        "/business-areas", json={"areas": [{"id": "Store-Ops", "label": "Store Ops"}]}
    )
    assert response.status_code == 422
    assert not overlay_file.exists()


def test_put_without_an_overlay_configured_is_503(graph_says, no_overlay):
    response = _client().put("/business-areas", json={"areas": []})
    assert response.status_code == 503
    assert "EGPT_RETAIL_ONTOLOGY_OVERLAY" in response.json()["detail"]


def test_scopes_are_the_contracts_own(graph_says, overlay_file):
    """``config:read`` for GET (the framework's config scope), ``configure:write`` for PUT."""
    assert (
        _client(authorization=True, scopes=()).get("/business-areas").status_code == 403
    )
    assert (
        _client(authorization=True, scopes=("config:read",))
        .get("/business-areas")
        .status_code
        == 200
    )
    assert (
        _client(authorization=True, scopes=("config:read",))
        .put("/business-areas", json={"areas": []})
        .status_code
        == 403
    )
    assert (
        _client(authorization=True, scopes=("configure:write",))
        .put("/business-areas", json={"areas": []})
        .status_code
        == 200
    )


def test_the_factory_mounts_the_router():
    import inspect

    from egpt_retail.app import factory

    assert "business_areas_router" in inspect.getsource(factory.build_agent_os)
