"""Adversarial tenant isolation for session/run data access (aicoe-tracker#197).

Mirrors ``test_component_tenant_adversarial.py``: tenant B, with a valid credential and a
*correct* ``session_id``, must not read, list, rename, or delete anything of tenant A's, and
must not be able to overwrite tenant A's row via an upsert collision on the global ``session_id``
primary key. Proven against a strict db (``require_session_tenant_id=True``), which is the mode
an operator must opt into for any of this to be enforced -- see ``egpt/db/session_scope.py`` for
why session scoping does not fail closed by default the way component scoping does.
"""

import os
import tempfile

import pytest
from sqlalchemy import text

from egpt.db.component_scope import TenantScopeRequiredError
from egpt.integrations.db.sqlite.sqlite import SqliteDb
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
def strict_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = SqliteDb(db_file=path, require_session_tenant_id=True)
    yield db
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def seeded(strict_db):
    """One tenant-A session, with a run embedded, that tenant B will try to reach."""
    session = AgentSession(
        session_id="session-a",
        agent_id="agent-1",
        user_id="user-1",
        created_at=1,
        session_data={"session_name": "Tenant A's session"},
    )
    strict_db.upsert_session(session, tenant_id=TENANT_A)
    return strict_db


def _snapshot(db):
    with db.Session() as sess:
        rows = sess.execute(text(f"SELECT * FROM {db.session_table_name} ORDER BY session_id")).fetchall()
    return [tuple(row) for row in rows]


class TestTenantBCannotRead:
    def test_get_session_is_indistinguishable_from_missing(self, seeded):
        foreign = seeded.get_session("session-a", tenant_id=TENANT_B)
        missing = seeded.get_session("no-such-session", tenant_id=TENANT_B)
        assert foreign is None
        assert foreign == missing

    def test_get_sessions_excludes_the_row_and_the_count(self, seeded):
        sessions, total = seeded.get_sessions(tenant_id=TENANT_B, deserialize=False)
        assert sessions == []
        assert total == 0

    def test_tenant_a_still_sees_its_own_session(self, seeded):
        assert seeded.get_session("session-a", tenant_id=TENANT_A) is not None
        _, total = seeded.get_sessions(tenant_id=TENANT_A, deserialize=False)
        assert total == 1


class TestTenantBCannotMutate:
    def test_delete_session_leaves_tenant_a_untouched(self, seeded):
        before = _snapshot(seeded)
        result = seeded.delete_session("session-a", tenant_id=TENANT_B)
        assert result is False
        assert _snapshot(seeded) == before
        assert seeded.get_session("session-a", tenant_id=TENANT_A) is not None

    def test_delete_sessions_leaves_tenant_a_untouched(self, seeded):
        before = _snapshot(seeded)
        seeded.delete_sessions(["session-a"], tenant_id=TENANT_B)
        assert _snapshot(seeded) == before
        assert seeded.get_session("session-a", tenant_id=TENANT_A) is not None

    def test_rename_session_leaves_tenant_a_untouched(self, seeded):
        before = _snapshot(seeded)
        result = seeded.rename_session("session-a", None, "renamed by globex", tenant_id=TENANT_B)
        assert result is None
        assert _snapshot(seeded) == before

    def test_upsert_session_creates_a_new_row_rather_than_editing_a_foreign_one(self, seeded):
        """The subtlest case: session_id is a global primary key. Tenant B's upsert against
        tenant A's id cannot see A's row (it is scoped out), so it takes the create branch
        against an id that already exists globally. What must hold is that A's row survives
        unchanged -- not that B's call is refused outright, since a raw IntegrityError is also
        an acceptable failure mode."""
        a_before = seeded.get_session("session-a", tenant_id=TENANT_A, deserialize=False)

        try:
            seeded.upsert_session(
                AgentSession(
                    session_id="session-a",
                    agent_id="agent-1",
                    user_id="user-1",
                    created_at=1,
                    session_data={"session_name": "Hijacked by globex"},
                ),
                tenant_id=TENANT_B,
            )
        except Exception:
            pass

        a_after = seeded.get_session("session-a", tenant_id=TENANT_A, deserialize=False)
        assert a_after == a_before
        assert a_after["session_data"]["session_name"] == "Tenant A's session"
        assert a_after["tenant_id"] == TENANT_A


class TestBulkUpsertDoesNotLeakOrOverwriteAcrossTenants:
    """Covers the bulk-upsert tenant guard added for aicoe-tracker#197: before this fix,
    ``SqliteDb.upsert_sessions`` had no ownership predicate on its conflict clause at all, so a
    tenant-B bulk call against a tenant-A-owned session_id both overwrote the row and read it
    back -- unlike the singular ``upsert_session``, which already refused the identical call."""

    def test_bulk_upsert_refuses_a_foreign_tenants_row(self, seeded):
        a_before = seeded.get_session("session-a", tenant_id=TENANT_A, deserialize=False)

        bulk_result = seeded.upsert_sessions(
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

        a_after = seeded.get_session("session-a", tenant_id=TENANT_A, deserialize=False)
        assert a_after == a_before
        assert a_after["session_data"]["session_name"] == "Tenant A's session"
        assert a_after["tenant_id"] == TENANT_A

    def test_bulk_upsert_still_writes_the_callers_own_rows(self, strict_db):
        result = strict_db.upsert_sessions(
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
        assert strict_db.get_session("session-b", tenant_id=TENANT_B) is not None
        assert strict_db.get_session("session-b", tenant_id=TENANT_A) is None


class TestUnscopedAccessIsRefusedNotSilentlyWidened:
    """A caller on a strict session db cannot fall back to an unscoped read to route around
    the tenant check -- confirms the guard applies to a *known* session_id too, not just to
    listings."""

    def test_omitted_tenant_id_on_a_known_session_id_still_refuses(self, seeded):
        with pytest.raises(TenantScopeRequiredError):
            seeded.get_session("session-a")

    def test_explicit_none_on_a_known_session_id_still_refuses(self, seeded):
        with pytest.raises(TenantScopeRequiredError):
            seeded.get_session("session-a", tenant_id=None)


class TestTenantACanStillOperateAfterTheSweep:
    def test_full_lifecycle_after_adversarial_attempts(self, seeded):
        for attempt in (
            lambda: seeded.get_session("session-a", tenant_id=TENANT_B),
            lambda: seeded.delete_session("session-a", tenant_id=TENANT_B),
            lambda: seeded.rename_session("session-a", None, "x", tenant_id=TENANT_B),
        ):
            try:
                attempt()
            except Exception:
                pass

        renamed = seeded.rename_session("session-a", None, "Renamed by acme", tenant_id=TENANT_A, deserialize=False)
        assert renamed is not None
        assert renamed["session_data"]["session_name"] == "Renamed by acme"

        assert seeded.delete_session("session-a", tenant_id=TENANT_A) is True
        assert seeded.get_session("session-a", tenant_id=TENANT_A) is None
