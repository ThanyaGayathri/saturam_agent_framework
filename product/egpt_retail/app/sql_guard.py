"""R6's allow-list, sourced from the semantic layer (K5's contract).

The framework's :class:`~egpt.guardrails.sql_guard.SQLGuard` never imports
:mod:`egpt_retail.contracts.semantic` -- the framework/product boundary (``CLAUDE.md``)
forbids ``libs/agno/egpt/`` reaching into ``product/egpt_retail/``. This module is the seam:
it satisfies the guard's ``TableAllowlistLike`` protocol by wrapping whatever
:class:`~egpt_retail.contracts.semantic.SemanticRead` the deployment is configured with, so
nothing downstream of the guard has to know a semantic layer exists.
"""

from __future__ import annotations

from typing import FrozenSet

from egpt.guardrails.sql_guard import SQLGuard

from egpt_retail.contracts.semantic import (
    SemanticRead,
    aavailable_tables,
    available_tables,
)
from egpt_retail.warehouse.schema import SILVER_SCHEMA

__all__ = ["SemanticTableAllowlist", "build_warehouse_read_guard"]


class SemanticTableAllowlist:
    """Adapts ``SemanticRead.list_tables()`` to the framework's ``TableAllowlistLike`` seam."""

    def __init__(self, reader: SemanticRead) -> None:
        self._reader = reader

    def allowed_tables(self) -> FrozenSet[str]:
        return available_tables(self._reader)

    async def aallowed_tables(self) -> FrozenSet[str]:
        return await aavailable_tables(self._reader)


def build_warehouse_read_guard(
    reader: SemanticRead, *, row_cap: int = 1000, statement_timeout_ms: int = 5000
) -> SQLGuard:
    """The guard every retail read path against the warehouse should be built with.

    No call site in ``app/components.py``/``app/factory.py`` needs this yet -- nothing there
    constructs a ``DatabaseContextProvider`` today; R11's playground (P4, "after R6") is what
    will. This function exists now so that call, when it is written, is one line rather than
    a re-derivation of this adapter.

    ``schema=SILVER_SCHEMA``: the allow-list itself is bare-name-only (``available_tables()``
    drops schema, same as the semantic contract it reads), so without this the guard cannot
    tell ``silver.orders`` apart from ``landing.orders`` or a future ``gold.orders`` -- only
    the ``egpt_reader`` role's grants would. Passing the one schema this deployment's
    semantic layer actually exposes closes that gap here, at the one call site that knows it.
    """
    return SQLGuard(
        allowlist=SemanticTableAllowlist(reader),
        row_cap=row_cap,
        statement_timeout_ms=statement_timeout_ms,
        dialect="postgres",
        schema=SILVER_SCHEMA,
    )
