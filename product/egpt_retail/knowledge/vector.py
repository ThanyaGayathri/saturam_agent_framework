"""Which vector store the domain prose lives in -- AZ-C of ``docs/plan/azure-migration-plan.md``.

K2's prose goes through ``egpt.knowledge.Knowledge`` over a plain ``VectorDb``, not through the
memory Protocol pair AZ-B built, so the choice of backend is made here once and the two
entrypoints (``load-knowledge`` writes, ``suggest`` reads) and the four K-cookbooks all take
the store from this one function rather than each naming ``PgVector`` by hand. Before this
module, the backend was pinned in four places plus two, and a deployment moving to Azure AI
Search would have had to find them all.

**What is inherited, not decided.** The embedder dimension and the hybrid-scoring semantic are
AZ-B's decisions (``cookbook/95_azure_migration/AZ_B_dimension_and_scoring_decisions.md``):

* The index width is whatever ``egpt_retail.knowledge.embedding`` resolves -- 768 for Mode A,
  1024 for Mode B -- on both backends. ``AzureAISearch`` reads it from the embedder it is
  handed, exactly as ``PgVector`` does; nothing here restates a number.
* Both backends run ``SearchType.hybrid``. On pgvector that is the weighted
  ``vector_score_weight * vector_score + text_rank_weight * text_rank`` blend; on Azure AI
  Search it is the service's native reciprocal-rank fusion. AZ-B's decision 2 recommends the
  native fusion for this ``VectorDb`` port and asks that the difference be said plainly: the
  ``similarity_score`` a result carries is not the same magnitude across the two. Callers
  that rank by it are unaffected; a caller reading the absolute value as calibrated is not.
  ``retail_unified_read`` (K10) fuses by rank, so it is the former.

**Default is the production truth.** ``retail_knowledge_backend`` defaults to
``azure_ai_search`` for the same reason ``retail_provider_mode`` defaults to ``frontier``: a
deployment that forgets to configure it should fail at construction naming the endpoint
variable, not write prose into a Postgres nothing reads. A laptop against compose Postgres
sets ``EGPT_RETAIL_KNOWLEDGE_BACKEND=pgvector`` explicitly, and the K-cookbooks do so at the
top of the file.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from egpt_retail.app.settings import RetailSettings

if TYPE_CHECKING:
    from egpt.vectordb.base import VectorDb

__all__ = ["build_knowledge_vector_db"]


def build_knowledge_vector_db(settings: RetailSettings, embedder: Any) -> "VectorDb":
    """The vector store this deployment keeps its domain prose in.

    Pure construction: both backends build a client that opens nothing until the first
    insert or search, so there is no async twin (the standing exemption in ``CLAUDE.md``).
    The I/O, and both of its variants, live on the ``VectorDb`` itself.

    ``embedder`` is the resolved ``KnowledgeEmbedder.embedder`` -- the model, not the wrapper --
    because that is what ``PgVector`` and ``AzureAISearch`` take, and the dimension the index
    is built at is read from it rather than restated here.

    The backend classes are imported inside the branch that needs them, as the entrypoints
    already did for ``PgVector``: neither client library is a cost the other backend should
    pay at import, and a test can stand in a fake for either without a database or a service.
    """
    from egpt.vectordb.search import SearchType

    backend = settings.retail_knowledge_backend
    if backend == "pgvector":
        from egpt.integrations.vectordb.pgvector import PgVector

        return PgVector(
            table_name=settings.retail_knowledge_table,
            db_url=settings.retail_db_url,
            search_type=SearchType.hybrid,
            embedder=embedder,
        )
    if backend == "azure_ai_search":
        if not settings.retail_knowledge_search_endpoint:
            raise RuntimeError(
                "EGPT_RETAIL_KNOWLEDGE_BACKEND is 'azure_ai_search' (the default) but "
                "EGPT_RETAIL_KNOWLEDGE_SEARCH_ENDPOINT names no service. Set it to the "
                "Azure AI Search endpoint, or set EGPT_RETAIL_KNOWLEDGE_BACKEND=pgvector "
                "to use the compose Postgres on a laptop."
            )
        # AZ-B's VectorDb, built in parallel with this package; the ignore keeps product
        # mypy clean on a checkout where it has not merged yet and is harmless once it has.
        from egpt.integrations.vectordb.azure_ai_search import (  # type: ignore[import-not-found]
            AzureAISearch,
        )

        return AzureAISearch(
            index_name=settings.retail_knowledge_search_index,
            endpoint=settings.retail_knowledge_search_endpoint,
            api_key=settings.retail_knowledge_search_api_key,
            embedder=embedder,
            search_type=SearchType.hybrid,
        )
    # Pydantic's Literal validation on RetailSettings rejects a typo before this runs; this
    # is the floor for whatever reaches here regardless, same as build_object_store.
    raise ValueError(
        f"Unrecognized EGPT_RETAIL_KNOWLEDGE_BACKEND {backend!r}. Expected 'pgvector' or "
        "'azure_ai_search'."
    )
