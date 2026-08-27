"""Tenant scoping for session/run data access (aicoe-tracker#197).

``docs/adr/0004-component-tenant-isolation.md`` scoped EGPT-2.7 to component data access only and
recorded sessions, memories and runs as a follow-up: those tables had no ``tenant_id`` column, so
scoping them was "a schema change with a migration, not an enforcement change". This module is
that follow-up for sessions. Runs are not a separate table -- a ``RunOutput`` lives inside the
``runs`` JSON column of the session row it belongs to (see ``AgentSession``/``TeamSession``/
``WorkflowSession``), so scoping the session read closes the run-read gap named in the ADR:
``Workflow.aget_run_output`` -> ``aget_session`` -> ``_aread_session`` all bottom out in
``BaseDb.get_session``.

Why ``tenant_id`` stays optional here, unlike the fourteen component methods
-----------------------------------------------------------------------------
EGPT-2.7 made ``tenant_id`` keyword-only with **no default** on every component method, so a
forgotten argument is a ``TypeError`` and a mypy ``call-arg`` error rather than a silent
cross-tenant read. That works because the component surface is narrow: the ``/components``
router, runtime hydration, and the studio toolkit are the only callers, all already updated in
the same PR.

The session surface is not narrow. ``get_session``/``upsert_session`` and friends sit in the hot
path of *every* agent, team and workflow run -- saved and loaded on every turn, from ~100 call
sites deep inside ``agent/_run.py``, ``team/_run.py`` and ``workflow.py`` alone, before counting
cookbooks and downstream code. Most of those call sites have no tenant to name because they run
in a single-tenant deployment. Making the parameter required would force every one of them to
pass ``tenant_id=None`` for no isolation gain where tenancy is off, and risks silently breaking
call sites this change cannot enumerate. So the session methods follow the *other* documented
precedent in this codebase instead: ``Agent.save``/``Team.save``/``Workflow.save`` take an
**optional** ``tenant_id: Optional[str] = None``, and ``require_tenant_id`` is the actual
control, not the default (see ADR 0004, "The non-HTTP entry points keep an unscoped default").

Why session enforcement is a *separate* flag from component enforcement
-------------------------------------------------------------------------
``require_session_tenant_id`` is deliberately not the same flag as ``require_tenant_id``, and
this module -- unlike the component methods, which reuse ``BaseDb._require_tenant_scope`` -- has
its own guard, ``BaseDb._require_session_tenant_scope`` / ``AsyncBaseDb._require_session_tenant_scope``,
backed by ``self.require_session_tenant_id``.

The reason is a real, checked-for regression, not caution for its own sake: ``EgptOS`` turns
``require_tenant_id`` on for *every registered db* the moment an operator configures
``AuthorizationConfig(tenant_id_claim=...)``. That is safe for components, because the write
paths that stamp a component's ``tenant_id`` (the ``/components`` router, ``Agent.save`` and
friends) were updated in the same PR that added the guard. It would not be safe for sessions:
this PR wires ``tenant_id`` through the *read* chain the issue names
(``aget_run_output``/``aget_session``/``_aread_session`` and the WS/HTTP call sites that resolve
a caller's tenant), but it deliberately does **not** thread ``tenant_id`` through session
*writes* -- ``read_or_create_session``, ``save_session``/``asave_session``, and the ~100 call
sites in the run loop that invoke them. That is future work, tracked separately, because doing it
correctly means auditing every one of those call sites rather than a mechanical parameter
addition. Until it ships, every session a run creates or updates is written with
``tenant_id=NULL``, regardless of the caller's tenant.

If session enforcement shared ``require_tenant_id``, turning on tenancy for components would
also turn on ``TenantScopeRequiredError`` for session/run access -- and since nothing stamps a
tenant on write yet, that failure would not be confined to the read endpoints this issue is
about. It would break ``Agent.run()``/``Team.run()``/``Workflow.run()`` themselves, the moment
any of them tries to load or save its own session with the implicit ``tenant_id=None`` every
internal call site still passes. ``require_session_tenant_id`` is therefore off by default and
**not** auto-enabled by ``EgptOS`` -- an operator opts in (constructor argument or
``EGPT_REQUIRE_SESSION_TENANT_ID``) only once they have verified their deployment's session
writes are tenant-safe, exactly the same posture ADR 0004 already documents for
``Agent.save``/``Team.save``/``Workflow.save``'s unscoped default: "``require_tenant_id`` is
therefore the control for these three writers, not their default."

That still closes the security gap the issue describes for a caller who does not opt into
strict mode's absence: the property being fixed is "a caller who knows a session_id/run_id can
read it across tenants when tenancy is on" -- and once an operator turns on
``require_session_tenant_id``, an unscoped session/run read is refused whether ``tenant_id`` was
explicitly passed as ``None`` or simply never named, because Python does not distinguish the two
for an optional parameter. What optional-with-a-default gives up is the *first* guard EGPT-2.7
had for components -- a forgotten argument failing at check time in every deployment, tenanted or
not. That guard is not reachable here without the same required-parameter migration across the
whole session hot path, which remains future work.

Reuses ``egpt.db.component_scope.resolve_tenant_scope`` (via ``BaseDb._require_session_tenant_scope``,
which is generic on the calling method's name and the ``require`` flag it checks, and was never
component-specific in its implementation) rather than duplicating the same validate-and-normalize
logic under a new name.

Where the constants are used
-----------------------------
:data:`SESSION_DATA_ACCESS_METHODS` is what the fail-closed test iterates instead of a
hand-maintained list, mirroring ``COMPONENT_DATA_ACCESS_METHODS``. :data:`TENANT_SCOPED_SESSION_TABLES`
is the single declaration of which table carries the tenant column, consumed by the same style of
DDL conformance check ``test_component_tenant_fail_closed.py`` runs for the component tables.
``db/migrations/versions/v2_9_0.py`` deliberately keeps its own table-type literal rather than
importing this constant, for the same reason ``v2_6_0.py`` does not import
``component_scope.TENANT_SCOPED_COMPONENT_TABLES``: a historical migration importing a live
constant would retroactively try to alter whatever gets added to it later. The relationship
between the two is pinned by a test instead.
"""

from typing import Tuple

from egpt.utils.env import branded_env_key, get_branded_env

__all__ = [
    "ASYNC_SESSION_DATA_ACCESS_METHODS",
    "REQUIRE_SESSION_TENANT_ID_ENV",
    "SESSION_DATA_ACCESS_METHODS",
    "TENANT_COLUMN",
    "TENANT_SCOPED_SESSION_TABLES",
    "require_session_tenant_id_from_env",
]

# Same column name and semantics as ``egpt.db.component_scope.TENANT_COLUMN``. Declared again
# here (rather than imported) so this module reads standalone and mirrors the component module's
# own shape.
TENANT_COLUMN = "tenant_id"

# The one table this module scopes. Declared as a tuple, not a bare string, so the shape matches
# ``component_scope.TENANT_SCOPED_COMPONENT_TABLES`` and any future session-adjacent table (e.g.
# a dedicated runs table, should one ever be split out) can be added without changing the type.
TENANT_SCOPED_SESSION_TABLES: Tuple[str, ...] = ("sessions",)

# Every session data-access method on ``BaseDb`` (sync). ``upsert_sessions`` (bulk) has no async
# counterpart on ``AsyncBaseDb``'s abstract surface today -- see
# ``ASYNC_SESSION_DATA_ACCESS_METHODS`` -- though ``AsyncSqliteDb``/``AsyncMySQLDb`` happen to
# implement it anyway as a concrete-only convenience method.
SESSION_DATA_ACCESS_METHODS: Tuple[str, ...] = (
    "get_session",
    "get_sessions",
    "upsert_session",
    "upsert_sessions",
    "rename_session",
    "delete_session",
    "delete_sessions",
)

# The ``AsyncBaseDb`` abstract surface, one method short of the sync one (no bulk
# ``upsert_sessions``).
ASYNC_SESSION_DATA_ACCESS_METHODS: Tuple[str, ...] = (
    "get_session",
    "get_sessions",
    "upsert_session",
    "rename_session",
    "delete_session",
    "delete_sessions",
)

REQUIRE_SESSION_TENANT_ID_ENV = "REQUIRE_SESSION_TENANT_ID"

# Same accepted spellings as ``component_scope.require_tenant_id_from_env`` -- duplicated rather
# than imported so this module's env contract does not silently change if the component one ever
# does, and because pulling in a private module-level set for one comparison is not worth the
# coupling.
_ENV_TRUE = frozenset({"true", "1", "yes", "y", "on", "t"})
_ENV_FALSE = frozenset({"false", "0", "no", "n", "off", "f", ""})


def require_session_tenant_id_from_env() -> bool:
    """Read ``EGPT_REQUIRE_SESSION_TENANT_ID``, refusing to guess at an unrecognised value.

    The session counterpart of ``egpt.db.component_scope.require_tenant_id_from_env`` --
    deliberately a separate environment variable, not an alias for
    ``EGPT_REQUIRE_TENANT_ID``, for the same reason ``require_session_tenant_id`` is a separate
    constructor argument: see this module's docstring.

    Raises:
        ValueError: If the variable is set to a value that is neither clearly true nor clearly
            false.
    """
    raw = get_branded_env(REQUIRE_SESSION_TENANT_ID_ENV)
    if raw is None:
        return False
    value = raw.strip().lower()
    if value in _ENV_TRUE:
        return True
    if value in _ENV_FALSE:
        return False
    raise ValueError(
        f"{branded_env_key(REQUIRE_SESSION_TENANT_ID_ENV)}={raw!r} is not a recognised boolean. "
        f"Use one of {sorted(_ENV_TRUE)} to require a tenant on every session/run operation, or "
        f"one of {sorted(v for v in _ENV_FALSE if v)} to allow unscoped access. Refusing to "
        f"start rather than silently leaving session tenant enforcement off."
    )
