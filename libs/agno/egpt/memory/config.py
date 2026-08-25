"""Memory provider configuration and the construction seam (SM-C1, implementing SM-R11).

This module is the reason swapping a memory backend, or moving a tenant between Mode A and Mode B,
is an edit to a config object rather than a code change. :func:`build_memory_provider` is the only
place a concrete provider is constructed; no call site names a backend, a DSN, or an SDK.

**SM-C5 (#93) extends this file** with per-tenant resolution -- which tiers are enabled, whether the
graph tier is on, which vector backend, which mode -- following the shape
``egpt/providers/config.py`` already uses for models (``PolicyKey`` = ``(tenant_id, agent_id,
role)`` -> ``ProviderConfig.lookup``). What is here is the single-config case that shape resolves
*to*.

**Why ``ProviderMode`` and not a memory-specific mode enum.** The SM-R11 spec sketches
``DeploymentMode.CO_LOCATED`` / ``EXTERNAL``. But ``egpt.providers.model_provider.ProviderMode``
already means exactly Mode A / Mode B for models, and ADR 0003 section B.5 rules against carrying
two vocabularies for one concept -- it caught three such drifts across the sibling documents and
fixed them rather than adding a fourth. So ``LOCAL`` is Mode A (co-located, in-VPC) and
``FRONTIER`` is Mode B (external services). One deployment answers "which mode am I in" once.

The provider registry stays local to this package rather than becoming a
``egpt.registry.discovery`` kind. Discovery's declarations live in **core**, and a memory provider
is a **services**-layer composition; declaring it there would have core naming a services module,
a direction ADR 0001 does not sanction. The *backends* a provider composes are a different matter
-- pgvector (SM-V1 #95) and the graph engine (SM-G1 #99) are integrations, resolved through
``discovery.construct``, which is what ADR 0001's "config-swappable backends use the same
constructor registry" refers to.
"""

import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from egpt.memory.errors import MemoryProviderConfigError
from egpt.memory.provider import MemoryProvider
from egpt.memory.retrieval.fuse import DEFAULT_RRF_K
from egpt.memory.retrieval.rerank import DEFAULT_BUDGET_MS, DEFAULT_CANDIDATES, RerankerLike
from egpt.providers.model_provider import ProviderMode

__all__ = [
    "FUSION_METHODS",
    "RERANK_OFFLINE_CAPABLE",
    "MemoryProviderConfig",
    "MemoryProviderConfigError",
    "ProviderFactory",
    "build_memory_provider",
    "register_provider",
    "registered_providers",
]

#: The fusion strategies ``config.fusion["method"]`` accepts. ``score_sum`` is
#: ``CoLocatedMemoryProvider``'s pre-SM-H2 default and stays the default -- ADR 0003 A.6 holds the
#: 3-leg weighted-vs-RRF choice open, gated on SM-G9 (#107)'s real graph retriever and SM-H2/H3's
#: own measurement, so this ticket adds ``rrf`` as an opt-in, not a replacement. See
#: ``egpt.memory.retrieval.fuse``.
FUSION_METHODS = ("score_sum", "rrf")

#: Keys ``config.fusion`` recognizes. A closed set, unlike ``vector``/``graph``'s opaque
#: backend-settings bags: fusion is decided by this seam, not by a backend, so there is no third
#: party whose keys this module must stay ignorant of.
_FUSION_KNOWN_KEYS = frozenset({"method", "k"})


def _validate_fusion(fusion: Dict[str, Any]) -> Dict[str, Any]:
    """Validate ``config.fusion`` at construction, per the four-questions boundary rule.

    Filling in ``k``'s default here, rather than leaving it for a reader of ``config.fusion`` to
    infer, is what lets ``CoLocatedMemoryProvider._merged`` treat a validated config as already
    known-good -- the same bargain ``resolve_substrate`` strikes for the backend settings.
    """
    if not isinstance(fusion, dict):
        raise MemoryProviderConfigError(
            f"config.fusion must be a dict, got {type(fusion).__name__}.", field_name="fusion"
        )
    unknown = sorted(set(fusion) - _FUSION_KNOWN_KEYS)
    if unknown:
        raise MemoryProviderConfigError(
            f"config.fusion has unknown key(s) {unknown}; only {sorted(_FUSION_KNOWN_KEYS)} are recognized.",
            field_name="fusion",
        )
    method = fusion.get("method", "score_sum")
    if method not in FUSION_METHODS:
        raise MemoryProviderConfigError(
            f"config.fusion['method'] must be one of {FUSION_METHODS}, got {method!r}.",
            field_name="fusion.method",
        )
    validated = dict(fusion)
    if "k" in validated:
        if method != "rrf":
            # `k` is meaningless for `score_sum` -- `_merge` takes no `k` at all. Storing it
            # anyway would be a knob that silently does nothing, exactly the shape this module's
            # own principle (see the comment below) refuses to default *in*; refusing it when the
            # caller supplies it explicitly is the same rule applied the other direction.
            raise MemoryProviderConfigError(
                "config.fusion['k'] is only meaningful for method='rrf'.", field_name="fusion.k"
            )
        k = validated["k"]
        if isinstance(k, bool) or not isinstance(k, int) or k < 1:
            raise MemoryProviderConfigError(
                f"config.fusion['k'] must be a positive integer, got {k!r}.",
                field_name="fusion.k",
            )
    elif method == "rrf":
        # RRF's `k` is meaningless for `score_sum`, so it is only defaulted in for the method that
        # reads it -- a stray default under `score_sum` would look like a knob that does something.
        validated["k"] = DEFAULT_RRF_K
    return validated


#: Keys ``config.rerank`` recognizes -- a closed set, same reasoning as ``_FUSION_KNOWN_KEYS``.
_RERANK_KNOWN_KEYS = frozenset({"enabled", "reranker", "candidates", "budget_ms"})

#: Reranker module names that stay inside the air gap, mirroring ``egpt.memory.embedding``'s
#: ``OFFLINE_CAPABLE`` for embedders (ADR 0003 A.3c) applied to the analogous rerank case (SM-R8
#: DECISION.md S1.2/S5): ``sentence_transformer`` runs the cross-encoder in-process, ``infinity``
#: reaches an in-VPC served instance -- both stay inside the air gap. ``cohere``/``aws_bedrock`` are
#: Mode-B-only network reranker APIs and are deliberately absent.
RERANK_OFFLINE_CAPABLE = frozenset({"sentence_transformer", "infinity"})


def _reject_online_reranker_for_local(reranker: RerankerLike) -> None:
    """Refuse a reranker that reaches the network when the deployment is air-gapped.

    Mirrors ``egpt.memory.embedding._reject_online_for_local`` (ADR 0003 A.3c): a reranker is
    accepted for Mode A either because its class lives in a :data:`RERANK_OFFLINE_CAPABLE` module,
    or because it declares ``offline_capable = True`` itself -- the same two escape hatches, and
    the same acknowledged limit that a module path is a proxy for the property, not the property
    (see that function's own docstring for why neither arm is a security boundary).
    """
    if getattr(reranker, "offline_capable", False) is True:
        return
    module = type(reranker).__module__ or ""
    if module.rsplit(".", 1)[-1] in RERANK_OFFLINE_CAPABLE:
        return
    raise MemoryProviderConfigError(
        f"{type(reranker).__name__} (from {module!r}) is not one of the offline-capable rerankers, and "
        f"mode={ProviderMode.LOCAL.value!r} (Mode A) is the air-gapped deployment path (ADR 0003 A.3c). "
        f"Offline-capable: {', '.join(sorted(RERANK_OFFLINE_CAPABLE))}. Pass one of those; or set "
        "offline_capable = True on it, which asserts that it reaches nothing outside the air gap; or run "
        f"this tenant in mode={ProviderMode.FRONTIER.value!r} if reaching a provider is intended.",
        field_name="rerank.reranker",
    )


def _validate_rerank(rerank: Dict[str, Any], mode: ProviderMode) -> Dict[str, Any]:
    """Validate ``config.rerank`` at construction, per the four-questions boundary rule.

    ``{}`` reads as disabled -- SM-R8's ruling (DECISION.md S1.2) is that cross-encoder rerank
    stays off the default hot path, so the zero-config case must not turn it on. Enabling it
    requires a ``reranker`` instance (SM-H3 reuses ``egpt.integrations.knowledge.reranker.*`` per
    S5 rather than building a new one), validated for the deployment's mode the same way
    ``resolve_substrate`` validates a backend for it.
    """
    if not isinstance(rerank, dict):
        raise MemoryProviderConfigError(
            f"config.rerank must be a dict, got {type(rerank).__name__}.", field_name="rerank"
        )
    unknown = sorted(set(rerank) - _RERANK_KNOWN_KEYS)
    if unknown:
        raise MemoryProviderConfigError(
            f"config.rerank has unknown key(s) {unknown}; only {sorted(_RERANK_KNOWN_KEYS)} are recognized.",
            field_name="rerank",
        )
    enabled = rerank.get("enabled", False)
    if not isinstance(enabled, bool):
        raise MemoryProviderConfigError(
            f"config.rerank['enabled'] must be a bool, got {enabled!r}.", field_name="rerank.enabled"
        )
    if not enabled:
        if set(rerank) - {"enabled"}:
            # `reranker`/`candidates`/`budget_ms` are meaningless while disabled -- the same "no
            # knob that silently does nothing" rule `_validate_fusion` applies to `k`.
            raise MemoryProviderConfigError(
                "config.rerank['reranker'/'candidates'/'budget_ms'] are only meaningful when enabled=True.",
                field_name="rerank",
            )
        return {"enabled": False}

    reranker = rerank.get("reranker")
    if not isinstance(reranker, RerankerLike):
        raise MemoryProviderConfigError(
            f"config.rerank['reranker'] must be a Reranker instance when enabled=True, got "
            f"{type(reranker).__name__ if reranker is not None else None!r}.",
            field_name="rerank.reranker",
        )
    if mode is ProviderMode.LOCAL:
        _reject_online_reranker_for_local(reranker)

    validated: Dict[str, Any] = {"enabled": True, "reranker": reranker}

    candidates = rerank.get("candidates", DEFAULT_CANDIDATES)
    if isinstance(candidates, bool) or not isinstance(candidates, int) or candidates < 1:
        raise MemoryProviderConfigError(
            f"config.rerank['candidates'] must be a positive integer, got {candidates!r}.",
            field_name="rerank.candidates",
        )
    validated["candidates"] = candidates

    budget_ms = rerank.get("budget_ms", DEFAULT_BUDGET_MS)
    if isinstance(budget_ms, bool) or not isinstance(budget_ms, (int, float)) or budget_ms <= 0:
        raise MemoryProviderConfigError(
            f"config.rerank['budget_ms'] must be a positive number, got {budget_ms!r}.",
            field_name="rerank.budget_ms",
        )
    validated["budget_ms"] = float(budget_ms)
    return validated


@dataclass
class MemoryProviderConfig:
    """Everything needed to build a provider, with no call site touched.

    ``vector`` and ``graph`` are opaque per-backend settings (dsn, dim, uri, index params). They
    stay untyped here on purpose: typing them would make this module name its backends, which is
    exactly the coupling the seam exists to prevent.

    ``fusion`` is different: it is decided by this seam, not a backend, so it is a closed,
    validated shape rather than an opaque bag -- see :data:`FUSION_METHODS`. It defaults to
    ``{}``, which reads as ``score_sum``, ``CoLocatedMemoryProvider``'s pre-SM-H2 default. SM-R8
    measured dense+BM25, never this seam's vector+graph pairing, and ADR 0003 A.6 holds the 3-leg
    weighted-vs-RRF choice open, gated on SM-G9 (#107) and SM-H2/H3's own measurement -- so
    ``score_sum`` stays the default here too. ``{"method": "rrf", "k": 60}`` opts into the
    strategy SM-H2 (#132) adds; see ``egpt.memory.retrieval.fuse``.

    ``rerank`` is the same shape of seam as ``fusion``: closed, validated, off by default.
    ``{}`` reads as disabled -- SM-R8 DECISION.md S1.2 measured CPU cross-encoder rerank at
    ~100x its own ~300ms budget, so the zero-config case must not turn it on.
    ``{"enabled": True, "reranker": SentenceTransformerReranker(...), "budget_ms": 300}`` opts
    into the stage SM-H3 (#133) adds; see ``egpt.memory.retrieval.rerank``. Because this config is
    the per-tenant unit SM-C5 (#93) will resolve to (see the module docstring), this is also how
    rerank is "toggleable per tenant" -- one tenant's config, not a global switch.
    """

    provider: str
    mode: ProviderMode = ProviderMode.LOCAL  # Mode A. FRONTIER is Mode B.
    vector: Dict[str, Any] = field(default_factory=dict)
    graph: Dict[str, Any] = field(default_factory=dict)
    fusion: Dict[str, Any] = field(default_factory=dict)
    rerank: Dict[str, Any] = field(default_factory=dict)
    options: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.provider, str) or not self.provider.strip():
            raise MemoryProviderConfigError(
                "MemoryProviderConfig.provider is required and selects the adapter to build.",
                field_name="provider",
            )
        self.provider = self.provider.strip()

        # Coerce mode at the boundary, the same way ``ModelResolveRequest`` does: a value that
        # arrived from JSON or a settings file is equal-but-not-identical to the enum member, and
        # every consumer compares with ``is``.
        if not isinstance(self.mode, ProviderMode):
            try:
                self.mode = ProviderMode(str(self.mode).strip().lower())
            except ValueError:
                known = ", ".join(repr(m.value) for m in ProviderMode)
                raise MemoryProviderConfigError(
                    f"Unknown mode {self.mode!r}. Expected one of: {known}.",
                    field_name="mode",
                ) from None

        self.fusion = _validate_fusion(self.fusion)
        self.rerank = _validate_rerank(self.rerank, self.mode)


def resolve_substrate(
    field: str,
    kind: str,
    settings: Dict[str, Any],
    injected: Any,
    option: str,
    check: Callable[[Any, str], Any],
    mode: Optional[ProviderMode] = None,
) -> Any:
    """Build a substrate from ``config.vector`` / ``config.graph``, or take the injected one.

    Written once and used for both substrates. An invariant applied to one leg and not the adjacent
    one is the defect class this seam keeps producing, and these two legs are as adjacent as they get.

    ``settings`` names its backend under ``"backend"`` and passes every other key to the
    constructor, so a DSN or a Bolt URI never reaches a call site. The backend itself is an
    integration resolved by registry key -- ``egpt/memory/`` imports no concrete engine (ADR 0001).

    ``mode`` is forwarded to any backend whose constructor **names** a ``mode`` parameter, and
    withheld from every other (SM-V2 #96). A backend needs it because Mode A and Mode B are not the
    same deployment: the vector store's embedder is chosen per mode and Mode A refuses one that
    reaches the network (ADR 0003 A.3c). Opt-in by signature rather than passed to everything,
    because most constructors take no ``**kwargs`` and would simply fail; and forwarded on **both**
    legs by the same rule, so an external graph engine picks it up without this function changing.

    Supplying **both** a config and an injected instance is refused rather than ranked. Either
    precedence is defensible, so a caller cannot tell which one it got, and the losing side is
    silently inert -- a test double quietly bypassed in favour of a real database, or the reverse.
    """
    if not settings:
        return check(injected, option)
    if injected is not None:
        raise MemoryProviderConfigError(
            f"config.{field} names a backend and options[{option!r}] supplies an instance. "
            "Pass one: with both, which of them is live depends on a precedence rule you cannot see.",
            field_name=field,
        )
    backend = settings.get("backend")
    if not isinstance(backend, str) or not backend.strip():
        raise MemoryProviderConfigError(
            f"config.{field} needs a 'backend' key naming a registered {kind} (for example "
            f"{{'backend': 'neo4j', ...}}); the remaining keys are passed to it.",
            field_name=field,
        )

    from egpt.registry import discovery

    rest = {key: value for key, value in settings.items() if key != "backend"}
    if "mode" in rest:
        # Refused rather than ranked, like the config-plus-instance case above. `config.mode` is the
        # deployment's one answer to "which mode am I in"; a second one here means a backend could
        # be air-gap-checked against a mode the rest of the deployment is not running in.
        raise MemoryProviderConfigError(
            f"config.{field} sets 'mode', which is already config.mode ({mode.value if mode else None!r}). "
            "One deployment has one mode; a second copy here drifts from the first.",
            field_name=field,
        )
    known = set(discovery.names(kind))
    if backend.strip() not in known:
        # Checked rather than caught. `discovery.resolve` signals an unknown name with `ValueError`,
        # but so does any constructor validating its own arguments -- and `MemoryOperationError` is
        # itself a `ValueError`, so a blanket `except ValueError` here would silently reclassify a
        # backend's per-request refusal as a configuration error.
        raise MemoryProviderConfigError(
            f"Unknown {kind} backend {backend.strip()!r}. Registered: {', '.join(sorted(known)) or '<none>'}.",
            field_name=field,
        )
    if mode is not None and _accepts_mode(discovery.resolve(kind, backend.strip())):
        rest["mode"] = mode
    # An ImportError from a missing SDK is deliberately left to propagate: the integration's own
    # "pip install neo4j" message is more useful than anything this frame could add.
    try:
        constructed = discovery.construct(kind, backend.strip(), **rest)
    except (ImportError, MemoryProviderConfigError):
        raise
    except Exception as exc:
        # Anything else a backend's constructor raises -- an unreachable host, a rejected password,
        # a bad DSN -- is a failure to *build* this provider, whatever its type. Callers of
        # `build_memory_provider` assert on `MemoryProviderConfigError`; leaving a raw
        # `ServiceUnavailable` here makes the seam's construction contract depend on which backend
        # the config happened to name.
        raise MemoryProviderConfigError(
            f"Could not construct the {kind} backend {backend.strip()!r}: {exc}", field_name=field
        ) from exc
    return check(constructed, option)


def _accepts_mode(constructor: Any) -> bool:
    """Whether ``constructor`` names a ``mode`` parameter, or takes ``**kwargs`` that would absorb one.

    A backend opts into the deployment mode by declaring it. A constructor that cannot be
    introspected at all -- a C extension, an exotic callable -- is treated as not accepting it,
    because guessing wrong here raises ``TypeError`` from a frame that names neither the backend nor
    the setting.
    """
    try:
        parameters = inspect.signature(constructor).parameters
    except (TypeError, ValueError):
        return False
    return "mode" in parameters or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values())


#: An adapter factory: takes a config, returns a ready provider.
ProviderFactory = Callable[[MemoryProviderConfig], MemoryProvider]

_REGISTRY: Dict[str, ProviderFactory] = {}


def register_provider(key: str) -> Callable[[ProviderFactory], ProviderFactory]:
    """Register an adapter factory under ``key``, for ``config.provider`` to select.

    Adapters self-register on import. Registration is not locked: module bodies run under the
    import lock, so two threads cannot execute this decorator for the same module concurrently.

    Raises:
        MemoryProviderConfigError: ``key`` is already registered. Silently overwriting would make
            which adapter you get depend on import order.
    """

    def _decorator(factory: ProviderFactory) -> ProviderFactory:
        if key in _REGISTRY:
            raise MemoryProviderConfigError(
                f"MemoryProvider {key!r} is already registered.",
                field_name="provider",
            )
        _REGISTRY[key] = factory
        return factory

    return _decorator


def build_memory_provider(config: MemoryProviderConfig) -> MemoryProvider:
    """Resolve ``config.provider`` to an adapter and build it. The only construction call site.

    Changing ``config.provider`` or ``config.mode`` swaps the implementation with no other edit --
    that property is what the swap test asserts by running one consumer function verbatim against
    two configs.

    Raises:
        MemoryProviderConfigError: the key is not registered, or the factory returned something
            that is not a ``MemoryProvider``.
    """
    factory = _REGISTRY.get(config.provider)
    if factory is None:
        known = ", ".join(sorted(_REGISTRY)) or "<none registered -- import an adapter module>"
        raise MemoryProviderConfigError(
            f"Unknown MemoryProvider {config.provider!r}. Registered: {known}.",
            field_name="provider",
        )

    provider = factory(config)
    # Checked here rather than trusted: a factory returning the wrong thing otherwise fails deep
    # inside a read, with a traceback that points anywhere but the registration.
    if not isinstance(provider, MemoryProvider):
        raise MemoryProviderConfigError(
            f"Factory for {config.provider!r} returned {type(provider).__name__}, which is not a MemoryProvider.",
            field_name="provider",
        )
    # `MemoryProvider.config` is a bare annotation with no default, and both in-tree adapters set
    # it themselves -- so a third-party factory that forgets builds cleanly here and raises
    # `AttributeError` at `provider.config.mode`, which is the deep failure the check above exists
    # to prevent. Backfilled rather than raised: the factory produced a working provider.
    if getattr(provider, "config", None) is None:
        provider.config = config
    return provider


def registered_providers() -> List[str]:
    """Sorted registry keys, for diagnostics and tests."""
    return sorted(_REGISTRY)


def _reset_registry_for_tests() -> None:
    """Drop all registrations. For test fixtures only.

    Mirrors ``egpt.registry.discovery._reset_for_tests``: a test that registers a throwaway adapter
    must not leak it into the next one, and duplicate registration is an error by design.
    """
    _REGISTRY.clear()
