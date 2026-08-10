"""The tenant capability policy: document semantics, tool classification, the walker (EGPT-2.3).

No optional dependency is imported anywhere here. Privileged toolkits are stood up as plain
`Toolkit` subclasses with `__module__` rewritten to the package the real one lives in, which
exercises the actual module-prefix matcher and MRO walk without pulling in docker, e2b, boto3 or
any of the rest. That keeps the file green under the `unit-drivers-absent` CI job with no
`pytest.importorskip` gates.
"""

import json
import re
from pathlib import Path

import pytest

from egpt.registry import Registry
from egpt.registry.policy import (
    CODE_CAPABILITY_NOT_PERMITTED,
    CODE_PRIVILEGED_TOOL_BLOCKED,
    PRIVILEGED_FUNCTION_NAMES,
    PRIVILEGED_TOOLKITS,
    CapabilityPolicy,
    PolicyViolationError,
    TenantPolicy,
    check_capabilities,
    classify_tools,
    enforce_policy_or_raise,
    get_default_policy,
    resolve_policy,
)
from egpt.tools.function import Function
from egpt.tools.toolkit import Toolkit

# =============================================================================
# Fixtures and helpers
# =============================================================================


def _toolkit(name, module, **functions):
    """A live Toolkit exposing `functions`, pretending to live in `module`.

    Rewriting `__module__` is the whole trick: classification keys on the module path, so this
    is indistinguishable from the real toolkit as far as the policy is concerned.
    """
    namespace = {}
    for function_name, doc in functions.items():

        def make(fn_name, fn_doc):
            def fn(self, value: str = "") -> str:
                return value

            fn.__name__ = fn_name
            fn.__doc__ = fn_doc
            return fn

        namespace[function_name] = make(function_name, doc)

    def __init__(self):
        Toolkit.__init__(self, name=name, tools=[getattr(self, fn) for fn in functions])

    namespace["__init__"] = __init__
    cls = type(f"Fake_{name}", (Toolkit,), namespace)
    cls.__module__ = module
    return cls()


@pytest.fixture
def shell_toolkit():
    return _toolkit("shell_tools", "egpt.integrations.tools.shell.shell", run_shell_command="Run a command.")


@pytest.fixture
def sql_toolkit():
    return _toolkit(
        "sql_tools",
        "egpt.integrations.tools.sql.sql",
        run_sql_query="Run SQL.",
        list_tables="List tables.",
    )


@pytest.fixture
def mcp_toolkit():
    """An MCP client toolkit, detected the way egpt/os/app.py detects one: by MRO class name."""

    class MCPTools(Toolkit):
        pass

    cls = _toolkit("mcp_docs_internal", "myapp.mcp", search_docs="Search the docs.").__class__
    merged = type("FakeMCPTools", (MCPTools, cls), {})
    merged.__module__ = "myapp.mcp"
    return merged()


def weather(city: str) -> str:
    """Look up the weather."""
    return f"sunny in {city}"


def read_file(path: str) -> str:
    """A tenant's own read_file, colliding by name with four privileged toolkits."""
    return path


def _policy(**defaults):
    return CapabilityPolicy.from_dict({"defaults": defaults})


def _codes(violations):
    return [v.code for v in violations]


# =============================================================================
# The privileged inventory
# =============================================================================


class TestPrivilegedInventory:
    def test_every_ticketed_toolkit_is_in_the_table(self):
        """The eleven toolkits EGPT-2.3 names, and no silent omission."""
        assert set(PRIVILEGED_TOOLKITS) == {
            "shell",
            "python",
            "docker",
            "e2b",
            "daytona",
            "sql",
            "aws_lambda",
            "aws_ses",
            "file",
            "local_file_system",
            "file_generation",
        }

    def test_every_row_names_an_integrations_package(self):
        """The table is strings only; matching is by module prefix, so the shape has to hold.

        That the module is never actually *imported* is a layering property, and it is asserted
        where the other layering properties live -- `test_schema_layering.py`, in a subprocess.
        An in-process `module not in sys.modules` check here would pass or fail on which other
        test file the xdist worker happened to run first, not on the property.
        """
        for slug, toolkit in PRIVILEGED_TOOLKITS.items():
            assert toolkit.module.startswith("egpt.integrations.tools."), slug
            assert not toolkit.module.endswith("."), slug
            assert toolkit.functions, f"{slug} exposes no functions, so nothing would ever match it"

    def test_the_backstop_records_every_colliding_owner(self):
        """The collisions are the reason the backstop fails closed rather than picking one."""
        assert PRIVILEGED_FUNCTION_NAMES["read_file"] == ("daytona", "file", "local_file_system", "python")
        assert PRIVILEGED_FUNCTION_NAMES["run_shell_command"] == ("daytona", "shell")
        assert PRIVILEGED_FUNCTION_NAMES["run_python_code"] == ("e2b", "python")
        assert PRIVILEGED_FUNCTION_NAMES["run_sql_query"] == ("sql",)


# =============================================================================
# Document parsing
# =============================================================================


class TestDocumentParsing:
    def test_absent_key_inherits_defaults_and_present_key_replaces(self):
        policy = CapabilityPolicy.from_dict(
            {
                "defaults": {"models": ["openai"], "tools": ["a"], "granted_privileged": ["sql"]},
                "tenants": {"acme": {"models": ["openai:gpt-5.5"]}},
            }
        )
        acme = policy.for_tenant("acme")
        assert acme.models == ("openai:gpt-5.5",)  # replaced
        assert acme.tools == ("a",)  # inherited
        assert acme.granted_privileged == ("sql",)  # inherited

    def test_null_means_unrestricted_and_empty_list_means_nothing(self):
        assert _policy(models=None).for_tenant(None).models is None
        assert _policy(models=[]).for_tenant(None).models == ()

    def test_an_omitted_key_is_unrestricted(self):
        assert TenantPolicy().models is None
        assert _policy().for_tenant(None).mcp_servers is None

    def test_unknown_tenant_falls_back_to_defaults_by_default(self):
        policy = CapabilityPolicy.from_dict({"defaults": {"models": ["openai"]}})
        assert policy.for_tenant("never-seen").models == ("openai",)

    def test_unknown_tenant_deny_gives_the_empty_policy(self):
        policy = CapabilityPolicy.from_dict({"unknown_tenant": "deny", "defaults": {"models": ["openai"]}})
        denied = policy.for_tenant("never-seen")
        assert denied.models == () and denied.tools == () and denied.granted_privileged == ()

    def test_tenant_none_gets_defaults_not_deny_all(self):
        """None is unscoped, and unscoped must not mean "denied" -- see for_tenant's docstring."""
        policy = CapabilityPolicy.from_dict({"unknown_tenant": "deny", "defaults": {"models": ["openai"]}})
        assert policy.for_tenant(None).models == ("openai",)

    @pytest.mark.parametrize(
        "document, fragment",
        [
            ({"version": 99}, "understands up to"),
            ({"version": "1"}, "must be an integer"),
            ({"mode": "audit"}, "'enforce' or 'warn'"),
            ({"unknown_tenant": "nope"}, "'defaults' or 'deny'"),
            ({"nonsense": 1}, "unknown key"),
            ({"defaults": {"priviledged_tools": []}}, "unknown key"),
            ({"defaults": {"models": "openai"}}, "must be a list of strings"),
            ({"defaults": {"models": [""]}}, "non-empty strings"),
            ({"defaults": {"granted_privileged": ["*"]}}, "does not accept '*'"),
            ({"defaults": {"granted_privileged": ["shel"]}}, "unknown privileged toolkit"),
        ],
    )
    def test_a_document_it_cannot_understand_is_rejected(self, document, fragment):
        """Strict on purpose: a typo that parsed as "no grants" would read as a working policy."""
        with pytest.raises(ValueError, match=fragment):
            CapabilityPolicy.from_dict(document)

    def test_from_file_round_trips(self, tmp_path):
        path = tmp_path / "policy.json"
        path.write_text(json.dumps({"defaults": {"models": ["openai"]}, "tenants": {"acme": {"tools": []}}}))
        policy = CapabilityPolicy.from_file(path)
        assert policy.for_tenant("acme").tools == ()
        assert policy.for_tenant("acme").models == ("openai",)

    def test_a_missing_file_raises_rather_than_disabling_the_policy(self, tmp_path):
        """A typo'd path must not silently switch the security control off."""
        with pytest.raises(FileNotFoundError, match="Capability policy file not found"):
            CapabilityPolicy.from_file(tmp_path / "nope.json")

    def test_malformed_json_names_the_file(self, tmp_path):
        path = tmp_path / "policy.json"
        path.write_text("{not json")
        with pytest.raises(ValueError, match="not valid JSON"):
            CapabilityPolicy.from_file(path)


# =============================================================================
# Policy resolution
# =============================================================================


class TestPolicyResolution:
    def test_unconfigured_resolves_to_a_disabled_policy(self, monkeypatch):
        monkeypatch.delenv("EGPT_CAPABILITY_POLICY_FILE", raising=False)
        monkeypatch.delenv("AGNO_CAPABILITY_POLICY_FILE", raising=False)
        assert get_default_policy() is None
        assert resolve_policy().enabled is False

    def test_explicit_beats_registry_beats_environment(self, tmp_path, monkeypatch):
        env_policy = tmp_path / "env.json"
        env_policy.write_text(json.dumps({"defaults": {"models": ["from-env"]}}))
        monkeypatch.setenv("EGPT_CAPABILITY_POLICY_FILE", str(env_policy))

        registry = Registry(capability_policy=_policy(models=["from-registry"]))
        explicit = _policy(models=["from-explicit"])

        assert resolve_policy(explicit, registry).for_tenant(None).models == ("from-explicit",)
        assert resolve_policy(None, registry).for_tenant(None).models == ("from-registry",)
        assert resolve_policy(None, None).for_tenant(None).models == ("from-env",)

    def test_a_disabled_policy_finds_nothing(self, shell_toolkit):
        config = {"model": "anything:at-all", "tools": [{"name": "run_shell_command"}]}
        assert check_capabilities(config, "agent", policy=CapabilityPolicy.disabled(), tenant_id=None) == []


# =============================================================================
# Tool classification
# =============================================================================


class TestToolClassification:
    def test_a_live_privileged_toolkit_resolves_to_its_slug(self, shell_toolkit):
        origin = classify_tools(["run_shell_command"], Registry(tools=[shell_toolkit]))["run_shell_command"]
        assert origin.source == "registry"
        assert origin.privileged_slugs == ("shell",)
        assert origin.ambiguous is False

    def test_a_subclass_defined_elsewhere_is_still_privileged(self):
        """The MRO walk, not just type(toolkit).__module__ -- a subclass must not launder shell."""

        base = _toolkit("shell_tools", "egpt.integrations.tools.shell.shell", run_shell_command="Run.").__class__
        subclass = type("MyShell", (base,), {})
        subclass.__module__ = "myapp.tools"

        origin = classify_tools(["run_shell_command"], Registry(tools=[subclass()]))["run_shell_command"]
        assert origin.privileged_slugs == ("shell",)

    def test_no_registry_falls_back_to_the_static_table(self):
        origin = classify_tools(["run_shell_command"], None)["run_shell_command"]
        assert origin.source == "backstop"
        assert origin.privileged_slugs == ("daytona", "shell")
        assert origin.ambiguous is True

    def test_a_registry_resolved_bare_function_is_not_run_through_the_backstop(self):
        """A tenant's own `read_file` is theirs. Registry evidence beats the name table."""
        registry = Registry(tools=[Function.from_callable(read_file)])
        origin = classify_tools(["read_file"], registry)["read_file"]
        assert origin.source == "registry"
        assert origin.privileged_slugs == ()

    def test_an_mcp_toolkit_classifies_as_mcp_under_its_server_name(self, mcp_toolkit):
        origin = classify_tools(["search_docs"], Registry(tools=[mcp_toolkit]))["search_docs"]
        assert origin.kind == "mcp"
        assert origin.ids == ("mcp_docs_internal",)

    def test_an_unknown_name_stays_unknown(self):
        origin = classify_tools(["totally_bespoke"], Registry())["totally_bespoke"]
        assert origin.source == "unknown"
        assert origin.privileged_slugs == ()


# =============================================================================
# The walker
# =============================================================================


class TestModelChecks:
    @pytest.mark.parametrize(
        "allowed, model, permitted",
        [
            (["openai"], "openai:gpt-5.5", True),
            (["openai"], "anthropic:claude-sonnet-5", False),
            (["openai:gpt-5.5"], "openai:gpt-5.5", True),
            (["openai:gpt-5.5"], "openai:gpt-4o", False),
            (["openai:gpt-5*"], "openai:gpt-5.5", True),
            (["openai:*"], "openai:anything", True),
            (["*"], "anthropic:claude-sonnet-5", True),
            ([], "openai:gpt-5.5", False),
        ],
    )
    def test_model_entry_grammar(self, allowed, model, permitted):
        violations = check_capabilities({"model": model}, "agent", policy=_policy(models=allowed), tenant_id=None)
        assert (violations == []) is permitted

    def test_the_provider_is_normalized_the_same_way_the_validator_normalizes_it(self):
        """Reuses resolve_provider_key, so casing and aliases cannot disagree between the two."""
        policy = _policy(models=["OpenAI"])
        for model in [
            "openai:gpt-5.5",
            {"id": "gpt-5.5", "provider": "OpenAI"},
            {"id": "gpt-5.5", "provider": "openai"},
        ]:
            assert check_capabilities({"model": model}, "agent", policy=policy, tenant_id=None) == []

    def test_a_refused_model_names_itself_and_the_permitted_set(self):
        violations = check_capabilities(
            {"model": "anthropic:claude-sonnet-5"}, "agent", policy=_policy(models=["openai"]), tenant_id=None
        )
        assert _codes(violations) == [CODE_CAPABILITY_NOT_PERMITTED]
        assert violations[0].path == "model"
        assert "anthropic:claude-sonnet-5" in violations[0].message
        assert "openai" in violations[0].message

    def test_an_absent_model_is_not_a_policy_problem(self):
        assert check_capabilities({}, "agent", policy=_policy(models=[]), tenant_id=None) == []


class TestPrivilegedToolChecks:
    def test_a_privileged_tool_is_blocked_by_default(self, sql_toolkit):
        violations = check_capabilities(
            {"tools": [{"name": "run_sql_query"}]},
            "agent",
            policy=_policy(tools=["*"]),
            tenant_id=None,
            registry=Registry(tools=[sql_toolkit]),
        )
        assert _codes(violations) == [CODE_PRIVILEGED_TOOL_BLOCKED]
        assert violations[0].path == "tools[0].name"
        assert "sql" in violations[0].message

    def test_a_tools_wildcard_does_not_unlock_the_denylist(self, shell_toolkit, sql_toolkit):
        """The grant lives on its own key precisely so `tools: ["*"]` cannot reach it."""
        registry = Registry(tools=[shell_toolkit, sql_toolkit])
        config = {"tools": [{"name": "run_shell_command"}, {"name": "run_sql_query"}]}
        violations = check_capabilities(config, "agent", policy=_policy(tools=["*"]), tenant_id=None, registry=registry)
        assert _codes(violations) == [CODE_PRIVILEGED_TOOL_BLOCKED, CODE_PRIVILEGED_TOOL_BLOCKED]

    def test_an_explicit_grant_admits_exactly_that_toolkit(self, shell_toolkit, sql_toolkit):
        registry = Registry(tools=[shell_toolkit, sql_toolkit])
        policy = _policy(tools=["*"], granted_privileged=["sql"])
        assert (
            check_capabilities(
                {"tools": [{"name": "run_sql_query"}]}, "agent", policy=policy, tenant_id=None, registry=registry
            )
            == []
        )
        still_blocked = check_capabilities(
            {"tools": [{"name": "run_shell_command"}]}, "agent", policy=policy, tenant_id=None, registry=registry
        )
        assert _codes(still_blocked) == [CODE_PRIVILEGED_TOOL_BLOCKED]

    def test_an_ambiguous_name_needs_every_candidate_granted(self):
        """Fail-closed: the registry's entrypoint lookup is last-wins, so picking one would guess."""
        config = {"tools": [{"name": "run_shell_command"}]}
        partial = check_capabilities(
            config, "agent", policy=_policy(tools=["*"], granted_privileged=["shell"]), tenant_id=None
        )
        assert _codes(partial) == [CODE_PRIVILEGED_TOOL_BLOCKED]
        assert "daytona" in partial[0].message

        full = check_capabilities(
            config, "agent", policy=_policy(tools=["*"], granted_privileged=["shell", "daytona"]), tenant_id=None
        )
        assert full == []

    def test_a_registry_that_disambiguates_narrows_the_grant_needed(self, shell_toolkit):
        """With the real toolkit registered, granting `shell` alone is enough."""
        config = {"tools": [{"name": "run_shell_command"}]}
        policy = _policy(tools=["*"], granted_privileged=["shell"])
        assert (
            check_capabilities(config, "agent", policy=policy, tenant_id=None, registry=Registry(tools=[shell_toolkit]))
            == []
        )


class TestToolAndMcpChecks:
    def test_a_tool_outside_the_allow_list_is_refused(self):
        registry = Registry(tools=[Function.from_callable(weather)])
        violations = check_capabilities(
            {"tools": [{"name": "weather"}]},
            "agent",
            policy=_policy(tools=["search_docs"]),
            tenant_id=None,
            registry=registry,
        )
        assert _codes(violations) == [CODE_CAPABILITY_NOT_PERMITTED]

    def test_a_tool_inside_the_allow_list_is_admitted(self):
        registry = Registry(tools=[Function.from_callable(weather)])
        assert (
            check_capabilities(
                {"tools": [{"name": "weather"}]},
                "agent",
                policy=_policy(tools=["weather"]),
                tenant_id=None,
                registry=registry,
            )
            == []
        )

    def test_an_mcp_tool_is_matched_against_mcp_servers_not_tools(self, mcp_toolkit):
        registry = Registry(tools=[mcp_toolkit])
        config = {"tools": [{"name": "search_docs"}]}

        refused = check_capabilities(
            config, "agent", policy=_policy(tools=["*"], mcp_servers=[]), tenant_id=None, registry=registry
        )
        assert _codes(refused) == [CODE_CAPABILITY_NOT_PERMITTED]
        assert "mcp_docs_internal" in refused[0].message

        allowed = check_capabilities(
            config, "agent", policy=_policy(tools=[], mcp_servers=["mcp_docs_*"]), tenant_id=None, registry=registry
        )
        assert allowed == []


class TestWalkerBehaviour:
    def test_violations_accumulate_rather_than_short_circuit(self):
        config = {"model": "anthropic:claude-sonnet-5", "tools": [{"name": "run_sql_query"}]}
        violations = check_capabilities(config, "agent", policy=_policy(models=["openai"], tools=["*"]), tenant_id=None)
        assert _codes(violations) == [CODE_CAPABILITY_NOT_PERMITTED, CODE_PRIVILEGED_TOOL_BLOCKED]

    @pytest.mark.parametrize("component_type", ["agent", "team", "workflow"])
    def test_every_component_type_is_walked(self, component_type):
        """A team config carries a model, and `tools` reaches all three via extra="allow"."""
        config = {"model": "anthropic:claude-sonnet-5"}
        violations = check_capabilities(config, component_type, policy=_policy(models=["openai"]), tenant_id=None)
        assert _codes(violations) == [CODE_CAPABILITY_NOT_PERMITTED]

    def test_the_per_tenant_policy_is_the_one_applied(self):
        policy = CapabilityPolicy.from_dict({"defaults": {"models": []}, "tenants": {"acme": {"models": ["openai"]}}})
        config = {"model": "openai:gpt-5.5"}
        assert check_capabilities(config, "agent", policy=policy, tenant_id="acme") == []
        assert _codes(check_capabilities(config, "agent", policy=policy, tenant_id="other")) == [
            CODE_CAPABILITY_NOT_PERMITTED
        ]


# =============================================================================
# enforce_policy_or_raise
# =============================================================================


class TestEnforcement:
    def test_it_raises_with_every_violation_attached(self):
        config = {"model": "anthropic:claude-sonnet-5", "tools": [{"name": "run_sql_query"}]}
        with pytest.raises(PolicyViolationError) as excinfo:
            enforce_policy_or_raise(
                config, "agent", tenant_id="acme", policy=_policy(models=["openai"], tools=["*"]), component_id="a1"
            )
        error = excinfo.value
        assert len(error.violations) == 2
        assert error.component_id == "a1"
        assert error.tenant_id == "acme"
        assert "a1" in str(error) and "acme" in str(error)

    def test_a_permitted_config_passes_silently(self):
        enforce_policy_or_raise({"model": "openai:gpt-5.5"}, "agent", tenant_id=None, policy=_policy(models=["openai"]))

    def test_no_policy_configured_is_a_no_op(self, monkeypatch):
        monkeypatch.delenv("EGPT_CAPABILITY_POLICY_FILE", raising=False)
        monkeypatch.delenv("AGNO_CAPABILITY_POLICY_FILE", raising=False)
        enforce_policy_or_raise({"tools": [{"name": "run_shell_command"}]}, "agent", tenant_id=None)

    def test_warn_mode_logs_instead_of_raising(self, caplog):
        """The rollout lever: audit an existing corpus before turning enforcement on."""
        policy = CapabilityPolicy.from_dict({"mode": "warn", "defaults": {"models": ["openai"]}})
        with caplog.at_level("WARNING"):
            enforce_policy_or_raise({"model": "anthropic:x"}, "agent", tenant_id=None, policy=policy)
        assert "capability-policy:warn" in caplog.text

    def test_warn_mode_reports_nothing_through_the_walker_either(self):
        policy = CapabilityPolicy.from_dict({"mode": "warn", "defaults": {"models": ["openai"]}})
        # The walker still finds them -- it is the caller that decides not to reject.
        assert len(check_capabilities({"model": "anthropic:x"}, "agent", policy=policy, tenant_id=None)) == 1


# =============================================================================
# Doc guard
# =============================================================================

DOC_PATH = Path(__file__).parents[5] / "docs" / "capability-policy.md"


class TestDocumentedInventory:
    """The privileged table is operator-facing config, so a silent drift would be a silent hole."""

    def test_the_documented_table_matches_the_code(self):
        if not DOC_PATH.exists():
            pytest.skip(f"{DOC_PATH.name} is not present in this checkout")

        section = re.search(
            r"<!-- privileged-toolkits:.*?-->(.*?)^---", DOC_PATH.read_text(), flags=re.MULTILINE | re.DOTALL
        )
        assert section is not None, "the doc no longer has the marked privileged-toolkit table"

        rows = dict(
            re.findall(r"^\|\s*`([a-z0-9_]+)`\s*\|\s*`([a-z0-9_.]+)`\s*\|", section.group(1), flags=re.MULTILINE)
        )
        assert rows == {slug: toolkit.module for slug, toolkit in PRIVILEGED_TOOLKITS.items()}
