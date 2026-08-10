"""EGPT-3.2: one config, two identities, two different backends, no code change.

The headline assertion is on ``resolve_detailed(...).provider_key`` -- the registry *key* --
rather than on the concrete model class. Asserting the class would need the backend's SDK
installed, so it would have to be ``importorskip``-gated and would therefore not run on the
base install. The key is a string the provider decides on before it constructs anything, so
this assertion always runs and still proves the routing decision.

A second test does assert the concrete class, gated on the SDK, so "the key is right but it
builds the wrong thing" cannot hide.
"""

from typing import Any, Dict

import pytest

from egpt.providers import (
    ConfigDrivenModelProvider,
    ModelPolicy,
    ModelProvider,
    ModelProviderConfigError,
    ModelResolveRequest,
    ProviderConfig,
    ProviderMode,
)
from egpt.registry import discovery

# --------------------------------------------------------------------------- #
# A fake backend, registered under two keys, so resolution can be asserted end to end
# without any SDK. The discovery table is restored by the package conftest.
# --------------------------------------------------------------------------- #


class _FakeModel:
    """Stands in for a Model. Records everything discovery.construct forwarded."""

    def __init__(self, id: str, **config: Any) -> None:
        self.id = id
        self.config: Dict[str, Any] = config
        self.provider = "fake"


@pytest.fixture
def fake_backends():
    """Register 'fake-local' and 'fake-frontier' for the duration of one test."""
    discovery.register("model", "fake-local", obj=_FakeModel)
    discovery.register("model", "fake-frontier", obj=_FakeModel)
    yield


@pytest.fixture
def two_tenant_config() -> ProviderConfig:
    """One config. 'acme' is served locally; everyone else gets the frontier default."""
    return ProviderConfig(
        default=ModelPolicy(
            mode=ProviderMode.FRONTIER,
            model_id="gpt-5.5",
            provider_key="fake-frontier",
            reason="default tier",
        ),
        policies={
            ("acme",): ModelPolicy(
                mode=ProviderMode.LOCAL,
                model_id="qwen3-8b",
                provider_key="fake-local",
                reason="acme is data-resident",
            ),
        },
    )


# --------------------------------------------------------------------------- #
# The headline: same code path, different backend per identity.
# --------------------------------------------------------------------------- #


def test_same_code_path_resolves_different_backends_per_identity(two_tenant_config):
    """The acceptance criterion for EGPT-3.2, asserted on the registry key.

    Imports nothing beyond the seam, needs no SDK, and runs on the base install.
    """
    provider = ConfigDrivenModelProvider(two_tenant_config)

    local = provider.policy_for(ModelResolveRequest(tenant_id="acme", agent_id="support"))
    frontier = provider.policy_for(ModelResolveRequest(tenant_id="globex", agent_id="support"))

    assert local is not None and frontier is not None
    assert local.mode is ProviderMode.LOCAL
    assert frontier.mode is ProviderMode.FRONTIER
    assert local.effective_provider_key != frontier.effective_provider_key


def test_resolve_detailed_reports_the_selected_key_per_identity(two_tenant_config, fake_backends):
    """Same assertion, now through the full resolve path including construction."""
    provider = ConfigDrivenModelProvider(two_tenant_config)

    local = provider.resolve_detailed(ModelResolveRequest(tenant_id="acme"))
    frontier = provider.resolve_detailed(ModelResolveRequest(tenant_id="globex"))

    assert (local.provider_key, local.mode) == ("fake-local", ProviderMode.LOCAL)
    assert (frontier.provider_key, frontier.mode) == ("fake-frontier", ProviderMode.FRONTIER)
    assert local.model.id == "qwen3-8b"
    assert frontier.model.id == "gpt-5.5"
    assert local.reason == "acme is data-resident"


def test_switching_mode_is_a_config_edit_not_a_code_edit(fake_backends):
    """Two configs differing only in the default policy's mode; identical call site."""
    request = ModelResolveRequest(tenant_id="globex", agent_id="support")

    local_cfg = ProviderConfig(
        default=ModelPolicy(mode=ProviderMode.LOCAL, model_id="qwen3-8b", provider_key="fake-local")
    )
    frontier_cfg = ProviderConfig(
        default=ModelPolicy(mode=ProviderMode.FRONTIER, model_id="gpt-5.5", provider_key="fake-frontier")
    )

    assert ConfigDrivenModelProvider(local_cfg).resolve_detailed(request).provider_key == "fake-local"
    assert ConfigDrivenModelProvider(frontier_cfg).resolve_detailed(request).provider_key == "fake-frontier"


def test_concrete_class_is_built_when_the_sdk_is_present():
    """The key-based assertions above prove the *decision*; this proves the *construction*.

    Gated on the SDK: the base install ships none, so this skips there by design.
    """
    pytest.importorskip("openai")
    config = ProviderConfig(
        default=ModelPolicy(mode=ProviderMode.FRONTIER, model_id="gpt-5.5", provider_key="openai-responses")
    )
    result = ConfigDrivenModelProvider(config).resolve_detailed(ModelResolveRequest(tenant_id="globex"))
    assert type(result.model).__name__ == "OpenAIResponses"
    assert result.model.id == "gpt-5.5"


# --------------------------------------------------------------------------- #
# Contract conformance
# --------------------------------------------------------------------------- #


def test_satisfies_the_model_provider_protocol(two_tenant_config):
    provider = ConfigDrivenModelProvider(two_tenant_config)
    assert isinstance(provider, ModelProvider)


def test_capabilities_needs_no_sdk_and_reports_both_modes(two_tenant_config):
    caps = ConfigDrivenModelProvider(two_tenant_config).capabilities()
    assert set(caps.modes) == {ProviderMode.LOCAL, ProviderMode.FRONTIER}
    assert set(caps.provider_keys) == {"fake-local", "fake-frontier"}
    assert caps.supports_adapters is True
    # Mixed-mode config can leave the VPC, so it must not claim egress_free.
    assert caps.egress_free is False


def test_capabilities_reports_egress_free_only_for_a_local_only_config():
    config = ProviderConfig(default=ModelPolicy(mode=ProviderMode.LOCAL, model_id="qwen3-8b"))
    assert ConfigDrivenModelProvider(config).capabilities().egress_free is True


def test_resolve_delegates_to_resolve_detailed(two_tenant_config, fake_backends):
    provider = ConfigDrivenModelProvider(two_tenant_config)
    model = provider.resolve(ModelResolveRequest(tenant_id="acme"))
    assert model.id == "qwen3-8b"


# --------------------------------------------------------------------------- #
# Precedence
# --------------------------------------------------------------------------- #


def test_explicit_requested_model_wins_when_no_policy_pins_a_model():
    """User intent outranks configuration -- the contract requires returning it unchanged.

    The policy here states a mode but no ``model_id``, i.e. "serve this tenant locally" without
    naming which model. Nothing to override the caller with, so the caller's model stands.
    """
    config = ProviderConfig(policies={("acme",): ModelPolicy(mode=ProviderMode.LOCAL)})
    pinned = _FakeModel(id="pinned-model")
    result = ConfigDrivenModelProvider(config).resolve_detailed(
        ModelResolveRequest(tenant_id="acme", requested=pinned)  # type: ignore[arg-type]
    )
    assert result.model is pinned
    assert result.mode is ProviderMode.LOCAL


def test_explicit_requested_model_stands_when_nothing_is_configured():
    """The EGPT-3.1 no-op path: an unconfigured provider must not reject a pinned model."""
    pinned = _FakeModel(id="pinned-model")
    result = ConfigDrivenModelProvider(ProviderConfig()).resolve_detailed(
        ModelResolveRequest(tenant_id="acme", requested=pinned)  # type: ignore[arg-type]
    )
    assert result.model is pinned


def test_a_policy_pinning_a_model_id_overrides_the_requested_model(fake_backends):
    """A deployment that pins a model for a tenant must not be silently bypassed."""
    config = ProviderConfig(
        default=ModelPolicy(mode=ProviderMode.LOCAL, model_id="qwen3-8b", provider_key="fake-local")
    )
    pinned = _FakeModel(id="user-choice")
    result = ConfigDrivenModelProvider(config).resolve_detailed(
        ModelResolveRequest(tenant_id="acme", requested=pinned)  # type: ignore[arg-type]
    )
    assert result.model is not pinned
    assert result.model.id == "qwen3-8b"


def test_explicit_request_mode_selects_the_matching_policy(fake_backends):
    """A caller override picks the default when the matched policy speaks the other mode."""
    config = ProviderConfig(
        default=ModelPolicy(mode=ProviderMode.FRONTIER, model_id="gpt-5.5", provider_key="fake-frontier"),
        policies={("acme",): ModelPolicy(mode=ProviderMode.LOCAL, model_id="qwen3-8b", provider_key="fake-local")},
    )
    provider = ConfigDrivenModelProvider(config)

    result = provider.resolve_detailed(ModelResolveRequest(tenant_id="acme", mode=ProviderMode.FRONTIER))
    assert result.provider_key == "fake-frontier"


def test_a_pinned_model_outranks_an_unsatisfiable_mode_override():
    """`requested` is precedence rank 1 and `mode` rank 2, so a caller who pinned both gets
    their model back rather than an error about the mode they no longer need served."""
    config = ProviderConfig(
        policies={("acme",): ModelPolicy(mode=ProviderMode.LOCAL, provider_key="fake-local")},
    )
    pinned = _FakeModel(id="pinned-model")
    result = ConfigDrivenModelProvider(config).resolve_detailed(
        ModelResolveRequest(tenant_id="acme", requested=pinned, mode=ProviderMode.FRONTIER)  # type: ignore[arg-type]
    )
    assert result.model is pinned


def test_a_pinned_model_is_not_reported_as_frontier_by_default():
    """Reporting a fabricated FRONTIER for a pinned in-VPC model would tell an escalation log
    or a billing consumer that the request left the VPC when it did not."""
    local_only = ProviderConfig(policies={("acme",): ModelPolicy(mode=ProviderMode.LOCAL)})
    pinned = _FakeModel(id="pinned-model")

    # The matched policy's mode wins.
    assert (
        ConfigDrivenModelProvider(local_only)
        .resolve_detailed(ModelResolveRequest(tenant_id="acme", requested=pinned))  # type: ignore[arg-type]
        .mode
        is ProviderMode.LOCAL
    )

    # No matching policy, but the config can only produce one mode -- so that one is reported.
    assert (
        ConfigDrivenModelProvider(local_only)
        .resolve_detailed(ModelResolveRequest(tenant_id="globex", requested=pinned))  # type: ignore[arg-type]
        .mode
        is ProviderMode.LOCAL
    )

    # The caller's own override is honoured when nothing is configured at all.
    assert (
        ConfigDrivenModelProvider(ProviderConfig())
        .resolve_detailed(
            ModelResolveRequest(tenant_id="acme", requested=pinned, mode=ProviderMode.LOCAL)  # type: ignore[arg-type]
        )
        .mode
        is ProviderMode.LOCAL
    )


def test_unsatisfiable_mode_override_is_an_error_not_a_silent_downgrade():
    config = ProviderConfig(
        policies={("acme",): ModelPolicy(mode=ProviderMode.LOCAL, model_id="qwen3-8b", provider_key="fake-local")},
    )
    with pytest.raises(ModelProviderConfigError) as exc:
        ConfigDrivenModelProvider(config).resolve_detailed(
            ModelResolveRequest(tenant_id="acme", mode=ProviderMode.FRONTIER)
        )
    assert exc.value.field_name == "mode"


# --------------------------------------------------------------------------- #
# Specificity
# --------------------------------------------------------------------------- #


def test_more_specific_keys_win(fake_backends):
    config = ProviderConfig(
        default=ModelPolicy(mode=ProviderMode.FRONTIER, model_id="gpt-5.5", provider_key="fake-frontier"),
        policies={
            ("acme",): ModelPolicy(mode=ProviderMode.LOCAL, model_id="tenant-wide", provider_key="fake-local"),
            ("acme", "support"): ModelPolicy(mode=ProviderMode.LOCAL, model_id="agent-wide", provider_key="fake-local"),
            ("acme", "support", "parser"): ModelPolicy(
                mode=ProviderMode.LOCAL, model_id="role-specific", provider_key="fake-local"
            ),
        },
    )
    provider = ConfigDrivenModelProvider(config)

    def resolved(**kwargs):
        return provider.resolve_detailed(ModelResolveRequest(**kwargs)).model.id

    assert resolved(tenant_id="acme", agent_id="support", role="parser") == "role-specific"
    assert resolved(tenant_id="acme", agent_id="support", role="model") == "agent-wide"
    assert resolved(tenant_id="acme", agent_id="other", role="model") == "tenant-wide"
    assert resolved(tenant_id="globex", agent_id="support", role="model") == "gpt-5.5"


def test_a_role_only_key_applies_across_tenants(fake_backends):
    """A cheap model for every 'parser' slot, whoever the tenant is."""
    config = ProviderConfig(
        default=ModelPolicy(mode=ProviderMode.FRONTIER, model_id="gpt-5.5", provider_key="fake-frontier"),
        policies={
            (None, None, "parser"): ModelPolicy(
                mode=ProviderMode.LOCAL, model_id="qwen3-1.7b", provider_key="fake-local"
            )
        },
    )
    provider = ConfigDrivenModelProvider(config)
    assert provider.resolve_detailed(ModelResolveRequest(tenant_id="anyone", role="parser")).model.id == "qwen3-1.7b"
    assert provider.resolve_detailed(ModelResolveRequest(tenant_id="anyone", role="model")).model.id == "gpt-5.5"


# --------------------------------------------------------------------------- #
# Adapters and construct kwargs
# --------------------------------------------------------------------------- #


def test_policy_config_is_forwarded_to_construct(fake_backends):
    config = ProviderConfig(
        default=ModelPolicy(
            mode=ProviderMode.LOCAL,
            model_id="qwen3-8b",
            provider_key="fake-local",
            config={"base_url": "http://vllm.internal:8000/v1"},
        )
    )
    model = ConfigDrivenModelProvider(config).resolve(ModelResolveRequest(tenant_id="acme"))
    assert model.config["base_url"] == "http://vllm.internal:8000/v1"


def test_request_adapter_overrides_the_policy_adapter(fake_backends):
    config = ProviderConfig(
        default=ModelPolicy(
            mode=ProviderMode.LOCAL, model_id="qwen3-8b", provider_key="fake-local", adapter="policy-lora"
        )
    )
    result = ConfigDrivenModelProvider(config).resolve_detailed(
        ModelResolveRequest(tenant_id="acme", adapter="request-lora")
    )
    assert result.adapter == "request-lora"
    assert result.model.config["adapter"] == "request-lora"


def test_adapter_is_not_forwarded_on_a_frontier_policy(fake_backends):
    """A FRONTIER policy cannot carry an adapter (validation forbids it); a request-level one
    is dropped rather than sent to a host that would reject or ignore it."""
    config = ProviderConfig(
        default=ModelPolicy(mode=ProviderMode.FRONTIER, model_id="gpt-5.5", provider_key="fake-frontier")
    )
    result = ConfigDrivenModelProvider(config).resolve_detailed(
        ModelResolveRequest(tenant_id="globex", adapter="stray-lora")
    )
    assert "adapter" not in result.model.config


# --------------------------------------------------------------------------- #
# Failure paths
# --------------------------------------------------------------------------- #


def test_no_matching_policy_and_no_default_is_an_error():
    provider = ConfigDrivenModelProvider(ProviderConfig())
    with pytest.raises(ModelProviderConfigError) as exc:
        provider.resolve(ModelResolveRequest(tenant_id="acme"))
    assert exc.value.field_name == "default"


def test_unknown_provider_key_names_the_field():
    config = ProviderConfig(
        default=ModelPolicy(mode=ProviderMode.LOCAL, model_id="qwen3-8b", provider_key="no-such-backend")
    )
    with pytest.raises(ModelProviderConfigError) as exc:
        ConfigDrivenModelProvider(config).resolve(ModelResolveRequest(tenant_id="acme"))
    assert exc.value.field_name == "provider_key"


def test_an_explicit_adapter_outranks_one_buried_in_the_config_blob(fake_backends):
    """Deferring to `config["adapter"]` would report one adapter on the result while different
    weights answered -- the exact mismatch the frontier-adapter guard exists to prevent."""
    config = ProviderConfig(
        default=ModelPolicy(
            mode=ProviderMode.LOCAL,
            model_id="qwen3-8b",
            provider_key="fake-local",
            adapter="policy-lora",
            config={"adapter": "stale-lora"},
        )
    )
    result = ConfigDrivenModelProvider(config).resolve_detailed(
        ModelResolveRequest(tenant_id="acme", adapter="request-lora")
    )
    assert result.adapter == "request-lora"
    assert result.model.config["adapter"] == "request-lora"


def test_a_backend_value_error_is_not_relabelled_as_an_unknown_provider_key(fake_backends):
    """`discovery.construct` runs the constructor inside `resolve`, so a single try/except
    would blame `provider_key` for a backend's own complaint about a parameter value."""

    class _PickyModel:
        def __init__(self, id: str, temperature: float = 0.0) -> None:
            if temperature > 2:
                raise ValueError("temperature must be between 0 and 2")
            self.id = id

    discovery.register("model", "picky", obj=_PickyModel)
    config = ProviderConfig(
        default=ModelPolicy(
            mode=ProviderMode.LOCAL, model_id="qwen3-8b", provider_key="picky", config={"temperature": 9.0}
        )
    )
    with pytest.raises(ValueError) as exc:
        ConfigDrivenModelProvider(config).resolve(ModelResolveRequest(tenant_id="acme"))
    assert "temperature must be between 0 and 2" in str(exc.value)
    assert getattr(exc.value, "field_name", None) != "provider_key"


def test_backend_rejecting_a_kwarg_is_reported_as_a_config_error(fake_backends):
    class _StrictModel:
        def __init__(self, id: str) -> None:  # accepts no extra kwargs
            self.id = id

    discovery.register("model", "strict", obj=_StrictModel)
    config = ProviderConfig(
        default=ModelPolicy(
            mode=ProviderMode.LOCAL, model_id="qwen3-8b", provider_key="strict", config={"base_url": "http://x"}
        )
    )
    with pytest.raises(ModelProviderConfigError) as exc:
        ConfigDrivenModelProvider(config).resolve(ModelResolveRequest(tenant_id="acme"))
    assert exc.value.field_name == "config"


def test_constructing_with_a_non_config_is_rejected():
    with pytest.raises(ModelProviderConfigError) as exc:
        ConfigDrivenModelProvider({"default": "frontier"})  # type: ignore[arg-type]
    assert exc.value.field_name == "config"


# --------------------------------------------------------------------------- #
# Laziness: importing the provider must not import a backend.
# --------------------------------------------------------------------------- #


def test_importing_the_provider_imports_no_integration():
    """The module-scope rule, asserted in-process against the module's own imports.

    ``tests/unit/registry/test_discovery_laziness.py`` covers this in a clean subprocess; this
    keeps the failure local and readable when someone adds a top-level import here.
    """
    import ast
    from pathlib import Path

    import egpt.providers.config_provider as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    offenders = []
    for node in ast.walk(tree):
        # Only module-scope statements: a function-local import is the sanctioned form.
        if node in tree.body and isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for name in names:
                if name.startswith("egpt.integrations") or name.startswith("egpt.registry"):
                    offenders.append(name)
    assert not offenders, f"module-scope imports of integrations/registry: {offenders}"


# --------------------------------------------------------------------------- #
# Review regressions (PR #46) -- each test is one inline finding's repro, pinned
# --------------------------------------------------------------------------- #


class _NoKwargsLocalModel:
    """Signature-shaped like the real VLLM dataclass: an `id`, no `adapter`, no **kwargs."""

    provider = "NoKwargs"

    def __init__(self, id: str):
        self.id = id
        self.config = {}


def test_pinned_model_survives_unsatisfiable_mode_when_policy_pins_a_model_id(fake_backends):
    """Finding 3744507133: `requested` at rank 1 must not be denied by a mode error at rank 2.

    The matched policy carries a model_id, so the pinned branch falls through -- previously
    into `policy_for`, which re-raised the very error `_pinning_policy` had swallowed."""
    config = ProviderConfig(
        policies={("acme",): ModelPolicy(mode=ProviderMode.LOCAL, model_id="qwen", provider_key="fake-local")}
    )
    provider = ConfigDrivenModelProvider(config)

    result = provider.resolve_detailed(
        ModelResolveRequest(tenant_id="acme", requested=_FakeModel(id="pinned"), mode=ProviderMode.FRONTIER)
    )

    # The policy's pin wins over the caller's model; the point is that it RESOLVES.
    assert result.model.id == "qwen"
    assert result.mode is ProviderMode.LOCAL


def test_pinned_mode_report_prefers_the_callers_explicit_mode(fake_backends):
    """Finding 3744507135: FRONTIER said + frontier model pinned must not be logged LOCAL --
    that under-reports egress to escalation logs and billing."""
    config = ProviderConfig(policies={("acme",): ModelPolicy(mode=ProviderMode.LOCAL)})
    provider = ConfigDrivenModelProvider(config)

    result = provider.resolve_detailed(
        ModelResolveRequest(tenant_id="acme", requested=_FakeModel(id="gpt-5.5"), mode=ProviderMode.FRONTIER)
    )

    assert result.mode is ProviderMode.FRONTIER


def test_result_adapter_is_none_when_no_adapter_was_applied(fake_backends):
    """Finding 3744507137: a stray request adapter on a FRONTIER policy must not be reported --
    `result.adapter` claims which weights answered."""
    config = ProviderConfig(
        default=ModelPolicy(mode=ProviderMode.FRONTIER, model_id="gpt-5.5", provider_key="fake-frontier")
    )
    provider = ConfigDrivenModelProvider(config)

    result = provider.resolve_detailed(ModelResolveRequest(tenant_id="globex", adapter="stray-lora"))

    assert result.adapter is None
    assert "adapter" not in result.model.config


def test_a_string_mode_resolves_instead_of_crashing(fake_backends):
    """Finding 3744507142: a mode arriving as 'frontier' from JSON/settings must coerce, not
    AttributeError."""
    config = ProviderConfig(
        default=ModelPolicy(mode=ProviderMode.FRONTIER, model_id="gpt-5.5", provider_key="fake-frontier")
    )
    provider = ConfigDrivenModelProvider(config)

    result = provider.resolve_detailed(ModelResolveRequest(mode="frontier"))

    assert result.mode is ProviderMode.FRONTIER


def test_an_unknown_string_mode_is_a_config_error():
    """The coercion half of 3744507142: garbage still fails loudly, with the field named."""
    with pytest.raises(ModelProviderConfigError) as exc:
        ModelResolveRequest(mode="quantum")
    assert exc.value.field_name == "mode"


def test_pinned_provider_key_is_a_registry_key_not_a_display_name(fake_backends):
    """Finding 3744507145: `provider_key` must round-trip through discovery.resolve."""
    from egpt.registry import discovery

    provider = ConfigDrivenModelProvider(ProviderConfig())
    result = provider.resolve_detailed(ModelResolveRequest(requested=_FakeModel(id="m")))

    assert result.provider_key == "fake-local"  # first key registered for this class
    assert discovery.resolve("model", result.provider_key) is _FakeModel


def test_pinned_provider_key_resolves_a_real_in_tree_model():
    """Finding 3747619518: the two tests above both match on the *identity* branch, because
    `fake_backends` registers its class at runtime. Every model that actually ships takes the
    lazy-declaration branch instead, and the first fix compared declaration strings to
    `cls.__module__` -- which the registry never holds, since it declares the re-exporting
    package (`egpt.integrations.models.vllm`) rather than the defining module (`...vllm.vllm`).
    That returned None for 40 of 42 registrations.

    VLLM is the pin because it needs no SDK to import, so this runs everywhere.
    """
    from egpt.integrations.models.vllm.vllm import VLLM
    from egpt.registry import discovery

    # The premise: were these equal, the original string comparison would have worked and this
    # test could not distinguish the two implementations.
    declaration = discovery.entries("model")["vllm"]
    assert declaration.module != VLLM.__module__, "re-export shape changed; this pin is now vacuous"

    result = ConfigDrivenModelProvider(ProviderConfig()).resolve_detailed(
        ModelResolveRequest(requested=VLLM(id="qwen3-8b"))
    )

    assert result.provider_key == "vllm"
    assert discovery.resolve("model", result.provider_key) is VLLM


def test_pinned_provider_key_is_none_for_an_unregistered_model():
    """The honesty half of 3744507145: no fake key that merely looks resolvable."""
    provider = ConfigDrivenModelProvider(ProviderConfig())

    result = provider.resolve_detailed(ModelResolveRequest(requested=_NoKwargsLocalModel(id="m")))

    assert result.provider_key is None


def test_local_adapter_renders_as_model_id_for_a_backend_without_adapter_support(fake_backends):
    """Finding 3744507126: the documented adapter feature must be reachable on a backend whose
    constructor has no `adapter` param and no **kwargs -- vLLM serves LoRA as a model id."""
    from egpt.registry import discovery

    discovery.register("model", "fake-vllm-shaped", obj=_NoKwargsLocalModel)
    config = ProviderConfig(
        policies={
            ("acme",): ModelPolicy(
                mode=ProviderMode.LOCAL,
                model_id="qwen3-8b",
                provider_key="fake-vllm-shaped",
                adapter="support-v3",
                config={"adapter": "stale-lora"},  # must be stripped, not forwarded
            )
        }
    )
    provider = ConfigDrivenModelProvider(config)

    result = provider.resolve_detailed(ModelResolveRequest(tenant_id="acme"))

    assert result.model.id == "support-v3"
    assert result.adapter == "support-v3"


def test_default_mode_alone_builds_a_working_provider(monkeypatch):
    """Finding 3744507152: declaring EGPT_DEFAULT_MODE without a policy file must not be a
    startup crash -- pinned requests resolve; config-chosen ones fail per-request, clearly."""
    monkeypatch.setenv("EGPT_DEFAULT_MODE", "local")
    monkeypatch.delenv("EGPT_POLICY_FILE", raising=False)

    provider = ConfigDrivenModelProvider.from_settings()

    pinned = provider.resolve_detailed(ModelResolveRequest(requested=_FakeModel(id="in-code")))
    assert pinned.model.id == "in-code"
    assert pinned.mode is ProviderMode.LOCAL

    with pytest.raises(ModelProviderConfigError) as exc:
        provider.resolve_detailed(ModelResolveRequest())
    assert exc.value.field_name == "model_id"


def test_an_invalid_default_mode_names_the_field(monkeypatch):
    """The startup error for garbage EGPT_DEFAULT_MODE stays a named config error."""
    monkeypatch.setenv("EGPT_DEFAULT_MODE", "quantum")
    monkeypatch.delenv("EGPT_POLICY_FILE", raising=False)

    with pytest.raises(ModelProviderConfigError) as exc:
        ConfigDrivenModelProvider.from_settings()
    assert exc.value.field_name == "default_mode"
