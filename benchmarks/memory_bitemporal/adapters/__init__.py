"""Concrete ``BiTemporalEdgeStore`` adapters (selected by config, never at call sites).

Only the zero-dependency stores are imported eagerly; ``PostgresEdgeStore`` /
``Neo4jEdgeStore`` are imported lazily by ``run.py`` so the package works without drivers.
"""

from .reference_store import ReferenceEdgeStore
from .single_time_store import SingleTimeEdgeStore

__all__ = ["ReferenceEdgeStore", "SingleTimeEdgeStore"]
