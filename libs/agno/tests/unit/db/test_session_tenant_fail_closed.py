"""Fail-closed tenant scoping on the session/run data layer (aicoe-tracker#197).

Mirrors ``test_component_tenant_fail_closed.py``'s structure, with the deliberate differences
documented in ``egpt/db/session_scope.py``:

1. ``tenant_id`` is keyword-only, but **has a default of ``None``** on every session method of
   every backend -- unlike the fourteen component methods, which require it. A forgotten
   argument is therefore not a ``TypeError``; see ``egpt/db/session_scope.py`` for why the
   session hot path cannot afford the component methods' required-parameter guarantee.
2. Explicit ``None`` (or an omitted argument, since Python cannot tell them apart for an
   optional parameter) is refused when the db was built with ``require_session_tenant_id`` --
   a **separate flag from ``require_tenant_id``**, not shared with it. The most important
   guarantee this file pins is that the two flags are independent: a db with
   ``require_tenant_id=True`` (component enforcement, which ``EgptOS`` turns on automatically
   for any tenancy-configured deployment) must not refuse an unscoped session call, or every
   agent/team/workflow run in such a deployment would break the moment it tries to save its own
   session -- see the module docstring in ``egpt/db/session_scope.py`` for the full reasoning.
3. The guard (``self._require_session_tenant_scope``) is wired in as the first statement of
   every session method, asserted against each backend's AST for the same reason the component
   suite does: a read method that silently returned nothing would pass a behavioural test while
   leaving a mutation unguarded.
"""

import ast
import inspect
import os
import tempfile
from pathlib import Path

import pytest

from egpt.db.base import BaseDb
from egpt.db.component_scope import TenantScopeRequiredError, resolve_tenant_scope
from egpt.db.session_scope import SESSION_DATA_ACCESS_METHODS, TENANT_SCOPED_SESSION_TABLES
from egpt.session.agent import AgentSession

TENANT_A = "tenant-a"


# ============================================================================
# FIXTURES / HELPERS
# ============================================================================


@pytest.fixture(autouse=True)
def _no_ambient_strict_mode(monkeypatch):
    """Clear both tenant-enforcement env vars for every test in this module.

    Mirrors the component suite's identical fixture -- a developer or CI job exporting either
    variable would silently change what these tests exercise.
    """
    monkeypatch.delenv("EGPT_REQUIRE_TENANT_ID", raising=False)
    monkeypatch.delenv("AGNO_REQUIRE_TENANT_ID", raising=False)
    monkeypatch.delenv("EGPT_REQUIRE_SESSION_TENANT_ID", raising=False)
    monkeypatch.delenv("AGNO_REQUIRE_SESSION_TENANT_ID", raising=False)


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
def strict_session_db():
    """A db that refuses unscoped *session* access, but not component access."""
    from egpt.integrations.db.sqlite.sqlite import SqliteDb

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = SqliteDb(db_file=path, require_session_tenant_id=True)
    yield db
    try:
        os.unlink(path)
    except OSError:
        pass


def _agent_session(session_id="s1", user_id="u1"):
    return AgentSession(session_id=session_id, agent_id="a1", user_id=user_id, created_at=1)


# The minimal arguments each method needs besides ``tenant_id``. Enough to reach the guard --
# which runs before any lookup, so none of these sessions has to exist for the read/delete/rename
# methods. ``upsert_session``/``upsert_sessions`` need a real ``Session`` object.
METHOD_CALLS = {
    "get_session": {"session_id": "s1"},
    "get_sessions": {},
    "upsert_session": {"session": _agent_session()},
    "upsert_sessions": {"sessions": [_agent_session()]},
    "rename_session": {"session_id": "s1", "session_type": None, "session_name": "new name"},
    "delete_session": {"session_id": "s1"},
    "delete_sessions": {"session_ids": ["s1"]},
}

BACKEND_SOURCES = {
    "PostgresDb": "egpt/integrations/db/postgres/postgres.py",
    "SqliteDb": "egpt/integrations/db/sqlite/sqlite.py",
}

_REPO_LIBS = Path(__file__).resolve().parents[3]


def _backend_classes():
    """Backend classes checked by signature.

    Deliberately the same set `test_component_tenant_fail_closed.py` uses (minus the async and
    ClickHouse entries, which have no bearing on the session guard): importing `MySQLDb`/
    `SingleStoreDb` pulls in SQLAlchemy's MySQL dialect machinery, which is not guaranteed safe
    under the `unit-drivers-absent` CI job the way `Postgres`/`Sqlite` are. The AST-based checks
    below (`TestGuardIsWiredIn`) cover Postgres/SQLite directly from source text without
    importing anything, which is where the real wiring guarantee comes from regardless.
    """
    from egpt.integrations.db.postgres.postgres import PostgresDb
    from egpt.integrations.db.sqlite.sqlite import SqliteDb

    return {
        "BaseDb": BaseDb,
        "PostgresDb": PostgresDb,
        "SqliteDb": SqliteDb,
    }


def _session_functiondefs(source_path):
    """Map method name -> ast.FunctionDef for the session methods in a backend source file."""
    tree = ast.parse((_REPO_LIBS / source_path).read_text())
    found = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for fn in node.body:
            if isinstance(fn, ast.FunctionDef) and fn.name in SESSION_DATA_ACCESS_METHODS:
                found[fn.name] = fn
    return found


def _has_session_tenant_guard(fn):
    """Whether the session guard is the method's first statement, after any docstring."""
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
        and stmt.value.func.attr == "_require_session_tenant_scope"
        and isinstance(stmt.targets[0], ast.Name)
        and stmt.targets[0].id == "tenant_id"
    )


# ============================================================================
# 1. THE SEPARATION FROM COMPONENT ENFORCEMENT (the property this file most needs to pin)
# ============================================================================


class TestSessionEnforcementIsIndependentOfComponentEnforcement:
    """The regression this suite exists to prevent: sharing one flag between the two.

    ``EgptOS`` turns ``require_tenant_id`` on automatically for every registered db as soon as
    an operator configures a tenant claim. If session methods checked that same flag, every
    agent/team/workflow run in such a deployment would start raising ``TenantScopeRequiredError``
    on its own (currently unscoped) session save -- not just the read endpoints aicoe-tracker#197
    is about. See ``egpt/db/session_scope.py`` for the full reasoning.
    """

    def test_component_strict_mode_does_not_gate_session_access(self, sqlite_db):
        sqlite_db.require_tenant_id = True
        assert sqlite_db.require_session_tenant_id is False

        # An unscoped session write and read must both still work.
        saved = sqlite_db.upsert_session(_agent_session(session_id="s-unscoped"))
        assert saved is not None
        assert sqlite_db.get_session("s-unscoped") is not None

    def test_session_strict_mode_does_not_gate_component_access(self, strict_session_db):
        assert strict_session_db.require_tenant_id is False
        assert strict_session_db.require_session_tenant_id is True

        # An unscoped component call must still work (no TenantScopeRequiredError).
        assert strict_session_db.get_component("does-not-exist", tenant_id=None) is None

    def test_the_two_flags_are_distinct_attributes(self, sqlite_db):
        assert hasattr(sqlite_db, "require_tenant_id")
        assert hasattr(sqlite_db, "require_session_tenant_id")
        sqlite_db.require_tenant_id = True
        assert sqlite_db.require_session_tenant_id is False
        sqlite_db.require_session_tenant_id = True
        # Setting one after the other must not cross-contaminate.
        assert sqlite_db.require_tenant_id is True


# ============================================================================
# 2. tenant_id IS OPTIONAL, NOT REQUIRED (the deliberate divergence from components)
# ============================================================================


class TestTenantIdIsOptional:
    """``tenant_id`` is keyword-only but has a default of ``None`` on every backend."""

    @pytest.mark.parametrize("class_name", sorted(_backend_classes()))
    @pytest.mark.parametrize("method_name", SESSION_DATA_ACCESS_METHODS)
    def test_tenant_id_is_keyword_only_with_a_default(self, class_name, method_name):
        cls = _backend_classes()[class_name]
        method = getattr(cls, method_name, None)
        if method is None:
            pytest.skip(f"{class_name} does not declare {method_name}")

        param = inspect.signature(method).parameters.get("tenant_id")
        assert param is not None, f"{class_name}.{method_name} has no tenant_id parameter"
        assert param.kind is inspect.Parameter.KEYWORD_ONLY, (
            f"{class_name}.{method_name}: tenant_id must be keyword-only, got {param.kind}"
        )
        assert param.default is None, (
            f"{class_name}.{method_name}: tenant_id must default to None -- unlike the "
            f"component methods, omitting it must not raise. See egpt/db/session_scope.py."
        )

    @pytest.mark.parametrize("method_name", SESSION_DATA_ACCESS_METHODS)
    def test_omitting_tenant_id_does_not_raise(self, sqlite_db, method_name):
        """The deliberate opposite of the component suite's identically-named-in-spirit test."""
        try:
            getattr(sqlite_db, method_name)(**METHOD_CALLS[method_name])
        except TypeError as e:
            if "tenant_id" in str(e):
                pytest.fail(f"{method_name} raised on a merely-omitted tenant_id: {e}")
            raise


# ============================================================================
# 3. EXPLICIT/OMITTED tenant_id IS REFUSED UNDER require_session_tenant_id
# ============================================================================


class TestUnscopedAccessIsRefusedWhenRequired:
    @pytest.mark.parametrize("method_name", SESSION_DATA_ACCESS_METHODS)
    def test_omitted_tenant_id_raises_on_a_strict_session_db(self, strict_session_db, method_name):
        with pytest.raises(TenantScopeRequiredError, match=method_name):
            getattr(strict_session_db, method_name)(**METHOD_CALLS[method_name])

    @pytest.mark.parametrize("method_name", SESSION_DATA_ACCESS_METHODS)
    def test_explicit_none_raises_on_a_strict_session_db(self, strict_session_db, method_name):
        with pytest.raises(TenantScopeRequiredError, match=method_name):
            getattr(strict_session_db, method_name)(tenant_id=None, **METHOD_CALLS[method_name])

    def test_strict_session_db_still_serves_scoped_calls(self, strict_session_db):
        saved = strict_session_db.upsert_session(_agent_session(session_id="s-scoped"), tenant_id=TENANT_A)
        assert saved is not None
        assert strict_session_db.get_session("s-scoped", tenant_id=TENANT_A) is not None

    def test_null_tenant_rows_are_unreachable_under_strict(self, sqlite_db):
        """Same deliberate consequence as the component suite's identical test."""
        from egpt.integrations.db.sqlite.sqlite import SqliteDb

        sqlite_db.upsert_session(_agent_session(session_id="legacy"), tenant_id=None)
        assert sqlite_db.get_session("legacy", tenant_id=None) is not None

        strict = SqliteDb(db_file=sqlite_db.db_file, require_session_tenant_id=True)
        with pytest.raises(TenantScopeRequiredError):
            strict.get_session("legacy", tenant_id=None)
        assert strict.get_session("legacy", tenant_id=TENANT_A) is None

    def test_env_var_enables_strict_session_mode_without_affecting_components(self, monkeypatch):
        from egpt.integrations.db.sqlite.sqlite import SqliteDb

        monkeypatch.setenv("EGPT_REQUIRE_SESSION_TENANT_ID", "true")
        db = SqliteDb(db_file=":memory:")
        assert db.require_session_tenant_id is True
        assert db.require_tenant_id is False

    @pytest.mark.parametrize("value", ["enabled", "ture", "maybe", "2", "please"])
    def test_an_unrecognised_env_value_refuses_to_start(self, monkeypatch, value):
        from egpt.integrations.db.sqlite.sqlite import SqliteDb

        monkeypatch.setenv("EGPT_REQUIRE_SESSION_TENANT_ID", value)
        with pytest.raises(ValueError, match="not a recognised boolean"):
            SqliteDb(db_file=":memory:")

    def test_explicit_ctor_arg_does_not_downgrade_the_env(self, monkeypatch):
        from egpt.integrations.db.sqlite.sqlite import SqliteDb

        monkeypatch.setenv("EGPT_REQUIRE_SESSION_TENANT_ID", "true")
        assert SqliteDb(db_file=":memory:", require_session_tenant_id=False).require_session_tenant_id is True

    def test_default_is_off(self, sqlite_db):
        assert sqlite_db.require_session_tenant_id is False


# ============================================================================
# 4. THE GUARD IS WIRED INTO EVERY METHOD
# ============================================================================


class TestGuardIsWiredIn:
    """Structural assertions on the backends that actually implement sessions."""

    @pytest.mark.parametrize("class_name", sorted(BACKEND_SOURCES))
    @pytest.mark.parametrize("method_name", SESSION_DATA_ACCESS_METHODS)
    def test_guard_is_the_first_statement(self, class_name, method_name):
        fn = _session_functiondefs(BACKEND_SOURCES[class_name]).get(method_name)
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
            and stmt.value.func.attr == "_require_session_tenant_scope"
            and isinstance(stmt.targets[0], ast.Name)
            and stmt.targets[0].id == "tenant_id"
        ), (
            f"{class_name}.{method_name}: the first statement must be "
            f"`tenant_id = self._require_session_tenant_scope(tenant_id, ...)`. It has to run "
            f"before the method's own try/except, or a scope refusal gets logged as a database "
            f"error."
        )
        assert stmt.value.args[1].value == method_name, (
            f"{class_name}.{method_name} passes the wrong method name to the guard, which would "
            f"misattribute the error message"
        )

    @pytest.mark.parametrize("class_name", sorted(BACKEND_SOURCES))
    def test_no_session_method_calls_the_component_guard(self, class_name):
        """A copy-paste of ``_require_tenant_scope`` instead of the session guard would silently
        gate session access on the wrong (auto-enabled) flag -- the exact regression this file's
        first test class exists to catch, reachable a second way."""
        offenders = []
        for method_name, fn in _session_functiondefs(BACKEND_SOURCES[class_name]).items():
            body = list(fn.body)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body = body[1:]
            if not body:
                continue
            stmt = body[0]
            if (
                isinstance(stmt, ast.Assign)
                and isinstance(stmt.value, ast.Call)
                and isinstance(stmt.value.func, ast.Attribute)
                and stmt.value.func.attr == "_require_tenant_scope"
            ):
                offenders.append(method_name)
        assert not offenders, f"{class_name} session methods wrongly call the component guard: {offenders}"


# ============================================================================
# 5. tenant_id AS A RECOGNIZED COLUMN
# ============================================================================


class TestTenantColumnDeclaration:
    def test_migration_table_matches_the_scope_declaration(self):
        from egpt.db.migrations.versions.v2_9_0 import SESSION_TABLE_TYPE

        assert {SESSION_TABLE_TYPE} == set(TENANT_SCOPED_SESSION_TABLES)


# ============================================================================
# 6. resolve_tenant_scope IS SHARED, NOT REIMPLEMENTED
# ============================================================================


class TestSharedPrimitive:
    """Session enforcement reuses ``resolve_tenant_scope`` -- only the ``require`` source differs."""

    def test_session_guard_and_component_guard_use_the_same_primitive(self, sqlite_db):
        assert sqlite_db._require_tenant_scope("acme", "m") == resolve_tenant_scope(
            "acme", require=sqlite_db.require_tenant_id, method="m"
        )
        assert sqlite_db._require_session_tenant_scope("acme", "m") == resolve_tenant_scope(
            "acme", require=sqlite_db.require_session_tenant_id, method="m"
        )
