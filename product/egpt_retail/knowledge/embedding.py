"""Which embedder the knowledge layers use -- and why it is not a fresh decision.

The memory layer already chose, and the choice is documented and evidenced: SM-R4's
``DECISION.md``, carried in ``egpt.memory.embedding.MODE_DEFAULTS``.

============  ==============================================  ==========
Mode          Model                                           Dimensions
============  ==============================================  ==========
A ``local``   ``sentence_transformer`` / ``BAAI/bge-base-en-v1.5``  768
B ``frontier``  ``azure_openai`` / ``text-embedding-3-large``       1024
============  ==============================================  ==========

Mode A is offline-capable, which ADR 0003 A.3c makes a hard constraint for air-gapped
deployments. Mode B's 1024 is a *constraint*, not a preference: the full 3072 exceeds
pgvector's 2000-dimension limit, and 1024 tested as a statistical tie.

**This module resolves through that same decision rather than making a second one.** The
domain prose (K2), the ontology (K3) and memory all sit behind one retrieval entry point at
K10, and a deployment that embedded prose with one model, the ontology with another and memory
with a third would be three vector spaces nobody can reason about together. It would also fail
quietly: retrieval simply gets worse, with no error anywhere.

The default a bare ``SentenceTransformerEmbedder()`` gives you is ``all-MiniLM-L6-v2`` at 384
dimensions. That is **not** the decision -- SM-R4 measured ``bge-base`` as significantly ahead
of a 384-dimension model (Holm p=0.002) on a memory-representative eval. Taking the library
default silently opts out of that evidence, which is what this module exists to prevent.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Optional

from egpt.memory.embedding import MODE_DEFAULTS, resolve_memory_embedder
from egpt.metrics import ProviderMode

__all__ = [
    "KnowledgeEmbedder",
    "aresolve_knowledge_embedder",
    "described_default",
    "resolve_knowledge_embedder",
]


@dataclass(frozen=True)
class KnowledgeEmbedder:
    """An embedder with the dimension every index built from it must declare.

    The pair travels together on purpose. A vector index is created at a fixed width, and the
    two ways to get that wrong -- building the index at the library default while embedding at
    the decided model, or swapping the model under an index that already exists -- both produce
    a store that accepts writes and returns worse answers rather than an error.
    """

    embedder: Any
    dimensions: int
    mode: ProviderMode
    model_id: str

    def __str__(self) -> str:
        return f"{self.model_id} @ {self.dimensions}d (mode {self.mode.value})"


def resolve_knowledge_embedder(
    mode: ProviderMode = ProviderMode.LOCAL,
    embedder: Any = None,
    dimensions: Optional[int] = None,
) -> KnowledgeEmbedder:
    """The embedder for this deployment's knowledge layers.

    Delegates to :func:`egpt.memory.embedding.resolve_memory_embedder`, so the model, the
    dimension and the offline-capability rule are read from the one place that decides them.
    Passing ``embedder`` overrides for a test double or a deployment that carries constructor
    arguments the registry row does not; the dimension agreement check still applies.
    """
    resolved = resolve_memory_embedder(
        mode=mode, embedder=embedder, dimensions=dimensions
    )
    return KnowledgeEmbedder(
        embedder=resolved.embedder,
        dimensions=resolved.dimensions,
        mode=mode,
        model_id=str(getattr(resolved.embedder, "id", MODE_DEFAULTS[mode]["id"])),
    )


async def aresolve_knowledge_embedder(
    mode: ProviderMode = ProviderMode.LOCAL,
    embedder: Any = None,
    dimensions: Optional[int] = None,
) -> KnowledgeEmbedder:
    """Async variant. Model loading is CPU-bound and happens off the event loop."""
    return await asyncio.to_thread(
        resolve_knowledge_embedder, mode, embedder, dimensions
    )


def described_default(mode: ProviderMode) -> str:
    """The mode's decided model and width, for a startup log or a report."""
    row = MODE_DEFAULTS[mode]
    return f"{row['backend']} / {row['id']} @ {row['dimensions']}d"
