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

Two narrower aliases of the same failure are guarded here too, both found by review:

* the sqlite seed used the raw ``db_file`` argument while the engine opens
  ``Path(db_file).resolve()``, so ``data/app.db``, ``./data/app.db`` and the absolute path
  were three ids for one database -- and the no-argument default seeded a cwd-independent
  literal for a cwd-dependent file;
* ``SingleStoreDb`` defaulted its seed's schema suffix to ``"ai"`` while storing
  ``db_schema`` verbatim, so an unset schema (unqualified tables in the connection's default
  database) and an explicit ``"ai"`` collapsed onto one id. ``AsyncMySQLDb`` is the mirror
  image -- it *does* default the attribute, so there the two genuinely are one target.

No server is touched: SQLAlchemy engine creation is lazy about connecting, and the
SingleStore and async-MySQL engine constructors are patched out because their dialect
packages are optional dependencies -- the seed is computed before the engine is built.
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


def _async_mysql_cls(monkeypatch):
    """AsyncMySQLDb with engine creation stubbed out.

    The module imports only sqlalchemy, so it loads fine without the asyncmy driver -- the
    driver is not needed until ``create_async_engine`` resolves the ``mysql+asyncmy``
    dialect inside ``__init__``. Guarding the import therefore guards nothing, and the tests
    would fail rather than skip wherever the extra is absent. Stubbing the engine call is
    what actually removes the dependency, and the seed is computed before the engine is
    built, so nothing under test is lost.
    """
    module = importlib.import_module("egpt.integrations.db.mysql.async_mysql")
    monkeypatch.setattr(module, "create_async_engine", MagicMock())
    return module.AsyncMySQLDb


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


@pytest.mark.parametrize("cls", [SqliteDb, AsyncSqliteDb], ids=["sync", "async"])
class TestSqlitePathAliases:
    """One file must be one id, however the caller spelled the path.

    The engine opens ``Path(db_file).resolve()``, so seeding the raw argument made
    ``data/app.db``, ``./data/app.db`` and the absolute path three ids for one database.
    All three would then be admitted to the Registry as separate dbs, leaving several
    connection pools writing one sqlite file.
    """

    def test_relative_and_absolute_paths_agree(self, cls, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").mkdir()

        relative = cls(db_file="data/app.db")
        dot_relative = cls(db_file="./data/app.db")
        absolute = cls(db_file=str(tmp_path / "data" / "app.db"))

        assert relative.id == absolute.id, (
            "a relative and an absolute path to one file must not produce two registry "
            "entries pointing at the same sqlite database"
        )
        assert relative.id == dot_relative.id

    def test_default_file_agrees_with_naming_it_explicitly(self, cls, tmp_path, monkeypatch):
        """``cls()`` opens ./egpt.db, so it must seed like an explicit ./egpt.db."""
        monkeypatch.chdir(tmp_path)

        assert cls().id == cls(db_file="egpt.db").id

    def test_default_file_differs_across_working_directories(self, cls, tmp_path, monkeypatch):
        """Two cwds means two different files, so the constant-literal seed was wrong."""
        first = tmp_path / "one"
        second = tmp_path / "two"
        first.mkdir()
        second.mkdir()

        monkeypatch.chdir(first)
        one = cls()
        monkeypatch.chdir(second)
        two = cls()

        assert one.id != two.id, (
            "the no-argument default opens a cwd-dependent file, so it cannot claim a cwd-independent id"
        )


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

    def test_unset_schema_is_distinct_from_an_explicit_ai_schema(self, monkeypatch):
        """The one schema pair that aliased, and the one a naive test steps over.

        SingleStoreDb stores db_schema verbatim, so None leaves tables unqualified in the
        connection's default database while "ai" qualifies them into schema ai -- two
        different targets. Defaulting the seed's suffix to "ai" collapsed them onto one id,
        which is the registry-shadowing bug this file exists to prevent.
        """
        cls = _singlestore_cls(monkeypatch)
        url = "singlestoredb://u:p@host-a:3306/db"

        assert cls(db_url=url).id != cls(db_url=url, db_schema="ai").id, (
            "an unset schema and an explicit 'ai' schema qualify tables differently, so they must not share an id"
        )

    def test_explicit_id_wins(self, monkeypatch):
        cls = _singlestore_cls(monkeypatch)

        assert cls(db_url="singlestoredb://u:p@host-a:3306/db", id="pinned").id == "pinned"


class TestAsyncMySQLIdSeed:
    def test_different_db_urls_get_different_ids(self, monkeypatch):
        cls = _async_mysql_cls(monkeypatch)

        a = cls(db_url="mysql+asyncmy://u:p@host-a:3306/db")
        b = cls(db_url="mysql+asyncmy://u:p@host-b:3306/db")

        assert a.id != b.id, "two mysql servers must not share a deterministic id"

    def test_same_url_is_stable_and_schema_still_contributes(self, monkeypatch):
        cls = _async_mysql_cls(monkeypatch)
        url = "mysql+asyncmy://u:p@host-a:3306/db"

        assert cls(db_url=url).id == cls(db_url=url).id
        assert cls(db_url=url).id != cls(db_url=url, db_schema="other").id

    def test_unset_schema_matches_an_explicit_ai_schema(self, monkeypatch):
        """The mirror image of the SingleStore case, and correct here.

        AsyncMySQLDb *does* default `self.db_schema` to "ai", so an unset schema and an
        explicit "ai" are the same target and must share an id. Pinned because the two
        backends look alike and the right answer is opposite.
        """
        cls = _async_mysql_cls(monkeypatch)
        url = "mysql+asyncmy://u:p@host-a:3306/db"

        assert cls(db_url=url).id == cls(db_url=url, db_schema="ai").id

    def test_explicit_id_wins(self, monkeypatch):
        cls = _async_mysql_cls(monkeypatch)

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
