# Imported for its registration side effect: in-tree adapters self-register on import, so
# `build_memory_provider` can resolve "co_located" without the caller knowing the module exists.
from egpt.memory.adapters import CoLocatedMemoryProvider, ExternalMemoryProvider, SeedMemoryProvider
from egpt.memory.adapters.co_located import PartialWriteError
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
from egpt.memory.substrates import GraphSubstrate, VectorSubstrate
from egpt.memory.tiers import PERSISTENT_TIERS, MemoryTier
from egpt.memory.types import (
    FUSION_KEYS,
    ConsolidationReport,
    FactWrite,
    MemoryEvent,
    MemoryOperationError,
    MemoryQuery,
    MemoryRecord,
    MemorySlice,
    Scope,
    Substrate,
    WriteReceipt,
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
    # Substrate ports (SM-C1)
    "GraphSubstrate",
    "VectorSubstrate",
    # The seam and its construction path (SM-C1)
    "CoLocatedMemoryProvider",
    "ExternalMemoryProvider",
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
