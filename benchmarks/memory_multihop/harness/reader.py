"""LLM reader: answer a question from the retrieved passages.

Kept deliberately thin and identical across retrievers so the only thing that
varies between methods is *what got retrieved* — an honest test of retrieval.
"""

from __future__ import annotations

from typing import List, Optional

from harness.llm import LLMClient, Usage
from harness.retrievers import Retrieved

_READER_SYSTEM = (
    "You answer multi-hop questions using only the provided passages. "
    "Reason across passages if needed, then reply with ONLY the final short answer "
    "(a name, entity, or date) — no explanation, no punctuation beyond the answer."
)


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
    return llm.complete(_READER_SYSTEM, user, usage=usage).strip()


async def aanswer(
    question: str,
    passages: List[Retrieved],
    texts: dict,
    llm: LLMClient,
    usage: Optional[Usage] = None,
) -> str:
    user = f"Passages:\n{_context(passages, texts)}\n\nQuestion: {question}\nAnswer:"
    return (await llm.acomplete(_READER_SYSTEM, user, usage=usage)).strip()
