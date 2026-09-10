"""R6's allow-list, sourced from the semantic layer (K5's contract) -- widened to gold by R5.

The framework's :class:`~egpt.guardrails.sql_guard.SQLGuard` never imports
:mod:`egpt_retail.contracts.semantic` -- the framework/product boundary (``CLAUDE.md``)
forbids ``libs/agno/egpt/`` reaching into ``product/egpt_retail/``. This module is the seam:
it satisfies the guard's ``TableAllowlistLike`` protocol by wrapping whatever
:class:`~egpt_retail.contracts.semantic.SemanticRead` the deployment is configured with, so
nothing downstream of the guard has to know a semantic layer exists.

Gold is not in the semantic layer. K11's graph edges point at *source* tables only, and a
harvest of ``silver`` does not see ``gold`` -- so ``available_tables(reader)`` alone would
refuse every read of the values the product exists to produce. :class:`WarehouseReadAllowlist`
is that set plus gold's fixed relations plus one view per registered KPI, and
:func:`build_warehouse_read_guard` names both schemas. The KPI identifiers come from the
registry the caller already holds, never from the statement being checked.
"""

from __future__ import annotations

from typing import FrozenSet, Iterable, Optional, Tuple

from egpt.guardrails.sql_guard import SQLGuard

from egpt_retail.contracts.semantic import (
    SemanticRead,
    aavailable_tables,
    available_tables,
)
from egpt_retail.warehouse.schema import (
    GOLD_SCHEMA,
    GOLD_TABLES,
    SILVER_SCHEMA,
    gold_view,
)

__all__ = [
    "SemanticTableAllowlist",
    "WarehouseReadAllowlist",
    "build_warehouse_read_guard",
]


class SemanticTableAllowlist:
    """Adapts ``SemanticRead.list_tables()`` to the framework's ``TableAllowlistLike`` seam.

    **Read once, per guard.** The framework calls ``allowed_tables()`` on every ``check()``,
    and a guard is built per call site for a batch of statements -- so an uncached adapter
    listed the whole graph once per statement checked. R5's nightly pass checks a statement
    per KPI, which made this a full graph listing per KPI, on the loop.

    Caching is not a shortcut here, it is the correct semantics: a guard's decisions should
    all be made against one view of the semantic layer. A harvest landing mid-pass would
    otherwise let statement 40 be judged against tables statement 1 was not, and two KPIs in
    the same run would be refused for different reasons with nothing recording why. The
    staleness this introduces is the staleness ``kpi_identifiers`` already documents: a guard
    is a snapshot, and a caller that wants a newer one builds a new guard.
    """

    def __init__(self, reader: SemanticRead) -> None:
        self._reader = reader
        self._cached: Optional[FrozenSet[str]] = None

    def allowed_tables(self) -> FrozenSet[str]:
        if self._cached is None:
            self._cached = available_tables(self._reader)
        return self._cached

    async def aallowed_tables(self) -> FrozenSet[str]:
        if self._cached is None:
            self._cached = await aavailable_tables(self._reader)
        return self._cached


class WarehouseReadAllowlist(SemanticTableAllowlist):
    """The semantic layer's tables, plus gold: its fixed relations and one view per KPI.

    ``kpi_identifiers`` is a snapshot taken at construction, on purpose. A guard is built
    per call site from the registry that site already loaded; a KPI registered after that
    is not readable through this guard until the caller rebuilds it, which is the same
    staleness rule the semantic tables already have (``available_tables`` is read per check,
    but a harvest between two checks is a snapshot change the caller sees elsewhere).
    """

    def __init__(
        self, reader: SemanticRead, kpi_identifiers: Iterable[str] = ()
    ) -> None:
        super().__init__(reader)
        self._gold: FrozenSet[str] = frozenset(GOLD_TABLES) | frozenset(
            gold_view(identifier) for identifier in kpi_identifiers
        )

    @property
    def gold_tables(self) -> FrozenSet[str]:
        return self._gold

    def allowed_tables(self) -> FrozenSet[str]:
        return super().allowed_tables() | self._gold

    async def aallowed_tables(self) -> FrozenSet[str]:
        return (await super().aallowed_tables()) | self._gold


#: The schemas a retail read may name. Unqualified references are resolved by the connection's
#: search_path (silver, set by the materialiser and the reader role's session), qualified ones
#: must be one of these two.
WAREHOUSE_READ_SCHEMAS: Tuple[str, ...] = (SILVER_SCHEMA, GOLD_SCHEMA)


def build_warehouse_read_guard(
    reader: SemanticRead,
    *,
    kpi_identifiers: Iterable[str] = (),
    row_cap: int = 1000,
    statement_timeout_ms: int = 5000,
) -> SQLGuard:
    """The guard every retail read path against the warehouse is built with.

    R5's materialiser and ``read_gold`` call this; R11's playground (P4) will. Every read of
    silver or gold goes through it -- "No SQL runs outside the guard" is an alignment rule,
    not a preference -- and a formula the guard refuses is recorded as a failed run, not
    executed anyway.

    ``schema={silver, gold}``: the allow-list itself is bare-name-only (``available_tables()``
    drops schema, same as the semantic contract it reads), so without this the guard cannot
    tell ``silver.orders`` apart from ``landing.orders`` -- only the ``egpt_reader`` role's
    grants would. Naming exactly the two schemas this deployment exposes closes that gap at
    the one call site that knows them. ``kpi_identifiers`` widens the allow-list to the
    per-KPI views; pass the registry's identifiers, or nothing for a silver-only reader.
    """
    return SQLGuard(
        allowlist=WarehouseReadAllowlist(reader, kpi_identifiers),
        row_cap=row_cap,
        statement_timeout_ms=statement_timeout_ms,
        dialect="postgres",
        schema=set(WAREHOUSE_READ_SCHEMAS),
    )
