"""Optional cross-encoder rerank stage after fusion (SM-H3 #133).

SM-R8 (``benchmarks/memory_retrieval/DECISION.md`` S1.2) measured CPU cross-encoder rerank at
~2.0-2.5s/query -- an order of magnitude over the ~300ms research budget -- and found it neutral-
to-harmful on the memory-shaped dataset. The ruling was not "never rerank": it was "off the default
hot path, optional, GPU/served, behind a latency budget, self-hosted for Mode A" -- exactly what
this module wires, reusing ``egpt.integrations.knowledge.reranker.*`` rather than building a new
reranking implementation (S5: "Reuse -- sentence_transformer/infinity = Mode A, cohere = Mode B").

The toggle and the budget live in ``MemoryProviderConfig.rerank`` (``egpt.memory.config``), the
same validated-closed-dict shape ``.fusion`` already uses -- so "toggleable per tenant" falls out
of the same config-is-the-per-tenant-unit story that module's docstring already tells; there is no
separate per-tenant switch to build here.

**The budget is enforced by abandoning the call, not by timing it.** A caller that only measured
elapsed time would still pay the full ~2s CPU cost on every query once enabled -- the budget would
be observed, never met. Both entry points hand the reranker call to something that can be walked
away from at ``budget_ms``: a one-shot thread for the sync path, ``asyncio.wait_for`` for the
async one. The abandoned call keeps running to completion in the background (nothing in this
process can interrupt a blocking Python call it did not write); what returns promptly is the
caller, with the pre-rerank fused order intact and ``budget_exceeded=True`` in the diagnostics.

**No concrete reranker is imported here.** ``egpt/memory/`` is a services-layer package (ADR
0001): it depends on :class:`RerankerLike`, the structural shape this stage actually calls, not
on ``egpt.integrations.knowledge.reranker.base.Reranker`` -- the same discipline
``egpt.memory.substrates`` applies to the vector/graph ports. A caller passes a real reranker
instance (``SentenceTransformerReranker(...)``, etc.) through ``config.rerank["reranker"]``; it
only needs to satisfy the shape below.
"""

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from typing import Any, Dict, List, Protocol, Sequence, Set, runtime_checkable

from egpt.knowledge.document import Document
from egpt.memory.types import MemoryRecord
from egpt.utils.log import log_warning

__all__ = [
    "DEFAULT_BUDGET_MS",
    "DEFAULT_CANDIDATES",
    "RerankOutcome",
    "RerankerLike",
    "arerank_records",
    "rerank_records",
]


@runtime_checkable
class RerankerLike(Protocol):
    """The two methods this stage calls on a reranker -- see the module docstring for why this
    is a structural type rather than an import of the concrete ``Reranker`` base class.

    Every ``egpt`` reranker adapter satisfies this already: ``rerank`` is the interface every
    adapter has always implemented, and ``arerank`` is the default this ticket adds to the base
    class (thread-offloading ``rerank``, or a real async override like ``InfinityReranker``'s).
    """

    def rerank(self, query: str, documents: List[Document]) -> List[Document]:
        """Reorder ``documents`` for ``query``, best match first."""
        ...

    async def arerank(self, query: str, documents: List[Document]) -> List[Document]:
        """Async variant of :meth:`rerank`."""
        ...


#: SM-R8 DECISION.md S1.2/S7: "<=300ms added", achievable only GPU/served -- CPU measured ~2.0-2.5s/
#: query. Rerank stays off by default (``config.rerank["enabled"]`` defaults to ``False``); this is
#: the budget a deployment that turns it on is opting into.
DEFAULT_BUDGET_MS = 300.0

#: SM-R8 DECISION.md S1.2: "rerank top ~50 fused candidates". The reranker's own ``top_n`` (a field
#: on every ``egpt`` reranker adapter) narrows further within this pool; this stage does not
#: truncate on its own, so a reranker with no ``top_n`` set simply reorders all ``candidates``.
DEFAULT_CANDIDATES = 50

#: Shared and bounded, not one-per-call (round-3 review finding): a ``ThreadPoolExecutor``
#: created fresh per call and abandoned on every budget overrun -- which the module docstring
#: already says is the *expected* case for a CPU-served Mode-A reranker (SM-R8 DECISION.md S1.2:
#: ~2.0-2.5s/query against this stage's ~300ms default budget) -- leaks one OS thread per overrun,
#: unbounded. A small, shared, module-level pool caps it instead: a sustained run of overruns
#: still queues work behind, at most, ``max_workers`` permanently-busy slots rather than spawning
#: another OS thread every time. Every call across every tenant's config shares this pool
#: deliberately -- rerank is meant to be an occasional, budgeted stage, not a throughput path.
_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="egpt-memory-rerank")


@dataclass(frozen=True)
class RerankOutcome:
    """What the rerank stage decided. ``diagnostics()`` folds into ``MemorySlice.fusion``'s extras."""

    records: List[MemoryRecord]
    applied: bool
    budget_exceeded: bool
    candidates: int
    elapsed_ms: float

    def diagnostics(self) -> Dict[str, Any]:
        return {
            "rerank_applied": self.applied,
            "rerank_budget_exceeded": self.budget_exceeded,
            "rerank_candidates": self.candidates,
            "rerank_elapsed_ms": round(self.elapsed_ms, 2),
        }


def _empty_outcome(records: Sequence[MemoryRecord]) -> RerankOutcome:
    return RerankOutcome(records=list(records), applied=False, budget_exceeded=False, candidates=0, elapsed_ms=0.0)


def _to_documents(records: Sequence[MemoryRecord]) -> List[Document]:
    return [Document(id=record.id, content=record.content) for record in records]


def _has_duplicate_pool_id(pool_ids: Set[str], reranked_docs: Sequence[Document]) -> bool:
    """Whether ``reranked_docs`` names the same pool id twice.

    The signal for "the reranker corrupted the set" (round-3 review finding): a duplicate means
    one real record was returned under two entries while a different one silently vanished, since
    every adapter reranks a fixed input set rather than inventing new ids. A hallucinated id (not
    in ``pool_ids`` at all) is not counted here -- :func:`_reordered` already drops it, a no-op
    rather than a data-loss bug.
    """
    seen: Set[str] = set()
    for doc in reranked_docs:
        if doc.id is None or doc.id not in pool_ids:
            continue
        if doc.id in seen:
            return True
        seen.add(doc.id)
    return False


def _reordered(pool: Sequence[MemoryRecord], reranked_docs: Sequence[Document]) -> List[MemoryRecord]:
    """Map reranked ``Document``s back onto their ``MemoryRecord``s, by id, carrying the new score.

    Deliberately does not require ``len(reranked_docs) == len(pool)``: every shipped reranker
    adapter has its own ``top_n`` field that truncates its output when configured (round-3 review
    finding -- `aws_bedrock.py`'s own docstring shows ``top_n=5`` as the idiomatic construction,
    not an edge case), so a shorter, duplicate-free result is a legitimate narrowing, not
    corruption. The caller checks for duplicates separately, with :func:`_has_duplicate_pool_id`,
    which *is* the corruption signal a length mismatch was standing in for.
    """
    by_id = {record.id: record for record in pool}
    ordered: List[MemoryRecord] = []
    for doc in reranked_docs:
        record = by_id.get(doc.id) if doc.id is not None else None
        if record is None:
            continue
        detached = record.detached()
        if doc.reranking_score is not None:
            detached.score = doc.reranking_score
        ordered.append(detached)
    return ordered


def rerank_records(
    query_text: str,
    records: Sequence[MemoryRecord],
    *,
    reranker: RerankerLike,
    candidates: int = DEFAULT_CANDIDATES,
    budget_ms: float = DEFAULT_BUDGET_MS,
) -> RerankOutcome:
    """Rerank the top ``candidates`` fused ``records``; anything past that stays in fused order.

    Falls back to the untouched fused ``records`` -- ``applied=False`` -- on a budget overrun, on
    any exception the reranker raises, or on a reranker returning a duplicate of a pool id (round-3
    review finding: two entries for one real record, silently dropping another). A shorter,
    duplicate-free result -- e.g. from the reranker's own ``top_n`` -- is accepted as a legitimate
    narrowing, not treated as corruption. A caller turned this on to *improve* precision; a failure
    in an optional stage must never be the reason a read comes back empty or wrong.
    """
    pool = list(records[:candidates])
    tail = list(records[candidates:])
    if not pool:
        return _empty_outcome(records)

    documents = _to_documents(pool)
    start = time.monotonic()
    try:
        future = _EXECUTOR.submit(reranker.rerank, query_text, documents)
        reranked_docs = future.result(timeout=budget_ms / 1000.0)
    except FutureTimeoutError:
        elapsed_ms = (time.monotonic() - start) * 1000.0
        log_warning(f"Memory rerank exceeded its {budget_ms:.0f}ms budget ({elapsed_ms:.0f}ms); using fused order.")
        return RerankOutcome(
            records=list(records), applied=False, budget_exceeded=True, candidates=len(pool), elapsed_ms=elapsed_ms
        )
    except Exception:
        elapsed_ms = (time.monotonic() - start) * 1000.0
        log_warning("Memory rerank raised; using fused order.")
        return RerankOutcome(
            records=list(records), applied=False, budget_exceeded=False, candidates=len(pool), elapsed_ms=elapsed_ms
        )

    elapsed_ms = (time.monotonic() - start) * 1000.0
    pool_ids = {record.id for record in pool}
    if _has_duplicate_pool_id(pool_ids, reranked_docs):
        return RerankOutcome(
            records=list(records), applied=False, budget_exceeded=False, candidates=len(pool), elapsed_ms=elapsed_ms
        )
    ordered = _reordered(pool, reranked_docs)
    return RerankOutcome(
        records=ordered + tail, applied=True, budget_exceeded=False, candidates=len(pool), elapsed_ms=elapsed_ms
    )


async def arerank_records(
    query_text: str,
    records: Sequence[MemoryRecord],
    *,
    reranker: RerankerLike,
    candidates: int = DEFAULT_CANDIDATES,
    budget_ms: float = DEFAULT_BUDGET_MS,
) -> RerankOutcome:
    """Async variant of :func:`rerank_records`, making the same decisions over ``reranker.arerank``.

    Every :class:`~egpt.integrations.knowledge.reranker.base.Reranker` has an ``arerank`` -- the
    base default offloads ``rerank`` to a thread, ``InfinityReranker`` overrides it with a real
    async call -- so this never needs to know which kind of reranker it was handed.
    """
    pool = list(records[:candidates])
    tail = list(records[candidates:])
    if not pool:
        return _empty_outcome(records)

    documents = _to_documents(pool)
    start = time.monotonic()
    try:
        reranked_docs = await asyncio.wait_for(reranker.arerank(query_text, documents), timeout=budget_ms / 1000.0)
    except asyncio.TimeoutError:
        elapsed_ms = (time.monotonic() - start) * 1000.0
        log_warning(f"Memory rerank exceeded its {budget_ms:.0f}ms budget ({elapsed_ms:.0f}ms); using fused order.")
        return RerankOutcome(
            records=list(records), applied=False, budget_exceeded=True, candidates=len(pool), elapsed_ms=elapsed_ms
        )
    except Exception:
        elapsed_ms = (time.monotonic() - start) * 1000.0
        log_warning("Memory rerank raised; using fused order.")
        return RerankOutcome(
            records=list(records), applied=False, budget_exceeded=False, candidates=len(pool), elapsed_ms=elapsed_ms
        )

    elapsed_ms = (time.monotonic() - start) * 1000.0
    pool_ids = {record.id for record in pool}
    if _has_duplicate_pool_id(pool_ids, reranked_docs):
        return RerankOutcome(
            records=list(records), applied=False, budget_exceeded=False, candidates=len(pool), elapsed_ms=elapsed_ms
        )
    ordered = _reordered(pool, reranked_docs)
    return RerankOutcome(
        records=ordered + tail, applied=True, budget_exceeded=False, candidates=len(pool), elapsed_ms=elapsed_ms
    )
