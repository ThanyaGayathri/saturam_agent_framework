"""Tenant scoping for memory data access (aicoe-tracker#175).

SM-C3 (#91) added a nullable ``tenant_id`` column to the memories table (Postgres and SQLite
only -- see ``db/migrations/versions/v2_8_0.py``), but nothing read or wrote it:
``MemoryManager`` partitioned on ``user_id`` alone, so two tenants sharing a ``user_id`` could
read each other's memories. This module is the consumer half: it makes ``tenant_id`` an actual
predicate on every memory data-access method, following the same shape
``egpt/db/session_scope.py`` established for sessions/runs (aicoe-tracker#197) -- optional,
keyword-only, gated by its own opt-in flag rather than the component methods' mandatory
``require_tenant_id``.

Why ``tenant_id`` stays optional here, unlike the fourteen component methods
-----------------------------------------------------------------------------
``egpt/db/component_scope.py`` made ``tenant_id`` keyword-only with **no default** on every
component method, because the component surface is narrow (the ``/components`` router, runtime
hydration, the studio toolkit) and every call site was updated in the same PR. The memory CRUD
surface is not narrow: ``MemoryManager.search_user_memories``/``add_user_memory`` and friends are
called from the agentic memory-creation loop, the `seed` provider bridge, and any cookbook or
downstream code that reads or writes memories directly -- with no tenant to name in the ordinary
single-tenant deployment. Requiring the parameter would force all of those call sites to pass
``tenant_id=None`` for no isolation gain where tenancy is off. So memory methods follow the
session precedent instead: an **optional** ``tenant_id: Optional[str] = None``, with
``require_memory_tenant_id`` as the actual control.

Why memory enforcement is a *separate* flag from component and session enforcement
-------------------------------------------------------------------------------------
``require_memory_tenant_id`` is its own flag, not an alias for ``require_tenant_id`` or
``require_session_tenant_id``, for the same reason those two are already separate from each
other: ``EgptOS`` turns ``require_tenant_id`` on for every registered db the moment an operator
configures a tenant claim, and that is safe for components because their write paths were
updated in the same PR that added the guard. It would not be safe for memory: nothing in this
change threads a caller's tenant through the ~agentic extraction call sites
(``MemoryManager.create_user_memories`` and friends), so every memory an ``Agent``/``Team`` run
writes through those paths still stamps ``tenant_id=NULL`` unless the caller passes one
explicitly to the narrower CRUD surface this module actually scopes. If memory enforcement
shared ``require_tenant_id``, turning on tenancy for components would also start refusing
unscoped memory reads/writes from every one of those untouched call sites. So
``require_memory_tenant_id`` is off by default and not auto-enabled by ``EgptOS`` -- an operator
opts in (constructor argument or ``EGPT_REQUIRE_MEMORY_TENANT_ID``) only once they have verified
their deployment's memory writes are tenant-safe.

Which backends can actually filter
-----------------------------------
Unlike sessions, the tenant column exists on **Postgres and SQLite only** -- v2_8_0.py's own
docstring is explicit that MySQL, SingleStore and DynamoDB "define a memories table but get
neither the column nor the tier tables". ``InMemoryDb`` carries no schema at all, so its
in-process rows can filter on whatever ``UserMemory.tenant_id`` already carries with no migration
needed, the same way its session store does. So :data:`MEMORY_TENANT_SCOPE_SUPPORTED_BACKENDS` is
narrower than :data:`egpt.db.session_scope.SESSION_TENANT_SCOPE_SUPPORTED_BACKENDS`: it omits
MySQL and SingleStore, which that tuple includes. Every other concrete backend that implements
the memory methods at all accepts ``tenant_id`` for signature parity with ``BaseDb`` but cannot
enforce it, and :func:`require_unsupported_memory_tenant_scope` refuses to construct one of those
with enforcement turned on rather than let the flag report protection it does not provide.

Reuses ``egpt.db.component_scope.resolve_tenant_scope`` (via ``BaseDb._require_memory_tenant_scope``
/ ``AsyncBaseDb._require_memory_tenant_scope``) rather than duplicating the same validate-and-
normalize logic under a third name -- it was never component-specific in its implementation, and
``session_scope.py`` already established the pattern of reusing it with a different flag name.

Where the constants are used
-----------------------------
:data:`MEMORY_DATA_ACCESS_METHODS` is the memory CRUD surface these guards apply to.
:data:`TENANT_SCOPED_MEMORY_TABLES` names the one table that carries the column, for the same
style of DDL/backend-support bookkeeping ``session_scope.py`` keeps for sessions.
"""

from typing import Tuple

from egpt.utils.env import branded_env_key, get_branded_env

__all__ = [
    "ASYNC_MEMORY_DATA_ACCESS_METHODS",
    "MEMORY_DATA_ACCESS_METHODS",
    "MEMORY_TENANT_SCOPE_SUPPORTED_BACKENDS",
    "REQUIRE_MEMORY_TENANT_ID_ENV",
    "TENANT_COLUMN",
    "TENANT_SCOPED_MEMORY_TABLES",
    "require_memory_tenant_id_from_env",
    "require_unsupported_memory_tenant_scope",
]

# Backends whose memory methods actually call ``_require_memory_tenant_scope`` and filter/stamp
# by it. Narrower than ``session_scope.SESSION_TENANT_SCOPE_SUPPORTED_BACKENDS``: the tenant
# column on the memories table was migrated for Postgres and SQLite only (v2_8_0.py), and
# ``InMemoryDb`` needs no migration because it carries no schema. MySQL and SingleStore -- which
# the session tuple includes -- define a memories table with no tenant column at all, so they
# cannot filter and are deliberately absent here.
MEMORY_TENANT_SCOPE_SUPPORTED_BACKENDS: Tuple[str, ...] = (
    "SqliteDb",
    "AsyncSqliteDb",
    "PostgresDb",
    "AsyncPostgresDb",
    "InMemoryDb",
)

# Same column name and semantics as ``component_scope.TENANT_COLUMN`` / ``session_scope.TENANT_COLUMN``.
# Declared again here so this module reads standalone.
TENANT_COLUMN = "tenant_id"

# The one table this module scopes.
TENANT_SCOPED_MEMORY_TABLES: Tuple[str, ...] = ("memories",)

# Every memory data-access method on ``BaseDb`` (sync).
MEMORY_DATA_ACCESS_METHODS: Tuple[str, ...] = (
    "clear_memories",
    "delete_user_memory",
    "delete_user_memories",
    "get_all_memory_topics",
    "get_user_memory",
    "get_user_memories",
    "get_user_memory_stats",
    "upsert_user_memory",
    "upsert_memories",
)

# The ``AsyncBaseDb`` abstract surface, one method short of the sync one (no bulk
# ``upsert_memories``) -- the same asymmetry ``session_scope.ASYNC_SESSION_DATA_ACCESS_METHODS``
# has for ``upsert_sessions``.
ASYNC_MEMORY_DATA_ACCESS_METHODS: Tuple[str, ...] = (
    "clear_memories",
    "delete_user_memory",
    "delete_user_memories",
    "get_all_memory_topics",
    "get_user_memory",
    "get_user_memories",
    "get_user_memory_stats",
    "upsert_user_memory",
)

REQUIRE_MEMORY_TENANT_ID_ENV = "REQUIRE_MEMORY_TENANT_ID"

# Same accepted spellings as ``component_scope.require_tenant_id_from_env`` / ``session_scope``'s
# copy -- duplicated rather than imported so this module's env contract does not silently change
# if either of those ever does.
_ENV_TRUE = frozenset({"true", "1", "yes", "y", "on", "t"})
_ENV_FALSE = frozenset({"false", "0", "no", "n", "off", "f", ""})


def require_memory_tenant_id_from_env() -> bool:
    """Read ``EGPT_REQUIRE_MEMORY_TENANT_ID``, refusing to guess at an unrecognised value.

    The memory counterpart of ``egpt.db.session_scope.require_session_tenant_id_from_env`` --
    deliberately its own environment variable, not an alias for
    ``EGPT_REQUIRE_SESSION_TENANT_ID`` or ``EGPT_REQUIRE_TENANT_ID``: see this module's docstring
    for why memory enforcement is its own flag.

    Raises:
        ValueError: If the variable is set to a value that is neither clearly true nor clearly
            false.
    """
    raw = get_branded_env(REQUIRE_MEMORY_TENANT_ID_ENV)
    if raw is None:
        return False
    value = raw.strip().lower()
    if value in _ENV_TRUE:
        return True
    if value in _ENV_FALSE:
        return False
    raise ValueError(
        f"{branded_env_key(REQUIRE_MEMORY_TENANT_ID_ENV)}={raw!r} is not a recognised boolean. "
        f"Use one of {sorted(_ENV_TRUE)} to require a tenant on every memory operation, or "
        f"one of {sorted(v for v in _ENV_FALSE if v)} to allow unscoped access. Refusing to "
        f"start rather than silently leaving memory tenant enforcement off."
    )


def require_unsupported_memory_tenant_scope(backend_name: str, require_memory_tenant_id: bool) -> None:
    """Refuse to construct a memory-tenant-unsupported backend with enforcement turned on.

    ``BaseDb``/``AsyncBaseDb`` resolve ``require_memory_tenant_id`` for *every* backend, including
    the ones that accept the ``tenant_id`` keyword on their memory methods for signature parity
    but have no filter/stamp behind it -- so without this check, the flag would report ``True``
    on a backend that enforces nothing, which is worse than no flag at all. Call this at the end
    of ``__init__`` on every backend not listed in :data:`MEMORY_TENANT_SCOPE_SUPPORTED_BACKENDS`.

    Raises:
        NotImplementedError: If ``require_memory_tenant_id`` is ``True`` (set explicitly or via
            ``EGPT_REQUIRE_MEMORY_TENANT_ID``).
    """
    if not require_memory_tenant_id:
        return
    supported = ", ".join(MEMORY_TENANT_SCOPE_SUPPORTED_BACKENDS)
    raise NotImplementedError(
        f"{backend_name} does not implement tenant-scoped memory enforcement (aicoe-tracker#175): "
        "its memory methods accept tenant_id for signature parity with BaseDb, but nothing "
        "filters or stamps by it. require_memory_tenant_id (or EGPT_REQUIRE_MEMORY_TENANT_ID) is "
        f"set on this construction, which this backend cannot honor. Memory tenant scoping is "
        f"currently implemented only on: {supported}."
    )
