"""Scope and batch checks every adapter must apply identically (SM-C1, SM-C4).

These live here rather than in one adapter because the seam's swap property is only real if the
adapters agree on the *edges*. Each of these started life inside ``co_located`` and was found
missing from ``seed`` a round later -- an invariant fixed on one path only. Sharing the
implementation is what stops that recurring; the cross-adapter conformance matrix in
``tests/unit/memory/test_provider_swap.py`` is what proves it did.

SM-C4 (#92) adds the entry guard. The rest of this module checks a call the provider has already
accepted; :func:`require_scope` decides whether there is a call to accept at all, and is the first
statement of every method in :data:`MEMORY_DATA_ACCESS_METHODS` on every adapter.
"""

from typing import Any, Sequence, Set, Union

from egpt.memory.types import (
    SCOPE_SLOTS,
    MemoryEvent,
    MemoryOperationError,
    MemoryRecord,
    MemoryScopeRequiredError,
    Scope,
)

__all__ = [
    "MEMORY_DATA_ACCESS_METHODS",
    "assert_scope_isolation",
    "reject_duplicate_ids",
    "reject_foreign_scopes",
    "require_scope",
]

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


def require_scope(scope: Any, *, method: str) -> Scope:
    """Refuse a memory data-access call that carries no usable tenant scope.

    ``Scope.__post_init__`` rejects a blank tenant, so a well-formed :class:`Scope` is already
    proof of scoping. What it cannot rule out is a caller that never constructed one: ``None``,
    an object that merely has the right attribute names, or a subclass whose ``__post_init__``
    does not run the tenant check. Each of those reaches a backend as whatever it is -- and the
    two that carry a ``tenant_id`` at all reach it **unnormalised**, so a stray ``"acme "``
    becomes a distinct tenant to any equality predicate, writing rows no trimmed caller can name
    again.

    Fails at the seam rather than in the backend so the refusal names the method the caller used.
    Without it the same mistake surfaced as an ``AttributeError`` from inside a driver's statement
    builder, several frames from anything the caller controls, and on a lenient backend it would
    surface as a wide answer instead.

    Args:
        scope: What the caller passed. Deliberately typed ``Any``: annotations are the thing being
            checked, so narrowing it here would state the assumption instead of testing it.
        method: The calling method's name, for the error message.

    Returns:
        The same :class:`Scope`, so a caller can write ``scope = require_scope(scope, ...)``.

    Raises:
        MemoryScopeRequiredError: If ``scope`` is not a ``Scope``, or carries a blank tenant.
    """
    if not isinstance(scope, Scope):
        raise MemoryScopeRequiredError(
            f"{method}() requires a Scope and got {type(scope).__name__}. Memory is tenant-"
            "partitioned at the data layer, and an unscoped call reads and writes every tenant's "
            "rows. Construct egpt.memory.Scope(tenant_id=...), which normalises every slot; an "
            "object that merely looks like one reaches the backend untrimmed.",
            field_name="scope",
        )
    # A subclass may define its own `__post_init__` and never reach `Scope`'s. Cheap to re-assert
    # here, and this is the one slot whose absence crosses a trust boundary rather than narrowing
    # a partition.
    if not isinstance(scope.tenant_id, str) or not scope.tenant_id.strip():
        raise MemoryScopeRequiredError(
            f"{method}() was called with Scope.tenant_id={scope.tenant_id!r}. A blank tenant is "
            "unscoped, and unscoped is the widest tenancy rather than the narrowest.",
            field_name="tenant_id",
        )
    return scope


def assert_scope_isolation(scope: Scope, records: Sequence[MemoryRecord], *, method: str) -> None:
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
