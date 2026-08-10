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

from typing import TYPE_CHECKING, Any, Dict, Iterable, Mapping, Optional, Tuple

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

# Which ProviderSettings field carries each backend's own documented credential. Read through
# settings, never a raw getenv: `tests/unit/test_env_read_guard.py` is a ratchet over
# `egpt/providers/`, and the settings field is also what gives the vendor spelling
# (`VLLM_API_KEY`) its no-warning fallback.
_BACKEND_API_KEY_FIELD: Mapping[str, str] = {
    "vllm": "mode_a_vllm_api_key",
    "ollama": "mode_a_ollama_api_key",
}


def _backend_api_key(settings: "ProviderSettings", backend: Optional[str]) -> Optional[str]:
    """The credential the backend itself would have read, via its settings field.

    Mode A passes ``api_key`` explicitly so that this module is the only thing deciding it, but
    that also suppresses each backend's own env fallback (they read it only when the field is
    falsy). Consulting the same value here keeps the documented per-backend setting working
    instead of shadowing it with a placeholder.
    """
    field = _BACKEND_API_KEY_FIELD.get(backend or "")
    return getattr(settings, field, None) if field else None


def _clean(value: Optional[str]) -> Optional[str]:
    """Strip a configured string, mapping blank to ``None``.

    A blank-but-set value ("" from `EGPT_MODE_A_BASE_URL=${URL:-}` in a compose file) means
    "unset", not "a host named ''". Collapsing it here keeps every `is None` check downstream
    honest instead of each site re-testing for emptiness.
    """
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


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

        # Every string setting is stripped at assignment. Validating `.strip()` while storing
        # the raw value let `EGPT_MODE_A_BACKEND=' vllm '` -- a trailing space in a .env file or
        # a compose interpolation -- fail construction with a message quoting the invisible
        # space, and `EGPT_MODE_A_MODEL_ID='qwen '` reach the server as a 404-ing model id.
        # Matches how mode_b.py handles the same class of value.
        self.base_url = _clean(base_url if base_url is not None else settings.mode_a_base_url)
        # `backend`, `adapter_mode` and `adapter_field` stay non-Optional: each has a real
        # default in ProviderSettings, and the validation below rejects a blank one outright, so
        # widening them to Optional would only push a `None` check onto every reader.
        self.backend: str = _clean(backend if backend is not None else settings.mode_a_backend) or ""
        self.model_id = _clean(model_id if model_id is not None else settings.mode_a_model_id)
        # `EGPT_MODE_A_API_KEY` first, then the backend's own documented credential. Resolved
        # here, once, because settings are read at construction and never per resolve.
        self.api_key = api_key if api_key is not None else settings.mode_a_api_key
        self._backend_api_key = _backend_api_key(
            settings, _clean(backend if backend is not None else settings.mode_a_backend)
        )
        self.adapter_mode: str = (
            _clean(adapter_mode if adapter_mode is not None else settings.mode_a_adapter_mode) or ""
        )
        self.adapter_field: str = (
            _clean(adapter_field if adapter_field is not None else settings.mode_a_adapter_field) or ""
        )
        self.context_window = context_window if context_window is not None else settings.mode_a_context_window
        raw_hosts: Iterable[str] = allowed_hosts if allowed_hosts is not None else settings.mode_a_allowed_hosts_list
        # A bare `str` satisfies `Iterable[str]`, so `allowed_hosts="llm.acme.com"` type-checks
        # and would iterate *characters* -- turning the operator's own allowlist into
        # ('l','l','m',...) and refusing the very endpoint they configured, with remediation
        # text pointing at the setting they had already set. Accept the single-host spelling
        # instead of silently shredding it.
        if isinstance(raw_hosts, str):
            raw_hosts = [raw_hosts]
        self.allowed_hosts: Tuple[str, ...] = tuple(
            cleaned for host in raw_hosts if (cleaned := str(host).strip().lower().lstrip("."))
        )
        # An adapter applied when the request names none. Lets a deployment pin one domain
        # adapter without every call site passing it.
        self.default_adapter = default_adapter

        # Validated at construction, not at first resolve: a misconfigured backend or adapter
        # mode is a deployment mistake, and surfacing it when the process wires up its provider
        # is far better than surfacing it on a user's first request.
        # Checked against the discovery registry rather than LOCAL_BACKENDS: that tuple is the
        # curated shortlist for error messages, and refusing a valid out-of-tree backend
        # registered through an `egpt.models` entry point would defeat the discovery seam --
        # a deployment running its own OpenAI-schema inference server is exactly the Mode A
        # use case. Same rationale as mode_b.py's provider-key check.
        from egpt.registry import discovery

        if not self.backend or discovery.get("model", self.backend) is None:
            raise ModelProviderConfigError(
                f"Unknown Mode A backend '{self.backend}'. Supported: {', '.join(LOCAL_BACKENDS)} "
                "(or any model registered through the 'egpt.models' entry point).",
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
        # `!=`, not `is not`: ProviderMode is a str mixin so that a config value ('local')
        # validates by construction, and an equal-but-not-identical string must take the same
        # path as the enum member. `ModelResolveRequest` coerces at the boundary today, but an
        # identity test here would break again for any caller constructing the result of that
        # coercion differently -- and the raise branch below formats `.value`, which a plain str
        # does not have, so the failure was an AttributeError naming neither field nor provider.
        if request.mode is not None and request.mode != ProviderMode.LOCAL:
            asked = getattr(request.mode, "value", request.mode)
            raise ModelProviderConfigError(
                f"{type(self).__name__} serves {ProviderMode.LOCAL.value!r} only, but the request asked for {asked!r}.",
                field_name="mode",
            )

        # User intent outranks configuration -- an explicit `Agent(model=...)` is returned
        # unchanged, as the frozen EGPT-3.1 contract requires. Mode A has no policy that
        # overrides a pinned model: someone who named a model meant that model.
        if request.requested is not None:
            # Attribute the *pinned* model, never this provider's configuration. Reporting
            # `mode=LOCAL` and `provider_key=<backend>` here meant that in a Mode A deployment
            # an `Agent(model=Claude(...))` was recorded as an in-VPC vLLM resolution while the
            # request went to api.anthropic.com -- so an operator auditing "did anything leave
            # the VPC?" from these records saw zero egress. The pinned model's own endpoint
            # decides the mode; when it names no in-VPC endpoint, this is egress and says so.
            pinned_endpoint = getattr(request.requested, "base_url", None) or getattr(request.requested, "host", None)
            pinned_is_local = bool(pinned_endpoint) and self._endpoint_is_in_vpc(str(pinned_endpoint))
            return ModelResolveResult(
                model=request.requested,
                mode=ProviderMode.LOCAL if pinned_is_local else ProviderMode.FRONTIER,
                provider_key=self._registry_key_for(request.requested),
                reason=(
                    "mode-a: caller pinned an explicit model at an in-VPC endpoint"
                    if pinned_is_local
                    else "mode-a: caller pinned an explicit model that is not in-VPC"
                ),
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

    async def aresolve(self, request: ModelResolveRequest) -> Model:
        """Resolve off the event loop.

        ``BaseModelProvider.aresolve`` delegates to the sync path and scopes that to "a provider
        that only reads local config". Mode A is not that case: ``discovery.construct`` triggers
        the first-time import of a vendor SDK (``vllm``, ``ollama``, ``openai``) -- hundreds of
        milliseconds of disk I/O and module execution.

        Because the base class defines ``aresolve``, ``aresolve_via`` treats this provider as
        async-capable and awaits it, so without this override the first resolution in each worker
        would stall the loop and everything queued behind it. Same override, same reasoning, as
        ``mode_b.FrontierModelProvider``.
        """
        return (await self.aresolve_detailed(request)).model

    async def aresolve_detailed(self, request: ModelResolveRequest) -> ModelResolveResult:
        """Resolve the detailed result off the event loop as well as the bare-model path.

        Leaving the inherited implementation in place made direct async detailed callers run the
        same first-time SDK import synchronously on the event-loop thread.
        """
        import asyncio

        return await asyncio.to_thread(self.resolve_detailed, request)

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
            #
            # The backend's own env var is consulted *before* the placeholder: VLLM only reads
            # `VLLM_API_KEY` when `self.api_key` is falsy, so passing the truthy placeholder
            # unconditionally made that documented fallback dead code -- a token-protected
            # in-VPC server configured the documented way started receiving
            # `Authorization: Bearer not-provided` and 401ing every run, with no config error
            # to explain it because Mode A considered itself fully configured.
            config["api_key"] = self.api_key or self._backend_api_key or "not-provided"

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

        # `ip_address` accepts only the dotted-quad form, but `getaddrinfo` -- and therefore
        # every HTTP client -- also accepts the integer, hex and octal spellings of the same
        # address. `http://134744072:8000` is 8.8.8.8, and reaching the dotless shortcut below
        # would have declared it in-VPC and reported `egress_free=True` while prompts left the
        # network. Resolve those forms to a real address and judge it as an IP.
        #
        # int(host, 0) covers 0x/0o/0b prefixes and plain decimal; a bare leading-zero string
        # ("010") is rejected by int(_, 0) as ambiguous, so it is retried as octal, which is how
        # inet_aton reads it.
        if host.isascii() and (host.isdigit() or host.startswith(("0x", "0o", "0b"))):
            for base in (0, 8):
                try:
                    packed = int(host, base)
                except ValueError:
                    continue
                try:
                    numeric = ip_address(packed)
                except ValueError:
                    break
                return numeric.is_private or numeric.is_loopback or numeric.is_link_local
            # A numeric-looking host that is not a usable address is not a service name.
            return False

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
