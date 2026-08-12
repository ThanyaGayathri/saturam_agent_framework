"""Concrete ``MemoryProvider`` adapters (SM-C1).

An adapter composes substrate ports into a working provider and self-registers under a key that
``MemoryProviderConfig.provider`` selects. Importing this package registers every in-tree adapter,
so ``build_memory_provider`` can resolve them.

Adapters are **services**-layer composition, not integrations: they contain no backend code and
import no SDK. The backends they compose (pgvector SM-V1 #95, the graph engine SM-G1 #99) are
integrations, injected or resolved by registry key.
"""

from egpt.memory.adapters.co_located import CoLocatedMemoryProvider
from egpt.memory.adapters.seed import SeedMemoryProvider

__all__ = ["CoLocatedMemoryProvider", "SeedMemoryProvider"]
