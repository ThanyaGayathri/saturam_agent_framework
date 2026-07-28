"""Retriever abstraction + dense and sparse adapters.

A `Retriever` maps a `Query` to a ranked `[(doc_id, score)]` list. Two adapters:

- `DenseRetriever` -- cosine over egpt-`Embedder` vectors (in-process numpy). Kept
  store-agnostic on purpose: fusion is rank/score math, so isolating the retriever
  from a specific vector store gives a clean, reproducible fusion comparison. The
  live pgvector hybrid path is cross-checked separately (see DECISION.md); its
  weighted fusion is reproduced faithfully in `fusion.WeightedFusion`.
- `BM25Retriever` -- Okapi BM25 sparse retrieval (`rank_bm25`), the sparse leg of
  the hybrid.

`Query` carries both the text (for BM25 / cross-encoder) and the precomputed vector
(for dense), so every retriever shares one uniform `search` signature. Both sync and
async are implemented.
"""

from __future__ import annotations

import asyncio
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

Scored = Tuple[str, float]


@dataclass
class Query:
    qid: str
    text: str
    vector: Optional[np.ndarray] = None


class Retriever(ABC):
    name: str = "retriever"

    @abstractmethod
    def index(self, corpus: Dict[str, str]) -> None: ...

    @abstractmethod
    def search(self, query: Query, top_k: int) -> List[Scored]: ...

    async def aindex(self, corpus: Dict[str, str]) -> None:
        self.index(corpus)

    async def asearch(self, query: Query, top_k: int) -> List[Scored]:
        return await asyncio.to_thread(self.search, query, top_k)


class DenseRetriever(Retriever):
    """Cosine similarity over precomputed, unit-normalized embeddings."""

    name = "dense"

    def __init__(
        self,
        doc_vectors: Optional[np.ndarray] = None,
        doc_ids: Optional[List[str]] = None,
    ):
        self._ids: List[str] = doc_ids or []
        self._mat: Optional[np.ndarray] = doc_vectors  # (n, d), unit-normalized

    def set_matrix(self, doc_ids: List[str], mat: np.ndarray) -> None:
        self._ids = doc_ids
        self._mat = mat

    def index(self, corpus: Dict[str, str]) -> None:
        # Vectors are supplied out of band (via the embedding cache) and matched by
        # id; index() only validates alignment.
        if self._mat is None or len(self._ids) != self._mat.shape[0]:
            raise RuntimeError(
                "DenseRetriever needs doc vectors set via set_matrix() first"
            )

    def search(self, query: Query, top_k: int) -> List[Scored]:
        if query.vector is None or self._mat is None:
            raise RuntimeError(
                "DenseRetriever requires a query vector and an indexed matrix"
            )
        # macOS Accelerate BLAS raises spurious FP-exception RuntimeWarnings (divide-by-zero/
        # overflow/invalid) on clean unit-norm float32 matmuls; verified the output has no
        # actual NaN/Inf (PR #16 review follow-up) -- suppress the warning, not the computation.
        with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
            sims = self._mat @ query.vector.astype(np.float32)
        k = min(top_k, sims.shape[0])
        idx = np.argpartition(-sims, k - 1)[:k]
        idx = idx[np.argsort(-sims[idx])]
        return [(self._ids[i], float(sims[i])) for i in idx]


_TOKEN = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> List[str]:
    return _TOKEN.findall(text.lower())


class BM25Retriever(Retriever):
    """Okapi BM25 sparse retrieval."""

    name = "bm25"

    def __init__(self) -> None:
        self._ids: List[str] = []
        self._bm25 = None

    def index(self, corpus: Dict[str, str]) -> None:
        from rank_bm25 import BM25Okapi

        self._ids = list(corpus.keys())
        tokenized = [_tokenize(corpus[i]) for i in self._ids]
        # Guard against empty docs (BM25Okapi divides by avgdl).
        tokenized = [toks or ["<empty>"] for toks in tokenized]
        self._bm25 = BM25Okapi(tokenized)

    def search(self, query: Query, top_k: int) -> List[Scored]:
        if self._bm25 is None:
            raise RuntimeError("BM25Retriever.index() not called")
        scores = self._bm25.get_scores(_tokenize(query.text))
        k = min(top_k, len(scores))
        idx = np.argpartition(-scores, k - 1)[:k]
        idx = idx[np.argsort(-scores[idx])]
        return [(self._ids[i], float(scores[i])) for i in idx]
