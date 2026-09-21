"""A KPI formula read into the parts a decomposition needs (K25).

The registry's gold formulas share one shape -- ``SELECT [grain,] <expr> AS value FROM
<table> WHERE <predicates on {as_of}> [GROUP BY grain] [ORDER BY ...]`` -- and this module
reads that shape into its **table**, its **window predicate**, and its **type and parts**:

* *ratio* -- ``100.0 * A / NULLIF(B, 0)`` (a rate, scaled to percent), ``A / NULLIF(B, 0)``
  (a plain ratio), or ``AVG(x)`` (``SUM(x)`` over ``COUNT(x)``): a numerator and a
  denominator, each an aggregate the fact's rows can be grouped under;
* *additive* -- ``COUNT(*)``, ``SUM(x)`` and the like: one aggregate, grouped as it is.

This is the "Made of" of a KPI (the audit panel's numerator / denominator), derived once
from the formula. A formula outside the shape raises :class:`FormulaShapeError`, and the
readiness check says the KPI cannot be diagnosed, rather than guessing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Tuple

__all__ = ["FormulaParts", "FormulaShapeError", "parts_of"]


class FormulaShapeError(ValueError):
    """The formula is not one the decomposition can read."""


@dataclass(frozen=True)
class FormulaParts:
    table: str
    where: str
    kind: str  # "ratio" | "additive"
    numerator: str
    denominator: Optional[str]
    scale: float
    grain_column: Optional[str]
    #: For a filtered-count ratio, the numerator's own predicate (the "won" side) -- what
    #: the evidence query negates to find the losing side.
    numerator_predicate: Optional[str]
    #: The column an AVG/SUM measures, when there is one (the evidence sorts by it).
    measure_column: Optional[str]
    #: Decimal places the formula's ROUND keeps -- what gold stores and the report shows;
    #: the headline is rounded to it so the diagnosis and the KPI tile agree.
    precision: int = 1


_SHAPE = re.compile(
    r"^\s*SELECT\s+(?:(?P<grain>[A-Za-z_][\w.]*)\s*,\s*)?(?P<expr>.+?)\s+AS\s+value\s+"
    r"FROM\s+(?P<table>[A-Za-z_][\w.]*)\s+WHERE\s+(?P<where>.+?)"
    r"(?:\s+GROUP\s+BY\s+.+?)?(?:\s+ORDER\s+BY\s+.+?)?\s*;?\s*$",
    re.IGNORECASE | re.DOTALL,
)
_ROUND = re.compile(r"^\s*ROUND\s*\(", re.IGNORECASE)
_NULLIF_RATIO = re.compile(
    r"^\s*(?:(?P<scale>\d+(?:\.\d+)?)\s*\*\s*)?(?P<num>.+?)\s*/\s*NULLIF\s*\(\s*(?P<den>.+)\s*,\s*0\s*\)\s*$",
    re.IGNORECASE | re.DOTALL,
)
_AVG = re.compile(r"^\s*AVG\s*\(\s*(?P<col>[A-Za-z_][\w.]*)\s*\)\s*$", re.IGNORECASE)
_FILTERED_COUNT = re.compile(
    r"^\s*COUNT\s*\(\s*\*\s*\)\s+FILTER\s*\(\s*WHERE\s+(?P<pred>.+)\)\s*$",
    re.IGNORECASE | re.DOTALL,
)
_SUM = re.compile(r"^\s*SUM\s*\(\s*(?P<col>[A-Za-z_][\w.]*)\s*\)\s*$", re.IGNORECASE)


def _round_places(expr: str) -> Optional[int]:
    """``k`` of an outer ``ROUND(<inner>, k)``, else None."""
    if not _ROUND.match(expr):
        return None
    match = re.search(r",\s*(\d+)\s*\)\s*$", expr)
    return int(match.group(1)) if match else 0


def _strip_round(expr: str) -> str:
    """``ROUND(<inner>, k)`` -> ``<inner>``; anything else unchanged. Rounding is display."""
    if not _ROUND.match(expr):
        return expr.strip()
    start = expr.index("(") + 1
    depth, last_comma = 0, None
    for i in range(start, len(expr)):
        ch = expr[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            if depth == 0:
                inner = expr[start:i]
                return (inner[:last_comma] if last_comma is not None else inner).strip()
            depth -= 1
        elif ch == "," and depth == 0:
            last_comma = i - start
    raise FormulaShapeError("unbalanced ROUND(...) in formula")


def _balanced(text: str) -> bool:
    depth = 0
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _ratio(expr: str) -> Optional[Tuple[str, str, float]]:
    match = _NULLIF_RATIO.match(expr)
    if not match:
        return None
    num, den = match.group("num").strip(), match.group("den").strip()
    if not (_balanced(num) and _balanced(den)):
        return None
    return num, den, float(match.group("scale") or 1)


def parts_of(sql: str) -> FormulaParts:
    """Read a gold formula into its parts, or raise :class:`FormulaShapeError`."""
    match = _SHAPE.match(sql or "")
    if not match:
        raise FormulaShapeError(
            "the formula is not 'SELECT <expr> AS value FROM <table> WHERE <predicates>'"
        )
    table, where = match.group("table"), match.group("where").strip()
    grain = match.group("grain")
    places = _round_places(match.group("expr").strip())
    expr = _strip_round(match.group("expr"))
    if "{as_of}" not in where:
        raise FormulaShapeError("the formula's WHERE does not read {as_of}")

    ratio = _ratio(expr)
    if ratio:
        num, den, scale = ratio
        filtered = _FILTERED_COUNT.match(num)
        return FormulaParts(
            table=table,
            where=where,
            kind="ratio",
            numerator=num,
            denominator=den,
            scale=scale,
            grain_column=grain,
            numerator_predicate=filtered.group("pred").strip() if filtered else None,
            measure_column=None,
            precision=places if places is not None else 1,
        )
    avg = _AVG.match(expr)
    if avg:
        col = avg.group("col")
        return FormulaParts(
            table=table,
            where=where,
            kind="ratio",
            numerator=f"SUM({col})",
            denominator=f"COUNT({col})",
            scale=1.0,
            grain_column=grain,
            numerator_predicate=None,
            measure_column=col,
            precision=places if places is not None else 1,
        )
    total = _SUM.match(expr)
    return FormulaParts(
        table=table,
        where=where,
        kind="additive",
        numerator=expr,
        denominator=None,
        scale=1.0,
        grain_column=grain,
        numerator_predicate=None,
        measure_column=total.group("col") if total else None,
        precision=places if places is not None else 0,
    )
