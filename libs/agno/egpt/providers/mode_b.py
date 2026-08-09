"""Mode B: hosted frontier providers, optionally behind a gateway (EGPT-3.4).

The other half of Epic 3's swap. Mode A (EGPT-3.3) serves an in-VPC SLM; Mode B serves a
hosted frontier model -- Anthropic, OpenAI, Google, Bedrock, Azure OpenAI -- and, when a
gateway is configured, routes that traffic through Portkey instead of straight to the vendor.
Both are the same ``ModelProvider`` shape, so an application picks between them with
environment variables and no code change.

Two properties this file is built around:

**Nothing is named by import.** Frontier backends resolve *by registry key* through
``egpt.registry.discovery.construct``, imported inside the function that calls it. Importing
this module therefore drags in no vendor SDK, which
``tests/unit/registry/test_discovery_laziness.py`` enforces from a clean subprocess. It also
means a deployment with only ``anthropic`` installed can import Mode B, ask it for
``capabilities()``, and never touch ``openai``.

**Frontier credentials are not settings.** ``OPENAI_API_KEY``, ``ANTHROPIC_API_KEY``,
``AWS_ACCESS_KEY_ID`` and friends are read by each vendor's own SDK under names that SDK owns
-- ``egpt/settings/__init__.py`` records why the framework does not re-namespace them. So this
provider never reads a credential and never re-implements credential resolution: a missing
frontier credential surfaces as that SDK's own error, which already names the variable to set.
What Mode B *does* own is routing -- which frontier, which model, which gateway -- and the
gateway's key, which is provisioned by us. Those are checked here and raise
``ModelProviderConfigError`` with the offending field name.

The LiteLLM gateway path is INERT by deliberate decision: ``egpt[litellm]`` is commented out in
``libs/agno/pyproject.toml`` (~line 249) over the March 2026 PyPI compromise. See
``gateways.py``; configuring it raises a clear configuration error rather than an
``ImportError``, and no test exercises it.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from egpt.providers.gateways import GatewayRoute, known_gateways, resolve_gateway
from egpt.providers.model_provider import (
    BaseModelProvider,
    ModelCapabilities,
    ModelProviderConfigError,
    ModelResolveRequest,
    ModelResolveResult,
    ProviderMode,
)

__all__ = ["FRONTIER_PROVIDER_KEYS", "FrontierModelProvider"]

# Registry keys Mode B will route to. Advisory, in the same sense as
# ``ModelCapabilities.model_ids``: it is what ``capabilities()`` reports and what an unknown
# ``mode_b_provider`` is diagnosed against, not a whitelist -- a deployment naming any other
# key registered in the discovery table (including one from an out-of-tree entry point) still
# resolves. Naming a key here imports nothing.
FRONTIER_PROVIDER_KEYS: Tuple[str, ...] = (
    "anthropic",
    "aws-bedrock",
    "azure-openai",
    "google",
    "openai",
)

# Used only when nothing at all is configured, so that a bare ``FrontierModelProvider()`` is
# still a working provider rather than an error. "openai" resolves to OpenAIResponses.
_DEFAULT_PROVIDER_KEY = "openai"
_DEFAULT_MODEL_ID = "gpt-5.5"


def _first_set(*candidates: Optional[str]) -> str:
    """The first candidate that is not None, even when it is blank.

    Distinct from ``a or b``, which treats "" as unset. Mode B needs the distinction: an
    explicitly blank setting is a configuration error to report, not a value to replace.
    """
    for candidate in candidates:
        if candidate is not None:
            return candidate
    return ""


class FrontierModelProvider(BaseModelProvider):
    """Resolves every request to a hosted frontier model, optionally via a gateway.

    Configuration comes from ``egpt.settings.providers.ProviderSettings`` unless it is passed
    to the constructor, which is what tests and programmatic callers use. Constructor
    arguments win over settings: an explicit value in code is a stronger statement than an
    environment variable, and it keeps the provider testable without mutating ``os.environ``.

    Instances are immutable after construction and hold no per-request state, so one provider
    is safe to share across concurrent requests -- the contract's requirement. Each
    ``resolve`` builds a *fresh* ``Model``, because ``Model`` is a mutable dataclass that
    orchestration assigns to.
    """

    name = "mode-b"

    def __init__(
        self,
        *,
        provider_key: Optional[str] = None,
        model_id: Optional[str] = None,
        gateway: Optional[str] = None,
        gateway_api_key: Optional[str] = None,
        gateway_base_url: Optional[str] = None,
        gateway_virtual_key: Optional[str] = None,
        model_config: Optional[Dict[str, Any]] = None,
        settings: Optional[Any] = None,
    ) -> None:
        """Build a Mode B provider.

        Args:
            provider_key: Registry key of the frontier backend ("anthropic", "openai", ...).
            model_id: Frontier model id, e.g. "claude-sonnet-4-5".
            gateway: "portkey", or None to talk to the frontier directly.
            gateway_api_key: Key for the gateway itself. EGPT-owned, unlike frontier
                credentials, so it is a setting.
            gateway_base_url: Overrides the gateway's own default endpoint. This is what a
                self-hosted or mock gateway is pointed at.
            gateway_virtual_key: Portkey virtual key naming the upstream provider.
            model_config: Extra kwargs forwarded verbatim to the constructed model.
            settings: A ``ProviderSettings`` to read defaults from. Injected mainly by tests;
                omitted, a fresh snapshot is taken from the environment.
        """
        if settings is None:
            # Imported here, not at module scope: settings pulls in pydantic-settings, and this
            # module must stay importable (and cheap) on the capabilities-only path.
            from egpt.settings.providers import get_provider_settings

            settings = get_provider_settings()

        # ``_first_set`` rather than ``or``: a blank string must reach the validation below as
        # a blank rather than silently falling through to the default. ``EGPT_MODE_B_PROVIDER=""``
        # is a deployment mistake, and defaulting it to OpenAI would answer it by guessing.
        self._provider_key: str = _first_set(
            provider_key, getattr(settings, "mode_b_provider", None), _DEFAULT_PROVIDER_KEY
        )
        self._model_id: str = _first_set(model_id, getattr(settings, "mode_b_model", None), _DEFAULT_MODEL_ID)
        # A blank gateway means "no gateway", not "a gateway named ''". The branded-env source
        # deliberately preserves empty-but-set values, so a compose file passing through an
        # unset host var (``EGPT_MODE_B_GATEWAY=${GATEWAY:-}``) arrives here as "". Reading that
        # as a name would turn "route direct" into a hard startup crash.
        gateway_name = (gateway or getattr(settings, "mode_b_gateway", None) or "").strip()
        self._gateway: Optional[str] = gateway_name or None
        self._gateway_api_key: Optional[str] = gateway_api_key or getattr(settings, "mode_b_gateway_api_key", None)
        self._gateway_base_url: Optional[str] = gateway_base_url or getattr(settings, "mode_b_gateway_base_url", None)
        self._gateway_virtual_key: Optional[str] = gateway_virtual_key or getattr(
            settings, "mode_b_gateway_virtual_key", None
        )
        self._model_config: Dict[str, Any] = dict(model_config or {})

        if not self._provider_key.strip():
            raise ModelProviderConfigError(
                "Mode B provider key is empty. Set EGPT_MODE_B_PROVIDER to a frontier registry "
                f"key, e.g. one of: {', '.join(FRONTIER_PROVIDER_KEYS)}.",
                field_name="mode_b_provider",
            )
        if not self._model_id.strip():
            raise ModelProviderConfigError(
                "Mode B model id is empty. Set EGPT_MODE_B_MODEL to a frontier model id.",
                field_name="mode_b_model",
            )
        if self._gateway is not None and self._gateway not in known_gateways():
            # Fail at construction, not at the first request: an unknown gateway name is a
            # deployment typo, and surfacing it at startup beats surfacing it under load.
            raise ModelProviderConfigError(
                f"Unknown gateway '{self._gateway}'. Known gateways: {', '.join(known_gateways())}. "
                "Leave EGPT_MODE_B_GATEWAY unset to route directly to the frontier provider.",
                field_name="mode_b_gateway",
            )

    # -- introspection ------------------------------------------------------

    @property
    def gateway(self) -> Optional[str]:
        """The configured gateway name, or None for direct-to-frontier routing."""
        return self._gateway

    @property
    def provider_key(self) -> str:
        """The frontier registry key this provider resolves to, ignoring any gateway."""
        return self._provider_key

    @property
    def model_id(self) -> str:
        """The frontier model id resolved when the caller pins nothing."""
        return self._model_id

    # -- the contract -------------------------------------------------------

    def capabilities(self) -> ModelCapabilities:
        """What Mode B can do. Pure: no I/O, no SDK import, no gateway probe.

        In particular it does not call ``resolve_gateway``, which checks whether the gateway's
        package is importable. ``capabilities()`` is called on paths where a missing optional
        dependency has to stay a non-event, so it reports the *configured* gateway without
        asserting that it is usable.
        """
        return ModelCapabilities(
            modes=(ProviderMode.FRONTIER,),
            provider_keys=FRONTIER_PROVIDER_KEYS,
            model_ids=(self._model_id,),
            supports_adapters=False,  # LoRA adapters are a Mode A concept
            supports_escalation=True,  # Mode B is EGPT-3.5's escalation target
            supports_tools=True,
            supports_structured_output=True,
            supports_vision=True,
            supports_streaming=True,
            gateway=self._gateway,
            egress_free=False,  # a hosted frontier is, by definition, egress
            notes={"provider_key": self._provider_key},
        )

    def resolve_detailed(self, request: ModelResolveRequest) -> ModelResolveResult:
        """Resolve to a frontier model, routing through the gateway when one is configured.

        A non-None ``request.requested`` is returned unchanged: the contract makes user intent
        outrank configuration, and Mode B has no policy that overrides it.

        Sync-only, like the rest of the ``ModelProvider`` contract. This builds an object and
        has nothing to await -- an ``async`` twin would be dead weight, the exemption
        CLAUDE.md grants pure-construction methods (cf. ``discovery.construct``).

        Raises:
            ModelProviderConfigError: the gateway is unknown, its package is unavailable, or
                its required routing config is missing.
            ImportError: the frontier backend's own SDK is not installed. Propagated
                unwrapped, matching ``discovery.resolve``.
        """
        if request.mode is not None and request.mode is not ProviderMode.FRONTIER:
            raise ModelProviderConfigError(
                f"Mode B serves {ProviderMode.FRONTIER.value!r} requests; got {request.mode.value!r}.",
                field_name="mode",
            )

        if request.requested is not None:
            return ModelResolveResult(
                model=request.requested,
                mode=ProviderMode.FRONTIER,
                provider_key=self._provider_key,
                reason="caller pinned an explicit model",
                gateway=self._gateway,
            )

        if self._gateway is None:
            model = self._construct(self._provider_key, self._model_id, **self._model_config)
            return ModelResolveResult(
                model=model,
                mode=ProviderMode.FRONTIER,
                provider_key=self._provider_key,
                reason="direct frontier resolution",
                gateway=None,
            )

        route = self._gateway_route()
        config: Dict[str, Any] = {**route.config, **self._model_config}
        # The model id stays the *frontier* id: a gateway is a transport, not a model choice.
        model = self._construct(route.provider_key, self._model_id, **config)
        return ModelResolveResult(
            model=model,
            mode=ProviderMode.FRONTIER,
            provider_key=route.provider_key,
            reason=f"frontier resolution via the {route.name} gateway",
            gateway=route.name,
        )

    # -- internals ----------------------------------------------------------

    def _gateway_route(self) -> GatewayRoute:
        """Plan the gateway construction. Raises ModelProviderConfigError when unusable."""
        assert self._gateway is not None  # guarded by the caller
        return resolve_gateway(
            self._gateway,
            api_key=self._gateway_api_key,
            base_url=self._gateway_base_url,
            virtual_key=self._gateway_virtual_key,
        )
