"""The knowledge-layer stores a K10 fan-out needs, built once from settings.

``entrypoints/suggest.py`` and the playground's ``knowledge`` mode both want the same three
things -- an embedder, the prose store over pgvector, and the ontology store over Neo4j -- and
until this module ``suggest.py`` was the only one that built them. The playground therefore
fanned out over the semantic leg alone, which made "chat with the knowledge base" search table
and column names rather than the knowledge base.

Two lines of this are load-bearing and are the reason it is one helper rather than two:

* **One embedder decides two vector spaces.** The prose index and the ontology index are both
  built from ``resolve_knowledge_embedder``'s output, so a deployment that resolved them
  separately could end up with a 768-wide prose table and a 1024-wide ontology index and no
  error until a query returned nonsense. ``knowledge/embedding.py`` records that decision;
  this module is what stops a second caller re-deciding it.
* **The Neo4j driver is shared with the semantic reader.** ``OntologyStore`` and
  ``SemanticGraphStore`` read the same graph, so opening two drivers for one fan-out is two
  connection pools for one question. :func:`open_knowledge_stores` takes a driver the caller
  already has, exactly as ``open_semantic_reader`` does.

Construction only: nothing here runs a query, and the imports that carry weight (torch, via
sentence-transformers, in Mode A) are function-local so importing this module stays cheap for
the callers that never ask for the stores.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Iterator, Optional

from egpt.metrics import ProviderMode

from egpt_retail.app.settings import RetailSettings

__all__ = ["KnowledgeStores", "build_knowledge_stores", "open_knowledge_stores"]


@dataclass(frozen=True)
class KnowledgeStores:
    """The prose and ontology stores, plus the embedder both were built from.

    ``embedder_model_id`` and ``dimensions`` are carried rather than recoverable from the
    stores, because the thing worth asserting in a test -- that one embedder built both -- is
    invisible once the stores exist.
    """

    knowledge: Any
    ontology: Any
    embedder: Any
    embedder_model_id: str
    dimensions: int


@lru_cache(maxsize=2)
def _embedder_for(mode: ProviderMode) -> Any:
    """The resolved embedder for a provider mode, once per process.

    This is the expensive line. In Mode A ``resolve_knowledge_embedder`` loads
    ``bge-base-en-v1.5`` through sentence-transformers -- measured at **9.5s** on a warm
    cache, and it is pure model-loading, not I/O. The stores around it are cheap
    construction, so caching the embedder alone keeps ``build_knowledge_stores`` callable
    per request (which is what lets the driver stay per-request, as ``modes.py`` wants)
    without paying for a transformer every time someone asks a question.

    Keyed by mode rather than by settings: the mode is what decides the model and the
    dimension (``knowledge/embedding.py``'s table), and two deployments in one process is not
    a thing that happens.

    An embedder is stateless once loaded -- it embeds text and returns vectors -- so sharing
    one across callers is safe in a way sharing a connection would not be.
    """
    from egpt_retail.knowledge.embedding import resolve_knowledge_embedder

    return resolve_knowledge_embedder(mode=mode)


def build_knowledge_stores(settings: RetailSettings, driver: Any) -> KnowledgeStores:
    """The prose and ontology stores over an already-open Neo4j driver.

    Lifted verbatim from ``entrypoints/suggest.py``'s own assembly, which now calls this
    instead -- the point is that there is one copy, not that this one is new. The embedder
    comes from :func:`_embedder_for`, so calling this per request costs construction rather
    than a model load.
    """
    from egpt.knowledge.knowledge import Knowledge
    from egpt.vectordb.pgvector import PgVector
    from egpt.vectordb.search import SearchType

    from egpt_retail.ontology.store import OntologyStore

    resolved = _embedder_for(settings.retail_provider_mode)
    knowledge = Knowledge(
        vector_db=PgVector(
            table_name=settings.retail_knowledge_table,
            db_url=settings.retail_db_url,
            search_type=SearchType.hybrid,
            embedder=resolved.embedder,
        )
    )
    ontology = OntologyStore(driver=driver, embedder=resolved.embedder)
    return KnowledgeStores(
        knowledge=knowledge,
        ontology=ontology,
        embedder=resolved.embedder,
        embedder_model_id=resolved.model_id,
        dimensions=resolved.dimensions,
    )


@contextmanager
def open_knowledge_stores(
    settings: RetailSettings, driver: Optional[Any] = None
) -> Iterator[KnowledgeStores]:
    """The stores for the duration of one block, closing the driver it opened on the way out.

    Mirrors ``open_semantic_reader``: pass a ``driver`` to reuse one already open, and the
    block leaves it open because whoever opened it owns it.
    """
    from egpt_retail.app.semantic import semantic_driver

    owned = driver is None
    handle: Any = driver if driver is not None else semantic_driver(settings)
    try:
        yield build_knowledge_stores(settings, handle)
    finally:
        if owned:
            handle.close()
