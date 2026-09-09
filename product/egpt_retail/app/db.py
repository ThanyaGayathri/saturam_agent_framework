"""Database construction: the control plane, and the warehouse.

Pure construction -- neither ``PostgresDb`` nor ``create_engine`` opens anything until first
use -- so there are no async twins here (the standing exemption in ``CLAUDE.md``).

Two databases, deliberately different types. The control plane gets a ``PostgresDb``, the
framework's abstraction over sessions, memories, components and schedules. The warehouse (D5)
gets a bare SQLAlchemy ``Engine``, and that asymmetry is the point: R4 needs an engine and
``sqlalchemy.text`` and nothing else, while handing it a ``PostgresDb`` would invite someone to
point a framework feature at the warehouse -- which is exactly what a separate database exists
to prevent. No framework table lives there and ``MigrationManager`` never touches it.
"""

from __future__ import annotations

import re

from egpt.integrations.db.postgres import PostgresDb
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from egpt_retail.app.settings import RetailSettings

__all__ = [
    "CONTROL_PLANE_DB_ID",
    "WAREHOUSE_DB_ID",
    "build_control_plane_db",
    "build_control_plane_engine",
    "build_warehouse_engine",
    "build_warehouse_readonly_engine",
    "psycopg_dsn",
]

#: Matches any SQLAlchemy ``postgresql+<driver>://`` scheme -- not just ``+psycopg`` -- so a DSN
#: written for a different driver (``+psycopg2``, ``+asyncpg``) still reaches ``psycopg.connect``
#: as plain libpq instead of failing there, opaquely, only once the consolidator starts.
_SQLALCHEMY_DRIVER_SCHEME = re.compile(r"^postgresql\+\w+://")

#: The id ``/config`` reports for the control-plane database, and the ``db_id`` on every
#: registered component. Stable so run history and memory partitions do not move.
CONTROL_PLANE_DB_ID = "egpt-control-plane"


def build_control_plane_db(settings: RetailSettings) -> PostgresDb:
    """The control-plane database: sessions, components, schedules, traces, memory tiers."""
    return PostgresDb(id=CONTROL_PLANE_DB_ID, db_url=settings.retail_db_url)


def build_control_plane_engine(settings: RetailSettings) -> Engine:
    """A bare ``Engine`` against the control-plane DSN, for DDL and writes that fall outside
    ``PostgresDb``'s own curated table set (R7, G4): the answer-provenance and delivery tables.

    ``MigrationManager`` is deliberately not extended for either -- its own scope is the
    framework's relational tables, and tracker #178 hardened it specifically against stamping
    a table it never migrated (the exact reasoning ``warehouse/ddl.py`` already documents for
    the warehouse). These two tables get the same hand-rolled, idempotent-DDL treatment R4's
    warehouse tables get, just pointed at the control-plane database instead of a separate one
    -- so a bare engine, not a second ``PostgresDb``, is what a caller doing that DDL needs.

    No refusal-when-unset branch, unlike the warehouse builders above: ``retail_db_url`` is a
    required setting, not ``Optional``, so a missing value already fails at settings
    construction rather than needing a point-of-use check here.
    """
    return create_engine(settings.retail_db_url, pool_pre_ping=True, future=True)


def psycopg_dsn(sqlalchemy_url: str) -> str:
    """The plain libpq form of a ``postgresql+<driver>://`` URL, for a raw psycopg connection.

    The consolidator's advisory lock needs a dedicated session that bypasses any pool, so it opens
    its own connection rather than borrowing SQLAlchemy's. Strips *any* SQLAlchemy driver suffix,
    not just ``+psycopg``: a DSN configured with ``+psycopg2`` or ``+asyncpg`` still starts fine
    under ``PostgresDb`` (the ``api``/``migrate`` roles never see this function), so leaving the
    suffix in would only surface as an opaque libpq scheme error from the consolidator, later and
    unrelated-looking.
    """
    return _SQLALCHEMY_DRIVER_SCHEME.sub("postgresql://", sqlalchemy_url, count=1)


#: What ``/config`` would call the warehouse if it ever listed it. It does not: the warehouse
#: is not a framework database and no component is bound to it.
WAREHOUSE_DB_ID = "egpt-warehouse"


def build_warehouse_engine(settings: RetailSettings) -> Engine:
    """The warehouse (D5): landing, the five silver entities, and the quality tables.

    Refuses when ``EGPT_RETAIL_WAREHOUSE_DB_URL`` is unset, naming the variable. This is a
    point-of-use refusal rather than a startup check on purpose -- the ``api`` and
    ``consolidator`` roles never read the warehouse, so failing their startup over a variable
    they do not use would be the wrong trade. The load path fails loudly the moment it needs
    the handle, which is early enough to be useful and late enough to be true.

    ``pool_pre_ping`` because a load is long-running and mostly idle between statements, and a
    connection dropped by a pooler mid-load would otherwise surface as an opaque failure
    partway through conformance.
    """
    if not settings.retail_warehouse_db_url:
        raise RuntimeError(
            "EGPT_RETAIL_WAREHOUSE_DB_URL is not set, so there is no warehouse to load into. "
            "The compose stack creates the database (deploy/postgres/init/01-warehouse.sql) "
            "and the Helm chart sets the variable from database.warehouseUrl."
        )
    return create_engine(
        settings.retail_warehouse_db_url, pool_pre_ping=True, future=True
    )


def build_warehouse_readonly_engine(settings: RetailSettings) -> Engine:
    """The read-only warehouse connection (R6, G3): authenticated as ``egpt_reader``
    (``warehouse/sql/ddl/065_readonly_role.sql``), which cannot write regardless of what any
    guard above it decides.

    ``execution_options={"postgresql_readonly": True}`` is a second, independent enforcement
    layer under the role and R6's SQL guard: SQLAlchemy sets the session to read-only mode at
    the driver level, the same defence-in-depth ``PostgresTools.connect()`` already applies
    by setting ``read_only = True`` on its raw psycopg connection. Three layers, none of them
    the whole story alone: the guard refuses before a statement is sent, the role rejects a
    write the guard let through by mistake, and the session mode rejects a write transaction
    outright even if the role and the guard were both bypassed somehow.

    Refuses when ``EGPT_RETAIL_WAREHOUSE_READONLY_DB_URL`` is unset, at point of use --
    same reasoning as :func:`build_warehouse_engine`.
    """
    if not settings.retail_warehouse_readonly_db_url:
        raise RuntimeError(
            "EGPT_RETAIL_WAREHOUSE_READONLY_DB_URL is not set, so there is no read-only "
            "warehouse connection for R6's SQL guard to sit above. Run warehouse-migrate "
            "first to create the egpt_reader role (065_readonly_role.sql)."
        )
    return create_engine(
        settings.retail_warehouse_readonly_db_url,
        pool_pre_ping=True,
        future=True,
        execution_options={"postgresql_readonly": True},
    )
