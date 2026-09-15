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


def sorted_embeddings(response: "CreateEmbeddingResponse", expected_count: int) -> List[List[float]]:
    """One vector per input text, in request order, regardless of the order records arrive in.

    Sorting alone guards *order*, not *content*: a response with one duplicate index and one
    missing index still has the same length as a correct one, so a plain sort would silently
    return a same-length, wrong-content list past every downstream count check (``_as_vectors``,
    ``ontology/store.py``'s ``len(vectors) != len(concepts)`` guard). This checks the index set is
    exactly ``range(expected_count)`` -- a clean permutation of the *request* size, not merely of
    however many records came back -- and raises rather than let a malformed response through
    disguised as a normal one (review finding on #270, round 2).

    ``expected_count`` is required, not inferred from ``len(response.data)``: validating a
    response only against its own length would pass a short response too (N texts requested,
    N-1 well-formed records returned, indices ``{0..N-2}`` -- a clean permutation of *that*
    length) and silently return one fewer vector than requested (review finding on #270, round 3).
    Every call site already wraps this in a ``try/except`` that logs and either returns ``[]`` or
    falls back to per-text calls, so raising here reaches that existing handling rather than
    needing its own.
    """
    data = list(response.data)
    indices = {item.index for item in data}
    expected = set(range(expected_count))
    if len(data) != expected_count or indices != expected:
        raise ValueError(
            f"embeddings response has {len(data)} record(s) for {expected_count} input(s); index "
            f"set was {sorted(indices)}, expected {sorted(expected)} -- a short response or a "
            "duplicate/missing index means at least one vector cannot be safely matched to its "
            "input text"
        )
    return [item.embedding for item in sorted(data, key=lambda item: item.index)]
