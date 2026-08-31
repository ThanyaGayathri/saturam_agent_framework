"""Agentic memory tool closures under require_memory_tenant_id=True (aicoe-tracker#175).

Round-1 review found that add_memory/update_memory/delete_memory in MemoryManager's
_get_db_tools/_aget_db_tools swallowed TenantScopeRequiredError into a generic
"Error adding memory" string, silently disabling the whole agentic memory-extraction path under
require_memory_tenant_id=True. Round-1 fixed the swallow by re-raising instead.

Round-2 review found that re-raising made things worse, not better: nothing threads a tenant_id
through the call chain that reaches these closures (create_user_memories -> update_memory_task ->
run_memory_task -> _get_db_tools all lack the parameter), so under
require_memory_tenant_id=True the raise now fires unconditionally and propagates uncaught --
egpt.models.base's function-call executor re-raises rather than swallowing, and agent/_run.py has
no catch-all around the response loop -- crashing the entire agent run the first time the model
calls one of these tools.

These tests exercise the closures directly (the same way an LLM tool call would) against a real
SqliteDb built with require_memory_tenant_id=True and no tenant_id supplied anywhere, proving the
tenant refusal degrades to a returned error string instead of raising.
"""

import asyncio
import os
import tempfile

import pytest

from egpt.integrations.db.sqlite.sqlite import SqliteDb
from egpt.memory.manager import MemoryManager


@pytest.fixture
def scoped_sqlite_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = SqliteDb(db_file=path, require_memory_tenant_id=True)
    yield db
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def manager(scoped_sqlite_db):
    return MemoryManager(db=scoped_sqlite_db)


class TestSyncClosuresDoNotCrashTheAgentRun:
    def test_add_memory_returns_an_error_string_instead_of_raising(self, manager, scoped_sqlite_db):
        tools = manager._get_db_tools(user_id="user-1", db=scoped_sqlite_db, input_string="hello")
        add_memory = next(t for t in tools if t.__name__ == "add_memory")

        result = add_memory(memory="the user likes tea")

        assert "tenant scope required" in result.lower()

    def test_update_memory_returns_an_error_string_instead_of_raising(self, manager, scoped_sqlite_db):
        tools = manager._get_db_tools(user_id="user-1", db=scoped_sqlite_db, input_string="hello")
        update_memory = next(t for t in tools if t.__name__ == "update_memory")

        result = update_memory(memory_id="mem-1", memory="updated")

        assert "tenant scope required" in result.lower()

    def test_delete_memory_returns_an_error_string_instead_of_raising(self, manager, scoped_sqlite_db):
        tools = manager._get_db_tools(user_id="user-1", db=scoped_sqlite_db, input_string="hello")
        delete_memory = next(t for t in tools if t.__name__ == "delete_memory")

        result = delete_memory(memory_id="mem-1")

        assert "tenant scope required" in result.lower()


class TestAsyncClosuresDoNotCrashTheAgentRun:
    def test_add_memory_returns_an_error_string_instead_of_raising(self, manager, scoped_sqlite_db):
        async def run():
            tools = await manager._aget_db_tools(user_id="user-1", db=scoped_sqlite_db, input_string="hello")
            add_memory = next(t for t in tools if t.__name__ == "add_memory")
            return await add_memory(memory="the user likes tea")

        result = asyncio.run(run())

        assert "tenant scope required" in result.lower()

    def test_update_memory_returns_an_error_string_instead_of_raising(self, manager, scoped_sqlite_db):
        async def run():
            tools = await manager._aget_db_tools(user_id="user-1", db=scoped_sqlite_db, input_string="hello")
            update_memory = next(t for t in tools if t.__name__ == "update_memory")
            return await update_memory(memory_id="mem-1", memory="updated")

        result = asyncio.run(run())

        assert "tenant scope required" in result.lower()

    def test_delete_memory_returns_an_error_string_instead_of_raising(self, manager, scoped_sqlite_db):
        async def run():
            tools = await manager._aget_db_tools(user_id="user-1", db=scoped_sqlite_db, input_string="hello")
            delete_memory = next(t for t in tools if t.__name__ == "delete_memory")
            return await delete_memory(memory_id="mem-1")

        result = asyncio.run(run())

        assert "tenant scope required" in result.lower()
