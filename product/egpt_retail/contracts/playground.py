"""R11's playground storage shapes -- domain dataclasses, distinct from the generated
Pydantic wire models in ``api/models/_generated.py`` (mirrors ``contracts/kpi.py``'s split:
one dataclass per resource, converted to/from DB rows by ``playground/store.py`` and to/from
the generated models by ``api/routers/playground.py``).

Everything here is pure data plus the validation the spec itself states (the 730-day custom
range cap, an identifier that must be a real vocabulary term where one applies). No I/O, so
the sync/async rule does not apply.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Optional, Tuple

__all__ = [
    "MAX_CUSTOM_RANGE_DAYS",
    "ChatFilters",
    "ChatMode",
    "Period",
    "PlaygroundPreferences",
    "Report",
    "ReportItem",
    "SavedPrompt",
]

#: ``playground.yaml``'s own rule: "A custom range is capped at 730 days."
MAX_CUSTOM_RANGE_DAYS = 730


class Period(str, Enum):
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"
    TWO_YEARS = "two_years"
    CUSTOM = "custom"


class ChatMode(str, Enum):
    """The answer-format preference -- verbosity, not grounding scope. Distinct from
    ``playground.modes.PlaygroundMode``, which is the four-way grounding axis this same
    package also routes on; the two are orthogonal and the OpenAPI contract only names
    this one (``schemas/playground.yaml#ChatMode``)."""

    FULL = "full"
    BRIEF = "brief"


@dataclass(frozen=True)
class SavedPrompt:
    id: str
    owner: str
    text: str
    label: str = ""
    saved_at: datetime = field(default_factory=lambda: datetime.now())
    last_used_at: Optional[datetime] = None
    usage_count: int = 0


@dataclass(frozen=True)
class ChatFilters:
    """One per owner -- upserted, never listed. ``period="custom"`` requires both dates and
    a span within :data:`MAX_CUSTOM_RANGE_DAYS`; the spec's own 422 example names the reason."""

    owner: str
    period: Period = Period.WEEK
    from_date: Optional[date] = None
    to_date: Optional[date] = None

    def __post_init__(self) -> None:
        if self.period is Period.CUSTOM:
            if self.from_date is None or self.to_date is None:
                raise ValueError("period='custom' requires both from_date and to_date")
            if self.to_date < self.from_date:
                raise ValueError("to_date must not precede from_date")
            span = (self.to_date - self.from_date).days
            if span > MAX_CUSTOM_RANGE_DAYS:
                raise ValueError(
                    f"Custom range is limited to {MAX_CUSTOM_RANGE_DAYS} days, got {span}."
                )


@dataclass(frozen=True)
class PlaygroundPreferences:
    """One per owner -- upserted, never listed."""

    owner: str
    mode: ChatMode = ChatMode.FULL


@dataclass(frozen=True)
class ReportItem:
    id: str
    report_id: str
    block: dict
    pinned_at: datetime
    source_question: str = ""
    run_id: Optional[str] = None
    use_case_id: Optional[str] = None


@dataclass(frozen=True)
class Report:
    id: str
    owner: str
    title: str
    created_at: datetime
    updated_at: datetime
    items: Tuple[ReportItem, ...] = ()
