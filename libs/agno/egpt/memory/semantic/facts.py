"""The semantic fact store: one write path across both substrates (SM-S1 #118).

``GraphSubstrate.upsert_facts`` and ``VectorSubstrate.upsert`` are the ports; nothing before this
ticket called both from a place a second caller could reuse -- ``CoLocatedMemoryProvider.consolidate``
inlines its own per-scope batching directly around the fan-out (``_by_scope``, ``_check_distilled``,
``PartialWriteError``), interleaved with its entity/relation extraction passes. SM-L3 (#124, fact
extraction) depends on SM-S1 for exactly this: a place to hand a batch of ``FactWrite``\\ s -- its
own ADD/UPDATE/DELETE/NOOP decisions, not consolidation's Jaccard placeholder -- and get both
substrates written consistently, with the same scope-grouping and partial-write contract
``consolidate`` already establishes.

``consolidate``'s own inlined fan-out is deliberately left as-is rather than routed through this
store: it is a heavily tested control flow sharing one ``receipt`` across the fact write and the
entity/relation passes that follow it, and rewiring it is a distinct, higher-risk change no
acceptance criterion here asks for. What changed there is only the vector-leg payload -- see
:func:`egpt.memory.schemas.semantic.fact_record`'s docstring for why.

**One contract, one divergence.** Both share the same scope-grouping
(:func:`~egpt.memory.validation.group_facts_by_scope`) and the same
refusal-before-any-commit-propagates-bare rule. They diverge on one point: ``consolidate`` /
``aconsolidate`` chain a mid-batch refusal with ``raise PartialWriteError(receipt) from None``
(``co_located.py``, both halves), while this store's ``write`` / ``awrite`` chain with ``from exc``,
preserving which substrate refused and why. Not a bug in either -- ``consolidate``'s choice predates
this ticket and changing it is the same out-of-scope risk as rewiring its fan-out -- but a caller
reading a traceback from one path sees a cause the other path discards.
"""

import asyncio
from typing import Awaitable, List, Optional, Sequence

from egpt.memory.schemas.semantic import fact_record
from egpt.memory.substrates import GraphSubstrate, VectorSubstrate
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import (
    FactWrite,
    MemoryOperationError,
    PartialWriteError,
    Scope,
    WriteReceipt,
    require_scope,
)
from egpt.memory.validation import group_facts_by_scope, reject_duplicate_ids

__all__ = ["SemanticFactStore"]


def _receipt(facts: Sequence[FactWrite]) -> WriteReceipt:
    # Always exactly `[MemoryTier.SEMANTIC]`, never built by scanning `facts` for distinct tiers --
    # `_require_semantic_tier` (called before this, in both `write`/`awrite`) already guarantees
    # every fact carries that one tier, so a second pass rediscovering what the guard already
    # proved would be dead weight, not a second source of truth.
    return WriteReceipt(
        accepted=len(facts),
        event_ids=[f.record.id for f in facts],
        tiers_written=[MemoryTier.SEMANTIC],
    )


def _require_semantic_tier(facts: Sequence[FactWrite]) -> None:
    """Refuse a batch carrying a non-SEMANTIC fact.

    The class name states the constraint; nothing enforced it. SM-L3's extraction pass builds
    ``FactWrite``\\ s from an LLM's ADD/UPDATE/DELETE/NOOP decision, and a tier bug there -- an
    extractor that stamped the wrong ``MemoryTier`` on one candidate -- would otherwise dual-write
    silently under the wrong tier rather than surface at the one seam positioned to catch it.
    """
    offenders = sorted({f.record.tier.value for f in facts if f.record.tier is not MemoryTier.SEMANTIC})
    if offenders:
        raise MemoryOperationError(
            f"SemanticFactStore only accepts SEMANTIC-tier facts; got {offenders}. A fact belongs "
            "to a different tier's store, or was stamped with the wrong MemoryTier upstream.",
            field_name="tier",
        )


class SemanticFactStore:
    """Dual-write a batch of :class:`~egpt.memory.types.FactWrite`\\ s to whichever of vector/graph
    is wired -- either may be ``None`` on a substrate-partial deployment, matching every other
    write on this seam.

    **Not required to be atomic across the two substrates**, matching
    ``VectorSubstrate.upsert``'s own docstring on batching: a caller that gets a
    :class:`~egpt.memory.types.PartialWriteError` retries the whole batch using
    ``receipt.event_ids`` -- safe because both ports upsert **by id**.

    The vector leg is written through :func:`~egpt.memory.schemas.semantic.fact_record`, not
    ``fact.record`` directly -- the projection that makes a fact retrieved through
    ``VectorSubstrate.search`` carry the same subject/predicate/object/confidence/derived_by a
    ``GraphSubstrate.recall_facts`` read already does.

    Every fact must carry :attr:`~egpt.memory.tiers.MemoryTier.SEMANTIC` -- the class name states
    it, :func:`_require_semantic_tier` enforces it.
    """

    def __init__(self, vector: Optional[VectorSubstrate], graph: Optional[GraphSubstrate]) -> None:
        self.vector = vector
        self.graph = graph

    def write(self, scope: Scope, facts: Sequence[FactWrite]) -> WriteReceipt:
        """Dual-write ``facts``, returning what was accepted.

        ``scope`` is validated (:func:`~egpt.memory.types.require_scope`) but is **not** the
        scope any fact is written under -- each fact is filed under its own ``record.scope`` via
        :func:`~egpt.memory.validation.group_facts_by_scope`, which can be narrower than ``scope``
        when a caller passes a tenant-wide scope over several users' facts. ``scope`` exists so a
        caller cannot skip the entry guard by constructing a batch with no scope of its own to
        check -- ``FactWrite`` carries no top-level scope, only ``record.scope`` per fact.
        """
        require_scope(scope, method="write")
        if not facts:
            return WriteReceipt()
        reject_duplicate_ids([fact.record for fact in facts])
        _require_semantic_tier(facts)
        receipt = _receipt(facts)
        committed_any = False
        try:
            for fact_scope, batch in group_facts_by_scope(facts):
                if self.vector is not None:
                    self.vector.upsert(fact_scope, [fact_record(f) for f in batch])
                    committed_any = True
                if self.graph is not None:
                    self.graph.upsert_facts(fact_scope, batch)
                    committed_any = True
        except PartialWriteError:
            raise
        except MemoryOperationError as exc:
            # A refusal before anything committed is just a refusal, matching
            # `CoLocatedMemoryProvider.write`'s rule. Once one substrate has taken the batch, the
            # other's refusal makes this a partial write like any other -- and `from exc` keeps
            # which substrate refused and why, rather than discarding it.
            if not committed_any:
                raise
            raise PartialWriteError(receipt) from exc
        except Exception as exc:
            # No `committed_any` check here, unlike the branch above -- an exception this generic
            # (a transport error, not a refusal this seam recognises) gives no reliable signal
            # about which side of a fan-out it interrupted, so treating it as anything less than a
            # partial write would be a guess. Matches `CoLocatedMemoryProvider.consolidate`'s own
            # unconditional `except Exception` branch.
            raise PartialWriteError(receipt) from exc
        return receipt

    async def awrite(self, scope: Scope, facts: Sequence[FactWrite]) -> WriteReceipt:
        """Async twin of :meth:`write`, making the same decisions -- see its docstring for
        ``scope``'s role."""
        require_scope(scope, method="awrite")
        if not facts:
            return WriteReceipt()
        reject_duplicate_ids([fact.record for fact in facts])
        _require_semantic_tier(facts)
        receipt = _receipt(facts)
        committed_any = False
        try:
            for fact_scope, batch in group_facts_by_scope(facts):
                writes: List[Awaitable[None]] = []
                if self.vector is not None:
                    writes.append(self.vector.aupsert(fact_scope, [fact_record(f) for f in batch]))
                if self.graph is not None:
                    writes.append(self.graph.aupsert_facts(fact_scope, batch))
                if not writes:
                    continue
                # Concurrent, not sequential: vector (pgvector) and graph (Neo4j) are independent
                # backends with no ordering dependency between them, so awaiting one after the
                # other doubles per-scope latency on any deployment with both substrates wired.
                # `return_exceptions=True` settles both before this group decides anything, matching
                # `CoLocatedMemoryProvider._gather_writes`'s settle-then-propagate rule -- a bare
                # `asyncio.gather` without it would leave a sibling write's outcome unobserved.
                results = await asyncio.gather(*writes, return_exceptions=True)
                failures = [r for r in results if isinstance(r, BaseException)]
                if len(failures) < len(writes):
                    committed_any = True
                if failures:
                    # A refusal wins over a transport error as the surfaced cause, mirroring
                    # `CoLocatedMemoryProvider._raise_write_failure`: a batch that got both a refusal
                    # and a transport error from its two concurrent legs should not report "retry
                    # me" on one run and "this will never work" on the next depending on which leg's
                    # future happened to be inspected first.
                    refusal = next((f for f in failures if isinstance(f, MemoryOperationError)), None)
                    raise refusal or failures[0]
        except PartialWriteError:
            raise
        except MemoryOperationError as exc:
            if not committed_any:
                raise
            raise PartialWriteError(receipt) from exc
        except Exception as exc:
            # See `write`'s matching branch: no reliable signal about what a generic exception
            # interrupted, so it is always a partial write.
            raise PartialWriteError(receipt) from exc
        return receipt
