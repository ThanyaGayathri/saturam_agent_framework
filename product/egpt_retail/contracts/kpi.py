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
    Callable,
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
    "FeasibilityReason",
    "FeasibilityVerdict",
    "assess_feasibility_with_joins",
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
    "positional_markers",
    "substitute_first_positional",
    "referenced_tables",
]

#: Bumped on any breaking change, per the change protocol. Additive changes do not bump it.
CONTRACT_VERSION = "2.0.0"

#: ``{name}`` placeholders. Deliberately not ``?``: a positional marker carries no name, so
#: nothing can check that the arguments supplied match the arguments a formula expects.
_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")

#: Positional markers, matched only so the shape can reject them with a useful message.
#: The lookbehind excludes ``:?`` (a named-parameter form in some drivers). A marker glued
#: to a preceding *or* following word character is still caught -- ``WHERE a=?AND b=1`` and
#: ``col?`` are both ugly and legal, and missing either would let a positional formula
#: through the one check that refuses them. Only a marker glued on *both* sides is excluded:
#: that shape never occurs in real positional-parameter syntax (a marker always follows an
#: operator, comma, or paren, so at least one side is non-word), but it does occur inside an
#: ordinary string literal -- a URL query string (``'foo.com/page?x=1'``) or free text
#: (``'what?next'``) -- which would otherwise be misread as a positional parameter.
_POSITIONAL_RE = re.compile(r"(?<!:)(?:(?<!\w)\?|\?(?!\w))")

#: ``FROM x`` / ``JOIN x``, with an optional schema qualifier. Crude on purpose -- it exists
#: to cross-check a hand-authored ``source_tables`` against the formula text, not to be a SQL
#: parser. The guard in R6 is what actually parses statements before execution.
#:
#: The schema group matters: without it ``FROM silver.orders`` captured ``silver``, so a
#: schema-qualified formula was refused at construction however its ``source_tables`` was
#: written -- listing ``orders`` failed the drift check, and listing ``silver`` passed it and
#: then left the KPI permanently blocked, because no snapshot contains a table called
#: ``silver``. ``TableRef.schema`` defaults to ``"silver"``, so qualified formulas are the
#: expected case rather than an exotic one.
_TABLE_RE = re.compile(
    r"\b(?:FROM|JOIN)\s+(?:([A-Za-z_][A-Za-z0-9_]*)\.)?([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)


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
        # Normalise before any comparison. ``referenced_tables`` lower-cases what it reads out
        # of the SQL, so a ``source_tables`` entry written as ``"Orders"`` failed the drift
        # check below against a formula that named the same table -- and the refusal blamed the
        # author for omitting a table they had in fact listed.
        object.__setattr__(
            self, "source_tables", frozenset(t.lower() for t in self.source_tables)
        )
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
        if read - self.source_tables:
            raise ValueError(
                f"{self.identifier}: formula reads {sorted(read - self.source_tables)} "
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


class FeasibilityReason(str, Enum):
    """*Why* a KPI is not ready, which is a different question from whether it is.

    A verdict tells a customer they cannot have the number. A reason tells them what to do
    about it, and the two cases are days apart in effort: a missing table needs a connector
    configured and data landed; an unjoinable pair needs one join proposal reviewed. Rendering
    them identically -- which is what a single ``rationale`` string built only from
    ``missing_tables`` did -- makes the cheap fix look as expensive as the dear one.
    """

    #: Nothing blocks it.
    NONE = "none"
    #: A source table is not in the semantic snapshot. Connect a source.
    MISSING_TABLES = "missing_tables"
    #: Every table is present, and review has recorded that two of them **must not** be
    #: joined -- a shared column name in two different id spaces, which is the case K4's
    #: collision detection finds and K8's mapping agent resolves. One review away, not one
    #: connector away, and until then the formula joins two unrelated keys and returns a
    #: number rather than an error, which is the expensive way to find out.
    #:
    #: Absence of a join edge is **not** this. A formula that states its own ``ON`` clause
    #: over a pair nobody has ruled on is an ordinary hand-written join; blocking it would
    #: make most multi-table KPIs unusable to enforce a rule nobody stated.
    NOT_JOINABLE = "not_joinable"


@dataclass(frozen=True)
class Feasibility:
    """The result of a set difference, not an opinion."""

    kpi_identifier: str
    verdict: FeasibilityVerdict
    missing_tables: FrozenSet[str] = field(default_factory=frozenset)
    #: Why, when it is not ready. Defaulted so every existing construction still works and
    #: infers the reason it already implied.
    reason: Optional[FeasibilityReason] = None
    #: The table pairs the formula needs joined and the layer cannot join. Empty unless
    #: ``reason`` is ``NO_JOIN_PATH``.
    unjoinable: Tuple[Tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.reason is None:
            inferred = (
                FeasibilityReason.NONE
                if self.verdict is FeasibilityVerdict.READY
                else FeasibilityReason.MISSING_TABLES
            )
            object.__setattr__(self, "reason", inferred)

    @property
    def rationale(self) -> str:
        if self.reason is FeasibilityReason.NOT_JOINABLE:
            pairs = "; ".join(f"{a} and {b}" for a, b in sorted(self.unjoinable))
            return (
                f"Every source table is present, but review has recorded that {pairs} must "
                "not be joined -- the shared column names are different id spaces. Resolve "
                "that pair before this can be computed; until then the formula would join "
                "two unrelated keys and return a number rather than fail."
            )
        if self.verdict is FeasibilityVerdict.READY:
            return "Every source table is present in the semantic snapshot."
        missing = ", ".join(sorted(self.missing_tables))
        return f"Missing from the semantic snapshot: {missing}."


def bound_parameter_names(sql: str) -> FrozenSet[str]:
    """The ``{name}`` placeholders a formula uses."""
    return frozenset(_PLACEHOLDER_RE.findall(sql))


def referenced_tables(sql: str) -> FrozenSet[str]:
    """The tables a formula reads, by scanning ``FROM`` and ``JOIN``.

    Returns bare, lower-cased table names: a schema qualifier is dropped, because that is how
    ``source_tables`` is authored and how :attr:`TableRef.name` holds it. Two tables of the
    same name in different schemas would collide here; declare the distinction in the semantic
    layer rather than relying on this cross-check to spot it.

    Subquery aliases are not table names, so anything that never appears after ``FROM`` or
    ``JOIN`` is absent by construction. The result is a floor on what the formula touches,
    which is the right direction to be wrong in: it can under-report a table the declared
    ``source_tables`` also omits, but it cannot invent one.
    """
    return frozenset(table.lower() for _schema, table in _TABLE_RE.findall(sql))


def substitute_first_positional(sql: str, name: str) -> str:
    """Replace the first positional ``?`` with a named ``{placeholder}``.

    Lives here beside the pattern rather than in the harness, so the rule for what counts as a
    positional marker has exactly one definition.
    """
    return _POSITIONAL_RE.sub("{" + name + "}", sql, count=1)


def positional_markers(sql: str) -> Tuple[str, ...]:
    """The positional ``?`` markers a formula uses.

    Public so the validation harness does not have to import a private name to ask the one
    question the shape refuses formulas over.
    """
    return tuple(_POSITIONAL_RE.findall(sql))


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


def assess_feasibility_with_joins(
    kpi: KpiDefinition,
    available_tables: FrozenSet[str],
    known_unjoinable: Callable[[str, str], bool],
) -> Feasibility:
    """:func:`assess_feasibility`, plus the joins review has ruled out.

    The set difference cannot see this: a formula over ``analytics_event`` and
    ``session_replay_session`` has both tables present, so it reports ``ready`` -- and then
    joins two ``session_id`` columns that are different id spaces, which the harvest's own
    column comment says outright. It returns a number rather than an error, and the number is
    wrong.

    **Only pairs review has ruled out.** ``known_unjoinable`` answers "has someone recorded
    that these must not be joined", not "is there a join edge". The first version asked the
    second question and blocked every multi-table KPI whose pair nobody had proposed a join
    for -- including formulas that state a correct ``ON`` clause of their own. Absence of a
    proposal is not a prohibition, and treating it as one would make most multi-table KPIs
    unusable to enforce a rule nobody stated. K11's own cookbook caught that: three of its six
    KPIs went blocked on a check that should not have had an opinion about them.

    A callable rather than a reader, so this stays a function over data. Deterministic, and
    with nothing to be persuaded by.

    A missing table is reported ahead of an unjoinable pair. Both may be true, and "connect
    this source" comes first -- there is no point reviewing a join to a table nobody landed.
    """
    base = assess_feasibility(kpi, available_tables)
    if base.verdict is not FeasibilityVerdict.READY:
        return base

    tables = sorted({t.lower() for t in kpi.source_tables})
    if len(tables) < 2:
        return base

    unjoinable = tuple(
        (left, right)
        for index, left in enumerate(tables)
        for right in tables[index + 1 :]
        if known_unjoinable(left, right)
    )
    if not unjoinable:
        return base
    return Feasibility(
        kpi_identifier=kpi.identifier,
        verdict=FeasibilityVerdict.BLOCKED,
        missing_tables=frozenset(),
        reason=FeasibilityReason.NOT_JOINABLE,
        unjoinable=unjoinable,
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
