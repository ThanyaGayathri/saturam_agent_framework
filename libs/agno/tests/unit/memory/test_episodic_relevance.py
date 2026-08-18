"""Episodic relevance: query-vs-episode cosine similarity as its own signal (SM-E6 #113).

No store and no network: :func:`cosine_relevance` is pure vector math, and
:func:`episode_relevance`/:func:`aepisode_relevance` are exercised against a deterministic stub
embedder, the same pattern ``test_pgvector_memory_substrate.py`` uses for SM-V1 -- a real (if
tiny) bag-of-hashed-tokens model, not a mock that only proves it was called.
"""

import asyncio
import math
from hashlib import blake2b

import pytest
from egpt.memory.episodic.relevance import aepisode_relevance, cosine_relevance, episode_relevance
from egpt.memory.errors import MemoryOperationError

DIM = 16


class StubEmbedder:
    """Deterministic and async-capable: same shape as text -> same vector, every time.

    Bag-of-hashed-tokens rather than random, so two overlapping-vocabulary texts land close in
    cosine space and two disjoint-vocabulary texts land far apart -- the property the matching /
    non-matching acceptance criterion needs.
    """

    dimensions = DIM

    def get_embedding(self, text):
        vector = [0.0] * DIM
        for token in text.lower().split():
            vector[int.from_bytes(blake2b(token.encode(), digest_size=4).digest(), "big") % DIM] += 1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    async def async_get_embedding(self, text):
        await asyncio.sleep(0)
        return self.get_embedding(text)


# -- cosine_relevance: pure vector math ----------------------------------------------------------


def test_identical_vectors_score_one():
    vector = [1.0, 0.0, 0.0]
    assert cosine_relevance(vector, vector) == pytest.approx(1.0)


def test_orthogonal_vectors_score_zero():
    """``normalize_score`` clamps non-positive similarity to 0.0 -- see ``vectordb/score.py``:
    ``normalize_cosine`` is ``1.0 - distance``, so it maps *similarity* linearly on ``[0, 1]`` and
    floors everything at or below "no correlation" (orthogonal, distance 1) to zero rather than
    the raw ``0.5`` the ``[0, 2]`` distance range would otherwise imply."""
    assert cosine_relevance([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_opposite_vectors_score_zero():
    assert cosine_relevance([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(0.0)


def test_a_zero_vector_scores_zero_rather_than_raising():
    """A zero vector has no direction -- treated as maximally distant, not as a crash or a NaN.

    Mutation check: a version that instead special-cased this to ``1.0`` (treating "no signal" as
    a perfect match) would also pass every other test here, so it is pinned on its own.
    """
    assert cosine_relevance([0.0, 0.0], [1.0, 0.0]) == pytest.approx(0.0)
    assert cosine_relevance([1.0, 0.0], [0.0, 0.0]) == pytest.approx(0.0)


def test_mismatched_dimensions_are_refused():
    with pytest.raises(MemoryOperationError, match="dimensions") as raised:
        cosine_relevance([1.0, 0.0], [1.0, 0.0, 0.0])
    assert raised.value.field_name == "episode_embedding"


def test_empty_embeddings_are_refused():
    with pytest.raises(MemoryOperationError, match="non-empty"):
        cosine_relevance([], [])


# -- episode_relevance / aepisode_relevance: the embedder-driven entry points --------------------


def test_a_relevant_episode_scores_higher_than_an_unrelated_one():
    """The acceptance criterion, verbatim: matching beats non-matching."""
    embedder = StubEmbedder()
    query = "deploy the payments service to production"
    matching = episode_relevance(embedder, query, "shipped the payments service to prod")
    unrelated = episode_relevance(embedder, query, "the office coffee machine is broken again")
    assert matching > unrelated


def test_episode_relevance_is_in_unit_range():
    embedder = StubEmbedder()
    score = episode_relevance(embedder, "deploy to production", "shipped v2 to prod")
    assert 0.0 <= score <= 1.0


@pytest.mark.asyncio
async def test_aepisode_relevance_agrees_with_the_sync_path():
    embedder = StubEmbedder()
    query, content = "deploy the payments service", "shipped the payments service"
    sync_score = episode_relevance(embedder, query, content)
    async_score = await aepisode_relevance(embedder, query, content)
    assert async_score == pytest.approx(sync_score)


@pytest.mark.asyncio
async def test_aepisode_relevance_matches_the_matching_non_matching_property():
    embedder = StubEmbedder()
    query = "deploy the payments service to production"
    matching = await aepisode_relevance(embedder, query, "shipped the payments service to prod")
    unrelated = await aepisode_relevance(embedder, query, "the office coffee machine is broken again")
    assert matching > unrelated
