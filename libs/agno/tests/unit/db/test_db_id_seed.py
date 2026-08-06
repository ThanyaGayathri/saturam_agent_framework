"""Deterministic-id seeding for the DB backends that derive an id from a connection target.

Regression guard for issue #153. Four backends seeded their deterministic id with an
unparenthesized conditional::

    seed = db_url or db_file or str(db_engine.url) if db_engine else "sqlite:///egpt.db"

which Python parses as ``(db_url or db_file or str(db_engine.url)) if db_engine else "..."``.
On the common construction path -- no ``db_engine`` -- the whole ``or`` chain was discarded
and every instance seeded from the same constant, so two different databases hashed to the
same UUID5.

That is not a cosmetic id clash. ``Registry.add_db`` de-dupes strictly by id and keeps the
first registration, and ``resolve_db_from_config`` looks components up by that id, so a
component's stored config resolved to whichever instance won the slot -- silently reading
and writing the wrong database. Nothing downstream compares ``db_file``/``db_url``, so the
mismatch produced no diagnostic at all.

No server is touched: SQLAlchemy engine creation is lazy about connecting, and SingleStore's
``create_engine`` is patched out because the dialect package is an optional dependency.
"""

import importlib
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine

from egpt.integrations.db.sqlite.async_sqlite import AsyncSqliteDb
from egpt.integrations.db.sqlite.sqlite import SqliteDb
from egpt.registry.registry import Registry

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _async_mysql_cls():
    """AsyncMySQLDb, or skip -- the asyncmy driver is an optional dependency."""
    try:
        return importlib.import_module("egpt.integrations.db.mysql.async_mysql").AsyncMySQLDb
    except ImportError as e:  # pragma: no cover - depends on the installed extras
        pytest.skip(f"optional driver for 'AsyncMySQLDb' not installed: {e}")


def _sqlite_url(cls, path):
    """A db_url the given sqlite backend will accept -- the async one needs an async driver."""
    scheme = "sqlite+aiosqlite" if cls is AsyncSqliteDb else "sqlite"
    return f"{scheme}:///{path}"


def _singlestore_cls(monkeypatch):
    """SingleStoreDb with engine creation stubbed out.

    The singlestoredb dialect is an optional dependency, so a real ``create_engine`` call
    would fail on dialect lookup before the id under test is ever observable. Only the seed
    matters here, and it is computed before the engine is built.
    """
    module = importlib.import_module("egpt.integrations.db.singlestore.singlestore")
    monkeypatch.setattr(module, "create_engine", MagicMock())
    return module.SingleStoreDb


# --------------------------------------------------------------------------- #
# SQLite (sync + async) -- seeded from db_file / db_url
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("cls", [SqliteDb, AsyncSqliteDb], ids=["sync", "async"])
class TestSqliteIdSeed:
    def test_different_db_files_get_different_ids(self, cls, tmp_path):
        a = cls(db_file=str(tmp_path / "a.db"))
        b = cls(db_file=str(tmp_path / "b.db"))

        assert a.id != b.id, (
            "two sqlite dbs pointed at different files must not share a deterministic id; "
            "the registry keeps only the first and every component resolving that id would "
            "read the other file"
        )

    def test_different_db_urls_get_different_ids(self, cls, tmp_path):
        a = cls(db_url=_sqlite_url(cls, tmp_path / "a.db"))
        b = cls(db_url=_sqlite_url(cls, tmp_path / "b.db"))

        assert a.id != b.id

    def test_same_db_file_is_stable_across_constructions(self, cls, tmp_path):
        path = str(tmp_path / "same.db")

        assert cls(db_file=path).id == cls(db_file=path).id, (
            "determinism is the point of the seed: the same target must survive a restart"
        )

    def test_explicit_id_wins(self, cls, tmp_path):
        assert cls(db_file=str(tmp_path / "a.db"), id="pinned").id == "pinned"


class TestSqliteEngineFallback:
    """``db_engine`` is the fallback seed, reachable only when no url/file is given."""

    def test_engine_only_seeds_from_engine_url(self, tmp_path):
        engine = create_engine(f"sqlite:///{tmp_path / 'engine.db'}")
        other = create_engine(f"sqlite:///{tmp_path / 'other.db'}")

        assert SqliteDb(db_engine=engine).id != SqliteDb(db_engine=other).id
        assert SqliteDb(db_engine=engine).id == SqliteDb(db_engine=engine).id

    def test_db_file_takes_precedence_over_engine(self, tmp_path):
        """The engine URL is a fallback, not an override.

        Guards the direction the parentheses must not flip: with both supplied, the
        explicit connection target wins, so the two dbs below stay distinct even though
        they share an engine.
        """
        engine = create_engine(f"sqlite:///{tmp_path / 'shared.db'}")

        a = SqliteDb(db_file=str(tmp_path / "a.db"), db_engine=engine)
        b = SqliteDb(db_file=str(tmp_path / "b.db"), db_engine=engine)

        assert a.id != b.id
        assert a.id != SqliteDb(db_engine=engine).id


# --------------------------------------------------------------------------- #
# SingleStore / async MySQL -- seeded from db_url plus the schema suffix
# --------------------------------------------------------------------------- #


class TestSingleStoreIdSeed:
    def test_different_db_urls_get_different_ids(self, monkeypatch):
        cls = _singlestore_cls(monkeypatch)

        a = cls(db_url="singlestoredb://u:p@host-a:3306/db")
        b = cls(db_url="singlestoredb://u:p@host-b:3306/db")

        assert a.id != b.id, "two singlestore servers must not share a deterministic id"

    def test_same_url_is_stable_and_schema_still_contributes(self, monkeypatch):
        cls = _singlestore_cls(monkeypatch)
        url = "singlestoredb://u:p@host-a:3306/db"

        assert cls(db_url=url).id == cls(db_url=url).id
        assert cls(db_url=url).id != cls(db_url=url, db_schema="other").id

    def test_explicit_id_wins(self, monkeypatch):
        cls = _singlestore_cls(monkeypatch)

        assert cls(db_url="singlestoredb://u:p@host-a:3306/db", id="pinned").id == "pinned"


class TestAsyncMySQLIdSeed:
    def test_different_db_urls_get_different_ids(self):
        cls = _async_mysql_cls()

        a = cls(db_url="mysql+asyncmy://u:p@host-a:3306/db")
        b = cls(db_url="mysql+asyncmy://u:p@host-b:3306/db")

        assert a.id != b.id, "two mysql servers must not share a deterministic id"

    def test_same_url_is_stable_and_schema_still_contributes(self):
        cls = _async_mysql_cls()
        url = "mysql+asyncmy://u:p@host-a:3306/db"

        assert cls(db_url=url).id == cls(db_url=url).id
        assert cls(db_url=url).id != cls(db_url=url, db_schema="other").id

    def test_explicit_id_wins(self):
        cls = _async_mysql_cls()

        assert cls(db_url="mysql+asyncmy://u:p@host-a:3306/db", id="pinned").id == "pinned"


# --------------------------------------------------------------------------- #
# The consequence the seed fix exists to prevent
# --------------------------------------------------------------------------- #


class TestRegistryDoesNotShadow:
    def test_two_sqlite_dbs_are_both_reachable_by_their_own_id(self, tmp_path):
        """The end-to-end shape of the bug: add_db drops any db whose id is taken.

        With colliding seeds the second db never entered the registry, and any component
        config carrying that id resolved to the first one's file.
        """
        registry = Registry()
        a = SqliteDb(db_file=str(tmp_path / "a.db"))
        b = SqliteDb(db_file=str(tmp_path / "b.db"))

        registry.add_db(a)
        registry.add_db(b)

        assert registry.get_db(a.id) is a
        assert registry.get_db(b.id) is b, (
            "the second db was shadowed by the first; components keyed on its id would "
            "silently read and write the wrong sqlite file"
        )
