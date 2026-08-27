"""Adversarial tenant isolation for session/run data access on the async path (aicoe-tracker#197).

The sync counterpart, ``test_session_tenant_adversarial.py``, exercises ``SqliteDb``. This module
exists because a PR review of this ticket found that behavioural tenant coverage existed only for
sync SQLite: stripping the tenant predicate from ``AsyncSqliteDb.get_session`` (or
``PostgresDb.get_session``) left the full ``tests/unit/db`` + ``tests/unit/workflow`` suite green.
The ticket's own read chain (``Workflow.aget_run_output`` -> ``aget_session`` -> ``_aread_session``)
is explicitly async, so ``AsyncSqliteDb`` is the highest-value backend to cover behaviourally
rather than only structurally.
"""

import os
import tempfile

import pytest
from sqlalchemy import text

from egpt.db.component_scope import TenantScopeRequiredError
from egpt.integrations.db.sqlite.async_sqlite import AsyncSqliteDb
from egpt.session.agent import AgentSession

TENANT_A = "acme"
TENANT_B = "globex"


@pytest.fixture(autouse=True)
def _no_ambient_strict_mode(monkeypatch):
    monkeypatch.delenv("EGPT_REQUIRE_TENANT_ID", raising=False)
    monkeypatch.delenv("AGNO_REQUIRE_TENANT_ID", raising=False)
    monkeypatch.delenv("EGPT_REQUIRE_SESSION_TENANT_ID", raising=False)
    monkeypatch.delenv("AGNO_REQUIRE_SESSION_TENANT_ID", raising=False)


@pytest.fixture
async def strict_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = AsyncSqliteDb(db_file=path, require_session_tenant_id=True)
    yield db
    await db.close()
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
async def seeded(strict_db):
    """One tenant-A session, seeded with the async db, that tenant B will try to reach."""
    session = AgentSession(
        session_id="session-a",
        agent_id="agent-1",
        user_id="user-1",
        created_at=1,
        session_data={"session_name": "Tenant A's session"},
    )
    await strict_db.upsert_session(session, tenant_id=TENANT_A)
    return strict_db


async def _snapshot(db):
    async with db.async_session_factory() as sess:
        result = await sess.execute(text(f"SELECT * FROM {db.session_table_name} ORDER BY session_id"))
        rows = result.fetchall()
    return [tuple(row) for row in rows]


class TestTenantBCannotReadAsync:
    async def test_get_session_is_indistinguishable_from_missing(self, seeded):
        foreign = await seeded.get_session("session-a", tenant_id=TENANT_B)
        missing = await seeded.get_session("no-such-session", tenant_id=TENANT_B)
        assert foreign is None
        assert foreign == missing

    async def test_get_sessions_excludes_the_row_and_the_count(self, seeded):
        sessions, total = await seeded.get_sessions(tenant_id=TENANT_B, deserialize=False)
        assert sessions == []
        assert total == 0

    async def test_tenant_a_still_sees_its_own_session(self, seeded):
        assert await seeded.get_session("session-a", tenant_id=TENANT_A) is not None
        _, total = await seeded.get_sessions(tenant_id=TENANT_A, deserialize=False)
        assert total == 1


class TestTenantBCannotMutateAsync:
    async def test_delete_session_leaves_tenant_a_untouched(self, seeded):
        before = await _snapshot(seeded)
        result = await seeded.delete_session("session-a", tenant_id=TENANT_B)
        assert result is False
        assert await _snapshot(seeded) == before
        assert await seeded.get_session("session-a", tenant_id=TENANT_A) is not None

    async def test_rename_session_leaves_tenant_a_untouched(self, seeded):
        before = await _snapshot(seeded)
        result = await seeded.rename_session("session-a", None, "renamed by globex", tenant_id=TENANT_B)
        assert result is None
        assert await _snapshot(seeded) == before

    async def test_upsert_session_does_not_leak_tenant_as_data_to_tenant_b(self, seeded):
        """Singular upsert: tenant B upserting against tenant A's session_id must not read
        tenant A's data back, and must not leave tenant A's row altered."""
        a_before = await seeded.get_session("session-a", tenant_id=TENANT_A, deserialize=False)

        result = await seeded.upsert_session(
            AgentSession(
                session_id="session-a",
                agent_id="agent-1",
                user_id="user-1",
                created_at=1,
                session_data={"session_name": "Hijacked by globex"},
            ),
            tenant_id=TENANT_B,
        )
        assert result is None

        a_after = await seeded.get_session("session-a", tenant_id=TENANT_A, deserialize=False)
        assert a_after == a_before
        assert a_after["session_data"]["session_name"] == "Tenant A's session"
        assert a_after["tenant_id"] == TENANT_A


class TestBulkUpsertDoesNotLeakOrOverwriteAcrossTenantsAsync:
    """Covers the async bulk-upsert tenant guard added for aicoe-tracker#197: before this fix,
    ``AsyncSqliteDb.upsert_sessions`` had no ownership predicate on its conflict clause at all,
    so a tenant-B bulk call against a tenant-A-owned session_id both overwrote the row and read
    it back."""

    async def test_bulk_upsert_refuses_a_foreign_tenants_row(self, seeded):
        a_before = await seeded.get_session("session-a", tenant_id=TENANT_A, deserialize=False)

        bulk_result = await seeded.upsert_sessions(
            [
                AgentSession(
                    session_id="session-a",
                    agent_id="agent-1",
                    user_id="mallory",
                    created_at=1,
                    session_data={"session_name": "Bulk-hijacked by globex"},
                )
            ],
            tenant_id=TENANT_B,
        )
        assert bulk_result == []

        a_after = await seeded.get_session("session-a", tenant_id=TENANT_A, deserialize=False)
        assert a_after == a_before
        assert a_after["session_data"]["session_name"] == "Tenant A's session"
        assert a_after["tenant_id"] == TENANT_A

    async def test_bulk_upsert_still_writes_the_callers_own_rows(self, strict_db):
        result = await strict_db.upsert_sessions(
            [
                AgentSession(
                    session_id="session-b",
                    agent_id="agent-1",
                    user_id="user-2",
                    created_at=1,
                    session_data={"session_name": "Tenant B's own session"},
                )
            ],
            tenant_id=TENANT_B,
        )
        assert len(result) == 1
        assert result[0].session_id == "session-b"
        assert await strict_db.get_session("session-b", tenant_id=TENANT_B) is not None
        assert await strict_db.get_session("session-b", tenant_id=TENANT_A) is None


class TestUnscopedAccessIsRefusedNotSilentlyWidenedAsync:
    async def test_omitted_tenant_id_on_a_known_session_id_still_refuses(self, seeded):
        with pytest.raises(TenantScopeRequiredError):
            await seeded.get_session("session-a")

    async def test_explicit_none_on_a_known_session_id_still_refuses(self, seeded):
        with pytest.raises(TenantScopeRequiredError):
            await seeded.get_session("session-a", tenant_id=None)
