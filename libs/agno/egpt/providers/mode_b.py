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

from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

if TYPE_CHECKING:
    # Annotation only, for ``aresolve``'s return type. Imported under TYPE_CHECKING rather than
    # at module scope so this module stays as cheap to import as ``capabilities()`` needs it to
    # be, and `from __future__ import annotations` above keeps the annotation a string.
    from egpt.models.base import Model

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


def _first_set_or_none(*candidates: Optional[str]) -> Optional[str]:
    """``_first_set``, but reporting "nothing was configured" as None rather than "".

    The caller needs all three states apart: a real value, an explicitly blank value (a
    configuration error to report), and nothing set at all (fall back to the default).
    """
    for candidate in candidates:
        if candidate is not None:
            return candidate
    return None


def _registry_key_for(model: "Model") -> str:
    """Return the discovery key that declares ``model``'s class, when known.

    ``Model.provider`` is a human-facing display name (for example ``"Anthropic"``), not the
    lowercase registry key accepted by ``discovery.resolve``. Match runtime registrations by
    object identity. For lazy registrations, inspect only already-loaded export modules and match
    the exported target by identity. This handles package re-exports and aliases without importing
    another integration or confusing same-named classes in sibling modules. The current frozen
    result contract requires a string, so an unregistered caller-owned class retains the explicit
    ``"requested"`` fallback.
    """
    import sys

    from egpt.registry import discovery

    cls = type(model)
    for key, registration in discovery.entries("model").items():
        if registration.obj is not None and registration.obj is cls:
            return key
        if registration.module is None or registration.attr is None:
            continue
        target = sys.modules.get(registration.module)
        if target is None:
            continue
        try:
            for part in registration.attr.split("."):
                target = getattr(target, part)
        except AttributeError:
            continue
        if target is cls:
            return key
    return "requested"


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
        # Stripped at assignment, not merely when validated. Validating `.strip()` while storing
        # the raw value let `EGPT_MODE_B_PROVIDER=" openai "` -- a trailing space in a .env file
        # or a compose interpolation -- pass startup and then fail every request inside
        # `discovery.construct` with a bare `Unknown model ' openai '` and no field name. The
        # gateway name below was already stripped, so this was inconsistent within one
        # constructor.
        self._provider_key: str = _first_set(
            provider_key, getattr(settings, "mode_b_provider", None), _DEFAULT_PROVIDER_KEY
        ).strip()
        # Whether the model id was chosen by this deployment or fell back to the default. The
        # default is only coherent with the default provider, so the pairing is checked below.
        #
        # `is None`, not `or`: `_first_set` deliberately preserves an explicitly blank setting
        # so the blank-check below can report it, and collapsing "" to the default here would
        # discard exactly the distinction it exists to keep.
        configured_model: Optional[str] = _first_set_or_none(model_id, getattr(settings, "mode_b_model", None))
        self._model_id: str = (_DEFAULT_MODEL_ID if configured_model is None else configured_model).strip()
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

        if not self._provider_key:
            raise ModelProviderConfigError(
                "Mode B provider key is empty. Set EGPT_MODE_B_PROVIDER to a frontier registry "
                f"key, e.g. one of: {', '.join(FRONTIER_PROVIDER_KEYS)}.",
                field_name="mode_b_provider",
            )
        if not self._model_id:
            raise ModelProviderConfigError(
                "Mode B model id is empty. Set EGPT_MODE_B_MODEL to a frontier model id.",
                field_name="mode_b_model",
            )
        # Fail at construction on an unrecognized provider, the way the gateway check below
        # already does. Deferring to `discovery.resolve` meant `EGPT_MODE_B_PROVIDER=antropic`
        # passed startup and health checks, then failed the first user request under load with a
        # bare ValueError dumping sixty registry names and carrying no field name -- so a
        # handler catching ModelProviderConfigError could not say which setting was wrong.
        #
        # Checked against the registry rather than FRONTIER_PROVIDER_KEYS: that tuple is the
        # curated shortlist for error messages, and refusing a valid out-of-tree provider
        # registered through an `egpt.models` entry point would defeat the discovery seam.
        from egpt.registry import discovery

        if discovery.get("model", self._provider_key) is None:
            raise ModelProviderConfigError(
                f"Unknown Mode B provider '{self._provider_key}'. Set EGPT_MODE_B_PROVIDER to a "
                f"registered model key, e.g. one of: {', '.join(FRONTIER_PROVIDER_KEYS)}.",
                field_name="mode_b_provider",
            )
        # The default model id is only coherent with the default provider. Defaulting them
        # independently meant `EGPT_MODE_B_PROVIDER=anthropic` alone built `Claude(id="gpt-5.5")`
        # -- construction succeeded, capabilities() reported the wrong id as fact, and every
        # request died with a vendor 404 that named neither setting.
        if configured_model is None and self._provider_key != _DEFAULT_PROVIDER_KEY:
            raise ModelProviderConfigError(
                f"Mode B provider '{self._provider_key}' is configured with no model id, and the "
                f"default '{_DEFAULT_MODEL_ID}' belongs to '{_DEFAULT_PROVIDER_KEY}'. Set "
                "EGPT_MODE_B_MODEL to a model this provider serves.",
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

        Blocking: see ``aresolve`` below. This is not the pure-construction case CLAUDE.md
        exempts -- it probes the filesystem and triggers a first-time SDK import.

        Raises:
            ModelProviderConfigError: the gateway is unknown, its package is unavailable, or
                its required routing config is missing.
            ImportError: the frontier backend's own SDK is not installed. Propagated
                unwrapped, matching ``discovery.resolve``.
        """
        if request.mode is not None:
            # Coerce rather than compare by identity. ProviderMode is deliberately a (str, Enum)
            # so a raw config value round-trips, ModelResolveRequest does no coercion, and
            # ProviderSettings.default_mode is typed Optional[str] -- so `mode="frontier"` is a
            # semantically valid frontier request that identity comparison rejected. The
            # rejection then crashed on `.value` with AttributeError, which is not a ValueError
            # and so escaped every `except ValueError` around model resolution. Formatting the
            # raw value keeps the message right for both forms.
            try:
                requested_mode = ProviderMode(request.mode)
            except ValueError:
                raise ModelProviderConfigError(
                    f"Unknown mode {request.mode!r}. Mode B serves {ProviderMode.FRONTIER.value!r} requests.",
                    field_name="mode",
                ) from None
            if requested_mode is not ProviderMode.FRONTIER:
                raise ModelProviderConfigError(
                    f"Mode B serves {ProviderMode.FRONTIER.value!r} requests; got {request.mode!r}.",
                    field_name="mode",
                )

        if request.requested is not None:
            # No provider_key, no gateway: this route was bypassed, and stamping the configured
            # ones onto the result described a call that never happened. With provider_key
            # "openai" and gateway "portkey" configured, `Agent(model=Claude(...))` reported a
            # request to OpenAI through Portkey when it went direct to Anthropic -- wrong cost
            # attribution for EGPT-3.5's escalation logging and wrong gateway-coverage numbers
            # for any tracing hook reading `gateway`. Recover the pinned model class's registry
            # key when it has one; its `provider` attribute is only a human-facing display name.
            return ModelResolveResult(
                model=request.requested,
                mode=ProviderMode.FRONTIER,
                provider_key=_registry_key_for(request.requested),
                reason="caller pinned an explicit model",
                gateway=None,
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

    async def aresolve(self, request: ModelResolveRequest) -> Model:
        """Resolve off the event loop.

        ``BaseModelProvider.aresolve`` delegates to the sync path and scopes that to "a provider
        that only reads local config", naming a gateway probe as the case that must override it.
        Mode B is that case: ``find_spec`` walks every ``sys.path`` entry, and
        ``discovery.construct`` triggers the first-time import of a vendor SDK (``anthropic``,
        ``openai``, ``portkey_ai``) -- hundreds of milliseconds of disk I/O and module execution.

        Because the base class defines ``aresolve``, ``aresolve_via`` treats this provider as
        async-capable and awaits it, so without this override the first resolution in each
        worker would stall the event loop and everything queued behind it. Offloading to a
        thread keeps the loop free; the work itself is unchanged, and the import is cached after
        the first call.
        """
        return (await self.aresolve_detailed(request)).model

    async def aresolve_detailed(self, request: ModelResolveRequest) -> ModelResolveResult:
        """Resolve the detailed result off the event loop as well as the bare-model path.

        Leaving the inherited implementation in place made direct async detailed callers run
        the same gateway probe and first-time SDK import synchronously on the event-loop thread.
        """
        import asyncio

        return await asyncio.to_thread(self.resolve_detailed, request)

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
