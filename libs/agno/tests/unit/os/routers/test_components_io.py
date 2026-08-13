"""Export and import over HTTP (EGPT-2.8).

A real temp-file SQLite database rather than a mock: the interesting behaviour is what happens
to *rows* -- that a 422 leaves none behind, that an import evicts the hydration cache, that an
export never emits a connection string -- and a mock would only confirm which methods were
called.

Two properties here are easy to break by accident and get their own tests:

* the scope map must name both new routes explicitly, because ``GET /components/*`` already
  fnmatches ``/components/{id}/export`` and would authorize it by accident;
* an import validates the whole bundle before writing anything, so a bundle that fails on its
  last component leaves no partial state.
"""

import os
import tempfile

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from egpt.os.routers.components.components import attach_routes
from egpt.os.scopes import get_default_scope_mappings, get_required_scopes_for_route
from egpt.registry import Registry
from egpt.registry.hydrate import ComponentConfigCache

AGENT_CONFIG = {
    "id": "faq-agent",
    "name": "FAQ Agent",
    "model": {"id": "gpt-5.5", "name": "OpenAIResponses", "provider": "OpenAI"},
}


@pytest.fixture
def db():
    from egpt.integrations.db.sqlite.sqlite import SqliteDb

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield SqliteDb(db_file=path, id="os-db")
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def registry(db):
    reg = Registry()
    reg.add_db(db)
    reg.component_config_cache = ComponentConfigCache(ttl_seconds=300)
    return reg


@pytest.fixture
def client(db, registry):
    app = FastAPI()
    app.include_router(attach_routes(APIRouter(), db, registry=registry))
    return TestClient(app)


@pytest.fixture
def seeded(db):
    from egpt.db.base import ComponentType

    db.create_component_with_config(
        component_id="faq-agent",
        component_type=ComponentType.AGENT,
        name="FAQ Agent",
        config=AGENT_CONFIG,
        stage="published",
        tenant_id=None,
    )
    db.create_component_with_config(
        component_id="support-team",
        component_type=ComponentType.TEAM,
        name="Support Team",
        config={
            "id": "support-team",
            "name": "Support Team",
            "model": {"id": "gpt-5.5", "name": "OpenAIResponses", "provider": "OpenAI"},
            "members": [{"type": "agent", "agent_id": "faq-agent"}],
        },
        stage="published",
        links=[
            {
                "link_kind": "member",
                "link_key": "member_0",
                "child_component_id": "faq-agent",
                "child_version": 1,
                "position": 0,
                "meta": {"type": "agent"},
            }
        ],
        tenant_id=None,
    )


class TestScopeMapping:
    """`GET /components/*` already fnmatches the export path.

    The fallback takes the first pattern that matches in insertion order, so without explicit
    entries above the wildcards these routes would be authorized by accident -- and a later
    reordering of that dict would silently change who can call them.
    """

    def test_export_requires_components_read(self):
        scopes = get_required_scopes_for_route(get_default_scope_mappings(), "GET", "/components/agent-1/export")

        assert scopes == ["components:read"]

    def test_import_requires_components_write(self):
        scopes = get_required_scopes_for_route(get_default_scope_mappings(), "POST", "/components/import")

        assert scopes == ["components:write"]

    def test_the_entries_sit_above_the_wildcards(self):
        keys = list(get_default_scope_mappings())

        assert keys.index("GET /components/*/export") < keys.index("GET /components/*")


class TestExport:
    def test_it_returns_a_bundle_with_dependencies(self, client, seeded):
        response = client.get("/components/support-team/export")

        assert response.status_code == 200
        body = response.json()
        assert [entry["component_id"] for entry in body["components"]] == ["faq-agent", "support-team"]
        assert body["root"] == "support-team"

    def test_it_offers_the_bundle_as_a_download(self, client, seeded):
        response = client.get("/components/support-team/export")

        assert response.headers["content-disposition"] == 'attachment; filename="support-team.egpt.json"'

    def test_include_dependencies_false_returns_only_the_root(self, client, seeded):
        response = client.get("/components/support-team/export", params={"include_dependencies": False})

        assert [entry["component_id"] for entry in response.json()["components"]] == ["support-team"]

    def test_a_missing_component_is_a_404(self, client, seeded):
        assert client.get("/components/nope/export").status_code == 404

    def test_secrets_never_leave_over_http(self, client, db):
        """There is no `include_secrets` on this route, deliberately.

        `components:read` is a read scope, not a licence to extract the database password out
        of a stored config. An operator who needs the DSN already has it and can use the CLI.
        """
        from egpt.db.base import ComponentType

        db.create_component_with_config(
            component_id="leaky-agent",
            component_type=ComponentType.AGENT,
            name="Leaky Agent",
            config={**AGENT_CONFIG, "id": "leaky-agent", "db": {"id": "os-db", "db_url": "postgresql://u:p@h/d"}},
            stage="published",
            tenant_id=None,
        )

        response = client.get("/components/leaky-agent/export", params={"include_secrets": True})

        body = response.json()
        assert "db_url" not in body["components"][0]["config"]["db"]
        assert body["redacted"] == ["components[0].config.db.db_url"]


class TestImport:
    def test_it_creates_every_component_and_reports_each(self, client, seeded, db):
        document = client.get("/components/support-team/export").json()

        response = client.post("/components/import", json={"document": document, "id_prefix": "copy-"})

        assert response.status_code == 201
        body = response.json()
        assert body["created"] == 2
        assert [item["action"] for item in body["results"]] == ["created", "created"]
        assert db.get_component("copy-support-team", tenant_id=None) is not None

    def test_a_colliding_id_is_a_409(self, client, seeded):
        document = client.get("/components/support-team/export").json()

        response = client.post("/components/import", json={"document": document})

        assert response.status_code == 409
        assert response.json()["detail"] == "Component faq-agent already exists"

    def test_an_invalid_config_is_a_422_with_field_level_errors(self, client, db):
        document = {
            "format": "egpt.component-bundle",
            "format_version": 1,
            "components": [
                {
                    "component_id": "broken-agent",
                    "component_type": "agent",
                    "name": "Broken Agent",
                    "config": {"id": "broken-agent", "model": {"id": "x", "provider": "no-such-provider"}},
                    "links": [],
                }
            ],
        }

        response = client.post("/components/import", json={"document": document})

        assert response.status_code == 422
        assert response.json()["error_code"] == "CONFIG_VALIDATION_ERROR"
        assert response.json()["errors"][0]["path"].startswith("config.")

    def test_a_failed_validation_writes_nothing_at_all(self, client, db):
        """The reason import validates the whole bundle before touching the database.

        The first component here is perfectly valid. If writes happened as each component was
        planned, it would land and the caller would be left with half a bundle and a 422.
        """
        document = {
            "format": "egpt.component-bundle",
            "format_version": 1,
            "components": [
                {
                    "component_id": "good-agent",
                    "component_type": "agent",
                    "name": "Good Agent",
                    "config": {**AGENT_CONFIG, "id": "good-agent"},
                    "links": [],
                },
                {
                    "component_id": "broken-agent",
                    "component_type": "agent",
                    "name": "Broken Agent",
                    "config": {"id": "broken-agent", "model": {"id": "x", "provider": "no-such-provider"}},
                    "links": [],
                },
            ],
        }

        response = client.post("/components/import", json={"document": document})

        assert response.status_code == 422
        assert db.get_component("good-agent", tenant_id=None) is None

    def test_a_malformed_bundle_is_a_400(self, client):
        response = client.post("/components/import", json={"document": {"not": "a bundle"}})

        assert response.status_code == 400
        assert "component bundle" in response.json()["detail"]

    def test_a_dangling_link_is_a_400_naming_the_offender(self, client):
        document = {
            "format": "egpt.component-bundle",
            "format_version": 1,
            "components": [
                {
                    "component_id": "lonely-team",
                    "component_type": "team",
                    "name": "Lonely Team",
                    "config": {
                        "id": "lonely-team",
                        "model": {"id": "gpt-5.5", "name": "OpenAIResponses", "provider": "OpenAI"},
                        "members": [{"type": "agent", "agent_id": "absent-agent"}],
                    },
                    "links": [
                        {
                            "link_kind": "member",
                            "link_key": "member_0",
                            "child_component_id": "absent-agent",
                            "position": 0,
                        }
                    ],
                }
            ],
        }

        response = client.post("/components/import", json={"document": document})

        assert response.status_code == 400
        assert "absent-agent" in response.json()["detail"]

    def test_a_root_only_export_still_imports_where_the_members_already_exist(self, client, seeded, db):
        """A link to something outside the bundle is a reference, not a dangling pointer.

        Exporting without dependencies is legitimate precisely when the target already holds
        them, and the id rewrite must leave those references alone -- so `copy-support-team`
        points at the original `faq-agent`.
        """
        document = client.get("/components/support-team/export", params={"include_dependencies": False}).json()

        response = client.post("/components/import", json={"document": document, "id_prefix": "copy-"})

        assert response.status_code == 201
        links = db.get_links("copy-support-team", 1, tenant_id=None)
        assert [link["child_component_id"] for link in links] == ["faq-agent"]

    def test_it_evicts_the_hydration_cache_for_every_touched_id(self, client, seeded, db, registry):
        """An import that publishes moves `current_version`.

        A cached `version=None` row would keep serving the old config until the TTL, so every id
        the import touched is evicted -- not only the ones that gained a version.
        """
        from egpt.registry.hydrate import fetch_config_row

        cache = registry.component_config_cache
        key = cache.make_key(db, "copy-faq-agent")

        document = client.get("/components/support-team/export").json()
        client.post("/components/import", json={"document": document, "id_prefix": "copy-"})
        # Warm the cache on the imported component, then re-import to bump it.
        fetch_config_row(db, "copy-faq-agent", "agent", registry=registry)
        assert cache.get(key) is not None

        changed = dict(document)
        changed["components"] = [dict(entry) for entry in document["components"]]
        changed["components"][0]["config"] = {**changed["components"][0]["config"], "markdown": True}
        client.post(
            "/components/import",
            json={"document": changed, "id_prefix": "copy-", "on_conflict": "new_version"},
        )

        assert cache.get(key) is None

    def test_the_imported_definition_hydrates(self, client, seeded, db, registry):
        from egpt.team import get_team_by_id

        document = client.get("/components/support-team/export").json()
        client.post("/components/import", json={"document": document, "id_prefix": "copy-"})

        team = get_team_by_id(db, "copy-support-team", registry=registry)

        assert team is not None
        assert [member.id for member in team.members] == ["copy-faq-agent"]
