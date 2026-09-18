"""R13's dashboard storage shapes -- domain dataclasses, distinct from the generated Pydantic
wire models in ``api/models/_generated.py`` (mirrors ``contracts/kpi.py``'s split).

``ChartKind``, ``RefreshSchedule`` and ``Visibility`` stay plain enums rather than vocabulary
terms: unlike KPI's ``business_area``, these are fixed application concepts a customer overlay
cannot extend (D9/G10 governs business vocabulary, not chart types), so
``contracts.vocabulary.VocabularySet`` does not apply here.

**Two kinds of tile (contract 1.2.0).** A ``kpi`` tile is what R13 shipped: a reference to a
registered, ready KPI, read from gold. A ``query`` tile carries its own SQL -- composed by the
dashboard suggestion agent from the semantic layer's tables, or written by a person in the
chart editor -- and is read from ``dashboards.dashboard_query_result``, which the guarded
runner in ``dashboards/queries.py`` fills. Neither kind ever runs SQL when a dashboard is
opened. A ``query`` tile satisfies the same rule ``KpiDefinition`` enforces on a formula: every
table the SQL reads is listed in ``source_tables``, so the guard's allow-list and the tile's own
declaration cannot drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional, Tuple

from egpt_retail.contracts.kpi import referenced_tables

__all__ = [
    "AxisKind",
    "ChartAxis",
    "ChartKind",
    "Dashboard",
    "DashboardFilterColumn",
    "RefreshSchedule",
    "Tile",
    "TilePosition",
    "TileQuery",
    "TileSource",
    "Visibility",
]


class ChartKind(str, Enum):
    NUMBER = "number"
    LINE = "line"
    BAR = "bar"
    TABLE = "table"


class TileSource(str, Enum):
    """Where a tile's numbers come from. ``kpi``: a registered KPI's gold view. ``query``: the
    tile's own guarded SQL, cached per refresh."""

    KPI = "kpi"
    QUERY = "query"


class AxisKind(str, Enum):
    """What a query tile's x column holds: a time (plot as a series) or a category (plot as
    labelled bars / rows)."""

    TIME = "time"
    CATEGORY = "category"


class RefreshSchedule(str, Enum):
    """How often the numbers are recomputed -- the declared cadence, stored for the
    (not-yet-built) scheduler to honour; this package enforces none of it.

    ``nightly`` (the default) follows gold: as soon as the nightly materialisation completes
    (D7). ``daily`` is once a day at a fixed morning time (06:00), reading what gold holds.
    ``weekly`` is Monday mornings. ``hourly`` is every hour, capped in practice by how often
    gold materialises. ``manual`` is only when asked.
    """

    NIGHTLY = "nightly"
    DAILY = "daily"
    WEEKLY = "weekly"
    HOURLY = "hourly"
    MANUAL = "manual"


class Visibility(str, Enum):
    PRIVATE = "private"
    TEAM = "team"
    EVERYONE = "everyone"


@dataclass(frozen=True)
class TilePosition:
    x: int
    y: int
    w: int
    h: int

    def __post_init__(self) -> None:
        if self.x < 0 or self.y < 0:
            raise ValueError(
                f"position must be non-negative, got x={self.x}, y={self.y}"
            )
        if self.w < 1 or self.h < 1:
            raise ValueError(f"size must be at least 1x1, got w={self.w}, h={self.h}")


@dataclass(frozen=True)
class ChartAxis:
    """One result column a query tile plots. ``kind`` matters on the x axis only; ``label``
    and ``unit`` are presentation, carried for the y axis."""

    column: str
    kind: AxisKind = AxisKind.CATEGORY
    label: str = ""
    unit: str = ""

    def __post_init__(self) -> None:
        if not self.column.strip():
            raise ValueError("an axis must name a result column")


@dataclass(frozen=True)
class TileQuery:
    """A ``query`` tile's SQL and how its result maps onto a chart.

    Construction validates the query against itself the way :class:`KpiDefinition` does: the
    SQL is non-empty, ``source_tables`` is non-empty, and every table the SQL reads is listed
    in it. ``source_tables`` is what the guard's allow-list and the KPI-delete reference check
    key off, so a drift here would let a chart read a table nothing knows it reads.
    """

    sql: str
    source_tables: frozenset
    dialect: str = "postgres"
    x: Optional[ChartAxis] = None
    y: Tuple[ChartAxis, ...] = ()
    series_by: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "source_tables", frozenset(t.lower() for t in self.source_tables)
        )
        if not self.sql.strip():
            raise ValueError("query sql is empty")
        if not self.source_tables:
            raise ValueError("query source_tables is empty")
        read = referenced_tables(self.sql)
        if read - self.source_tables:
            raise ValueError(
                f"query reads {sorted(read - self.source_tables)} which source_tables "
                "does not list"
            )


@dataclass(frozen=True)
class Tile:
    id: str
    #: The KPI a ``kpi`` tile reads; ``None`` on a ``query`` tile.
    kpi_identifier: Optional[str]
    chart: ChartKind
    position: TilePosition
    title: str = ""
    #: For a KPI at a non-overall grain, which row (a supplier id, a location) this tile
    #: shows. ``None`` renders the top-N as a table -- the spec's own distinction.
    grain_value: Optional[str] = None
    source: TileSource = TileSource.KPI
    query: Optional[TileQuery] = None
    #: Shown under the chart. Free text a person may edit; never used to run anything.
    description: str = ""
    #: Why the suggestion agent proposed this chart. Read-only once written.
    rationale: str = ""

    def __post_init__(self) -> None:
        if self.source is TileSource.KPI:
            if not self.kpi_identifier:
                raise ValueError(
                    f"tile {self.id}: a kpi tile must name a kpi_identifier"
                )
            if self.query is not None:
                raise ValueError(f"tile {self.id}: a kpi tile carries no query")
        else:
            if self.query is None:
                raise ValueError(f"tile {self.id}: a query tile must carry a query")
            if self.kpi_identifier:
                raise ValueError(
                    f"tile {self.id}: a query tile does not name a kpi_identifier"
                )

    @property
    def is_query(self) -> bool:
        return self.source is TileSource.QUERY


@dataclass(frozen=True)
class DashboardFilterColumn:
    """A column exposed as an end-user filter on the published dashboard.

    A definition, not a selection: it names which ``(table, column)`` becomes a dropdown
    on the dashboard, never a value someone picked in it. What a viewer picks is per-viewer,
    transient view-time state, not part of the saved dashboard (R13.1)."""

    table: str
    column: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "table", self.table.strip().lower())
        object.__setattr__(self, "column", self.column.strip().lower())
        if not self.table:
            raise ValueError("filter column must name a table")
        if not self.column:
            raise ValueError("filter column must name a column")


@dataclass(frozen=True)
class Dashboard:
    id: str
    name: str
    owner: str
    created_at: datetime
    updated_at: datetime
    description: str = ""
    tiles: Tuple[Tile, ...] = ()
    filters: Tuple[DashboardFilterColumn, ...] = ()
    refresh_schedule: RefreshSchedule = RefreshSchedule.NIGHTLY
    visibility: Visibility = Visibility.PRIVATE

    def visible_to(self, caller: str) -> bool:
        """Whether ``caller`` may see this dashboard in a list -- owner always can; team and
        everyone visibility open it to anyone in this single-tenant-per-deployment (D3)."""
        return self.owner == caller or self.visibility in (
            Visibility.TEAM,
            Visibility.EVERYONE,
        )

    def tile(self, tile_id: str) -> Optional[Tile]:
        for item in self.tiles:
            if item.id == tile_id:
                return item
        return None
