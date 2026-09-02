"""The schema-version contract every db backend must satisfy (aicoe-tracker#160).

`MigrationManager` calls `get_latest_schema_version(table_name)` and
`upsert_schema_version(table_name, version)` on whatever db it is handed. Nine backends
shipped these as no-argument `pass` stubs, so the manager raised `TypeError` on the very
first table and no migration could ever run on them -- invisible because nothing exercised
the combination.

These tests pin the contract statically: they inspect signatures without instantiating
anything, so they need no drivers, no containers and no credentials, and they fail loudly the
moment a new backend (or an edit to an old one) drops the parameter again.

The behavioural half -- that the version actually round-trips -- lives in
`test_schema_version_roundtrip.py`, which does need a real database.
"""

import importlib
import inspect

import pytest

from egpt.db.base import AsyncBaseDb, BaseDb
from egpt.db.migrations.manager import MigrationManager

# Every concrete backend. Kept explicit rather than discovered, so that deleting a backend
# from this list is a visible diff rather than a silent loss of coverage.
BACKENDS = [
    ("egpt.integrations.db.clickhouse.clickhouse", "ClickhouseDb"),
    ("egpt.integrations.db.dynamo.dynamo", "DynamoDb"),
    ("egpt.integrations.db.firestore.firestore", "FirestoreDb"),
    ("egpt.integrations.db.gcs_json.gcs_json_db", "GcsJsonDb"),
    ("egpt.integrations.db.in_memory.in_memory_db", "InMemoryDb"),
    ("egpt.integrations.db.json.json_db", "JsonDb"),
    ("egpt.integrations.db.mongo.async_mongo", "AsyncMongoDb"),
    ("egpt.integrations.db.mongo.mongo", "MongoDb"),
    ("egpt.integrations.db.mysql.async_mysql", "AsyncMySQLDb"),
    ("egpt.integrations.db.mysql.mysql", "MySQLDb"),
    ("egpt.integrations.db.postgres.async_postgres", "AsyncPostgresDb"),
    ("egpt.integrations.db.postgres.postgres", "PostgresDb"),
    ("egpt.integrations.db.redis.redis", "RedisDb"),
    ("egpt.integrations.db.singlestore.singlestore", "SingleStoreDb"),
    ("egpt.integrations.db.sqlite.async_sqlite", "AsyncSqliteDb"),
    ("egpt.integrations.db.sqlite.sqlite", "SqliteDb"),
    ("egpt.integrations.db.surrealdb.surrealdb", "SurrealDb"),
]

IDS = [name for _, name in BACKENDS]


def _load(module_path: str, class_name: str):
    """Import a backend class, skipping if its optional driver is absent.

    Importing is what needs the driver here, not instantiating -- several backends import
    their SDK at module scope.
    """
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:  # pragma: no cover - depends on the installed extras
        pytest.skip(f"{class_name} unavailable: {exc}")
    return getattr(module, class_name)


def _params(func) -> list:
    return [p for p in inspect.signature(func).parameters if p != "self"]


@pytest.mark.parametrize(("module_path", "class_name"), BACKENDS, ids=IDS)
def test_get_latest_schema_version_takes_table_name(module_path, class_name):
    """Schema versions are per-table, so the lookup must accept which table."""
    cls = _load(module_path, class_name)
    method = getattr(cls, "get_latest_schema_version", None)

    assert method is not None, f"{class_name} does not define get_latest_schema_version"
    assert _params(method)[:1] == ["table_name"], (
        f"{class_name}.get_latest_schema_version{inspect.signature(method)} does not match the "
        "BaseDb contract (self, table_name). MigrationManager passes the table name positionally."
    )


@pytest.mark.parametrize(("module_path", "class_name"), BACKENDS, ids=IDS)
def test_upsert_schema_version_takes_table_name_and_version(module_path, class_name):
    cls = _load(module_path, class_name)
    method = getattr(cls, "upsert_schema_version", None)

    assert method is not None, f"{class_name} does not define upsert_schema_version"
    assert _params(method)[:2] == ["table_name", "version"], (
        f"{class_name}.upsert_schema_version{inspect.signature(method)} does not match the "
        "BaseDb contract (self, table_name, version)."
    )


@pytest.mark.parametrize(("module_path", "class_name"), BACKENDS, ids=IDS)
def test_asyncness_matches_the_base_class(module_path, class_name):
    """Async backends must define these as coroutines -- the manager awaits them.

    A sync `def` on an AsyncBaseDb subclass type-checks fine and then returns a coroutine-less
    value into an `await`, so this is only catchable here or at runtime.
    """
    cls = _load(module_path, class_name)
    expect_async = issubclass(cls, AsyncBaseDb)

    for name in ("get_latest_schema_version", "upsert_schema_version"):
        method = getattr(cls, name)
        assert inspect.iscoroutinefunction(method) is expect_async, (
            f"{class_name}.{name} is {'sync' if not inspect.iscoroutinefunction(method) else 'async'} "
            f"but {class_name} subclasses {'AsyncBaseDb' if expect_async else 'BaseDb'}"
        )


@pytest.mark.parametrize(("module_path", "class_name"), BACKENDS, ids=IDS)
def test_no_backend_left_as_a_stub(module_path, class_name):
    """A body of just `pass`/docstring silently discards the version and re-runs migrations.

    This is the shape the nine broken backends had. Signature checks alone would accept it.
    """
    cls = _load(module_path, class_name)

    for name in ("get_latest_schema_version", "upsert_schema_version"):
        method = getattr(cls, name)
        try:
            source = inspect.getsource(method)
        except OSError:  # pragma: no cover - source not available
            continue

        body = [
            line.strip()
            for line in source.splitlines()
            if line.strip() and not line.strip().startswith(("def ", "async def ", "@", "#"))
        ]
        # Drop the docstring, however it is quoted.
        meaningful = [line for line in body if not line.startswith(('"""', "'''", '"', "'"))]
        assert meaningful != ["pass"], (
            f"{class_name}.{name} is an unimplemented stub. Either implement it or, if the "
            "backend genuinely cannot store versions, return a documented constant explaining why."
        )


def test_base_classes_agree_on_the_contract():
    """The two base classes must not drift from each other."""
    sync_get = _params(BaseDb.get_latest_schema_version)
    async_get = _params(AsyncBaseDb.get_latest_schema_version)
    assert sync_get == async_get == ["table_name"]

    sync_put = _params(BaseDb.upsert_schema_version)
    async_put = _params(AsyncBaseDb.upsert_schema_version)
    assert sync_put == async_put == ["table_name", "version"]


def test_backend_list_is_complete():
    """Guard against a new backend being added without contract coverage."""
    import pkgutil

    import egpt.integrations.db as db_pkg

    covered = {name for _, name in BACKENDS}
    discovered = set()

    for mod in pkgutil.walk_packages(db_pkg.__path__, db_pkg.__name__ + "."):
        if mod.name.rsplit(".", 1)[-1] in {"utils", "schemas", "models", "queries", "metrics"}:
            continue
        try:
            module = importlib.import_module(mod.name)
        except Exception:  # pragma: no cover - optional driver absent
            continue
        for attr, obj in vars(module).items():
            if (
                inspect.isclass(obj)
                and issubclass(obj, (BaseDb, AsyncBaseDb))
                and obj not in (BaseDb, AsyncBaseDb)
                and obj.__module__ == mod.name
            ):
                discovered.add(attr)

    missing = discovered - covered
    assert not missing, f"Backends missing from the contract test: {sorted(missing)}"


# ---------------------------------------------------------------------------
# The migration-version-module contract (aicoe-tracker#178)
#
# MigrationManager._supports() calls `module.supports(table_type, db_type)` on every
# versions/vX_Y_Z module in its catalogue, unconditionally -- there is no signature check or
# `getattr` fallback anywhere in the call chain. A module that omits `supports` (or ships it
# with the wrong signature) raises AttributeError/TypeError out of `up()` for the very first
# table on every backend, not just its own version. This is the same failure class #160 pinned
# above for `get_latest_schema_version`/`upsert_schema_version`, one level up the stack: pin it
# statically here too, rather than relying on a database run to discover it.
# ---------------------------------------------------------------------------

MIGRATION_MODULES = [version for version, _ in MigrationManager.available_versions if version != "v2_0_0"]


@pytest.mark.parametrize("version", MIGRATION_MODULES)
def test_migration_module_defines_the_full_entry_point_contract(version):
    """Every non-floor version module must define all five entry points MigrationManager
    dispatches to by attribute access alone."""
    module = importlib.import_module(f"egpt.db.migrations.versions.{version}")

    for name in ("supports", "up", "async_up", "down", "async_down"):
        assert hasattr(module, name), f"versions.{version} does not define {name}()"


@pytest.mark.parametrize("version", MIGRATION_MODULES)
def test_migration_module_supports_has_the_right_signature(version):
    module = importlib.import_module(f"egpt.db.migrations.versions.{version}")

    assert _params(module.supports) == ["table_type", "db_type"], (
        f"versions.{version}.supports{inspect.signature(module.supports)} does not match the "
        "(table_type, db_type) contract MigrationManager._supports calls it with."
    )


@pytest.mark.parametrize("version", MIGRATION_MODULES)
def test_migration_module_supports_is_total(version):
    """`supports()` must return `False` rather than raise for input it does not recognize --
    `MigrationManager._supports` calls every module's `supports()` for every table type it
    knows about, not just the ones a given migration targets."""
    module = importlib.import_module(f"egpt.db.migrations.versions.{version}")

    assert module.supports("not-a-real-table-type", "NotARealDbType") is False


def test_every_migration_module_on_disk_is_registered():
    """The inverse of the check above: a version file nobody points `available_versions` at is
    dead code, and a version listed in `available_versions` with no file is the ModuleNotFoundError
    `_supports`/`_up_migration`/`_down_migration` would raise the moment it is reached."""
    import pathlib

    import egpt.db.migrations.versions as versions_pkg

    versions_dir = pathlib.Path(versions_pkg.__file__).parent
    on_disk = {p.stem for p in versions_dir.glob("v*.py")}

    registered = {version for version, _ in MigrationManager.available_versions}

    assert on_disk == registered - {"v2_0_0"}, (
        f"on disk: {sorted(on_disk)}, registered (excluding the floor): {sorted(registered - {'v2_0_0'})}"
    )
