"""Scope and batch checks every adapter must apply identically (SM-C1, SM-C4).

These live here rather than in one adapter because the seam's swap property is only real if the
adapters agree on the *edges*. Each of these started life inside ``co_located`` and was found
missing from ``seed`` a round later -- an invariant fixed on one path only. Sharing the
implementation is what stops that recurring; the cross-adapter conformance matrix in
``tests/unit/memory/test_provider_swap.py`` is what proves it did.

SM-C4 (#92) adds :data:`MEMORY_DATA_ACCESS_METHODS` and :func:`assert_scope_isolation`. The entry
guard itself, :func:`egpt.memory.types.require_scope`, lives beside the ``Scope`` it validates: the
graph backend calls it from ``cypher.scope_predicate``, and ADR 0001 lets an integration reach
``egpt.memory.types`` but not this module. Splitting them on that line keeps the boundary contract
green without a second copy of the guard.
"""

from typing import Any, Dict, List, Sequence, Set, Tuple, Union

from egpt.memory.types import (
    SCOPE_SLOTS,
    FactWrite,
    MemoryEvent,
    MemoryOperationError,
    MemoryRecord,
    Scope,
    require_scope,
)

__all__ = [
    "MEMORY_DATA_ACCESS_METHODS",
    "assert_scope_isolation",
    "group_facts_by_scope",
    "reject_duplicate_ids",
    "reject_foreign_scopes",
]


def group_facts_by_scope(facts: Sequence[FactWrite]) -> List[Tuple[Scope, List[FactWrite]]]:
    """Batch facts by their own scope, because that is the scope they must be written under.

    ``VectorSubstrate.upsert(scope, records)`` and ``GraphSubstrate.upsert_facts(scope, facts)``
    both store *within* the call scope, so passing a pass-wide scope would file a fact derived
    from one user's episodes under the whole tenant -- readable by every user in it.
    ``CoLocatedMemoryProvider.consolidate`` is one caller (its ``write`` path never needs this: a
    fact-write's scope and the call scope always agree there, since ``_prepare`` stamps the call
    scope onto every record); ``egpt.memory.semantic.SemanticFactStore`` -- SM-L3's (#124) intended
    caller -- is the second. Shared here rather than kept as two copies of one decision: the two
    were identical when this function was extracted, and a caller-count of two is already the
    point where a fix to one and not the other silently drifts.

    Keyed on the frozen ``Scope`` itself, never on ``.key()``. ``.key()`` is escaped and injective,
    but grouping identity should not depend on a string function at all -- the object is hashable,
    and an equality bug there is a misfiled write.
    """
    batched: Dict[Scope, List[FactWrite]] = {}
    for fact in facts:
        batched.setdefault(fact.record.scope, []).append(fact)
    return [(sc, batched[sc]) for sc in sorted(batched, key=lambda s: s.key())]


#: Every method on the seam that reaches stored memory, both halves of each verb.
#:
#: A tuple rather than a convention, because the conformance test in
#: ``tests/unit/memory/test_tenant_scoping.py`` iterates it: a provider that ships one of these
#: without the entry guard fails CI, and a verb added to the seam without being added here fails a
#: second test that compares this tuple against ``MemoryProvider``'s abstract methods.
#: ``substrates`` / ``tiers`` / ``close`` are absent because they report wiring settled at
#: construction and touch no rows.
MEMORY_DATA_ACCESS_METHODS = (
    "read",
    "aread",
    "read_tier",
    "aread_tier",
    "write",
    "awrite",
    "write_tier",
    "awrite_tier",
    "consolidate",
    "aconsolidate",
)


def assert_scope_isolation(scope: Any, records: Sequence[MemoryRecord], *, method: str) -> None:
    """No record may leave the seam, or be persisted, under a scope that did not ask for it.

    **All five slots, not just the tenant.** The substrate ports require a backend to apply every
    set slot conjunctively, and a backend that honours the tenant and drops the ``user_id``
    predicate -- the exact bug those docstrings warn about -- served one user's row to another with
    a tenant-only guard watching. Four more comparisons on a list already in hand.

    A ``None`` slot on the call scope means "any", so it constrains nothing; a set slot must match
    exactly. Applied on the consolidate path too, where a leaked row would otherwise be distilled
    into a genuine fact under the caller's scope and pass every later check.

    This is a **post**-condition, and it is worth being precise about what it can and cannot see:
    it catches a backend that returned rows it should not have, and it is silent about a backend
    that ignored the scope and happened to hold one tenant's rows. Proving the predicate was
    *applied* is a property of the statement the backend issues -- ``cypher.scope_predicate`` for
    the graph -- not of the rows that come back.

    It guards its own scope for a reason particular to this shape: an unset slot constrains
    nothing, so an unscoped scope constrains **nothing at all** and every record passes. A
    security check that is vacuous rather than wrong is the harder one to notice, and this is
    exported for third-party adapters that have no ``require_scope`` above it.
    """
    require_scope(scope, method=method)
    # Slot names and a count, never the offending values. This guard fires exactly when a backend
    # has leaked another tenant's rows, so echoing their identifiers puts them in a log line or an
    # API response -- and "which slot, how many" is the whole of what an operator can act on.
    slots: Set[str] = set()
    offenders = 0
    for record in records:
        for slot in SCOPE_SLOTS:
            wanted = getattr(scope, slot)
            if wanted is not None and getattr(record.scope, slot, None) != wanted:
                slots.add(slot)
                offenders += 1
                break
    if offenders:
        raise MemoryOperationError(
            f"{method}(): a substrate returned {offenders} record(s) outside the call scope; it "
            f"dropped a scope predicate on {sorted(slots)}.",
            field_name="scope",
        )


def reject_duplicate_ids(events: Sequence[Union[MemoryEvent, MemoryRecord]]) -> None:
    """Refuse two events or records sharing one id in a single batch.

    Backends upsert by id, so a duplicate destroys the first event's content -- and the receipt
    still reports both accepted. On the co-located path the two substrates additionally end up
    holding different content under one id, which then collides in fusion's dedup.

    Batch-scoped only. Idempotency *across* calls is ``upsert_episodes``'s by-id contract.

    Takes records as well as events because consolidation writes distilled facts, which carry the
    same hazard through a different type -- one guard rather than two that can drift.
    """
    seen: Set[str] = set()
    for event in events:
        if event.id is None:
            continue
        if event.id in seen:
            raise MemoryOperationError(
                f"Duplicate event id {event.id!r} in one write batch. One id must identify one "
                "memory, so two events sharing it would overwrite each other behind a receipt "
                "claiming both landed.",
                field_name="id",
            )
        seen.add(event.id)


def reject_foreign_scopes(scope: Scope, events: Sequence[MemoryEvent]) -> None:
    """Refuse an event carrying a different scope than the write.

    ``MemoryEvent.scope`` is required, and a provider that ignores it silently rewrites every
    event to the call scope -- accepted-and-ignored on the one field whose mismatch is a tenancy
    question.
    """
    for event in events:
        if event.scope != scope:
            raise MemoryOperationError(
                f"MemoryEvent.scope ({event.scope.key()}) does not match the write scope "
                f"({scope.key()}). One write is one scope; batch per scope.",
                field_name="scope",
            )
