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
    """One vector per input text, in request order, regardless of the order records arrive in."""
    return [item.embedding for item in sorted(response.data, key=lambda item: item.index)]
