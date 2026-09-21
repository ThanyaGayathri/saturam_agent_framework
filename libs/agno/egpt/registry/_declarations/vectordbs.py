"""Built-in vector DB backend declarations.

One row per backend package:

    key -> (module, class_name)

New in EGPT-1.9. Vector dbs were the one integration kind with no name-to-class seam at
all -- ``tests/unit/vectordb/test_backend_layout.py`` said so in its own docstring -- so
these rows are written from the tree rather than migrated from an existing table.

``key`` is the backend's package directory name, matching the convention the db rows
already use. Not the class name: that would make ``chroma``/``ChromaDb`` and
``pineconedb``/``PineconeDb`` disagree about what to call the same backend, and it is the
directory that the layout guards already enumerate.

Five keys here also exist under the ``db`` kind -- ``clickhouse``, ``redis``,
``singlestore``, ``surrealdb``, and ``mongodb`` against db's ``mongo``. They are different
products with different classes, which is why the registry is keyed on ``(kind, name)``
rather than a flat namespace. Do not "harmonize" ``mongo`` and ``mongodb``: each matches
its own package directory, and changing either would break the key that backend's own
layer-guard enumerates.

Nothing in this repository constructs a vector db by name today; user code instantiates the
class it wants. These rows exist so that AC3 holds, so the layout guard can check every
backend has a row, and so DB-defined-agent hydration (decision D2) has the seam it needs.
"""

from egpt.registry.discovery import SOURCE_BUILTIN, register

_ROWS = {
    # AZ-C: the knowledge-side (K2) Azure AI Search backend, keyed on its package directory like
    # every other row. Its memory-side twin is the ``vectorstore`` kind's row of the same name.
    "azure_ai_search": ("egpt.integrations.vectordb.azure_ai_search", "AzureAISearch"),
    "cassandra": ("egpt.integrations.vectordb.cassandra", "Cassandra"),
    "chroma": ("egpt.integrations.vectordb.chroma", "ChromaDb"),
    "clickhouse": ("egpt.integrations.vectordb.clickhouse", "Clickhouse"),
    "couchbase": ("egpt.integrations.vectordb.couchbase", "CouchbaseSearch"),
    "lancedb": ("egpt.integrations.vectordb.lancedb", "LanceDb"),
    "langchaindb": ("egpt.integrations.vectordb.langchaindb", "LangChainVectorDb"),
    "lightrag": ("egpt.integrations.vectordb.lightrag", "LightRag"),
    "llamaindex": ("egpt.integrations.vectordb.llamaindex", "LlamaIndexVectorDb"),
    "milvus": ("egpt.integrations.vectordb.milvus", "Milvus"),
    "mongodb": ("egpt.integrations.vectordb.mongodb", "MongoDb"),
    "pgvector": ("egpt.integrations.vectordb.pgvector", "PgVector"),
    "pineconedb": ("egpt.integrations.vectordb.pineconedb", "PineconeDb"),
    "qdrant": ("egpt.integrations.vectordb.qdrant", "Qdrant"),
    "redis": ("egpt.integrations.vectordb.redis", "RedisDB"),
    "singlestore": ("egpt.integrations.vectordb.singlestore", "SingleStore"),
    "surrealdb": ("egpt.integrations.vectordb.surrealdb", "SurrealDb"),
    "upstashdb": ("egpt.integrations.vectordb.upstashdb", "UpstashVectorDb"),
    "weaviate": ("egpt.integrations.vectordb.weaviate", "Weaviate"),
}


def register_all() -> None:
    """Register every row. Called by the discovery seam, and safe to call again."""
    for key, (module, class_name) in _ROWS.items():
        register("vectordb", key, module=module, attr=class_name, source=SOURCE_BUILTIN, override=True)
