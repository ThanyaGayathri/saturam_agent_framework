"""R13's dashboard storage shapes -- domain dataclasses, distinct from the generated Pydantic
wire models in ``api/models/_generated.py`` (mirrors ``contracts/kpi.py``'s split).

``ChartKind``, ``RefreshSchedule`` and ``Visibility`` stay plain enums rather than vocabulary
terms: unlike KPI's ``business_area``, these are fixed application concepts a customer overlay
cannot extend (D9/G10 governs business vocabulary, not chart types), so
``contracts.vocabulary.VocabularySet`` does not apply here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional, Tuple

__all__ = [
    "ChartKind",
    "Dashboard",
    "DashboardFilter",
    "RefreshSchedule",
    "Tile",
    "TilePosition",
    "Visibility",
]


class ChartKind(str, Enum):
    NUMBER = "number"
    LINE = "line"
    BAR = "bar"
    TABLE = "table"


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
class Tile:
    id: str
    kpi_identifier: str
    chart: ChartKind
    position: TilePosition
    title: str = ""
    #: For a KPI at a non-overall grain, which row (a supplier id, a location) this tile
    #: shows. ``None`` renders the top-N as a table -- the spec's own distinction.
    grain_value: Optional[str] = None


@dataclass(frozen=True)
class DashboardFilter:
    field: str
    value: str


@dataclass(frozen=True)
class Dashboard:
    id: str
    name: str
    owner: str
    created_at: datetime
    updated_at: datetime
    description: str = ""
    tiles: Tuple[Tile, ...] = ()
    filters: Tuple[DashboardFilter, ...] = ()
    refresh_schedule: RefreshSchedule = RefreshSchedule.NIGHTLY
    visibility: Visibility = Visibility.PRIVATE

    def visible_to(self, caller: str) -> bool:
        """Whether ``caller`` may see this dashboard in a list -- owner always can; team and
        everyone visibility open it to anyone in this single-tenant-per-deployment (D3)."""
        return self.owner == caller or self.visibility in (
            Visibility.TEAM,
            Visibility.EVERYONE,
        )
