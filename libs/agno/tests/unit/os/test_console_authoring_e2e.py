"""End-to-end: an agent authored and published over the console's API, then run (EGPT-2.9).

This is the acceptance criteria of EGPT-2.9 as a test. Everything an admin does goes over HTTP
with a real service-account token, exactly as the console does it -- there is no direct db call
on the authoring side, because a console that cannot do it through the API has not delivered the
no-deploy authoring path.

The runtime half is deliberately *not* HTTP: `Agent.load` is what a worker process actually does
to hydrate a published definition, and asserting on the loaded agent is what proves the rollback
reached a runtime rather than merely moving a pointer in a row.

The model is registered into the discovery table so hydration resolves offline. That table is
process-global, so the fixture snapshots and restores it -- leaking a fake provider would poison
every later test in the session.
"""

import time
from typing import Any, AsyncIterator, Iterator

import pytest
from fastapi.testclient import TestClient

from egpt.agent.agent import Agent
from egpt.db.schemas.service_accounts import ServiceAccount
from egpt.integrations.db.sqlite import SqliteDb
from egpt.models.base import Model
from egpt.models.message import MessageMetrics
from egpt.models.response import ModelResponse
from egpt.os.app import EgptOS
from egpt.os.config import AuthorizationConfig
from egpt.os.service_accounts import generate_token
from egpt.registry import Registry, discovery
from egpt.registry.hydrate import NoPublishedVersionError

ADMIN_SCOPES = ["components:read", "components:write", "components:delete"]
TENANT = "acme"
OTHER_TENANT = "globex"

# The provider the authored configs name. Registered below, so no network and no API key.
MOCK_PROVIDER = "e2e-mock-provider"

V1_ANSWER = "answer from version one"
V2_ANSWER = "answer from version two"


class MockModel(Model):
    """Offline model whose reply identifies which config version hydrated it."""

    def __init__(self, id: str = "canned", name: str = "canned", provider: str = MOCK_PROVIDER, **kwargs: Any):
        super().__init__(id=id, name=name, provider=provider)
        # The id is what the config picks, so the reply can prove which version was loaded.
        answer = V2_ANSWER if id.endswith("v2") else V1_ANSWER
        self._response = ModelResponse(content=answer, role="assistant", response_usage=MessageMetrics())

    def get_instructions_for_model(self, *args: Any, **kwargs: Any) -> None:
        return None

    def get_system_message_for_model(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def aget_instructions_for_model(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def aget_system_message_for_model(self, *args: Any, **kwargs: Any) -> None:
        return None

    def parse_args(self, *args: Any, **kwargs: Any) -> dict:
        return {}

    def invoke(self, *args: Any, **kwargs: Any) -> ModelResponse:
        return self._response

    async def ainvoke(self, *args: Any, **kwargs: Any) -> ModelResponse:
        return self._response

    def invoke_stream(self, *args: Any, **kwargs: Any) -> Iterator[ModelResponse]:
        yield self._response

    async def ainvoke_stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[ModelResponse]:
        yield self._response
        return

    def _parse_provider_response(self, response: Any, **kwargs: Any) -> ModelResponse:
        return self._response

    def _parse_provider_response_delta(self, response: Any) -> ModelResponse:
        return self._response


def config_for(model_id: str, instructions: str) -> dict:
    return {
        "name": "Support",
        "model": {"provider": MOCK_PROVIDER, "id": model_id},
        "instructions": instructions,
    }


V1_CONFIG = config_for("canned-v1", "Answer as version one.")
V2_CONFIG = config_for("canned-v2", "Answer as version two.")


@pytest.fixture(autouse=True)
def mock_provider():
    """Register the offline provider, then put the global discovery table back exactly as found."""
    saved_table = {kind: dict(table) for kind, table in discovery._TABLE.items()}
    saved_ready = set(discovery._READY)
    discovery.register("model", MOCK_PROVIDER, obj=MockModel, override=True)
    try:
        yield
    finally:
        for kind, table in saved_table.items():
            discovery._TABLE[kind] = table
            discovery._VERSION[kind] += 1
        discovery._READY.clear()
        discovery._READY.update(saved_ready)


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    """A tenanted EgptOS with the console on, plus admin tokens for two tenants.

    The security key is what actually installs the auth layer: without it `authorization` stays
    falsy, `app.state.tenant_id_claim` is never stamped, and the whole deployment runs open --
    which would quietly turn every scope and tenancy assertion below into a no-op.

    `hydrate_published_only` is the production posture, and the one that makes "publish" a gate
    rather than a label: by default `get_config` falls back to the latest draft when a component
    has no current version (see the docstring on the sqlite backend), so a runtime would happily
    pick up work in progress.
    """
    monkeypatch.setenv("EGPT_OS_SECURITY_KEY", "test-security-key-for-console-e2e")
    db = SqliteDb(db_file=str(tmp_path / "console-e2e.db"), id="os-db")
    registry = Registry(hydrate_published_only=True)
    app = EgptOS(
        db=db,
        id="console-e2e",
        registry=registry,
        enable_console=True,
        authorization_config=AuthorizationConfig(tenant_id_claim="tid"),
    ).get_app()

    def mint(tenant: str) -> str:
        token, token_hash, prefix = generate_token()
        db.create_service_account(
            ServiceAccount(
                id=f"sa-{tenant}",
                name=f"admin-{tenant}",
                token_hash=token_hash,
                token_prefix=prefix,
                scopes=list(ADMIN_SCOPES),
                created_at=int(time.time()),
                tenant_id=tenant,
            ).to_dict()
        )
        return token

    return {
        "db": db,
        "registry": registry,
        "app": app,
        "admin": {"Authorization": f"Bearer {mint(TENANT)}"},
        "outsider": {"Authorization": f"Bearer {mint(OTHER_TENANT)}"},
    }


@pytest.fixture
def client(deployment):
    with TestClient(deployment["app"], raise_server_exceptions=False) as test_client:
        yield test_client


def author_and_publish(client, headers, config=V1_CONFIG) -> str:
    """The happy path as one call, for the tests that start from a published component."""
    created = client.post(
        "/components",
        json={"name": "Support", "component_type": "agent", "config": config, "stage": "draft"},
        headers=headers,
    )
    assert created.status_code == 201
    component_id = created.json()["component_id"]

    published = client.patch(f"/components/{component_id}/configs/1", json={"stage": "published"}, headers=headers)
    assert published.status_code == 200
    return component_id


# =============================================================================
# The acceptance path
# =============================================================================


class TestAuthorValidatePublishHydrateRun:
    def test_the_console_page_is_served(self, client):
        assert client.get("/console").status_code == 200

    def test_an_invalid_config_is_refused_and_nothing_is_created(self, client, deployment):
        """ "Errors inline, no publish" as a state assertion: a rejected dry run must leave the
        tenant with exactly as many components as it started with."""
        headers = deployment["admin"]

        response = client.post(
            "/components/validate",
            json={"component_type": "agent", "config": {"instructions": "no model named"}},
            headers=headers,
        )

        assert response.status_code == 422
        body = response.json()
        assert body["error_code"] == "CONFIG_VALIDATION_ERROR"
        assert [(e["path"], e["code"]) for e in body["errors"]] == [("config.model", "missing_required_field")]

        assert client.get("/components", headers=headers).json()["data"] == []

    def test_a_valid_config_validates(self, client, deployment):
        response = client.post(
            "/components/validate",
            json={"component_type": "agent", "config": V1_CONFIG},
            headers=deployment["admin"],
        )

        assert response.status_code == 200
        assert response.json()["valid"] is True

    def test_a_draft_is_not_current_until_it_is_published(self, client, deployment):
        headers = deployment["admin"]

        created = client.post(
            "/components",
            json={"name": "Support", "component_type": "agent", "config": V1_CONFIG, "stage": "draft"},
            headers=headers,
        )
        component_id = created.json()["component_id"]

        configs = client.get(f"/components/{component_id}/configs", headers=headers).json()
        assert [c["stage"] for c in configs] == ["draft"]
        assert client.get(f"/components/{component_id}", headers=headers).json().get("current_version") is None

    def test_publishing_makes_the_version_current(self, client, deployment):
        headers = deployment["admin"]
        component_id = author_and_publish(client, headers)

        component = client.get(f"/components/{component_id}", headers=headers).json()

        assert component["current_version"] == 1
        configs = client.get(f"/components/{component_id}/configs", headers=headers).json()
        assert configs[0]["stage"] == "published"

    def test_a_runtime_hydrates_and_runs_the_published_agent(self, client, deployment):
        """The acceptance criterion. Nothing about this agent was written in Python."""
        component_id = author_and_publish(client, deployment["admin"])

        agent = Agent.load(component_id, db=deployment["db"], registry=deployment["registry"], tenant_id=TENANT)

        assert agent is not None
        assert agent.name == "Support"
        assert agent.instructions == "Answer as version one."
        assert agent.run("hello").content == V1_ANSWER

    def test_an_unpublished_component_is_not_hydrated(self, client, deployment):
        """Publishing is the gate -- for a registry configured the way production is.

        Worth being precise about, because the default is the other way round: with plain
        `Registry()`, `get_config` falls back to the latest draft for a component that has no
        current version, and a runtime would run work in progress. `hydrate_published_only=True`
        is what makes the draft unreachable, and the deployment fixture sets it.
        """
        headers = deployment["admin"]
        client.post(
            "/components",
            json={"name": "Support", "component_type": "agent", "config": V1_CONFIG, "stage": "draft"},
            headers=headers,
        )

        with pytest.raises(NoPublishedVersionError):
            Agent.load("support", db=deployment["db"], registry=deployment["registry"], tenant_id=TENANT)


# =============================================================================
# Rollback
# =============================================================================


class TestRollback:
    @pytest.fixture
    def published_twice(self, client, deployment):
        headers = deployment["admin"]
        component_id = author_and_publish(client, headers)

        created = client.post(
            f"/components/{component_id}/configs",
            json={"config": V2_CONFIG, "stage": "draft"},
            headers=headers,
        )
        assert created.status_code == 201
        version = created.json()["version"]
        assert (
            client.patch(
                f"/components/{component_id}/configs/{version}", json={"stage": "published"}, headers=headers
            ).status_code
            == 200
        )
        return component_id, version

    def test_publishing_v2_moves_current(self, client, deployment, published_twice):
        component_id, version = published_twice

        component = client.get(f"/components/{component_id}", headers=deployment["admin"]).json()

        assert component["current_version"] == version == 2

    def test_set_current_rolls_the_pointer_back(self, client, deployment, published_twice):
        component_id, _ = published_twice
        headers = deployment["admin"]

        response = client.post(f"/components/{component_id}/configs/1/set-current", headers=headers)

        assert response.status_code == 200
        assert client.get(f"/components/{component_id}", headers=headers).json()["current_version"] == 1

    def test_a_runtime_gets_v1_after_the_rollback(self, client, deployment, published_twice):
        """The assertion that catches a missed hydration-cache eviction: the pointer moving is
        not the point, what a worker actually loads afterwards is."""
        component_id, _ = published_twice
        db, registry = deployment["db"], deployment["registry"]

        before = Agent.load(component_id, db=db, registry=registry, tenant_id=TENANT)
        assert before.run("hello").content == V2_ANSWER

        client.post(f"/components/{component_id}/configs/1/set-current", headers=deployment["admin"])

        after = Agent.load(component_id, db=db, registry=registry, tenant_id=TENANT)
        assert after.instructions == "Answer as version one."
        assert after.run("hello").content == V1_ANSWER


# =============================================================================
# The lifecycle rules the console's disabled buttons encode
# =============================================================================


class TestLifecycleRulesAreEnforcedByTheServer:
    """The console greys these actions out with an explanation. The server is still the authority,
    and each of these is the rule behind one disabled button."""

    @pytest.fixture
    def published(self, client, deployment):
        return author_and_publish(client, deployment["admin"])

    def test_a_published_config_cannot_be_edited(self, client, deployment, published):
        response = client.patch(
            f"/components/{published}/configs/1",
            json={"config": V2_CONFIG},
            headers=deployment["admin"],
        )

        assert response.status_code == 400
        assert "published" in response.json()["detail"].lower()

    def test_the_current_version_cannot_be_deleted(self, client, deployment, published):
        response = client.delete(f"/components/{published}/configs/1", headers=deployment["admin"])

        assert response.status_code == 400

    def test_a_draft_cannot_be_made_current(self, client, deployment, published):
        headers = deployment["admin"]
        client.post(f"/components/{published}/configs", json={"config": V2_CONFIG, "stage": "draft"}, headers=headers)

        response = client.post(f"/components/{published}/configs/2/set-current", headers=headers)

        assert response.status_code == 400

    def test_cloning_a_published_config_to_a_draft_is_how_you_edit_it(self, client, deployment, published):
        """The console's "Clone to draft" button: the escape hatch from the immutability rule."""
        headers = deployment["admin"]
        original = client.get(f"/components/{published}/configs/1", headers=headers).json()

        clone = client.post(
            f"/components/{published}/configs",
            json={"config": original["config"], "stage": "draft", "notes": "Cloned from v1"},
            headers=headers,
        )

        assert clone.status_code == 201
        assert clone.json()["stage"] == "draft"
        assert clone.json()["version"] == 2

        edited = client.patch(
            f"/components/{published}/configs/2",
            json={"config": V2_CONFIG},
            headers=headers,
        )
        assert edited.status_code == 200


# =============================================================================
# Tenancy
# =============================================================================


class TestTheConsoleOnlySeesItsOwnTenant:
    @pytest.fixture
    def published(self, client, deployment):
        return author_and_publish(client, deployment["admin"])

    def test_another_tenants_component_is_not_listed(self, client, deployment, published):
        listing = client.get("/components", headers=deployment["outsider"]).json()

        assert listing["data"] == []

    def test_another_tenants_component_is_a_404(self, client, deployment, published):
        assert client.get(f"/components/{published}", headers=deployment["outsider"]).status_code == 404

    def test_another_tenant_cannot_publish_or_roll_it_back(self, client, deployment, published):
        outsider = deployment["outsider"]

        assert (
            client.patch(
                f"/components/{published}/configs/1", json={"stage": "published"}, headers=outsider
            ).status_code
            == 404
        )
        assert client.post(f"/components/{published}/configs/1/set-current", headers=outsider).status_code == 404

    def test_another_tenant_cannot_delete_it(self, client, deployment, published):
        assert client.delete(f"/components/{published}", headers=deployment["outsider"]).status_code == 404
        assert client.get(f"/components/{published}", headers=deployment["admin"]).status_code == 200

    def test_a_runtime_in_another_tenant_does_not_hydrate_it(self, client, deployment, published):
        """None, not an error: outside its tenant the component reads as absent, exactly as the
        HTTP 404 above -- the row is filtered out before anything can fail on it."""
        loaded = Agent.load(published, db=deployment["db"], registry=deployment["registry"], tenant_id=OTHER_TENANT)

        assert loaded is None

    def test_an_unauthenticated_call_is_refused(self, client, published):
        """The page is open; the API behind it is not."""
        assert client.get("/components").status_code == 401
