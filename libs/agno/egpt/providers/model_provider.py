"""The dual-mode model-resolution seam (EGPT-3.1).

Epic 3 needs one place that answers "which model serves this request", so that Mode A
(in-VPC SLMs over an OpenAI-compatible endpoint) and Mode B (frontier providers, optionally
behind a gateway) are a configuration swap rather than a code change.

This module is the contract four other tickets code against, so it is deliberately thin:

* It imports ``egpt.models.base`` for the ``Model`` return type and nothing else from the
  framework. In particular it never imports a concrete provider -- ``providers/`` sits in the
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

FROZEN as of EGPT-3.1. EGPT-3.2 (#64), 3.3 (#65), 3.4 (#66) and 3.5 (#67) are built in
parallel against this file. Additive changes only: new fields carry defaults, and no field is
removed or retyped without re-cutting all four branches.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Optional, Protocol, Tuple, runtime_checkable

from egpt.models.base import Model

__all__ = [
    "BaseModelProvider",
    "DetailedModelProvider",
    "ModelCapabilities",
    "ModelProvider",
    "ModelProviderConfigError",
    "ModelResolveRequest",
    "ModelResolveResult",
    "ProviderMode",
]


class ProviderMode(str, Enum):
    """Which family of backends a request is served from.

    ``str`` mixin so a config value ("local") validates by construction and a serialized run
    record round-trips without a custom encoder -- the same choice ``egpt.metrics.ModelType``
    makes.
    """

    LOCAL = "local"  # Mode A: in-VPC vLLM / Ollama / any OpenAI-compatible endpoint
    FRONTIER = "frontier"  # Mode B: hosted frontier providers, optionally via a gateway


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
    # typed as ``str`` so this module need not import ``egpt.metrics``.
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

    # -- escape hatch -------------------------------------------------------
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Same trick as ``registry.discovery.Registration``: freeze the mapping so a provider
        # holding the request cannot mutate what the caller passed.
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        if self.escalation_level < 0:
            raise ValueError("escalation_level must be >= 0")
        if self.max_escalations < 0:
            raise ValueError("max_escalations must be >= 0")

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


@dataclass(frozen=True)
class ModelResolveResult:
    """A resolved model plus why it was chosen.

    ``resolve`` returns a bare ``Model`` because that is what every call site needs and forcing
    a wrapper on them would ripple. This richer result exists for ``resolve_detailed``, which
    EGPT-3.5's escalation logging and any future tracing hook read.
    """

    model: Model
    mode: ProviderMode
    provider_key: str
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
    """A provider that can also explain its choice. Optional."""

    def resolve_detailed(self, request: ModelResolveRequest) -> ModelResolveResult: ...


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

    def resolve_detailed(self, request: ModelResolveRequest) -> ModelResolveResult:
        raise NotImplementedError(f"{type(self).__name__} must implement resolve_detailed() (or override resolve()).")

    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities()

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
