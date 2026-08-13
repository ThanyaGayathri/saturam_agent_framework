"""Isolation for tests that mutate the process-global discovery table, plus real SQLite dbs.

Registration is process-global by design (see ``egpt/registry/discovery.py`` -- constructors
belong to the interpreter, not to a particular ``Registry`` instance). The cost is that a
test registering a fake provider would otherwise leak it into every test that runs after it,
in whatever order pytest happens to pick. This fixture snapshots the table and restores it.

The database fixtures below back the EGPT-2.8 import/export suites. They hand out *real*
temp-file SQLite databases rather than mocks, matching ``tests/unit/db/test_component_*``:
these tests are about what the rows and the tenant predicates actually do, and a mock would
assert only that the right method was called with the right arguments.
"""

import os
import tempfile
from typing import Iterator

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


def _temp_sqlite_db(db_id: str):
    from egpt.integrations.db.sqlite.sqlite import SqliteDb

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return SqliteDb(db_file=path, id=db_id), path


@pytest.fixture
def component_db() -> Iterator[object]:
    """A real, empty SQLite database with a stable id, for the source side of a round trip."""
    db, path = _temp_sqlite_db("source-db")
    yield db
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def target_db() -> Iterator[object]:
    """A second, independent database -- the target of an import.

    Separate from ``component_db`` on purpose: importing into the same file would let a test
    pass while carrying a source-local version number or db handle across, which is exactly the
    class of bug a round-trip test exists to catch.
    """
    db, path = _temp_sqlite_db("target-db")
    yield db
    try:
        os.unlink(path)
    except OSError:
        pass
