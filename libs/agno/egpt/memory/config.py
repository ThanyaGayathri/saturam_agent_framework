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
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from egpt.guardrails.pii import PIIDetectionGuardrail
from egpt.memory.errors import MemoryProviderConfigError
from egpt.memory.provider import MemoryProvider
from egpt.memory.retrieval.fuse import DEFAULT_RRF_K
from egpt.memory.retrieval.rerank import DEFAULT_BUDGET_MS, DEFAULT_CANDIDATES, RerankerLike
from egpt.memory.security.pii import PIIGuardrailLike
from egpt.providers.model_provider import ProviderMode

__all__ = [
    "FUSION_METHODS",
    "RERANK_OFFLINE_CAPABLE",
    "MemoryConfigResolver",
    "MemoryPolicyKey",
    "MemoryProviderConfig",
    "MemoryProviderConfigError",
    "ProviderFactory",
    "build_memory_provider",
    "build_memory_provider_for",
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

#: Keys ``config.pii`` recognizes -- a closed set, same reasoning as ``_FUSION_KNOWN_KEYS``.
_PII_KNOWN_KEYS = frozenset({"enabled", "guardrail"})


def _validate_pii(pii: Dict[str, Any]) -> Dict[str, Any]:
    """Validate ``config.pii`` at construction, per the four-questions boundary rule.

    ``{}`` reads as disabled -- the same "zero-config is the safe no-op" convention
    ``_validate_fusion``/``_validate_rerank`` establish for their own seams, applied here to the
    PII containment boundary (SM-X5, #141): a deployment that never opts a tenant in keeps the
    pre-SM-X5 behaviour, memory write-back and read-inject untouched. ``egpt.memory.security.pii``
    is what actually applies this at the two boundaries -- this function only validates the shape.

    ``guardrail`` lets a tenant bring its own :class:`~egpt.guardrails.pii.PIIDetectionGuardrail`
    (custom patterns, a narrower or wider set of the built-in checks); omitting it builds the
    default one (every built-in check on). Either way the instance is built/validated once, here,
    not per write or per read.
    """
    if not isinstance(pii, dict):
        raise MemoryProviderConfigError(f"config.pii must be a dict, got {type(pii).__name__}.", field_name="pii")
    unknown = sorted(set(pii) - _PII_KNOWN_KEYS)
    if unknown:
        raise MemoryProviderConfigError(
            f"config.pii has unknown key(s) {unknown}; only {sorted(_PII_KNOWN_KEYS)} are recognized.",
            field_name="pii",
        )
    enabled = pii.get("enabled", False)
    if not isinstance(enabled, bool):
        raise MemoryProviderConfigError(
            f"config.pii['enabled'] must be a bool, got {enabled!r}.", field_name="pii.enabled"
        )
    if not enabled:
        if set(pii) - {"enabled"}:
            # `guardrail` is meaningless while disabled -- the same "no knob that silently does
            # nothing" rule `_validate_fusion` applies to `k`.
            raise MemoryProviderConfigError(
                "config.pii['guardrail'] is only meaningful when enabled=True.", field_name="pii"
            )
        return {"enabled": False}

    guardrail = pii.get("guardrail")
    if guardrail is None:
        # `mask_pii` plays no role here -- `egpt.memory.security.pii` calls `detect`/`mask`
        # directly, never `check`/`async_check`, so this flag (which only changes what those two
        # methods do) is inert for a guardrail built here. Left at the class default rather than
        # forced, so a caller-supplied instance reused from elsewhere is not silently rewritten.
        guardrail = PIIDetectionGuardrail()
    elif not isinstance(guardrail, PIIGuardrailLike):
        # Structural, not `isinstance(guardrail, PIIDetectionGuardrail)`: `security/pii.py`'s own
        # module docstring promises "any object exposing the same detect/mask shape works" (a
        # future Presidio (EGPT-8) swap-in, or a tenant's own detector) -- checking the concrete
        # class here silently narrowed that promise back down to one type (SM-X5 #141 round-1
        # review, finding #3). Mirrors `_validate_rerank`'s `isinstance(reranker, RerankerLike)`.
        raise MemoryProviderConfigError(
            f"config.pii['guardrail'] must expose detect(content) and mask(content, pii_types), "
            f"got {type(guardrail).__name__}.",
            field_name="pii.guardrail",
        )
    return {"enabled": True, "guardrail": guardrail}


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

    ``pii`` is the same shape again: closed, validated, off by default. ``{}`` reads as disabled,
    so a deployment that never opts in keeps write-back and read-inject exactly as before SM-X5
    (#141). ``{"enabled": True}`` redacts PII (SSN/CC/email/phone by default) out of every
    write-back event's ``content`` field before it reaches storage, and out of the assembled
    feed-forward context before it is injected -- both boundaries, per issue #141's decision to
    handle PII on both rather than one (a redact-once policy leaks on whichever path it skips).
    Reuses ``egpt.guardrails.pii.PIIDetectionGuardrail`` (the seed's PII guardrail); pass
    ``{"enabled": True, "guardrail": PIIDetectionGuardrail(...)}`` for a tenant-specific pattern
    set. See ``egpt.memory.security.pii``, which is what actually applies this at the two
    boundaries. Being per-tenant config is what makes this "driven by tenant policy": two tenants
    sharing one deployment resolve to different ``MemoryProviderConfig``s (SM-C5), so one can have
    PII handling on while another has it off, or a different pattern set.

    **Only ``content`` is scanned -- ``entities``/``topics``/``metadata`` (and every other
    structured field an event/record carries) are permanently exempt, on both boundaries, at every
    call site**, not just for the common case (round-1 review, finding on the acceptance-criteria
    claim above): they are caller-supplied structured data, not free text, and this guardrail's
    patterns are built for scanning prose. A tenant that writes PII into a topic tag or a metadata
    value rather than ``content`` will not have it caught by turning this on.
    """

    provider: str
    mode: ProviderMode = ProviderMode.LOCAL  # Mode A. FRONTIER is Mode B.
    vector: Dict[str, Any] = field(default_factory=dict)
    graph: Dict[str, Any] = field(default_factory=dict)
    fusion: Dict[str, Any] = field(default_factory=dict)
    rerank: Dict[str, Any] = field(default_factory=dict)
    pii: Dict[str, Any] = field(default_factory=dict)
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
        self.pii = _validate_pii(self.pii)


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


#: ``(tenant_id, agent_id)``. ``None`` in a slot is a wildcard, so ``("acme", None)`` answers every
#: agent of that tenant. Two slots rather than the model seam's three: ``role`` distinguishes
#: *which model* a step should use, a question memory does not ask -- a tenant's memory is the same
#: store whichever role reads it.
#:
#: Adding a third slot is **not** a one-line edit, despite ``_KEY_SLOTS`` looking like the single
#: source of arity: ``_normalize_key`` reads it for the length check and the padding, but
#: :meth:`MemoryConfigResolver.candidate_keys` spells its four probes out literally, so a widened
#: ``_KEY_SLOTS`` alone would leave the extra slot unqueried. Widen both together, or the
#: duplicate-key guard is the only thing standing between the change and silently collapsed keys.
#: ``Scope.key()`` (``memory/types.py``) makes the opposite, genuinely additive promise, and is
#: built that way because it has one slot list and no second literal.
MemoryPolicyKey = Tuple[Optional[str], Optional[str]]

_KEY_SLOTS = ("tenant_id", "agent_id")


@dataclass(frozen=True)
class MemoryConfigResolver:
    """Which :class:`MemoryProviderConfig` a given tenant gets (SM-C5, #93).

    **Selection, not isolation.** This chooses which config answers for an identity; it does not
    partition data. Two tenants that both fall through to ``default`` share one physical store, and
    ``lookup`` hands them the same config object. Isolation remains entirely ``Scope``'s job at
    read/write time -- nothing here is a tenancy boundary.

    The per-tenant layer the module docstring promises, deliberately shaped like
    ``egpt.providers.config.ProviderConfig``: same wildcard key, same most-specific-first
    ordering, same ``default`` fallback. One resolution vocabulary for models and memory means an
    operator who can read one config can read the other, and a reviewer checking "which tenant got
    what" uses one mental model rather than two.

    **Where the parity stops.** The shape matches; the surface does not, in one respect. There is
    no ``from_dict`` here, so a policy map can only be built from hand-written Python -- a tuple
    key cannot come from JSON or YAML, which is exactly why the model seam grew one. Doing it well
    needs a record-list schema, unknown-field rejection and all-``None``-record rejection, so it is
    deliberately left to its own ticket rather than half-shipped here. Until then, a typo in a
    deployment's policy document is not a case this class can reject, because it never sees one.
    The other two model-seam protections -- ``(None, None)`` rejection and a read-only mapping --
    *are* present.

    What SM-C5 does **not** need to add, because it is already here: the graph tier toggles by
    omitting ``config.graph`` (``CoLocatedMemoryProvider`` treats an absent graph as vector-only
    throughout), and Mode A/B is ``config.mode``. Per-tenant selection was the missing piece --
    *which* config answers for a tenant -- not the knobs themselves.

    :meth:`lookup` is pure and cheap: at most four dict lookups, no I/O and no import, so it is
    safe on a per-request path. :func:`build_memory_provider_for` is **not** -- it runs the full
    construction path down to ``discovery.construct``, which opens engines and drivers. Resolve per
    request if you like; construct once and reuse, as CLAUDE.md's "never create agents in loops"
    says for the same reason.
    """

    policies: Mapping[MemoryPolicyKey, MemoryProviderConfig] = field(default_factory=dict)
    default: Optional[MemoryProviderConfig] = None

    def __post_init__(self) -> None:
        if not isinstance(self.policies, Mapping):
            # Checked before dict(): a list or string "policies" in a hand-written config would
            # otherwise surface as a bare builtin ValueError ("dictionary update sequence element
            # #0 ...") with no field name -- invisible to every `except MemoryProviderConfigError`
            # handler, which is the whole of AC4's promise. `ModelPolicy.__post_init__` guards the
            # analogous case for the same reason.
            raise MemoryProviderConfigError(
                f"policies must be a mapping of policy key -> MemoryProviderConfig, "
                f"got {type(self.policies).__name__}.",
                field_name="policies",
            )

        normalized: Dict[MemoryPolicyKey, MemoryProviderConfig] = {}
        first_spelling: Dict[MemoryPolicyKey, Any] = {}
        for raw_key, policy in dict(self.policies).items():
            key = self._normalize_key(raw_key)
            if not isinstance(policy, MemoryProviderConfig):
                raise MemoryProviderConfigError(
                    f"Policy for key {raw_key!r} must be a MemoryProviderConfig, got {type(policy).__name__}.",
                    field_name="policies",
                )
            if key in normalized:
                # Two spellings of one key (``"acme"`` and ``("acme", None)``) would otherwise let
                # whichever iterated last win silently -- the losing config is inert and nothing
                # says so. Name both spellings: in a 200-entry policy map the normalized key alone
                # does not tell an operator which two lines to go and look at.
                raise MemoryProviderConfigError(
                    f"Duplicate policy key {key!r}: {first_spelling[key]!r} and {raw_key!r} both "
                    f"normalize to the same ({', '.join(_KEY_SLOTS)}) pair.",
                    field_name="policies",
                )
            normalized[key] = policy
            first_spelling[key] = raw_key
        object.__setattr__(self, "policies", MappingProxyType(normalized))

        if self.default is not None and not isinstance(self.default, MemoryProviderConfig):
            raise MemoryProviderConfigError(
                f"default must be a MemoryProviderConfig or None, got {type(self.default).__name__}.",
                field_name="default",
            )
        if self.default is not None and (None, None) in normalized:
            # `lookup` consults every candidate key before `default`, and (None, None) is a
            # candidate for every identity -- so this pair means `default` can never win, and
            # editing it silently changes nothing. Two defaults, one live, no error is exactly the
            # misconfiguration shape this module rejects everywhere else. Rejecting it in
            # `__post_init__` also closes the `with_policy` door, since `replace()` re-validates.
            raise MemoryProviderConfigError(
                "A policy keyed (None, None) matches every identity and shadows 'default' "
                "entirely. Keep one: either the identity-less policy or 'default'.",
                field_name="policies",
            )
        if not normalized and self.default is None:
            # An empty resolver answers ``None`` for everything, which surfaces later as a
            # confusing "no config" at the build site rather than here at the definition.
            raise MemoryProviderConfigError(
                "MemoryConfigResolver needs at least one policy or a default; an empty resolver "
                "can never answer a lookup.",
                field_name="policies",
            )

    @staticmethod
    def _normalize_part(part: Any, *, context: str, field_name: str) -> Optional[str]:
        """One identity slot, stripped and checked. ``None`` stays ``None`` -- it is the wildcard.

        Both sides of the seam run through here. A policy map keyed ``"acme"`` and a request
        carrying ``"acme "`` must land on the same key, or an air-gapped tenant silently falls
        through to whatever ``default`` is -- the misroute is invisible because ``modes()`` reports
        what the resolver *can* produce, not what a given identity *got*.

        This package already ruled this way for the same hazard: ``Scope`` strips every slot and
        rejects blanks (``memory/types.py``), and ``require_scope``'s docstring names the failure
        verbatim -- a stray ``"acme "`` *"becomes a distinct tenant to any equality predicate."*
        The model seam's laxity is not a precedent to follow here.
        """
        if part is None:
            return None
        if not isinstance(part, str):
            raise MemoryProviderConfigError(
                f"{context} has a non-string part {part!r} ({type(part).__name__}); "
                f"identity slots are strings or None.",
                field_name=field_name,
            )
        stripped = part.strip()
        if not stripped:
            raise MemoryProviderConfigError(
                f"{context} has a blank part {part!r}. A blank identity slot is not a wildcard -- "
                f"use None for that -- and it cannot identify a tenant either.",
                field_name=field_name,
            )
        return stripped

    @classmethod
    def _normalize_key(cls, raw_key: Any) -> MemoryPolicyKey:
        """Pad a bare tenant string or 1-tuple out to the full 2-slot key, stripped and checked."""
        if isinstance(raw_key, str) or raw_key is None:
            parts: Tuple[Any, ...] = (raw_key,)
        elif isinstance(raw_key, tuple):
            parts = raw_key
        else:
            raise MemoryProviderConfigError(
                f"Policy key must be a string or a tuple of up to 2 strings, got {type(raw_key).__name__}.",
                field_name="policies",
            )
        if len(parts) > len(_KEY_SLOTS):
            raise MemoryProviderConfigError(
                f"Policy key {raw_key!r} has {len(parts)} parts; expected at most "
                f"{len(_KEY_SLOTS)} ({', '.join(_KEY_SLOTS)}).",
                field_name="policies",
            )
        padded = tuple(parts) + (None,) * (len(_KEY_SLOTS) - len(parts))
        cleaned = tuple(
            cls._normalize_part(part, context=f"Policy key {raw_key!r}", field_name="policies") for part in padded
        )
        return (cleaned[0], cleaned[1])

    @classmethod
    def _normalize_identity(cls, tenant_id: Any, agent_id: Any) -> MemoryPolicyKey:
        """The query side of :meth:`_normalize_key` -- same stripping, same rejections.

        Validating only the policy side would leave the misroute reachable from the half that takes
        untrusted input: tenant ids arrive from headers and JWTs, and nothing upstream forces them
        through ``Scope``. An ``int`` tenant id -- a common shape straight out of a database --
        previously resolved to ``default`` with no signal at all.
        """
        return (
            cls._normalize_part(tenant_id, context=f"tenant_id={tenant_id!r}", field_name="tenant_id"),
            cls._normalize_part(agent_id, context=f"agent_id={agent_id!r}", field_name="agent_id"),
        )

    def candidate_keys(
        self, tenant_id: Optional[str] = None, agent_id: Optional[str] = None
    ) -> Tuple[MemoryPolicyKey, ...]:
        """Every key that could serve this identity, most specific first.

        Public rather than private for the same reason its model-seam twin is: the question "why
        did this tenant get the vector-only config?" is answered by showing this list beside
        ``policies``.

        The identity is normalized here, so the probes shown are the ones actually tried -- a
        diagnostic that echoed the caller's un-stripped spelling would hide the very mismatch an
        operator runs it to find.

        Raises:
            MemoryProviderConfigError: a slot is neither a string nor ``None``, or is blank.
        """
        tenant, agent = self._normalize_identity(tenant_id, agent_id)
        return (
            (tenant, agent),
            (tenant, None),
            (None, agent),
            (None, None),
        )

    def lookup(self, tenant_id: Optional[str] = None, agent_id: Optional[str] = None) -> Optional[MemoryProviderConfig]:
        """The most specific config for this identity, or ``default``, or ``None``.

        Returns the **stored** config instance, not a copy: every identity resolving to the same
        policy gets the same object, so mutating a resolved config mutates it for all of them.
        That is a documented consequence of ``MemoryProviderConfig`` being a plain (unfrozen)
        dataclass, pinned by a test rather than left to be discovered. Nothing in ``egpt`` mutates
        a config post-construction; treat one as read-only and use :meth:`with_policy` to vary a
        policy.

        Raises:
            MemoryProviderConfigError: a slot is neither a string nor ``None``, or is blank.
        """
        for key in self.candidate_keys(tenant_id, agent_id):
            policy = self.policies.get(key)
            if policy is not None:
                return policy
        return self.default

    def with_policy(self, key: Any, config: MemoryProviderConfig) -> "MemoryConfigResolver":
        """A copy with one policy added or replaced. Pure construction -- nothing to await."""
        updated = dict(self.policies)
        updated[self._normalize_key(key)] = config
        return replace(self, policies=updated)

    def modes(self) -> Tuple[ProviderMode, ...]:
        """Every mode this resolver can produce, sorted and de-duplicated.

        A deployment that believes it is air-gapped can assert this is ``(LOCAL,)`` -- one call
        rather than walking every policy, which is the check ADR 0003 A.3c actually wants.
        """
        configs = list(self.policies.values())
        if self.default is not None:
            configs.append(self.default)
        return tuple(sorted({c.mode for c in configs}, key=lambda m: m.value))


def build_memory_provider_for(
    resolver: MemoryConfigResolver,
    tenant_id: str,
    agent_id: Optional[str] = None,
) -> MemoryProvider:
    """Resolve this identity's config and build its provider (SM-C5, #93).

    The per-tenant entry point. :func:`build_memory_provider` stays the single construction site --
    this only chooses *which* config reaches it, so every invariant that function enforces still
    applies, and a caller cannot bypass it by resolving separately.

    ``tenant_id`` is **required**, unlike on :meth:`MemoryConfigResolver.lookup`, which stays
    permissive so an operator can ask diagnostic questions about wildcard policies. ``memory/
    types.py`` states the rule this follows: *"a seam where the unscoped call is the easiest one to
    write is a seam that leaks."* ``build_memory_provider_for(resolver)`` used to be both legal and
    the shortest thing to type, and it resolved cleanly to whatever ``default`` was.

    **Construction is not per-request cheap.** Resolution is four dict lookups; this runs the whole
    way down to ``discovery.construct``, which opens engines and drivers for backends like
    ``pgvector`` and ``neo4j``. Build once per identity and reuse the provider -- calling this per
    request is the defect class CLAUDE.md names in "never create agents in loops".

    Raises:
        MemoryProviderConfigError: ``tenant_id`` is ``None``, blank, or not a string; no policy and
            no default matched this identity; or the resolved config fails to build.
    """
    # `None` is rejected here rather than left to `_normalize_part`, whose `None` branch is a
    # *wildcard* -- correct for a policy key (`("acme", None)` = every agent of acme), wrong for
    # this query entry point, where an absent tenant means "no tenant identified". Without this,
    # an explicit `tenant_id=None` resolved cleanly to `default` while the semantically identical
    # `""` was refused, so the laxer spelling was the one that silently worked.
    #
    # The precedent is `require_scope` in `memory/types.py`, which refuses an unscoped data-access
    # call at the seam "so the refusal names the method the caller used" -- and the failure this
    # prevents is not hypothetical: `agent/_run.py` records that an unthreaded `tenant_id=None`
    # silently defeated per-tenant resolution in `ProviderConfig`, the sibling seam this class is
    # modelled on, until someone traced it.
    if tenant_id is None:
        raise MemoryProviderConfigError(
            "build_memory_provider_for requires a tenant_id; got None. Resolution is per-tenant, "
            "so an unidentified caller must not be handed another identity's provider -- pass the "
            "caller's tenant, or use MemoryConfigResolver.lookup directly for a diagnostic query.",
            field_name="tenant_id",
        )

    config = resolver.lookup(tenant_id, agent_id)
    if config is None:
        raise MemoryProviderConfigError(
            f"No memory config for (tenant_id={tenant_id!r}, agent_id={agent_id!r}) and no default. "
            f"Tried, most specific first: {list(resolver.candidate_keys(tenant_id, agent_id))}.",
            field_name="policies",
        )
    return build_memory_provider(config)
