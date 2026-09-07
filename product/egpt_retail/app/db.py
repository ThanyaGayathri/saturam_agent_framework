"""Database construction for the control plane.

Pure construction -- ``PostgresDb`` opens nothing until first use -- so there is no async twin
(the standing exemption in ``CLAUDE.md``). The warehouse is a separate database (D5) and is
deliberately not constructed here: nothing in phase zero reads it, and a handle nobody uses is
one that gets used by accident.
"""

from __future__ import annotations

from egpt.integrations.db.postgres import PostgresDb

from egpt_retail.app.settings import RetailSettings

__all__ = ["CONTROL_PLANE_DB_ID", "build_control_plane_db", "psycopg_dsn"]

#: The id ``/config`` reports for the control-plane database, and the ``db_id`` on every
#: registered component. Stable so run history and memory partitions do not move.
CONTROL_PLANE_DB_ID = "egpt-control-plane"


def build_control_plane_db(settings: RetailSettings) -> PostgresDb:
    """The control-plane database: sessions, components, schedules, traces, memory tiers."""
    return PostgresDb(id=CONTROL_PLANE_DB_ID, db_url=settings.retail_db_url)


def psycopg_dsn(sqlalchemy_url: str) -> str:
    """The plain libpq form of a ``postgresql+psycopg://`` URL, for a raw psycopg connection.

    The consolidator's advisory lock needs a dedicated session that bypasses any pool, so it opens
    its own connection rather than borrowing SQLAlchemy's.
    """
    if sqlalchemy_url.startswith("postgresql+psycopg://"):
        return "postgresql://" + sqlalchemy_url[len("postgresql+psycopg://") :]
    return sqlalchemy_url
