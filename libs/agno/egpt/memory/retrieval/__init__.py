"""Retrieval: the read-path fan-out (SM-H1 #131) and the KB + memory union (SM-S4 #121).

``orchestrate``/``aorchestrate`` are internal: the public surface for a substrate fan-out is
``CoLocatedMemoryProvider.read``/``aread`` (via ``MemoryProvider``, per
``egpt.memory.config.build_memory_provider``), not this package -- a caller reaching them
directly bypasses the provider's own scope/tier post-conditions. For that reason they are not
re-exported from ``egpt.memory``'s top-level ``__init__``, even though this package's own
``__init__`` exports them (the boundary is the top-level seam, not this module).

``unified_read``/``aunified_read`` are the opposite: the public entry point KB + memory callers
use directly, so both this package's ``__init__`` and ``egpt.memory``'s re-export them.
"""

from egpt.memory.retrieval.orchestrator import RetrievalLegs, aorchestrate, orchestrate
from egpt.memory.retrieval.unified import (
    KbFilters,
    KnowledgeRetrievable,
    RetrievalSource,
    UnifiedResult,
    UnifiedSlice,
    aunified_read,
    unified_read,
)

__all__ = [
    "KbFilters",
    "KnowledgeRetrievable",
    "RetrievalLegs",
    "RetrievalSource",
    "UnifiedResult",
    "UnifiedSlice",
    "aorchestrate",
    "aunified_read",
    "orchestrate",
    "unified_read",
]
