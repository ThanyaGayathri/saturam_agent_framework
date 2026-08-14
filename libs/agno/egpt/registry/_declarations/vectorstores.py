"""Built-in memory vector-store declarations (SM-V1 #95).

One row per backend package, ``key -> (module, class_name)``, matching the convention the
``vectordb`` and ``graphdb`` rows already use: the key is the backend's package directory name.

A ``vectorstore`` is a backend for ``egpt.memory.substrates.VectorSubstrate`` -- memory vectors
scoped by tenant and tier, with the seam's ``a``-prefixed async methods and a similarity score
in ``[0.0, 1.0]``.

**Kept a separate kind from ``vectordb``, which answers a different Protocol.** ``VectorDb`` is
document retrieval for knowledge: its async methods carry the ``async_`` prefix, it has no notion
of a scope or a tier, and its ``search`` returns ``Document``. Folding the two together would let
a config name a knowledge store where a memory substrate is required and fail at the first read
rather than at construction -- and the names would collide, since ``pgvector`` already means
``PgVector`` under ``vectordb``. The same reasoning ``graphdbs.py`` records for the graph half.
"""

from egpt.registry.discovery import SOURCE_BUILTIN, register

_ROWS = {
    "pgvector": ("egpt.integrations.memory.vector.pgvector", "PgVectorMemorySubstrate"),
}


def register_all() -> None:
    for key, (module, class_name) in _ROWS.items():
        register("vectorstore", key, module=module, attr=class_name, source=SOURCE_BUILTIN, override=True)
