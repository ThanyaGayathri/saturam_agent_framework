"""Episodic relevance: query-vs-episode embedding similarity as its own signal (SM-E6 #113).

Generative Agents' composite score is recency + importance + **relevance(cosine)**, equal-weighted
(``docs/MEMORY-RESEARCH-episodic-and-neurosymbolic.md`` section 1). ``VectorSubstrate.search``
(SM-V1 #95) already ranks by a normalized cosine score, but only for episodes that came back from
*that* ANN call -- a candidate reaching composite scoring by another path (graph traversal, a scan,
a page already fused from a prior read) has no score attached. This module computes the same signal
directly, so SM-E7 (#114) can score any candidate against the query regardless of how it arrived.

**Not here:** BM25 blending (SM-V3 #97 ships a DB-level ``HybridSearchSubstrate`` port for the
SM-H1 #131 orchestrator, a different concern from this standalone per-episode signal); recency
and importance (SM-E4 #111, SM-E5 #112 -- separate signals, not this one); the composite
weighting itself (SM-E7 #114, the only intended caller).

Nothing here reads or writes a store. :func:`cosine_relevance` is pure vector math and has no async
twin, the same exemption ``memory/schemas/episodic.py`` documents. :func:`episode_relevance` calls
an :class:`EmbedderLike`, which is I/O, so it gets the seam's ``a``-prefixed twin.
"""

import asyncio
import math
from typing import List, Optional, Protocol, Sequence, runtime_checkable

from egpt.memory.errors import MemoryOperationError
from egpt.vectordb.distance import Distance
from egpt.vectordb.score import normalize_score

__all__ = ["EmbedderLike", "aepisode_relevance", "cosine_relevance", "episode_relevance"]


@runtime_checkable
class EmbedderLike(Protocol):
    """Structural requirement, not ``egpt.knowledge.embedder.Embedder`` by name.

    ``egpt.memory`` is a services-layer package with no existing dependency on
    ``egpt.knowledge`` (a sibling services package); importing ``Embedder`` here just to spell a
    parameter type would add that edge for a type annotation alone. A Protocol is the same choice
    ``VectorSubstrate``/``GraphSubstrate`` (``egpt/memory/substrates.py``) already made for the
    same reason -- any embedder, in-tree or not, satisfies this structurally without inheriting
    from anything.
    """

    def get_embedding(self, text: str) -> List[float]: ...

    async def async_get_embedding(self, text: str) -> List[float]: ...


def _cosine_distance(query_embedding: Optional[Sequence[float]], episode_embedding: Optional[Sequence[float]]) -> float:
    """Cosine distance in ``[0, 2]`` (0=identical, 1=orthogonal, 2=opposite).

    Matches ``normalize_score``'s contract for ``Distance.cosine`` exactly, so relevance lands on
    the same ``[0.0, 1.0]`` similarity scale ``VectorSubstrate.search`` promises its callers --
    SM-E7 blends this signal with recency/importance and an inconsistent scale would let whichever
    signal happens to be larger decide the ranking.

    ``None`` is accepted, not just an empty sequence, as a defensive guard rather than an observed
    case: nothing in this tree's ``Embedder`` implementations returns ``None`` today, but nothing
    in :class:`EmbedderLike` forbids a future or external one from doing so on failure, and a
    caller forwarding that result here should get the named refusal below, not a ``TypeError``
    from whatever this function happened to do with an unchecked ``None``.
    """
    # Checked and raised separately per side, not as one combined condition: a caller routes on
    # `field_name`, and a combined check named `episode_embedding` even when only the *query*
    # side was empty would blame the side that was actually fine. `is None` first because a `None`
    # embedding (an embedder's own failure mode) is not just falsy -- `len(None)` inside an f-string
    # would raise a bare TypeError before this error is ever constructed.
    if query_embedding is None or not query_embedding:
        raise MemoryOperationError(
            "Relevance needs a non-empty query embedding; got "
            f"{'None' if query_embedding is None else len(query_embedding)} dimensions.",
            field_name="query_embedding",
        )
    if episode_embedding is None or not episode_embedding:
        raise MemoryOperationError(
            "Relevance needs a non-empty episode embedding; got "
            f"{'None' if episode_embedding is None else len(episode_embedding)} dimensions.",
            field_name="episode_embedding",
        )
    if len(query_embedding) != len(episode_embedding):
        # No side is objectively "wrong" in a width mismatch -- both are individually well-formed,
        # just incomparable. `episode_embedding` is named by convention: the query is the caller's
        # held-fixed reference (it is what every candidate in a scoring pass is compared against),
        # so a candidate that does not match it is the one reported.
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


def cosine_relevance(query_embedding: Optional[Sequence[float]], episode_embedding: Optional[Sequence[float]]) -> float:
    """The relevance signal, given two embeddings: a similarity in ``[0.0, 1.0]``, higher is better.

    Reuses ``vectordb.score.normalize_score`` rather than re-deriving the clamp -- the pgvector
    substrate's own cosine normalization lives there, and a second copy here is exactly the kind of
    invariant that drifts when only one of the two is fixed later.
    """
    return normalize_score(_cosine_distance(query_embedding, episode_embedding), Distance.cosine)


def episode_relevance(embedder: EmbedderLike, query_text: str, episode_content: str) -> float:
    """Relevance of one episode's content to a query, embedding both with ``embedder``.

    The embedder is the caller's -- typically whatever ``MemoryProviderConfig`` wired for the vector
    substrate (SM-V2 #96 owns *which* model that is) -- so a candidate scored here lands on the same
    embedding space as one that arrived with a score already attached from ``VectorSubstrate.search``.

    **Single-candidate convenience, not a batch primitive.** Every call re-embeds ``query_text``,
    so scoring N candidates against one query through this function makes N redundant query
    embedding calls. A caller scoring many candidates should embed the query once and call
    :func:`cosine_relevance` directly per candidate embedding instead -- SM-E7 (#114), the intended
    caller, owns that wiring.
    """
    query_embedding = embedder.get_embedding(query_text)
    episode_embedding = embedder.get_embedding(episode_content)
    return cosine_relevance(query_embedding, episode_embedding)


async def aepisode_relevance(embedder: EmbedderLike, query_text: str, episode_content: str) -> float:
    """Async variant of :func:`episode_relevance`.

    The two embedding calls are independent I/O, run concurrently with ``asyncio.gather`` rather
    than awaited in sequence -- sequential awaits would double per-call latency against a remote
    embedder for no reason, since neither call depends on the other's result.
    """
    query_embedding, episode_embedding = await asyncio.gather(
        embedder.async_get_embedding(query_text),
        embedder.async_get_embedding(episode_content),
    )
    return cosine_relevance(query_embedding, episode_embedding)
