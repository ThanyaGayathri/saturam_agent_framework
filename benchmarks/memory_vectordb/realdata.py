"""Real-embedding data source for the SM-R3 benchmark (addresses review #3).

The v1 benchmark used i.i.d. Gaussian unit vectors — near-orthogonal, trivially
separable, which pinned recall@10 to 1.0 and hid HNSW-quality differences. This module
replaces that with a **public ANN dataset** (ann-benchmarks GloVe, cosine/"angular"),
so vectors carry realistic cluster structure and recall becomes a real signal.

How it plugs in without changing the store interface: `DatasetEmbedder` is an `Embedder`
that returns a *precomputed real vector* selected by an id encoded in the document text
("R:<i>" for a record, "Q:<qid>" for a query). Inserts/searches still go through the
egpt `VectorDb` interface unchanged — only the vectors are now real.

Queries are **discriminative**: each query is a real in-tenant record vector perturbed by
small noise (a realistic "find similar memories" probe), so recall measures whether the
index can separate true near-neighbours in a dense region — not just pick one of N
near-orthogonal points. Ground truth is exact cosine top-k within the query's tenant.

Dataset: http://ann-benchmarks.com/glove-100-angular.hdf5 (1.18M x 100d, cosine).
Path via env MEMBENCH_ANN_HDF5, else the default cache path below.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

from egpt.knowledge.embedder import Embedder

DEFAULT_HDF5 = os.environ.get(
    "MEMBENCH_ANN_HDF5",
    os.path.expanduser(
        "/private/tmp/claude-501/-Users-welcome-aicoe-saturam-agent-framework/"
        "64af95b9-81ed-4d30-a300-7748f20f152c/scratchpad/data/glove-100-angular.hdf5"
    ),
)


def _normalize(mat: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (mat / norms).astype(np.float32)


def load_train_vectors(
    path: str = DEFAULT_HDF5, limit: Optional[int] = None
) -> np.ndarray:
    """Load and unit-normalize the dataset's base ('train') vectors."""
    import h5py

    if not os.path.exists(path):
        raise FileNotFoundError(
            f"ANN dataset not found at {path}. Download with:\n"
            f"  curl -L -o {path} http://ann-benchmarks.com/glove-100-angular.hdf5"
        )
    with h5py.File(path, "r") as f:
        train = np.asarray(f["train"])
    if limit is not None:
        train = train[:limit]
    return _normalize(train)


@dataclass
class Record:
    id: str
    content: str  # "R:<train_index>"
    tenant_id: int
    train_index: int


class DatasetEmbedder(Embedder):
    """Embedder that returns precomputed real vectors keyed by id in the text.

    Not a text model — it maps "R:<i>" -> train[i] and "Q:<qid>" -> a registered query
    vector, so the store benchmark uses real embeddings without re-embedding millions.
    """

    def __init__(self, train: np.ndarray):
        self.train = train
        self.dimensions = int(train.shape[1])
        self.enable_batch = True
        self.batch_size = 1024
        self._query_vecs: Dict[str, np.ndarray] = {}

    def register_query(self, qid: str, vec: np.ndarray) -> None:
        self._query_vecs[qid] = vec.astype(np.float32)

    def _vec(self, content: str) -> np.ndarray:
        kind, _, idx = content.partition(":")
        if kind == "R":
            return self.train[int(idx)]
        if kind == "Q":
            return self._query_vecs[content]
        raise ValueError(f"unrecognized content key: {content!r}")

    def get_embedding(self, text: str) -> List[float]:
        return self._vec(text).tolist()

    def get_embedding_and_usage(self, text: str) -> Tuple[List[float], Optional[Dict]]:
        return self.get_embedding(text), None

    async def async_get_embedding(self, text: str) -> List[float]:
        return self.get_embedding(text)

    async def async_get_embedding_and_usage(
        self, text: str
    ) -> Tuple[List[float], Optional[Dict]]:
        return self.get_embedding(text), None

    def get_embeddings_batch_and_usage(self, texts: List[str]):
        return [self._vec(t).tolist() for t in texts], [None] * len(texts)

    async def async_get_embeddings_batch_and_usage(self, texts: List[str]):
        return self.get_embeddings_batch_and_usage(texts)


def build_records(
    n: int, num_tenants: int, train: np.ndarray, seed: int = 7
) -> List[Record]:
    """Assign the first n dataset vectors to tenants (round-robin)."""
    if n > train.shape[0]:
        raise ValueError(f"requested {n} vectors but dataset only has {train.shape[0]}")
    records: List[Record] = []
    for i in range(n):
        records.append(
            Record(
                id=f"mem-{i}",
                content=f"R:{i}",
                tenant_id=int(i % num_tenants),
                train_index=i,
            )
        )
    return records


def build_queries(
    m: int,
    records: List[Record],
    embedder: DatasetEmbedder,
    seed: int = 99,
    noise: float = 0.05,
) -> List[Dict]:
    """Build m discriminative queries: perturbed copies of random in-tenant record vectors.

    Registers each query's vector with the embedder and tags it with its tenant.
    """
    rng = np.random.default_rng(seed)
    by_tenant: Dict[int, List[int]] = {}
    for idx, r in enumerate(records):
        by_tenant.setdefault(r.tenant_id, []).append(idx)
    tenants = sorted(by_tenant)

    queries: List[Dict] = []
    for j in range(m):
        tenant = tenants[j % len(tenants)]
        anchor_ridx = by_tenant[tenant][rng.integers(len(by_tenant[tenant]))]
        base = embedder.train[records[anchor_ridx].train_index]
        vec = base + noise * rng.standard_normal(base.shape).astype(np.float32)
        vec /= np.linalg.norm(vec) or 1.0
        qid = f"Q:{j}"
        embedder.register_query(qid, vec)
        queries.append({"text": qid, "tenant_id": tenant})
    return queries


def exact_topk_per_tenant(
    records: List[Record],
    queries: List[Dict],
    embedder: DatasetEmbedder,
    k: int,
) -> List[List[str]]:
    """Exact cosine top-k record ids within each query's own tenant (ground truth)."""
    by_tenant: Dict[int, List[int]] = {}
    for idx, r in enumerate(records):
        by_tenant.setdefault(r.tenant_id, []).append(idx)

    # Cache per-tenant matrices of real vectors.
    tenant_mat: Dict[int, np.ndarray] = {}
    tenant_ids: Dict[int, List[str]] = {}
    for t, idxs in by_tenant.items():
        tenant_mat[t] = np.vstack(
            [embedder.train[records[i].train_index] for i in idxs]
        )
        tenant_ids[t] = [records[i].id for i in idxs]

    out: List[List[str]] = []
    for q in queries:
        t = q["tenant_id"]
        qv = embedder._vec(q["text"])
        sims = tenant_mat[t] @ qv
        top = np.argsort(-sims)[:k]
        out.append([tenant_ids[t][i] for i in top])
    return out
