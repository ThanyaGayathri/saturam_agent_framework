"""Built-in temporal-graph backend declarations (SM-G1 #99).

One row per backend package, ``key -> (module, class_name)``, matching the convention the
``vectordb`` and ``db`` rows already use: the key is the backend's package directory name.

A ``graphdb`` is a backend for ``egpt.memory.substrates.GraphSubstrate`` -- a bi-temporal knowledge
graph, not a graph-RAG index. ``lightrag`` is deliberately absent: SM-R2 measured that it carries no
temporal fields at all and silently discards the ``filters`` argument, so a point-in-time predicate
cannot even be sent to it. It stays available as a ``VectorDb``, which is what it is.

Kept a separate kind from ``vectordb`` because the two answer different Protocols. Folding them
together would let a config name a vector store where a graph is required and fail at the first
``traverse`` rather than at construction.
"""

from egpt.registry.discovery import SOURCE_BUILTIN, register

_ROWS = {
    "neo4j": ("egpt.integrations.memory.graph.neo4j", "Neo4jGraphSubstrate"),
}


def register_all() -> None:
    for key, (module, class_name) in _ROWS.items():
        register("graphdb", key, module=module, attr=class_name, source=SOURCE_BUILTIN, override=True)
