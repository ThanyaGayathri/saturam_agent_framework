"""Shared ordering guard for OpenAI-compatible embeddings responses.

``response.data`` is documented to come back in request order, but a documented ordering is not
an enforced one -- especially once this is exercised against a third-party ``base_url`` (both
``OpenAIEmbedder`` and ``AzureOpenAIEmbedder`` support one). Sorting by ``.index`` before zipping
each vector back to its input text is the one place this idiom should live: before this module
existed, the same ``sorted(response.data, key=lambda item: item.index)`` line was copied into four
call sites across ``openai.py`` and ``azure_openai.py``, and the async batch method in each file
was missed -- exactly the drift that produced the bug this guard exists to close in the first
place (review finding on #270).
"""

from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from openai.types.create_embedding_response import CreateEmbeddingResponse


def sorted_embeddings(response: "CreateEmbeddingResponse") -> List[List[float]]:
    """One vector per input text, in request order, regardless of the order records arrive in.

    Sorting alone guards *order*, not *content*: a response with one duplicate index and one
    missing index still has the same length as a correct one, so a plain sort would silently
    return a same-length, wrong-content list past every downstream count check (``_as_vectors``,
    ``ontology/store.py``'s ``len(vectors) != len(concepts)`` guard). This checks the index set is
    exactly ``range(len(data))`` -- a clean permutation, not merely a reorderable multiset -- and
    raises rather than let a malformed response through disguised as a normal one (review finding
    on #270, round 2). Every call site already wraps this in a ``try/except`` that logs and either
    returns ``[]`` or falls back to per-text calls, so raising here reaches that existing handling
    rather than needing its own.
    """
    data = list(response.data)
    indices = {item.index for item in data}
    expected = set(range(len(data)))
    if indices != expected:
        raise ValueError(
            f"embeddings response has an inconsistent index set: expected {sorted(expected)}, "
            f"got {sorted(indices)} across {len(data)} records -- a duplicate or missing index "
            "means at least one vector cannot be safely matched to its input text"
        )
    return [item.embedding for item in sorted(data, key=lambda item: item.index)]
