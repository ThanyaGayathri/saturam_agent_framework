"""Mode B: hosted frontier providers, optionally behind a gateway (EGPT-3.4).

The suite is deliberately SDK-free where it can be. ``FrontierModelProvider`` resolves
backends by *registry key* through ``discovery.construct``, so most assertions are about the
key that would be constructed -- a string, whose production imports nothing. Where a real class
must be built (Portkey), the test asks for it and gates on ``importorskip``.

The package ``conftest.py`` snapshots and restores the discovery table around every test here,
which is what makes registering the fake model below safe.

There is no LiteLLM test in this file. Its guard is covered once, in ``test_gateways.py``; the
path itself is inert by decision (``libs/agno/pyproject.toml`` ~line 249, the March 2026 PyPI
compromise) and must not be exercised.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import pytest

from egpt.models.base import Model
from egpt.providers.gateways import GATEWAY_PORTKEY
from egpt.providers.mode_b import FRONTIER_PROVIDER_KEYS, FrontierModelProvider
from egpt.providers.model_provider import (
    DetailedModelProvider,
    ModelProvider,
    ModelProviderConfigError,
    ModelResolveRequest,
    ProviderMode,
)
from egpt.registry import discovery


@dataclass
class _FakeModel(Model):
    """Stands in for a frontier backend. Records what it was constructed with.

    A real ``Model`` subclass rather than a mock, so ``ModelResolveResult.model`` is genuinely
    the contract's type and the ``isinstance`` checks below mean something.
    """

    id: str = "fake"
    name: str = "FakeFrontier"
    provider: str = "fake"
    base_url: Optional[str] = None
    portkey_api_key: Optional[str] = None
    virtual_key: Optional[str] = None
    temperature: Optional[float] = None

    # ``Model`` is an ABC whose six request methods are abstract. Mode B only ever constructs
    # a model -- it never calls one -- so these exist to make the class instantiable and are
    # deliberately unreachable.
    def invoke(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def ainvoke(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def invoke_stream(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def ainvoke_stream(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _parse_provider_response(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _parse_provider_response_delta(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError


def _register_fake(key: str) -> None:
    """Register ``_FakeModel`` under a model key for the duration of one test.

    ``discovery.names`` first, and not decoratively: ``register`` writes straight into the
    table without marking the kind ready, so a later first read would run the builtin
    bootstrap and overwrite the fake with the real row. Forcing the bootstrap now means the
    fake is registered on top of it. The package ``conftest.py`` rolls the table back after.
    """
    discovery.names("model")
    discovery.register("model", key, obj=_FakeModel, override=True)


class _StubSettings:
    """A settings snapshot with nothing configured.

    Injected so the tests never depend on the ambient environment: a developer with
    ``EGPT_MODE_B_GATEWAY`` exported must not change what these assert.
    """

    mode_b_provider: Optional[str] = None
    mode_b_model: Optional[str] = None
    mode_b_gateway: Optional[str] = None
    mode_b_gateway_api_key: Optional[str] = None
    mode_b_gateway_base_url: Optional[str] = None
    mode_b_gateway_virtual_key: Optional[str] = None


def _provider(**kwargs: Any) -> FrontierModelProvider:
    kwargs.setdefault("settings", _StubSettings())
    return FrontierModelProvider(**kwargs)


# --------------------------------------------------------------------------- #
# Contract conformance
# --------------------------------------------------------------------------- #


def test_satisfies_the_model_provider_protocol():
    provider = _provider()
    assert isinstance(provider, ModelProvider)
    assert isinstance(provider, DetailedModelProvider)
    assert provider.name == "mode-b"


def test_capabilities_are_pure_and_report_frontier_mode():
    caps = _provider(provider_key="anthropic", model_id="claude-sonnet-4-5").capabilities()

    assert caps.modes == (ProviderMode.FRONTIER,)
    assert caps.provider_keys == FRONTIER_PROVIDER_KEYS
    assert caps.model_ids == ("claude-sonnet-4-5",)
    assert caps.supports_adapters is False  # LoRA is a Mode A concept
    assert caps.supports_escalation is True
    assert caps.egress_free is False  # a hosted frontier is egress by definition
    assert caps.gateway is None


def test_capabilities_report_the_configured_gateway_without_probing_it():
    """``capabilities()`` must not assert the gateway is usable -- it is called where a missing
    optional dependency has to stay a non-event. It reports what is configured, nothing more."""
    caps = _provider(gateway=GATEWAY_PORTKEY, gateway_api_key="pk-test").capabilities()
    assert caps.gateway == GATEWAY_PORTKEY


# --------------------------------------------------------------------------- #
# Gateway-off vs gateway-on
# --------------------------------------------------------------------------- #


def test_gateway_off_resolves_directly_to_the_frontier():
    _register_fake("anthropic")
    result = _provider(provider_key="anthropic", model_id="claude-sonnet-4-5").resolve_detailed(ModelResolveRequest())

    assert result.provider_key == "anthropic"
    assert result.gateway is None
    assert result.mode is ProviderMode.FRONTIER
    assert isinstance(result.model, _FakeModel)
    assert result.model.id == "claude-sonnet-4-5"


def test_gateway_on_resolves_through_the_gateway_key_instead():
    """The same configured frontier yields a different ``provider_key`` and a set ``gateway``
    once routing is turned on -- that difference is the whole observable effect of a gateway."""
    _register_fake("anthropic")
    _register_fake("portkey")

    direct = _provider(provider_key="anthropic", model_id="claude-sonnet-4-5").resolve_detailed(ModelResolveRequest())
    routed = _provider(
        provider_key="anthropic",
        model_id="claude-sonnet-4-5",
        gateway=GATEWAY_PORTKEY,
        gateway_api_key="pk-test",
        gateway_virtual_key="vk-anthropic",
    ).resolve_detailed(ModelResolveRequest())

    assert (direct.provider_key, direct.gateway) == ("anthropic", None)
    assert (routed.provider_key, routed.gateway) == ("portkey", GATEWAY_PORTKEY)
    # A gateway is a transport, not a model choice: the frontier model id survives routing.
    assert routed.model.id == direct.model.id == "claude-sonnet-4-5"


def test_gateway_config_reaches_the_constructed_model():
    _register_fake("portkey")
    result = _provider(
        provider_key="anthropic",
        model_id="claude-sonnet-4-5",
        gateway=GATEWAY_PORTKEY,
        gateway_api_key="pk-test",
        gateway_base_url="http://127.0.0.1:9/v1",
        gateway_virtual_key="vk-anthropic",
    ).resolve_detailed(ModelResolveRequest())

    model = result.model
    assert isinstance(model, _FakeModel)
    assert model.portkey_api_key == "pk-test"
    assert model.virtual_key == "vk-anthropic"
    assert model.base_url == "http://127.0.0.1:9/v1"


def test_resolve_returns_a_bare_model():
    _register_fake("openai")
    model = _provider(provider_key="openai", model_id="gpt-5.5").resolve(ModelResolveRequest())
    assert isinstance(model, Model)
    assert model.id == "gpt-5.5"


def test_each_resolve_builds_a_fresh_model():
    """``Model`` is a mutable dataclass that orchestration assigns to, so two concurrent
    requests must never receive the same instance."""
    _register_fake("openai")
    provider = _provider(provider_key="openai", model_id="gpt-5.5")
    assert provider.resolve(ModelResolveRequest()) is not provider.resolve(ModelResolveRequest())


def test_model_config_is_forwarded_and_wins_over_gateway_config():
    _register_fake("portkey")
    result = _provider(
        model_id="gpt-5.5",
        gateway=GATEWAY_PORTKEY,
        gateway_api_key="pk-test",
        model_config={"temperature": 0.1, "base_url": "http://127.0.0.1:1/v1"},
    ).resolve_detailed(ModelResolveRequest())

    model = result.model
    assert isinstance(model, _FakeModel)
    assert model.temperature == 0.1
    assert model.base_url == "http://127.0.0.1:1/v1"


# --------------------------------------------------------------------------- #
# Request handling
# --------------------------------------------------------------------------- #


def test_an_explicitly_requested_model_is_returned_unchanged():
    """The contract makes user intent outrank configuration, and Mode B has no policy that
    overrides it."""
    pinned = _FakeModel(id="pinned")
    result = _provider(provider_key="openai", gateway=GATEWAY_PORTKEY, gateway_api_key="pk").resolve_detailed(
        ModelResolveRequest(requested=pinned)
    )

    assert result.model is pinned
    assert result.mode is ProviderMode.FRONTIER


def test_a_local_mode_request_is_rejected():
    with pytest.raises(ModelProviderConfigError) as excinfo:
        _provider().resolve_detailed(ModelResolveRequest(mode=ProviderMode.LOCAL))
    assert excinfo.value.field_name == "mode"


def test_an_explicit_frontier_mode_request_is_served():
    _register_fake("openai")
    result = _provider(provider_key="openai").resolve_detailed(ModelResolveRequest(mode=ProviderMode.FRONTIER))
    assert result.mode is ProviderMode.FRONTIER


def test_the_request_is_not_mutated():
    _register_fake("openai")
    request = ModelResolveRequest(role="parser", tool_count=3)
    _provider(provider_key="openai").resolve_detailed(request)
    assert (request.role, request.tool_count, request.requested) == ("parser", 3, None)


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


def test_settings_supply_defaults_and_constructor_args_win():
    class _Configured(_StubSettings):
        mode_b_provider = "google"
        mode_b_model = "gemini-3-pro"
        mode_b_gateway_api_key = "pk-from-settings"

    from_settings = FrontierModelProvider(settings=_Configured())
    assert (from_settings.provider_key, from_settings.model_id) == ("google", "gemini-3-pro")

    overridden = FrontierModelProvider(settings=_Configured(), provider_key="anthropic")
    assert overridden.provider_key == "anthropic"


def test_an_unconfigured_provider_still_resolves():
    """A bare Mode B provider must be usable, not an error: EGPT-3.1's seam is a no-op until
    something is configured, and the defaults keep that true one layer down."""
    provider = _provider()
    assert provider.provider_key == "openai"
    assert provider.gateway is None


def test_an_unknown_gateway_fails_at_construction():
    """A gateway typo is a deployment mistake; surfacing it at startup beats surfacing it
    under load."""
    with pytest.raises(ModelProviderConfigError) as excinfo:
        _provider(gateway="not-a-gateway")
    assert excinfo.value.field_name == "mode_b_gateway"


def test_a_gateway_missing_its_key_fails_at_resolve_with_a_config_error():
    """Not an ImportError and not a bare ModelAuthenticationError from inside Portkey's client
    construction: the deployment gets told which setting to set."""
    _register_fake("portkey")
    provider = _provider(gateway=GATEWAY_PORTKEY)
    with pytest.raises(ModelProviderConfigError) as excinfo:
        provider.resolve_detailed(ModelResolveRequest())
    assert excinfo.value.field_name == "mode_b_gateway_api_key"


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_gateway_means_no_gateway(blank: str):
    """The branded-env source preserves empty-but-set values, so a compose file passing an
    unset host var through (``EGPT_MODE_B_GATEWAY=${GATEWAY:-}``) arrives here blank. That must
    read as "route direct", not as a gateway named '' -- which would be a startup crash."""

    class _Blank(_StubSettings):
        mode_b_gateway = blank

    provider = FrontierModelProvider(settings=_Blank())
    assert provider.gateway is None
    assert provider.capabilities().gateway is None

    _register_fake("openai")
    assert provider.resolve_detailed(ModelResolveRequest()).gateway is None


@pytest.mark.parametrize("bad", ["", "   "])
def test_blank_routing_config_is_rejected(bad: str):
    with pytest.raises(ModelProviderConfigError):
        _provider(provider_key=bad)
    with pytest.raises(ModelProviderConfigError):
        _provider(model_id=bad)


def test_no_frontier_credential_is_read_from_settings():
    """Frontier credentials are owned by each vendor SDK's own env-var convention. Mode B must
    neither declare fields for them nor read them -- a missing one surfaces as the SDK's own
    error, which already names the variable to set."""
    from egpt.settings.providers import ProviderSettings

    banned = ("api_key", "secret", "token", "credential")
    offenders = [
        field
        for field in ProviderSettings.model_fields
        if field.startswith("mode_b_") and not field.startswith("mode_b_gateway") and any(b in field for b in banned)
    ]
    assert not offenders, f"Mode B must own no frontier credential fields; found: {offenders}"


# --------------------------------------------------------------------------- #
# Real SDK construction
# --------------------------------------------------------------------------- #


def test_portkey_constructs_for_real_and_carries_gateway_settings():
    """The only test here that builds a real class. Everything above asserts on the resolved
    registry key instead, which imports nothing and so always runs.

    Construction only -- no client is created and no request is made, so no network and no
    real key are involved.
    """
    pytest.importorskip("portkey_ai")

    result = _provider(
        provider_key="anthropic",
        model_id="claude-sonnet-4-5",
        gateway=GATEWAY_PORTKEY,
        gateway_api_key="pk-test",
        gateway_base_url="http://127.0.0.1:9/v1",
        gateway_virtual_key="vk-anthropic",
    ).resolve_detailed(ModelResolveRequest())

    assert type(result.model).__name__ == "Portkey"
    assert result.model.id == "claude-sonnet-4-5"
    assert result.model.portkey_api_key == "pk-test"  # type: ignore[attr-defined]
    assert result.model.virtual_key == "vk-anthropic"  # type: ignore[attr-defined]
    assert result.model.base_url == "http://127.0.0.1:9/v1"  # type: ignore[attr-defined]


def test_frontier_keys_are_all_registered():
    """Every key ``capabilities()`` advertises must actually be resolvable. Reads the discovery
    table's names, which imports no provider module."""
    registered = set(discovery.names("model"))
    assert set(FRONTIER_PROVIDER_KEYS) <= registered
