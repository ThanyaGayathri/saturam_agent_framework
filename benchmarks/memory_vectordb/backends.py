"""Backend factory — builds pgvector / qdrant / milvus behind the egpt VectorDb ABC.

The whole point of SM-R3 is that switching stores is plug-and-play: every backend here
is constructed the same way (embedder + search_type + cosine distance) and driven only
through the VectorDb interface (create / insert / search / drop). Nothing in the harness
knows which concrete store it is talking to.

Connection defaults match the repo's docker scripts:
- pgvector : cookbook/scripts/run_pgvector.sh  (port 5532, db/user/pass = ai)
- qdrant   : cookbook/scripts/run_qdrant.sh    (port 6333)
- milvus   : benchmarks/memory_vectordb/docker/milvus-compose.yml (port 19530)

Override any endpoint with env vars: PGV_URL, QDRANT_URL, MILVUS_URI.
"""

from __future__ import annotations

import os
from typing import Callable

from egpt.knowledge.embedder import Embedder
from egpt.vectordb.base import VectorDb
from egpt.vectordb.distance import Distance
from egpt.vectordb.search import SearchType

PGV_URL = os.environ.get("PGV_URL", "postgresql+psycopg://ai:ai@localhost:5532/ai")
QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
MILVUS_URI = os.environ.get("MILVUS_URI", "http://localhost:19530")

# A builder takes a collection/table name and returns a ready VectorDb instance.
Builder = Callable[[str], VectorDb]


def _pgvector_builder(embedder: Embedder, search_type: SearchType) -> Builder:
    from egpt.vectordb.pgvector import PgVector
    from egpt.vectordb.pgvector.index import HNSW

    def build(name: str) -> VectorDb:
        return PgVector(
            table_name=name,
            schema="ai",
            db_url=PGV_URL,
            embedder=embedder,
            search_type=search_type,
            vector_index=HNSW(),
            distance=Distance.cosine,
        )

    return build


def _qdrant_builder(embedder: Embedder, search_type: SearchType) -> Builder:
    from egpt.vectordb.qdrant import Qdrant

    def build(name: str) -> VectorDb:
        return Qdrant(
            collection=name,
            url=QDRANT_URL,
            embedder=embedder,
            search_type=search_type,
            distance=Distance.cosine,
            # Default qdrant-client REST timeout is 5s; bulk insert / index build at 1M+
            # vectors exceeds it. Production clients should set a generous timeout.
            timeout=120.0,
        )

    return build


def _milvus_builder(embedder: Embedder, search_type: SearchType) -> Builder:
    from egpt.vectordb.milvus import Milvus

    def build(name: str) -> VectorDb:
        return Milvus(
            collection=name,
            uri=MILVUS_URI,
            embedder=embedder,
            search_type=search_type,
            distance=Distance.cosine,
        )

    return build


BUILDERS = {
    "pgvector": _pgvector_builder,
    "qdrant": _qdrant_builder,
    "milvus": _milvus_builder,
}


def get_builder(backend: str, embedder: Embedder, search_type: SearchType) -> Builder:
    if backend not in BUILDERS:
        raise ValueError(f"Unknown backend '{backend}'. Choices: {sorted(BUILDERS)}")
    return BUILDERS[backend](embedder, search_type)
