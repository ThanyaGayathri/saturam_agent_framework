"""Embedding backend for the multi-hop spike.

Drives egpt's ``AzureOpenAIEmbedder`` (or the plain ``OpenAIEmbedder`` fallback)
behind a small ``TextEmbedder`` seam, with an on-disk cache keyed by
(model, text) so vector-baseline reruns and PPR entity embeddings are free and
deterministic after the first pass. Both sync and async variants (house rule).

The vector store the baseline uses is *not* hard-wired here: the harness only
ever sees ``embed(texts) -> np.ndarray`` and does exact cosine top-k in numpy, so
swapping in a real egpt ``VectorDb`` later is a call-site-free change.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np

from harness.llm import load_env


class TextEmbedder:
    """Abstract embedding seam: ``embed`` / ``aembed`` return an (n, d) matrix."""

    def embed(self, texts: Sequence[str]) -> np.ndarray:  # pragma: no cover - interface
        raise NotImplementedError

    async def aembed(self, texts: Sequence[str]) -> np.ndarray:  # pragma: no cover - interface
        raise NotImplementedError


class CachedEmbedder(TextEmbedder):
    """Wraps an egpt embedder with an on-disk per-text cache.

    Cache mode mirrors the LLM client (env ``HARNESS_EMBED_MODE``):
      - ``auto`` (default): use cache if present, else embed live and record.
      - ``replay``: cache only; raise on miss (offline / CI).
    """

    def __init__(
        self,
        egpt_embedder=None,
        cache_dir: Optional[Path] = None,
        mode: Optional[str] = None,
        model_tag: Optional[str] = None,
    ) -> None:
        self.mode = (mode or os.getenv("HARNESS_EMBED_MODE") or "auto").lower()
        self._embedder = egpt_embedder
        self.model_tag = model_tag or os.getenv("HARNESS_EMBED_TAG", "azure-embedding")
        if cache_dir is None:
            cache_dir = Path(__file__).resolve().parent.parent / "results" / "embed_cache"
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.calls = 0  # number of live embed calls (cost accounting)

    @property
    def embedder(self):
        if self._embedder is None:
            self._embedder = build_embedder()
        return self._embedder

    def _key(self, text: str) -> str:
        payload = json.dumps({"model": self.model_tag, "text": text}, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _load(self, text: str) -> Optional[np.ndarray]:
        path = self.cache_dir / f"{self._key(text)}.json"
        if not path.exists():
            return None
        return np.asarray(json.loads(path.read_text())["vector"], dtype=np.float64)

    def _store(self, text: str, vec: np.ndarray) -> None:
        path = self.cache_dir / f"{self._key(text)}.json"
        path.write_text(json.dumps({"vector": vec.tolist()}))

    def _cached_or_none(self, texts: Sequence[str]) -> Dict[str, Optional[np.ndarray]]:
        return {t: self._load(t) for t in dict.fromkeys(texts)}

    async def _embed_missing(self, missing: List[str]) -> Dict[str, np.ndarray]:
        """Batch-embed the uncached texts in one shot (batches of self.batch_size)."""
        if not missing:
            return {}
        if self.mode == "replay":
            raise RuntimeError("embed replay miss; run once with HARNESS_EMBED_MODE=auto to warm the cache.")
        result: Dict[str, np.ndarray] = {}
        bs = int(os.getenv("HARNESS_EMBED_BATCH", "256"))
        for i in range(0, len(missing), bs):
            chunk = missing[i : i + bs]
            self.calls += 1
            vecs, _ = await self.embedder.async_get_embeddings_batch_and_usage(chunk)
            for t, v in zip(chunk, vecs):
                arr = np.asarray(v, dtype=np.float64)
                self._store(t, arr)
                result[t] = arr
        return result

    def _assemble(self, texts: Sequence[str], have: Dict[str, np.ndarray]) -> np.ndarray:
        return _normalize(np.vstack([have[t] for t in texts]))

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        cached = self._cached_or_none(texts)
        missing = [t for t, v in cached.items() if v is None]
        fresh = asyncio.run(self._embed_missing(missing)) if missing else {}
        have = {t: (v if v is not None else fresh[t]) for t, v in cached.items()}
        return self._assemble(texts, have)

    async def aembed(self, texts: Sequence[str]) -> np.ndarray:
        cached = self._cached_or_none(texts)
        missing = [t for t, v in cached.items() if v is None]
        fresh = await self._embed_missing(missing) if missing else {}
        have = {t: (v if v is not None else fresh[t]) for t, v in cached.items()}
        return self._assemble(texts, have)


def build_embedder():
    """Build the egpt embedder from environment (Azure preferred, OpenAI fallback)."""
    load_env()
    if os.getenv("AZURE_EMBEDDER_OPENAI_API_KEY") and os.getenv("AZURE_EMBEDDER_OPENAI_ENDPOINT"):
        from egpt.knowledge.embedder.azure_openai import AzureOpenAIEmbedder

        return AzureOpenAIEmbedder(id=os.getenv("AZURE_EMBEDDER_DEPLOYMENT", "text-embedding-3-small"))
    if os.getenv("OPENAI_API_KEY"):
        from egpt.knowledge.embedder.openai import OpenAIEmbedder

        return OpenAIEmbedder(id=os.getenv("HARNESS_EMBED_MODEL", "text-embedding-3-small"))
    raise RuntimeError(
        "No embedding credentials found. Set AZURE_EMBEDDER_OPENAI_API_KEY / "
        "AZURE_EMBEDDER_OPENAI_ENDPOINT (or OPENAI_API_KEY) in benchmarks/memory_multihop/.env "
        "(see .env.example). Or run in replay mode (HARNESS_EMBED_MODE=replay) against a warm cache."
    )


def _normalize(mat: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return mat / norms
