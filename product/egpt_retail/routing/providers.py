"""The provider objects behind the routing table, built once per process.

Composition, not a new provider. Both tiers are the framework's ``FrontierModelProvider``
(Mode B, ``azure-openai``); the small tier is wrapped in the framework's
``EscalatingModelProvider`` with the frontier as its target. The only product-side class is
``TierModelProvider``, which exists for one reason: ``FrontierModelProvider.capabilities()``
never states a ``context_window`` -- it cannot, without asking a server -- and the escalation
rule ``input_exceeds_context_window`` returns ``None`` on an unknown window, so without a
declared one the pre-call escalation this package exists for could never fire.

Nothing here calls a model. ``AzureOpenAI`` reads ``AZURE_OPENAI_*`` in ``_get_client_params``,
at the first request, so a provider can be built and asked for its capabilities on a machine
with no credential -- which is what lets ``/config`` list the agents and the cookbook prove
the pre-call decision offline.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable, List, Optional

from egpt.providers.escalation import EscalatingModelProvider
from egpt.providers.mode_b import FrontierModelProvider
from egpt.providers.model_provider import ModelCapabilities, ModelProvider

from egpt_retail.app.settings import RetailSettings
from egpt_retail.routing.table import (
    Tier,
    UnroutedAgentError,
    assert_every_agent_routed,
    tier_for,
)

__all__ = [
    "AZURE_PROVIDER_KEY",
    "TierModelProvider",
    "TierProviders",
    "build_tier_providers",
    "provider_for",
    "route_agents",
]

#: The registry key both tiers build under. One key, two deployments: the tiers differ in
#: ``model_id`` (the Azure deployment name) and in the window the small one declares.
AZURE_PROVIDER_KEY = "azure-openai"


class TierModelProvider(FrontierModelProvider):
    """A Mode B provider pinned to one Azure deployment, that can state its context window.

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
        context_window: Optional[int] = None,
        settings: Optional[Any] = None,
    ) -> None:
        # `azure_deployment` pinned on the client, not only `id` on the request. Azure routes a
        # call by the *client's* deployment (`/openai/deployments/<name>/...`) and ignores the
        # request's `model`; `AzureOpenAI._get_client_params` falls back to the
        # AZURE_OPENAI_DEPLOYMENT env var when the client has none. With that variable set --
        # every developer's azure_key.env sets it -- both tiers would have hit the same
        # deployment while reporting different ids, and routing would have been a label.
        super().__init__(
            provider_key=AZURE_PROVIDER_KEY,
            model_id=deployment,
            model_config={"azure_deployment": deployment},
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
            provider_keys=(AZURE_PROVIDER_KEY,),
            context_window=self._context_window,
            notes={**base.notes, "tier": self.tier.value, "deployment": self.model_id},
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
    """Both tiers from settings. Pure construction; no credential is read."""
    return TierProviders(
        small=TierModelProvider(
            tier=Tier.SMALL,
            deployment=settings.retail_azure_small_deployment,
            context_window=settings.retail_small_context_window,
        ),
        frontier=TierModelProvider(
            tier=Tier.FRONTIER, deployment=settings.retail_azure_deployment
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
