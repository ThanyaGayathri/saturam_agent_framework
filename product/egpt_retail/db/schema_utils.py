"""The advisory-lock-then-DDL-then-commit boilerplate, shared (R15, id=3988596598).

``ProposalStore.ensure_schema`` (``semantic/proposals.py``) wrote this pattern first and its
docstring is the reference every later self-provisioning store -- ``SnapshotStore``,
``KpiStore``, ``PlaygroundStore``, ``DashboardsStore``, ``RehearsalReviewStore`` -- has copied
by hand since: take a transaction-scoped advisory lock keyed by a string unique to the store's
own DDL, run every ``CREATE TABLE IF NOT EXISTS`` / ``ALTER TABLE ... ADD COLUMN IF NOT
EXISTS`` statement, commit. ``CREATE TABLE IF NOT EXISTS`` alone is not race-safe -- two
callers can both see no table and both try to create it, and one fails -- so the lock is not
optional decoration.

``RehearsalReviewStore`` is the first caller of this helper (R15). The other five stores are
deliberately left as they were: each is owned by a different lane, and folding five call sites
across three lanes into one PR-that-is-actually-about-golden-sets is exactly the kind of
out-of-lane refactor that does not belong here. This module exists so each of those owners can
adopt it independently, on their own schedule, in their own review.
"""

from __future__ import annotations

import asyncio
from typing import Any, Sequence

__all__ = ["ensure_schema", "aensure_schema"]


def ensure_schema(
    connection: Any, ddl_statements: Sequence[str], lock_key: str
) -> None:
    """Run ``ddl_statements`` under a transaction-scoped advisory lock, then commit.

    ``lock_key`` should be unique to the caller's own DDL (by convention, a dotted path like
    ``"egpt_retail.evals.ddl"``) -- ``hashtext`` collapses it to the lock id, so two stores
    sharing a key would serialise against each other for no reason.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (lock_key,))
        for statement in ddl_statements:
            cursor.execute(statement)
    connection.commit()


async def aensure_schema(
    connection: Any, ddl_statements: Sequence[str], lock_key: str
) -> None:
    """Async variant."""
    await asyncio.to_thread(ensure_schema, connection, ddl_statements, lock_key)
