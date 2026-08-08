"""Isolation for tests that mutate the process-global discovery table.

Registration is process-global by design (see ``egpt/registry/discovery.py`` -- constructors
belong to the interpreter, not to a particular ``Registry`` instance). The cost is that a
test registering a fake provider would otherwise leak it into every test that runs after it,
in whatever order pytest happens to pick. This fixture snapshots the table and restores it.
"""

import pytest

from egpt.registry import discovery


@pytest.fixture(autouse=True)
def restore_discovery_table():
    """Snapshot and restore the discovery table around every test in this package."""
    saved_table = {kind: dict(table) for kind, table in discovery._TABLE.items()}
    saved_ready = set(discovery._READY)
    saved_entry_points = discovery._ENTRY_POINTS
    try:
        yield
    finally:
        for kind, table in saved_table.items():
            discovery._TABLE[kind] = table
            # Bump rather than restore: a consumer caching on `version` (egpt/models/utils.py
            # keys its alias indices on it) must see the rollback as a change, or it would
            # keep serving indices built from a test's fake providers.
            discovery._VERSION[kind] += 1
        discovery._READY.clear()
        discovery._READY.update(saved_ready)
        discovery._BOOTSTRAPPING.clear()
        discovery._ENTRY_POINTS = saved_entry_points
