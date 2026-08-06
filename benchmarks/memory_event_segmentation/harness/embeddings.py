"""Real text embeddings for the confirmation run.

The decision is made on the deterministic synthetic vectors (which isolate the
topic signal). This module re-encodes each event's natural-language ``text`` with
a local ``sentence-transformers`` model so we can confirm the ranking survives
real semantic-embedding noise — same pattern as the SM-R5 harness. No network or
API keys are needed after the first model download.
"""

from __future__ import annotations

from functools import lru_cache
from typing import List

import numpy as np

from harness.stream import Stream


def embeddings_available() -> bool:
    try:
        import sentence_transformers  # noqa: F401

        return True
    except Exception:
        return False


@lru_cache(maxsize=2)
def _load_model(model_name: str):
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(model_name)


def embed_stream(stream: Stream, model_name: str = "sentence-transformers/all-MiniLM-L6-v2") -> None:
    """Overwrite every event's ``vector`` with a real (normalised) text embedding."""
    model = _load_model(model_name)
    texts: List[str] = [e.text for e in stream.events]
    vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    arr = np.asarray(vecs, dtype=np.float64)
    for e, v in zip(stream.events, arr):
        e.vector = v


async def aembed_stream(stream: Stream, model_name: str = "sentence-transformers/all-MiniLM-L6-v2") -> None:
    """Async mirror of :func:`embed_stream`."""
    embed_stream(stream, model_name=model_name)
