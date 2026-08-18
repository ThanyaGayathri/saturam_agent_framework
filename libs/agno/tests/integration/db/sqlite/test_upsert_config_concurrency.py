"""Concurrent version assignment in ``upsert_config`` against a real (temp-file) SQLite
database (aicoe-tracker#168).

``upsert_config`` with no explicit ``version`` used to read ``max(version)`` unlocked and
insert ``max + 1``: two concurrent creators could compute the same next version, and the loser
surfaced a raw ``IntegrityError`` off the ``(component_id, version)`` primary key instead of
succeeding. The fix is a conflict-tolerant insert (``ON CONFLICT DO NOTHING``) retried with a
fresh read, mirrored across Postgres and SQLite.

The race is between real connections, so the threads here are released through a barrier to
maximize the collision window, following ``test_trace_upsert.py``.
"""

import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from egpt.db.base import ComponentType
from egpt.db.component_audit import ComponentAuditAction

NUM_THREADS = 8


def test_concurrent_version_creates_get_distinct_contiguous_versions(sqlite_db_real):
    db = sqlite_db_real
    component_id = f"agent-{uuid.uuid4().hex[:8]}"
    db.create_component_with_config(
        component_id=component_id,
        component_type=ComponentType.AGENT,
        name=component_id,
        config={"model": "gpt-5.5"},
        tenant_id="t1",
    )  # v1

    barrier = Barrier(NUM_THREADS)

    def worker(i):
        barrier.wait()
        created = db.upsert_config(component_id, config={"model": f"thread-{i}"}, tenant_id="t1")
        return created["version"]

    with ThreadPoolExecutor(max_workers=NUM_THREADS) as executor:
        futures = [executor.submit(worker, i) for i in range(NUM_THREADS)]
        # result() re-raises in the main thread, so a losing creator's IntegrityError
        # (pre-fix) or retry-exhaustion ValueError fails the test loudly.
        versions = [future.result() for future in futures]

    assert sorted(versions) == list(range(2, NUM_THREADS + 2))

    rows, _ = db.list_component_audit(
        component_id, tenant_id="t1", action=ComponentAuditAction.CONFIG_CREATE.value, limit=100
    )
    # One create row per landed version: v1 from the component create, the rest from the threads.
    assert sorted(row["version"] for row in rows) == list(range(1, NUM_THREADS + 2))
