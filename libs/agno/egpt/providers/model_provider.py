"""The dual-mode model-resolution seam (EGPT-3.1).

Epic 3 needs one place that answers "which model serves this request", so that Mode A
(in-VPC SLMs over an OpenAI-compatible endpoint) and Mode B (frontier providers, optionally
behind a gateway) are a configuration swap rather than a code change.

This module is the contract four other tickets code against, so it is deliberately thin:

* It imports ``egpt.models.base`` for the ``Model`` return type, ``egpt.metrics`` for the
  ``ProviderMode`` vocabulary (its core home since the EGPT-1.7c follow-up), and nothing else
  from the framework. In particular it never imports a concrete provider -- ``providers/`` sits in the
  same "may not name an integration" cell of ADR 0001's matrix as ``registry/``, and
  ``tests/unit/registry/test_discovery_laziness.py`` fails any seam that eagerly imports a
  provider class. Construction goes through ``egpt.registry.discovery.construct``, imported
  inside the function that uses it.

* ``ModelProvider`` is a ``Protocol``, not an ABC, for the same reason
  ``registry/protocols.py`` is one: the callers (``egpt/agent/_init.py``) must depend on a
  *shape*. An out-of-tree provider then needs no inheritance, and core needs no import of any
  concrete provider to type-check the call. ``BaseModelProvider`` below is an optional
  convenience base for the providers this repository ships; satisfying the Protocol directly
  is equally valid.

FROZEN as of EGPT-3.1. EGPT-3.2 (#64), 3.3 (#65), 3.4 (#66) and 3.5 (#67) were built in
parallel against this file. Additive changes only: new fields carry defaults, and no field is
removed or retyped without re-cutting all four branches.

EGPT-3.6 (#68) adds `budget_remaining_tokens` / `max_tokens_this_run` under that rule -- both
default to None, both join `__hash__`, and neither touches the reserved `budget_remaining_usd` /
`max_cost_usd` pair, which #163 now gives a real source and which a future USD budget will want
for their literal meaning.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any, Mapping, Optional, Protocol, Tuple, runtime_checkable

# ProviderMode is re-exported from its core home (see the enum's docstring for the
# EGPT-1.7c move): every services-layer import site keeps working, while integrations
# import it from egpt.metrics.
from egpt.metrics import ProviderMode
from egpt.models.base import Model

__all__ = [
    "AsyncModelProvider",
    "BaseModelProvider",
    "DetailedModelProvider",
    "ModelCapabilities",
    "ModelProvider",
    "ModelProviderConfigError",
    "ModelResolveRequest",
    "ModelResolveResult",
    "ProviderMode",
    "aresolve_via",
    "aresolve_via_detailed",
    "resolve_via_detailed",
    "supports_detailed_resolve",
]


@dataclass(frozen=True)
class ModelResolveRequest:
    """Everything a provider is allowed to decide on.

    Frozen so two concurrent requests can never share mutable state -- the same reason
    ``RequestContext`` in ``egpt/factory/utils.py`` is frozen. ``metadata`` is normalized to a
    read-only mapping in ``__post_init__`` so "frozen" is not a lie.

    Every field has a default. A provider must behave sensibly given ``ModelResolveRequest()``:
    that is the "no mode configured" path, and it is what lets EGPT-3.1 attach a seam that
    changes nothing until something is configured.
    """

    # -- tenancy / identity ------------------------------------------------
    # There is no tenant concept in this tree yet (EGPT-3.2 documents the gap): today this is
    # populated from ``RequestContext.user_id`` or a JWT claim. Kept as its own field, distinct
    # from ``user_id``, so that when Lane A's tenant column lands the wiring changes in one
    # place and every provider written against this contract keeps working.
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    agent_id: Optional[str] = None

    # -- what the caller asked for -----------------------------------------
    # An explicit ``Agent(model=...)``. A provider that receives a non-None ``requested`` and
    # has no policy overriding it MUST return it unchanged: user intent outranks configuration.
    requested: Optional[Model] = None
    # Which functional slot is being resolved. Mirrors ``egpt.metrics.ModelType`` values but is
    # typed as ``str`` so a provider can name a role the enum has not grown yet.
    role: str = "model"

    # -- mode selection (EGPT-3.2) -----------------------------------------
    # None means "the provider decides from its own configuration". An explicit value is a
    # caller override and is authoritative.
    mode: Optional[ProviderMode] = None

    # -- Mode A adapter selection (EGPT-3.3) -------------------------------
    # A LoRA adapter name. vLLM serves adapters as first-class model ids, so a Mode A provider
    # renders this either as the request model id or into ``extra_body``. Ignored by Mode B.
    adapter: Optional[str] = None

    # -- escalation (EGPT-3.5) ---------------------------------------------
    # 0 on the first attempt. A rule-based escalating provider increments it and re-resolves,
    # so a provider can see "this is attempt 2" without any hidden state.
    escalation_level: int = 0
    max_escalations: int = 1
    # Free-form so EGPT-3.5 can name its own rules without this file enumerating them.
    escalation_reason: Optional[str] = None

    # -- signals a rule may read (EGPT-3.5) --------------------------------
    # Cheap, already-known request shape. Deliberately *not* the messages themselves: a
    # provider that reads message content is doing confidence routing, which EGPT-3.5 puts
    # explicitly out of scope.
    estimated_input_tokens: Optional[int] = None
    tool_count: int = 0
    requires_structured_output: bool = False
    requires_vision: bool = False

    # -- budget hook (reserved) --------------------------------------------
    # Not consumed by any Epic 3 ticket. Present so a future budget ticket (EGPT-3.6) is an
    # additive read rather than a signature change across five providers.
    budget_remaining_usd: Optional[float] = None
    max_cost_usd: Optional[float] = None

    # -- token budget (EGPT-3.6) -------------------------------------------
    # Deliberately a *separate* pair from the reserved `_usd` fields above rather than a
    # reinterpretation of them: #163 now populates `MessageMetrics.cost`, so a USD budget has a
    # real source and will want those fields for their literal meaning. Conflating the two would
    # make a dollar budget indistinguishable from a token budget at every reader.
    #
    # `budget_remaining_tokens` is what the tenant has left in the current window; a provider
    # reads it to decide whether it can afford the tier it would otherwise pick.
    # `max_tokens_this_run` is the per-run ceiling, which a downgrade sets when headroom is thin.
    budget_remaining_tokens: Optional[int] = None
    max_tokens_this_run: Optional[int] = None

    # -- escape hatch -------------------------------------------------------
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Same trick as ``registry.discovery.Registration``: freeze the mapping so a provider
        # holding the request cannot mutate what the caller passed.
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        # Coerce ``mode`` at the boundary: every provider compares it with ``is``, and a
        # string that arrived from JSON, a settings value or a serialized run record is
        # equal-but-not-identical to the enum member. ProviderMode is a str Enum precisely so
        # 'local' validates by construction -- so honour that here, once, for every consumer.
        if self.mode is not None and not isinstance(self.mode, ProviderMode):
            try:
                object.__setattr__(self, "mode", ProviderMode(str(self.mode).strip().lower()))
            except ValueError:
                known = ", ".join(repr(m.value) for m in ProviderMode)
                raise ModelProviderConfigError(
                    f"Unknown mode {self.mode!r}. Expected one of: {known}.",
                    field_name="mode",
                ) from None
        if self.escalation_level < 0:
            raise ValueError("escalation_level must be >= 0")
        if self.max_escalations < 0:
            raise ValueError("max_escalations must be >= 0")

    # ``frozen=True`` with the default ``eq=True`` generates a real ``__hash__``, so the type
    # *presents* as hashable -- but ``metadata`` is a mappingproxy and ``requested`` holds a
    # ``Model`` (an eq=True dataclass, so ``__hash__ = None``), and hashing either raises. A
    # provider memoizing resolution (an ``lru_cache`` over a policy lookup is the obvious
    # implementation for EGPT-3.2) would hit that at agent-construction time.
    #
    # Every component here must be hashed by *value*, because the generated ``__eq__`` compares
    # by value and ``a == b`` must imply ``hash(a) == hash(b)``. Hashing ``requested`` by
    # address (``id()``) satisfies the letter of "does not raise" and breaks that invariant:
    # two equal requests would hash differently, so an ``lru_cache`` never hits and a
    # hand-rolled dict grows one entry per call -- the same failure the hash was added to
    # prevent, one layer down. ``requested.id`` is the model's own value-identity.
    #
    # ``metadata`` is excluded outright: unequal-but-same-hash is permitted, so dropping a
    # field is safe where hashing it by address is not.
    def __hash__(self) -> int:
        return hash(
            (
                self.tenant_id,
                self.user_id,
                self.session_id,
                self.agent_id,
                self.requested.id if self.requested is not None else None,
                self.role,
                self.mode,
                self.adapter,
                self.escalation_level,
                self.max_escalations,
                # EGPT-3.6. Both join the hash for the reason the comment above gives: a field
                # that participates in `==` (every dataclass field does) must participate here, or
                # two unequal requests collide and a cache serves one tenant's answer to another.
                self.budget_remaining_tokens,
                self.max_tokens_this_run,
            )
        )

    def escalated(self, *, reason: str) -> "ModelResolveRequest":
        """A copy one escalation level higher. The only mutation Epic 3 needs."""
        return replace(
            self,
            escalation_level=self.escalation_level + 1,
            escalation_reason=reason,
            # An escalation re-opens the decision: the pinned model was the thing that proved
            # insufficient, so it must not re-pin the escalated resolution.
            requested=None,
        )

    @property
    def can_escalate(self) -> bool:
        return self.escalation_level < self.max_escalations


@dataclass(frozen=True)
class ModelCapabilities:
    """What a provider can do, answerable without resolving anything.

    Read by callers that must decide *before* a request whether a provider is usable -- e.g.
    EGPT-3.5 asking "does my frontier tier support vision?" -- and by diagnostics. It must
    never perform I/O or import an SDK: ``capabilities()`` is called on paths where a missing
    optional dependency has to stay a non-event.

    ``model_ids`` is advisory and may be empty: a vLLM deployment's served ids are only
    knowable by asking the server, which this must not do.
    """

    modes: Tuple[ProviderMode, ...] = ()
    # Registry keys ("vllm", "openai-like", "anthropic") this provider may build. Cheap to
    # produce -- these are strings, and naming one imports nothing.
    provider_keys: Tuple[str, ...] = ()
    model_ids: Tuple[str, ...] = ()

    supports_adapters: bool = False  # EGPT-3.3 LoRA selection
    supports_escalation: bool = False  # EGPT-3.5
    supports_tools: bool = True
    supports_structured_output: bool = True
    supports_vision: bool = False
    supports_streaming: bool = True
    # A gateway sits in front (Portkey, LiteLLM). EGPT-3.4.
    gateway: Optional[str] = None
    # True when this provider only ever talks to an in-VPC endpoint. EGPT-3.3's "never default
    # to a cloud host" is asserted against this plus the resolved base_url, not against it
    # alone.
    egress_free: bool = False

    # Context window of the default model, when the provider can state one without asking a
    # server. EGPT-3.5's "input too large" rule reads it; None means "unknown, do not route on
    # this signal".
    context_window: Optional[int] = None

    notes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "notes", MappingProxyType(dict(self.notes)))

    # See ModelResolveRequest.__hash__: frozen=True advertises hashability that the mappingproxy
    # in ``notes`` would break. Hash the capability flags, which are what a caller comparing or
    # caching capabilities actually cares about.
    def __hash__(self) -> int:
        return hash(
            (
                self.modes,
                self.provider_keys,
                self.model_ids,
                self.supports_adapters,
                self.supports_escalation,
                self.supports_tools,
                self.supports_structured_output,
                self.supports_vision,
                self.supports_streaming,
                self.gateway,
                self.egress_free,
                self.context_window,
            )
        )


@dataclass(frozen=True)
class ModelResolveResult:
    """A resolved model plus why it was chosen.

    ``resolve`` returns a bare ``Model`` because that is what every call site needs and forcing
    a wrapper on them would ripple. This richer result exists for ``resolve_detailed``, which
    EGPT-3.5's escalation logging and any future tracing hook read.
    """

    model: Model
    mode: ProviderMode
    # The registry key the model resolved through -- the exact string
    # ``discovery.resolve("model", key)`` accepts. ``None`` when the model was caller-supplied
    # and no registry declaration matches its class: a display name that merely looks like a
    # key would crash any consumer that feeds it back through the seam.
    provider_key: Optional[str]
    adapter: Optional[str] = None
    escalated_from: Optional[str] = None  # provider_key we escalated away from
    reason: Optional[str] = None
    gateway: Optional[str] = None


class ModelProviderConfigError(ValueError):
    """A provider's configuration is invalid or incomplete.

    ``ValueError`` so EGPT-3.2's "invalid configuration is rejected with a clear error" lands
    on the exception type every existing model-resolution failure in ``egpt/models/utils.py``
    already raises, and existing ``pytest.raises(ValueError)`` assertions keep holding.

    Deliberately *not* named ``ModelProviderError``: ``egpt.exceptions.ModelProviderError``
    already owns that name and is caught by the fallback machinery in
    ``egpt/models/fallback.py``. A deployment mistake surfaced at configuration time must not
    be swallowed as a provider API failure and silently fall back to another model.
    """

    def __init__(self, message: str, *, field_name: Optional[str] = None) -> None:
        super().__init__(message)
        self.field_name = field_name


@runtime_checkable
class ModelProvider(Protocol):
    """Answers "which Model serves this request".

    Implementations must be safe to call concurrently and must not mutate the request.
    Returning the *same* ``Model`` instance across calls is permitted only when that instance
    is not request-specific; anything that varies per request (a LoRA adapter, a per-tenant
    base_url) must return a fresh instance, because ``Model`` is a mutable dataclass and
    orchestration assigns to ``model.model_type``.

    **A resolve must return a per-request ``Model`` instance.** EGPT-3.6 keys a budget reservation
    on the returned model to settle it against real usage after the call, so a provider that returns
    a cached or singleton model would have two concurrent requests share one reservation and
    mis-settle both, silently. Every in-tree provider satisfies this by construction --
    ``discovery.construct`` caches nothing -- but this Protocol exists for third-party
    implementations, so the requirement is stated rather than assumed.
    """

    #: Stable identifier, for logs and registry keying. e.g. "mode-a", "mode-b".
    name: str

    def resolve(self, request: ModelResolveRequest) -> Model:
        """Return the ``Model`` to use. Never ``None``.

        Raises:
            ModelProviderConfigError: the provider is misconfigured for this request
                (unknown mode, missing base_url, unknown adapter).
            ImportError: the chosen backend's optional SDK is not installed. Propagated
                unwrapped, matching ``discovery.resolve``.
        """
        ...

    def capabilities(self) -> ModelCapabilities:
        """What this provider can do. Pure, cheap, imports no SDK."""
        ...


@runtime_checkable
class DetailedModelProvider(ModelProvider, Protocol):
    """A provider that can also explain its choice. Optional.

    ``isinstance`` against a runtime_checkable Protocol is a structural ``hasattr`` check, so
    it cannot tell a real implementation from an inherited stub -- and ``BaseModelProvider``
    defines ``resolve_detailed`` unconditionally (it raises). Every subclass would therefore
    report as detail-capable, and a caller gating on this Protocol would take the detailed
    branch and die with ``NotImplementedError``.

    Use :func:`supports_detailed_resolve` rather than ``isinstance`` for that decision.
    """

    def resolve_detailed(self, request: ModelResolveRequest) -> ModelResolveResult: ...


@runtime_checkable
class AsyncModelProvider(ModelProvider, Protocol):
    """A provider with a native async path. Optional.

    CLAUDE.md requires both sync and async variants for public methods, and ``resolve`` is not
    the exempt pure-construction case: it may raise ``ImportError`` from SDK loading, EGPT-3.2
    reads a per-tenant policy and EGPT-3.4 may probe a gateway. Under ``await agent.arun(...)``
    a synchronous fetch inside ``resolve`` blocks the event loop, stalling every other in-flight
    request in that worker behind one model resolution.

    Deliberately a *separate* Protocol rather than a second method on ``ModelProvider``: adding
    a required member would mean every out-of-tree provider implementing only ``resolve`` stops
    satisfying the contract -- a silent break for exactly the third-party integrations the
    structural Protocol exists to support. Callers use :func:`aresolve_via`, which uses a native
    async path when there is one and otherwise falls back to the sync call.
    """

    async def aresolve(self, request: ModelResolveRequest) -> Model: ...


async def aresolve_via(provider: Any, request: ModelResolveRequest) -> Model:
    """Resolve through ``provider``, preferring its native async path.

    The one call every async caller should use. A provider that does real I/O implements
    ``aresolve`` and gets it awaited; one that only reads local config -- every in-tree provider
    today -- is called synchronously, which costs nothing and blocks nothing.
    """
    if isinstance(provider, AsyncModelProvider):
        return await provider.aresolve(request)
    return provider.resolve(request)  # type: ignore[no-any-return]


def resolve_via_detailed(provider: Any, request: ModelResolveRequest) -> Tuple[Model, Optional[ModelResolveResult]]:
    """Resolve through ``provider``, keeping the detailed result when the provider offers one.

    The per-run hook needs two things from a resolution: the model to serve, and -- when the
    provider composes tiers -- *why* that model was chosen, so an escalation can be written
    into the run record rather than only into the log (G6). ``resolve`` discards the second;
    ``resolve_detailed`` is optional on the Protocol. This is the one place that reconciles
    them: a provider with a real ``resolve_detailed`` (see ``supports_detailed_resolve``) is
    asked for the detail, and the model is read off it, so nothing resolves twice. A provider
    with only ``resolve`` returns ``(model, None)`` -- ``None`` rather than a synthesised
    ``ModelResolveResult``, because that dataclass requires a ``mode`` and inventing one would
    put a fact in the run record that no provider stated.
    """
    if supports_detailed_resolve(provider):
        detail: ModelResolveResult = provider.resolve_detailed(request)
        return detail.model, detail
    return provider.resolve(request), None


async def aresolve_via_detailed(
    provider: Any, request: ModelResolveRequest
) -> Tuple[Model, Optional[ModelResolveResult]]:
    """Async counterpart of ``resolve_via_detailed``; same preference for a provider's own async path.

    Prefers ``aresolve_detailed`` when the provider defines one (every ``BaseModelProvider``
    does, and the ones that do I/O override it), so a Mode B gateway probe stays off the event
    loop on the detailed path exactly as ``aresolve_via`` keeps it off on the bare-model path.
    """
    if supports_detailed_resolve(provider):
        adetailed = getattr(provider, "aresolve_detailed", None)
        if adetailed is not None:
            detail: ModelResolveResult = await adetailed(request)
        else:
            detail = provider.resolve_detailed(request)
        return detail.model, detail
    return await aresolve_via(provider, request), None


def supports_detailed_resolve(provider: Any) -> bool:
    """Whether ``provider`` really implements ``resolve_detailed``, stub excluded.

    The check ``isinstance(provider, DetailedModelProvider)`` cannot answer this: it is
    structural, and ``BaseModelProvider``'s stub satisfies it. Comparing the bound function
    against the base's own implementation distinguishes an override from the inherited raiser,
    which is what a caller deciding between ``resolve_detailed`` and plain ``resolve`` needs --
    EGPT-3.5's escalation logging being the motivating case.
    """
    impl = getattr(type(provider), "resolve_detailed", None)
    if impl is None:
        return False
    return impl is not BaseModelProvider.resolve_detailed


class BaseModelProvider:
    """Shared plumbing for in-tree providers.

    Not an ``abc.ABC``: ``@runtime_checkable Protocol`` already gives callers the
    ``isinstance`` check they want, and an ABC would add a second, redundant inheritance
    requirement that out-of-tree providers cannot satisfy. Implementing ``ModelProvider``
    directly is equally valid -- nothing type-checks against this class.
    """

    name: str = "model-provider"

    def resolve(self, request: ModelResolveRequest) -> Model:
        return self.resolve_detailed(request).model

    async def aresolve(self, request: ModelResolveRequest) -> Model:
        """Delegates to the sync path, which is correct for a provider that only reads local
        config -- every in-tree provider today. One that does real I/O (a per-tenant policy row,
        a gateway probe) overrides this; leaving the default in place would block the event
        loop, which is the whole reason the async variant exists.
        """
        return self.resolve(request)

    def resolve_detailed(self, request: ModelResolveRequest) -> ModelResolveResult:
        raise NotImplementedError(f"{type(self).__name__} must implement resolve_detailed() (or override resolve()).")

    async def aresolve_detailed(self, request: ModelResolveRequest) -> ModelResolveResult:
        """Async counterpart of ``resolve_detailed``. Same delegation rationale as ``aresolve``."""
        return self.resolve_detailed(request)

    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities()

    @staticmethod
    def _registry_key_for(model: Model) -> Optional[str]:
        """The discovery key that registers ``model``'s class, or ``None`` when unregistered.

        ``Model.provider`` is a human-facing display name ('VLLM', 'OpenAI'), not the lowercase
        key ``discovery.resolve`` accepts, so it cannot stand in for one: feeding it back through
        the seam raises unknown-provider on a model that resolved fine, and metrics split 'VLLM'
        from 'vllm' for a single backend.

        Two match strategies, because the registry holds two kinds of row:

        * A runtime registration (tests, plugins) stores the class itself, so match on identity.
        * A lazy declaration stores ``(module, attr)`` strings -- and those name the *package
          that re-exports* the class, not the module that defines it
          (``egpt.integrations.models.vllm`` vs ``...vllm.vllm``). Comparing them to
          ``cls.__module__``/``__qualname__`` therefore fails for 40 of the 42 in-tree model
          registrations, so resolve the declaration and compare by identity instead. That also
          follows aliases, and cannot confuse same-named classes in sibling modules the way a
          bare ``__qualname__`` comparison can.

        Only already-imported modules are inspected (``sys.modules.get``, never ``import_module``):
        the caller handed us a live instance, so the module defining its class is loaded by
        construction, and probing the rest of the table would drag every optional SDK into the
        process -- exactly what the lazy registry exists to avoid. ``test_discovery_laziness.py``
        pins that this stays true.
        """
        import sys

        from egpt.registry import discovery

        cls = type(model)
        # entries(), not declarations(): the guard-facing declarations view deliberately omits
        # runtime registrations, which are the ones tests and plugins rely on.
        for key, registration in discovery.entries("model").items():
            if registration.obj is not None and registration.obj is cls:
                return key
            if registration.module is None or registration.attr is None:
                continue
            target: Any = sys.modules.get(registration.module)
            if target is None:
                continue
            try:
                for part in registration.attr.split("."):
                    target = getattr(target, part)
            except AttributeError:
                continue
            if target is cls:
                return key
        return None

    @staticmethod
    def _construct(provider_key: str, model_id: str, **config: Any) -> Model:
        """Build a model through the discovery seam.

        ``discovery.construct`` rather than ``egpt.models.utils._get_model_class``: the latter
        constructs with ``id=`` only, so it cannot carry a ``base_url`` or gateway config.

        Imported inside the function, never at module scope, so importing ``providers/`` never
        touches the registry table -- ``test_discovery_laziness.py`` pins that.
        """
        from egpt.registry import discovery

        model = discovery.construct("model", provider_key, id=model_id, **config)
        return model  # type: ignore[no-any-return]
