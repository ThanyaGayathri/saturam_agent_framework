"""The memory provider config and resolver, from settings.

Every key here was checked against the framework rather than copied from a cookbook, because
one cookbook (``47_per_tenant_memory_config.py``) uses a ``dsn`` key that is never constructed.
The working shape is ``13_vector_substrate_pgvector.py``: ``backend`` and **``db_url``**.

* ``provider="co_located"`` -- the adapter registered in ``memory/adapters/co_located.py``.
* ``mode`` comes from ``settings.retail_provider_mode`` (K1). It was hardcoded to Mode B, which
  was right for the Azure deployment and wrong as a constant: the knowledge layers read the same
  setting, and a hardcoded mode here meant memory could embed at Mode B's 1024 while prose and
  the ontology embedded at Mode A's 768. Three vector spaces behind one retrieval entry point,
  failing silently as worse answers. One setting, both subsystems.

* Mode B is the deployment's choice. Mode A (``LOCAL``) refuses every online embedder
  (``memory/embedding.py:331``, ``OFFLINE_CAPABLE``), and the deployment embeds through an Azure
  OpenAI deployment. Mode B's default embedder is ``azure_openai`` / ``text-embedding-3-large`` at
  1024 dimensions (``embedding.py:353``), configured through ``AZURE_EMBEDDER_*`` and consulted only
  on write and search, never at construction -- so a no-work tick needs no key.
* No ``graph`` key. ``resolve_substrate`` short-circuits on an empty mapping (``memory/config.py:384``)
  and ``CoLocatedMemoryProvider`` accepts ``graph=None``; consolidation is vector-only-safe
  (``co_located.py:1519, 2457``).
* The vector backend is ``settings.retail_memory_backend`` (AZ-B, config-only from this side --
  the seam's whole point). ``azure_ai_search`` is the deployment's default and the dict below
  carries exactly the kwargs ``AzureAISearchMemorySubstrate.__init__`` names (``endpoint``,
  ``api_key``, ``index_name``, ``ef_search``); ``resolve_substrate`` forwards every key except
  ``backend`` to the constructor and adds ``mode`` because the constructor declares it. The
  endpoint and key are passed through from settings rather than left to the substrate's own
  ``EGPT_MEMORY_AZURE_SEARCH_*`` fallbacks, so a missing endpoint is refused *here*, naming the
  ``EGPT_RETAIL_*`` variable the deployment actually sets. ``pgvector`` is the laptop and kind
  option; its dict is unchanged from before the flip.
* Both stores self-provision on construction: pgvector runs ``CREATE EXTENSION IF NOT EXISTS
  vector``, schema, partitioned table and indexes (``pgvector.py:407, 629``) and the role must be
  allowed to create the extension (the compose Postgres user is the container superuser); Azure
  AI Search calls ``create_or_update_index`` (``azure_ai_search.py``, ``auto_init_schema``), so
  construction is one HTTPS round trip and needs the service reachable. Neither is lazy.
"""

from __future__ import annotations

from typing import Any, Dict

from egpt.memory.config import MemoryConfigResolver, MemoryProviderConfig
from egpt.memory.errors import MemoryProviderConfigError

from egpt_retail.app.settings import RetailSettings

__all__ = [
    "build_memory_config",
    "build_memory_resolver",
    "build_memory_vector_settings",
]

#: Per-deployment HNSW search width. 256 reaches recall@10 0.967 at 100k rows per tenant for a
#: p95 of 7 ms (benchmarks/memory_vectordb/EF_SWEEP.md); the framework's old constant was 64.
#: Carried to Azure AI Search unchanged: its HNSW ``efSearch`` has the same meaning and the
#: substrate accepts the same range (``_checked_hnsw_params``); no Azure-side sweep has been run.
EF_SEARCH = 256


def build_memory_vector_settings(settings: RetailSettings) -> Dict[str, Any]:
    """The ``vector`` bag ``resolve_substrate`` constructs the backend from. Pure construction.

    Refuses an ``azure_ai_search`` backend with no endpoint rather than passing ``None`` down:
    the substrate would refuse too, but naming ``EGPT_MEMORY_AZURE_SEARCH_ENDPOINT`` -- a
    variable this deployment never sets -- instead of the ``EGPT_RETAIL_*`` one that fixes it.
    """
    if settings.retail_memory_backend == "pgvector":
        return {
            "backend": "pgvector",
            "db_url": settings.retail_db_url,
            "table": settings.retail_memory_table,
            "ef_search": EF_SEARCH,
        }
    endpoint = (settings.retail_memory_search_endpoint or "").strip()
    if not endpoint:
        raise MemoryProviderConfigError(
            "EGPT_RETAIL_MEMORY_BACKEND is 'azure_ai_search' but "
            "EGPT_RETAIL_MEMORY_SEARCH_ENDPOINT is unset. Set it to the Azure AI Search "
            "service endpoint (https://<service>.search.windows.net), or set "
            "EGPT_RETAIL_MEMORY_BACKEND=pgvector on a machine without one.",
            field_name="retail_memory_search_endpoint",
        )
    return {
        "backend": "azure_ai_search",
        "endpoint": endpoint,
        "api_key": settings.retail_memory_search_api_key,
        "index_name": settings.retail_memory_search_index,
        "ef_search": EF_SEARCH,
    }


def build_memory_config(settings: RetailSettings) -> MemoryProviderConfig:
    """The one config every tenant resolves to in phase zero. Pure construction."""
    return MemoryProviderConfig(
        provider="co_located",
        mode=settings.retail_provider_mode,
        vector=build_memory_vector_settings(settings),
    )


def build_memory_resolver(settings: RetailSettings) -> MemoryConfigResolver:
    """A resolver with one default. Per-tenant policies are a P1 concern; the seam is here."""
    return MemoryConfigResolver(default=build_memory_config(settings))
