"""Run real formulas through the KPI formula shape and report what does not fit.

The delivery plan rates the KPI formula shape the least likely of the five contracts to
survive P0, and names four places it expects to break: whether the formula carries its own
grain, how multiple filter parameters work, how time windows are expressed, and whether a
run recomputes or appends. A review meeting cannot settle any of those. Feeding the shape
formulas somebody already wrote can.

The corpus is the nineteen formulas from the ``ecom_graphrag_poc`` semantic layer, vendored
at ``_fixtures/poc_kpis.json``. That layer was built on synthetic ecommerce data, which is
the right corpus for this question and the wrong one for the semantic read API's payloads:
the question here is whether the shape can *express* a formula somebody wrote in earnest,
not whether the numbers describe a real business.

Every check below is a property of the formula text or its declared metadata. None is a
judgment, so re-running this after a shape change tells you what the change bought.

Run it: ``python cookbook/94_retail/K11_kpi_formula_shape.py``
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Mapping, Sequence, Tuple

from egpt_retail.contracts.kpi import (
    _POSITIONAL_RE,
    Dialect,
    KpiDefinition,
    KpiParameter,
    ParameterKind,
    ResultShape,
    bound_parameter_names,
    kpi_identifier,
    referenced_tables,
)

__all__ = [
    "Finding",
    "Rule",
    "Severity",
    "migrate_corpus",
    "migrate_poc_record",
    "check_corpus",
    "check_formula",
    "load_poc_corpus",
    "aload_poc_corpus",
]

FIXTURE = Path(__file__).parent / "_fixtures" / "poc_kpis.json"

#: SQLite-only constructs. The warehouse is PostgreSQL (D5), so each is a translation the
#: definition owes before Lane R can execute it.
_SQLITE_ONLY = (
    (re.compile(r"\bjulianday\s*\(", re.IGNORECASE), "julianday()"),
    (re.compile(r"\bstrftime\s*\(", re.IGNORECASE), "strftime()"),
    (re.compile(r"\bdate\s*\(\s*'now'", re.IGNORECASE), "date('now', ...)"),
)

#: A lookback written into the formula text rather than declared.
_BAKED_WINDOW = re.compile(
    r"'now'|CURRENT_DATE|CURRENT_TIMESTAMP|NOW\s*\(\s*\)", re.IGNORECASE
)

#: ``x * <n> / NULLIF(x, ...)`` -- the same column on both sides of a ratio, which cancels
#: to a constant. Narrow by design: it catches a specific arithmetic mistake rather than
#: attempting to judge whether a formula computes what its description claims.
_SELF_CANCELLING = re.compile(
    r"\b(\w+)\s*\*\s*[\d.]+\s*/\s*NULLIF\s*\(\s*\1\b",
    re.IGNORECASE,
)

_LIMIT = re.compile(r"\bLIMIT\s+\d+", re.IGNORECASE)
_ORDER_BY = re.compile(r"\bORDER\s+BY\b", re.IGNORECASE)


class Severity(str, Enum):
    """How much a finding actually costs.

    The distinction matters more than the count. ``INCOMPATIBLE`` means the shape and the
    formula genuinely disagree and one of them has to move. ``METADATA_GAP`` means the shape
    asks for something the corpus simply never recorded -- filling it in is an afternoon of
    hand-authoring, not a contract defect. ``RECORDED`` means the formula fits and the shape
    has surfaced a property of it that was previously invisible, which is the shape earning
    its keep rather than failing.
    """

    INCOMPATIBLE = "incompatible"
    METADATA_GAP = "metadata_gap"
    RECORDED = "recorded"


class Rule(str, Enum):
    """What was checked. One per way the shape and a real formula can disagree."""

    POSITIONAL_PARAMETER = "positional_parameter"
    UNDECLARED_PARAMETER = "undeclared_parameter"
    PARAMETER_NAME_MISMATCH = "parameter_name_mismatch"
    MISSING_GRAIN = "missing_grain"
    MISSING_BUSINESS_AREA = "missing_business_area"
    SOURCE_TABLES_DRIFT = "source_tables_drift"
    FOREIGN_DIALECT = "foreign_dialect"
    BAKED_TIME_WINDOW = "baked_time_window"
    ROWSET_NOT_METRIC = "rowset_not_metric"
    SELF_CANCELLING_RATIO = "self_cancelling_ratio"


#: What each rule costs. Kept beside the rules so a new rule cannot be added without
#: someone deciding whether it is a contract defect or an observation.
_SEVERITY: Mapping[Rule, Severity] = {
    Rule.POSITIONAL_PARAMETER: Severity.INCOMPATIBLE,
    Rule.UNDECLARED_PARAMETER: Severity.INCOMPATIBLE,
    Rule.SOURCE_TABLES_DRIFT: Severity.INCOMPATIBLE,
    Rule.PARAMETER_NAME_MISMATCH: Severity.INCOMPATIBLE,
    Rule.MISSING_GRAIN: Severity.METADATA_GAP,
    Rule.MISSING_BUSINESS_AREA: Severity.METADATA_GAP,
    Rule.FOREIGN_DIALECT: Severity.RECORDED,
    Rule.BAKED_TIME_WINDOW: Severity.RECORDED,
    Rule.ROWSET_NOT_METRIC: Severity.RECORDED,
    Rule.SELF_CANCELLING_RATIO: Severity.RECORDED,
}


@dataclass(frozen=True)
class Finding:
    """One formula failing one rule."""

    concept: str
    kpi_name: str
    rule: Rule
    detail: str

    @property
    def severity(self) -> Severity:
        return _SEVERITY[self.rule]

    @property
    def blocks_construction(self) -> bool:
        """Whether :class:`~egpt_retail.contracts.kpi.KpiDefinition` would refuse to build.

        True for a metadata gap as well as an incompatibility -- the shape requires a grain
        either way. The severity is what says whether that refusal is expensive.
        """
        return self.severity is not Severity.RECORDED


def load_poc_corpus(path: Path = FIXTURE) -> Mapping[str, Sequence[Mapping[str, Any]]]:
    """The vendored POC formulas, keyed by the concept they hang off."""
    with path.open() as handle:
        corpus: Dict[str, List[Dict[str, Any]]] = json.load(handle)
    return corpus


async def aload_poc_corpus(
    path: Path = FIXTURE,
) -> Mapping[str, Sequence[Mapping[str, Any]]]:
    """Async variant. The read happens off the event loop."""
    return await asyncio.to_thread(load_poc_corpus, path)


def _split_top_level(text: str) -> List[str]:
    """Split on commas that are not inside parentheses.

    A naive ``str.split(",")`` counts ``NULLIF(x, 0)`` as two projected columns and reports
    a scalar metric as a row listing, which is the kind of false positive that gets a whole
    harness ignored.
    """
    parts: List[str] = []
    depth = 0
    current: List[str] = []
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))
    return [part for part in parts if part.strip()]


def _declared_result_shape(sql: str) -> Tuple[bool, str]:
    """Whether the formula returns rows rather than one number, and why."""
    if _LIMIT.search(sql):
        return True, "carries a LIMIT, so it returns a listing rather than one value"
    if _ORDER_BY.search(sql):
        return True, "carries an ORDER BY, which only means something for a listing"
    # A projection naming more than the aggregate, under a GROUP BY, is a row per group.
    if re.search(r"\bGROUP\s+BY\b", sql, re.IGNORECASE):
        upper = sql.upper()
        head = sql[: upper.index("FROM")] if "FROM" in upper else sql
        head = re.sub(r"^\s*SELECT\s+", "", head, flags=re.IGNORECASE)
        projected = _split_top_level(head)
        if len(projected) > 1:
            return (
                True,
                f"projects {len(projected)} columns under a GROUP BY, so it returns one row per group",
            )
    return False, ""


def check_formula(concept: str, record: Mapping[str, Any]) -> List[Finding]:
    """Every way one POC formula disagrees with the shape."""
    name = str(record.get("name", "<unnamed>"))
    sql = str(record.get("formula", ""))
    findings: List[Finding] = []

    def add(rule: Rule, detail: str) -> None:
        findings.append(
            Finding(concept=concept, kpi_name=name, rule=rule, detail=detail)
        )

    positional = _POSITIONAL_RE.findall(sql)
    if positional:
        add(
            Rule.POSITIONAL_PARAMETER,
            f"{len(positional)} positional '?' marker(s); nothing can check arity or meaning",
        )

    used = bound_parameter_names(sql)
    declared_name = record.get("param")
    declared = {str(declared_name)} if declared_name else set()

    if used - declared:
        add(
            Rule.UNDECLARED_PARAMETER,
            f"formula uses {sorted(used - declared)} with no declaration",
        )
    if declared - used and not positional:
        add(
            Rule.UNDECLARED_PARAMETER,
            f"declares {sorted(declared - used)} the formula never uses",
        )
    if declared and positional and not used:
        add(
            Rule.PARAMETER_NAME_MISMATCH,
            f"declares param {sorted(declared)!r} but binds positionally, so the name is decorative",
        )

    if "grain" not in record:
        add(
            Rule.MISSING_GRAIN,
            "no grain; it is implied by the concept key and cannot be read off the definition",
        )
    if "business_area" not in record:
        add(
            Rule.MISSING_BUSINESS_AREA,
            "no business area; the discovery screen groups by it",
        )

    declared_tables = {str(t).lower() for t in record.get("source_tables", [])}
    read = referenced_tables(sql)
    undeclared_reads = read - declared_tables
    if undeclared_reads:
        add(
            Rule.SOURCE_TABLES_DRIFT,
            f"reads {sorted(undeclared_reads)} which source_tables omits; feasibility would report ready",
        )
    unread_declares = declared_tables - read
    if unread_declares:
        add(
            Rule.SOURCE_TABLES_DRIFT,
            f"declares {sorted(unread_declares)} the formula never reads; feasibility would block on a table it does not need",
        )

    for pattern, label in _SQLITE_ONLY:
        if pattern.search(sql):
            add(Rule.FOREIGN_DIALECT, f"uses {label}, which PostgreSQL does not have")

    if _BAKED_WINDOW.search(sql):
        add(
            Rule.BAKED_TIME_WINDOW,
            "lookback is written against wall-clock time, so the run is not reproducible and cannot be backfilled",
        )

    is_rowset, why = _declared_result_shape(sql)
    if is_rowset:
        add(Rule.ROWSET_NOT_METRIC, why)

    match = _SELF_CANCELLING.search(sql)
    if match:
        add(
            Rule.SELF_CANCELLING_RATIO,
            f"{match.group(1)!r} appears on both sides of a ratio, so the expression reduces to a constant",
        )

    return findings


def check_corpus(corpus: Mapping[str, Sequence[Mapping[str, Any]]]) -> List[Finding]:
    """Every finding across every formula, in corpus order."""
    findings: List[Finding] = []
    for concept, records in corpus.items():
        for record in records:
            findings.extend(check_formula(concept, record))
    return findings


def corpus_size(corpus: Mapping[str, Sequence[Mapping[str, Any]]]) -> int:
    """How many formulas were checked."""
    return sum(len(records) for records in corpus.values())


def formulas_clearing_the_shape(
    corpus: Mapping[str, Sequence[Mapping[str, Any]]],
) -> FrozenSet[str]:
    """The formulas that would construct as a :class:`KpiDefinition` untouched."""
    blocked = {
        f"{f.concept}.{f.kpi_name}"
        for f in check_corpus(corpus)
        if f.blocks_construction
    }
    every = {f"{concept}.{r['name']}" for concept, rs in corpus.items() for r in rs}
    return frozenset(every - blocked)


# --- Migration -------------------------------------------------------------------------
#
# What it takes to move the POC corpus into the shape. Split deliberately into the part a
# script can do and the part it cannot, because that split is the actual cost estimate.

#: The grain each POC concept implies, as terms from the shipped ``grain`` vocabulary.
#: Recovered by hand, because the POC recorded grain nowhere -- which is precisely the
#: finding. These maps are POC-specific on purpose: they exist to move one corpus into the
#: shape, and generalising them would be inventing a mapping nobody needs.
#:
#: Note what is *not* here. The POC had ``StockLevel`` and ``WarehouseZone`` grains, which
#: are artifacts of one synthetic ecommerce dataset rather than facts about retail. They map
#: onto shipped terms instead of becoming shipped terms; a deployment that genuinely needs
#: them adds them by overlay.
_CONCEPT_GRAIN: Mapping[str, str] = {
    "CustomerSegment": "customer_segment",
    "Product": "product",
    "Category": "product_category",
    "Supplier": "supplier",
    "StockLevel": "product",
}

_CONCEPT_AREA: Mapping[str, str] = {
    "CustomerSegment": "customer",
    "Product": "product",
    "Category": "product",
    "Supplier": "supply_chain",
    "StockLevel": "inventory",
}

#: What the single positional ``?`` in each concept's formulas actually binds to. Read off
#: the surrounding SQL by hand -- which is the argument against positional markers, since
#: this is exactly the information ``?`` throws away.
_CONCEPT_BIND: Mapping[str, str] = {
    "Product": "product_id",
    "Category": "category_id",
    "Supplier": "supplier_id",
    "StockLevel": "stock_level",
}


def migrate_poc_record(concept: str, record: Mapping[str, Any]) -> KpiDefinition:
    """Move one POC formula into the shape, doing only what is mechanical.

    Positional markers become named parameters and the missing metadata is filled from the
    concept. Dialect translation and de-baking a time window are deliberately *not* done:
    both are rewrites of the SQL's meaning, and a migration script that silently rewrote
    them would hide the one cost worth reporting.
    """
    name = str(record["name"])
    sql = str(record["formula"])
    parameters: List[KpiParameter] = []

    declared = record.get("param")
    if "{" in sql and declared:
        parameters.append(
            KpiParameter(
                name=str(declared),
                kind=ParameterKind.PREDICATE,
                description="Scoping filter carried on the concept.",
            )
        )
    elif _POSITIONAL_RE.search(sql):
        bind = _CONCEPT_BIND[concept]
        sql = _POSITIONAL_RE.sub("{" + bind + "}", sql, count=1)
        parameters.append(
            KpiParameter(
                name=bind,
                kind=ParameterKind.SCALAR,
                description=f"The {concept} this KPI is computed for.",
            )
        )

    is_rowset, _ = _declared_result_shape(sql)
    dialect = Dialect.POSTGRES
    for pattern, _label in _SQLITE_ONLY:
        if pattern.search(sql):
            dialect = Dialect.SQLITE
            break

    return KpiDefinition(
        identifier=kpi_identifier(concept, name),
        name=name,
        description=str(record.get("description", "")),
        sql=sql,
        grain=_CONCEPT_GRAIN[concept],
        business_area=_CONCEPT_AREA[concept],
        source_tables=frozenset(
            str(t).lower() for t in record.get("source_tables", [])
        ),
        result_shape=ResultShape.ROWSET if is_rowset else ResultShape.SCALAR,
        dialect=dialect,
        parameters=tuple(parameters),
        concept=concept,
    )


def migrate_corpus(
    corpus: Mapping[str, Sequence[Mapping[str, Any]]],
) -> List[KpiDefinition]:
    """The whole POC corpus as KPI definitions, in corpus order."""
    return [
        migrate_poc_record(concept, record)
        for concept, records in corpus.items()
        for record in records
    ]
