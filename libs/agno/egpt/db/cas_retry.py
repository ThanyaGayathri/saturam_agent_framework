"""Shared retry envelope for the compare-and-swap loops guarding component concurrency.

``upsert_config`` (version assignment, the publish pointer) and ``set_current_version`` (the
rollback pointer) each guard a read-then-conditional-write against concurrent writers with the
same shape: read the current state, attempt a write that only lands if the state has not moved,
and on a lost race re-read and retry rather than raising or silently overwriting a change made
by someone else. See :mod:`egpt.db.component_audit` for why each of those three needs the guard
at all.

Before this module existed, that shape was inlined three times per backend -- six near-identical
loops across Postgres and SQLite that differed only in their success signal (``RETURNING`` a row
vs. ``rowcount > 0``) and what to do once an attempt landed. :func:`run_cas_retry` factors out the
part that must not drift: the attempt-and-retry envelope and the exhaustion error. Each call site
supplies only a closure over its own read/write statements and its own success/retry decision.
"""

from typing import Callable, Optional, TypeVar

T = TypeVar("T")


class RetriesExhausted(ValueError):
    """A compare-and-swap loop never landed within its attempt budget.

    Subclasses ``ValueError`` so existing ``except ValueError`` call sites keep working
    unchanged. A caller that wants to tell "concurrent writers kept winning" apart from an
    ordinary validation error -- to answer 409 instead of 400, for instance -- can catch this
    type first.
    """


def run_cas_retry(attempt: Callable[[], Optional[T]], max_attempts: int, exhausted_message: str) -> T:
    """Call ``attempt`` up to ``max_attempts`` times, returning its first non-``None`` result.

    ``attempt`` performs one read-then-conditional-write try inside the caller's already-open
    transaction. It returns a non-``None`` result once the write has landed (or turned out to
    be unnecessary), or ``None`` if a concurrent writer won the race and the step needs a fresh
    read on the next try. ``None`` is therefore reserved for "retry" -- an attempt whose genuine
    success value could be ``None`` cannot use this helper as-is.

    Raises :class:`RetriesExhausted` with ``exhausted_message`` if no attempt lands.
    """
    for _ in range(max_attempts):
        result = attempt()
        if result is not None:
            return result
    raise RetriesExhausted(exhausted_message)
