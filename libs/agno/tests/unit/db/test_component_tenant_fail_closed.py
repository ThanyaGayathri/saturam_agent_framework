"""Fail-closed tenant scoping on the component data layer (EGPT-2.7).

Three distinct guarantees, because each fails in a different way and none of them implies the
others:

1. **Omission is impossible.** ``tenant_id`` is keyword-only with no default on every component
   method of every backend, so a caller that forgets it gets a ``TypeError`` at the call and a
   mypy ``call-arg`` error before that. This is what closes the original hole -- a forgotten
   scope used to be a silent, successful, cross-tenant read.
2. **Explicit ``None`` is refused when the deployment scopes by tenant.** ``tenant_id=None``
   stays legal by default (single-tenant deployments, and the NULL-tenant rows written before
   multi-tenancy), and raises ``TenantScopeRequiredError`` on a db built with
   ``require_tenant_id``.
3. **The guard is actually wired into every method.** Asserted against each backend's AST rather
   than by calling through, because a read method that silently returned nothing would pass a
   behavioural test while leaving a mutation unguarded.

Guarantees 1 and 3 are structural assertions on the source, which is deliberate: they are what
stop a *future* backend or method from reintroducing the fail-open default, and no amount of
behavioural testing of today's fourteen methods does that. This is the same reasoning behind
``_strip_tenant_scoping`` in ``test_component_tenant_scoping.py`` -- pin the mechanism, not only
its current effect.
"""

import ast
import inspect
import os
import tempfile
from pathlib import Path

import pytest

from egpt.db.base import BaseDb, ComponentType
from egpt.db.component_scope import (
    COMPONENT_DATA_ACCESS_METHODS,
    TENANT_SCOPED_COMPONENT_TABLES,
    TenantScopeRequiredError,
    resolve_tenant_scope,
)

TENANT_A = "tenant-a"


# ============================================================================
# FIXTURES / HELPERS
# ============================================================================


@pytest.fixture(autouse=True)
def _no_ambient_strict_mode(monkeypatch):
    """Clear ``EGPT_REQUIRE_TENANT_ID`` for every test in this module.

    ``BaseDb`` reads that variable at construction, so a developer or CI job exporting it would
    change what these tests exercise -- the assertions pinning the default as *off* would fail, and
    the ones pinning strict behaviour would pass for the wrong reason. The tests that want strict
    mode set it themselves, after this has run.
    """
    monkeypatch.delenv("EGPT_REQUIRE_TENANT_ID", raising=False)
    monkeypatch.delenv("AGNO_REQUIRE_TENANT_ID", raising=False)


@pytest.fixture
def sqlite_db():
    from egpt.integrations.db.sqlite.sqlite import SqliteDb

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = SqliteDb(db_file=path)
    yield db
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def strict_db():
    """A db that refuses unscoped component access."""
    from egpt.integrations.db.sqlite.sqlite import SqliteDb

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = SqliteDb(db_file=path, require_tenant_id=True)
    yield db
    try:
        os.unlink(path)
    except OSError:
        pass


# The minimal arguments each method needs besides ``tenant_id``. Enough to reach the guard --
# which runs before any lookup, so none of these components has to exist.
METHOD_CALLS = {
    "get_component": {"component_id": "c1"},
    "upsert_component": {"component_id": "c1", "component_type": ComponentType.AGENT},
    "delete_component": {"component_id": "c1"},
    "list_components": {},
    "create_component_with_config": {
        "component_id": "c1",
        "component_type": ComponentType.AGENT,
        "name": "c1",
        "config": {"model": "gpt-5.5"},
    },
    "get_config": {"component_id": "c1"},
    "upsert_config": {"component_id": "c1", "config": {"model": "gpt-5.5"}},
    "delete_config": {"component_id": "c1", "version": 1},
    "list_configs": {"component_id": "c1"},
    "set_current_version": {"component_id": "c1", "version": 1},
    "list_component_audit": {"component_id": "c1"},
    "get_links": {"component_id": "c1", "version": 1},
    "get_dependents": {"component_id": "c1"},
    "load_component_graph": {"component_id": "c1"},
}

BACKEND_SOURCES = {
    "PostgresDb": "egpt/integrations/db/postgres/postgres.py",
    "SqliteDb": "egpt/integrations/db/sqlite/sqlite.py",
}

_REPO_LIBS = Path(__file__).resolve().parents[3]


def _backend_classes():
    """Every backend class that declares component methods, implemented or not."""
    from egpt.integrations.db.clickhouse.clickhouse import ClickhouseDb
    from egpt.integrations.db.postgres.async_postgres import AsyncPostgresDb
    from egpt.integrations.db.postgres.postgres import PostgresDb
    from egpt.integrations.db.sqlite.async_sqlite import AsyncSqliteDb
    from egpt.integrations.db.sqlite.sqlite import SqliteDb

    return {
        "BaseDb": BaseDb,
        "PostgresDb": PostgresDb,
        "SqliteDb": SqliteDb,
        "AsyncPostgresDb": AsyncPostgresDb,
        "AsyncSqliteDb": AsyncSqliteDb,
        "ClickhouseDb": ClickhouseDb,
    }


def _component_functiondefs(source_path):
    """Map method name -> ast.FunctionDef for the component methods in a backend source file."""
    tree = ast.parse((_REPO_LIBS / source_path).read_text())
    found = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for fn in node.body:
            if isinstance(fn, ast.FunctionDef) and fn.name in COMPONENT_DATA_ACCESS_METHODS:
                found[fn.name] = fn
    return found


def _touches_tenant_scoped_table(fn):
    """Whether a method body reads or writes rows of a tenant-scoped component table.

    Two conditions, both required:

    1. It resolves one of the component tables, via the backends' single idiom for reaching a
       table (``self._get_table(table_type="components")``).
    2. It executes a statement.

    Detecting by behaviour rather than by name is the whole point -- a name filter is an
    allowlist in disguise and is what let ``resolve_version`` through. The second condition is
    what separates row access from table plumbing: ``_get_table`` itself and
    ``_require_component_audit_table`` resolve component tables but only ever return the
    ``Table`` object, so they have no rows to scope and nothing to guard.
    """
    resolves_table = False
    executes = False
    for node in ast.walk(fn):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr == "execute":
            executes = True
        elif node.func.attr == "_get_table":
            for kw in node.keywords:
                if (
                    kw.arg == "table_type"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value in TENANT_SCOPED_COMPONENT_TABLES
                ):
                    resolves_table = True
    return resolves_table and executes


def _has_tenant_guard(fn):
    """Whether the guard is the method's first statement, after any docstring."""
    body = list(fn.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    if not body:
        return False
    stmt = body[0]
    return (
        isinstance(stmt, ast.Assign)
        and isinstance(stmt.value, ast.Call)
        and isinstance(stmt.value.func, ast.Attribute)
        and stmt.value.func.attr == "_require_tenant_scope"
        and isinstance(stmt.targets[0], ast.Name)
        and stmt.targets[0].id == "tenant_id"
    )


# ============================================================================
# 1. OMISSION IS IMPOSSIBLE
# ============================================================================


class TestTenantIdIsRequired:
    """``tenant_id`` is keyword-only with no default, on every backend."""

    @pytest.mark.parametrize("class_name", sorted(_backend_classes()))
    @pytest.mark.parametrize("method_name", COMPONENT_DATA_ACCESS_METHODS)
    def test_tenant_id_is_keyword_only_without_a_default(self, class_name, method_name):
        cls = _backend_classes()[class_name]
        method = getattr(cls, method_name, None)
        if method is None:
            # A backend may legitimately not declare a method at all -- it then inherits BaseDb's,
            # which this same test already pins.
            pytest.skip(f"{class_name} does not declare {method_name}")

        param = inspect.signature(method).parameters.get("tenant_id")
        assert param is not None, f"{class_name}.{method_name} has no tenant_id parameter"
        assert param.kind is inspect.Parameter.KEYWORD_ONLY, (
            f"{class_name}.{method_name}: tenant_id must be keyword-only so it cannot be "
            f"passed positionally by accident, got {param.kind}"
        )
        assert param.default is inspect.Parameter.empty, (
            f"{class_name}.{method_name}: tenant_id must have no default. A default makes a "
            f"forgotten scope a silent cross-tenant call, which is the hole EGPT-2.7 closes."
        )

    @pytest.mark.parametrize("method_name", COMPONENT_DATA_ACCESS_METHODS)
    def test_omitting_tenant_id_raises_type_error(self, sqlite_db, method_name):
        kwargs = METHOD_CALLS[method_name]
        with pytest.raises(TypeError, match="tenant_id"):
            getattr(sqlite_db, method_name)(**kwargs)

    @pytest.mark.parametrize("class_name", sorted(BACKEND_SOURCES))
    def test_every_method_touching_a_tenant_scoped_table_is_guarded(self, class_name):
        """Enumerate by what a method *touches*, not by what it is *called*.

        This is the test that has to be right, because it is the one that decides which methods
        the rest of this file checks. Two earlier framings were both wrong:

        - Enumerating from ``vars(BaseDb)`` cannot see a method defined only on a backend class.
          ``SqliteDb.resolve_version`` was exactly that -- a public, positionally-callable,
          unguarded read of the ``components`` table sitting between two guarded siblings,
          invisible to a ``BaseDb``-derived list.
        - Filtering by name (``"component" | "config" | "link" | "version"``) is an allowlist
          wearing a heuristic's clothes. It does not even match ``get_dependents``, one of the
          fourteen, which survived only because the list was hand-typed. A future
          ``probe_ownership`` would escape silently.

        So: walk every backend class in the source, find each method that resolves a
        tenant-scoped table, and require the guard. That property is the one actually wanted,
        and it needs no list kept in sync -- a new method is covered the moment it is written.
        """
        tree = ast.parse((_REPO_LIBS / BACKEND_SOURCES[class_name]).read_text())
        unguarded = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for fn in node.body:
                if not isinstance(fn, ast.FunctionDef):
                    continue
                if not _touches_tenant_scoped_table(fn):
                    continue
                if not _has_tenant_guard(fn):
                    unguarded.append(f"{node.name}.{fn.name} (line {fn.lineno})")

        assert not unguarded, (
            "these methods resolve a tenant-scoped component table but do not call "
            "self._require_tenant_scope() as their first statement, so they read or write "
            "component rows with no tenant predicate on a strict db:\n  " + "\n  ".join(unguarded)
        )

    def test_the_declared_method_list_is_complete(self):
        """``COMPONENT_DATA_ACCESS_METHODS`` covers every guarded public method.

        The list drives the signature and behavioural tests above. The AST scan is what finds
        methods; this pins that the hand-maintained list has not fallen behind it, for the public
        API surface. Private helpers (``_resolve_version``) are guarded but are not part of the
        documented fourteen, so they are excluded here and covered by the scan.
        """
        found = set()
        for source in BACKEND_SOURCES.values():
            tree = ast.parse((_REPO_LIBS / source).read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                for fn in node.body:
                    if isinstance(fn, ast.FunctionDef) and not fn.name.startswith("_") and _has_tenant_guard(fn):
                        found.add(fn.name)

        missing = found - set(COMPONENT_DATA_ACCESS_METHODS)
        assert not missing, f"guarded public methods missing from COMPONENT_DATA_ACCESS_METHODS: {sorted(missing)}"


# ============================================================================
# 2. EXPLICIT None IS REFUSED UNDER require_tenant_id
# ============================================================================


class TestUnscopedAccessIsRefusedWhenRequired:
    @pytest.mark.parametrize("method_name", COMPONENT_DATA_ACCESS_METHODS)
    def test_explicit_none_raises_on_a_strict_db(self, strict_db, method_name):
        with pytest.raises(TenantScopeRequiredError, match=method_name):
            getattr(strict_db, method_name)(tenant_id=None, **METHOD_CALLS[method_name])

    @pytest.mark.parametrize("method_name", COMPONENT_DATA_ACCESS_METHODS)
    def test_explicit_none_is_allowed_by_default(self, sqlite_db, method_name):
        """Unscoped access stays legal on a db that has not opted in.

        This is the compatibility guarantee: single-tenant deployments pass ``None`` because they
        have no tenant, and it is the only way to reach pre-multi-tenancy NULL rows. Some of
        these raise ``ValueError`` for unrelated reasons (no such component); what matters is
        that none raises ``TenantScopeRequiredError``.
        """
        try:
            getattr(sqlite_db, method_name)(tenant_id=None, **METHOD_CALLS[method_name])
        except TenantScopeRequiredError:  # pragma: no cover - the assertion this test exists for
            pytest.fail(f"{method_name} refused unscoped access on a non-strict db")
        except ValueError:
            pass

    def test_strict_db_still_serves_scoped_calls(self, strict_db):
        """The flag refuses *unscoped* access, not all access."""
        strict_db.create_component_with_config(
            component_id="c1",
            component_type=ComponentType.AGENT,
            name="c1",
            config={"model": "gpt-5.5"},
            tenant_id=TENANT_A,
        )
        assert strict_db.get_component("c1", tenant_id=TENANT_A) is not None
        rows, total = strict_db.list_components(tenant_id=TENANT_A)
        assert total == 1

    def test_null_tenant_rows_are_unreachable_under_strict(self, sqlite_db):
        """The deliberate consequence, pinned so it cannot regress by accident.

        A pre-multi-tenancy row has a NULL tenant and is reachable only with ``tenant_id=None``.
        Under ``require_tenant_id`` that call is refused, so the row becomes unreachable -- which
        is correct, because an unscoped row in a tenanted deployment belongs to no tenant and is
        visible to all of them. ``docs/tenancy.md`` carries the backfill SQL.
        """
        from egpt.integrations.db.sqlite.sqlite import SqliteDb

        sqlite_db.create_component_with_config(
            component_id="legacy",
            component_type=ComponentType.AGENT,
            name="legacy",
            config={"model": "gpt-5.5"},
            tenant_id=None,
        )
        assert sqlite_db.get_component("legacy", tenant_id=None) is not None

        strict = SqliteDb(db_file=sqlite_db.db_file, require_tenant_id=True)
        with pytest.raises(TenantScopeRequiredError):
            strict.get_component("legacy", tenant_id=None)
        assert strict.get_component("legacy", tenant_id=TENANT_A) is None

    def test_env_var_enables_strict_mode(self, monkeypatch):
        from egpt.integrations.db.sqlite.sqlite import SqliteDb

        monkeypatch.setenv("EGPT_REQUIRE_TENANT_ID", "true")
        assert SqliteDb(db_file=":memory:").require_tenant_id is True

    @pytest.mark.parametrize("value", ["1", "yes", "TRUE", "on", "y", "t", "  true  "])
    def test_env_var_accepts_the_common_truthy_spellings(self, monkeypatch, value):
        """``1``, ``yes``, ``on`` and friends must all work.

        Accepting only the literal ``true`` would leave an operator who set
        ``EGPT_REQUIRE_TENANT_ID=1`` believing a security control was on while it was off.
        """
        from egpt.integrations.db.sqlite.sqlite import SqliteDb

        monkeypatch.setenv("EGPT_REQUIRE_TENANT_ID", value)
        assert SqliteDb(db_file=":memory:").require_tenant_id is True

    @pytest.mark.parametrize("value", ["0", "no", "off", "FALSE", "n", ""])
    def test_env_var_accepts_the_common_falsy_spellings(self, monkeypatch, value):
        from egpt.integrations.db.sqlite.sqlite import SqliteDb

        monkeypatch.setenv("EGPT_REQUIRE_TENANT_ID", value)
        assert SqliteDb(db_file=":memory:").require_tenant_id is False

    @pytest.mark.parametrize("value", ["enabled", "ture", "maybe", "2", "please"])
    def test_an_unrecognised_env_value_refuses_to_start(self, monkeypatch, value):
        """A typo'd security flag must fail to boot, not fail open.

        The dangerous behaviour is the ordinary one: a general-purpose boolean reader maps
        anything it does not recognise to ``False``, so ``EGPT_REQUIRE_TENANT_ID=enabled`` would
        leave enforcement off silently, on a deployment whose operator believes they turned it
        on. Raising is recoverable in seconds; running unprotected is never noticed.
        """
        from egpt.integrations.db.sqlite.sqlite import SqliteDb

        monkeypatch.setenv("EGPT_REQUIRE_TENANT_ID", value)
        with pytest.raises(ValueError, match="not a recognised boolean"):
            SqliteDb(db_file=":memory:")

    def test_explicit_ctor_arg_does_not_downgrade_the_env(self, monkeypatch):
        """``require_tenant_id=False`` must not switch off what the environment turned on.

        The flag is only ever OR'd on. Letting a caller's default argument silently relax a
        deployment-wide security control is the direction that loses isolation.
        """
        from egpt.integrations.db.sqlite.sqlite import SqliteDb

        monkeypatch.setenv("EGPT_REQUIRE_TENANT_ID", "true")
        assert SqliteDb(db_file=":memory:", require_tenant_id=False).require_tenant_id is True

    def test_default_is_off(self, sqlite_db):
        assert sqlite_db.require_tenant_id is False


# ============================================================================
# 3. THE GUARD IS WIRED INTO EVERY METHOD
# ============================================================================


class TestGuardIsWiredIn:
    """Structural assertions on the backends that actually implement components."""

    @pytest.mark.parametrize("class_name", sorted(BACKEND_SOURCES))
    @pytest.mark.parametrize("method_name", COMPONENT_DATA_ACCESS_METHODS)
    def test_guard_is_the_first_statement(self, class_name, method_name):
        fn = _component_functiondefs(BACKEND_SOURCES[class_name]).get(method_name)
        assert fn is not None, f"{class_name} does not implement {method_name}"

        body = list(fn.body)
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            body = body[1:]  # skip the docstring
        assert body, f"{class_name}.{method_name} has an empty body"

        stmt = body[0]
        assert (
            isinstance(stmt, ast.Assign)
            and isinstance(stmt.value, ast.Call)
            and isinstance(stmt.value.func, ast.Attribute)
            and stmt.value.func.attr == "_require_tenant_scope"
            and isinstance(stmt.targets[0], ast.Name)
            and stmt.targets[0].id == "tenant_id"
        ), (
            f"{class_name}.{method_name}: the first statement must be "
            f"`tenant_id = self._require_tenant_scope(tenant_id, ...)`. It has to run before the "
            f"method's own try/except, or a scope refusal gets logged as a database error."
        )
        assert stmt.value.args[1].value == method_name, (
            f"{class_name}.{method_name} passes the wrong method name to the guard, which would "
            f"misattribute the error message"
        )


# ============================================================================
# 4. THE PRIMITIVE ITSELF
# ============================================================================


class TestResolveTenantScope:
    def test_passes_a_real_tenant_through_unchanged(self):
        assert resolve_tenant_scope(TENANT_A, require=True, method="m") == TENANT_A
        assert resolve_tenant_scope(TENANT_A, require=False, method="m") == TENANT_A

    def test_none_is_allowed_when_not_required(self):
        assert resolve_tenant_scope(None, require=False, method="m") is None

    def test_none_raises_when_required(self):
        with pytest.raises(TenantScopeRequiredError, match="unscoped"):
            resolve_tenant_scope(None, require=True, method="m")

    @pytest.mark.parametrize("value", ["", " ", "\t"])
    @pytest.mark.parametrize("require", [True, False])
    def test_blank_tenant_always_raises(self, value, require):
        """A blank tenant is refused regardless of the flag.

        It is never a legitimate value -- the middleware coerces one to ``None`` -- and on a
        write it would stamp rows into a tenant no caller can name again.
        """
        with pytest.raises(TenantScopeRequiredError, match="empty"):
            resolve_tenant_scope(value, require=require, method="m")

    @pytest.mark.parametrize("value", ["acme ", " acme", "  acme\t", "\nacme\n"])
    def test_surrounding_whitespace_is_stripped(self, value):
        """Returning the raw string would create a tenant nobody can name again.

        ``"acme "`` and ``"acme"`` are different values to a SQL predicate, so an unstripped
        write lands rows in a namespace no properly-trimmed caller can reach -- the same
        consequence the blank-tenant check above exists to prevent, by a subtler route. The HTTP
        path is safe (the JWT middleware strips); this reaches the non-HTTP callers, where the
        tenant comes from a script argument, a config file or an environment variable.
        """
        assert resolve_tenant_scope(value, require=True, method="m") == "acme"

    def test_a_padded_tenant_reaches_the_same_rows_as_the_trimmed_one(self, sqlite_db):
        """The end-to-end consequence of the strip, through a real write and read."""
        sqlite_db.create_component_with_config(
            component_id="c1",
            component_type=ComponentType.AGENT,
            name="c1",
            config={"model": "gpt-5.5"},
            tenant_id="acme ",
        )
        assert sqlite_db.get_component("c1", tenant_id="acme") is not None
        assert sqlite_db.get_component("c1", tenant_id=" acme") is not None

    def test_error_names_the_method(self):
        with pytest.raises(TenantScopeRequiredError, match="get_component"):
            resolve_tenant_scope(None, require=True, method="get_component")

    def test_is_not_a_value_error(self):
        """The components router maps ``ValueError`` to a 400; this is not a client error."""
        assert not issubclass(TenantScopeRequiredError, ValueError)


# ============================================================================
# 5. tenant_id AS A RECOGNIZED COLUMN
# ============================================================================


class TestTenantColumnDeclaration:
    def test_migration_table_list_agrees_with_the_scope_declaration(self):
        """The v2_6_0 migration's narrower list must differ only by the audit table.

        The migration deliberately keeps its own tuple rather than importing
        ``TENANT_SCOPED_COMPONENT_TABLES``: it predates ``component_audit``, which was born with
        a ``tenant_id`` column and must not be re-altered. A historical migration that imported
        a live constant would retroactively try to alter whatever got added to it later. This
        pins the relationship that the import would otherwise have expressed.
        """
        from egpt.db.migrations.versions.v2_6_0 import COMPONENT_TABLE_TYPES

        assert set(COMPONENT_TABLE_TYPES) | {"component_audit"} == set(TENANT_SCOPED_COMPONENT_TABLES)
