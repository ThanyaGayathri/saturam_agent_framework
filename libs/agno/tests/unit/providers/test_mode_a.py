"""EGPT-3.3: Mode A binds resolution to a self-hosted small model inside the VPC.

The tests that matter here are the negative ones. Mode A's whole value is the guarantee that
a request never leaves the network, and the two ways that guarantee breaks are quiet:

* ``egpt/integrations/models/ollama/chat.py`` defaults ``host`` to ``https://ollama.com`` and
  ``vllm.py`` defaults ``base_url`` to ``http://localhost:8000/v1/``. A provider that lets
  either default through still *works* -- it just works against the wrong server, and in
  Ollama's case against a public one.
* ``OpenAILike`` had no declaration row, so a Mode A agent stored in the components table
  rebuilt as ``OpenAIResponses``: a cloud client wearing a self-hosted agent's config.

Both are asserted mechanically below rather than left to configuration review.

A file-local fake backend stands in for vLLM so adapter rendering can be checked against real
constructor state with no server and no SDK. It is registered into the discovery table, which
``conftest.py`` snapshots and restores around every test in this package.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

import pytest

os.environ.setdefault("OPENAI_API_KEY", "test-key-for-testing")

from egpt.models.base import Model
from egpt.providers.mode_a import LocalModelProvider
from egpt.providers.model_provider import (
    ModelProvider,
    ModelProviderConfigError,
    ModelResolveRequest,
    ProviderMode,
)
from egpt.registry import discovery
from egpt.settings.providers import ProviderSettings

VPC_URL = "http://vllm.internal:8000/v1"


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


@dataclass
class _StubModel(Model):
    """Concrete no-op Model. `Model` is an ABC with six abstract methods.

    They are stubbed rather than left abstract because an abstract fake cannot be
    instantiated at all -- so a test using one would fail on the TypeError instead of on the
    behaviour it meant to assert, or, worse, pass because the fake was never reached.
    """

    def invoke(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("fake backend: no request is ever sent")

    async def ainvoke(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("fake backend: no request is ever sent")

    def invoke_stream(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("fake backend: no request is ever sent")

    async def ainvoke_stream(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("fake backend: no request is ever sent")

    def _parse_provider_response(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("fake backend: no response is ever parsed")

    def _parse_provider_response_delta(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("fake backend: no response is ever parsed")


@dataclass
class _FakeOpenAISchemaBackend(_StubModel):
    """Stands in for VLLM/OpenAILike: same constructor surface, no SDK, no server.

    Only the attributes Mode A actually sets are modelled. Anything Mode A passes that this
    does not declare is a TypeError, which is the point -- it catches a provider that starts
    forwarding a kwarg the real backend would reject.
    """

    id: str = "fake"
    name: str = "FakeBackend"
    provider: str = "FakeBackend"
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    request_params: Optional[Dict[str, Any]] = None


@dataclass
class _FakeHostBackend(_StubModel):
    """Stands in for Ollama, which takes `host` rather than `base_url`."""

    id: str = "fake"
    name: str = "FakeHostBackend"
    provider: str = "FakeHostBackend"
    host: Optional[str] = None
    api_key: Optional[str] = None
    request_params: Optional[Dict[str, Any]] = None


@dataclass
class _DefaultingBackend(_StubModel):
    """A backend that fills in a public host when given none -- the hazard, reproduced.

    Mirrors what `Ollama._get_client_params` does with `https://ollama.com`. Used to prove the
    egress assertion catches a backend default rather than merely trusting configuration.
    """

    id: str = "fake"
    name: str = "DefaultingBackend"
    provider: str = "DefaultingBackend"
    host: Optional[str] = None
    api_key: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.host:
            self.host = "https://ollama.com"


def _register(key: str, cls: type) -> None:
    """Register a fake under `key`, replacing the real backend. Undone by the conftest snapshot.

    The `discovery.get` call is load-bearing, not defensive. `register` writes straight into
    the table, but the built-in declarations are bootstrapped lazily on first *read*, and that
    bootstrap calls `register_all()`, which would overwrite a fake registered before it ran.
    Touching the table through a read first forces the bootstrap, so the fake is written on top
    of the real row rather than under it.

    Without this, these tests silently exercise the real VLLM/Ollama classes -- which is what
    they did until the abstract fakes proved they were never being instantiated.
    """
    discovery.get("model", key)
    discovery.register("model", key, obj=cls, override=True)
    assert discovery.resolve("model", key) is cls, f"fake backend for '{key}' did not take effect"


@pytest.fixture
def fake_vllm():
    _register("vllm", _FakeOpenAISchemaBackend)
    return _FakeOpenAISchemaBackend


@pytest.fixture
def fake_ollama():
    _register("ollama", _FakeHostBackend)
    return _FakeHostBackend


def _settings(**overrides: Any) -> ProviderSettings:
    """Provider settings built from explicit values, ignoring the ambient environment.

    `ProviderSettings()` reads EGPT_*/AGNO_* names, so a developer machine with
    EGPT_MODE_A_BASE_URL exported would otherwise change what these tests assert.
    """
    base: Dict[str, Any] = {
        "mode_a_base_url": None,
        "mode_a_backend": "vllm",
        "mode_a_model_id": None,
        "mode_a_api_key": None,
        "mode_a_adapter_mode": "model-id",
        "mode_a_adapter_field": "lora_request",
        "mode_a_allowed_hosts": [],
        "mode_a_context_window": None,
    }
    base.update(overrides)
    return ProviderSettings(**base)


def _provider(**overrides: Any) -> LocalModelProvider:
    return LocalModelProvider(settings=_settings(**overrides))


# --------------------------------------------------------------------------- #
# Contract conformance
# --------------------------------------------------------------------------- #


def test_local_provider_satisfies_the_model_provider_protocol():
    """The seam's callers type against the Protocol, not against BaseModelProvider."""
    provider = _provider(mode_a_base_url=VPC_URL, mode_a_model_id="qwen")
    assert isinstance(provider, ModelProvider)
    assert provider.name == "mode-a"


def test_capabilities_imports_no_sdk_and_reports_local_only():
    caps = _provider(mode_a_base_url=VPC_URL, mode_a_model_id="qwen", mode_a_context_window=32768).capabilities()
    assert caps.modes == (ProviderMode.LOCAL,)
    assert caps.provider_keys == ("vllm",)
    assert caps.supports_adapters is True
    assert caps.supports_escalation is False
    assert caps.context_window == 32768


def test_ollama_backend_reports_no_adapter_support():
    """Ollama has no multi-LoRA concept, so the capability must not claim one."""
    caps = _provider(mode_a_base_url=VPC_URL, mode_a_model_id="qwen", mode_a_backend="ollama").capabilities()
    assert caps.supports_adapters is False


# --------------------------------------------------------------------------- #
# The egress hazard
# --------------------------------------------------------------------------- #


def test_unset_base_url_raises_naming_the_field(fake_vllm):
    """Unset is an error, never a fallback -- the core guarantee of this ticket."""
    provider = _provider(mode_a_model_id="qwen")
    with pytest.raises(ModelProviderConfigError) as excinfo:
        provider.resolve(ModelResolveRequest())
    assert excinfo.value.field_name == "mode_a_base_url"


def test_unset_base_url_does_not_fall_through_to_the_vllm_default(fake_vllm):
    """The specific regression: vLLM's own `http://localhost:8000/v1/` must never be reached.

    Asserted by proving nothing was constructed at all -- a provider that built a model and
    only then complained would already have bound the wrong endpoint.
    """
    constructed = []

    @dataclass
    class _Recording(_FakeOpenAISchemaBackend):
        def __post_init__(self) -> None:
            constructed.append(self)

    _register("vllm", _Recording)
    with pytest.raises(ModelProviderConfigError):
        _provider(mode_a_model_id="qwen").resolve(ModelResolveRequest())
    assert constructed == [], "Mode A constructed a model despite having no configured endpoint."


def test_mode_a_never_resolves_a_model_pointing_at_ollama_cloud(fake_ollama):
    """No Mode A resolution may yield a model whose endpoint is the Ollama public host."""
    model = _provider(
        mode_a_base_url=VPC_URL,
        mode_a_model_id="qwen",
        mode_a_backend="ollama",
    ).resolve(ModelResolveRequest())
    endpoint = str(getattr(model, "host", "") or getattr(model, "base_url", ""))
    assert "ollama.com" not in endpoint
    assert endpoint == VPC_URL


def test_a_backend_that_defaults_to_a_public_host_is_rejected():
    """Belt and braces: the endpoint is re-read off the constructed model, not merely configured.

    Reproduces `Ollama._get_client_params`'s fallback to `https://ollama.com` in a fake, and
    asserts Mode A refuses the resolution instead of returning a cloud-bound model.
    """

    @dataclass
    class _IgnoresConfiguredHost(_DefaultingBackend):
        """The worst case: the backend's public default wins over what Mode A passed."""

        def __post_init__(self) -> None:
            self.host = "https://ollama.com"

    _register("ollama", _IgnoresConfiguredHost)
    provider = _provider(mode_a_base_url=VPC_URL, mode_a_model_id="qwen", mode_a_backend="ollama")
    with pytest.raises(ModelProviderConfigError, match="not in-VPC"):
        provider.resolve(ModelResolveRequest())


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://vllm.internal:8000/v1",
        "http://localhost:8000/v1",
        "http://127.0.0.1:8000/v1",
        "http://10.4.2.9:8000/v1",
        "http://192.168.1.5:8000/v1",
        "http://172.16.0.3:8000/v1",
        "http://vllm:8000/v1",  # bare Kubernetes service name
        "http://vllm.default.svc.cluster.local:8000/v1",
        "http://ollama.comms.internal:11434",  # substring of "ollama.com", but internal
    ],
)
def test_in_vpc_endpoints_are_accepted(fake_vllm, endpoint):
    provider = _provider(mode_a_base_url=endpoint, mode_a_model_id="qwen")
    assert provider.capabilities().egress_free is True
    assert provider.resolve(ModelResolveRequest()).base_url == endpoint


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://ollama.com",
        "https://api.openai.com/v1",
        # The denylist failure this allowlist exists to close: a fat-fingered public endpoint
        # that is not one of the two obvious SaaS hostnames.
        "https://api.together.xyz/v1",
        "https://generativelanguage.googleapis.com",
        "https://api.groq.com/openai/v1",
        "http://8.8.8.8:8000/v1",
        # Matches "api.openai.com" as a substring but is a completely different host.
        "http://api.openai.com.evil.net/v1",
    ],
)
def test_public_endpoints_are_refused(fake_vllm, endpoint):
    """Egress is decided by an allowlist, so an unknown public host fails closed."""
    provider = _provider(mode_a_base_url=endpoint, mode_a_model_id="qwen")
    assert provider.capabilities().egress_free is False
    with pytest.raises(ModelProviderConfigError, match="not in-VPC"):
        provider.resolve(ModelResolveRequest())


def test_an_internal_domain_can_be_allowlisted(fake_vllm):
    """A deployment whose internal domain looks public names it rather than being locked out."""
    endpoint = "https://vllm.acme-corp.io/v1"
    assert _provider(mode_a_base_url=endpoint, mode_a_model_id="q").capabilities().egress_free is False

    provider = _provider(
        mode_a_base_url=endpoint,
        mode_a_model_id="qwen",
        mode_a_allowed_hosts=["acme-corp.io"],
    )
    assert provider.capabilities().egress_free is True
    assert provider.resolve(ModelResolveRequest()).base_url == endpoint


def test_allowlisting_a_domain_does_not_allow_a_lookalike(fake_vllm):
    """Suffix matching is on a dot boundary, so "acme-corp.io.evil.net" stays refused."""
    provider = _provider(
        mode_a_base_url="https://vllm.acme-corp.io.evil.net/v1",
        mode_a_model_id="qwen",
        mode_a_allowed_hosts=["acme-corp.io"],
    )
    with pytest.raises(ModelProviderConfigError, match="not in-VPC"):
        provider.resolve(ModelResolveRequest())


def test_configuring_a_public_endpoint_is_not_egress_free():
    """`egress_free` is a claim about the resolved endpoint, not a constant."""
    assert _provider(mode_a_base_url=VPC_URL, mode_a_model_id="q").capabilities().egress_free is True
    assert _provider(mode_a_model_id="q").capabilities().egress_free is False
    assert _provider(mode_a_base_url="https://ollama.com", mode_a_model_id="q").capabilities().egress_free is False


def test_base_url_comes_from_settings(fake_vllm):
    model = _provider(mode_a_base_url=VPC_URL, mode_a_model_id="qwen").resolve(ModelResolveRequest())
    assert model.base_url == VPC_URL
    assert model.id == "qwen"


# --------------------------------------------------------------------------- #
# Adapter rendering
# --------------------------------------------------------------------------- #


def test_adapter_renders_as_the_model_id_by_default(fake_vllm):
    """vLLM's multi-LoRA convention: the adapter is served as a first-class model id."""
    result = _provider(mode_a_base_url=VPC_URL, mode_a_model_id="qwen").resolve_detailed(
        ModelResolveRequest(adapter="legal-v1")
    )
    assert result.model.id == "legal-v1"
    assert result.model.request_params is None
    assert result.adapter == "legal-v1"
    assert result.model.base_url == VPC_URL


def test_adapter_renders_into_extra_body_when_configured(fake_vllm):
    """The alternative convention: base weights stay the model id, adapter rides in extra_body."""
    result = _provider(
        mode_a_base_url=VPC_URL,
        mode_a_model_id="qwen",
        mode_a_adapter_mode="extra-body",
    ).resolve_detailed(ModelResolveRequest(adapter="legal-v1"))
    assert result.model.id == "qwen"
    assert result.model.request_params == {"extra_body": {"lora_request": "legal-v1"}}
    assert result.adapter == "legal-v1"


def test_extra_body_adapter_field_is_configurable(fake_vllm):
    result = _provider(
        mode_a_base_url=VPC_URL,
        mode_a_model_id="qwen",
        mode_a_adapter_mode="extra-body",
        mode_a_adapter_field="adapter_name",
    ).resolve_detailed(ModelResolveRequest(adapter="legal-v1"))
    assert result.model.request_params == {"extra_body": {"adapter_name": "legal-v1"}}


def test_no_adapter_leaves_the_base_model_untouched(fake_vllm):
    result = _provider(mode_a_base_url=VPC_URL, mode_a_model_id="qwen").resolve_detailed(ModelResolveRequest())
    assert result.model.id == "qwen"
    assert result.model.request_params is None
    assert result.adapter is None


def test_adapter_is_ignored_rather_than_rejected_for_a_non_vllm_backend(fake_ollama):
    """An Ollama deployment has no adapter concept; a config shared across backends must not break."""
    result = _provider(
        mode_a_base_url=VPC_URL,
        mode_a_model_id="qwen",
        mode_a_backend="ollama",
    ).resolve_detailed(ModelResolveRequest(adapter="legal-v1"))
    assert result.model.id == "qwen"
    assert result.adapter is None
    assert result.model.request_params is None


def test_each_resolution_returns_a_fresh_model(fake_vllm):
    """The contract requires it: Model is mutable and adapters vary per request."""
    provider = _provider(mode_a_base_url=VPC_URL, mode_a_model_id="qwen")
    first = provider.resolve(ModelResolveRequest(adapter="legal-v1"))
    second = provider.resolve(ModelResolveRequest(adapter="finance-v2"))
    assert first is not second
    assert (first.id, second.id) == ("legal-v1", "finance-v2")


def test_missing_model_id_without_an_adapter_raises(fake_vllm):
    with pytest.raises(ModelProviderConfigError) as excinfo:
        _provider(mode_a_base_url=VPC_URL).resolve(ModelResolveRequest())
    assert excinfo.value.field_name == "mode_a_model_id"


def test_an_adapter_supplies_the_model_id_under_the_model_id_convention(fake_vllm):
    """With `adapter_mode="model-id"` the adapter *is* the id, so no separate id is required."""
    model = _provider(mode_a_base_url=VPC_URL).resolve(ModelResolveRequest(adapter="legal-v1"))
    assert model.id == "legal-v1"


# --------------------------------------------------------------------------- #
# Configuration validation
# --------------------------------------------------------------------------- #


def test_unknown_backend_is_rejected_at_construction():
    with pytest.raises(ModelProviderConfigError) as excinfo:
        _provider(mode_a_base_url=VPC_URL, mode_a_backend="not-a-backend")
    assert excinfo.value.field_name == "mode_a_backend"


def test_unknown_adapter_mode_is_rejected_at_construction():
    with pytest.raises(ModelProviderConfigError) as excinfo:
        _provider(mode_a_base_url=VPC_URL, mode_a_adapter_mode="telepathy")
    assert excinfo.value.field_name == "mode_a_adapter_mode"


def test_requesting_frontier_mode_is_refused(fake_vllm):
    """Silently serving the SLM to a caller who asked for a frontier model would read as a
    quality regression rather than the configuration error it is."""
    provider = _provider(mode_a_base_url=VPC_URL, mode_a_model_id="qwen")
    with pytest.raises(ModelProviderConfigError) as excinfo:
        provider.resolve(ModelResolveRequest(mode=ProviderMode.FRONTIER))
    assert excinfo.value.field_name == "mode"


def test_explicit_local_mode_is_served(fake_vllm):
    model = _provider(mode_a_base_url=VPC_URL, mode_a_model_id="qwen").resolve(
        ModelResolveRequest(mode=ProviderMode.LOCAL)
    )
    assert model.id == "qwen"


def test_constructor_arguments_override_settings(fake_vllm):
    """Wiring Mode A in code must not require going through the environment."""
    provider = LocalModelProvider(
        settings=_settings(mode_a_base_url="http://ignored:1/v1", mode_a_model_id="ignored"),
        base_url=VPC_URL,
        model_id="qwen",
    )
    model = provider.resolve(ModelResolveRequest())
    assert (model.base_url, model.id) == (VPC_URL, "qwen")


def test_default_adapter_applies_when_the_request_names_none(fake_vllm):
    provider = LocalModelProvider(
        settings=_settings(mode_a_base_url=VPC_URL, mode_a_model_id="qwen"),
        default_adapter="legal-v1",
    )
    assert provider.resolve(ModelResolveRequest()).id == "legal-v1"
    # An explicit request adapter still wins.
    assert provider.resolve(ModelResolveRequest(adapter="finance-v2")).id == "finance-v2"


def test_settings_read_the_branded_environment(monkeypatch):
    """The endpoint is configurable as EGPT_MODE_A_BASE_URL, like every other EGPT setting."""
    monkeypatch.setenv("EGPT_MODE_A_BASE_URL", VPC_URL)
    monkeypatch.setenv("EGPT_MODE_A_MODEL_ID", "qwen")
    provider = LocalModelProvider()
    assert provider.base_url == VPC_URL
    assert provider.model_id == "qwen"


def test_an_explicitly_requested_model_is_returned_unchanged(fake_vllm):
    """The frozen contract's MUST: user intent outranks configuration.

    `ModelResolveRequest.requested` is an explicit `Agent(model=...)`. Mode A has no policy
    that overrides a pinned model, so it must hand back the very same instance rather than
    substituting `mode_a_model_id`.
    """
    pinned = _FakeOpenAISchemaBackend(id="pinned-model", base_url="http://pinned.internal:9/v1")
    result = _provider(mode_a_base_url=VPC_URL, mode_a_model_id="qwen").resolve_detailed(
        ModelResolveRequest(requested=pinned)
    )
    assert result.model is pinned
    assert result.model.id == "pinned-model"


def test_a_pinned_model_is_honoured_even_with_no_endpoint_configured(fake_vllm):
    """Nothing is constructed, so the missing-endpoint error must not fire."""
    pinned = _FakeOpenAISchemaBackend(id="pinned-model", base_url="http://pinned.internal:9/v1")
    assert _provider().resolve(ModelResolveRequest(requested=pinned)) is pinned


def test_ollama_receives_the_configured_api_key(fake_ollama, monkeypatch):
    """Ollama defaults api_key to getenv("OLLAMA_API_KEY"); the configured value must win.

    Passing it explicitly is what stops an ambient environment variable outranking settings,
    and what stops an authenticated in-VPC Ollama being called with no credential at all.
    """
    monkeypatch.setenv("OLLAMA_API_KEY", "ambient-should-not-win")
    model = _provider(
        mode_a_base_url=VPC_URL,
        mode_a_model_id="qwen",
        mode_a_backend="ollama",
        mode_a_api_key="configured-key",
    ).resolve(ModelResolveRequest())
    assert model.api_key == "configured-key"


def test_api_key_placeholder_for_unauthenticated_in_vpc_servers(fake_vllm):
    """VLLM raises without a key, and an in-VPC server usually has none."""
    model = _provider(mode_a_base_url=VPC_URL, mode_a_model_id="qwen").resolve(ModelResolveRequest())
    assert model.api_key == "not-provided"

    configured = _provider(mode_a_base_url=VPC_URL, mode_a_model_id="qwen", mode_a_api_key="secret").resolve(
        ModelResolveRequest()
    )
    assert configured.api_key == "secret"


# --------------------------------------------------------------------------- #
# The openai-like round-trip regression (Part 1 of EGPT-3.3)
# --------------------------------------------------------------------------- #


def test_openai_like_round_trip_preserves_class():
    """Regression: a stored OpenAILike agent rebuilt as OpenAIResponses -- a cloud client.

    `OpenAILike` had no declaration row, so `resolve_provider_key("OpenAI", "OpenAILike")`
    fell back to the "openai" key. A Mode A agent persisted in the components table therefore
    came back as the hosted OpenAI class, and the in-VPC endpoint it was configured with was
    not part of what came back either. Silent, and exactly the failure Mode A exists to
    prevent.
    """
    pytest.importorskip("openai")
    from egpt.integrations.models.openai_like import OpenAILike
    from egpt.models.utils import get_model_from_dict

    original = OpenAILike(id="qwen", base_url=VPC_URL)
    rebuilt = get_model_from_dict(original.to_dict())

    assert type(rebuilt) is OpenAILike, (
        f"OpenAILike rebuilt as {type(rebuilt).__name__}. A self-hosted, in-VPC model must not "
        "round-trip into a cloud OpenAI client."
    )
    assert rebuilt.id == "qwen"


def test_to_dict_does_not_carry_base_url_so_mode_a_must_reconfigure_it():
    """Documents the remaining half of the round-trip gap, so it cannot regress unnoticed.

    `Model.to_dict` serializes only `{name, id, provider}`. Restoring the class (the test
    above) is therefore necessary but not sufficient: the endpoint is *not* part of what comes
    back, and `OpenAILike` with `base_url=None` falls back to the OpenAI SDK's public default.

    Widening `to_dict` is deliberately out of scope here -- it also feeds
    `agent/_telemetry.py`, so adding `base_url` would ship internal hostnames off-box. The
    supported path is that Mode A supplies the endpoint at resolve time from settings, which
    `test_base_url_comes_from_settings` covers. This test pins the boundary between the two.
    """
    pytest.importorskip("openai")
    from egpt.integrations.models.openai_like import OpenAILike
    from egpt.models.utils import get_model_from_dict

    serialized = OpenAILike(id="qwen", base_url=VPC_URL).to_dict()
    assert "base_url" not in serialized, (
        "to_dict now carries base_url. That may be a fix, but it also flows into telemetry -- "
        "check egpt/agent/_telemetry.py before accepting it."
    )
    assert get_model_from_dict(serialized).base_url is None


def test_openai_like_is_a_declared_provider():
    """The row must exist and point at the openai_like package.

    `egpt.integrations.models.openai` deliberately does not re-export OpenAILike (it subclasses
    OpenAIChat, so re-exporting cycles), so the row has to name the package that defines it.
    """
    registration = discovery.declarations("model").get("openai-like")
    assert registration is not None, "openai-like is missing from the model declarations."
    assert registration.module == "egpt.integrations.models.openai_like"
    assert registration.attr == "OpenAILike"


def test_declaring_openai_like_does_not_disturb_openai_resolution():
    """The new row shares the display provider "openai"; existing resolutions must not move."""
    # Public since EGPT-2.2, which needs it in the component-config validator.
    from egpt.models.utils import resolve_provider_key

    assert resolve_provider_key("OpenAI", "OpenAILike") == "openai-like"
    assert resolve_provider_key("OpenAI", None) == "openai"
    assert resolve_provider_key("OpenAI", "OpenAIResponses") == "openai"
    assert resolve_provider_key("OpenAI", "OpenAIChat") == "openai-chat"


def test_openai_like_row_stays_lazy():
    """A row carrying a preloaded object would mean the module was imported to declare it."""
    registration = discovery.declarations("model")["openai-like"]
    assert registration.obj is None


# --------------------------------------------------------------------------- #
# Review round 2 (PR #48): regressions for nandha-saturam's findings
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "host, label",
    [
        ("134744072", "integer form of 8.8.8.8"),
        ("0x08080808", "hex form of 8.8.8.8"),
        ("0x08.0x08.0x08.0x08", "dotted-hex form of 8.8.8.8"),
    ],
)
def test_a_numeric_public_address_is_not_mistaken_for_a_service_name(host, label):
    """`ip_address` parses only the dotted-quad spelling, but getaddrinfo -- and so every HTTP
    client -- accepts the integer and hex forms too. Those reached the "no dot means a
    Kubernetes service name" shortcut and were declared in-VPC, so prompts left the network
    under a positive `egress_free=True` assurance. The allowlist comment argues that a false
    positive is worse than no check at all; this is that case."""
    provider = _provider(mode_a_base_url=VPC_URL, mode_a_model_id="qwen")

    assert provider._endpoint_is_in_vpc(f"http://{host}:8000/v1") is False, (
        f"the {label} resolves to a public address and must not count as in-VPC"
    )


@pytest.mark.parametrize(
    "host",
    ["2130706433", "0x7f000001", "167772161"],
)
def test_numeric_private_and_loopback_addresses_are_still_in_vpc(host):
    """The numeric-form fix must not refuse the private/loopback addresses it normalizes:
    127.0.0.1 and 10.0.0.1 spelled as integers are still inside the network."""
    provider = _provider(mode_a_base_url=VPC_URL, mode_a_model_id="qwen")

    assert provider._endpoint_is_in_vpc(f"http://{host}:8000/v1") is True


def test_a_single_allowed_host_string_is_not_iterated_per_character():
    """`str` satisfies `Iterable[str]`, so `allowed_hosts="llm.acme.com"` type-checked and
    iterated *characters* -- turning the allowlist into ('l','l','m',...) and refusing the very
    endpoint the operator had just allowed, with remediation text pointing at the setting they
    had already set."""
    provider = LocalModelProvider(
        settings=_settings(mode_a_base_url="https://llm.acme.com/v1", mode_a_model_id="qwen"),
        allowed_hosts="llm.acme.com",
    )

    assert provider.allowed_hosts == ("llm.acme.com",)
    assert provider._endpoint_is_in_vpc("https://llm.acme.com/v1") is True


def test_settings_strings_are_stripped_at_assignment():
    """A trailing space from a .env line or a compose interpolation made the backend fail
    construction with a message quoting an invisible space, and reached the server as a
    404-ing model id. mode_b.py strips for the same reason."""
    provider = LocalModelProvider(
        settings=_settings(),
        backend=" vllm ",
        base_url=f" {VPC_URL} ",
        model_id=" qwen ",
    )

    assert (provider.backend, provider.base_url, provider.model_id) == ("vllm", VPC_URL, "qwen")


def test_an_out_of_tree_backend_registered_through_discovery_is_accepted():
    """The backend was validated against the hardcoded LOCAL_BACKENDS tuple, so a deployment
    registering its own OpenAI-schema server through the `egpt.models` entry point -- the
    discovery seam's whole purpose -- was refused at construction even though
    `discovery.construct` would have built it. mode_b.py checks the registry for this reason."""
    discovery.register("model", "my-inference-server", obj=_FakeOpenAISchemaBackend, override=True)
    try:
        provider = LocalModelProvider(
            settings=_settings(mode_a_base_url=VPC_URL, mode_a_model_id="qwen"),
            backend="my-inference-server",
        )
        assert provider.backend == "my-inference-server"
    finally:
        discovery._reset_for_tests()


def test_a_string_mode_equal_to_local_is_served_not_crashed():
    """ProviderMode is a str mixin so a config value ('local') validates by construction. An
    `is not` identity test sent that equal-but-not-identical string into the raise branch, which
    then formatted `.value` on a plain str -- an AttributeError naming neither field nor
    provider."""
    provider = _provider(mode_a_base_url=VPC_URL, mode_a_model_id="qwen")
    request = ModelResolveRequest()
    object.__setattr__(request, "mode", "local")

    result = provider.resolve_detailed(request)

    assert result.mode is ProviderMode.LOCAL


def test_a_rejected_string_mode_reports_the_value_without_crashing():
    """The other half: a non-LOCAL *string* must still produce a ModelProviderConfigError naming
    the field, not an AttributeError from `.value` on a str."""
    provider = _provider(mode_a_base_url=VPC_URL, mode_a_model_id="qwen")
    request = ModelResolveRequest()
    object.__setattr__(request, "mode", "frontier")

    with pytest.raises(ModelProviderConfigError) as exc:
        provider.resolve_detailed(request)

    assert exc.value.field_name == "mode"
    assert "frontier" in str(exc.value)


def test_a_pinned_model_outside_the_vpc_is_not_reported_as_local():
    """A pinned `Agent(model=Claude(...))` in a Mode A deployment was stamped `mode=LOCAL` and
    `provider_key=<backend>`, so an operator auditing "did anything leave the VPC?" from these
    records saw zero egress while every such request went to a public frontier API."""
    provider = _provider(mode_a_base_url=VPC_URL, mode_a_model_id="qwen")
    pinned = _FakeOpenAISchemaBackend(id="claude-sonnet-4-5")
    pinned.base_url = "https://api.anthropic.com/v1"

    result = provider.resolve_detailed(ModelResolveRequest(requested=pinned))

    assert result.model is pinned
    assert result.mode is ProviderMode.FRONTIER, "an out-of-VPC pinned model is not a local resolution"
    assert "not in-VPC" in (result.reason or "")


def test_a_pinned_in_vpc_model_is_still_reported_as_local():
    """The counterpart: pinning the deployment's own in-VPC model is a genuine LOCAL
    resolution, so the honesty fix must not relabel it as egress."""
    provider = _provider(mode_a_base_url=VPC_URL, mode_a_model_id="qwen")
    pinned = _FakeOpenAISchemaBackend(id="qwen")
    pinned.base_url = VPC_URL

    result = provider.resolve_detailed(ModelResolveRequest(requested=pinned))

    assert result.mode is ProviderMode.LOCAL


async def test_aresolve_detailed_runs_off_the_event_loop():
    """`BaseModelProvider.aresolve` delegates to the sync path and scopes that to "reads local
    config only". Mode A triggers a first-time vendor SDK import through `discovery.construct`,
    so without an override the first resolution per worker stalls the loop and everything queued
    behind it. mode_b.py overrides both for the same reason."""
    import asyncio
    import threading

    provider = _provider(mode_a_base_url=VPC_URL, mode_a_model_id="qwen")
    loop_thread = threading.get_ident()
    ran_on: Dict[str, int] = {}
    inner = provider.resolve_detailed

    def spy(request: ModelResolveRequest) -> Any:
        ran_on["tid"] = threading.get_ident()
        return inner(request)

    provider.resolve_detailed = spy  # type: ignore[method-assign]
    await provider.aresolve_detailed(ModelResolveRequest())

    assert ran_on["tid"] != loop_thread, "resolution ran on the event-loop thread"
    assert asyncio.iscoroutinefunction(LocalModelProvider.aresolve)


def test_a_restored_openai_schema_proxy_drops_the_placeholder_credential():
    """`OpenAILike.api_key` defaults to the truthy "not-provided", which suppresses
    `OpenAIChat`'s own OPENAI_API_KEY fallback. A persisted config carries no endpoint or
    credential (`Model.to_dict` stores only id/name/provider -- deliberately, since it also
    feeds telemetry), so the rebuilt client aimed at the OpenAI SaaS default holding a
    placeholder key and failed its first run with 401. None restores the env fallback."""
    pytest.importorskip("openai")
    from egpt.models.utils import get_model_from_dict

    rebuilt = get_model_from_dict({"provider": "OpenAI", "name": "OpenAILike", "id": "qwen"})

    assert rebuilt is not None
    assert rebuilt.api_key is None, "the placeholder must not survive a restore and mask the env fallback"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("llm.acme.com", ["llm.acme.com"]),
        ("a.acme.com,b.acme.com", ["a.acme.com", "b.acme.com"]),
        (" spaced.com , x.com ", ["spaced.com", "x.com"]),
        ('["json.com","two.com"]', ["json.com", "two.com"]),
        ("", []),
    ],
)
def test_allowed_hosts_accepts_the_form_the_error_message_recommends(monkeypatch, raw, expected):
    """`_assert_no_egress` tells the operator to "add its domain to EGPT_MODE_A_ALLOWED_HOSTS".
    With the field typed `List[str]`, pydantic-settings JSON-decoded it in the settings *source*
    -- before any validator -- so the bare domain raised SettingsError and took down every read
    of ProviderSettings in the process, not just this field: `default_mode` and all of Mode B
    became unreadable too. The documented instruction now works, and a JSON array still does."""
    from egpt.settings.providers import get_provider_settings

    monkeypatch.setenv("EGPT_MODE_A_ALLOWED_HOSTS", raw)

    settings = get_provider_settings()

    assert settings.mode_a_allowed_hosts_list == expected
    # The whole model must stay readable -- the original failure was process-wide.
    assert settings.default_mode is None


def test_allowed_hosts_still_accepts_a_real_list_programmatically():
    """The env-string support must not cost the in-code spelling a caller already uses."""
    settings = _settings(mode_a_allowed_hosts=["acme-corp.io", "other.io"])

    assert settings.mode_a_allowed_hosts_list == ["acme-corp.io", "other.io"]


def test_the_backends_own_credential_is_used_before_the_placeholder(monkeypatch):
    """VLLM reads `VLLM_API_KEY` only when its `api_key` field is falsy, and Mode A always
    passed the truthy "not-provided" placeholder -- so a token-protected in-VPC server
    configured the documented way received `Authorization: Bearer not-provided` and 401'd every
    run, with no config error because Mode A considered itself fully configured."""
    monkeypatch.setenv("VLLM_API_KEY", "secret-token")

    provider = LocalModelProvider(backend="vllm", base_url=VPC_URL, model_id="qwen")
    result = provider.resolve_detailed(ModelResolveRequest())

    assert result.model.api_key == "secret-token"


def test_the_mode_a_credential_outranks_the_backends_own(monkeypatch):
    """`EGPT_MODE_A_API_KEY` is the Mode A-specific statement, so it wins over the vendor name."""
    monkeypatch.setenv("VLLM_API_KEY", "vendor-token")

    provider = LocalModelProvider(
        settings=_settings(mode_a_api_key="mode-a-token"),
        backend="vllm",
        base_url=VPC_URL,
        model_id="qwen",
    )
    result = provider.resolve_detailed(ModelResolveRequest())

    assert result.model.api_key == "mode-a-token"


def test_the_placeholder_still_applies_when_nothing_is_configured(monkeypatch):
    """An unauthenticated in-VPC server is the common case, and VLLM raises without any key --
    so the placeholder must remain the last resort rather than being dropped."""
    monkeypatch.delenv("VLLM_API_KEY", raising=False)

    provider = LocalModelProvider(settings=_settings(), backend="vllm", base_url=VPC_URL, model_id="qwen")
    result = provider.resolve_detailed(ModelResolveRequest())

    assert result.model.api_key == "not-provided"
