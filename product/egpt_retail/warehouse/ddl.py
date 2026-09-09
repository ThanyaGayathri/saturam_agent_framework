"""Applying the warehouse schema.

Every script under ``sql/ddl/`` is idempotent -- ``CREATE ... IF NOT EXISTS`` or
``CREATE OR REPLACE`` throughout, which ``product/tests/test_r4_sql_assets.py`` enforces --
so applying them is safe to repeat and needs no version ledger. That is deliberate, and it
is why ``MigrationManager`` is not extended here: it covers the framework's control-plane
tables, the warehouse holds none of them, and tracker #178 hardened it specifically against
stamping tables it never migrated.

Versioning becomes worth its cost when a column has to change under live data, which is R5's
problem once gold exists. Until then, re-runnability is the property that replaces it.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Tuple

from sqlalchemy.engine import Engine

from egpt_retail.warehouse.sql import ddl_scripts

__all__ = ["apply_warehouse_ddl", "aapply_warehouse_ddl"]

log = logging.getLogger(__name__)


def apply_warehouse_ddl(engine: Engine) -> Tuple[str, ...]:
    """Apply every DDL script in dependency order. Returns the names applied.

    One transaction for the whole set: a half-created schema is worse than none, because the
    next run would find some objects present and the failure would look like success.

    ``exec_driver_sql`` rather than ``text()`` because these scripts hold several statements
    each and take no parameters -- ``text()`` would try to parse ``$fn$ ... $fn$`` bodies for
    bind markers it must not touch.
    """
    applied = []
    with engine.begin() as connection:
        for name, statement in ddl_scripts():
            log.info("warehouse-migrate: applying %s", name)
            connection.exec_driver_sql(statement)
            applied.append(name)
    return tuple(applied)


async def aapply_warehouse_ddl(engine: Engine) -> Tuple[str, ...]:
    """:func:`apply_warehouse_ddl` off the event loop.

    The engine is the synchronous one, so the work is moved to a thread rather than pretended
    to be awaitable -- the idiom ``DatabaseContextProvider.astatus`` already uses.
    """
    return await asyncio.to_thread(apply_warehouse_ddl, engine)
