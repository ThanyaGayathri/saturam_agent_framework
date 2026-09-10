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
from functools import lru_cache

from egpt.integrations.db.postgres import PostgresDb
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import PoolProxiedConnection

from egpt_retail.app.settings import RetailSettings

__all__ = [
    "CONTROL_PLANE_DB_ID",
    "WAREHOUSE_DB_ID",
    "build_control_plane_db",
    "build_control_plane_engine",
    "build_warehouse_engine",
    "build_warehouse_readonly_engine",
    "pooled_control_plane_connection",
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

    Refuses a non-Postgres DSN by name, rather than letting one reach the DDL: R7's provenance
    and delivery tables are Postgres-only (``JSONB``, ``TIMESTAMPTZ``, ``now()``), the same
    assumption ``build_control_plane_db``'s ``PostgresDb`` already makes for every other
    control-plane table -- an opaque driver error three calls downstream is a worse failure
    than naming the setting here, at the one place both DDL callers construct their engine
    through.
    """
    return _validated_engine(settings.retail_db_url)


def _validated_engine(db_url: str) -> Engine:
    if not _SQLALCHEMY_DRIVER_SCHEME.match(db_url):
        raise ValueError(
            f"EGPT_RETAIL_DB_URL must be a postgresql+<driver>:// DSN, got "
            f"{db_url!r}. The control-plane database, including R7's "
            "answer-provenance and delivery tables, is Postgres-only."
        )
    return create_engine(db_url, pool_pre_ping=True, future=True)


@lru_cache(maxsize=None)
def _pooled_control_plane_engine(db_url: str) -> Engine:
    """One pooled ``Engine`` per control-plane DSN, cached for the process's lifetime.

    Keyed by the DSN string, not by ``RetailSettings`` (not hashable in a way ``lru_cache``
    could use) -- the DSN does not change within one process's lifetime, so this is a
    process-wide singleton in effect, the same posture a long-lived server holds its
    connection pool for. Nothing here opens a connection; ``create_engine`` is lazy, same as
    :func:`build_control_plane_engine`.
    """
    return _validated_engine(db_url)


def pooled_control_plane_connection(settings: RetailSettings) -> PoolProxiedConnection:
    """A raw DBAPI connection from the process-wide pooled engine above -- for a caller (a
    store class) that expects a psycopg-shaped connection with ``.cursor()``/``.commit()``,
    not a SQLAlchemy ``Connection``. Returns to the pool on ``.close()`` rather than actually
    disconnecting, which is the entire difference from ``psycopg.connect(psycopg_dsn(...))``
    -- the R11/R13 routers' per-request dependencies (``api/routers/playground.py``,
    ``dashboards.py``) call this many times a second under real traffic, and a fresh TCP
    connection per call is a real latency and connection-exhaustion risk.
    """
    return _pooled_control_plane_engine(settings.retail_db_url).raw_connection()


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
