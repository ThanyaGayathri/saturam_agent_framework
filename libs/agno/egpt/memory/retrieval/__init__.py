"""Feed-forward retrieval: fan the read path out across substrates (SM-H1 #131).

Internal: the public surface is ``CoLocatedMemoryProvider.read`` / ``aread`` (via
``MemoryProvider``, per ``egpt.memory.config.build_memory_provider``), not this package. Not
re-exported from ``egpt.memory``'s top-level ``__init__`` for that reason -- a caller reaching
``orchestrate``/``aorchestrate`` directly bypasses the provider's own scope/tier post-conditions.
"""

from egpt.memory.retrieval.orchestrator import RetrievalLegs, aorchestrate, orchestrate

__all__ = ["RetrievalLegs", "aorchestrate", "orchestrate"]
