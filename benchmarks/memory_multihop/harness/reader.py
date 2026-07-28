"""LLM reader: answer a question from the retrieved passages.

Kept deliberately thin and identical across retrievers so the only thing that
varies between methods is *what got retrieved* — an honest test of retrieval.

Reader calls are **best-effort**, mirroring the OpenIE extraction path: the Azure
content filter rejects a small number of prompts outright because a retrieved
Wikipedia passage trips a category filter. One such passage must not take down a
30-question × 3-method run, so a filtered call scores as an empty prediction (EM/F1
= 0, the conservative direction) and is recorded here as a data-quality metric
surfaced in the run summary — not silently swallowed.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from harness.llm import LLMClient, Usage
from harness.retrievers import Retrieved

#: (qid-or-tag, reason) for reader calls the provider refused. Surfaced per run.
READER_FAILURES: List[Dict[str, str]] = []

_READER_SYSTEM = (
    "You answer multi-hop questions using only the provided passages. "
    "Reason across passages if needed, then reply with ONLY the final short answer "
    "(a name, entity, or date) — no explanation, no punctuation beyond the answer."
)


def _is_content_filter(exc: Exception) -> bool:
    text = str(exc).lower()
    return "content management policy" in text or "content_filter" in text or "contentfiltered" in text


def _record_failure(tag: str, exc: Exception) -> str:
    """Log a refused reader call and score it as no-answer. Re-raise anything else."""
    if not _is_content_filter(exc):
        raise exc
    READER_FAILURES.append({"tag": tag, "reason": "azure_content_filter"})
    return ""


def _context(passages: List[Retrieved], texts: dict) -> str:
    blocks = []
    for i, r in enumerate(passages):
        blocks.append(f"[{i + 1}] {r.title}: {texts.get(r.pid, '')}")
    return "\n".join(blocks)


def answer(
    question: str,
    passages: List[Retrieved],
    texts: dict,
    llm: LLMClient,
    usage: Optional[Usage] = None,
) -> str:
    user = f"Passages:\n{_context(passages, texts)}\n\nQuestion: {question}\nAnswer:"
    try:
        return llm.complete(_READER_SYSTEM, user, usage=usage).strip()
    except Exception as exc:  # noqa: BLE001 - re-raised unless it is a content filter
        return _record_failure(question[:60], exc)


async def aanswer(
    question: str,
    passages: List[Retrieved],
    texts: dict,
    llm: LLMClient,
    usage: Optional[Usage] = None,
) -> str:
    user = f"Passages:\n{_context(passages, texts)}\n\nQuestion: {question}\nAnswer:"
    try:
        return (await llm.acomplete(_READER_SYSTEM, user, usage=usage)).strip()
    except Exception as exc:  # noqa: BLE001 - re-raised unless it is a content filter
        return _record_failure(question[:60], exc)
