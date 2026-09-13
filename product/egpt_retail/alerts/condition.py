"""The alert condition: a pure comparison, deliberately boring (R12, G7).

Same discipline K11's ``assess_feasibility`` and R6's guard follow: whether a threshold is
crossed decides whether a person gets paged, so it cannot be a judgment call any more than
feasibility can be. This module accepts a value, a threshold and an operator and returns a
boolean and the sentence that explains it -- nothing here reads a database or calls a model.

Pure, so the repository's sync/async rule does not apply -- there is no I/O, so there is
nothing to await (the construction exemption CLAUDE.md and ``contracts/kpi.py`` both name).
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional, Union

from egpt_retail.contracts.kpi import TimeWindow, TimeWindowUnit

__all__ = [
    "ConditionResult",
    "ConditionUnavailable",
    "OPERATOR_SYMBOLS",
    "as_decimal",
    "condition_met",
    "render_rule_sentence",
    "requires_previous_period",
    "shift_date",
]

#: How each operator reads in a rendered rule sentence (the contract's own worked example:
#: ``"supplier__on_time_rate_30d < 0.9 for 2 days"``).
OPERATOR_SYMBOLS = {
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
    "eq": "==",
    "delta_gt": "increased by more than",
    "delta_lt": "decreased by more than",
}

_DELTA_OPERATORS = frozenset({"delta_gt", "delta_lt"})

#: ``eq``'s tolerance, in the KPI's own unit. A gold ``value`` is a ``float()`` cast of a
#: Postgres ``NUMERIC`` column (``executors/alerts.py``'s ``evaluate_alert_condition``); a
#: threshold is the same cast of a ``NUMERIC`` column in ``alert_definitions``
#: (``alerts/store.py``'s ``_row_to_definition``). Comparing the two casts with plain ``==``
#: means an alert on a realistic continuous KPI -- a rate, a percentage, this module's own
#: worked example ``supplier__on_time_rate_30d`` -- essentially never fires: the two casts
#: rarely land on the exact same double even when the underlying decimals agree. Widening the
#: comparison past bit-for-bit float equality needs *some* tolerance; 1e-9 is far smaller than
#: any realistic KPI's meaningful precision (a rate or a percentage cares about the 3rd or 4th
#: decimal place at most) and far larger than the float noise a NUMERIC->float cast introduces,
#: so it closes the gap the cast opens without opening a new one of its own.
#:
#: Sized for a rate or percentage, not a large-magnitude KPI: an absolute ``1e-9`` is below
#: ``float64``'s own precision floor once a value runs into the millions (a revenue total,
#: say), where it adds nothing beyond what plain ``==`` already gave -- not a new bug at that
#: scale, just not the improvement this tolerance is for. ``eq`` on such a KPI inherits the
#: same "essentially never fires" problem this constant exists to fix for smaller ones.
_EQ_TOLERANCE = Decimal("1e-9")


def as_decimal(value: Union[float, int, Decimal, str]) -> Decimal:
    """``value`` as an exact :class:`Decimal`, via its string form.

    Never ``Decimal(a_float)`` directly -- that imports the float's *exact* binary value (a
    ~55-digit decimal expansion essentially unrelated to what anyone typed or what Postgres
    computed), not the decimal it was meant to represent. Going through ``str()`` recovers the
    shortest decimal that round-trips to that float, which is what both a human-entered
    threshold and a ``NUMERIC`` column's ``float()`` cast actually mean.

    Public (not this module's private convention) because R13's dashboard tile delta
    (``dashboards/data.py``) needs the same guarantee against the same class of value and
    should not hand-roll a second copy of it.
    """
    return value if isinstance(value, Decimal) else Decimal(str(value))


class ConditionUnavailable(ValueError):
    """The condition cannot be evaluated -- an unknown operator, or a ``delta_*`` operator
    with no previous-period value to compare against. Distinct from ``met=False``: this is
    "the question could not be asked", not "the answer was no"."""


@dataclass(frozen=True)
class ConditionResult:
    """The outcome of one comparison, and the sentence a screen or a log line can show
    verbatim. Frozen: the whole point is that this is computed once and then only reported."""

    met: bool
    detail: str


def requires_previous_period(operator: str) -> bool:
    """Whether ``operator`` needs a prior-period value (a ``delta_*`` operator) to evaluate."""
    return operator in _DELTA_OPERATORS


def condition_met(
    operator: str,
    threshold: float,
    value: float,
    previous_value: Optional[float] = None,
) -> ConditionResult:
    """Whether ``value`` crosses ``threshold`` under ``operator``.

    Raises :class:`ConditionUnavailable` for an operator this module does not recognise, or
    for a ``delta_*`` operator given no ``previous_value`` -- callers must not report that as
    ``met=False``, which would be indistinguishable from a fired-never-fires. K11's contract
    of never inventing a plausible value for missing data applies to a threshold check
    exactly as it applies to a formula: an unanswerable comparison must say so, not guess "no".

    ``eq`` compares as :class:`~decimal.Decimal`, via each operand's own decimal string, and
    tolerates a difference of ``1e-9`` (:data:`_EQ_TOLERANCE`) rather than requiring bit-for-bit
    float equality -- see that constant's own comment for why plain ``==`` on two independent
    ``NUMERIC``-to-``float`` casts essentially never fires for a continuous KPI. An ``eq``
    alert over a pure integer or count KPI (no cast noise to absorb) is unaffected: the
    tolerance is many orders of magnitude below "1 count".
    """
    if operator not in OPERATOR_SYMBOLS:
        raise ConditionUnavailable(
            f"unknown operator {operator!r}; must be one of {sorted(OPERATOR_SYMBOLS)}"
        )
    if requires_previous_period(operator) and previous_value is None:
        raise ConditionUnavailable(
            f"{operator!r} compares against the previous period, but no previous-period "
            "value is available"
        )

    if operator == "gt":
        met = value > threshold
    elif operator == "gte":
        met = value >= threshold
    elif operator == "lt":
        met = value < threshold
    elif operator == "lte":
        met = value <= threshold
    elif operator == "eq":
        met = abs(as_decimal(value) - as_decimal(threshold)) <= _EQ_TOLERANCE
    elif operator == "delta_gt":
        met = (value - previous_value) > threshold  # type: ignore[operator]
    else:  # delta_lt
        met = (value - previous_value) < threshold  # type: ignore[operator]

    symbol = OPERATOR_SYMBOLS[operator]
    if requires_previous_period(operator):
        delta = value - previous_value  # type: ignore[operator]
        detail = (
            f"{value} {symbol} {threshold} against the previous period ({previous_value}); "
            f"delta={delta}"
        )
    else:
        detail = f"{value} {symbol} {threshold}"
    return ConditionResult(met=met, detail=detail)


def render_rule_sentence(
    kpi_identifier: str, operator: str, threshold: float, window: Optional[TimeWindow]
) -> str:
    """The definition rendered as a sentence, frozen at firing time (the contract's own
    ``AlertEvent.rule`` field) -- e.g. ``"supplier__on_time_rate_30d < 0.9 for 2 days"``."""
    symbol = OPERATOR_SYMBOLS[operator]
    sentence = f"{kpi_identifier} {symbol} {threshold}"
    if window is not None:
        unit = (
            window.unit.value
            if isinstance(window.unit, TimeWindowUnit)
            else window.unit
        )
        plural = "s" if window.length != 1 else ""
        sentence += f" for {window.length} {unit}{plural}"
    return sentence


def shift_date(as_of: date, window: TimeWindow) -> date:
    """``as_of`` moved back by one ``window``, for the previous-period value a ``delta_*``
    operator compares against.

    Calendar-aware for month/quarter/year (a 30-day fixed offset would drift against "the
    same day last month" the way a naive ``timedelta`` does); clamped to the shifted month's
    last day when ``as_of`` does not exist there (e.g. Mar 31 minus one month is Feb 28/29).
    """
    if window.unit == TimeWindowUnit.DAY:
        return date.fromordinal(as_of.toordinal() - window.length)
    if window.unit == TimeWindowUnit.WEEK:
        return date.fromordinal(as_of.toordinal() - 7 * window.length)

    months_back = {
        TimeWindowUnit.MONTH: window.length,
        TimeWindowUnit.QUARTER: window.length * 3,
        TimeWindowUnit.YEAR: window.length * 12,
    }[window.unit]
    total_months = (as_of.year * 12 + (as_of.month - 1)) - months_back
    year, month0 = divmod(total_months, 12)
    month = month0 + 1
    day = min(as_of.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)
