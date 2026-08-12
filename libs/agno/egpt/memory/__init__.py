# Imported for its registration side effect: in-tree adapters self-register on import, so
# `build_memory_provider` can resolve "co_located" without the caller knowing the module exists.
from egpt.memory.adapters import CoLocatedMemoryProvider, SeedMemoryProvider
from egpt.memory.config import (
    MemoryProviderConfig,
    MemoryProviderConfigError,
    build_memory_provider,
    register_provider,
    registered_providers,
)
from egpt.memory.manager import MemoryManager, UserMemory
from egpt.memory.provider import MemoryProvider
from egpt.memory.strategies import (
    MemoryOptimizationStrategy,
    MemoryOptimizationStrategyFactory,
    MemoryOptimizationStrategyType,
    SummarizeStrategy,
)
from egpt.memory.substrates import GraphSubstrate, VectorSubstrate, checked_graph, checked_vector
from egpt.memory.tiers import PERSISTENT_TIERS, SUBSTRATES_BY_TIER, MemoryTier, substrates_for_tier
from egpt.memory.types import (
    CONSOLIDATION_KEYS,
    FUSION_KEYS,
    ConsolidationReport,
    FactWrite,
    MemoryEvent,
    MemoryOperationError,
    MemoryQuery,
    MemoryRecord,
    MemorySlice,
    PartialWriteError,
    Scope,
    Substrate,
    WriteReceipt,
    consolidation_details,
    fusion_diagnostics,
)

__all__ = [
    "MemoryManager",
    "UserMemory",
    "MemoryOptimizationStrategy",
    "MemoryOptimizationStrategyType",
    "MemoryOptimizationStrategyFactory",
    "SummarizeStrategy",
    # Symbiotic Memory seam envelope (SM-C1)
    "PERSISTENT_TIERS",
    "ConsolidationReport",
    "FactWrite",
    "MemoryEvent",
    "MemoryOperationError",
    "MemoryQuery",
    "MemoryRecord",
    "MemorySlice",
    "MemoryTier",
    "Scope",
    "Substrate",
    "WriteReceipt",
    # Substrate ports and the helpers a third-party adapter needs (SM-C1)
    "GraphSubstrate",
    "VectorSubstrate",
    "SUBSTRATES_BY_TIER",
    "checked_graph",
    "checked_vector",
    "consolidation_details",
    "fusion_diagnostics",
    "substrates_for_tier",
    # The seam and its construction path (SM-C1)
    "CONSOLIDATION_KEYS",
    "CoLocatedMemoryProvider",
    "FUSION_KEYS",
    "MemoryProvider",
    "PartialWriteError",
    "MemoryProviderConfig",
    "MemoryProviderConfigError",
    "SeedMemoryProvider",
    "build_memory_provider",
    "register_provider",
    "registered_providers",
]
