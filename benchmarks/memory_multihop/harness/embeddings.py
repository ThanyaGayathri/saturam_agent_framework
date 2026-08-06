"""Embedding backend for the multi-hop spike.

Drives an egpt ``Embedder`` behind a small ``TextEmbedder`` seam, with an on-disk
cache keyed by (model, text) so vector-baseline reruns and PPR entity embeddings
are free and deterministic after the first pass. Both sync and async variants
(house rule).

**Model choice is SM-R4's ratified decision** (`benchmarks/memory_embedding/
DECISION.md` §1), not a placeholder:

  - **Mode A** (self-hostable / air-gapped): ``BAAI/bge-base-en-v1.5`` @ 768 via
    ``SentenceTransformerEmbedder``. Runs offline, no credentials.
  - **Mode B** (provider allowed): Azure ``text-embedding-3-large`` truncated to
    **1024** via ``AzureOpenAIEmbedder``.

This matters for SM-R9 specifically: the dense leg here is the *baseline PPR is
measured against*, so if it runs on an embedder we will not deploy, the reported
PPR gain is a margin over a strawman (PR #15 review). The same embedder also
feeds PPR's own entity vectors (synonymy edges + query→node linking), so both
arms move together when this changes.

Override with ``HARNESS_EMBED_BACKEND=local|azure|openai``; default is Mode A
(local) unless Azure credentials are present, so the harness stays reproducible
on a host with no keys.

The vector store the baseline uses is *not* hard-wired here: the harness only
ever sees ``embed(texts) -> np.ndarray`` and does exact cosine top-k in numpy, so
swapping in a real egpt ``VectorDb`` later is a call-site-free change.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np

from harness.llm import load_env


# --------------------------------------------------------------------------- #
# SM-R4 ratified embedder spec (benchmarks/memory_embedding/DECISION.md §1)
# --------------------------------------------------------------------------- #
#: Mode A — self-hostable, offline, MIT. SM-R4's default.
SM_R4_MODE_A_MODEL = "BAAI/bge-base-en-v1.5"
SM_R4_MODE_A_DIM = 768
#: Mode B — provider allowed. 1024 (not 3072) to fit the pgvector `vector` cap;
#: SM-R4 §1 measured 1024 vs 3072 as a statistical tie (p=0.82).
SM_R4_MODE_B_MODEL = "text-embedding-3-large"
SM_R4_MODE_B_DIM = 1024


@dataclass(frozen=True)
class EmbedderSpec:
    """Which embedder the harness will use — resolved from env, no client built.

    Kept side-effect free so the cache tag can be derived without constructing a
    model or requiring credentials.
    """

    backend: str  # local | azure | openai
    model: str
    dimensions: int

    @property
    def tag(self) -> str:
        """Cache key component. **Must** encode the model+dim.

        The previous default was the constant ``"azure-embedding"``, which meant
        swapping the embedder silently reused the *old* model's cached vectors —
        a re-run would have looked like a no-op. Deriving the tag from the spec
        makes a model change invalidate the cache, as it must.
        """
        return f"{self.backend}:{self.model}@{self.dimensions}"


def resolve_embedder_spec() -> EmbedderSpec:
    """Pick the embedder from env, defaulting to SM-R4's ratified choice."""
    load_env()
    backend = (os.getenv("HARNESS_EMBED_BACKEND") or "").strip().lower()
    if not backend:
        has_azure = bool(os.getenv("AZURE_EMBEDDER_OPENAI_API_KEY") and os.getenv("AZURE_EMBEDDER_OPENAI_ENDPOINT"))
        # Mode A is the default: it needs no credentials, so the harness stays
        # reproducible on any host. Mode B only when Azure is actually configured.
        backend = "azure" if has_azure else "local"
    if backend == "local":
        return EmbedderSpec(
            backend="local",
            model=os.getenv("HARNESS_EMBED_MODEL", SM_R4_MODE_A_MODEL),
            dimensions=int(os.getenv("HARNESS_EMBED_DIM", SM_R4_MODE_A_DIM)),
        )
    if backend in ("azure", "openai"):
        return EmbedderSpec(
            backend=backend,
            model=os.getenv(
                "AZURE_EMBEDDER_DEPLOYMENT" if backend == "azure" else "HARNESS_EMBED_MODEL",
                SM_R4_MODE_B_MODEL,
            ),
            dimensions=int(os.getenv("HARNESS_EMBED_DIM", SM_R4_MODE_B_DIM)),
        )
    raise ValueError(f"unknown HARNESS_EMBED_BACKEND {backend!r} (expected local|azure|openai)")


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
        self.spec = resolve_embedder_spec()
        # Tag defaults to the *resolved model*, never a provider-generic label, so
        # switching embedders invalidates the cache instead of silently replaying
        # the previous model's vectors (see EmbedderSpec.tag).
        self.model_tag = model_tag or os.getenv("HARNESS_EMBED_TAG") or self.spec.tag
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
            vecs = await _embed_batch(self.embedder, chunk)
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


async def _embed_batch(embedder, chunk: List[str]) -> List[Sequence[float]]:
    """Embed a batch, tolerating embedders without the async batch+usage API.

    The OpenAI/Azure embedders implement ``async_get_embeddings_batch_and_usage``;
    ``SentenceTransformerEmbedder`` (SM-R4 Mode A) does not, but its
    ``get_embedding`` accepts a list and encodes it as one batch.
    """
    batch_api = getattr(embedder, "async_get_embeddings_batch_and_usage", None)
    if batch_api is not None:
        vecs, _ = await batch_api(chunk)
        return vecs
    return await embedder.async_get_embedding(list(chunk))


def build_embedder(spec: Optional[EmbedderSpec] = None):
    """Build the egpt embedder for SM-R4's ratified choice (`resolve_embedder_spec`).

    Mode A is credential-free, so it is the default on a host with no Azure keys —
    which is what makes the dense baseline reproducible offline.
    """
    load_env()
    spec = spec or resolve_embedder_spec()
    if spec.backend == "local":
        from egpt.knowledge.embedder.sentence_transformer import (
            SentenceTransformerEmbedder,
        )

        return SentenceTransformerEmbedder(id=spec.model, dimensions=spec.dimensions)
    if spec.backend == "azure":
        if not (os.getenv("AZURE_EMBEDDER_OPENAI_API_KEY") and os.getenv("AZURE_EMBEDDER_OPENAI_ENDPOINT")):
            raise RuntimeError(
                "HARNESS_EMBED_BACKEND=azure but no Azure embedding credentials. Set "
                "AZURE_EMBEDDER_OPENAI_API_KEY / AZURE_EMBEDDER_OPENAI_ENDPOINT in "
                "benchmarks/memory_multihop/.env (see .env.example), or use the "
                "credential-free Mode A default (HARNESS_EMBED_BACKEND=local)."
            )
        from egpt.knowledge.embedder.azure_openai import AzureOpenAIEmbedder

        return AzureOpenAIEmbedder(id=spec.model, dimensions=spec.dimensions)
    if spec.backend == "openai":
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError(
                "HARNESS_EMBED_BACKEND=openai but OPENAI_API_KEY is unset. Use the "
                "credential-free Mode A default (HARNESS_EMBED_BACKEND=local) instead."
            )
        from egpt.knowledge.embedder.openai import OpenAIEmbedder

        return OpenAIEmbedder(id=spec.model, dimensions=spec.dimensions)
    raise ValueError(f"unknown backend {spec.backend!r}")


def _normalize(mat: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return mat / norms
