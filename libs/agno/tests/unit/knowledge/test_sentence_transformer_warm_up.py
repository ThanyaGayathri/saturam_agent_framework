"""The warm-up that stops a concurrent first ``encode()`` from deadlocking.

``SentenceTransformerEmbedder`` builds its model in ``__post_init__`` -- but constructing is not
initializing. The first ``encode()`` is what allocates the backend's compute context, and two
threads entering *that* call concurrently never return: both sit at ~300% CPU indefinitely.

Found through a retrieval fan-out running a pgvector prose leg and a Neo4j ontology leg
concurrently over one shared embedder. Neither leg was at fault and each was fine alone; the
combination was the first code in the tree to make the first encode concurrent.

The obvious test -- spawn two threads and assert they finish -- is the one not written here: on
a fixed build it passes in milliseconds, and on a broken one it *hangs the suite* rather than
failing it, which is worse than no test. So this asserts the mechanism instead: construction
encodes exactly once, before any caller can. ``test_two_cold_threads_do_not_deadlock`` below
covers the real behaviour with a hard timeout, and is skipped unless the model is already
cached locally.
"""

import threading
import time

import pytest

pytest.importorskip(
    "sentence_transformers",
    reason="SentenceTransformerEmbedder imports its SDK at module level",
)

from egpt.integrations.knowledge.embedder.sentence_transformer import (  # noqa: E402
    SentenceTransformerEmbedder,
)


class _RecordingClient:
    """Stands in for ``SentenceTransformer``, counting encodes and returning a fixed vector."""

    def __init__(self) -> None:
        self.calls: list = []

    def encode(self, text, prompt=None, normalize_embeddings=False):
        self.calls.append(text)
        return [0.0, 1.0, 0.0]


def test_construction_warms_the_model_exactly_once():
    """The warm-up runs at construction, so the first *caller* is never the first encode."""
    client = _RecordingClient()
    SentenceTransformerEmbedder(id="stub", dimensions=3, sentence_transformer_client=client)
    assert client.calls == ["warm up"], (
        "construction must run exactly one throwaway encode -- without it, two threads whose "
        "first call is concurrent deadlock"
    )


def test_a_caller_encodes_after_the_warm_up():
    """The warm-up does not consume or interfere with a real call."""
    client = _RecordingClient()
    embedder = SentenceTransformerEmbedder(id="stub", dimensions=3, sentence_transformer_client=client)
    assert embedder.get_embedding("real query") == [0.0, 1.0, 0.0]
    assert client.calls == ["warm up", "real query"]


def test_a_failing_warm_up_does_not_refuse_construction():
    """A model that cannot warm up still constructs: the real call raises on its own terms."""

    class _Failing(_RecordingClient):
        def encode(self, text, prompt=None, normalize_embeddings=False):
            raise RuntimeError("no backend")

    embedder = SentenceTransformerEmbedder(id="stub", dimensions=3, sentence_transformer_client=_Failing())
    assert embedder.sentence_transformer_client is not None


@pytest.mark.timeout(120)
def test_two_cold_threads_do_not_deadlock():
    """The real thing, with a timeout so a regression fails rather than hangs.

    Skipped unless the weights are already on disk -- this asserts a concurrency property, not
    that CI can reach Hugging Face.
    """
    pytest.importorskip("torch", reason="the real model needs torch")
    from huggingface_hub import try_to_load_from_cache  # type: ignore[import-not-found]

    model_id = "BAAI/bge-base-en-v1.5"
    if try_to_load_from_cache(model_id, "config.json") is None:
        pytest.skip(f"{model_id} is not in the local cache")

    embedder = SentenceTransformerEmbedder(id=model_id, dimensions=768)
    done = []

    def encode(i: int) -> None:
        embedder.get_embedding(f"query {i}")
        done.append(i)

    threads = [threading.Thread(target=encode, args=(i,)) for i in range(2)]
    started = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert len(done) == 2, (
        f"two concurrent encodes did not both finish in {time.time() - started:.0f}s -- "
        "the construction warm-up has regressed"
    )
