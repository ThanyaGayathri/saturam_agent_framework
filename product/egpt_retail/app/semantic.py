"""The one place that turns settings into a ``SemanticRead`` (R5; K5's contract).

Every consumer of the semantic layer -- R5's materialiser and preflight, R9's gap check, K11's
feasibility executor -- needs the same three lines: a Neo4j driver from the settings, a
``SemanticGraphStore`` over it, and a promise to close the driver. Until this module each
wrote its own copy (``executors/kpi.py`` still does; announced as a seam to adopt), which is
how one of them ends up pointing at a different database from the rest.

Pure construction plus one context manager. Nothing here runs a query; the driver connects
lazily, so building a reader with the graph down fails at the first read with Neo4j's own
error, not here.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Optional

from egpt_retail.app.settings import RetailSettings
from egpt_retail.contracts.semantic import SemanticRead
from egpt_retail.semantic.store import SemanticGraphStore

__all__ = ["build_semantic_reader", "open_semantic_reader", "semantic_driver"]


def semantic_driver(settings: RetailSettings) -> Any:
    """A Neo4j driver for the ontology graph, from settings. The caller owns its lifetime.

    Refuses when the URI is blank rather than letting the driver raise a URI-parse error that
    names no setting. The password may legitimately be unset for an auth-less local graph, so
    it is passed as an empty string, matching ``executors/kpi.py`` and ``load_knowledge``.
    """
    uri = (settings.retail_neo4j_uri or "").strip()
    if not uri:
        raise RuntimeError(
            "EGPT_RETAIL_NEO4J_URI is not set, so there is no semantic layer to read. The "
            "compose stack's full profile runs Neo4j and the Helm chart sets it from "
            "neo4j.ontologyUri."
        )
    from neo4j import GraphDatabase

    return GraphDatabase.driver(
        uri, auth=(settings.retail_neo4j_user, settings.retail_neo4j_password or "")
    )


def build_semantic_reader(settings: RetailSettings, driver: Any) -> SemanticRead:
    """The graph store over an already-open driver. Pure construction; no async twin."""
    return SemanticGraphStore(driver=driver, database=settings.retail_neo4j_database)


@contextmanager
def open_semantic_reader(
    settings: RetailSettings, driver: Optional[Any] = None
) -> Iterator[SemanticRead]:
    """A reader for the duration of one block, closing the driver it opened on the way out.

    Pass ``driver`` to reuse one already open (a cookbook holding a session); the block then
    does not close it -- the caller that opened it owns it.
    """
    owned = driver is None
    handle: Any = driver if driver is not None else semantic_driver(settings)
    try:
        yield build_semantic_reader(settings, handle)
    finally:
        if owned:
            handle.close()
