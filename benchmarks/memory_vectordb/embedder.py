"""Deterministic, offline embedder for the SM-R3 vector-store benchmark.

The benchmark compares vector *stores*, not embedding models. Every backend must
index the exact same vectors so that latency and recall are compared apples-to-apples,
and we must avoid paying an embedding API (money + network jitter) for millions of docs.

This embedder maps text -> a fixed pseudo-random unit vector, seeded by a hash of the
text. Properties that matter here:

- Deterministic: the same text always yields the same vector, on every backend and run.
- Offline + cheap: pure numpy, no network, so insert time reflects the store, not the model.
- Normalized: unit vectors, so cosine and inner-product distances behave consistently.

Because vectors are fully determined by content, we can compute exact brute-force
nearest neighbours over the corpus (see dataset.py) and measure each store's recall
against that ground truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import blake2b
from typing import Dict, List, Optional, Tuple

import numpy as np

from egpt.knowledge.embedder import Embedder


@dataclass
class DeterministicEmbedder(Embedder):
    """Hash-seeded unit-vector embedder. Not semantic — for store benchmarking only."""

    dimensions: Optional[int] = 768
    enable_batch: bool = True
    batch_size: int = 512

    def _vector(self, text: str) -> np.ndarray:
        # 8-byte digest -> uint64 seed. Stable across processes/machines.
        digest = blake2b(text.encode("utf-8"), digest_size=8).digest()
        seed = int.from_bytes(digest, "big")
        rng = np.random.default_rng(seed)
        vec = rng.standard_normal(self.dimensions).astype(np.float32)
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec /= norm
        return vec

    def get_embedding(self, text: str) -> List[float]:
        return self._vector(text).tolist()

    def get_embedding_and_usage(self, text: str) -> Tuple[List[float], Optional[Dict]]:
        return self.get_embedding(text), None

    async def async_get_embedding(self, text: str) -> List[float]:
        return self.get_embedding(text)

    async def async_get_embedding_and_usage(
        self, text: str
    ) -> Tuple[List[float], Optional[Dict]]:
        return self.get_embedding(text), None

    # Batch variants — pgvector/qdrant/milvus insert paths use these when enable_batch=True,
    # which keeps bulk insert fast enough to reach 1M+ vectors.
    def get_embeddings_batch_and_usage(
        self, texts: List[str]
    ) -> Tuple[List[List[float]], List[Optional[Dict]]]:
        embeddings = [self._vector(t).tolist() for t in texts]
        usages: List[Optional[Dict]] = [None] * len(texts)
        return embeddings, usages

    async def async_get_embeddings_batch_and_usage(
        self, texts: List[str]
    ) -> Tuple[List[List[float]], List[Optional[Dict]]]:
        return self.get_embeddings_batch_and_usage(texts)

    def matrix(self, texts: List[str]) -> np.ndarray:
        """Return an (n, dim) float32 matrix of unit vectors for exact-NN ground truth."""
        return np.vstack([self._vector(t) for t in texts])
