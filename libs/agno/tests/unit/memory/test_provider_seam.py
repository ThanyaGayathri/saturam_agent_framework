"""The MemoryProvider seam, its config, and the construction registry (SM-C1, phase 3).

The properties under test are the ones that make a backend swap a config edit: exactly one
construction path, a config that rejects nonsense at the boundary instead of silently degrading,
and an ABC that cannot be half-implemented.
"""

from typing import Dict, List, Optional, Sequence

import pytest

from egpt.memory import config as config_module
from egpt.memory.config import (
    MemoryProviderConfig,
    MemoryProviderConfigError,
    _reset_registry_for_tests,
    build_memory_provider,
    register_provider,
    registered_providers,
)
from egpt.memory.provider import MemoryProvider
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import (
    ConsolidationReport,
    MemoryEvent,
    MemoryQuery,
    MemoryRecord,
    MemorySlice,
    Scope,
    Substrate,
    WriteReceipt,
)
from egpt.providers.model_provider import ProviderMode

SCOPE = Scope(tenant_id="acme")


class StubProvider(MemoryProvider):
    """Minimal concrete provider -- enough to exercise the registry, nothing more."""

    provider_key = "stub"

    def __init__(self, config: Optional[MemoryProviderConfig] = None) -> None:
        self.config = config

    def read(self, scope: Scope, query: MemoryQuery) -> MemorySlice:
        return MemorySlice(scope=scope, query=query)

    async def aread(self, scope: Scope, query: MemoryQuery) -> MemorySlice:
        return self.read(scope, query)

    def write(self, scope: Scope, events: Sequence[MemoryEvent]) -> WriteReceipt:
        return WriteReceipt(accepted=len(events))

    async def awrite(self, scope: Scope, events: Sequence[MemoryEvent]) -> WriteReceipt:
        return self.write(scope, events)

    def consolidate(self, scope: Scope, tiers: Optional[Sequence[MemoryTier]] = None) -> ConsolidationReport:
        return ConsolidationReport(scope=scope)

    async def aconsolidate(self, scope: Scope, tiers: Optional[Sequence[MemoryTier]] = None) -> ConsolidationReport:
        return self.consolidate(scope, tiers)

    def read_tier(self, scope: Scope, tier: MemoryTier, query: MemoryQuery) -> List[MemoryRecord]:
        return []

    async def aread_tier(self, scope: Scope, tier: MemoryTier, query: MemoryQuery) -> List[MemoryRecord]:
        return []

    def write_tier(self, scope: Scope, tier: MemoryTier, events: Sequence[MemoryEvent]) -> WriteReceipt:
        return WriteReceipt(accepted=len(events), tiers_written=[tier])

    async def awrite_tier(self, scope: Scope, tier: MemoryTier, events: Sequence[MemoryEvent]) -> WriteReceipt:
        return self.write_tier(scope, tier, events)

    def substrates(self) -> Dict[Substrate, bool]:
        return {Substrate.VECTOR: True, Substrate.GRAPH: False}


@pytest.fixture(autouse=True)
def clean_registry():
    """Isolate the global registry, then put back exactly what was there.

    Restoring rather than clearing matters: in-tree adapters self-register at import time, and a
    module body runs once per process. A fixture that cleared on teardown would leave every later
    test in the session facing an empty registry with no way to re-trigger registration -- the
    failure mode ``discovery._ensure_ready`` has a comment warning about.
    """
    saved = dict(config_module._REGISTRY)
    _reset_registry_for_tests()
    yield
    _reset_registry_for_tests()
    config_module._REGISTRY.update(saved)


# ---------------------------------------------------------------------------
# The ABC cannot be half-implemented
# ---------------------------------------------------------------------------


def test_the_seam_is_abstract():
    with pytest.raises(TypeError):
        MemoryProvider()  # type: ignore[abstract]


@pytest.mark.parametrize("missing", ["aread", "awrite", "aconsolidate", "aread_tier", "awrite_tier"])
def test_a_provider_missing_an_async_twin_cannot_be_instantiated(missing):
    """Both halves are abstract, so a sync-only backend fails at construction, not under load.

    A default async implementation delegating to the sync one would let this class of omission
    ship looking complete and surface later as a blocked event loop.
    """
    body = {name: fn for name, fn in vars(StubProvider).items() if name != missing}
    partial = type("PartialProvider", (MemoryProvider,), body)

    with pytest.raises(TypeError, match=missing):
        partial()


def test_every_verb_has_both_halves():
    """Guards CLAUDE.md's sync+async rule against a verb being added with only one half."""
    for verb in ("read", "write", "consolidate", "read_tier", "write_tier"):
        assert hasattr(MemoryProvider, verb)
        assert hasattr(MemoryProvider, "a" + verb)


# ---------------------------------------------------------------------------
# Config: rejects at the boundary rather than degrading silently
# ---------------------------------------------------------------------------


def test_mode_defaults_to_co_located():
    assert MemoryProviderConfig(provider="p").mode is ProviderMode.LOCAL


@pytest.mark.parametrize("raw,expected", [("local", ProviderMode.LOCAL), ("FRONTIER", ProviderMode.FRONTIER)])
def test_a_mode_string_from_config_is_coerced_to_the_enum(raw, expected):
    """Consumers compare with `is`; a string from JSON is equal but not identical."""
    assert MemoryProviderConfig(provider="p", mode=raw).mode is expected


def test_an_unknown_mode_is_rejected_naming_the_valid_ones():
    with pytest.raises(MemoryProviderConfigError, match="Expected one of") as exc:
        MemoryProviderConfig(provider="p", mode="co_located")
    assert exc.value.field_name == "mode"


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_a_missing_provider_key_is_rejected(blank):
    with pytest.raises(MemoryProviderConfigError, match="provider"):
        MemoryProviderConfig(provider=blank)  # type: ignore[arg-type]


def test_the_config_rejects_a_fusion_knob_rather_than_absorbing_it():
    """A knob nothing has measured is a knob that silently does nothing.

    SM-R8 measured dense+BM25, never this seam's vector+graph pairing, and with no backend wired
    there is nothing here that could measure it. Ranked fusion and its configuration are SM-H2's
    (#132); a `fusion=` field would be finding F5 -- accepted at the boundary, read by nothing.

    Asserting the constructor *rejects* it rather than that the attribute is absent: `not
    hasattr(...)` also passes if the field is quietly reintroduced as a dict absorbed into
    `options`, which is the shape the regression would actually take.
    """
    with pytest.raises(TypeError, match="fusion"):
        MemoryProviderConfig(provider="p", fusion={"method": "weighted"})  # type: ignore[call-arg]


def test_backend_settings_stay_opaque():
    """Typing these would make the seam name its backends -- the coupling it exists to prevent."""
    config = MemoryProviderConfig(provider="p", vector={"dsn": "postgres://x", "dim": 768}, graph={"uri": "bolt://y"})

    assert config.vector["dim"] == 768
    assert config.graph["uri"] == "bolt://y"


# ---------------------------------------------------------------------------
# Registry: one construction path
# ---------------------------------------------------------------------------


def test_build_resolves_a_registered_adapter():
    @register_provider("stub")
    def _build(config: MemoryProviderConfig) -> MemoryProvider:
        return StubProvider(config)

    provider = build_memory_provider(MemoryProviderConfig(provider="stub"))

    assert isinstance(provider, StubProvider)
    assert provider.config is not None
    assert registered_providers() == ["stub"]


def test_an_unknown_provider_names_what_is_registered():
    @register_provider("stub")
    def _build(config: MemoryProviderConfig) -> MemoryProvider:
        return StubProvider(config)

    with pytest.raises(MemoryProviderConfigError, match="Registered: stub"):
        build_memory_provider(MemoryProviderConfig(provider="nope"))


def test_an_empty_registry_says_so_instead_of_listing_nothing():
    with pytest.raises(MemoryProviderConfigError, match="none registered"):
        build_memory_provider(MemoryProviderConfig(provider="nope"))


def test_duplicate_registration_is_refused():
    """Overwriting would make which adapter you get depend on import order."""

    @register_provider("stub")
    def _first(config: MemoryProviderConfig) -> MemoryProvider:
        return StubProvider(config)

    with pytest.raises(MemoryProviderConfigError, match="already registered"):

        @register_provider("stub")
        def _second(config: MemoryProviderConfig) -> MemoryProvider:
            return StubProvider(config)


def test_a_factory_returning_the_wrong_type_fails_at_construction():
    """Otherwise it fails deep inside a read, pointing anywhere but the registration."""

    @register_provider("bad")
    def _build(config: MemoryProviderConfig) -> MemoryProvider:
        return "not a provider"  # type: ignore[return-value]

    with pytest.raises(MemoryProviderConfigError, match="not a MemoryProvider"):
        build_memory_provider(MemoryProviderConfig(provider="bad"))


def test_the_mode_reaches_the_adapter_so_a_swap_is_config_only():
    """The Mode A/B property SM-C5 builds on: same key, different mode, different wiring."""

    @register_provider("dual")
    def _build(config: MemoryProviderConfig) -> MemoryProvider:
        return StubProvider(config)

    mode_a = build_memory_provider(MemoryProviderConfig(provider="dual", mode=ProviderMode.LOCAL))
    mode_b = build_memory_provider(MemoryProviderConfig(provider="dual", mode=ProviderMode.FRONTIER))

    assert mode_a.config.mode is ProviderMode.LOCAL
    assert mode_b.config.mode is ProviderMode.FRONTIER
