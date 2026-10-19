"""The provider objects behind the routing table, built once per process.

Composition, not a new provider. Both tiers are the framework's ``FrontierModelProvider``
(Mode B); the small tier is wrapped in the framework's ``EscalatingModelProvider`` with the
frontier as its target. Which Mode B registry key the tiers build under is
``settings.retail_model_provider`` (``EGPT_RETAIL_MODEL_PROVIDER``), default ``azure-openai``
-- production's provider today, not the only one this module can build (#268). The only
product-side class is ``TierModelProvider``, which exists for two reasons: (1)
``FrontierModelProvider.capabilities()`` never states a ``context_window`` -- it cannot,
without asking a server -- and the escalation rule ``input_exceeds_context_window`` returns
``None`` on an unknown window, so without a declared one the pre-call escalation this package
exists for could never fire; (2) Azure OpenAI specifically needs its deployment pinned on the
client (see the comment on ``TierModelProvider.__init__``), which no other Mode B provider
needs.

Nothing here calls a model. ``AzureOpenAI`` reads ``AZURE_OPENAI_*`` in ``_get_client_params``,
at the first request (and every other Mode B backend reads its own credential the same lazy
way), so a provider can be built and asked for its capabilities on a machine with no credential
-- which is what lets ``/config`` list the agents and the cookbook prove the pre-call decision
offline.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Dict, Iterable, List, Optional

from egpt.providers.escalation import EscalatingModelProvider
from egpt.providers.mode_b import FrontierModelProvider
from egpt.providers.model_provider import (
    ModelCapabilities,
    ModelProvider,
    ModelProviderConfigError,
)

from egpt_retail.app.settings import RetailSettings
from egpt_retail.routing.table import (
    Tier,
    UnroutedAgentError,
    assert_every_agent_routed,
    tier_for,
)

__all__ = [
    "AZURE_PROMPT_CACHE_MIN_API_VERSION",
    "AZURE_PROVIDER_KEY",
    "TierModelProvider",
    "TierProviders",
    "build_tier_providers",
    "check_prompt_cache_config",
    "prompt_cache_config",
    "provider_for",
    "route_agents",
]

#: The default registry key both tiers build under when no other provider is configured
#: (``settings.retail_model_provider``, default). One key, two deployments: the tiers differ
#: in ``model_id`` (the Azure deployment name) and in the window the small one declares.
#:
#: Does double duty as the sentinel ``TierModelProvider.__init__`` compares against to decide
#: whether the Azure-only ``azure_deployment`` client pin applies. Before #268, Azure was the
#: only reachable provider at all, so "no second case exists yet" was trivially true; now any of
#: ``egpt.providers.mode_b.FRONTIER_PROVIDER_KEYS`` is one setting away, which is precisely the
#: condition under which a second deployment-routed provider needing an analogous pin becomes
#: plausible. Still not worth a lookup table of one member -- split this out the day a second
#: provider actually needs its own client-side pin, not before.
AZURE_PROVIDER_KEY = "azure-openai"

#: Mode B keys whose model class takes ``prompt_cache_key`` / ``prompt_cache_retention``
#: (``OpenAIChat`` behind ``azure-openai``, ``OpenAIResponses`` behind ``openai``).
_PROMPT_CACHE_KEY_PROVIDERS = frozenset({AZURE_PROVIDER_KEY, "openai"})

#: The first dated Azure OpenAI ``api-version`` Microsoft's prompt-caching how-to lists
#: *automatic* caching under. That is a floor, not a confirmation: whether the deployment's
#: model accepts the ``prompt_cache_key`` request field is a property of the model behind the
#: deployment (a non-OpenAI model served through a Foundry resource rejects it with a 400),
#: which no version comparison can see -- the live probe in deploy/vm/egpt.env.example is the
#: only gate for that. The framework's own pin (``DEFAULT_API_VERSION``) is already past this
#: floor, so the check fires only on a deployment that exported an older version deliberately.
AZURE_PROMPT_CACHE_MIN_API_VERSION = "2024-10-01-preview"

#: Dated versions are ``YYYY-MM-DD`` with an optional ``-preview`` suffix; the undated ``v1``
#: surface (``"v1"``, ``"preview"``, ``"latest"``) is newer than every dated one.
_API_VERSION_DATE_LEN = len("YYYY-MM-DD")


def _azure_api_version_supports_prompt_caching(api_version: str) -> bool:
    version = api_version.strip()
    if not version[:4].isdigit():
        return True
    # The date prefix only: compared whole, "2024-10-01" (no suffix) would sort *below*
    # "2024-10-01-preview" and be refused for being shorter, not older.
    return (
        version[:_API_VERSION_DATE_LEN]
        >= (AZURE_PROMPT_CACHE_MIN_API_VERSION[:_API_VERSION_DATE_LEN])
    )


def _azure_api_version_for_prompt_caching() -> str:
    """The Azure ``api-version`` a cached request will carry: ``AZURE_OPENAI_API_VERSION``,
    else the framework's pin. ``AzureOpenAI`` itself never reads the variable (a framework
    upgrade must not move every Azure call onto a preview version because an env file happened
    to name one), so the product passes it through explicitly, and only while caching is on.
    Imported lazily: this module is importable without the openai SDK.
    """
    from egpt.integrations.models.azure.openai_chat import resolve_api_version

    return resolve_api_version()


def check_prompt_cache_config(settings: RetailSettings) -> None:
    """Refuse, naming the version and the variable, when caching is on and the Azure
    ``api-version`` it would send predates ``AZURE_PROMPT_CACHE_MIN_API_VERSION``.

    Called once, where the tier providers are built at boot -- not from ``prompt_cache_config``.
    That function also runs inside a per-request FastAPI dependency (``drafting_agent``) and
    per extraction/explanation run (``UseCaseService._agent_for``), where a raise is an opaque
    500 on every call (the catch-all handler withholds ``str(exc)`` by design) rather than a
    deployment that fails to come up. Keyed on the caching flag alone, not on
    ``settings.retail_model_provider``: those two sites build ``AzureOpenAI`` directly whatever
    provider the tiers use, so a deployment routing its tiers through ``openai`` still sends
    the field to Azure from them.
    """
    if not settings.retail_prompt_cache_enabled:
        return
    version = _azure_api_version_for_prompt_caching()
    if _azure_api_version_supports_prompt_caching(version):
        return
    raise ModelProviderConfigError(
        "prompt caching is on (EGPT_RETAIL_PROMPT_CACHE_ENABLED) but the Azure OpenAI "
        f"api-version in use, {version!r} (AZURE_OPENAI_API_VERSION), predates "
        f"{AZURE_PROMPT_CACHE_MIN_API_VERSION!r}, the first version Azure lists automatic "
        "prompt caching under. Set AZURE_OPENAI_API_VERSION to "
        f"{AZURE_PROMPT_CACHE_MIN_API_VERSION} or newer, or turn caching off. A version at or "
        "past that floor still does not confirm the deployment's model accepts "
        "prompt_cache_key; only a live call does (deploy/vm/egpt.env.example)."
    )


def prompt_cache_config(settings: RetailSettings, provider_key: str) -> Dict[str, Any]:
    """The model kwargs that turn prompt caching on for ``provider_key``, from settings.

    Empty when caching is disabled (the default) or the provider has no matching knob. Never
    a guess: the result is passed to the model constructor as keyword arguments, so a key the
    model class does not declare would be a ``TypeError`` on the first request. Pure -- it
    neither reads a credential nor refuses; ``check_prompt_cache_config`` does the refusing,
    once, at boot.

    For Azure OpenAI the kwargs also carry ``api_version``, resolved from
    ``AZURE_OPENAI_API_VERSION``: the one place the product lets the environment name the
    version, because caching is the one feature that needs a version newer than the
    framework's pin. Off, nothing is sent and the pin stands.

    Anthropic is behind its own switch, ``retail_prompt_cache_anthropic``: its cache *write*
    is billed above a plain input token, so on a prompt that varies per call (datetime,
    dynamic context) ``cache_system_prompt`` costs more and reads nothing until the
    stable-prefix split lands. ``retail_prompt_cache_enabled`` alone must not buy that.
    """
    if not settings.retail_prompt_cache_enabled:
        return {}
    key = provider_key.strip()
    if key in _PROMPT_CACHE_KEY_PROVIDERS:
        config: Dict[str, Any] = {"prompt_cache_key": settings.retail_prompt_cache_key}
        if settings.retail_prompt_cache_retention is not None:
            config["prompt_cache_retention"] = settings.retail_prompt_cache_retention
        if key == AZURE_PROVIDER_KEY:
            config["api_version"] = _azure_api_version_for_prompt_caching()
        return config
    if key == "anthropic" and settings.retail_prompt_cache_anthropic:
        return {"cache_system_prompt": True, "cache_tools": True}
    return {}


class TierModelProvider(FrontierModelProvider):
    """A Mode B provider pinned to one deployment/model id, that can state its context window.

    ``provider_key`` names the Mode B registry key to build under -- ``azure-openai`` by
    default (production's provider today, #268), but any other key Mode B accepts
    (``anthropic``, ``openai``, ``aws-bedrock``, ``google``, ...) builds through the same path.

    ``name`` carries the tier so the framework's escalation log line reads
    ``small-tier -> frontier-tier`` rather than ``mode-b -> mode-b``. ``capabilities()``
    narrows ``provider_keys`` to the one key this instance actually builds under -- the base
    class advertises the whole frontier shortlist, and ``EscalatingModelProvider`` reports
    ``provider_keys[0]`` as ``escalated_from``, which would have named a provider this
    deployment never used.
    """

    def __init__(
        self,
        *,
        tier: Tier,
        deployment: str,
        provider_key: str = AZURE_PROVIDER_KEY,
        context_window: Optional[int] = None,
        settings: Optional[Any] = None,
        cache_config: Optional[Dict[str, Any]] = None,
    ) -> None:
        # `azure_deployment` pinned on the client, not only `id` on the request -- but only for
        # Azure OpenAI. Azure routes a call by the *client's* deployment
        # (`/openai/deployments/<name>/...`) and ignores the request's `model`;
        # `AzureOpenAI._get_client_params` falls back to the AZURE_OPENAI_DEPLOYMENT env var
        # when the client has none. With that variable set -- every developer's azure_key.env
        # sets it -- both tiers would have hit the same deployment while reporting different
        # ids, and routing would have been a label. No other Mode B provider has this failure
        # mode: each keys a request off `id` alone, which `model_id=deployment` below already
        # gives it, so nothing extra is pinned for them.
        #
        # Compared against `provider_key.strip()`, not the raw argument. `FrontierModelProvider.
        # __init__` `.strip()`s the same argument before storing/validating it, and
        # `RetailSettings` has no `str_strip_whitespace` config to normalize
        # `EGPT_RETAIL_MODEL_PROVIDER` first. Comparing the raw value let a stray space (a
        # `.env`/compose-interpolation artifact) fail this check while the base class still
        # normalized to a clean `"azure-openai"` key -- `capabilities()` then reported a
        # healthy provider while silently skipping the pin, and both tiers collapsed onto
        # `AZURE_OPENAI_DEPLOYMENT` with no error raised anywhere, reintroducing #268. Computed
        # before `super().__init__()` and passed through its own `model_config` parameter,
        # rather than set afterward on the private `_model_config` it stores: that way this
        # provider's config always went through the constructor's normal validation path, and
        # never depends on that attribute's exact name staying stable in the base class.
        model_config: Optional[Dict[str, Any]] = (
            {"azure_deployment": deployment}
            if provider_key.strip() == AZURE_PROVIDER_KEY
            else None
        )
        if cache_config:
            model_config = {**(model_config or {}), **cache_config}
        super().__init__(
            provider_key=provider_key,
            model_id=deployment,
            model_config=model_config,
            settings=settings,
        )
        self.tier = tier
        self.name = f"{tier.value}-tier"
        self._context_window = context_window

    @property
    def context_window(self) -> Optional[int]:
        return self._context_window

    def capabilities(self) -> ModelCapabilities:
        base = super().capabilities()
        return replace(
            base,
            provider_keys=(self.provider_key,),
            context_window=self._context_window,
            # Only `tier` here, not a duplicate of `model_ids` (already `(self.model_id,)` on
            # `base`, two lines up): a `notes["model_id"]` key next to a real `model_ids` field
            # of the same name invites a reader to treat them as two independent facts that
            # could disagree, when they are the same value by construction.
            notes={**base.notes, "tier": self.tier.value},
        )


@dataclass(frozen=True)
class TierProviders:
    """The two tier providers, and the composed provider each routed agent is handed."""

    small: TierModelProvider
    frontier: TierModelProvider

    @property
    def small_with_escalation(self) -> EscalatingModelProvider:
        # A fresh composition per access is deliberate: `EscalatingModelProvider` is a mutable
        # dataclass (its target can be swapped on a running instance), and two agents sharing
        # one would share that mutation. The tier providers underneath are immutable and shared.
        return EscalatingModelProvider(
            primary=self.small,
            escalation_target=self.frontier,
            name="small-then-frontier",
        )


def build_tier_providers(settings: RetailSettings) -> TierProviders:
    """Both tiers from settings. Pure construction; no credential is read.

    Also the boot-time home of ``check_prompt_cache_config``: every assembly path runs this
    builder before serving (``app/components.py``), so a caching config that would fail is
    refused here, once, with the real message -- not per request behind an opaque 500.
    """
    check_prompt_cache_config(settings)
    cache_config = prompt_cache_config(settings, settings.retail_model_provider)
    return TierProviders(
        small=TierModelProvider(
            tier=Tier.SMALL,
            deployment=settings.retail_azure_small_deployment,
            provider_key=settings.retail_model_provider,
            context_window=settings.retail_small_context_window,
            cache_config=cache_config,
        ),
        frontier=TierModelProvider(
            tier=Tier.FRONTIER,
            deployment=settings.retail_azure_deployment,
            provider_key=settings.retail_model_provider,
            cache_config=cache_config,
        ),
    )


def provider_for(tier: Tier, providers: TierProviders) -> ModelProvider:
    """The provider an agent on ``tier`` is handed.

    Small-tier agents get the escalating composition; frontier-tier agents get the frontier
    directly -- there is nothing above it to escalate to, and wrapping it would only add a
    rule evaluation that can never fire.
    """
    if tier is Tier.SMALL:
        return providers.small_with_escalation
    return providers.frontier


def route_agents(agents: Iterable[Any], providers: TierProviders) -> None:
    """Install each agent's provider per the routing table; refuse if any agent is unrouted.

    ``model_provider`` is not an ``Agent`` constructor argument -- the framework reads it as a
    plain attribute and ``deep_copy`` carries it -- so it is set here, after construction.

    Refuses an agent that pins a ``model``: ``FrontierModelProvider`` honours a pinned model
    (``request.requested`` outranks configuration by contract), so a pinned agent would be
    listed as routed while every one of its runs bypassed the table. ``model=None`` is what
    makes the provider decide, at startup through ``set_default_model`` and again per run.
    """
    listed: List[Any] = list(agents)
    assert_every_agent_routed(getattr(agent, "id", None) or "" for agent in listed)
    pinned = sorted(
        getattr(a, "id", "?") for a in listed if getattr(a, "model", None) is not None
    )
    if pinned:
        raise UnroutedAgentError(
            f"agent(s) {pinned} pin a model; routing decides the model, so build them with "
            "model=None and let the tier provider resolve it."
        )
    for agent in listed:
        setattr(agent, "model_provider", provider_for(tier_for(agent.id), providers))  # noqa: B010
