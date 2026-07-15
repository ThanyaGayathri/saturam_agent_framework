"""
Weaviate Upsert
===============

Demonstrates repeated inserts with `skip_if_exists` in Weaviate.
"""

from egpt.knowledge.embedder.sentence_transformer import SentenceTransformerEmbedder
from egpt.knowledge.knowledge import Knowledge
from egpt.utils.log import set_log_level_to_debug
from egpt.vectordb.search import SearchType
from egpt.vectordb.weaviate import Distance, VectorIndex, Weaviate

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
embedder = SentenceTransformerEmbedder()
vector_db = Weaviate(
    collection="recipes",
    search_type=SearchType.hybrid,
    vector_index=VectorIndex.HNSW,
    distance=Distance.COSINE,
    embedder=embedder,
    local=True,
)


# ---------------------------------------------------------------------------
# Create Knowledge Base
# ---------------------------------------------------------------------------
knowledge_base = Knowledge(vector_db=vector_db)


# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------
def main() -> None:
    vector_db.drop()
    set_log_level_to_debug()

    knowledge_base.insert(
        url="https://agno-public.s3.amazonaws.com/recipes/ThaiRecipes.pdf"
    )
    print(
        "Knowledge base loaded with PDF content. Loading the same data again will not recreate it."
    )

    knowledge_base.insert(
        url="https://agno-public.s3.amazonaws.com/recipes/ThaiRecipes.pdf",
        skip_if_exists=True,
    )

    vector_db.drop()


if __name__ == "__main__":
    main()
