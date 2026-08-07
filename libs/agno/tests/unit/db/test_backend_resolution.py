"""Guards for DB-backend resolution and the EGPT-1.6 compatibility shims.

EGPT-1.6 moved the 14 backend packages from ``egpt/db/`` to ``egpt/integrations/db/`` and
left a deprecation shim at every old path. Three things can silently rot after a move like
that, and prose in a docstring cannot catch any of them:

* a backend is added to the integrations tree without a ``_DB_BACKENDS`` row, so it is
  unreachable by ``db_from_dict``;
* a shim is omitted or stops mirroring its target, so the deprecated path degrades from a
  drop-in replacement to a partially-bound module (exactly what happened to the ``azure``
  and ``langdb`` model shims in EGPT-1.4);
* core reaches back into a concrete backend, undoing the extraction.

The layout checks are AST-based and never import, so they hold in an environment with none
of the optional DB drivers installed.
"""

import importlib
import inspect
import sys
import warnings
from pathlib import Path

import pytest

from egpt.db.base import BaseDb
from egpt.db.table_names import TABLE_SUFFIXES, table_name_attr
from egpt.db.utils import db_from_dict
from egpt.registry.discovery import declarations
from tests.unit._layer_guards import (
    check_shim_parity,
    classes_defining,
    discover_subclasses,
    find_shim_path_strings,
    import_sites,
    package_root,
    registered_defining_classes,
)

# `declarations`, not `entries`: these guards describe what this repository ships, so a
# third-party backend registered through an `egpt.dbs` entry point must not be pulled into
# assertions about the integrations tree on disk.
DB_DECLARATIONS = declarations("db")

# The three views the pre-EGPT-1.9 `db/utils.py` exported, derived from the declaration
# rows instead. Kept as local names so each guard below still reads as a statement about
# the table it guards.
DB_BACKEND_CLASSES = {key: (reg.module, reg.attr) for key, reg in DB_DECLARATIONS.items()}
ASYNC_DB_BACKEND_CLASSES = {
    key: (reg.module, reg.metadata["async_attr"])
    for key, reg in DB_DECLARATIONS.items()
    if reg.metadata.get("async_attr")
}
RECONSTRUCTABLE_DB_CLASSES = {
    key: (reg.module, reg.attr) for key, reg in DB_DECLARATIONS.items() if reg.metadata.get("reconstructable")
}

# Backend packages that moved in EGPT-1.6. `async_postgres` is an alias package with no
# declaration row of its own; it still needs a shim, so it is listed here.
_MOVED_PACKAGES = frozenset(DB_BACKEND_CLASSES) | {"async_postgres"}

# Every .py file that had a public `egpt.db.<backend>` import path before EGPT-1.6, taken
# from the tree at the move commit. Frozen on purpose -- see test_every_moved_module_has_a_shim
# for why this cannot be re-derived from the live integrations tree.
_MOVED_MODULES = frozenset(
    {
        "async_postgres/__init__.py",
        "clickhouse/__init__.py",
        "clickhouse/clickhouse.py",
        "clickhouse/schemas.py",
        "clickhouse/utils.py",
        "dynamo/__init__.py",
        "dynamo/dynamo.py",
        "dynamo/schemas.py",
        "dynamo/utils.py",
        "firestore/__init__.py",
        "firestore/firestore.py",
        "firestore/schemas.py",
        "firestore/utils.py",
        "gcs_json/__init__.py",
        "gcs_json/gcs_json_db.py",
        "gcs_json/utils.py",
        "in_memory/__init__.py",
        "in_memory/in_memory_db.py",
        "in_memory/utils.py",
        "json/__init__.py",
        "json/json_db.py",
        "json/utils.py",
        "mongo/__init__.py",
        "mongo/async_mongo.py",
        "mongo/mongo.py",
        "mongo/schemas.py",
        "mongo/utils.py",
        "mysql/__init__.py",
        "mysql/async_mysql.py",
        "mysql/mysql.py",
        "mysql/schemas.py",
        "mysql/utils.py",
        "postgres/__init__.py",
        "postgres/async_postgres.py",
        "postgres/postgres.py",
        "postgres/schemas.py",
        "postgres/utils.py",
        "redis/__init__.py",
        "redis/redis.py",
        "redis/schemas.py",
        "redis/utils.py",
        "singlestore/__init__.py",
        "singlestore/schemas.py",
        "singlestore/singlestore.py",
        "singlestore/utils.py",
        "sqlite/__init__.py",
        "sqlite/async_sqlite.py",
        "sqlite/schemas.py",
        "sqlite/sqlite.py",
        "sqlite/utils.py",
        "surrealdb/__init__.py",
        "surrealdb/metrics.py",
        "surrealdb/models.py",
        "surrealdb/queries.py",
        "surrealdb/surrealdb.py",
        "surrealdb/utils.py",
    }
)


def _backends_root():
    """Backend implementations live in the integrations layer as of EGPT-1.6."""
    root = package_root() / "egpt" / "integrations" / "db"
    assert root.is_dir(), f"db integrations root not found: {root}"
    return root


def _deprecated_db_root():
    """The pre-EGPT-1.6 `egpt/db` tree, which now holds the interface plus shims only."""
    return package_root() / "egpt" / "db"


def _discover_db_subclasses(root, packages=None):
    """Every concrete BaseDb/AsyncBaseDb subclass under ``root``, found without importing.

    Rooted at both ABCs because they are siblings, not one deriving from the other. The scan
    skips ``__init__.py``: those only re-export, and counting a re-exported class as a second
    definition would make the registration guard demand a table row per import path.
    """
    return discover_subclasses(root, ("BaseDb", "AsyncBaseDb"), include_init=False, packages=packages)


def _registered_defining_classes(root):
    """Resolve every _DB_BACKENDS entry to the (defining_module, class) it points at."""
    return registered_defining_classes(
        root, list(DB_BACKEND_CLASSES.values()) + list(ASYNC_DB_BACKEND_CLASSES.values())
    )


def test_every_db_subclass_is_registered():
    """Every concrete backend on disk must appear in the _DB_BACKENDS table.

    CI then fails on the same PR that adds a backend without a row, so the table stays the
    single source of truth without per-backend runtime wiring.
    """
    root = _backends_root()
    discovered = _discover_db_subclasses(root)
    covered = _registered_defining_classes(root)

    # Non-vacuity floor. Without it, a stale scan root yields an empty `discovered`, the
    # assertion below can never fire, and the test reports PASS while checking nothing --
    # which is how the EGPT-1.4 models guard silently broke when providers moved.
    assert len(discovered) >= 17, (
        f"Only {len(discovered)} BaseDb/AsyncBaseDb subclasses discovered under {root}; expected "
        "at least the 13 sync + 4 async backends. The scan root is probably stale, which makes "
        "this guard vacuous."
    )

    missing = sorted(f"{name} ({module})" for (module, name) in discovered - covered)
    assert not missing, (
        "Backend classes missing from _DB_BACKENDS: "
        + ", ".join(missing)
        + ". Add each to egpt/registry/_declarations/dbs.py."
    )


def test_deprecated_db_tree_holds_only_shims():
    """No backend implementation may reappear under the deprecated `egpt/db` tree.

    That tree is the interface, the domain schemas, the migrations and compatibility shims.
    A backend defined there would be invisible to test_every_db_subclass_is_registered,
    which scans the integrations layer, so it could ship unregistered.

    Scans the whole tree, not just the 14 moved package names. Filtering to those names
    would miss a backend added in a *new* directory under egpt/db/ -- the likeliest way
    this rule actually gets broken, and precisely the case the rule exists for.
    """
    stragglers = _discover_db_subclasses(_deprecated_db_root())
    assert not stragglers, (
        "Backend classes found under the deprecated egpt/db tree: "
        + ", ".join(sorted(f"{name} ({module})" for (module, name) in stragglers))
        + ". Backend implementations belong under egpt/integrations/db/."
    )


# Names a shim exports that its target deliberately does not. Empty on purpose: every db
# package declares its own __all__, so the azure/langdb failure mode from EGPT-1.4 -- where
# there was no __all__ to copy -- cannot recur here. An unexplained extra fails just like an
# omission; this is a record of deliberate asymmetry, not a soft escape hatch.
_SHIM_EXTRA_EXPORTS: dict = {}


# Names a shim lists in __all__ but deliberately does not bind eagerly. mongo's target
# provides AsyncMongoDb through its own __getattr__ so that `import egpt.db.mongo` does not
# pull in motor; binding it here would break that.
_LAZILY_BOUND_SHIM_EXPORTS = {"mongo": {"AsyncMongoDb"}}


def test_deprecated_shims_reexport_everything_their_target_exports():
    """Each `egpt/db/<backend>` shim must export what its integrations target exports."""
    checked, problems = check_shim_parity(
        _backends_root(),
        _deprecated_db_root(),
        extra_exports=_SHIM_EXTRA_EXPORTS,
        lazy_exports=_LAZILY_BOUND_SHIM_EXPORTS,
    )

    assert checked >= 14, (
        f"Only {checked} shim/target pairs compared; expected all 14 backend packages. One of the "
        "two roots is probably stale, which makes this guard vacuous."
    )
    assert not problems, "Compatibility shims out of sync with their targets:\n" + "\n".join(problems)


@pytest.mark.parametrize("package", sorted(_MOVED_PACKAGES))
def test_shim_names_are_really_bound_not_just_listed(package):
    """Every name a shim advertises must be in its module dict, not only reachable via __getattr__.

    The AST parity check above compares two ``__all__`` literals, which a shim consisting of
    nothing but ``__all__`` plus the catch-all ``__getattr__`` would satisfy while binding
    nothing. Such a shim still works at runtime, so only this check would catch it -- and
    the explicit imports are exactly what makes ``mypy`` resolve the deprecated path and
    what gives ``mock.patch`` a real target.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        try:
            shim = importlib.import_module(f"egpt.db.{package}")
        except ImportError as e:
            pytest.skip(f"optional driver for '{package}' not installed: {e}")

    lazy = _LAZILY_BOUND_SHIM_EXPORTS.get(package, set())
    unbound = sorted(name for name in shim.__all__ if name not in lazy and name not in vars(shim))
    assert not unbound, (
        f"egpt.db.{package} lists {unbound} in __all__ but does not bind them eagerly; they "
        "resolve only through __getattr__, so mypy and mock.patch cannot see them. Import "
        "them explicitly, or record the reason in _LAZILY_BOUND_SHIM_EXPORTS."
    )


@pytest.mark.parametrize("package", sorted(_MOVED_PACKAGES))
def test_shim_dir_lists_everything_it_exports(package):
    """``dir()`` on a shim must cover ``__all__``, including the lazily-provided names.

    The shim docstrings promise ``dir()`` keeps working, but a module's default ``dir()``
    returns its globals and never consults ``__all__``. Names the target provides lazily
    -- ``AsyncMongoDb`` is the live case -- are reachable via ``__getattr__`` and listed in
    ``__all__`` yet absent from the module dict, so without a ``__dir__`` override the
    promise was false exactly where a caller doing discovery would notice.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        try:
            shim = importlib.import_module(f"egpt.db.{package}")
        except ImportError as e:
            pytest.skip(f"optional driver for '{package}' not installed: {e}")

    missing = sorted(set(shim.__all__) - set(dir(shim)))
    assert not missing, (
        f"egpt.db.{package} exports {missing} but dir() omits them; add or fix the "
        "__dir__ override so discovery matches __all__."
    )


@pytest.mark.parametrize("key", sorted(ASYNC_DB_BACKEND_CLASSES))
def test_every_registered_async_backend_resolves(key):
    """The async half of the table needs the same resolution check as the sync half."""
    module_name, class_name = ASYNC_DB_BACKEND_CLASSES[key]
    try:
        module = importlib.import_module(module_name)
    except ImportError as e:
        pytest.skip(f"optional driver for '{key}' not installed: {e}")

    assert getattr(module, class_name, None) is not None, f"{module_name} does not export {class_name}"


def test_every_moved_module_has_a_shim():
    """Every module that had a public egpt.db path before EGPT-1.6 still has one.

    Checked against a frozen list rather than against whatever is on disk under
    egpt/integrations/db today. Enumerating the live tree would demand a shim for modules
    added *after* the move -- paths no one could ever have imported -- so unrelated future
    work would go red and the only way to green it would be to hand-write a deprecation
    shim for a never-public path, growing the surface this is meant to retire.

    The list is the closed set of .py files under egpt/db/<backend>/ at the move commit.
    A shim may be deleted from it only when the whole deprecation layer is retired.
    """
    shims_root = _deprecated_db_root()

    missing = sorted(rel for rel in _MOVED_MODULES if not (shims_root / rel).exists())
    assert not missing, (
        "Modules that had a public egpt.db path before EGPT-1.6 and now have no shim: "
        + ", ".join(missing)
        + ". Old import paths must keep resolving until the shims are retired."
    )


def test_no_shim_outlives_its_target():
    """The reverse direction: a shim whose target is gone is a dangling import path."""
    shims_root = _deprecated_db_root()
    targets_root = _backends_root()

    dangling = []
    for path in sorted(shims_root.rglob("*.py")):
        rel = path.relative_to(shims_root)
        if rel.parts[0] not in _MOVED_PACKAGES:
            continue
        if not (targets_root / rel).exists():
            dangling.append(str(rel))

    assert not dangling, (
        "Shims whose target module no longer exists: "
        + ", ".join(dangling)
        + ". Delete the shim alongside the module it forwards to."
    )


@pytest.mark.parametrize("key", sorted(DB_BACKEND_CLASSES))
def test_every_registered_backend_resolves(key):
    """Each table row points at a class that actually exists, and the shim binds the same object.

    Resolution only, never construction: a backend whose inherited ``BaseDb.from_dict``
    would reject forwarded table kwargs must not produce a false failure here.
    """
    module_name, class_name = DB_BACKEND_CLASSES[key]
    try:
        module = importlib.import_module(module_name)
    except ImportError as e:
        pytest.skip(f"optional driver for '{key}' not installed: {e}")

    target_class = getattr(module, class_name, None)
    assert target_class is not None, f"{module_name} does not export {class_name}"

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        shim = importlib.import_module(module_name.replace("egpt.integrations.db.", "egpt.db.", 1))
    assert getattr(shim, class_name) is target_class, (
        f"egpt.db.{key}.{class_name} is not the same object as {module_name}.{class_name}; "
        "the shim has drifted from its target."
    )


def test_db_from_dict_does_not_touch_the_deprecated_shims():
    """Resolution must go straight to the integrations layer, importing no shim.

    Asserted on sys.modules rather than on a DeprecationWarning. The warning fires in a
    shim's module body, which Python executes only once per process, so an earlier test
    that imported the shim leaves the warning permanently spent and a
    ``simplefilter("error")`` guard here can never trip -- the check would report PASS
    against the exact regression it exists to catch. Purging the shim modules first makes
    both signals live again, and the sys.modules assertion holds even if the warning is
    later removed from the shims.
    """
    shim_modules = [f"egpt.db.{key}" for key in _MOVED_PACKAGES]
    saved = {name: mod for name, mod in sys.modules.items() if any(name.startswith(s) for s in shim_modules)}
    for name in saved:
        del sys.modules[name]

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            db = db_from_dict({"type": "sqlite", "db_file": ":memory:", "id": "guard"})

        leaked = sorted(name for name in sys.modules if any(name.startswith(s) for s in shim_modules))
        assert not leaked, (
            "db_from_dict imported deprecated shim modules: "
            + ", ".join(leaked)
            + ". Core must resolve through egpt.integrations.db.* directly."
        )
    finally:
        sys.modules.update(saved)

    assert db is not None
    assert type(db).__module__.startswith("egpt.integrations.db.sqlite")


def test_db_from_dict_returns_none_for_unknown_type():
    assert db_from_dict({"type": "definitely-not-a-backend"}) is None
    assert db_from_dict({}) is None


@pytest.mark.parametrize("key", sorted(set(DB_BACKEND_CLASSES) - set(RECONSTRUCTABLE_DB_CLASSES)))
def test_db_from_dict_rejects_backends_that_cannot_be_rebuilt(key, caplog):
    """A backend that cannot be rebuilt is reported as unknown, not dispatched to.

    Dispatching to a backend that inherits ``BaseDb.from_dict`` raises a TypeError naming
    a table-name keyword, which sends the operator debugging the wrong thing, and for
    ``in_memory`` it is worse than that: from_dict succeeds and the caller silently gets a
    live empty db instead of the None that makes it fall back to the AgentOS-level db.
    """
    caplog.clear()
    assert db_from_dict({"type": key, "id": "x"}) is None
    assert any("cannot be rebuilt from a stored config" in r.message for r in caplog.records), (
        f"expected a 'cannot be rebuilt' warning naming '{key}', got: " + "; ".join(r.message for r in caplog.records)
    )
    assert not any("Unknown database type" in r.message for r in caplog.records), (
        f"'{key}' is a supported backend, so reporting it as an unknown type misdirects the operator"
    )


def test_reconstructable_flag_is_symmetric_with_from_dict():
    """The reconstructable flag and reality must disagree only where a reason says so.

    ``test_reconstructable_backends_really_define_from_dict`` covers one direction, but it
    imports each class and so skips every backend whose optional driver is missing -- which
    on a base install is nine of thirteen. This is the AST counterpart: it needs no driver,
    so it never skips, and it closes the other direction too. A backend that gains a
    ``from_dict`` without anyone flipping its flag stays silently unreconstructable
    otherwise, which is the failure mode that motivated the flag in the first place.

    The asymmetry is allowed, but only as a decision: a row that defines ``from_dict`` and
    is still flagged False must carry ``reconstruct_exempt_reason``.
    """
    root = _backends_root()
    defines_from_dict = classes_defining(root, "from_dict")

    # Non-vacuity: without this a stale scan root compares two empty sets and reports PASS.
    assert len(defines_from_dict) >= 4, (
        f"Only {len(defines_from_dict)} classes defining from_dict found under {root}; the scan "
        "root is probably stale, which makes this guard vacuous."
    )

    flagged, unflagged = {}, {}
    for key, reg in DB_DECLARATIONS.items():
        target = next(iter(registered_defining_classes(root, [(reg.module, reg.attr)])))
        (flagged if reg.metadata.get("reconstructable") else unflagged)[key] = target

    lying = sorted(key for key, target in flagged.items() if target not in defines_from_dict)
    assert not lying, (
        f"Backends flagged reconstructable that never define from_dict: {lying}. They would "
        "inherit BaseDb.from_dict and fail on a forwarded table-name kwarg."
    )

    undeclared = sorted(
        key
        for key, target in unflagged.items()
        if target in defines_from_dict and not DB_DECLARATIONS[key].metadata.get("reconstruct_exempt_reason")
    )
    assert not undeclared, (
        f"Backends that define from_dict but are not flagged reconstructable: {undeclared}. "
        "Either flip the flag in egpt/registry/_declarations/dbs.py, or record why not in "
        "_RECONSTRUCT_EXEMPT_REASONS."
    )


@pytest.mark.parametrize("key", sorted(RECONSTRUCTABLE_DB_CLASSES))
def test_reconstructable_backends_really_define_from_dict(key):
    """The reconstructable flag must track reality, not intent.

    A backend flagged True that only inherits ``BaseDb.from_dict`` would forward all 18
    TABLE_SUFFIXES kwargs into an __init__ that does not accept them.
    """
    module_name, class_name = RECONSTRUCTABLE_DB_CLASSES[key]
    try:
        cls = getattr(importlib.import_module(module_name), class_name)
    except ImportError as e:
        pytest.skip(f"optional driver for '{key}' not installed: {e}")

    assert cls.from_dict.__func__ is not BaseDb.from_dict.__func__, (
        f"{class_name} is flagged reconstructable in _DB_BACKENDS but does not override "
        "from_dict, so db_from_dict would fail on a table-name kwarg."
    )


def test_clone_preserves_every_non_table_constructor_setting():
    """A clone must carry over all connection/behaviour state, not just the obvious fields.

    `create_schema` was dropped by both Postgres clones: it defaults to True, so a
    deployment that set it False because its role lacks CREATE privilege had schema DDL
    silently re-enabled the moment a config carried a table override. Enumerating the
    constructor rather than listing fields by hand is what makes this catch the next one.
    """
    from egpt.integrations.db.postgres import PostgresDb
    from egpt.integrations.db.sqlite import SqliteDb

    table_params = set(TABLE_SUFFIXES) | {"table_prefix"}

    # Constructed lazily and driver-gated: SQLAlchemy resolves the dialect's DBAPI inside
    # create_engine, so PostgresDb(db_url="postgresql+psycopg://...") raises
    # ModuleNotFoundError on an environment without psycopg. That is an absent optional
    # driver, not a failure of this guard -- every other test here skips on it, and this
    # one used to error instead. sqlite3 is stdlib, so the SqliteDb case always runs.
    factories = [
        lambda: PostgresDb(db_url="postgresql+psycopg://u:p@h/db", db_schema="custom", create_schema=False),
        lambda: SqliteDb(db_file=":memory:"),
    ]
    cases = []
    for factory in factories:
        try:
            cases.append(factory())
        except ImportError as e:
            warnings.warn(f"optional driver not installed, skipping one clone case: {e}", stacklevel=1)

    # Non-vacuity floor: the loop below must actually assert on something.
    assert cases, "no clone case could be constructed; this guard would pass vacuously"

    for source in cases:
        clone = source.clone_with_table_overrides(session_table="cloned_sessions")
        assert clone is not None
        assert clone.session_table_name == "cloned_sessions"

        carried = [
            name
            for name in inspect.signature(type(source).__init__).parameters
            if name not in table_params and name not in ("self", "db_engine") and hasattr(source, name)
        ]
        dropped = [name for name in carried if getattr(clone, name) != getattr(source, name)]
        assert not dropped, (
            f"{type(source).__name__}.clone_with_table_overrides dropped {dropped}; the clone "
            "must carry over every non-table constructor setting."
        )
        assert clone.db_engine is source.db_engine, "clone must share the engine, not open a new pool"


@pytest.mark.parametrize(
    "module_name,class_name,kwargs",
    [
        (
            "egpt.integrations.db.postgres.async_postgres",
            "AsyncPostgresDb",
            {"db_url": "postgresql+psycopg://u:p@h/db"},
        ),
        ("egpt.integrations.db.sqlite.async_sqlite", "AsyncSqliteDb", {"db_file": ":memory:"}),
    ],
)
def test_async_clone_refuses_exactly_the_overrides_its_constructor_lacks(module_name, class_name, kwargs):
    """The async clones must refuse exactly the table kwargs their __init__ lacks -- no more, no less.

    This was a hand-written frozenset of three component-table names in each file, with no
    test referencing either copy. Both drift directions are live and neither is loud:

    * refusing too little -- a new TABLE_SUFFIXES entry landing ahead of the constructors
      gets forwarded, and _clone_db_with_table_overrides turns the resulting TypeError into
      a logged error and a silent fallback, so the component keeps the registry instance's
      tables;
    * refusing too much -- once these classes gain the component-table parameters, a stale
      set keeps dropping overrides the constructor would now honour.

    Asserting both halves against the live signature is what makes the derived set
    load-bearing rather than decorative: checking only the refused half would pass just as
    happily against the old hardcoded frozenset.
    """
    try:
        cls = getattr(importlib.import_module(module_name), class_name)
        source = cls(**kwargs)
    except ImportError as e:
        pytest.skip(f"optional driver for '{class_name}' not installed: {e}")

    accepted = set(inspect.signature(cls.__init__).parameters)
    unsupported = set(TABLE_SUFFIXES) - accepted

    assert unsupported, (
        f"{class_name} now accepts every TABLE_SUFFIXES parameter; drop the refusal branch "
        "in clone_with_table_overrides rather than leaving a check that can never fire."
    )

    for param in sorted(TABLE_SUFFIXES):
        try:
            clone = source.clone_with_table_overrides(**{param: "cloned_table"})
        except Exception as e:  # noqa: BLE001 - a raising clone is itself the defect
            pytest.fail(
                f"{class_name}.clone_with_table_overrides({param}=...) raised {e!r} instead of "
                "cloning or returning None; the caller degrades that to the registry instance's "
                "own table names."
            )

        if param in unsupported:
            assert clone is None, (
                f"{class_name}.__init__ does not accept {param!r}, so a clone carrying it must "
                "return None rather than forward it into a TypeError."
            )
        else:
            assert clone is not None, (
                f"{class_name}.__init__ accepts {param!r}, so the clone must honour it rather "
                "than refusing -- a stale hand-listed refusal set silently drops the override."
            )
            assert getattr(clone, table_name_attr(param)) == "cloned_table"


def test_legacy_backend_attrs_all_resolve():
    """Every `egpt.db` legacy re-export must have a row in the backend table.

    `__getattr__` looks each name's key up in DB_BACKEND_CLASSES. If a key dropped out of
    `_DB_BACKENDS` the lookup would raise, and a KeyError escaping a module `__getattr__`
    breaks the module contract -- `hasattr(egpt.db, "MongoDb")` would blow up instead of
    answering False. The implementation now degrades to AttributeError; this asserts the
    degraded path is unreachable in the first place.
    """
    import egpt.db as db_pkg

    missing = sorted(set(db_pkg._LEGACY_BACKEND_ATTRS.values()) - set(DB_BACKEND_CLASSES))
    assert not missing, f"_LEGACY_BACKEND_ATTRS names backend keys absent from _DB_BACKENDS: {missing}"

    resolved = 0
    for name in db_pkg._LEGACY_BACKEND_ATTRS:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            try:
                assert getattr(db_pkg, name).__name__ == name
                resolved += 1
            except ImportError:
                pass  # optional driver absent; the table row is what this guard checks

    assert resolved, "no legacy re-export resolved; this guard would pass vacuously"

    # The contract this protects: an unknown name answers False rather than raising.
    assert not hasattr(db_pkg, "NoSuchDb")


def test_default_clone_with_table_overrides_returns_none():
    """A backend that does not override the hook inherits BaseDb's None.

    Asserted against a real instance rather than a Mock, so it stays honest if the mocks in
    test_resolve_db_from_config.py ever drift from the real default.
    """
    from egpt.integrations.db.in_memory import InMemoryDb

    assert InMemoryDb().clone_with_table_overrides(session_table="x") is None


def test_no_test_patches_a_deprecated_shim_path():
    """No test may aim mock.patch at an `egpt.db.<backend>` path.

    A submodule shim is `from <target> import *`, so patching a name on the shim rebinds
    the shim's own copy while the implementation keeps reading its module globals. The
    patch silently no-ops -- a patched `create_engine` would let the test open a real
    connection instead of failing loudly about a bad target. The shim docstrings record
    this, but prose does not stop anyone re-introducing it, so it gets a check.

    Scans the whole test tree, not just `tests/unit`. A shim patched from an integration or
    performance test no-ops exactly the same way, and scoping the scan to this guard's own
    neighbourhood would wave those through.
    """
    tests_root = package_root() / "tests"
    assert tests_root.is_dir(), f"test root not found: {tests_root}"
    # Non-vacuity floor. A scan root that stops resolving yields no offenders and reports
    # PASS, which is how a guard of this shape silently stops guarding anything.
    scanned = len(list(tests_root.rglob("test_*.py")))
    assert scanned >= 100, f"only {scanned} test files found under {tests_root}; the scan root is stale"

    offenders = find_shim_path_strings(
        tests_root,
        "egpt.db",
        _MOVED_PACKAGES,
        exclude={Path(__file__).resolve()},  # this file names the paths on purpose
    )

    assert not offenders, (
        "Tests referencing a deprecated shim path as a string (mock.patch targets patch the "
        "shim's binding, not the implementation):\n"
        + "\n".join(offenders)
        + "\nPoint them at egpt.integrations.db.* instead."
    )


def _iter_core_db_modules():
    """Core `egpt/db` modules: everything outside the shim packages."""
    root = _deprecated_db_root()
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if rel.parts[0] in _MOVED_PACKAGES:
            continue
        yield path


# Core modules still permitted to name a concrete backend, with the reason. These are
# cast()-only imports inside the v1->v2 migration: dispatch is already duck-typed on
# type(db).__name__ and the imports are typing sugar. Deleting them risks new mypy
# attr-defined errors, so they are recorded rather than removed.
_CORE_BACKEND_IMPORT_ALLOWLIST = {"egpt/db/migrations/v1_to_v2.py"}


def test_core_db_package_imports_no_backend():
    """Core must not import a concrete backend -- at module level or inside a function.

    Walks the full AST rather than checking the import block, because the coupling this
    replaced was function-local: `db_from_dict` imported PostgresDb inside an if/elif, and
    `_clone_db_with_table_overrides` imported it inside a try.
    """
    root = package_root()
    offenders = []
    for path in _iter_core_db_modules():
        rel = str(path.relative_to(root))
        if rel in _CORE_BACKEND_IMPORT_ALLOWLIST:
            continue
        for lineno, module in import_sites(path):
            if module.startswith("egpt.integrations"):
                offenders.append(f"{rel}:{lineno} imports {module}")
            elif module.startswith("egpt.db."):
                tail = module[len("egpt.db.") :].split(".")[0]
                if tail in _MOVED_PACKAGES:
                    offenders.append(f"{rel}:{lineno} imports {module}")

    assert not offenders, (
        "Core egpt/db modules import a concrete backend, which re-couples core to the "
        "integrations layer:\n" + "\n".join(offenders)
    )
