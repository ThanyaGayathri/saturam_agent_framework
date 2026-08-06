"""Optional embedding-backed relevance (confirmation mode).

The default harness uses deterministic synthetic vectors so the decay signal is
isolated (see stream.py). This module offers the *other* relevance option: encode
each episode/query's natural-language ``text`` with a real local sentence-embedding
model and use cosine over those embeddings instead.

It is intentionally lazy: ``sentence-transformers`` is imported only when embedding
is requested, so the default offline path never needs it. Because the produced
vectors are unit-normalised and written back onto ``episode.vector`` /
``query.vector``, the rest of the pipeline (ranking, metrics, decay) is unchanged
— only the source of the relevance signal differs.
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np

from harness.episode import Stream

DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

_MODEL_CACHE: Dict[str, Any] = {}


def embeddings_available() -> bool:
    """True if the optional embedding backend can be imported."""
    try:
        import sentence_transformers  # noqa: F401

        return True
    except Exception:
        return False


def _load_model(name: str) -> Any:
    if name not in _MODEL_CACHE:
        from sentence_transformers import SentenceTransformer

        _MODEL_CACHE[name] = SentenceTransformer(name)
    return _MODEL_CACHE[name]


def _normalize_rows(mat: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return mat / norms


def embed_stream(
    stream: Stream, model_name: str = DEFAULT_MODEL, batch_size: int = 64
) -> Stream:
    """Replace every episode/query ``vector`` with a real unit text embedding.

    Mutates and returns the same :class:`Stream`. Deterministic for a fixed model.
    """
    model = _load_model(model_name)
    ep_texts = [e.text for e in stream.episodes]
    q_texts = [q.text for q in stream.queries]

    ep_vecs = _normalize_rows(
        np.asarray(
            model.encode(ep_texts, batch_size=batch_size, show_progress_bar=False),
            dtype=float,
        )
    )
    q_vecs = _normalize_rows(
        np.asarray(
            model.encode(q_texts, batch_size=batch_size, show_progress_bar=False),
            dtype=float,
        )
    )

    for episode, vec in zip(stream.episodes, ep_vecs):
        episode.vector = vec
    for query, vec in zip(stream.queries, q_vecs):
        query.vector = vec
    if ep_vecs.size:
        stream.dim = int(ep_vecs.shape[1])
    return stream


async def aembed_stream(
    stream: Stream, model_name: str = DEFAULT_MODEL, batch_size: int = 64
) -> Stream:
    """Async variant of :func:`embed_stream` (runs inline)."""
    return embed_stream(stream, model_name=model_name, batch_size=batch_size)
