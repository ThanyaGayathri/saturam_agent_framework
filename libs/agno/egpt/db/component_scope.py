"""Mandatory tenant scoping for component data access (EGPT-2.7).

Tenant isolation for DB-defined components used to be enforced *above* this layer: the
``/components`` router gates on a tenant (EGPT-2.4) and threads it into every DB call, and the
component tables have carried a ``tenant_id`` column since EGPT-2.1. But the data layer itself
was fail-open -- ``tenant_id`` defaulted to ``None`` on all fourteen component methods, and
``None`` means "no tenant predicate". A caller that simply forgot the argument read and wrote
across every tenant, silently and successfully. This module closes that.

Two layers, two different mistakes
----------------------------------
The mistakes are distinct and neither guard catches the other, so both exist:

1. **The argument was forgotten.** ``tenant_id`` is keyword-only with no default on every
   component method, so omitting it is a ``TypeError`` at the call and a ``call-arg`` error
   under mypy. This is always on, in every deployment, and it fails at *check* time -- which is
   the whole reason it is a required parameter rather than a sentinel default. A sentinel would
   have the same blast radius and only fail at runtime.

2. **The call is deliberately unscoped in a deployment that scopes by tenant.** ``tenant_id=None``
   stays legal, because it is the correct and only way to reach the NULL-tenant rows written
   before multi-tenancy, and single-tenant deployments have no tenant to pass. So a second,
   opt-in guard refuses it: a ``BaseDb`` built with ``require_tenant_id=True`` (or with
   ``EGPT_REQUIRE_TENANT_ID=true`` set) raises :class:`TenantScopeRequiredError` on an explicit
   ``None``. ``EgptOS`` turns it on for every registered db when an operator configures
   ``AuthorizationConfig(tenant_id_claim=...)``, so opting into tenancy at the edge opts into
   enforcement at the bottom.

The consequence worth stating plainly: under ``require_tenant_id`` the pre-multi-tenancy NULL
rows become unreachable. That is the point -- they are unscoped, and an unscoped row in a
tenanted deployment is visible to everyone. ``docs/tenancy.md`` carries the backfill SQL.

Why the guard and not a rewrite of the predicates
-------------------------------------------------
Each backend already applies ``if tenant_id is not None: stmt = stmt.where(...)`` in roughly
ninety places, and ``list_components`` / ``list_component_audit`` already share one
``where_clauses`` list between their count and page queries so the two cannot drift.
:func:`resolve_tenant_scope` is called once at the top of each method instead of centralising
those predicates: mutation testing (``_strip_tenant_scoping`` in
``tests/unit/db/test_component_tenant_scoping.py``) already established that the tenant-scoped
*prechecks* are the load-bearing layer and the inner predicates are defence in depth. Rewriting
working predicates buys no enforcement the entry-point guard does not already provide.

What makes it stay mandatory is a test, not a convention:
``tests/unit/db/test_component_tenant_fail_closed.py`` parses each backend's AST and asserts the
first statement of all fourteen methods is the guard call, and asserts on every backend class
that ``tenant_id`` is keyword-only with no default. A backend added later without either cannot
pass CI.

Why this is not a ``ValueError``
--------------------------------
The components router maps a ``ValueError`` out of the DB layer to a 400. An unscoped-access
refusal is not a client error -- the client did nothing wrong and cannot fix it by sending
something else. It is a server-side configuration or programming fault, and should surface as
one. :class:`TenantScopeRequiredError` therefore inherits from ``Exception`` directly.

Where the constants are used
----------------------------
:data:`TENANT_SCOPED_COMPONENT_TABLES` and :data:`TENANT_COLUMN` are the single declaration of
which tables carry the tenant column, consumed by the DDL conformance test that asserts
``tenant_id`` exists and leads a composite index on each of them, on both backends. Note that
``db/migrations/versions/v2_6_0.py`` deliberately keeps its own narrower tuple rather than
importing this one: it predates the audit table, and a historical migration that imported a live
constant would retroactively try to alter whatever was added to it later. The relationship
between the two is pinned by a test instead.
"""

from typing import Optional, Tuple

from egpt.utils.env import branded_env_key, get_branded_env

__all__ = [
    "COMPONENT_DATA_ACCESS_METHODS",
    "REQUIRE_TENANT_ID_ENV",
    "TENANT_COLUMN",
    "TENANT_SCOPED_COMPONENT_TABLES",
    "TENANT_SCOPE_REQUIRED_DETAIL",
    "TenantScopeRequiredError",
    "require_tenant_id_from_env",
    "resolve_tenant_scope",
]

# The column every component query scopes on. Declared once so the DDL conformance test and the
# schema definitions cannot disagree about its name.
TENANT_COLUMN = "tenant_id"

# Table *types* (the keys backends resolve to real, prefixed table names) that carry
# ``tenant_id``. All four component tables do, and on each of them the column leads a composite
# index rather than having one of its own -- a tenant-scoped lookup is always a lookup *within* a
# tenant, never a scan of one.
TENANT_SCOPED_COMPONENT_TABLES: Tuple[str, ...] = (
    "components",
    "component_configs",
    "component_links",
    "component_audit",
)

# Every component data-access method on ``BaseDb``. The fail-closed tests iterate this rather
# than a hand-maintained list, so a method added without a tenant guard is caught.
COMPONENT_DATA_ACCESS_METHODS: Tuple[str, ...] = (
    "get_component",
    "upsert_component",
    "delete_component",
    "list_components",
    "create_component_with_config",
    "get_config",
    "upsert_config",
    "delete_config",
    "list_configs",
    "set_current_version",
    "list_component_audit",
    "get_links",
    "get_dependents",
    "load_component_graph",
)

TENANT_SCOPE_REQUIRED_DETAIL = (
    "This database requires every component operation to name a tenant, and this call passed "
    "tenant_id=None (unscoped). Pass the caller's tenant, or build the db without "
    "require_tenant_id to allow unscoped access. See docs/tenancy.md."
)


REQUIRE_TENANT_ID_ENV = "REQUIRE_TENANT_ID"

# Spellings accepted for the environment switch, compared case-insensitively after stripping.
# Anything outside these two sets is a hard error rather than a silent False -- see
# :func:`require_tenant_id_from_env`.
_ENV_TRUE = frozenset({"true", "1", "yes", "y", "on", "t"})
_ENV_FALSE = frozenset({"false", "0", "no", "n", "off", "f", ""})


class TenantScopeRequiredError(Exception):
    """Raised when a component operation is unscoped on a db that requires a tenant.

    Not a ``ValueError``: the components router maps that to a 400, and this is a server-side
    misconfiguration rather than something a client sent wrong.
    """


def require_tenant_id_from_env() -> bool:
    """Read ``EGPT_REQUIRE_TENANT_ID``, refusing to guess at a value it does not recognise.

    A general-purpose boolean reader treats anything it does not recognise as false. That is the
    wrong default for a security switch: ``EGPT_REQUIRE_TENANT_ID=on`` or ``=enabled`` or a
    typo'd ``=ture`` would leave enforcement off, with no error and nothing in the logs, on a
    deployment whose operator believes they turned it on. Failing to boot is recoverable in
    seconds; silently running unprotected is not noticed at all.

    So the accepted spellings are deliberately wide (``true``/``1``/``yes``/``y``/``on``/``t``
    and their negatives, case-insensitive, surrounding whitespace ignored) and anything else
    raises.

    Raises:
        ValueError: If the variable is set to a value that is neither clearly true nor clearly
            false.
    """
    raw = get_branded_env(REQUIRE_TENANT_ID_ENV)
    if raw is None:
        return False
    value = raw.strip().lower()
    if value in _ENV_TRUE:
        return True
    if value in _ENV_FALSE:
        return False
    raise ValueError(
        f"{branded_env_key(REQUIRE_TENANT_ID_ENV)}={raw!r} is not a recognised boolean. "
        f"Use one of {sorted(_ENV_TRUE)} to require a tenant on every component operation, or "
        f"one of {sorted(v for v in _ENV_FALSE if v)} to allow unscoped access. Refusing to "
        f"start rather than silently leaving tenant enforcement off."
    )


def resolve_tenant_scope(tenant_id: Optional[str], *, require: bool, method: str) -> Optional[str]:
    """Validate and normalize the tenant scope of one component call.

    Called as the first statement of every component method on every backend, *before* the
    method's own ``try``. Inside the ``try`` a refusal would be caught by the
    ``except Exception: log_error("Error getting component: ...")`` wrapper those methods share
    and reported as a database error, which is a misleading diagnostic for what is actually a
    configuration or programming fault.

    Args:
        tenant_id: The tenant the call is scoped to. ``None`` is *explicitly unscoped*, which is
            how the NULL-tenant rows written before multi-tenancy are reached; it is refused when
            ``require`` is set. Omitting the argument entirely is a ``TypeError`` at the call
            site and never reaches here.
        require: Whether this db refuses unscoped access (``BaseDb.require_tenant_id``).
        method: The calling method's name, for the error message.

    Returns:
        The tenant, **stripped of surrounding whitespace**, or ``None`` for an unscoped call.

        Stripping is a correctness requirement, not tidiness. ``"acme "`` and ``"acme"`` are
        different values to a SQL predicate, so passing the raw string through would let a write
        stamp rows into a tenant that no properly-trimmed caller can ever name again -- exactly
        the outcome the blank-tenant check below exists to prevent, reached by a subtler route.
        The HTTP path is not where this bites (the JWT middleware already strips); it is the
        non-HTTP callers this whole ticket exists to protect, where the tenant arrives from a
        script argument, a config file, or an environment variable.

    Raises:
        TenantScopeRequiredError: If ``tenant_id`` is ``None`` and ``require`` is set, or if it
            is an empty or whitespace-only string. An empty tenant is never legitimate -- the
            middleware already coerces one to ``None`` (``os/tenancy.py``) -- and on a write it
            would stamp rows into a tenant no caller can name, so it is refused regardless of
            ``require`` rather than being silently treated as unscoped.
    """
    if tenant_id is None:
        if require:
            raise TenantScopeRequiredError(f"{method}() was called unscoped. {TENANT_SCOPE_REQUIRED_DETAIL}")
        return None
    normalized = tenant_id.strip()
    if not normalized:
        raise TenantScopeRequiredError(
            f"{method}() was called with an empty tenant_id. Pass a real tenant, or None for "
            "explicitly unscoped access."
        )
    return normalized
