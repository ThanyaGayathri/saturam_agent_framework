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
"""

from typing import List, Optional, Sequence, Tuple

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
from egpt.memory.validation import reject_duplicate_ids

__all__ = ["SemanticFactStore"]


def _by_scope(facts: Sequence[FactWrite]) -> List[Tuple[Scope, List[FactWrite]]]:
    """Group facts under their own scope, not the caller's -- mirrors ``CoLocatedMemoryProvider._by_scope``.

    A pass scoped wider than any one fact (a tenant-wide consolidation, or an SM-L3 extraction run
    over several users' episodes) must file each fact under the scope it actually belongs to, or a
    narrower fact is written under the wider scope and becomes readable by everyone it includes.

    Keyed on the frozen ``Scope`` itself, never on ``.key()`` -- the object is hashable, and an
    equality bug in a string function should not be able to misfile a write.
    """
    batched: dict = {}
    for fact in facts:
        batched.setdefault(fact.record.scope, []).append(fact)
    return [(sc, batched[sc]) for sc in sorted(batched, key=lambda s: s.key())]


def _receipt(facts: Sequence[FactWrite]) -> WriteReceipt:
    tiers: List[MemoryTier] = []
    for fact in facts:
        if fact.record.tier not in tiers:
            tiers.append(fact.record.tier)
    return WriteReceipt(
        accepted=len(facts),
        event_ids=[f.record.id for f in facts],
        tiers_written=tiers,
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
    """

    def __init__(self, vector: Optional[VectorSubstrate], graph: Optional[GraphSubstrate]) -> None:
        self.vector = vector
        self.graph = graph

    def write(self, scope: Scope, facts: Sequence[FactWrite]) -> WriteReceipt:
        require_scope(scope, method="write")
        if not facts:
            return WriteReceipt()
        reject_duplicate_ids([fact.record for fact in facts])
        receipt = _receipt(facts)
        committed_any = False
        try:
            for fact_scope, batch in _by_scope(facts):
                if self.vector is not None:
                    self.vector.upsert(fact_scope, [fact_record(f) for f in batch])
                    committed_any = True
                if self.graph is not None:
                    self.graph.upsert_facts(fact_scope, batch)
                    committed_any = True
        except PartialWriteError:
            raise
        except MemoryOperationError:
            # A refusal before anything committed is just a refusal, matching
            # `CoLocatedMemoryProvider.write`'s rule. Once one substrate has taken the batch, the
            # other's refusal makes this a partial write like any other.
            if not committed_any:
                raise
            raise PartialWriteError(receipt) from None
        except Exception as exc:
            raise PartialWriteError(receipt) from exc
        return receipt

    async def awrite(self, scope: Scope, facts: Sequence[FactWrite]) -> WriteReceipt:
        require_scope(scope, method="awrite")
        if not facts:
            return WriteReceipt()
        reject_duplicate_ids([fact.record for fact in facts])
        receipt = _receipt(facts)
        committed_any = False
        try:
            for fact_scope, batch in _by_scope(facts):
                if self.vector is not None:
                    await self.vector.aupsert(fact_scope, [fact_record(f) for f in batch])
                    committed_any = True
                if self.graph is not None:
                    await self.graph.aupsert_facts(fact_scope, batch)
                    committed_any = True
        except PartialWriteError:
            raise
        except MemoryOperationError:
            if not committed_any:
                raise
            raise PartialWriteError(receipt) from None
        except Exception as exc:
            raise PartialWriteError(receipt) from exc
        return receipt
