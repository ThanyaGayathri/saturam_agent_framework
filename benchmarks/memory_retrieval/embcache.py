"""Disk-cached embeddings through the real egpt `Embedder` abstraction.

The dense retriever must embed each dataset's corpus once (fiqa is ~58k docs) and
reuse those vectors across every option run, so this caches the embedding matrix to
`.npz` keyed by (dataset, model). It calls egpt's `AzureOpenAIEmbedder` batch API --
we retrieve through the real seam, not a re-implementation -- and never touches core.

Default embedder (wired in `run.py::build_embedder`) is SM-R4's ratified choice
(`bge-base-en-v1.5` Mode A / `text-embedding-3-large`@1024 Mode B) -- swappable for any
other `Embedder` instance with no call-site change. Both sync and async embed paths are
provided.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
from typing import List, Optional

import numpy as np

from egpt.knowledge.embedder.base import Embedder

# Not a dev-machine scratchpad path -- must resolve on any host that clones this repo.
DEFAULT_CACHE_DIR = os.environ.get(
    "MEMBENCH_EMB_CACHE",
    os.path.join(tempfile.gettempdir(), "membench_cache", "emb_cache"),
)


def _normalize(mat: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (mat / norms).astype(np.float32)


class EmbeddingCache:
    """Caches unit-normalized embedding matrices per (namespace, model)."""

    def __init__(
        self, embedder: Embedder, model_id: str, cache_dir: str = DEFAULT_CACHE_DIR
    ):
        self.embedder = embedder
        self.model_id = model_id
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)

    def _key(self, namespace: str, texts: List[str]) -> str:
        h = hashlib.sha1()
        h.update(self.model_id.encode())
        h.update(namespace.encode())
        h.update(str(len(texts)).encode())
        # Hash a sample of the content so a changed corpus invalidates the cache.
        for t in texts[:64]:
            h.update(t[:256].encode("utf-8", "ignore"))
        return h.hexdigest()[:16]

    def _path(self, namespace: str, texts: List[str]) -> str:
        safe = namespace.replace("/", "_").replace("\\", "_")
        return os.path.join(self.cache_dir, f"{safe}_{self._key(namespace, texts)}.npz")

    def embed(self, namespace: str, texts: List[str]) -> np.ndarray:
        """Sync: return an (n, d) unit-normalized matrix, cached on disk."""
        path = self._path(namespace, texts)
        if os.path.exists(path):
            return np.load(path)["mat"]
        mat = _normalize(np.asarray(self._embed_raw(texts), dtype=np.float32))
        np.savez_compressed(path, mat=mat)
        return mat

    async def aembed(self, namespace: str, texts: List[str]) -> np.ndarray:
        """Async variant of `embed` (runs the reliable sync path in a thread)."""
        return await asyncio.to_thread(self.embed, namespace, texts)

    def _embed_raw(self, texts: List[str]) -> List[List[float]]:
        # Real corpora contain empty documents (e.g. a LongMemEval session that renders
        # empty); the embeddings API rejects empty input, so substitute a placeholder.
        texts = [t if (t and t.strip()) else "[empty]" for t in texts]
        # Local sentence-transformers embedders accept a list in get_embedding (native,
        # fast, offline, no rate limits) -> preferred for the air-gapped Mode-A path.
        # OpenAI/Azure embedders expose a `.client`; we batch through it with retry
        # (egpt's async batch helper hangs against this Azure endpoint, and the endpoint
        # rate-limits long-doc corpora -- see findings/embedding-async-batch-hang.md).
        if getattr(self.embedder, "sentence_transformer_client", None) is not None:
            return self._embed_native(texts)
        return self._embed_openai(texts)

    def _embed_native(self, texts: List[str]) -> List[List[float]]:
        batch = max(1, getattr(self.embedder, "batch_size", 100) or 100)
        out: List[List[float]] = []
        for i in range(0, len(texts), batch):
            embs = self.embedder.get_embedding(
                texts[i : i + batch]
            )  # list[str] -> list[vec]
            out.extend(embs)
        return out

    def _embed_openai(self, texts: List[str]) -> List[List[float]]:
        import time as _time

        client = self.embedder.client
        model = getattr(self.embedder, "id", None)
        dim = self.embedder.dimensions
        batch = max(1, getattr(self.embedder, "batch_size", 100))
        out: List[List[float]] = []
        for i in range(0, len(texts), batch):
            chunk = texts[i : i + batch]
            last_err: Optional[Exception] = None
            for attempt in range(6):
                try:
                    resp = client.embeddings.create(
                        model=model, input=chunk, encoding_format="float"
                    )
                    rows = [d.embedding for d in resp.data]
                    if self._valid(rows, dim, len(chunk)):
                        out.extend(rows)
                        break
                    last_err = RuntimeError("incomplete/empty embeddings")
                except Exception as e:  # 429 / connection reset / timeout
                    last_err = e
                _time.sleep(min(2**attempt, 20))
            else:
                raise RuntimeError(f"embedding failed after retries: {last_err}")
        return out

    @staticmethod
    def _valid(rows: List[List[float]], dim: Optional[int], expected_n: int) -> bool:
        if len(rows) != expected_n:
            return False
        lengths = {len(r) for r in rows}
        if len(lengths) != 1 or next(iter(lengths)) == 0:
            return False
        return dim is None or next(iter(lengths)) == dim
