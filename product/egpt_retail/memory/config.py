"""The memory provider config and resolver, from settings.

Every key here was checked against the framework rather than copied from a cookbook, because
one cookbook (``47_per_tenant_memory_config.py``) uses a ``dsn`` key that is never constructed.
The working shape is ``13_vector_substrate_pgvector.py``: ``backend`` and **``db_url``**.

* ``provider="co_located"`` -- the adapter registered in ``memory/adapters/co_located.py``.
* ``mode=ProviderMode.FRONTIER`` -- Mode B. Mode A (``LOCAL``) refuses every online embedder
  (``memory/embedding.py:331``, ``OFFLINE_CAPABLE``), and the deployment embeds through an Azure
  OpenAI deployment. Mode B's default embedder is ``azure_openai`` / ``text-embedding-3-large`` at
  1024 dimensions (``embedding.py:353``), configured through ``AZURE_EMBEDDER_*`` and consulted only
  on write and search, never at construction -- so a no-work tick needs no key.
* No ``graph`` key. ``resolve_substrate`` short-circuits on an empty mapping (``memory/config.py:384``)
  and ``CoLocatedMemoryProvider`` accepts ``graph=None``; consolidation is vector-only-safe
  (``co_located.py:1519, 2457``).
* The vector store self-provisions: ``CREATE EXTENSION IF NOT EXISTS vector``, schema, partitioned
  table and indexes on construction (``pgvector.py:407, 629``). The role must be allowed to create
  the extension; the compose Postgres user is the container superuser.
"""

from __future__ import annotations

from egpt.memory.config import MemoryConfigResolver, MemoryProviderConfig
from egpt.providers.model_provider import ProviderMode

from egpt_retail.app.settings import RetailSettings

__all__ = ["build_memory_config", "build_memory_resolver"]

#: Per-deployment HNSW search width. 256 reaches recall@10 0.967 at 100k rows per tenant for a
#: p95 of 7 ms (benchmarks/memory_vectordb/EF_SWEEP.md); the framework's old constant was 64.
EF_SEARCH = 256


def build_memory_config(settings: RetailSettings) -> MemoryProviderConfig:
    """The one config every tenant resolves to in phase zero. Pure construction."""
    return MemoryProviderConfig(
        provider="co_located",
        mode=ProviderMode.FRONTIER,
        vector={
            "backend": "pgvector",
            "db_url": settings.retail_db_url,
            "ef_search": EF_SEARCH,
        },
    )


def build_memory_resolver(settings: RetailSettings) -> MemoryConfigResolver:
    """A resolver with one default. Per-tenant policies are a P1 concern; the seam is here."""
    return MemoryConfigResolver(default=build_memory_config(settings))
