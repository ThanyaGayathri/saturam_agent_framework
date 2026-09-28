"""The knowledge-layer stores a K10 fan-out needs, built once from settings.

``entrypoints/suggest.py`` and the playground's ``knowledge`` mode both want the same three
things -- an embedder, the prose store (pgvector or Azure AI Search, per
``knowledge/vector.py``), and the ontology store over Neo4j -- and
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

import asyncio
import threading
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Any, Iterator, Optional

from egpt.metrics import ProviderMode

from egpt_retail.app.settings import RetailSettings

__all__ = [
    "KnowledgeStores",
    "abuild_knowledge_stores",
    "build_knowledge_stores",
    "aopen_knowledge_stores",
    "open_knowledge_stores",
]


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


#: Serialises embedder construction. ``lru_cache`` holds no lock across the wrapped call, so
#: two concurrent misses both run it -- and since ``abuild_knowledge_stores`` dispatches to a
#: thread, those are two real threads each constructing a ``SentenceTransformerEmbedder`` and
#: each running its own first ``encode()``. That is exactly the condition the embedder's
#: construction warm-up exists to prevent, and it deadlocks both. The warm-up makes one
#: instance safe; this makes there be one instance.
_EMBEDDER_LOCK = threading.Lock()

#: Built stores, keyed by the settings that decide them. ``PgVector`` calls ``create_engine``
#: and builds a ``scoped_session`` in its constructor, so building the stores per question
#: leaked a control-plane connection pool per question -- the same defect ``app/db.py``'s
#: pooled accessor fixes for the warehouse, on the leg this branch introduced.
_STORES: dict = {}
_STORES_LOCK = threading.Lock()


@lru_cache(maxsize=2)
def _embedder_for_uncached(mode: ProviderMode) -> Any:
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


def _embedder_for(mode: ProviderMode) -> Any:
    """:func:`_embedder_for_uncached`, but at most one construction at a time."""
    with _EMBEDDER_LOCK:
        return _embedder_for_uncached(mode)


def build_knowledge_stores(settings: RetailSettings, driver: Any) -> KnowledgeStores:
    """Cached by the values that decide the stores; see :func:`_build_knowledge_stores`."""
    # Everything ``knowledge/vector.py`` reads to pick and address the store. The backend and
    # the Azure endpoint/index are part of the key for the same reason the table and db url
    # are: a key that omitted them would hand back a store built for a different backend.
    # The API key is deliberately not here -- a credential does not decide *which* store this
    # is, and it should not sit in a dict key.
    key = (
        settings.retail_provider_mode,
        settings.retail_knowledge_backend,
        settings.retail_knowledge_table,
        settings.retail_db_url,
        settings.retail_knowledge_search_endpoint,
        settings.retail_knowledge_search_index,
    )
    with _STORES_LOCK:
        cached = _STORES.get(key)
        if cached is not None:
            # The ontology store holds the driver it was built with. A cached one whose driver
            # has been closed would fail on read, so it is rebuilt against the live driver --
            # cheap, since the embedder and the PgVector engine are what cost anything.
            from egpt_retail.ontology.store import OntologyStore

            return replace(
                cached, ontology=OntologyStore(driver=driver, embedder=cached.embedder)
            )
        built = _build_knowledge_stores(settings, driver)
        _STORES[key] = built
        return built


def _build_knowledge_stores(settings: RetailSettings, driver: Any) -> KnowledgeStores:
    """The prose and ontology stores over an already-open Neo4j driver.

    Lifted verbatim from ``entrypoints/suggest.py``'s own assembly, which now calls this
    instead -- the point is that there is one copy, not that this one is new. The embedder
    comes from :func:`_embedder_for`, so calling this per request costs construction rather
    than a model load.
    """
    from egpt.knowledge.knowledge import Knowledge

    from egpt_retail.knowledge.vector import build_knowledge_vector_db
    from egpt_retail.ontology.store import OntologyStore

    resolved = _embedder_for(settings.retail_provider_mode)
    knowledge = Knowledge(
        vector_db=build_knowledge_vector_db(settings, resolved.embedder)
    )
    ontology = OntologyStore(driver=driver, embedder=resolved.embedder)
    return KnowledgeStores(
        knowledge=knowledge,
        ontology=ontology,
        embedder=resolved.embedder,
        embedder_model_id=resolved.model_id,
        dimensions=resolved.dimensions,
    )


async def abuild_knowledge_stores(
    settings: RetailSettings, driver: Any
) -> KnowledgeStores:
    """:func:`build_knowledge_stores` off the event loop.

    Not a formality. The first call loads ``bge-base-en-v1.5`` through sentence-transformers --
    measured at **9.5s**, CPU-bound, plus the warm-up encode. Called inline from an ``async def``
    tool, that blocks the whole ASGI loop: every other request, every in-flight SSE stream and
    the health check stall behind one person's first question. ``knowledge/embedding.py``'s own
    ``aresolve_knowledge_embedder`` makes the same move for the same reason.
    """
    return await asyncio.to_thread(build_knowledge_stores, settings, driver)


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


@asynccontextmanager
async def aopen_knowledge_stores(
    settings: RetailSettings, driver: Optional[Any] = None
) -> Any:
    """Async twin of :func:`open_knowledge_stores`, building off the event loop."""
    from egpt_retail.app.semantic import semantic_driver

    owned = driver is None
    handle: Any = driver if driver is not None else semantic_driver(settings)
    try:
        yield await abuild_knowledge_stores(settings, handle)
    finally:
        if owned:
            handle.close()
