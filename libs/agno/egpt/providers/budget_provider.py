"""Budget enforcement at the resolution seam (EGPT-3.6 unit 5).

Escalation decides what the request *needs*. Budget decides what the tenant *may have*. Budget is
evaluated on escalation's answer, and budget wins -- but only by denying, never by silently
downgrading past a capability gap.

**Outermost in the stack**, wrapping ``EscalatingModelProvider`` wrapping
``ConfigDrivenModelProvider``, for three reasons that are all load-bearing:

1. *The inner answer is the thing being priced.* You cannot price the escalation target until
   escalation has chosen it. Budget-innermost prices the primary and is then overruled by an
   escalation it never saw.
2. *Escalation is pure and cheap; budget is I/O.* A counter read is a round-trip. Outermost lets
   the no-budget path return before any store access.
3. *``capabilities()`` only composes one way.* ``EscalatingModelProvider.capabilities()`` computes
   a real union of both tiers; a budget wrapper has no capabilities of its own, so inner-position
   would hide the union from the very rules that need to see it.

The distinction that matters, and the reason this is not simply "deny when low":

===========================  ==============  =====================================================
Escalation fired             Headroom        Outcome
===========================  ==============  =====================================================
a threshold rule, or none    thin            Downgrade: re-resolve with ``can_escalate`` False.
                                             A threshold is a quality *preference*.
a capability rule            thin            Do **not** downgrade. Serve the escalated model and
                                             let the chokepoint decide -- downgrading routes a
                                             vision request onto a text-only model, which fails
                                             silently rather than loudly.
anything                     exhausted       Raise ``BudgetExceededError`` at the seam.
===========================  ==============  =====================================================

Budget predicates mirror EGPT-3.5's ``EscalationRule`` signature so the two rule sets read alike
and can be reasoned about together.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from egpt.exceptions import BudgetExceededError, RateLimitExceededError
from egpt.providers.budget import BudgetEnforcer, BudgetLimits
from egpt.providers.model_provider import (
    BaseModelProvider,
    ModelCapabilities,
    ModelProvider,
    ModelResolveRequest,
    ModelResolveResult,
    aresolve_via,
    supports_detailed_resolve,
)
from egpt.utils.log import log_debug, log_warning

if TYPE_CHECKING:
    from egpt.models.base import Model

__all__ = ["BudgetAwareModelProvider"]

#: Reasons an escalation was a *capability* gap rather than a preference. Downgrading past one of
#: these strands the request on a model that provably cannot serve it, so budget pressure must not
#: override them. Mirrors ``escalation.CAPABILITY_RULES`` by reason string rather than by importing
#: the rule objects, so this module stays free of any dependency on the escalation provider.
_CAPABILITY_REASONS = frozenset(
    {
        "input_exceeds_context_window",
        "vision_unsupported",
        "tools_unsupported",
        "structured_output_unsupported",
    }
)


class BudgetAwareModelProvider(BaseModelProvider):
    """Wraps a provider and enforces a per-tenant token budget on its answer.

    ``limits_for`` is a callable rather than a stored ``BudgetLimits`` because a budget is
    per-tenant: the provider is installed once per process and must read the ceiling that applies
    to *this* request. The default reads ``ModelPolicy.budget`` off the wrapped config provider,
    falling back to the deployment-wide setting.
    """

    name = "budget-aware"

    def __init__(
        self,
        inner: ModelProvider,
        *,
        enforcer: Optional[BudgetEnforcer] = None,
        limits_for: Optional[object] = None,
        default_limits: Optional[BudgetLimits] = None,
    ) -> None:
        self.inner = inner
        self.enforcer = enforcer or BudgetEnforcer()
        self._limits_for = limits_for
        self._default_limits = default_limits

    @classmethod
    def from_settings(
        cls, inner: Optional[ModelProvider] = None, **overrides: Any
    ) -> Optional["BudgetAwareModelProvider"]:
        """Build the budget stack from settings, or ``None`` when no budget is configured.

        Returning ``None`` rather than a pass-through wrapper keeps the no-budget path explicit: a
        caller installs what it gets back, and gets nothing back when nothing is configured, so the
        no-budget deployment carries no extra layer.

        **This factory does not install anything.** Nothing in ``egpt/`` calls
        ``set_default_model_provider``, by design -- installing a process-wide provider is a
        deployment decision, not something a library does on import. So a budget is enforced only
        once an operator wires it, which the cookbook
        (``cookbook/93_components/tenant_budget.py``) and ``docs/`` show. An earlier version of this
        docstring said the factory "is what makes AC1/AC2 true for a real deployment", which
        overstated it: the factory makes enforcement *reachable*; the operator makes it *active*.

        ``inner`` defaults to ``ConfigDrivenModelProvider.from_settings()``. **Note what that does
        not include:** no escalation layer. ``EscalatingModelProvider`` composes two *injected*
        providers by design and has no ``from_settings`` of its own -- there is no settings-only way
        to name a primary and a target -- so a deployment that wants
        budget-over-escalation-over-config passes that stack in explicitly::

            escalating = EscalatingModelProvider(primary=mode_a, escalation_target=mode_b)
            set_default_model_provider(BudgetAwareModelProvider.from_settings(escalating))

        The module docstring above describes that full stack because it is the shape budget is
        *designed* for and the one the downgrade logic reasons about; this factory builds the part
        that settings alone can express. An earlier version of this docstring claimed the factory
        produced the full stack, which it never did.
        """
        from egpt.settings.providers import get_provider_settings

        settings = get_provider_settings(**overrides)
        default_limits = settings.default_budget

        # A budget exists if either the deployment default or *any* tenant policy names one.
        # Checking only the default would leave a per-tenant-only configuration unenforced, which
        # is the shape AC4 is most likely to be used in.
        if inner is None:
            from egpt.providers.config_provider import ConfigDrivenModelProvider

            inner = ConfigDrivenModelProvider.from_settings(**overrides)

        if default_limits is None and not _config_names_a_budget(inner):
            return None

        store = None
        if settings.budget_redis_url:
            from egpt.providers.budget import RedisCounterStore

            store = RedisCounterStore(settings.budget_redis_url)

        return cls(
            inner,
            enforcer=BudgetEnforcer(store),
            default_limits=default_limits,
        )

    # -- limits ------------------------------------------------------------

    def limits_for(self, request: ModelResolveRequest) -> Optional[BudgetLimits]:
        """The ceiling that applies to this request, or ``None`` for "no budget".

        Precedence: an injected resolver, then the tenant's own ``ModelPolicy.budget``, then the
        deployment-wide default. The per-tenant value wins over the deployment default because a
        blanket ceiling is a floor for tenants nobody configured, not an override of the ones
        somebody did.
        """
        if self._limits_for is not None:
            return self._limits_for(request)  # type: ignore[operator]

        policy_budget = self._policy_budget(request)
        if policy_budget is not None:
            return policy_budget
        return self._default_limits

    def _policy_budget(self, request: ModelResolveRequest) -> Optional[BudgetLimits]:
        """``ModelPolicy.budget`` for this request's identity, when the inner stack has one.

        Duck-typed on ``config.lookup`` rather than importing ``ConfigDrivenModelProvider``: this
        provider composes *any* ``ModelProvider``, and naming a concrete sibling here would make
        the stack's order a hard dependency rather than a composition choice.
        """
        for candidate in (self.inner, getattr(self.inner, "primary", None)):
            config = getattr(candidate, "config", None)
            lookup = getattr(config, "lookup", None)
            if lookup is None:
                continue
            try:
                policy = lookup(tenant_id=request.tenant_id, agent_id=request.agent_id, role=request.role)
            except Exception as e:  # a policy lookup must not fail a resolve
                log_debug(f"Budget policy lookup failed; falling back to the default budget: {e}")
                continue
            if policy is not None and getattr(policy, "budget", None) is not None:
                return policy.budget
        return None

    # -- resolution --------------------------------------------------------

    def resolve(self, request: ModelResolveRequest) -> "Model":
        return self.resolve_detailed(request).model

    def resolve_detailed(self, request: ModelResolveRequest) -> ModelResolveResult:
        limits = self.limits_for(request)

        # The whole no-budget path: one call and one `is None` before any store access or any
        # extra work on the inner resolve.
        if limits is None or not limits.enabled or request.tenant_id is None:
            return self._delegate(request)

        # Refuse before resolving when the tenant is already out. Resolving first would build a
        # model (and possibly import an SDK) for a request that cannot be served.
        # The estimate is passed so the "would cross the ceiling" branch can fire. Without it,
        # enforcement degraded to "deny only once already at the ceiling" -- the unit test for that
        # branch passed while the integration could never reach it.
        decision = self.enforcer.check(request.tenant_id, limits, estimated_tokens=request.estimated_input_tokens or 0)
        if decision.denied:
            raise self._denial(request, decision)

        enriched = self._with_headroom(request, limits)
        result = self._delegate(enriched)

        # Escalation may have chosen a costlier tier than the tenant can afford. Downgrade only
        # when the escalation was a preference; a capability gap must be served or refused, never
        # quietly routed onto a model that cannot do the job.
        if self._should_downgrade(result, request, limits):
            log_debug(f"Budget headroom is thin for tenant {request.tenant_id}; re-resolving without escalation")
            result = self._delegate(replace_can_escalate(enriched))

        # Reserved AFTER the downgrade decision, against the model actually served. Reserving
        # before it bound the settlement note to the escalated model that was then discarded, so
        # the served call never reconciled and the estimate became a permanent charge.
        self._reserve_for(result, request, limits)
        return result

    async def aresolve(self, request: ModelResolveRequest) -> "Model":
        return (await self.aresolve_detailed(request)).model

    async def aresolve_detailed(self, request: ModelResolveRequest) -> ModelResolveResult:
        """Async counterpart. Same decisions; only the delegation differs.

        Overrides both variants per the convention EGPT-3.3 established (``mode_a.py``), whose
        docstring records that leaving ``aresolve_detailed`` inherited was a bug: the inherited
        implementation calls the sync path, so a provider implementing ``aresolve`` never had it
        reached and a policy read ran on the event loop.
        """
        limits = self.limits_for(request)
        if limits is None or not limits.enabled or request.tenant_id is None:
            return await self._adelegate(request)

        # Store reads go through a thread: `check`, `_with_headroom` and `reserve` all hit the
        # counter store, which for RedisCounterStore is a network round-trip. Calling them inline
        # from an async resolve blocks the event loop -- the same hazard `aresolve` exists to
        # avoid, reintroduced one layer down.
        import asyncio

        decision = await asyncio.to_thread(
            self.enforcer.check,
            request.tenant_id,
            limits,
            estimated_tokens=request.estimated_input_tokens or 0,
        )
        if decision.denied:
            raise self._denial(request, decision)

        enriched = await asyncio.to_thread(self._with_headroom, request, limits)
        result = await self._adelegate(enriched)

        # Also threaded: `_should_downgrade` calls `_with_headroom` internally, which reads the
        # store -- so leaving this inline kept a round-trip on the event loop even though the
        # three reads around it were moved.
        if await asyncio.to_thread(self._should_downgrade, result, request, limits):
            result = await self._adelegate(replace_can_escalate(enriched))

        await asyncio.to_thread(self._reserve_for, result, request, limits)
        return result

    # -- internals ---------------------------------------------------------

    def _reserve_for(self, result: ModelResolveResult, request: ModelResolveRequest, limits: BudgetLimits) -> None:
        """Charge the estimate and leave a settlement note for the chokepoint (AC3).

        Charging *before* the call rather than after is what makes concurrent requests from one
        tenant sum correctly: post-only accounting lets N in-flight requests each read the same
        pre-call total and collectively overshoot by N-1 requests.

        The note is keyed on the metrics object the call will populate, never on the model (shared
        by reference across concurrent requests) and never in a ContextVar (survives the tool
        executor handoff, which on a tenancy boundary is a cross-tenant leak).
        """
        if request.tenant_id is None:
            return
        estimate = max(0, request.estimated_input_tokens or 0)
        self.enforcer.reserve(request.tenant_id, limits, estimated_tokens=estimate, agent_id=request.agent_id)

        # Keyed on the resolved model. An earlier version read
        # `getattr(result.model, "metrics", None)` -- `Model` has no such attribute, so it returned
        # early every time and NOTHING ever reconciled: a 1300-token call left the counter at its
        # 120-token estimate. The resolved model is per-request (each provider builds it fresh via
        # `_construct`), so it is a safe carrier where `agent.model` -- shared by reference through
        # `deep_copy` -- would not be.
        try:
            from egpt.providers.budget_runtime import BudgetReservation, record_reservation

            record_reservation(
                result.model,
                BudgetReservation(
                    tenant_id=request.tenant_id,
                    estimated_tokens=estimate,
                    enforcer=self.enforcer,
                    limits=limits,
                    agent_id=request.agent_id,
                    # Starts at the estimate, which is what `reserve` just charged. The first turn
                    # then settles estimate -> actual, and each later turn charges its own delta.
                    settled_tokens=estimate,
                ),
            )
        except Exception as e:
            log_debug(f"Could not record the budget reservation for reconciliation: {e}")

    def _delegate(self, request: ModelResolveRequest) -> ModelResolveResult:
        if supports_detailed_resolve(self.inner):
            return self.inner.resolve_detailed(request)  # type: ignore[attr-defined,no-any-return]
        model = self.inner.resolve(request)
        caps = self.inner.capabilities()
        return ModelResolveResult(
            model=model,
            mode=caps.modes[0] if caps.modes else None,  # type: ignore[arg-type]
            provider_key=self._registry_key_for(model),
            reason="budget: delegated",
        )

    async def _adelegate(self, request: ModelResolveRequest) -> ModelResolveResult:
        aresolve_detailed = getattr(self.inner, "aresolve_detailed", None)
        if aresolve_detailed is not None and supports_detailed_resolve(self.inner):
            return await aresolve_detailed(request)  # type: ignore[no-any-return]
        if supports_detailed_resolve(self.inner):
            return self.inner.resolve_detailed(request)  # type: ignore[attr-defined,no-any-return]
        model = await aresolve_via(self.inner, request)
        caps = self.inner.capabilities()
        return ModelResolveResult(
            model=model,
            mode=caps.modes[0] if caps.modes else None,  # type: ignore[arg-type]
            provider_key=self._registry_key_for(model),
            reason="budget: delegated",
        )

    def _with_headroom(self, request: ModelResolveRequest, limits: BudgetLimits) -> ModelResolveRequest:
        """Tell the inner provider how much room is left, so it can decide for itself."""
        from dataclasses import replace

        if limits.max_tokens is None:
            return request
        try:
            used = self.enforcer.usage(request.tenant_id or "", limits).get("tokens", 0)
        except Exception as e:
            # `check` and `reserve` deliberately fail open, and the module docstring makes a point
            # of it -- but `usage` has no guard of its own, so a store outage raised straight out of
            # `resolve_detailed` immediately after `check` had gone to the trouble of allowing. The
            # stated guarantee was not true as written.
            log_warning(f"Budget headroom read failed; resolving without it: {e}")
            return request
        return replace(request, budget_remaining_tokens=max(0, limits.max_tokens - used))

    def _should_downgrade(self, result: ModelResolveResult, request: ModelResolveRequest, limits: BudgetLimits) -> bool:
        """Whether to re-resolve without escalation.

        Three conditions, all required: the request actually escalated, the reason was a
        *threshold* rather than a capability gap, and the remaining headroom is thin. Anything
        else serves the answer escalation gave.
        """
        if result.escalated_from is None or limits.max_tokens is None:
            return False
        if result.reason in _CAPABILITY_REASONS:
            # The load-bearing case. Downgrading a vision request onto a text-only model produces
            # a silent wrong answer or an unsupported-content error from the provider; refusing or
            # serving are both honest, and this is not the layer that should choose.
            return False
        remaining = self._with_headroom(request, limits).budget_remaining_tokens
        if remaining is None:
            return False
        # "Thin" is a quarter of the window's allowance. A fixed fraction rather than a tunable:
        # one more knob for a heuristic that only decides between two models the tenant can both
        # afford is not worth the configuration surface.
        return remaining < (limits.max_tokens // 4)

    def _denial(self, request: ModelResolveRequest, decision) -> Exception:
        """The typed error for a refusal (AC5).

        Neither type subclasses ``ModelProviderError``, which is deliberate and tested: 429 is
        classified retryable, so the retry loop would re-attempt a denial that cannot succeed, and
        ``fallback.py`` would answer "out of budget" by calling a costlier model.
        """
        if decision.reason == "request_rate_exceeded":
            return RateLimitExceededError(
                f"Tenant {request.tenant_id!r} exceeded its request rate "
                f"({decision.used}/{decision.limit} per {decision.window}).",
                tenant_id=request.tenant_id,
                limit=decision.limit,
                window=decision.window,
                retry_after=decision.retry_after,
            )
        return BudgetExceededError(
            f"Tenant {request.tenant_id!r} exhausted its token budget "
            f"({decision.used}/{decision.limit} per {decision.window}).",
            tenant_id=request.tenant_id,
            limit=decision.limit,
            used=decision.used,
            window=decision.window,
            retry_after=decision.retry_after,
        )

    def capabilities(self) -> ModelCapabilities:
        """The inner stack's capabilities, unchanged.

        A budget constrains *how much*, never *what* -- so reporting anything of its own here
        would misdescribe the stack to the escalation rules that read it.
        """
        try:
            return self.inner.capabilities()
        except Exception as e:
            log_warning(f"Inner provider capabilities unavailable: {e}")
            return ModelCapabilities()


def _config_names_a_budget(provider: Any) -> bool:
    """Whether any policy in the wrapped stack carries a budget.

    Duck-typed on ``config.policies`` for the same reason ``_policy_budget`` is: this provider
    composes any ``ModelProvider``, and importing a concrete sibling would make the stack order a
    dependency rather than a choice.
    """
    for candidate in (provider, getattr(provider, "primary", None)):
        config = getattr(candidate, "config", None)
        if config is None:
            continue
        if getattr(config, "default", None) is not None and getattr(config.default, "budget", None) is not None:
            return True
        for policy in (getattr(config, "policies", None) or {}).values():
            if getattr(policy, "budget", None) is not None:
                return True
    return False


def replace_can_escalate(request: ModelResolveRequest) -> ModelResolveRequest:
    """A copy that cannot escalate, used for a budget downgrade.

    ``max_escalations=0`` rather than a new flag: the contract already expresses "no escalation"
    that way, and ``can_escalate`` is derived from it.
    """
    from dataclasses import replace

    return replace(request, max_escalations=0, escalation_level=0)
