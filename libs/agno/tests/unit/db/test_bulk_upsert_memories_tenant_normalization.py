"""upsert_memories (bulk) normalizes tenant_id the same way upsert_user_memory does
(aicoe-tracker#175).

Round-2 review found that the bulk path's per-memory tenant validation loop called
``self._require_memory_tenant_scope(memory.tenant_id, "upsert_memories")`` -- which strips
whitespace and would raise on an empty string -- but discarded the return value. The actual insert
then read the raw, un-normalized ``memory.tenant_id`` instead, so a memory with
``tenant_id="acme "`` (trailing space) passed validation but was stored un-stripped, silently
diverging from what ``upsert_user_memory`` does for the same object and producing a row a later
``tenant_id="acme"`` query could never match.

Sqlite, AsyncSqlite, and Postgres all had the same pattern; these tests cover the sync Sqlite
lane, which is representative of the fix applied to all three.
"""

import os
import tempfile

import pytest

from egpt.db.schemas import UserMemory
from egpt.integrations.db.sqlite.sqlite import SqliteDb


@pytest.fixture
def sqlite_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = SqliteDb(db_file=path)
    yield db
    try:
        os.unlink(path)
    except OSError:
        pass


def test_bulk_upsert_strips_whitespace_from_each_memorys_own_tenant_id(sqlite_db):
    sqlite_db.upsert_memories([UserMemory(memory_id="mem-1", user_id="user-1", memory="likes tea", tenant_id="acme ")])

    # A caller trimming their own tenant name the way any well-behaved caller would must be able
    # to find the row -- this is the exact scenario the discarded-normalization bug broke.
    results = sqlite_db.get_user_memories(user_id="user-1", tenant_id="acme")

    assert len(results) == 1
    assert results[0].memory_id == "mem-1"


def test_bulk_upsert_matches_the_single_item_paths_normalization(sqlite_db):
    # upsert_user_memory (single-item) already normalizes; bulk must produce the identical
    # stored value for the same input, not a different (un-stripped) one.
    sqlite_db.upsert_user_memory(UserMemory(memory_id="single", user_id="user-1", memory="a", tenant_id="acme "))
    sqlite_db.upsert_memories([UserMemory(memory_id="bulk", user_id="user-1", memory="b", tenant_id="acme ")])

    single_row = sqlite_db.get_user_memory(memory_id="single", user_id="user-1", tenant_id="acme")
    bulk_row = sqlite_db.get_user_memory(memory_id="bulk", user_id="user-1", tenant_id="acme")

    assert single_row is not None
    assert bulk_row is not None
