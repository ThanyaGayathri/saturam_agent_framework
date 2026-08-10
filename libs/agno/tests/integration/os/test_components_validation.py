"""End-to-end config validation through the real app and a real SQLite db (EGPT-2.2).

The unit tests mock the db and mount the router on a bare FastAPI. What only this level can
prove is the whole loop: a definition posted over HTTP is validated, persisted as JSONB, read
back, and hydrated into an equivalent agent -- and that a rejection keeps its 422 shape through
the full middleware stack rather than being flattened by an exception handler along the way.
"""

import pytest
from fastapi.testclient import TestClient

from egpt.agent.agent import Agent
from egpt.integrations.models.openai import OpenAIResponses
from egpt.os import EgptOS
from egpt.registry.schema import SCHEMA_VERSION, SCHEMA_VERSION_KEY

AGENT_CONFIG = {
    "model": {"id": "gpt-5.5", "provider": "OpenAI", "name": "OpenAIResponses"},
    "name": "Support Agent",
    "description": "Answers billing questions.",
    "instructions": ["Be terse.", "Never guess an amount."],
    "markdown": True,
    "num_history_runs": 5,
}


@pytest.fixture
def client(shared_db):
    """An OS with a real SQLite db and no code-defined components."""
    return TestClient(EgptOS(db=shared_db).get_app())


def _create(client, component_id, config, component_type="agent"):
    return client.post(
        "/components",
        json={
            "name": component_id,
            "component_id": component_id,
            "component_type": component_type,
            "config": config,
        },
    )


class TestValidDefinitionRoundTrips:
    def test_write_read_from_dict_reconstructs_the_agent(self, client):
        assert _create(client, "support-agent", dict(AGENT_CONFIG)).status_code == 201

        stored = client.get("/components/support-agent/configs/current").json()["config"]
        rebuilt = Agent.from_dict(stored)

        assert stored == {**AGENT_CONFIG, SCHEMA_VERSION_KEY: SCHEMA_VERSION}
        assert rebuilt.name == "Support Agent"
        assert rebuilt.instructions == ["Be terse.", "Never guess an amount."]
        assert rebuilt.num_history_runs == 5
        assert isinstance(rebuilt.model, OpenAIResponses)
        assert rebuilt.model.id == "gpt-5.5"

    def test_a_config_written_by_to_dict_is_accepted_verbatim(self, client):
        """Whatever `Agent.to_dict` emits must be a config the API will take."""
        config = Agent(model=OpenAIResponses(id="gpt-5.5"), name="Serialized", markdown=True).to_dict()

        assert _create(client, "serialized-agent", config).status_code == 201
        assert client.get("/components/serialized-agent/configs/current").json()["config"] == config

    def test_a_new_config_version_is_validated_too(self, client):
        _create(client, "versioned-agent", dict(AGENT_CONFIG))

        response = client.post(
            "/components/versioned-agent/configs",
            json={"config": {**AGENT_CONFIG, "instructions": "Be even terser."}},
        )

        assert response.status_code == 201
        assert response.json()["version"] == 2


class TestInvalidDefinitionsAreRefused:
    def test_a_config_without_a_model_is_rejected_and_not_stored(self, client):
        response = _create(client, "no-model", {"instructions": "hi"})

        assert response.status_code == 422
        body = response.json()
        assert body["error_code"] == "CONFIG_VALIDATION_ERROR"
        assert body["errors"][0]["path"] == "config.model"
        assert client.get("/components/no-model").status_code == 404

    def test_a_typo_is_caught_before_it_reaches_a_run(self, client):
        response = _create(client, "typo-agent", {**AGENT_CONFIG, "instruction": "singular"})

        assert response.status_code == 422
        assert response.json()["errors"][0]["code"] == "unknown_field"

    def test_a_config_from_a_newer_framework_is_refused(self, client):
        config = {**AGENT_CONFIG, SCHEMA_VERSION_KEY: SCHEMA_VERSION + 1}

        response = _create(client, "future-agent", config)

        assert response.status_code == 422
        assert response.json()["errors"][0]["code"] == "unsupported_schema_version"

    def test_a_config_naming_an_unknown_db_is_refused(self, client):
        response = _create(client, "ghost-db-agent", {**AGENT_CONFIG, "db": {"id": "ghost"}})

        assert response.status_code == 422
        assert response.json()["errors"][0]["path"] == "config.db.id"

    def test_the_os_db_can_still_be_referenced_by_id(self, client, shared_db):
        response = _create(client, "os-db-agent", {**AGENT_CONFIG, "db": {"id": shared_db.id}})

        assert response.status_code == 201
        stored = client.get("/components/os-db-agent/configs/current").json()["config"]
        assert stored["db"]["type"] == "sqlite"
