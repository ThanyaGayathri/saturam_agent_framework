"""Tests for v2.9.0 migration dispatch, focused on the async SingleStore regression found in
round-2 review of aicoe-tracker#178.

``SingleStoreDb`` shares MySQL's *sync* migration code and has no async class of its own. Before
this fix, `_MYSQL_FAMILY_DB_TYPES` (which includes "SingleStoreDb") was used identically by both
the sync dispatch (correct) and the async dispatch (wrong): `db_type in _MYSQL_FAMILY_DB_TYPES`
in `async_up`/`async_down` matched "SingleStoreDb" too, routing it to
`_migrate_async_mysql`/`_revert_async_mysql` -- helpers that call `db.async_session_factory()`,
an attribute a sync-only `SingleStoreDb` does not have, raising `AttributeError` instead of
falling through to "does not require schema migrations". Unreachable via `MigrationManager`
today (it picks `up`/`down` vs `async_up`/`async_down` purely by `isinstance(db, AsyncBaseDb)`,
and `SingleStoreDb` is not one), but a real crash waiting for a test -- like this one -- or a
future `AsyncBaseDb` `SingleStoreDb` subclass that calls `async_up`/`async_down` directly.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")

from egpt.db.migrations.versions import v2_9_0  # noqa: E402


def _make_async_db(class_name: str):
    """Create a stub DB whose type().__name__ returns the given class_name, mirroring the helper
    in test_migration_v2_5_0_dispatch.py."""
    mock = MagicMock()

    class FakeDb:
        def __getattr__(self, name):
            return getattr(mock, name)

    FakeDb.__name__ = class_name
    FakeDb.__qualname__ = class_name
    return FakeDb()


class TestAsyncSingleStoreFallsThrough:
    @pytest.mark.asyncio
    async def test_async_up_does_not_dispatch_singlestore_to_mysql(self):
        db = _make_async_db("SingleStoreDb")
        with patch.object(v2_9_0, "_migrate_async_mysql", new_callable=AsyncMock) as mock_fn:
            result = await v2_9_0.async_up(db, "sessions", "my_sessions")
        mock_fn.assert_not_called()
        assert result is False

    @pytest.mark.asyncio
    async def test_async_down_does_not_dispatch_singlestore_to_mysql(self):
        db = _make_async_db("SingleStoreDb")
        with patch.object(v2_9_0, "_revert_async_mysql", new_callable=AsyncMock) as mock_fn:
            result = await v2_9_0.async_down(db, "sessions", "my_sessions")
        mock_fn.assert_not_called()
        assert result is False


class TestSyncSingleStoreStillDispatchesToMysql:
    """The sync side is unaffected by this fix and must keep working: SingleStoreDb genuinely
    shares MySQL's sync migration code."""

    @patch.object(v2_9_0, "_migrate_mysql", return_value=True)
    def test_up_dispatches_singlestore_to_mysql(self, mock_fn):
        db = _make_async_db("SingleStoreDb")
        result = v2_9_0.up(db, "sessions", "my_sessions")
        mock_fn.assert_called_once_with(db, "my_sessions")
        assert result is True

    @patch.object(v2_9_0, "_revert_mysql", return_value=True)
    def test_down_dispatches_singlestore_to_mysql(self, mock_fn):
        db = _make_async_db("SingleStoreDb")
        result = v2_9_0.down(db, "sessions", "my_sessions")
        mock_fn.assert_called_once_with(db, "my_sessions")
        assert result is True


class TestAsyncMysqlStillDispatches:
    """The real async-capable member of the family must still work after the split."""

    @pytest.mark.asyncio
    @patch.object(v2_9_0, "_migrate_async_mysql", new_callable=AsyncMock, return_value=True)
    async def test_async_up_dispatches_asyncmysql(self, mock_fn):
        db = _make_async_db("AsyncMySQLDb")
        result = await v2_9_0.async_up(db, "sessions", "my_sessions")
        mock_fn.assert_called_once_with(db, "my_sessions")
        assert result is True

    @pytest.mark.asyncio
    @patch.object(v2_9_0, "_revert_async_mysql", new_callable=AsyncMock, return_value=True)
    async def test_async_down_dispatches_asyncmysql(self, mock_fn):
        db = _make_async_db("AsyncMySQLDb")
        result = await v2_9_0.async_down(db, "sessions", "my_sessions")
        mock_fn.assert_called_once_with(db, "my_sessions")
        assert result is True
