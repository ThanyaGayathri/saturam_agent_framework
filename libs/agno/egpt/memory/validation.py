"""Write-path checks every adapter must apply identically (SM-C1).

These live here rather than in one adapter because the seam's swap property is only real if the
adapters agree on the *edges*. Each of these started life inside ``co_located`` and was found
missing from ``seed`` a round later -- an invariant fixed on one path only. Sharing the
implementation is what stops that recurring; the cross-adapter conformance matrix in
``tests/unit/memory/test_provider_swap.py`` is what proves it did.
"""

from typing import Sequence, Set

from egpt.memory.types import MemoryEvent, MemoryOperationError, Scope

__all__ = ["reject_duplicate_ids", "reject_foreign_scopes"]


def reject_duplicate_ids(events: Sequence[MemoryEvent]) -> None:
    """Refuse two events sharing one id in a single batch.

    Backends upsert by id, so a duplicate destroys the first event's content -- and the receipt
    still reports both accepted. On the co-located path the two substrates additionally end up
    holding different content under one id, which then collides in fusion's dedup.

    Batch-scoped only. Idempotency *across* calls is ``upsert_episodes``'s by-id contract.
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
