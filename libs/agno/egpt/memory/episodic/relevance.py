"""Episodic relevance: query-vs-episode embedding similarity as its own signal (SM-E6 #113).

Generative Agents' composite score is recency + importance + **relevance(cosine)**, equal-weighted
(``docs/MEMORY-RESEARCH-episodic-and-neurosymbolic.md`` section 1). ``VectorSubstrate.search``
(SM-V1 #95) already ranks by a normalized cosine score, but only for episodes that came back from
*that* ANN call -- a candidate reaching composite scoring by another path (graph traversal, a scan,
a page already fused from a prior read) has no score attached. This module computes the same signal
directly, so SM-E7 (#114) can score any candidate against the query regardless of how it arrived.

**Not here:** BM25 blending (SM-V3 #97, unmerged -- no real hybrid backend to validate against
yet); recency and importance (SM-E4 #111, SM-E5 #112 -- separate signals, not this one); the
composite weighting itself (SM-E7 #114, the only intended caller).

Nothing here reads or writes a store. :func:`cosine_relevance` is pure vector math and has no async
twin, the same exemption ``memory/schemas/episodic.py`` documents. :func:`episode_relevance` calls
an :class:`Embedder`, which is I/O, so it gets the seam's ``a``-prefixed twin.
"""

import math
from typing import Sequence

from egpt.knowledge.embedder import Embedder
from egpt.memory.errors import MemoryOperationError
from egpt.vectordb.distance import Distance
from egpt.vectordb.score import normalize_score

__all__ = ["aepisode_relevance", "cosine_relevance", "episode_relevance"]


def _cosine_distance(query_embedding: Sequence[float], episode_embedding: Sequence[float]) -> float:
    """Cosine distance in ``[0, 2]`` (0=identical, 1=orthogonal, 2=opposite).

    Matches ``normalize_score``'s contract for ``Distance.cosine`` exactly, so relevance lands on
    the same ``[0.0, 1.0]`` similarity scale ``VectorSubstrate.search`` promises its callers --
    SM-E7 blends this signal with recency/importance and an inconsistent scale would let whichever
    signal happens to be larger decide the ranking.
    """
    if not query_embedding or not episode_embedding:
        raise MemoryOperationError(
            f"Relevance needs a non-empty embedding on both sides; got {len(query_embedding)} "
            f"query dimensions and {len(episode_embedding)} episode dimensions.",
            field_name="episode_embedding",
        )
    if len(query_embedding) != len(episode_embedding):
        raise MemoryOperationError(
            f"query embedding has {len(query_embedding)} dimensions but the episode embedding has "
            f"{len(episode_embedding)}; cosine similarity is undefined between vectors of "
            "different width.",
            field_name="episode_embedding",
        )
    dot = sum(q * e for q, e in zip(query_embedding, episode_embedding))
    query_norm = math.sqrt(sum(q * q for q in query_embedding))
    episode_norm = math.sqrt(sum(e * e for e in episode_embedding))
    if query_norm == 0.0 or episode_norm == 0.0:
        # A zero vector has no direction, so "how similar" has no answer -- not identical (0) and
        # not orthogonal (1). Reported as maximally distant: an embedder returning an all-zero
        # vector is reporting "nothing extracted", and scoring that as the closest match would
        # rank an empty episode ahead of ones that actually matched the query.
        return 2.0
    return 1.0 - dot / (query_norm * episode_norm)


def cosine_relevance(query_embedding: Sequence[float], episode_embedding: Sequence[float]) -> float:
    """The relevance signal, given two embeddings: a similarity in ``[0.0, 1.0]``, higher is better.

    Reuses ``vectordb.score.normalize_score`` rather than re-deriving the clamp -- the pgvector
    substrate's own cosine normalization lives there, and a second copy here is exactly the kind of
    invariant that drifts when only one of the two is fixed later.
    """
    return normalize_score(_cosine_distance(query_embedding, episode_embedding), Distance.cosine)


def episode_relevance(embedder: Embedder, query_text: str, episode_content: str) -> float:
    """Relevance of one episode's content to a query, embedding both with ``embedder``.

    The embedder is the caller's -- typically whatever ``MemoryProviderConfig`` wired for the vector
    substrate (SM-V2 #96 owns *which* model that is) -- so a candidate scored here lands on the same
    embedding space as one that arrived with a score already attached from ``VectorSubstrate.search``.
    """
    query_embedding = embedder.get_embedding(query_text)
    episode_embedding = embedder.get_embedding(episode_content)
    return cosine_relevance(query_embedding, episode_embedding)


async def aepisode_relevance(embedder: Embedder, query_text: str, episode_content: str) -> float:
    """Async variant of :func:`episode_relevance`."""
    query_embedding = await embedder.async_get_embedding(query_text)
    episode_embedding = await embedder.async_get_embedding(episode_content)
    return cosine_relevance(query_embedding, episode_embedding)
