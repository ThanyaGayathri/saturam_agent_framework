"""
Episodic Relevance Scoring
==========================

This example shows SM-E6's relevance signal: query-vs-episode cosine similarity,
computed independently of a live vector-store search.

Key concepts:
- VectorSubstrate.search (SM-V1) already ranks episodic hits by a normalized
  cosine score -- but only for episodes that came back from that ANN call.
  A candidate reaching composite scoring by another path (graph traversal, a
  scan, a page already fused from a prior read) has no score attached.
  cosine_relevance/episode_relevance compute the same signal directly, so
  SM-E7 (composite scoring, not yet built) can score any candidate.
- cosine_relevance takes two already-computed embeddings and is pure vector
  math: no I/O, no async twin, a similarity in [0.0, 1.0] on the same scale
  VectorSubstrate.search promises.
- episode_relevance/aepisode_relevance take an embedder plus raw text and
  embed both sides -- a single-candidate convenience, not a batch primitive:
  scoring many candidates against one query should embed the query once and
  call cosine_relevance directly per candidate (SM-E7's job).
- Every boundary refuses rather than guesses: an empty or mismatched-width
  embedding, or a None an embedder returned instead of raising.

Runs with no database and no API key: the embedder below is a tiny
deterministic bag-of-hashed-tokens model, not a real one -- which model to use
for memory is SM-V2's call.
"""

import math
from hashlib import blake2b

from egpt.memory import MemoryOperationError, cosine_relevance, episode_relevance

DIM = 128


class DeterministicEmbedder:
    """Same text -> same vector, every time. Overlapping vocabulary lands close in cosine
    space; disjoint vocabulary lands far apart -- enough to demonstrate the signal without a
    real model or an API key."""

    def get_embedding(self, text):
        vector = [0.0] * DIM
        for token in text.lower().split():
            vector[
                int.from_bytes(blake2b(token.encode(), digest_size=4).digest(), "big")
                % DIM
            ] += 1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    async def async_get_embedding(self, text):
        return self.get_embedding(text)


embedder = DeterministicEmbedder()

# ---------------------------------------------------------------------------
# 1. cosine_relevance: the signal, given two embeddings already in hand
# ---------------------------------------------------------------------------

query_embedding = embedder.get_embedding("deploy the payments service to production")
matching_embedding = embedder.get_embedding("shipped the payments service to prod")
unrelated_embedding = embedder.get_embedding(
    "the office coffee machine is broken again"
)

print("cosine_relevance on precomputed embeddings")
print("  matching:  ", cosine_relevance(query_embedding, matching_embedding))
print("  unrelated: ", cosine_relevance(query_embedding, unrelated_embedding))

# ---------------------------------------------------------------------------
# 2. episode_relevance: embed both sides from raw text
# ---------------------------------------------------------------------------

query = "deploy the payments service to production"
matching_episode = "shipped the payments service to prod"
unrelated_episode = "the office coffee machine is broken again"

print()
print("episode_relevance on raw text (embeds both sides)")
print("  matching:  ", episode_relevance(embedder, query, matching_episode))
print("  unrelated: ", episode_relevance(embedder, query, unrelated_episode))

# ---------------------------------------------------------------------------
# 3. The boundary refuses rather than guesses
# ---------------------------------------------------------------------------

print()
print("Refusals")

for label, call in (
    ("empty query embedding", lambda: cosine_relevance([], matching_embedding)),
    ("mismatched dimensions", lambda: cosine_relevance([1.0, 0.0], [1.0, 0.0, 0.0])),
    (
        "a None an embedder returned instead of a vector",
        lambda: cosine_relevance(None, matching_embedding),
    ),
):
    try:
        call()
        print("  " + label + ": NOT REFUSED -- this boundary no longer guards")
    except MemoryOperationError as error:
        print("  " + label + ":", error)

# ---------------------------------------------------------------------------
# 4. What this layer deliberately does not do
# ---------------------------------------------------------------------------

print()
print("Owned elsewhere")
print(
    "  BM25 hybrid blending:            SM-V3 (#97, HybridSearchSubstrate) / SM-H1 (#131)"
)
print("  recency and importance scoring:  SM-E4 (#111), SM-E5 (#112)")
print("  blending all three into one score: SM-E7 (#114)")
