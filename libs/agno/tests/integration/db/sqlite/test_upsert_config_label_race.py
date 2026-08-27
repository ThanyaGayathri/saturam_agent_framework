"""Concurrent label assignment in ``upsert_config`` against a real (temp-file) SQLite database
(aicoe-tracker#188).

``upsert_config``'s label uniqueness check used to be a plain ``SELECT`` run before the
insert/update that creates or updates the labeled row: two concurrent calls creating new
versions with the same label could both pass the check before either committed, landing two
config rows with a duplicate label and no error to signal the corruption. The fix issues
``BEGIN IMMEDIATE`` as the first statement of the transaction (Postgres's twin uses a
transaction-scoped ``pg_advisory_xact_lock``), forcing pysqlite to acquire its write lock right
there instead of lazily before the first DML, so the label check and the eventual write are
atomic with respect to other writers.

The race is between real connections, so the threads here are released through a barrier to
maximize the collision window, following the barrier technique used for the version-assignment
race in ``test_upsert_config_concurrency.py`` (aicoe-tracker#168). The barrier only synchronizes
thread launch, not the label check itself several statements deep inside ``upsert_config``, so a
single round misses the collision under a reverted fix nearly half the time -- the concurrent
test runs multiple rounds with a fresh component per round to make detection reliable.
"""

import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from egpt.db.base import ComponentType

NUM_THREADS = 8


def test_concurrent_same_label_creates_exactly_one_winner(sqlite_db_real):
    # A single barrier-launched round only synchronizes thread *launch*: by the time the
    # threads reach the label check several statements deep inside upsert_config, they have
    # drifted apart and the collision window is frequently missed (measured ~43-50%
    # false-negative rate against a reverted fix). Running multiple rounds with a fresh
    # component_id each time gives the race many independent chances to land, which measured
    # 12/12 detection under the same mutation.
    db = sqlite_db_real
    label = "stable"

    for _ in range(10):
        component_id = f"agent-{uuid.uuid4().hex[:8]}"
        db.create_component_with_config(
            component_id=component_id,
            component_type=ComponentType.AGENT,
            name=component_id,
            config={"model": "gpt-5.5"},
            tenant_id="t1",
        )  # v1

        barrier = Barrier(NUM_THREADS)

        def worker(i, component_id=component_id):
            barrier.wait()
            try:
                created = db.upsert_config(component_id, config={"model": f"thread-{i}"}, label=label, tenant_id="t1")
                return ("ok", created["version"])
            except ValueError as e:
                return ("error", str(e))

        with ThreadPoolExecutor(max_workers=NUM_THREADS) as executor:
            futures = [executor.submit(worker, i) for i in range(NUM_THREADS)]
            results = [future.result() for future in futures]

        winners = [r for r in results if r[0] == "ok"]
        losers = [r for r in results if r[0] == "error"]

        # Exactly one concurrent caller lands the label; the race must not let two through.
        assert len(winners) == 1, f"expected exactly one winner, got {winners}"
        assert len(losers) == NUM_THREADS - 1

        # Losers get the existing clean error, never a raw OperationalError/constraint violation.
        for _, message in losers:
            assert message == f"Label '{label}' already exists for {component_id}"

        # The database itself has exactly one row with the label -- not just the caller-side count.
        configs = db.list_configs(component_id, tenant_id="t1")
        labeled = [c for c in configs if c["label"] == label]
        assert len(labeled) == 1
        assert labeled[0]["version"] == winners[0][1]


def test_non_concurrent_label_conflict_still_raises(sqlite_db_real):
    """No change to the existing single-request label-uniqueness behavior."""
    db = sqlite_db_real
    component_id = f"agent-{uuid.uuid4().hex[:8]}"
    db.create_component_with_config(
        component_id=component_id,
        component_type=ComponentType.AGENT,
        name=component_id,
        config={"model": "gpt-5.5"},
        tenant_id="t1",
    )  # v1

    db.upsert_config(component_id, config={"model": "v2"}, label="stable", tenant_id="t1")  # v2

    try:
        db.upsert_config(component_id, config={"model": "v3"}, label="stable", tenant_id="t1")
        assert False, "expected ValueError for duplicate label"
    except ValueError as e:
        assert str(e) == f"Label 'stable' already exists for {component_id}"
