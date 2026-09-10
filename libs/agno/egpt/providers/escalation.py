"""Rule-based escalation from a small model to a frontier one (EGPT-3.5).

A deployment runs most traffic on the cheap in-VPC tier and needs the expensive one only for
the requests the cheap tier cannot serve. This module decides that *before* the call, from the
request's shape alone.

Two mechanisms, deliberately distinct, and both must keep working:

* **Escalation** (this module) is a decision made UP FRONT. The request looks too hard for the
  primary -- more input than its context window, an image it cannot see -- so the frontier tier
  serves it instead. Nothing has failed; nothing is retried.

* **Fallback** (``egpt/models/fallback.py``, unmodified) is a REACTION to a call that already
  failed. ``to_fallback_config`` below composes the frontier tier into that existing machinery
  so a primary that escalation cleared as adequate, and which then dies at runtime, still lands
  on the frontier model.

Confidence-based routing is explicitly out of scope for v1 and deferred to v2: estimating
"will the small model get this right" from the prompt is unsolved, and a wrong guess silently
degrades answer quality rather than failing loudly. The rules here therefore read only the
cheap, structural signals the frozen contract exposes -- never message content.
``tests/unit/providers/test_escalation.py::test_no_rule_reads_message_content`` guards the
boundary mechanically, so it is a property of the code rather than a promise in a docstring.

No provider class is imported at module scope (ADR 0001; pinned by
``tests/unit/registry/test_discovery_laziness.py``): ``EscalatingModelProvider`` composes two
``ModelProvider`` instances handed to it, typed against the Protocol. That also means the Mode
A and Mode B bindings are never named here -- escalation works for *any* provider pair, and is
testable with fakes.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Optional, Tuple

from egpt.providers.events import EscalationEvent, log_escalation_event
from egpt.providers.model_provider import (
    BaseModelProvider,
    ModelCapabilities,
    ModelProvider,
    ModelResolveRequest,
    ModelResolveResult,
    ProviderMode,
    aresolve_via,
    supports_detailed_resolve,
)
from egpt.utils.log import log_warning

if TYPE_CHECKING:
    from egpt.models.base import Model
    from egpt.models.fallback import FallbackConfig

__all__ = [
    "CAPABILITY_RULES",
    "DEFAULT_RULES",
    "ESCALATE_METADATA_KEY",
    "EscalatingModelProvider",
    "EscalationRule",
    "explicit_policy_flag",
    "input_exceeds_context_window",
    "structured_output_unsupported",
    "tool_count_over",
    "too_many_tools",
    "tools_unsupported",
    "vision_unsupported",
]

#: A caller sets ``metadata={ESCALATE_METADATA_KEY: True}`` to force escalation for one
#: request -- the "explicit policy flag" rule. A key in the request's own metadata rather than
#: a settings field: escalation policy is per-request, and ``settings/providers.py`` is owned
#: line-by-line by three sibling tickets this cycle.
ESCALATE_METADATA_KEY = "escalate"

#: Above this many tools the primary is assumed to lose track of its tool choice. Overridable
#: per provider instance via ``tool_threshold``; the module-level rule uses this default.
DEFAULT_TOOL_THRESHOLD = 8


#: A rule is a pure predicate over the request and the primary's capabilities. It returns a
#: reason string when it fires and ``None`` when it does not, so the reason that lands in the
#: log is written by the rule that caused it rather than reconstructed by the caller.
EscalationRule = Callable[[ModelResolveRequest, ModelCapabilities], Optional[str]]


def input_exceeds_context_window(request: ModelResolveRequest, caps: ModelCapabilities) -> Optional[str]:
    """The request does not fit in the primary's context window.

    Both signals are optional and a missing one means "unknown": the contract says an unknown
    ``context_window`` must not be routed on, and a provider that cannot state its window
    (a vLLM deployment whose served ids are only knowable by asking the server) must not be
    escalated away from on a guess.
    """
    tokens = request.estimated_input_tokens
    if tokens is None or caps.context_window is None:
        return None
    if tokens > caps.context_window:
        return "input_exceeds_context_window"
    return None


def vision_unsupported(request: ModelResolveRequest, caps: ModelCapabilities) -> Optional[str]:
    """The request carries images and the primary is text-only.

    A hard capability gap rather than a quality judgement: the primary cannot serve this at all.
    """
    if request.requires_vision and not caps.supports_vision:
        return "vision_unsupported"
    return None


def tools_unsupported(request: ModelResolveRequest, caps: ModelCapabilities) -> Optional[str]:
    """The request needs tool calling and the primary has none."""
    if request.tool_count > 0 and not caps.supports_tools:
        return "tools_unsupported"
    return None


def structured_output_unsupported(request: ModelResolveRequest, caps: ModelCapabilities) -> Optional[str]:
    """The request needs a schema-constrained response and the primary cannot produce one."""
    if request.requires_structured_output and not caps.supports_structured_output:
        return "structured_output_unsupported"
    return None


def tool_count_over(threshold: int) -> EscalationRule:
    """Build a rule firing above ``threshold`` tools.

    Unlike the rules above this is a threshold, not a capability gap, so it is the one rule a
    deployment is most likely to retune. The threshold is bound into the returned closure at
    construction rather than read from module state, so a provider's ``tool_threshold`` is
    honoured no matter how the rule is wrapped, aliased or re-exported.
    """

    def too_many_tools(request: ModelResolveRequest, caps: ModelCapabilities) -> Optional[str]:
        if request.tool_count > threshold:
            return "too_many_tools"
        return None

    return too_many_tools


#: The default-threshold instance, so ``DEFAULT_RULES`` is a ready-to-use tuple.
too_many_tools = tool_count_over(DEFAULT_TOOL_THRESHOLD)


def explicit_policy_flag(request: ModelResolveRequest, caps: ModelCapabilities) -> Optional[str]:
    """The caller asked for the frontier tier outright.

    The escape hatch for everything the structural signals cannot express -- a tenant on a
    premium plan, a known-hard workflow step. Truthiness rather than ``is True`` so a config
    layer that renders the flag as ``"1"`` behaves as its author intended.

    **Per-request only when set per request.** ``resolve_run_options`` merges run metadata with
    the component's and lets the *component* win, so ``Agent(metadata={"escalate": True})``
    escalates every run and a call-site ``metadata={"escalate": False}`` cannot switch it off --
    the component's value overwrites it before the request is built. That precedence predates this
    rule and is not changed here; it is documented because the flag reads as a per-request switch
    and at component level it is a one-way latch. Set it per run to get per-run behaviour.
    """
    if request.metadata.get(ESCALATE_METADATA_KEY):
        return "explicit_policy_flag"
    return None


#: Rules describing something the primary provably *cannot* do. When one of these fires and the
#: escalation ceiling blocks the escalation, the request is stranded on a model that will fail
#: or silently degrade -- so that case is logged as a warning rather than passing in silence.
CAPABILITY_RULES: Tuple[EscalationRule, ...] = (
    input_exceeds_context_window,
    vision_unsupported,
    tools_unsupported,
    structured_output_unsupported,
)

#: Evaluated in order; the first to fire wins and names the reason. Capability gaps come before
#: the tunable threshold so a log line says *why* the primary was actually unusable rather than
#: reporting a tool count that merely happened to be checked first.
DEFAULT_RULES: Tuple[EscalationRule, ...] = (
    explicit_policy_flag,
    *CAPABILITY_RULES,
    too_many_tools,
)


@dataclass
class EscalatingModelProvider(BaseModelProvider):
    """Serves from ``primary`` unless a rule says the request needs ``escalation_target``.

    Composes two arbitrary ``ModelProvider`` instances rather than importing the Mode A/B
    bindings: those land in sibling tickets, the Protocol is all this needs, and a provider
    pair injected at construction is what makes the rules testable without a network.

    A dataclass and not a frozen one: it holds two providers and a rule tuple, and a deployment
    that wants to swap the escalation target on a running instance should not have to rebuild
    the object. Nothing here mutates per request -- ``resolve_detailed`` reads only its own
    fields -- so concurrent calls remain safe, as the contract requires.
    """

    primary: ModelProvider
    escalation_target: ModelProvider
    name: str = "escalating"
    rules: Tuple[EscalationRule, ...] = DEFAULT_RULES
    #: Retunes the default ``too_many_tools`` rule for this instance. Ignored when ``rules`` is
    #: given explicitly -- a caller supplying its own rule set has already chosen its thresholds.
    tool_threshold: int = DEFAULT_TOOL_THRESHOLD

    def __post_init__(self) -> None:
        # Rebuild the tunable rule with this instance's threshold bound in, so the override
        # holds regardless of how the rule is later wrapped or aliased.
        if self.rules is DEFAULT_RULES and self.tool_threshold != DEFAULT_TOOL_THRESHOLD:
            self.rules = (explicit_policy_flag, *CAPABILITY_RULES, tool_count_over(self.tool_threshold))

    # -- decision ----------------------------------------------------------

    def _fired_rule(self, request: ModelResolveRequest) -> Optional[Tuple[str, bool]]:
        """The first rule to fire, as ``(reason, is_capability_gap)``, or ``None``.

        Evaluated without reference to the escalation ceiling: whether the primary can serve
        this request is a different question from whether we are allowed to move it, and
        conflating them is what would let a stranded request pass silently.
        """
        caps = self.primary.capabilities()
        for rule in self.rules:
            reason = rule(request, caps)
            if reason is not None:
                return reason, rule in CAPABILITY_RULES
        return None

    def should_escalate(self, request: ModelResolveRequest) -> Optional[str]:
        """The reason this request must escalate, or ``None`` to stay on the primary.

        Pure: no I/O, no SDK import, no message content. Exposed publicly because a caller that
        wants to know the decision without taking it (a dry-run planner, a budget check) should
        not have to resolve a model to find out.
        """
        fired = self._fired_rule(request)
        if fired is None:
            return None
        # At the ceiling the frontier tier is the last stop by construction, so there is nothing
        # above it to escalate to. That is a normal terminal state -- but see resolve_detailed,
        # which warns when the stranded request is one the primary provably cannot serve.
        return fired[0] if request.can_escalate else None

    # -- resolution --------------------------------------------------------

    async def aresolve(self, request: ModelResolveRequest) -> "Model":
        """Async counterpart of ``resolve``.

        Not the pure-construction case CLAUDE.md exempts: the providers this composes may read a
        per-tenant policy row (EGPT-3.2) or probe a gateway (EGPT-3.4), and the base class's
        `aresolve` would run both synchronously on the event-loop thread. Delegating through
        `aresolve_via` lets a provider that implements `aresolve` actually have it called.
        """
        return (await self.aresolve_detailed(request)).model

    async def aresolve_detailed(self, request: ModelResolveRequest) -> ModelResolveResult:
        """Async counterpart of ``resolve_detailed``, with the same decision logic.

        The rule evaluation is pure and cheap; only the two delegations can do I/O, so those are
        what this awaits. Kept as a mirror of the sync body rather than sharing a helper because
        the two differ solely in how `_delegate` is invoked, and threading a sync-or-async
        callable through the decision logic obscured it more than the duplication does.
        """
        fired = self._fired_rule(request)

        if fired is None or not request.can_escalate:
            if fired is not None and fired[1]:
                log_warning(
                    "Escalation ceiling reached at level %d; serving '%s' on '%s' despite %s"
                    % (request.escalation_level, request.role, self.primary.name, fired[0])
                )
            return await self._adelegate(self.primary, request)

        reason = fired[0]
        escalated_request = request.escalated(reason=reason)
        result = await self._adelegate(self.escalation_target, escalated_request)

        log_escalation_event(
            EscalationEvent(
                reason=reason,
                escalated_from=self.primary.name,
                target=self.escalation_target.name,
                escalation_level=escalated_request.escalation_level,
                tenant_id=request.tenant_id,
                user_id=request.user_id,
                session_id=request.session_id,
                agent_id=request.agent_id,
                model_id=getattr(result.model, "id", None),
            )
        )

        return ModelResolveResult(
            model=result.model,
            mode=result.mode,
            provider_key=result.provider_key,
            adapter=result.adapter,
            escalated_from=self._provider_key(self.primary),
            reason=reason,
            gateway=result.gateway,
            # The real depth this resolution reached, off the request that was actually served --
            # not re-derived by a caller from the pre-call request. See `ModelResolveResult`.
            escalation_level=escalated_request.escalation_level,
        )

    def resolve_detailed(self, request: ModelResolveRequest) -> ModelResolveResult:
        fired = self._fired_rule(request)

        if fired is None or not request.can_escalate:
            if fired is not None and fired[1]:
                # The ceiling is blocking an escalation the primary provably cannot absorb: a
                # vision request onto a text-only model, or more input than the context window
                # holds. The call will fail or silently degrade, and this is the only place that
                # is knowable, so it must not pass in silence.
                log_warning(
                    "Escalation ceiling reached at level %d; serving '%s' on '%s' despite %s"
                    % (request.escalation_level, request.role, self.primary.name, fired[0])
                )
            return self._delegate(self.primary, request)

        reason = fired[0]
        # ``escalated`` clears ``requested``: the pinned model is what proved insufficient, so
        # re-pinning it on the escalated resolution would defeat the escalation entirely.
        escalated_request = request.escalated(reason=reason)
        result = self._delegate(self.escalation_target, escalated_request)

        log_escalation_event(
            EscalationEvent(
                reason=reason,
                escalated_from=self.primary.name,
                target=self.escalation_target.name,
                escalation_level=escalated_request.escalation_level,
                tenant_id=request.tenant_id,
                user_id=request.user_id,
                session_id=request.session_id,
                agent_id=request.agent_id,
                model_id=getattr(result.model, "id", None),
            )
        )

        # ``escalated_from`` is the provider key the target reported for the primary's tier,
        # per the contract's "provider_key we escalated away from".
        primary_key = self._provider_key(self.primary)
        return ModelResolveResult(
            model=result.model,
            mode=result.mode,
            provider_key=result.provider_key,
            adapter=result.adapter,
            escalated_from=primary_key,
            reason=reason,
            gateway=result.gateway,
            # The real depth this resolution reached, off the request that was actually served --
            # not re-derived by a caller from the pre-call request. See `ModelResolveResult`.
            escalation_level=escalated_request.escalation_level,
        )

    @staticmethod
    async def _adelegate(provider: ModelProvider, request: ModelResolveRequest) -> ModelResolveResult:
        """Async counterpart of ``_delegate``.

        Routes the detailed path through the provider's own ``aresolve_detailed`` when it has
        one, and the bare path through ``aresolve_via`` -- which is the function that picks
        ``aresolve`` over ``resolve`` when the provider implements it. Calling ``resolve`` here
        would keep a per-tenant policy read or gateway probe on the event-loop thread, which is
        the whole hazard the async variants exist to avoid.
        """
        if supports_detailed_resolve(provider):
            aresolve_detailed = getattr(provider, "aresolve_detailed", None)
            if aresolve_detailed is not None:
                return await aresolve_detailed(request)  # type: ignore[no-any-return]
            return provider.resolve_detailed(request)  # type: ignore[attr-defined,no-any-return]

        model = await aresolve_via(provider, request)
        caps = provider.capabilities()
        return ModelResolveResult(
            model=model,
            mode=caps.modes[0] if caps.modes else ProviderMode.LOCAL,
            provider_key=EscalatingModelProvider._provider_key(provider, caps),
            adapter=request.adapter if caps.supports_adapters else None,
            gateway=caps.gateway,
        )

    @staticmethod
    def _delegate(provider: ModelProvider, request: ModelResolveRequest) -> ModelResolveResult:
        """Resolve through ``provider``, preferring its detailed answer.

        A provider need only satisfy ``ModelProvider``; ``resolve_detailed`` is optional. When
        it is absent the result is synthesized from ``capabilities()`` so the escalation
        decision is still fully describable.
        """
        # `supports_detailed_resolve`, not `getattr`: `BaseModelProvider` defines
        # `resolve_detailed` unconditionally as a stub that raises NotImplementedError, so
        # `getattr` always found it and the branch was always taken. A provider written the way
        # that stub's own message sanctions -- subclass the base, override only `resolve()` --
        # therefore crashed Agent construction with NotImplementedError, or, reached from the run
        # hook, had the error swallowed so escalation silently never happened.
        if supports_detailed_resolve(provider):
            return provider.resolve_detailed(request)  # type: ignore[attr-defined,no-any-return]

        model = provider.resolve(request)
        caps = provider.capabilities()
        return ModelResolveResult(
            model=model,
            # LOCAL when the provider declares no modes. ``modes`` defaults to empty and a
            # provider is not obliged to fill it, so this default is a guess either way -- and
            # guessing FRONTIER would label in-VPC traffic as metered egress, which is the more
            # damaging of the two errors for the cost and egress reporting that reads this.
            mode=caps.modes[0] if caps.modes else ProviderMode.LOCAL,
            provider_key=EscalatingModelProvider._provider_key(provider, caps),
            # Only report an adapter the provider could actually have applied. A LoRA name
            # belongs to the tier that serves it; echoing it onto a provider that does not
            # support adapters would tell EGPT-3.3 the frontier model is running a vLLM LoRA.
            adapter=request.adapter if caps.supports_adapters else None,
            gateway=caps.gateway,
        )

    @staticmethod
    def _provider_key(provider: ModelProvider, caps: Optional[ModelCapabilities] = None) -> str:
        """The registry key a provider builds under, falling back to its name.

        Takes an already-fetched ``caps`` where the caller has one. ``capabilities()`` is
        documented cheap, but only by convention, and a third-party provider that computes it
        should not pay twice on the resolve path.
        """
        if caps is None:
            caps = provider.capabilities()
        return caps.provider_keys[0] if caps.provider_keys else provider.name

    # -- capabilities ------------------------------------------------------

    def capabilities(self) -> ModelCapabilities:
        """The union of both tiers: this provider can serve anything either tier can.

        A caller asking "can you do vision" gets ``True`` when the frontier tier can, because
        a vision request escalates to it -- reporting the primary's answer alone would make the
        escalation invisible to exactly the callers that exist to see it. ``context_window`` is
        the max for the same reason. ``egress_free`` is the opposite: it is only true when
        *both* tiers are, since a request may leave the VPC via the escalation target.
        """
        primary = self.primary.capabilities()
        target = self.escalation_target.capabilities()
        windows = [w for w in (primary.context_window, target.context_window) if w is not None]
        return ModelCapabilities(
            modes=tuple(dict.fromkeys(primary.modes + target.modes)),
            provider_keys=tuple(dict.fromkeys(primary.provider_keys + target.provider_keys)),
            model_ids=tuple(dict.fromkeys(primary.model_ids + target.model_ids)),
            supports_adapters=primary.supports_adapters or target.supports_adapters,
            supports_escalation=True,
            supports_tools=primary.supports_tools or target.supports_tools,
            supports_structured_output=primary.supports_structured_output or target.supports_structured_output,
            supports_vision=primary.supports_vision or target.supports_vision,
            # Streaming is the one capability a caller *relies* on rather than merely wants: a
            # streaming call that lands on a non-streaming tier breaks. Both must support it.
            supports_streaming=primary.supports_streaming and target.supports_streaming,
            gateway=target.gateway or primary.gateway,
            egress_free=primary.egress_free and target.egress_free,
            context_window=max(windows) if windows else None,
        )

    # -- failure fallback --------------------------------------------------

    def to_fallback_config(self, request: Optional[ModelResolveRequest] = None) -> "FallbackConfig":
        """Compose the frontier tier into the existing failure-fallback machinery.

        Escalation cannot help a call that has already failed -- by then the model is chosen and
        in flight. ``egpt/models/fallback.py`` already handles that case, including the
        rate-limit and context-overflow classification, so this returns a ``FallbackConfig``
        holding the escalation target's model rather than reimplementing any of it.

        The frontier model is placed in all three lists: it is the right answer to a 5xx on the
        primary, to a 429 on the primary, and to a context overflow the up-front rule missed
        (which happens whenever ``estimated_input_tokens`` was ``None``). ``get_fallback_models``
        then picks per error class exactly as it does for a hand-written config.

        Calls ``resolve``, so it is *not* the pure-construction case CLAUDE.md exempts --
        ``ato_fallback_config`` below is the async counterpart.
        """
        from egpt.models.fallback import FallbackConfig

        frontier_request = self._frontier_request(request)
        # A separate instance per list: ``Model`` is a mutable dataclass that orchestration
        # assigns ``model_type`` to, so three aliases of one object would let a mutation on the
        # rate-limit path show up on the error path. ``FallbackConfig.resolve_models`` deep-copies
        # and would mask this, but a caller using ``get_fallback_models`` directly never calls it.
        #
        # One resolve plus two copies, not three resolves: `resolve` is the method this codebase
        # documents as potentially reading a per-tenant policy row or probing a gateway, so paying
        # for it three times to obtain three copies of one answer is waste -- and it could return
        # three *different* models if the provider is non-deterministic, which is not what the
        # distinctness argument above asks for.
        resolved = self.escalation_target.resolve(frontier_request)
        return FallbackConfig(
            on_error=[resolved],
            on_rate_limit=[deepcopy(resolved)],
            on_context_overflow=[deepcopy(resolved)],
        )

    def _frontier_request(self, request: Optional[ModelResolveRequest]) -> ModelResolveRequest:
        """The request the frontier tier should see for a failure fallback.

        Strips unconditionally rather than going through ``escalated``: at the escalation ceiling
        that helper is not applicable, and a vLLM LoRA name or a primary-tier pin forwarded to a
        frontier provider is an unknown-model error at best.
        """
        from dataclasses import replace as _replace

        base = request or ModelResolveRequest()
        return _replace(
            base,
            requested=None,
            adapter=None,
            escalation_reason="failure_fallback",
            escalation_level=min(base.escalation_level + 1, base.max_escalations),
        )

    async def ato_fallback_config(self, request: Optional[ModelResolveRequest] = None) -> "FallbackConfig":
        """Async counterpart of ``to_fallback_config``.

        The sync docstring called this pure construction and exempt from CLAUDE.md's sync/async
        rule. That does not hold: it calls ``resolve``, which the contract documents as possibly
        reading a per-tenant policy row (EGPT-3.2) or probing a gateway (EGPT-3.4) -- so on a
        public method reached from an async run path it has something to await after all.
        """
        from egpt.models.fallback import FallbackConfig

        frontier_request = self._frontier_request(request)
        resolved = await aresolve_via(self.escalation_target, frontier_request)
        return FallbackConfig(
            on_error=[resolved],
            on_rate_limit=[deepcopy(resolved)],
            on_context_overflow=[deepcopy(resolved)],
        )
