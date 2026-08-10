"""Unit tests for the component-config schema and its semantic validator (EGPT-2.2).

These exercise `egpt.registry.schema` on its own -- no FastAPI, and no orchestration except
where a test deliberately supplies a hydrator. That separation is the point of the module:
core validates what it can see, and takes the component contract as an argument.
"""

import re
from pathlib import Path

import pytest

from egpt.registry import discovery, schema
from egpt.registry.schema import (
    SCHEMA_VERSION,
    SCHEMA_VERSION_KEY,
    ComponentSpec,
    ConfigValidationError,
    config_schema_version,
    json_schema,
    validate_config,
)

VALID_MODEL = {"id": "gpt-5.5", "provider": "OpenAI", "name": "OpenAIResponses"}


def _errors(config, component_type="agent", **kwargs):
    """Validate and return {path: code} for whatever failed."""
    with pytest.raises(ConfigValidationError) as exc_info:
        validate_config(config, component_type, **kwargs)
    return {error.path: error.code for error in exc_info.value.errors}


def _messages(config, component_type="agent", **kwargs):
    with pytest.raises(ConfigValidationError) as exc_info:
        validate_config(config, component_type, **kwargs)
    return {error.path: error.message for error in exc_info.value.errors}


class FakeRegistry:
    """The slice of `Registry` the validator consults, with nothing else attached."""

    def __init__(self, tools=(), knowledge=(), schemas=(), models=()):
        self._tools = set(tools)
        self._knowledge = set(knowledge)
        self._schemas = set(schemas)
        self._models = set(models)

    def has_entrypoint(self, name):
        return name in self._tools

    def get_knowledge_names(self):
        return self._knowledge

    def get_schema(self, name):
        return object if name in self._schemas else None

    def get_model(self, model_id, provider=None, name=None):
        return object() if model_id in self._models else None


# =============================================================================
# Happy path
# =============================================================================


class TestValidConfigs:
    def test_minimal_agent_config_is_accepted_and_stamped(self):
        result = validate_config({"model": VALID_MODEL}, "agent")

        assert result == {"model": VALID_MODEL, SCHEMA_VERSION_KEY: SCHEMA_VERSION}

    def test_model_may_be_a_provider_id_string(self):
        assert validate_config({"model": "openai:gpt-5.5"}, "agent")[SCHEMA_VERSION_KEY] == SCHEMA_VERSION

    def test_the_caller_config_is_not_mutated(self):
        config = {"model": VALID_MODEL}

        validate_config(config, "agent")

        assert SCHEMA_VERSION_KEY not in config

    def test_an_already_stamped_config_round_trips_unchanged(self):
        config = {"model": VALID_MODEL, SCHEMA_VERSION_KEY: SCHEMA_VERSION}

        assert validate_config(config, "agent") == config

    def test_unknown_component_type_is_a_programming_error_not_a_config_error(self):
        with pytest.raises(ValueError, match="Unknown component type"):
            validate_config({}, "sandwich")


# =============================================================================
# Structure
# =============================================================================


class TestStructure:
    def test_missing_model_is_reported_against_the_field(self):
        assert _errors({"instructions": "hi"}) == {"model": "missing_required_field"}

    def test_a_non_object_config_is_rejected(self):
        assert _errors(["not", "a", "config"]) == {"": "invalid_type"}

    def test_negative_history_counts_are_rejected(self):
        assert _errors({"model": VALID_MODEL, "num_history_runs": -1}) == {"num_history_runs": "invalid_value"}

    def test_instructions_must_be_text_or_a_list(self):
        assert _errors({"model": VALID_MODEL, "instructions": 42}) == {"instructions": "invalid_type"}

    def test_every_problem_is_reported_in_one_pass(self):
        """A caller fixing a config should not have to peel errors off one request at a time."""
        errors = _errors(
            {"model": {"provider": "nope"}, "tools": [{}], "knowledge": {}, "learning": "yes"},
        )

        assert errors == {
            "model.id": "missing_required_field",
            "tools[0].name": "missing_required_field",
            "knowledge.name": "missing_required_field",
            "learning": "invalid_type",
        }


class TestSchemaVersion:
    def test_an_unversioned_config_validates_as_v1(self):
        assert config_schema_version({"model": VALID_MODEL}) == 0
        assert validate_config({"model": VALID_MODEL}, "agent")[SCHEMA_VERSION_KEY] == SCHEMA_VERSION

    def test_a_future_version_is_refused_rather_than_silently_downgraded(self):
        errors = _errors({"model": VALID_MODEL, SCHEMA_VERSION_KEY: SCHEMA_VERSION + 1})

        assert errors == {SCHEMA_VERSION_KEY: "unsupported_schema_version"}

    def test_a_non_integer_version_is_rejected(self):
        assert _errors({"model": VALID_MODEL, SCHEMA_VERSION_KEY: "1"}) == {SCHEMA_VERSION_KEY: "invalid_type"}

    def test_a_boolean_is_not_version_one(self):
        assert config_schema_version({SCHEMA_VERSION_KEY: True}) == 0


class TestUnknownKeys:
    spec = ComponentSpec("agent", frozenset({"model", "instructions", "temperature"}))

    def test_an_unknown_key_is_rejected_with_a_suggestion(self):
        messages = _messages({"model": VALID_MODEL, "instruction": "hi"}, spec=self.spec)

        assert "Did you mean 'instructions'?" in messages["instruction"]

    def test_a_key_with_no_near_match_is_still_rejected(self):
        messages = _messages({"model": VALID_MODEL, "zzzzzz": 1}, spec=self.spec)

        assert messages["zzzzzz"] == "Unknown field 'zzzzzz'."

    def test_declared_schema_fields_are_always_allowed(self):
        """`spec.known_keys` comes from the constructor; the schema's own fields extend it."""
        validate_config({"model": VALID_MODEL, SCHEMA_VERSION_KEY: 1, "name": "A"}, "agent", spec=self.spec)

    def test_without_a_spec_the_check_is_skipped(self):
        validate_config({"model": VALID_MODEL, "whatever": 1}, "agent")


# =============================================================================
# Reference resolution
# =============================================================================


class TestModelRefs:
    def test_an_unregistered_provider_is_rejected(self):
        errors = _errors({"model": {"id": "x", "provider": "acme"}})

        assert errors == {"model.provider": "unresolvable_ref"}

    def test_the_message_lists_registered_providers(self):
        messages = _messages({"model": {"id": "x", "provider": "acme"}})

        assert "Registered providers: " in messages["model.provider"]

    def test_a_malformed_model_string_is_rejected(self):
        assert _errors({"model": "gpt-5.5"}) == {"model": "invalid_format"}

    def test_a_model_that_is_neither_object_nor_string_is_rejected(self):
        assert _errors({"model": 12}) == {"model": "invalid_type"}

    def test_a_registered_live_instance_settles_it(self):
        """A registry instance is authoritative even for a provider discovery knows nothing about."""
        registry = FakeRegistry(models={"acme-1"})

        validate_config({"model": {"id": "acme-1", "provider": "acme"}}, "agent", registry=registry)

    def test_provider_resolution_imports_no_integration(self, monkeypatch):
        """Ref-checking must not import the provider: `discovery.get` answers from the table."""
        discovery.register("model", "explodes", module="egpt.tests.does_not_exist", attr="Nope", override=True)

        validate_config({"model": {"id": "x", "provider": "explodes"}}, "agent")


class TestToolRefs:
    def test_an_unregistered_tool_is_rejected(self):
        config = {"model": VALID_MODEL, "tools": [{"name": "search_web"}, {"name": "serch_web"}]}

        errors = _errors(config, registry=FakeRegistry(tools={"search_web"}))

        assert errors == {"tools[1].name": "unresolvable_ref"}

    def test_tool_refs_are_skipped_without_a_registry(self):
        """With nothing registered, "unknown tool" and "unconfigured process" are the same shape."""
        validate_config({"model": VALID_MODEL, "tools": [{"name": "anything"}]}, "agent")

    def test_a_tool_must_be_an_object_with_a_name(self):
        errors = _errors({"model": VALID_MODEL, "tools": ["search_web"]})

        assert errors["tools[0]"] == "invalid_type"

    def test_tool_parameters_must_be_a_schema_object(self):
        errors = _errors({"model": VALID_MODEL, "tools": [{"name": "t", "parameters": []}]})

        assert errors == {"tools[0].parameters": "invalid_type"}


class TestDbRefs:
    def test_a_db_reference_needs_an_id_or_a_type(self):
        assert _errors({"model": VALID_MODEL, "db": {}}) == {"db": "missing_required_field"}

    def test_an_unregistered_db_type_is_rejected(self):
        assert _errors({"model": VALID_MODEL, "db": {"type": "acmedb"}}) == {"db.type": "unresolvable_ref"}

    def test_an_id_the_caller_could_not_resolve_is_rejected(self):
        errors = _errors({"model": VALID_MODEL, "db": {"id": "ghost"}}, unresolved_db_id="ghost")

        assert errors == {"db.id": "unresolvable_ref"}

    def test_a_resolved_id_passes(self):
        validate_config({"model": VALID_MODEL, "db": {"id": "os-db", "type": "sqlite"}}, "agent")


class TestKnowledgeAndSchemaRefs:
    def test_unregistered_knowledge_is_rejected(self):
        config = {"model": VALID_MODEL, "knowledge": {"name": "Docs"}}

        errors = _errors(config, registry=FakeRegistry(knowledge={"Other"}))

        assert errors == {"knowledge.name": "unresolvable_ref"}

    def test_unregistered_output_schema_is_rejected(self):
        config = {"model": VALID_MODEL, "output_schema": "OrderOutput"}

        errors = _errors(config, registry=FakeRegistry(schemas={"OrderInput"}))

        assert errors == {"output_schema": "unresolvable_ref"}

    def test_an_inline_schema_object_is_accepted(self):
        validate_config({"model": VALID_MODEL, "output_schema": {"type": "object"}}, "agent")

    def test_an_empty_inline_schema_is_rejected(self):
        assert _errors({"model": VALID_MODEL, "input_schema": {}}) == {"input_schema": "invalid_type"}

    def test_registry_backed_refs_are_skipped_without_a_registry(self):
        validate_config({"model": VALID_MODEL, "knowledge": {"name": "Docs"}, "output_schema": "Nope"}, "agent")


# =============================================================================
# Team and workflow envelopes
# =============================================================================


class TestTeamEnvelope:
    def test_a_member_needs_a_known_type(self):
        errors = _errors({"members": [{"type": "squad", "agent_id": "a"}]}, "team")

        assert errors == {"members[0].type": "invalid_value"}

    def test_a_member_needs_the_id_matching_its_type(self):
        errors = _errors({"members": [{"type": "team"}]}, "team")

        assert errors == {"members[0].team_id": "missing_required_field"}

    def test_a_team_cannot_contain_itself(self):
        config = {"members": [{"type": "team", "team_id": "t-1"}]}

        errors = _errors(config, "team", self_component_id="t-1")

        assert errors == {"members[0].team_id": "cyclic_reference"}

    def test_a_team_without_members_is_allowed(self):
        """Creating a team and filling it in afterwards is a legitimate flow; it only warns."""
        validate_config({"members": []}, "team")
        validate_config({}, "team")

    def test_a_team_does_not_need_a_model(self):
        validate_config({"members": [{"type": "agent", "agent_id": "a"}]}, "team")


class TestWorkflowEnvelope:
    def test_an_unknown_step_type_is_rejected(self):
        errors = _errors({"steps": [{"type": "Step"}, {"type": "Frobnicate"}]}, "workflow")

        assert errors == {"steps[1].type": "invalid_value"}

    def test_a_step_without_an_explicit_type_defaults_to_step(self):
        validate_config({"steps": [{"name": "first"}]}, "workflow")


# =============================================================================
# Dry-run hydration
# =============================================================================


class TestDryRun:
    def test_a_hydration_value_error_lands_on_the_field_it_names(self):
        def hydrate(_config):
            raise ValueError("Model provider 'acme' is not supported.")

        errors = _errors({"model": VALID_MODEL}, spec=ComponentSpec("agent", frozenset(), hydrate))

        assert errors == {"model.provider": "hydration_failed"}

    def test_a_missing_provider_sdk_is_a_hard_failure_with_its_own_code(self):
        def hydrate(_config):
            raise ImportError("`anthropic` not installed. Please install using `pip install anthropic`")

        errors = _messages({"model": VALID_MODEL}, spec=ComponentSpec("agent", frozenset(), hydrate))

        assert "pip install anthropic" in errors["model.provider"]

    def test_an_unexpected_exception_becomes_an_error_not_a_crash(self):
        def hydrate(_config):
            raise RuntimeError("boom")

        errors = _errors({"model": VALID_MODEL}, spec=ComponentSpec("agent", frozenset(), hydrate))

        assert errors == {"": "hydration_failed"}

    def test_the_db_block_is_withheld_so_no_backend_is_constructed(self):
        seen = {}

        def hydrate(config):
            seen["config"] = config

        config = {"model": VALID_MODEL, "db": {"id": "os-db", "type": "sqlite"}}
        validate_config(config, "agent", spec=ComponentSpec("agent", frozenset(), hydrate))

        assert "db" not in seen["config"]

    def test_the_hydrator_cannot_mutate_what_gets_persisted(self):
        def hydrate(config):
            config["instructions"] = "clobbered"
            config["metadata"]["team"] = "clobbered"

        config = {"model": VALID_MODEL, "instructions": "original", "metadata": {}}
        result = validate_config(config, "agent", spec=ComponentSpec("agent", frozenset(), hydrate))

        assert result["instructions"] == "original"
        assert result["metadata"] == {}

    def test_hydration_is_skipped_once_something_else_has_failed(self):
        """Dry-running a config already known to be broken produces noise, not information."""
        calls = []

        config = {"model": {"id": "x", "provider": "acme"}}
        _errors(config, spec=ComponentSpec("agent", frozenset(), lambda cfg: calls.append(cfg)))

        assert calls == []

    def test_team_and_workflow_are_not_dry_run_in_this_release(self):
        calls = []
        spec = ComponentSpec("team", frozenset(), lambda cfg: calls.append(cfg))

        validate_config({"members": [{"type": "agent", "agent_id": "a"}]}, "team", spec=spec)

        assert calls == []


# =============================================================================
# JSON Schema export
# =============================================================================


class TestJsonSchema:
    def test_model_is_the_only_required_agent_field(self):
        assert json_schema("agent")["required"] == ["model"]

    @pytest.mark.parametrize("component_type", ["agent", "team", "workflow"])
    def test_every_component_type_exports_a_titled_schema(self, component_type):
        assert json_schema(component_type)["title"].endswith(f"config v{SCHEMA_VERSION}")

    def test_an_unknown_component_type_raises(self):
        with pytest.raises(ValueError, match="Unknown component type"):
            json_schema("sandwich")


# =============================================================================
# Documentation
# =============================================================================
#
# docs/component-config-schema.md is normative: clients are told to branch on the error codes
# rather than on message text, and the concrete lists it publishes are the kind of fact that
# goes stale silently. Every hand-written fact in it is pinned against the live value here, so
# extending the vocabulary or registering a backend fails the suite instead of the document
# quietly becoming wrong.

DOC_PATH = Path(__file__).parents[5] / "docs" / "component-config-schema.md"


def doc_text() -> str:
    if not DOC_PATH.exists():
        pytest.skip(f"{DOC_PATH.name} is not present in this checkout")
    return DOC_PATH.read_text()


class TestDocumentedErrorCodes:
    """The error-code vocabulary is closed, so an eleventh code must be documented with it."""

    def _documented_codes(self):
        # Scoped to the vocabulary section: the field tables elsewhere in the doc have the same
        # row shape, and matching those would compare the codes against every documented field.
        section = re.search(r"^## \d+\. Error codes$(.*?)^## ", doc_text(), flags=re.MULTILINE | re.DOTALL)
        assert section is not None, "the doc no longer has an 'Error codes' section"
        # The table's first column, e.g. "| `unknown_field` | Fires when ... |".
        return set(re.findall(r"^\|\s*`([a-z_]+)`\s*\|", section.group(1), flags=re.MULTILINE))

    def test_documented_codes_match_the_exported_vocabulary(self):
        exported = {value for name, value in vars(schema).items() if name.startswith("CODE_")}

        assert exported, "no CODE_* constants found; the vocabulary moved"
        assert self._documented_codes() == exported

    def test_the_documented_schema_version_is_the_current_one(self):
        assert f"`SCHEMA_VERSION = {SCHEMA_VERSION}`" in doc_text()


class TestDocumentedDbTypes:
    """The doc enumerates the registered db backends, and an enumeration is one backend from stale."""

    def test_documented_db_types_match_the_registry(self):
        # The block is marked in the source so the parse cannot drift onto neighbouring prose,
        # which is full of backticked field names that look just like backend names.
        block = re.search(r"<!-- db-types:.*?-->\s*```text\n(.*?)```", doc_text(), flags=re.DOTALL)
        assert block is not None, "the doc no longer has a marked db-types block"
        documented = {name.strip() for name in block.group(1).replace("\n", " ").split(",") if name.strip()}

        assert documented == set(discovery.names("db"))
