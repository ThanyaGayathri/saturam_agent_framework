"""Mode A: bind resolution to a self-hosted small model inside the VPC (EGPT-3.3).

Mode A is the half of Epic 3 where nothing leaves the network. The provider builds an
OpenAI-schema client pointed at an in-VPC endpoint (vLLM, Ollama, or any other
OpenAI-compatible server) and optionally selects a LoRA adapter for a domain.

Two properties are load-bearing, and both are about what this module refuses to do:

* **No egress by default.** ``egpt/integrations/models/ollama/chat.py`` falls back to
  ``https://ollama.com`` when ``host`` is unset, and ``vllm.py`` to
  ``http://localhost:8000/v1/`` when ``base_url`` is. Either default reached from a Mode A
  resolution means the request left the VPC (or hit the wrong box) while the deployment
  believed it was self-hosted. So ``mode_a_base_url`` has no default at all: unset is a
  ``ModelProviderConfigError``, not a fallback, and the configured endpoint is always passed
  explicitly so the SDK's own default is unreachable.

* **No import of a backend.** ``providers/`` sits in the same cell of ADR 0001's matrix as
  ``registry/``: it may name an integration, never import one. Every backend is built by
  registry key through ``discovery.construct``, imported inside the method that calls it, so
  ``import egpt.providers.mode_a`` costs no SDK.

Environment is read only through ``egpt.settings.providers`` -- ``tests/unit/test_env_read_guard.py``
is a ratchet, and a raw ``getenv`` here would fail it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Iterable, Optional, Tuple

from egpt.providers.model_provider import (
    BaseModelProvider,
    ModelCapabilities,
    ModelProviderConfigError,
    ModelResolveRequest,
    ModelResolveResult,
    ProviderMode,
)

if TYPE_CHECKING:
    from egpt.models.base import Model
    from egpt.settings.providers import ProviderSettings

__all__ = ["ADAPTER_MODES", "LOCAL_BACKENDS", "LocalModelProvider"]

# Registry keys Mode A will build. Naming a key imports nothing -- these are strings, and the
# class behind each is resolved lazily by `discovery.construct`.
LOCAL_BACKENDS: Tuple[str, ...] = ("vllm", "ollama", "openai-like")

# How a LoRA adapter is rendered onto the outbound request.
#
# "model-id" is vLLM's multi-LoRA convention: `vllm serve --lora-modules name=/path` serves
# each adapter as a first-class model id, so selecting one is just asking for that id. It is
# the default because it is what a stock deployment expects and it needs no server support
# beyond the OpenAI schema itself.
#
# "extra-body" instead threads the adapter through the OpenAI client's `extra_body`
# passthrough, for servers that take it as a side-channel parameter and keep the model id
# fixed at the base weights.
ADAPTER_MODES: Tuple[str, ...] = ("model-id", "extra-body")

# Which endpoints count as in-VPC.
#
# An allowlist, not a denylist. A denylist of known SaaS hostnames ("ollama.com",
# "api.openai.com") would pass every *other* public endpoint -- a fat-fingered
# `https://api.together.xyz/v1` would resolve cleanly and still report `egress_free=True`,
# which is a worse failure than no check at all because it carries a positive assurance.
#
# So an endpoint is in-VPC when its host is loopback, a private/link-local IP, a name with no
# dot (a bare Kubernetes service or container name), or a name under one of the internal
# suffixes below. Anything else is treated as egress and refused.
_PRIVATE_SUFFIXES: Tuple[str, ...] = (
    ".internal",
    ".local",
    ".localdomain",
    ".cluster.local",
    ".svc",
    ".svc.cluster.local",
    ".intranet",
    ".lan",
    ".corp",
    ".home.arpa",
)

# Backends whose server-side convention is multi-LoRA. Adapter selection is ignored (not an
# error) for anything else: an Ollama deployment has no adapter concept, and failing a request
# that merely *mentioned* one would make `adapter` unusable in a config shared across backends.
_ADAPTER_BACKENDS: Tuple[str, ...] = ("vllm", "openai-like")


class LocalModelProvider(BaseModelProvider):
    """Resolve every request to a self-hosted model at a configured in-VPC endpoint.

    Settings are read once at construction, not per resolve: a provider is installed for the
    process, and re-reading the environment per request would let a mid-flight change split one
    deployment's traffic across two endpoints. Tests build a fresh provider instead.

    Constructor arguments override the corresponding settings field, so a caller wiring Mode A
    in code need not go through the environment.
    """

    name: str = "mode-a"

    def __init__(
        self,
        *,
        settings: Optional["ProviderSettings"] = None,
        base_url: Optional[str] = None,
        backend: Optional[str] = None,
        model_id: Optional[str] = None,
        api_key: Optional[str] = None,
        adapter_mode: Optional[str] = None,
        adapter_field: Optional[str] = None,
        context_window: Optional[int] = None,
        default_adapter: Optional[str] = None,
        allowed_hosts: Optional[Iterable[str]] = None,
    ) -> None:
        if settings is None:
            from egpt.settings.providers import get_provider_settings

            settings = get_provider_settings()

        self.base_url = base_url if base_url is not None else settings.mode_a_base_url
        self.backend = backend if backend is not None else settings.mode_a_backend
        self.model_id = model_id if model_id is not None else settings.mode_a_model_id
        self.api_key = api_key if api_key is not None else settings.mode_a_api_key
        self.adapter_mode = adapter_mode if adapter_mode is not None else settings.mode_a_adapter_mode
        self.adapter_field = adapter_field if adapter_field is not None else settings.mode_a_adapter_field
        self.context_window = context_window if context_window is not None else settings.mode_a_context_window
        raw_hosts = allowed_hosts if allowed_hosts is not None else settings.mode_a_allowed_hosts
        self.allowed_hosts: Tuple[str, ...] = tuple(host.strip().lower().lstrip(".") for host in raw_hosts if host)
        # An adapter applied when the request names none. Lets a deployment pin one domain
        # adapter without every call site passing it.
        self.default_adapter = default_adapter

        # Validated at construction, not at first resolve: a misconfigured backend or adapter
        # mode is a deployment mistake, and surfacing it when the process wires up its provider
        # is far better than surfacing it on a user's first request.
        if self.backend not in LOCAL_BACKENDS:
            raise ModelProviderConfigError(
                f"Unknown Mode A backend '{self.backend}'. Supported: {', '.join(LOCAL_BACKENDS)}.",
                field_name="mode_a_backend",
            )
        if self.adapter_mode not in ADAPTER_MODES:
            raise ModelProviderConfigError(
                f"Unknown Mode A adapter mode '{self.adapter_mode}'. Supported: {', '.join(ADAPTER_MODES)}.",
                field_name="mode_a_adapter_mode",
            )

    # -- resolution --------------------------------------------------------

    def resolve_detailed(self, request: ModelResolveRequest) -> ModelResolveResult:
        """Build the in-VPC model for this request.

        Raises:
            ModelProviderConfigError: no endpoint is configured, no model id is available, or
                the caller asked for a mode this provider does not serve.
            ImportError: the backend's optional SDK is not installed. Propagated unwrapped,
                matching ``discovery.resolve``.
        """
        # An explicit caller mode is authoritative, and Mode A serves only LOCAL. Refusing
        # rather than quietly serving a local model is the point: a caller that asked for
        # FRONTIER and silently got the SLM would look like a quality regression, not a
        # configuration error.
        if request.mode is not None and request.mode is not ProviderMode.LOCAL:
            raise ModelProviderConfigError(
                f"{type(self).__name__} serves {ProviderMode.LOCAL.value!r} only, "
                f"but the request asked for {request.mode.value!r}.",
                field_name="mode",
            )

        # User intent outranks configuration -- an explicit `Agent(model=...)` is returned
        # unchanged, as the frozen EGPT-3.1 contract requires. Mode A has no policy that
        # overrides a pinned model: someone who named a model meant that model.
        if request.requested is not None:
            return ModelResolveResult(
                model=request.requested,
                mode=ProviderMode.LOCAL,
                provider_key=self.backend,
                reason="mode-a: caller pinned an explicit model",
            )

        base_url = self._require_base_url()
        adapter = request.adapter if request.adapter is not None else self.default_adapter
        model_id, config = self._build_config(base_url=base_url, adapter=adapter)

        model = self._construct(self.backend, model_id, **config)
        self._assert_no_egress(model)

        return ModelResolveResult(
            model=model,
            mode=ProviderMode.LOCAL,
            provider_key=self.backend,
            adapter=adapter if self._adapter_applies(adapter) else None,
            reason=f"mode-a: self-hosted {self.backend} at {base_url}",
        )

    def capabilities(self) -> ModelCapabilities:
        """What Mode A can do. Pure and cheap: it imports no SDK and asks no server.

        ``egress_free`` is True only when an in-VPC endpoint is actually configured and does
        not point at a known public host. An unconfigured provider claiming egress freedom
        would be the exact false assurance this ticket exists to prevent.
        """
        return ModelCapabilities(
            modes=(ProviderMode.LOCAL,),
            provider_keys=(self.backend,),
            model_ids=(self.model_id,) if self.model_id else (),
            supports_adapters=self.backend in _ADAPTER_BACKENDS,
            supports_escalation=False,
            supports_vision=False,
            egress_free=self._is_egress_free(),
            context_window=self.context_window,
            notes={
                "base_url": self.base_url,
                "adapter_mode": self.adapter_mode,
            },
        )

    # -- internals ---------------------------------------------------------

    def _require_base_url(self) -> str:
        """The configured endpoint, or a configuration error naming the field.

        Never a fallback. Returning a default here is precisely the failure mode described in
        the module docstring: vLLM would reach for ``http://localhost:8000/v1/`` and Ollama for
        ``https://ollama.com``, and a deployment that believed it was self-hosted would be
        talking to neither the box it meant nor, in Ollama's case, its own network.
        """
        if not self.base_url:
            raise ModelProviderConfigError(
                "Mode A requires an explicit in-VPC endpoint. Set EGPT_MODE_A_BASE_URL (or pass "
                "base_url=). There is deliberately no default: vLLM would fall back to "
                "http://localhost:8000/v1/ and Ollama to https://ollama.com, so an unset endpoint "
                "must fail loudly rather than silently leave the VPC.",
                field_name="mode_a_base_url",
            )
        return self.base_url

    def _adapter_applies(self, adapter: Optional[str]) -> bool:
        return bool(adapter) and self.backend in _ADAPTER_BACKENDS

    def _build_config(self, *, base_url: str, adapter: Optional[str]) -> Tuple[str, Dict[str, Any]]:
        """The model id and constructor kwargs for this resolution.

        Split out from ``resolve_detailed`` so the adapter-rendering rules are testable without
        constructing anything -- the interesting behavior is which of the two conventions puts
        the adapter where.
        """
        model_id = self.model_id
        config: Dict[str, Any] = {}

        # Ollama's client takes `host`; the OpenAI-schema clients take `base_url`. Both are
        # passed explicitly and unconditionally, which is what makes the SDK defaults dead code
        # on this path.
        if self.backend == "ollama":
            config["host"] = base_url
            # Ollama defaults `api_key` to `getenv("OLLAMA_API_KEY")`, so leaving it unset here
            # would let an ambient environment variable outrank the configured value -- and
            # would silently drop `mode_a_api_key` for an authenticated in-VPC server. Passing
            # it explicitly (None included) keeps this module the only thing that decides.
            config["api_key"] = self.api_key
        else:
            config["base_url"] = base_url
            # VLLM raises ModelAuthenticationError when it finds no key, and an in-VPC server
            # is usually unauthenticated. OpenAILike's own "not-provided" placeholder is the
            # established convention for that case.
            config["api_key"] = self.api_key or "not-provided"

        if self._adapter_applies(adapter):
            if self.adapter_mode == "model-id":
                # vLLM's multi-LoRA convention: the adapter *is* the model id.
                model_id = adapter
            else:
                # `extra_body` passthrough. VLLM merges its own vLLM-specific params into this
                # same dict in `get_request_params`, so seeding it here composes rather than
                # collides.
                config["request_params"] = {"extra_body": {self.adapter_field: adapter}}

        if not model_id:
            raise ModelProviderConfigError(
                "Mode A requires a model id. Set EGPT_MODE_A_MODEL_ID (or pass model_id=), or "
                "select an adapter with adapter_mode='model-id'.",
                field_name="mode_a_model_id",
            )

        return model_id, config

    def _endpoint_is_in_vpc(self, endpoint: Optional[str]) -> bool:
        """Whether ``endpoint``'s host is one Mode A considers inside the network.

        Compares the parsed *hostname*, never a substring of the URL. Substring matching is
        wrong in both directions: it would classify ``http://ollama.comms.internal`` as public
        because "ollama.com" appears in it, and would match
        ``http://api.openai.com.evil.net/v1`` for a reason that has nothing to do with which
        server is actually being addressed.
        """
        if not endpoint:
            return False

        from ipaddress import ip_address
        from urllib.parse import urlsplit

        candidate = endpoint if "//" in endpoint else f"//{endpoint}"
        try:
            host = (urlsplit(candidate).hostname or "").lower()
        except ValueError:
            return False
        if not host:
            return False

        try:
            # An IP literal answers definitively: RFC1918/loopback/link-local are in-VPC,
            # anything routable on the public internet is not.
            address = ip_address(host)
        except ValueError:
            pass
        else:
            return address.is_private or address.is_loopback or address.is_link_local

        if host == "localhost":
            return True
        # A bare name with no dot is a container/Kubernetes service name, which cannot be a
        # public FQDN.
        if "." not in host:
            return True
        if any(host == suffix.lstrip(".") or host.endswith(suffix) for suffix in _PRIVATE_SUFFIXES):
            return True
        return any(host == allowed or host.endswith(f".{allowed}") for allowed in self.allowed_hosts)

    def _is_egress_free(self) -> bool:
        return self._endpoint_is_in_vpc(self.base_url)

    def _assert_no_egress(self, model: "Model") -> None:
        """Fail a resolution whose constructed model points at a public host.

        Belt and braces over ``_require_base_url``: this reads the endpoint back off the model
        that was actually built, so an SDK default that slipped through -- or an endpoint
        configured to a public host by mistake -- is caught before a single token is sent
        rather than after.
        """
        endpoint = getattr(model, "base_url", None) or getattr(model, "host", None)
        if not endpoint:
            raise ModelProviderConfigError(
                f"Mode A resolved a {self.backend} model with no endpoint set, which would let the "
                "backend fall back to its own default and leave the VPC.",
                field_name="mode_a_base_url",
            )
        if not self._endpoint_is_in_vpc(str(endpoint)):
            raise ModelProviderConfigError(
                f"Mode A resolved an endpoint that is not in-VPC: {endpoint}. Mode A must only "
                "talk to a self-hosted server. If this host is internal, add its domain to "
                "EGPT_MODE_A_ALLOWED_HOSTS.",
                field_name="mode_a_base_url",
            )
