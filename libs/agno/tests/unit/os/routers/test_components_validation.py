"""Tests for the api-layer half of config validation (EGPT-2.2).

`egpt.registry.schema` is covered on its own in tests/unit/registry/test_config_schema.py.
What is only testable here is the part that needs orchestration: the `ComponentSpec` factories,
the dry-run hydration they close, and the promise that a definition written through the API
comes back out as the same agent.
"""

from unittest.mock import MagicMock, patch

import pytest

from egpt.agent import Agent
from egpt.integrations.models.openai import OpenAIResponses
from egpt.os.routers.components.validation import (
    component_spec_for,
    config_validation_response,
    validate_component_config,
)
from egpt.registry import Registry
from egpt.registry.schema import SCHEMA_VERSION, SCHEMA_VERSION_KEY, ConfigValidationError
from egpt.tools.function import Function


def weather(city: str) -> str:
    """Look up the weather."""
    return f"sunny in {city}"


@pytest.fixture
def registry():
    return Registry(tools=[Function.from_callable(weather)])


# =============================================================================
# Component specs
# =============================================================================


class TestComponentSpecs:
    @pytest.mark.parametrize("component_type", ["agent", "team", "workflow"])
    def test_every_component_type_supplies_a_hydrator(self, component_type):
        """A spec whose `hydrate` is None would make the dry run silently do nothing."""
        spec = component_spec_for(component_type)

        assert spec.component_type == component_type
        assert spec.hydrate is not None

    def test_known_keys_track_the_constructor(self):
        """Derived, not hand-listed -- a new Agent setting must not become an unknown field."""
        spec = component_spec_for("agent")

        assert {"instructions", "tools", "markdown", "output_schema"} <= spec.known_keys
        assert SCHEMA_VERSION_KEY in spec.known_keys

    def test_an_unknown_component_type_raises(self):
        with pytest.raises(ValueError, match="Unknown component type"):
            component_spec_for("sandwich")


# =============================================================================
# Round trip
# =============================================================================


class TestRoundTrip:
    def test_a_serialized_agent_survives_write_read_from_dict(self):
        """write -> read -> from_dict, byte-for-byte, for a definition with no tools."""
        agent = Agent(
            model=OpenAIResponses(id="gpt-5.5"),
            name="Terse Agent",
            instructions=["Be terse.", "Cite the city."],
            markdown=True,
        )
        config = agent.to_dict()

        stored = validate_component_config(config, "agent")
        rebuilt = Agent.from_dict(stored)

        assert stored == config
        assert rebuilt.to_dict() == config

    def test_a_definition_with_tools_rebuilds_an_equivalent_agent(self, registry):
        """Tools do not compare byte-for-byte: rehydration reattaches the live entrypoint and
        re-derives its JSON Schema, so the second `to_dict` is a fuller version of the first.
        What must hold is that the stored config is untouched and the agent comes back whole.
        """
        agent = Agent(
            model=OpenAIResponses(id="gpt-5.5"),
            name="Weather Agent",
            tools=[weather],
        )
        config = agent.to_dict()

        stored = validate_component_config(config, "agent", registry=registry)
        rebuilt = Agent.from_dict(stored, registry=registry)

        assert stored == config
        assert rebuilt.name == "Weather Agent"
        assert rebuilt.model.id == "gpt-5.5"
        assert [tool.name for tool in rebuilt.tools] == ["weather"]
        assert rebuilt.tools[0].entrypoint is not None

    def test_a_tool_reference_needs_only_a_name(self, registry):
        """The registered entrypoint supplies the signature, so a bare name must hydrate."""
        config = {"model": "openai:gpt-5.5", "tools": [{"name": "weather"}]}

        rebuilt = Agent.from_dict(validate_component_config(config, "agent", registry=registry), registry=registry)

        assert rebuilt.tools[0].parameters == {"type": "object", "properties": {}, "required": []}

    def test_to_dict_stamps_the_schema_version(self):
        agent = Agent(model=OpenAIResponses(id="gpt-5.5"))

        assert agent.to_dict()[SCHEMA_VERSION_KEY] == SCHEMA_VERSION

    def test_from_dict_accepts_a_config_written_before_versioning(self):
        config = {"model": {"id": "gpt-5.5", "provider": "OpenAI", "name": "OpenAIResponses"}, "name": "Legacy"}

        rebuilt = Agent.from_dict(validate_component_config(dict(config), "agent"))

        assert rebuilt.name == "Legacy"
        assert rebuilt.model.id == "gpt-5.5"


# =============================================================================
# Dry run
# =============================================================================


class TestDryRunIsSideEffectFree:
    def test_no_database_backend_is_constructed(self):
        """The db block is fully checked without one, so hydration must not build an engine."""
        config = {
            "model": "openai:gpt-5.5",
            "db": {"type": "sqlite", "db_file": "/tmp/should-never-be-opened.db"},
        }

        with patch("egpt.db.utils.db_from_dict") as db_from_dict:
            validate_component_config(config, "agent")

        db_from_dict.assert_not_called()

    def test_no_tool_is_executed(self):
        exploding = MagicMock(side_effect=AssertionError("a dry run must not call a tool"))
        exploding.__name__ = "weather"
        registry = Registry(tools=[exploding])

        validate_component_config(
            {"model": "openai:gpt-5.5", "tools": [{"name": "weather"}]}, "agent", registry=registry
        )

        exploding.assert_not_called()

    def test_the_config_that_gets_persisted_is_the_one_that_was_sent(self):
        config = {"model": "openai:gpt-5.5", "instructions": "original", "metadata": {"team": "core"}}

        stored = validate_component_config(dict(config), "agent")

        assert stored == {**config, SCHEMA_VERSION_KEY: SCHEMA_VERSION}


class TestDryRunFailures:
    def test_an_unhydratable_config_is_rejected(self):
        """`from_dict` degrades quietly on bad refs, so hydration has to be able to raise."""
        with patch("egpt.agent._storage.from_dict", side_effect=ValueError("Invalid model string format: 'x'")):
            with pytest.raises(ConfigValidationError) as exc_info:
                validate_component_config({"model": "openai:gpt-5.5"}, "agent")

        assert [(e.path, e.code) for e in exc_info.value.errors] == [("model", "hydration_failed")]

    def test_a_registered_provider_with_no_sdk_installed_is_rejected(self):
        message = "`anthropic` not installed. Please install using `pip install anthropic`"
        with patch("egpt.agent._storage.from_dict", side_effect=ImportError(message)):
            with pytest.raises(ConfigValidationError) as exc_info:
                validate_component_config({"model": "anthropic:claude-sonnet-4-5"}, "agent")

        error = exc_info.value.errors[0]
        assert error.code == "provider_not_installed"
        assert "pip install anthropic" in error.message


# =============================================================================
# Error rendering
# =============================================================================


class TestErrorResponse:
    def test_paths_are_reported_relative_to_the_request_body(self):
        with pytest.raises(ConfigValidationError) as exc_info:
            validate_component_config({"instructions": "hi"}, "agent")

        response = config_validation_response(exc_info.value)
        body = response.body.decode()

        assert response.status_code == 422
        assert '"path":"config.model"' in body
        assert '"error_code":"CONFIG_VALIDATION_ERROR"' in body

    def test_a_config_level_error_reports_the_config_itself(self):
        with pytest.raises(ConfigValidationError) as exc_info:
            validate_component_config(["not", "an", "object"], "agent")

        assert '"path":"config"' in config_validation_response(exc_info.value).body.decode()
