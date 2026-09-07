"""The KPI formula shape -- contract between Lane K and Lane R.

Lane K owns the definition, its feasibility and its suggestion. Lane R owns nightly
materialisation. This module is the seam: the point where a definition becomes something a
scheduled job can execute against the warehouse.

Everything here is pure. There is no I/O, so the repository's sync/async rule does not
apply (see the construction exemption in ``CLAUDE.md``); the async variants live on the
semantic read API, which is what actually reads a snapshot.

Two rules from the alignment table are enforced structurally rather than by convention:

* **Feasibility is a set difference.** :func:`assess_feasibility` takes a formula and the
  set of tables a snapshot holds and subtracts one from the other. It accepts no model, no
  client and no callable, so there is no seam through which a judgment could enter.
* **Values come from closed sets.** Every field that a proposing agent may fill is an
  :class:`~enum.Enum`, so an agent that invents a grain fails to parse instead of producing
  a plausible-sounding wrong answer.

Validated in P0 against the nineteen formulas in the ``ecom_graphrag_poc`` semantic layer;
:mod:`egpt_retail.contracts.kpi_check` is that harness, and ``CHANGELOG.md`` records what
the validation changed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import (
    TYPE_CHECKING,
    FrozenSet,
    Iterator,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

if TYPE_CHECKING:
    from egpt_retail.contracts.vocabulary import VocabularySet

__all__ = [
    "CONTRACT_VERSION",
    "Dialect",
    "Feasibility",
    "FeasibilityVerdict",
    "KpiDefinition",
    "KpiRegistry",
    "KpiParameter",
    "ParameterKind",
    "ResultShape",
    "TimeWindow",
    "TimeWindowUnit",
    "assess_feasibility",
    "enums_match_vocabulary",
    "bound_parameter_names",
    "referenced_tables",
]

#: Bumped on any breaking change, per the change protocol. Additive changes do not bump it.
CONTRACT_VERSION = "2.0.0"

#: ``{name}`` placeholders. Deliberately not ``?``: a positional marker carries no name, so
#: nothing can check that the arguments supplied match the arguments a formula expects.
_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")

#: Positional markers, matched only so the shape can reject them with a useful message.
_POSITIONAL_RE = re.compile(r"(?<![:\w])\?(?!\w)")

#: ``FROM x`` / ``JOIN x``. Crude on purpose -- it exists to cross-check a hand-authored
#: ``source_tables`` against the formula text, not to be a SQL parser. The guard in R6 is
#: what actually parses statements before execution.
_TABLE_RE = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)


class ResultShape(str, Enum):
    """Whether the formula yields one number or a set of rows.

    Stays an enum, unlike ``grain`` and ``business_area``: a customer overlay can add a
    business area we did not think of, but it cannot invent a third way for a SQL statement
    to return. Mirrored in the shipped ``result_shape`` vocabulary so a prompt can render it
    from the same place as everything else; :func:`enums_match_vocabulary` checks the two agree.

    ``SCALAR`` is a metric: one value per grain per run, which is what a dashboard tile, a
    threshold comparison (R12) and a period-over-period delta all assume. ``ROWSET`` is a
    listing -- useful to a human, but an alert cannot be evaluated against it and a trend
    line cannot be drawn from it.
    """

    SCALAR = "scalar"
    ROWSET = "rowset"


class Dialect(str, Enum):
    """The SQL dialect a formula is written in.

    Present because the warehouse is PostgreSQL (D5) while definitions may be authored
    elsewhere. A formula tagged with a foreign dialect is a translation task, and naming it
    is what stops it being discovered at two in the morning during materialisation.
    """

    POSTGRES = "postgres"
    SQLITE = "sqlite"


class ParameterKind(str, Enum):
    """What a placeholder in a formula stands for.

    ``PREDICATE`` is the scoping filter carried on the concept itself -- an entire boolean
    SQL fragment such as ``c.age BETWEEN 18 AND 25``, interpolated into the ``WHERE``
    clause. It is the mechanism the semantic layer uses to turn a concept into rows, and it
    is textual by nature, so it can never come from user input; it comes from an accepted
    proposal and nowhere else.

    ``SCALAR`` is an ordinary bound value -- an identifier, a threshold. It is passed to the
    driver as a parameter and never interpolated.
    """

    PREDICATE = "predicate"
    SCALAR = "scalar"


class TimeWindowUnit(str, Enum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"


@dataclass(frozen=True)
class TimeWindow:
    """A declared lookback, relative to the run's as-of date.

    Declared rather than written into the formula text. A window baked in as ``now`` minus
    an interval computes against wall-clock time, so the same formula run twice gives two
    answers, yesterday's number cannot be recomputed, and a backfill is impossible. Lane R
    binds the as-of date at execution; the definition only says how far back to look.
    """

    length: int
    unit: TimeWindowUnit

    def __post_init__(self) -> None:
        if self.length <= 0:
            raise ValueError(f"time window length must be positive, got {self.length}")


@dataclass(frozen=True)
class KpiParameter:
    """One named placeholder a formula expects."""

    name: str
    kind: ParameterKind
    description: str = ""

    def __post_init__(self) -> None:
        if not self.name.isidentifier():
            raise ValueError(f"parameter name must be an identifier, got {self.name!r}")


@dataclass(frozen=True)
class KpiDefinition:
    """One KPI, authoritative in Postgres and mirrored as a graph node (K11).

    Construction validates the definition against itself: every placeholder in the SQL is
    declared, every declared parameter appears, and every table the SQL reads is listed in
    ``source_tables``. That last check is the one that matters most, because
    ``source_tables`` is the input to feasibility -- if it drifts from the formula text, the
    ready and blocked badges quietly start lying.
    """

    identifier: str
    name: str
    description: str
    sql: str
    #: A term from the ``grain`` vocabulary. A plain string rather than an enum because the
    #: permitted set is the shipped ontology plus the customer's overlay (D9, G10), which a
    #: frozen enum cannot express. Validated by :meth:`KpiRegistry.add`, never by guesswork.
    grain: str
    #: A term from the ``business_area`` vocabulary. Same reasoning.
    business_area: str
    source_tables: FrozenSet[str]
    result_shape: ResultShape = ResultShape.SCALAR
    dialect: Dialect = Dialect.POSTGRES
    parameters: Tuple[KpiParameter, ...] = ()
    time_window: Optional[TimeWindow] = None
    #: The semantic snapshot version this definition was last validated against (K6). A
    #: harvest that changes a column this formula reads moves it to review rather than
    #: letting it fail during the nightly run.
    validated_against_snapshot: Optional[str] = None
    concept: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.identifier.isidentifier():
            raise ValueError(
                f"identifier must be an identifier, got {self.identifier!r}"
            )
        if not self.sql.strip():
            raise ValueError(f"{self.identifier}: sql is empty")

        positional = _POSITIONAL_RE.findall(self.sql)
        if positional:
            raise ValueError(
                f"{self.identifier}: formula uses {len(positional)} positional '?' marker(s). "
                "Use named {placeholders} with a declared KpiParameter, so arity and meaning "
                "can be checked before execution."
            )

        used = bound_parameter_names(self.sql)
        declared = {p.name for p in self.parameters}
        if used - declared:
            raise ValueError(
                f"{self.identifier}: formula uses undeclared parameter(s) {sorted(used - declared)}"
            )
        if declared - used:
            raise ValueError(
                f"{self.identifier}: declares parameter(s) {sorted(declared - used)} the formula never uses"
            )

        if not self.source_tables:
            raise ValueError(
                f"{self.identifier}: source_tables is empty, so feasibility cannot be computed"
            )
        read = referenced_tables(self.sql)
        if read - set(self.source_tables):
            raise ValueError(
                f"{self.identifier}: formula reads {sorted(read - set(self.source_tables))} "
                "which source_tables does not list. Feasibility would report ready while the "
                "formula is missing a table."
            )

    @property
    def predicate_parameters(self) -> Tuple[KpiParameter, ...]:
        """The parameters carrying a SQL fragment rather than a value."""
        return tuple(p for p in self.parameters if p.kind is ParameterKind.PREDICATE)


class FeasibilityVerdict(str, Enum):
    """Rendered directly by the discovery screen, so the values are the badge labels.

    An enum for the same reason as :class:`ResultShape`: a set difference has exactly three
    outcomes, and no customer's business adds a fourth.
    """

    READY = "ready"
    PARTIAL = "partial"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class Feasibility:
    """The result of a set difference, not an opinion."""

    kpi_identifier: str
    verdict: FeasibilityVerdict
    missing_tables: FrozenSet[str] = field(default_factory=frozenset)

    @property
    def rationale(self) -> str:
        if self.verdict is FeasibilityVerdict.READY:
            return "Every source table is present in the semantic snapshot."
        missing = ", ".join(sorted(self.missing_tables))
        return f"Missing from the semantic snapshot: {missing}."


def bound_parameter_names(sql: str) -> FrozenSet[str]:
    """The ``{name}`` placeholders a formula uses."""
    return frozenset(_PLACEHOLDER_RE.findall(sql))


def referenced_tables(sql: str) -> FrozenSet[str]:
    """The tables a formula reads, by scanning ``FROM`` and ``JOIN``.

    Subquery aliases are not table names, so anything that never appears after ``FROM`` or
    ``JOIN`` is absent by construction. The result is a floor on what the formula touches,
    which is the right direction to be wrong in: it can under-report a table the declared
    ``source_tables`` also omits, but it cannot invent one.
    """
    return frozenset(match.lower() for match in _TABLE_RE.findall(sql))


def assess_feasibility(
    kpi: KpiDefinition, available_tables: FrozenSet[str]
) -> Feasibility:
    """Subtract what the snapshot has from what the formula needs.

    Takes data and returns data. No model, no client, no callable -- the guarantee behind
    the ready and blocked badges is that this function has nothing to be persuaded by.

    ``PARTIAL`` means some but not all source tables are present: the KPI cannot be
    computed, but the gap is one table rather than the whole subject area, which is the
    distinction a customer deciding what to connect next actually needs.
    """
    needed = {t.lower() for t in kpi.source_tables}
    have = {t.lower() for t in available_tables}
    missing = needed - have
    if not missing:
        verdict = FeasibilityVerdict.READY
    elif missing == needed:
        verdict = FeasibilityVerdict.BLOCKED
    else:
        verdict = FeasibilityVerdict.PARTIAL
    return Feasibility(
        kpi_identifier=kpi.identifier,
        verdict=verdict,
        missing_tables=frozenset(missing),
    )


def assess_registry(
    kpis: Sequence[KpiDefinition],
    available_tables: FrozenSet[str],
) -> Mapping[str, Feasibility]:
    """Feasibility for a whole registry, keyed by KPI identifier."""
    return {kpi.identifier: assess_feasibility(kpi, available_tables) for kpi in kpis}


def kpi_identifier(concept: str, name: str) -> str:
    """A stable identifier for a KPI, from the concept it hangs off and its name."""
    return f"{concept.lower()}__{name.lower()}"


@dataclass(frozen=True)
class KpiRegistry:
    """A validated collection of KPI definitions.

    :class:`KpiDefinition` validates itself -- parameters declared, source tables listed --
    but it cannot validate its ``grain`` and ``business_area``, because the permitted terms
    come from the deployment's ontology and a dataclass has no business reaching for global
    state to find them. The registry is where those two meet, and it is the only sanctioned
    way to hold definitions: a bare ``KpiDefinition`` is self-consistent but its terms are
    unchecked until it enters one.

    Construction is pure, so the sync/async rule does not apply; what does I/O is loading the
    vocabularies, which has both variants.
    """

    definitions: Tuple[KpiDefinition, ...]
    vocabularies: "VocabularySet"

    def __post_init__(self) -> None:
        seen = set()
        for kpi in self.definitions:
            if kpi.identifier in seen:
                raise ValueError(f"duplicate KPI identifier {kpi.identifier!r}")
            seen.add(kpi.identifier)
            self.vocabularies.validate("grain", kpi.grain)
            self.vocabularies.validate("business_area", kpi.business_area)

    @classmethod
    def build(
        cls,
        definitions: Sequence[KpiDefinition],
        vocabularies: "VocabularySet",
    ) -> "KpiRegistry":
        """Validate every definition's terms against what this deployment actually holds."""
        return cls(definitions=tuple(definitions), vocabularies=vocabularies)

    def __iter__(self) -> "Iterator[KpiDefinition]":
        return iter(self.definitions)

    def __len__(self) -> int:
        return len(self.definitions)

    def by_area(self, business_area: str) -> Tuple[KpiDefinition, ...]:
        """Every KPI in one business area. The discovery screen groups by this."""
        self.vocabularies.validate("business_area", business_area)
        return tuple(
            kpi for kpi in self.definitions if kpi.business_area == business_area
        )

    def feasibility(
        self, available_tables: FrozenSet[str]
    ) -> Mapping[str, Feasibility]:
        """Feasibility across the whole registry. Still a set difference."""
        return assess_registry(self.definitions, available_tables)


def enums_match_vocabulary(vocabularies: "VocabularySet") -> Mapping[str, bool]:
    """Whether the two enums that stayed enums still agree with the shipped vocabulary.

    ``result_shape`` and ``feasibility`` are defined twice on purpose -- as Python enums,
    because code branches on them, and as vocabulary terms, because prompts render from
    vocabularies. Defined twice means they can drift, so this is the check that they have not.
    """
    return {
        "result_shape": {m.value for m in ResultShape}
        == vocabularies["result_shape"].values,
        "feasibility": {m.value for m in FeasibilityVerdict}
        == vocabularies["feasibility"].values,
    }
