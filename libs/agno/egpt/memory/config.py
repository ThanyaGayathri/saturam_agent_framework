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
-- pgvector (SM-V1 #95) and the graph engine (SM-G1 #99) are integrations and will resolve through
``discovery.construct``, which is what ADR 0001's "config-swappable backends use the same
constructor registry" refers to.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from egpt.memory.provider import MemoryProvider
from egpt.providers.model_provider import ProviderMode

__all__ = [
    "MemoryProviderConfig",
    "MemoryProviderConfigError",
    "ProviderFactory",
    "build_memory_provider",
    "register_provider",
    "registered_providers",
]


class MemoryProviderConfigError(ValueError):
    """A memory provider's configuration is invalid or incomplete.

    ``ValueError`` for the same reason ``ModelProviderConfigError`` is one: a deployment mistake
    caught at configuration time should land on the type callers already assert against.

    Deliberately distinct from ``ModelProviderConfigError`` -- that one is about resolving a
    *model*, and collapsing the two would make a bad memory DSN look like a bad model policy in
    every log and every ``except`` clause.
    """

    def __init__(self, message: str, *, field_name: Optional[str] = None) -> None:
        super().__init__(message)
        self.field_name = field_name


@dataclass
class MemoryProviderConfig:
    """Everything needed to build a provider, with no call site touched.

    ``vector`` and ``graph`` are opaque per-backend settings (dsn, dim, uri, index params). They
    stay untyped here on purpose: typing them would make this module name its backends, which is
    exactly the coupling the seam exists to prevent.

    There are no fusion knobs. SM-R8 measured dense+BM25, never this seam's vector+graph pairing,
    so a `method` or `vector_weight` here would be a setting nothing had measured and, with no
    backend wired, nothing could measure. Rank fusion and its configuration are SM-H2's (#132).
    """

    provider: str
    mode: ProviderMode = ProviderMode.LOCAL  # Mode A. FRONTIER is Mode B.
    vector: Dict[str, Any] = field(default_factory=dict)
    graph: Dict[str, Any] = field(default_factory=dict)
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
