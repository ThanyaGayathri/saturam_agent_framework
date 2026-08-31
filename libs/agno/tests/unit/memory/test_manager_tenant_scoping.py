"""MemoryManager CRUD methods against a real db + a real tenant_id (aicoe-tracker#175).

A PR review of this ticket found that `clear`, `delete_user_memory`, `clear_user_memories`, and
`aclear_user_memories` -- 4 of the 8 newly tenant-aware `MemoryManager` CRUD methods -- had no
test exercising a real db + a real `tenant_id` end to end. `test_memory_manager_crud.py` mocks
`self.db` entirely and never mentions `tenant_id`; `get_user_memories`/`add_user_memory` and
`search_user_memories`/`add_user_memory` got real coverage via the seed-bridge tests, but these
four did not. A future edit that drops `**_tenant_kwargs(tenant_id)` from any of these call sites
would regress silently without a test like this to catch it.
"""

import asyncio
import os
import tempfile

import pytest

from egpt.db.schemas import UserMemory
from egpt.integrations.db.sqlite.sqlite import SqliteDb
from egpt.memory.manager import MemoryManager

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"


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


@pytest.fixture
def manager(sqlite_db):
    return MemoryManager(db=sqlite_db)


def _seed_two_tenants(sqlite_db) -> None:
    sqlite_db.upsert_user_memory(
        UserMemory(memory_id="mem-a", user_id="user-1", memory="tenant A's memory"), tenant_id=TENANT_A
    )
    sqlite_db.upsert_user_memory(
        UserMemory(memory_id="mem-b", user_id="user-1", memory="tenant B's memory"), tenant_id=TENANT_B
    )


class TestDeleteUserMemory:
    def test_deletes_only_within_the_given_tenant(self, manager, sqlite_db):
        _seed_two_tenants(sqlite_db)

        manager.delete_user_memory(memory_id="mem-a", user_id="user-1", tenant_id=TENANT_A)

        assert sqlite_db.get_user_memory(memory_id="mem-a", user_id="user-1", tenant_id=TENANT_A) is None
        # Tenant B's memory is untouched.
        assert sqlite_db.get_user_memory(memory_id="mem-b", user_id="user-1", tenant_id=TENANT_B) is not None

    def test_cross_tenant_delete_does_not_remove_the_other_tenants_memory(self, manager, sqlite_db):
        _seed_two_tenants(sqlite_db)

        # Deleting mem-b while scoped to tenant A must not touch tenant B's row.
        manager.delete_user_memory(memory_id="mem-b", user_id="user-1", tenant_id=TENANT_A)

        assert sqlite_db.get_user_memory(memory_id="mem-b", user_id="user-1", tenant_id=TENANT_B) is not None


class TestClear:
    def test_clears_only_the_given_tenant(self, manager, sqlite_db):
        _seed_two_tenants(sqlite_db)

        manager.clear(tenant_id=TENANT_A)

        assert sqlite_db.get_user_memories(user_id="user-1", tenant_id=TENANT_A) == []
        assert len(sqlite_db.get_user_memories(user_id="user-1", tenant_id=TENANT_B)) == 1


class TestClearUserMemories:
    def test_clears_only_the_given_users_memories_in_the_given_tenant(self, manager, sqlite_db):
        sqlite_db.upsert_user_memory(UserMemory(memory_id="mem-a1", user_id="user-1", memory="a1"), tenant_id=TENANT_A)
        sqlite_db.upsert_user_memory(UserMemory(memory_id="mem-a2", user_id="user-2", memory="a2"), tenant_id=TENANT_A)
        sqlite_db.upsert_user_memory(UserMemory(memory_id="mem-b1", user_id="user-1", memory="b1"), tenant_id=TENANT_B)

        manager.clear_user_memories(user_id="user-1", tenant_id=TENANT_A)

        assert sqlite_db.get_user_memories(user_id="user-1", tenant_id=TENANT_A) == []
        # A different user in the same tenant is untouched.
        assert len(sqlite_db.get_user_memories(user_id="user-2", tenant_id=TENANT_A)) == 1
        # The same user_id in a different tenant is untouched.
        assert len(sqlite_db.get_user_memories(user_id="user-1", tenant_id=TENANT_B)) == 1


class TestAClearUserMemories:
    def test_clears_only_the_given_users_memories_in_the_given_tenant(self, manager, sqlite_db):
        sqlite_db.upsert_user_memory(UserMemory(memory_id="mem-a1", user_id="user-1", memory="a1"), tenant_id=TENANT_A)
        sqlite_db.upsert_user_memory(UserMemory(memory_id="mem-b1", user_id="user-1", memory="b1"), tenant_id=TENANT_B)

        asyncio.run(manager.aclear_user_memories(user_id="user-1", tenant_id=TENANT_A))

        assert sqlite_db.get_user_memories(user_id="user-1", tenant_id=TENANT_A) == []
        assert len(sqlite_db.get_user_memories(user_id="user-1", tenant_id=TENANT_B)) == 1
