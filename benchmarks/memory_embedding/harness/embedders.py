"""Embedder registry for the SM-R4 memory-embedding benchmark.

Every candidate is exercised through an *existing egpt embedder abstraction*
(no core code is modified). We deliberately go through the framework classes so
the measured numbers reflect what the memory subsystem would actually get:

  - Mode A (self-hostable / air-gapped): SentenceTransformerEmbedder, FastEmbedEmbedder
  - Mode B (provider):                   OpenAIEmbedder

Both the sync (`get_embedding`) and async (`async_get_embedding`) code paths of
each adapter are wrapped here so the harness can validate both variants.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

# Pin embedding to CPU. The target deployment (Mode A) is CPU / air-gapped, so CPU
# is the representative device for both quality and latency. On a Mac dev box
# SentenceTransformer would otherwise auto-select the Metal (MPS) GPU, which (a)
# gives non-representative latency and (b) OOMs under memory pressure from other
# processes. FastEmbed (ONNX) already runs on CPU; Azure/OpenAI are remote.
try:  # pragma: no cover - environment guard
    import torch

    torch.backends.mps.is_available = lambda: False  # type: ignore[assignment]
except Exception:  # noqa: BLE001
    pass


@dataclass
class Candidate:
    """A single model under test, plus its static/spec attributes."""

    key: str  # short benchmark key, e.g. "bge-base"
    mode: str  # "A" (self-hostable) or "B" (provider)
    egpt_class: str  # which egpt embedder abstraction is used
    model_id: str  # the underlying model id
    dim: int  # embedding dimension actually produced
    license: str  # model / API license
    offline: bool  # can it run fully inside an air-gapped VPC?
    params_m: Optional[int]  # approx parameter count (millions), None for API
    disk_mb: Optional[int]  # approx on-disk footprint (MB), None for API
    normalize: bool = True  # cosine setups want normalized vectors
    trust_remote_code: bool = False  # model ships custom modeling code (e.g. nomic)
    _factory: Optional[Callable] = None  # builds the egpt embedder lazily

    def build(self):
        if self._factory is None:
            raise RuntimeError(f"candidate {self.key} has no factory")
        return self._factory(self)


# --------------------------------------------------------------------------- #
# Factories. Each returns a live egpt embedder instance.
# --------------------------------------------------------------------------- #
def _sentence_transformer(c: "Candidate"):
    import dataclasses

    from egpt.knowledge.embedder.sentence_transformer import SentenceTransformerEmbedder

    # E5/GTE/nomic families expect an instruction prefix on the *query* side; we
    # keep encoding symmetric here (prefix handled at call sites when needed) and
    # simply normalize for cosine similarity, which is what pgvector will use.
    kwargs: Dict[str, Any] = dict(
        id=c.model_id, dimensions=c.dim, normalize_embeddings=c.normalize
    )
    if c.trust_remote_code:
        # Requires the SM-R4 `st_trust_remote_code.patch` on the core adapter
        # (see findings/). Guarded so the harness stays importable on stock core.
        supported = {f.name for f in dataclasses.fields(SentenceTransformerEmbedder)}
        if "trust_remote_code" in supported:
            kwargs["trust_remote_code"] = True
        else:
            raise RuntimeError(
                "nomic requires trust_remote_code, which the stock "
                "SentenceTransformerEmbedder does not expose. Apply "
                "findings/st_trust_remote_code.patch to core to benchmark it."
            )
    return SentenceTransformerEmbedder(**kwargs)


def _fastembed(c: "Candidate"):
    from egpt.knowledge.embedder.fastembed import FastEmbedEmbedder

    return FastEmbedEmbedder(id=c.model_id, dimensions=c.dim)


def _openai(c: "Candidate"):
    from egpt.knowledge.embedder.openai import OpenAIEmbedder

    # text-embedding-3-* support Matryoshka truncation via `dimensions`.
    return OpenAIEmbedder(id=c.model_id, dimensions=c.dim)


def _azure_openai(c: "Candidate"):
    import os

    from egpt.knowledge.embedder.azure_openai import AzureOpenAIEmbedder

    # Endpoint / key / api-version come from the adapter's standard env vars
    # (AZURE_EMBEDDER_OPENAI_ENDPOINT / _API_KEY / _API_VERSION). The Azure
    # *deployment name* is resource-specific and differs per model, so it is
    # supplied per-model via env, falling back to the adapter's default var.
    dep_env = {
        "text-embedding-3-small": "AZURE_DEPLOYMENT_SMALL",
        "text-embedding-3-large": "AZURE_DEPLOYMENT_LARGE",
    }.get(c.model_id)
    deployment = (os.getenv(dep_env) if dep_env else None) or os.getenv(
        "AZURE_EMBEDDER_DEPLOYMENT"
    )
    return AzureOpenAIEmbedder(
        id=c.model_id, dimensions=c.dim, azure_deployment=deployment
    )


# --------------------------------------------------------------------------- #
# The shortlist (English-only scope, 10M+ scale => dimension cost matters).
# disk_mb / params_m are approximate published footprints, used only for the
# footprint column; quality/latency are always measured, never cited.
# --------------------------------------------------------------------------- #
_REGISTRY: Dict[str, Candidate] = {}


def _reg(c: Candidate) -> None:
    _REGISTRY[c.key] = c


_reg(
    Candidate(
        "minilm-l6",
        "A",
        "SentenceTransformerEmbedder",
        "sentence-transformers/all-MiniLM-L6-v2",
        384,
        "Apache-2.0",
        True,
        23,
        90,
        _factory=_sentence_transformer,
    )
)
_reg(
    Candidate(
        "bge-small",
        "A",
        "SentenceTransformerEmbedder",
        "BAAI/bge-small-en-v1.5",
        384,
        "MIT",
        True,
        33,
        130,
        _factory=_sentence_transformer,
    )
)
_reg(
    Candidate(
        "bge-base",
        "A",
        "SentenceTransformerEmbedder",
        "BAAI/bge-base-en-v1.5",
        768,
        "MIT",
        True,
        109,
        420,
        _factory=_sentence_transformer,
    )
)
_reg(
    Candidate(
        "bge-large",
        "A",
        "SentenceTransformerEmbedder",
        "BAAI/bge-large-en-v1.5",
        1024,
        "MIT",
        True,
        335,
        1280,
        _factory=_sentence_transformer,
    )
)
_reg(
    Candidate(
        "e5-base",
        "A",
        "SentenceTransformerEmbedder",
        "intfloat/e5-base-v2",
        768,
        "MIT",
        True,
        109,
        420,
        _factory=_sentence_transformer,
    )
)
_reg(
    Candidate(
        "gte-base",
        "A",
        "SentenceTransformerEmbedder",
        "thenlper/gte-base",
        768,
        "MIT",
        True,
        109,
        420,
        _factory=_sentence_transformer,
    )
)
_reg(
    Candidate(
        "nomic-1.5",
        "A",
        "SentenceTransformerEmbedder",
        "nomic-ai/nomic-embed-text-v1.5",
        768,
        "Apache-2.0",
        True,
        137,
        550,
        trust_remote_code=True,
        _factory=_sentence_transformer,
    )
)
# fastembed / ONNX route to the same BGE weights (CPU-optimised runtime)
_reg(
    Candidate(
        "bge-small-onnx",
        "A",
        "FastEmbedEmbedder",
        "BAAI/bge-small-en-v1.5",
        384,
        "MIT",
        True,
        33,
        130,
        normalize=False,
        _factory=_fastembed,
    )
)
# Provider baseline (Mode B), Azure AI Foundry / Azure OpenAI. This is the path
# actually exercised for this deployment. dims include full + Matryoshka-truncated
# sizes so we can measure the quality/storage trade-off at 10M scale.
_reg(
    Candidate(
        "azure-3-small",
        "B",
        "AzureOpenAIEmbedder",
        "text-embedding-3-small",
        1536,
        "proprietary (Azure)",
        False,
        None,
        None,
        _factory=_azure_openai,
    )
)
_reg(
    Candidate(
        "azure-3-small-512",
        "B",
        "AzureOpenAIEmbedder",
        "text-embedding-3-small",
        512,
        "proprietary (Azure)",
        False,
        None,
        None,
        _factory=_azure_openai,
    )
)
_reg(
    Candidate(
        "azure-3-large",
        "B",
        "AzureOpenAIEmbedder",
        "text-embedding-3-large",
        3072,
        "proprietary (Azure)",
        False,
        None,
        None,
        _factory=_azure_openai,
    )
)
_reg(
    Candidate(
        "azure-3-large-1024",
        "B",
        "AzureOpenAIEmbedder",
        "text-embedding-3-large",
        1024,
        "proprietary (Azure)",
        False,
        None,
        None,
        _factory=_azure_openai,
    )
)

# Non-Azure equivalent (raw OpenAI API). Registered for reproducibility on hosts
# that have a plain OPENAI_API_KEY; not run for this Foundry-based deployment.
_reg(
    Candidate(
        "openai-3-small",
        "B-openai",
        "OpenAIEmbedder",
        "text-embedding-3-small",
        1536,
        "proprietary (API)",
        False,
        None,
        None,
        _factory=_openai,
    )
)
_reg(
    Candidate(
        "openai-3-large-1024",
        "B-openai",
        "OpenAIEmbedder",
        "text-embedding-3-large",
        1024,
        "proprietary (API)",
        False,
        None,
        None,
        _factory=_openai,
    )
)

# Models whose family benefits from an asymmetric query prefix (E5/GTE/nomic).
# BGE also recommends a query instruction for retrieval.
_QUERY_PREFIX: Dict[str, str] = {
    "e5-base": "query: ",
    "nomic-1.5": "search_query: ",
    "bge-small": "Represent this sentence for searching relevant passages: ",
    "bge-base": "Represent this sentence for searching relevant passages: ",
    "bge-large": "Represent this sentence for searching relevant passages: ",
    "bge-small-onnx": "Represent this sentence for searching relevant passages: ",
}
_DOC_PREFIX: Dict[str, str] = {
    "e5-base": "passage: ",
    "nomic-1.5": "search_document: ",
}


def list_candidates(mode: Optional[str] = None) -> List[Candidate]:
    return [c for c in _REGISTRY.values() if mode is None or c.mode == mode]


def get_candidate(key: str) -> Candidate:
    return _REGISTRY[key]


def query_prefix(key: str) -> str:
    return _QUERY_PREFIX.get(key, "")


def doc_prefix(key: str) -> str:
    return _DOC_PREFIX.get(key, "")


# --------------------------------------------------------------------------- #
# Uniform embedding wrappers over the egpt adapters. Both variants exposed.
# --------------------------------------------------------------------------- #
def embed_texts_sync(embedder, texts: List[str]) -> List[List[float]]:
    """Sync path: exercises `Embedder.get_embedding` one item at a time.

    This mirrors the memory subsystem's per-item write path and is what the
    single-query latency measurement uses. For embedding a large *corpus* during
    a benchmark, prefer `embed_texts_batched` — same vectors, far faster.
    """
    return [embedder.get_embedding(t) for t in texts]


def embed_texts_batched(
    embedder, texts: List[str], batch_size: int = 256
) -> List[List[float]]:
    """Batched corpus embedding for the self-hostable (Mode A) models.

    Only the corpus-indexing path is batched; single-query latency is still
    measured per-item elsewhere. Uses each adapter's native batch capability:

      - SentenceTransformerEmbedder: `get_embedding` already accepts a list and
        returns a list of vectors (SentenceTransformer batches internally).
      - FastEmbedEmbedder: its `get_embedding` returns only the first vector for a
        list, so we call the underlying ONNX client's `embed` directly.
      - Anything else (e.g. provider APIs): fall back to per-item.
    """
    cls = type(embedder).__name__

    if cls == "FastEmbedEmbedder":
        out: List[List[float]] = []
        for e in embedder.client.embed(texts, batch_size=batch_size):
            out.append(e.tolist() if hasattr(e, "tolist") else list(e))
        return out

    if cls == "SentenceTransformerEmbedder":
        out = []
        for i in range(0, len(texts), batch_size):
            vecs = embedder.get_embedding(texts[i : i + batch_size])
            # guard: if the adapter returned a single vector, batching isn't
            # supported here — fall back to per-item for this chunk.
            if vecs and isinstance(vecs[0], (int, float)):
                out.extend(embedder.get_embedding(t) for t in texts[i : i + batch_size])
            else:
                out.extend(vecs)
        return out

    return [embedder.get_embedding(t) for t in texts]


async def embed_texts_async(
    embedder, texts: List[str], concurrency: int = 8
) -> List[List[float]]:
    """Async path: exercises `Embedder.async_get_embedding` with bounded concurrency."""
    sem = asyncio.Semaphore(concurrency)

    async def one(t: str) -> List[float]:
        async with sem:
            return await embedder.async_get_embedding(t)

    return await asyncio.gather(*(one(t) for t in texts))
