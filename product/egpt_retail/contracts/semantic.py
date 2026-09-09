"""The semantic read API -- contract between Lane K and Lane R.

How anything asks what tables, columns, concepts and joins exist, and what a concept's
synonyms and filters are. Called by every agent and every preflight check, which is why it
is a contract rather than an internal interface: preflight, the playground and the spec
screen are all built against it before the real graph exists (K5, P2).

**Two rules are enforced by the shape of this API rather than by anyone remembering them.**

*Nothing unconfirmed leaks into an answer.* Every read returns accepted rows only. A
proposal is visible solely to a caller that asks for it by passing ``include_proposed=True``
and is then handed rows with ``status`` set to ``proposed``, so there is no way to receive an
unreviewed synonym and mistake it for a confirmed one. The review screen is the one caller
expected to pass it.

*A join path is never guessed.* :meth:`SemanticRead.find_join_path` returns ``None`` when the
graph holds no path between two tables, and raises when it cannot answer. Those are different
failures and collapsing them is how a model ends up inventing a join. An empty result is an
answer; an error is not.

The stub in :mod:`egpt_retail.contracts.semantic_stub` implements this against fixtures, so
Lane R builds against real signatures from day one and swaps to the graph-backed
implementation at P2 without touching a caller.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import FrozenSet, Optional, Tuple

try:  # pragma: no cover -- Protocol is 3.8+, runtime_checkable 3.8+
    from typing import Protocol, runtime_checkable
except ImportError:  # pragma: no cover
    from typing_extensions import (  # type: ignore[assignment]
        Protocol,
        runtime_checkable,
    )

__all__ = [
    "CONTRACT_VERSION",
    "AuthoritativeSource",
    "Column",
    "Concept",
    "JoinEdge",
    "JoinPath",
    "ProposalStatus",
    "SemanticRead",
    "SemanticReadError",
    "Snapshot",
    "Table",
    "TableRef",
    "TableSet",
    "aavailable_tables",
    "aconcept_vocabulary",
    "adescribe_reader",
    "available_tables",
    "concept_vocabulary",
    "describe_reader",
]

#: Bumped on any breaking change, per the change protocol.
CONTRACT_VERSION = "1.1.0"


class ProposalStatus(str, Enum):
    """Whether a row has been through review.

    Only ``ACCEPTED`` rows reach the graph (D10, G5). ``PROPOSED`` rows live in the proposals
    table and are returned by this API only when a caller explicitly asks for them.
    """

    ACCEPTED = "accepted"
    PROPOSED = "proposed"


class SemanticReadError(RuntimeError):
    """The read could not be answered.

    Deliberately distinct from an empty result. "No join path exists between these two
    tables" is an answer and is returned as ``None``; "the graph is unreachable" or "this
    deployment has no harvest yet" is not an answer and is raised. A caller that cannot tell
    the two apart will present the second as the first, and a model handed an empty result
    will fill the gap.
    """


@dataclass(frozen=True)
class Snapshot:
    """Which semantic version a read was answered from.

    On every response, because K6 makes each harvest a versioned snapshot and every KPI
    records the version it was validated against. An answer that cannot say which snapshot it
    came from cannot be re-checked after the next harvest.
    """

    version: str
    harvested_at: str
    source: str = "warehouse"


@dataclass(frozen=True)
class Column:
    """One column of one table, as the harvest found it (K4)."""

    name: str
    data_type: str
    nullable: bool = True
    is_key: bool = False
    #: Set by enrichment and confirmed by a person. Drives the compliance tags a provenance
    #: record has to carry, so it is part of the read surface rather than a private detail.
    is_personal_data: bool = False
    description: str = ""
    #: Values the harvest actually saw. Bounded, and never the whole column: this is a read
    #: API for types and concepts, and rows stay in the warehouse.
    sampled_values: Tuple[str, ...] = ()
    status: ProposalStatus = ProposalStatus.ACCEPTED


@dataclass(frozen=True)
class TableRef:
    """A table's identity, without its columns. What listing returns."""

    name: str
    schema: str = "silver"
    row_count: Optional[int] = None

    @property
    def qualified(self) -> str:
        return f"{self.schema}.{self.name}"


@dataclass(frozen=True)
class Table:
    """A table and its columns."""

    ref: TableRef
    columns: Tuple[Column, ...]
    description: str = ""
    #: Ontology concepts this table is an instance of, once mapping is confirmed (K8).
    concepts: Tuple[str, ...] = ()
    status: ProposalStatus = ProposalStatus.ACCEPTED

    @property
    def column_names(self) -> FrozenSet[str]:
        return frozenset(column.name for column in self.columns)

    @property
    def personal_data_columns(self) -> Tuple[Column, ...]:
        return tuple(column for column in self.columns if column.is_personal_data)

    def column(self, name: str) -> Optional[Column]:
        for column in self.columns:
            if column.name == name:
                return column
        return None


@dataclass(frozen=True)
class Concept:
    """What a concept means, and how to turn it into rows.

    This is the enrichment the lead calls the moat: organisation-specific, not replicable,
    and the reason a question asked in a customer's own words reaches the right data. Every
    field on it arrives through a proposal a person accepted.
    """

    name: str
    description: str
    synonyms: Tuple[str, ...] = ()
    question_patterns: Tuple[str, ...] = ()
    #: The SQL boolean fragment that scopes this concept. Interpolated as a
    #: ``ParameterKind.PREDICATE`` in a KPI formula. Never built from user input -- it comes
    #: from an accepted proposal and nowhere else.
    sql_filter: str = ""
    personal_data_fields: Tuple[str, ...] = ()
    compliance_tags: Tuple[str, ...] = ()
    #: Metric identifiers reachable from this concept, so a concept reaches its KPIs by
    #: traversal rather than by a second lookup (K11).
    metrics: Tuple[str, ...] = ()
    business_area: Optional[str] = None
    status: ProposalStatus = ProposalStatus.ACCEPTED


@dataclass(frozen=True)
class JoinEdge:
    """One hop of a join path."""

    left_table: str
    right_table: str
    #: The join predicate, exactly as it would appear after ``ON``.
    on: str
    cardinality: str = "many-to-one"


@dataclass(frozen=True)
class JoinPath:
    """How two tables connect, through however many hops it takes."""

    from_table: str
    to_table: str
    edges: Tuple[JoinEdge, ...]

    @property
    def intermediate_tables(self) -> Tuple[str, ...]:
        """Tables traversed that are neither endpoint. A preflight check needs these: a path
        through a table the semantic layer does not expose is not a usable path."""
        seen = []
        for edge in self.edges:
            for table in (edge.left_table, edge.right_table):
                if table not in (self.from_table, self.to_table) and table not in seen:
                    seen.append(table)
        return tuple(seen)

    @property
    def hops(self) -> int:
        return len(self.edges)


@dataclass(frozen=True)
class AuthoritativeSource:
    """Which table answers for a concept, and what the alternatives were (G20).

    ``alternatives`` is carried rather than discarded because a provenance record has to be
    able to say *"answered from crm_deal, not from analytics_event"*. A decision recorded
    without the options it beat is indistinguishable from there having been no decision.
    """

    concept: str
    table: str
    #: The tables that could also have served, and did not.
    alternatives: Tuple[str, ...] = ()
    #: Why this one. A sentence a person wrote or accepted, never a model's inference -- an
    #: authoritative source that a model chose is the thing G20 exists to prevent.
    rationale: str = ""
    status: ProposalStatus = ProposalStatus.ACCEPTED

    @property
    def was_contested(self) -> bool:
        return bool(self.alternatives)


@runtime_checkable
class SemanticRead(Protocol):
    """The read surface. Implemented by the stub in P0 and by the graph in P2.

    Every method has a synchronous and an asynchronous variant, per the repository's standing
    rule -- these are I/O against Neo4j in the real implementation, so a sync-only API would
    block the event loop while looking correct.

    ``include_proposed`` appears on every read that can return reviewable content. It
    defaults to ``False`` everywhere. **This default is Lane K's proposal and is not yet
    confirmed by Lane R**; it is recorded in ``CHANGELOG.md`` as an open question. The
    argument for it: the constraint is that nothing unproposed and unconfirmed enters the
    semantic layer, and a read surface whose default includes unreviewed rows makes that
    constraint depend on every caller remembering to opt out.
    """

    def snapshot(self) -> Snapshot:
        """Which semantic version this reader is answering from."""
        ...

    async def asnapshot(self) -> Snapshot: ...

    def list_tables(self, *, include_proposed: bool = False) -> Tuple[TableRef, ...]:
        """Every table the semantic layer exposes.

        This is also the allow-list R6's SQL guard restricts statements to, so a table absent
        here is a table no model can reach.
        """
        ...

    async def alist_tables(
        self, *, include_proposed: bool = False
    ) -> Tuple[TableRef, ...]: ...

    def get_table(
        self, name: str, *, include_proposed: bool = False
    ) -> Optional[Table]:
        """One table with its columns, or ``None`` if the layer does not expose it."""
        ...

    async def aget_table(
        self, name: str, *, include_proposed: bool = False
    ) -> Optional[Table]: ...

    def list_concepts(self, *, include_proposed: bool = False) -> Tuple[str, ...]:
        """Every concept name, for rendering a closed set into a prompt."""
        ...

    async def alist_concepts(
        self, *, include_proposed: bool = False
    ) -> Tuple[str, ...]: ...

    def get_concept(
        self, name: str, *, include_proposed: bool = False
    ) -> Optional[Concept]:
        """What a concept means, or ``None`` if this deployment has no such concept."""
        ...

    async def aget_concept(
        self, name: str, *, include_proposed: bool = False
    ) -> Optional[Concept]: ...

    def find_join_path(self, from_table: str, to_table: str) -> Optional[JoinPath]:
        """How two tables connect, or ``None`` if the graph holds no path.

        ``None`` means "there is no path", which is a real answer and the one a preflight
        check acts on. If the reader cannot determine an answer it raises
        :class:`SemanticReadError` instead, because a caller that receives ``None`` will tell
        the user the two datasets cannot be joined, and that must never be a way of saying
        the graph was unreachable.
        """
        ...

    async def afind_join_path(
        self, from_table: str, to_table: str
    ) -> Optional[JoinPath]: ...

    def authoritative_source(self, concept: str) -> Optional["AuthoritativeSource"]:
        """Which table is the source of truth for a concept, when more than one could serve.

        Added for G20. Nothing in the layer declared this, and the absence is not neutral: two
        tables that can both answer a question leave an agent to pick one, and it sounds
        equally confident either way. K4 makes the problem visible by recording column-name
        collisions; this is where the decision it demands gets recorded.

        Returns ``None`` when the concept has no contested source -- one table, or none. That
        is a real answer and distinct from an unanswerable one, which raises, for the same
        reason ``find_join_path`` separates the two.
        """
        ...

    async def aauthoritative_source(
        self, concept: str
    ) -> Optional["AuthoritativeSource"]: ...

    def list_business_areas(self, *, include_proposed: bool = False) -> Tuple[str, ...]:
        """Areas this deployment supports.

        Derived from confirmed mappings rather than guessed (K9). Areas exist in the ontology
        before any source is connected, so an empty result here means no source has been
        connected, never that the ontology is empty.
        """
        ...

    async def alist_business_areas(
        self, *, include_proposed: bool = False
    ) -> Tuple[str, ...]: ...


@dataclass(frozen=True)
class TableSet:
    """A convenience view over ``list_tables``, for the set difference feasibility performs."""

    tables: Tuple[TableRef, ...] = field(default_factory=tuple)

    @property
    def names(self) -> FrozenSet[str]:
        return frozenset(ref.name.lower() for ref in self.tables)

    @classmethod
    def from_reader(cls, reader: SemanticRead) -> "TableSet":
        return cls(tables=tuple(reader.list_tables()))


def available_tables(reader: SemanticRead) -> FrozenSet[str]:
    """The table names a KPI's feasibility is computed against.

    The one line joining this contract to the KPI formula shape: feasibility is
    ``formula.source_tables - available_tables(reader)``, and both halves come from data.
    """
    return TableSet.from_reader(reader).names


async def aavailable_tables(reader: SemanticRead) -> FrozenSet[str]:
    """Async variant."""
    return frozenset(ref.name.lower() for ref in await reader.alist_tables())


def concept_vocabulary(reader: SemanticRead) -> Tuple[str, ...]:
    """Concept names as a closed set, for rendering into a proposer's prompt.

    A proposer told to use "only concepts present in the ontology" and not given the list will
    invent one. This is the list.
    """
    return tuple(sorted(reader.list_concepts()))


async def aconcept_vocabulary(reader: SemanticRead) -> Tuple[str, ...]:
    """Async variant."""
    return tuple(sorted(await reader.alist_concepts()))


def describe_reader(reader: SemanticRead) -> str:
    """A one-line summary, for a provenance record or a startup log."""
    snap = reader.snapshot()
    return (
        f"semantic snapshot {snap.version} harvested {snap.harvested_at} "
        f"({len(reader.list_tables())} tables, {len(reader.list_concepts())} concepts)"
    )


async def adescribe_reader(reader: SemanticRead) -> str:
    """Async variant."""
    snap = await reader.asnapshot()
    tables = await reader.alist_tables()
    concepts = await reader.alist_concepts()
    return (
        f"semantic snapshot {snap.version} harvested {snap.harvested_at} "
        f"({len(tables)} tables, {len(concepts)} concepts)"
    )
