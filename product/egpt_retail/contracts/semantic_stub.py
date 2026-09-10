"""A fixture-backed semantic reader, so Lane R can build before the graph exists.

The delivery plan's reasoning: a running stub beats a document, because a contract nobody can
execute is one everybody interprets differently. This is that stub. It implements
:class:`~egpt_retail.contracts.semantic.SemanticRead` exactly, so swapping it for the
graph-backed reader at P2 changes no caller.

**It ships with no fixture loaded, and that is deliberate.** The alternative was to seed it
from the POC's synthetic ecommerce payloads so it returned something immediately. Every read
would then hand Lane R plausible retail-shaped data that is not the customer's, and wiring
built against `products` and `order_items` would need re-doing when the real harvest arrives.
An empty stub that raises a clear error costs Lane R nothing they were not going to pay
anyway, and it cannot teach them the wrong shapes in the meantime.

So the stub gives them the half that actually blocks: signatures, types, and the semantics of
an empty result. Loading a fixture is one call once the sample data is in.

    reader = StubSemanticRead.from_fixture(Path("fixtures/middleby.json"))

Fixture format is the JSON mirror of the dataclasses in
:mod:`egpt_retail.contracts.semantic`; see ``_fixtures/README.md``.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from egpt_retail.contracts.semantic import (
    AuthoritativeSource,
    Column,
    Concept,
    JoinEdge,
    JoinPath,
    ProposalStatus,
    SemanticReadError,
    Snapshot,
    Table,
    TableRef,
)

__all__ = ["StubSemanticRead", "FixtureNotLoaded"]


class FixtureNotLoaded(SemanticReadError):
    """No fixture has been loaded into the stub.

    Raised rather than returning an empty list, because an empty list is a meaningful answer
    from this API -- "this deployment has no tables yet" -- and a caller cannot tell that
    apart from "nobody wired the fixture". Lane R sees a message naming the fix instead of a
    screen that renders zero rows and looks like it works.
    """

    def __init__(self, operation: str) -> None:
        super().__init__(
            f"{operation}: the semantic read stub has no fixture loaded. "
            "Load one with StubSemanticRead.from_fixture(path), or point the caller at the "
            "graph-backed reader once K5 lands. This is an unconfigured stub, not an empty "
            "semantic layer."
        )


def _column(payload: Mapping[str, Any]) -> Column:
    return Column(
        name=payload["name"],
        data_type=payload.get("data_type", "text"),
        nullable=bool(payload.get("nullable", True)),
        is_key=bool(payload.get("is_key", False)),
        is_personal_data=bool(payload.get("is_personal_data", False)),
        description=payload.get("description", ""),
        sampled_values=tuple(payload.get("sampled_values", ())),
        status=ProposalStatus(payload.get("status", "accepted")),
        # 1.2.0's three. The stub claims to implement SemanticRead exactly, so a field the
        # real store returns and this one drops makes the swap the P0 contract exists for
        # visible to a caller -- which is the one thing the stub must never do.
        concept=payload.get("concept"),
        meaning=payload.get("meaning", ""),
        joinable=bool(payload.get("joinable", False)),
    )


def _table(payload: Mapping[str, Any]) -> Table:
    return Table(
        ref=TableRef(
            name=payload["name"],
            schema=payload.get("schema", "silver"),
            row_count=payload.get("row_count"),
        ),
        columns=tuple(_column(c) for c in payload.get("columns", ())),
        description=payload.get("description", ""),
        concepts=tuple(payload.get("concepts", ())),
        status=ProposalStatus(payload.get("status", "accepted")),
    )


def _concept(payload: Mapping[str, Any]) -> Concept:
    return Concept(
        name=payload["name"],
        description=payload.get("description", ""),
        synonyms=tuple(payload.get("synonyms", ())),
        question_patterns=tuple(payload.get("question_patterns", ())),
        sql_filter=payload.get("sql_filter", ""),
        personal_data_fields=tuple(payload.get("personal_data_fields", ())),
        compliance_tags=tuple(payload.get("compliance_tags", ())),
        metrics=tuple(payload.get("metrics", ())),
        business_area=payload.get("business_area"),
        status=ProposalStatus(payload.get("status", "accepted")),
    )


def _edge(payload: Mapping[str, Any]) -> JoinEdge:
    return JoinEdge(
        left_table=payload["left_table"],
        right_table=payload["right_table"],
        on=payload["on"],
        cardinality=payload.get("cardinality", "many-to-one"),
    )


@dataclass
class StubSemanticRead:
    """A semantic reader backed by fixtures rather than by Neo4j.

    Satisfies the :class:`SemanticRead` protocol structurally. Every async variant simply
    awaits the synchronous one off the event loop: the stub does no real I/O, and inventing
    concurrency it does not have would make it a worse rehearsal of the real reader, not a
    better one.
    """

    _tables: Dict[str, Table] = field(default_factory=dict)
    _concepts: Dict[str, Concept] = field(default_factory=dict)
    _edges: List[JoinEdge] = field(default_factory=list)
    _snapshot: Optional[Snapshot] = None
    _authoritative: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    _loaded: bool = False

    # --- construction ------------------------------------------------------------------

    @classmethod
    def from_fixture(cls, path: Path) -> "StubSemanticRead":
        """Load a fixture from disk."""
        return cls.from_payload(json.loads(Path(path).read_text()))

    @classmethod
    async def afrom_fixture(cls, path: Path) -> "StubSemanticRead":
        """Async variant. The read happens off the event loop."""

        def _read() -> str:
            return Path(path).read_text()

        return cls.from_payload(json.loads(await asyncio.to_thread(_read)))

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "StubSemanticRead":
        """Build from an already-parsed fixture.

        Pure construction with nothing to await, so it has no async variant by the standing
        exemption -- ``afrom_fixture`` is where the I/O is.
        """
        snapshot_payload = payload.get("snapshot", {})
        return cls(
            _tables={t["name"]: _table(t) for t in payload.get("tables", ())},
            _concepts={c["name"]: _concept(c) for c in payload.get("concepts", ())},
            _edges=[_edge(e) for e in payload.get("join_edges", ())],
            _authoritative={
                item["concept"]: item
                for item in payload.get("authoritative_sources", ())
            },
            _snapshot=Snapshot(
                version=snapshot_payload.get("version", "unversioned"),
                harvested_at=snapshot_payload.get("harvested_at", "unknown"),
                source=snapshot_payload.get("source", "warehouse"),
            ),
            _loaded=True,
        )

    def _require_fixture(self, operation: str) -> None:
        if not self._loaded:
            raise FixtureNotLoaded(operation)

    @staticmethod
    def _visible(status: ProposalStatus, include_proposed: bool) -> bool:
        return status is ProposalStatus.ACCEPTED or include_proposed

    # --- reads -------------------------------------------------------------------------

    def snapshot(self) -> Snapshot:
        self._require_fixture("snapshot")
        assert self._snapshot is not None
        return self._snapshot

    async def asnapshot(self) -> Snapshot:
        return self.snapshot()

    def list_tables(self, *, include_proposed: bool = False) -> Tuple[TableRef, ...]:
        self._require_fixture("list_tables")
        return tuple(
            table.ref
            for table in self._tables.values()
            if self._visible(table.status, include_proposed)
        )

    async def alist_tables(
        self, *, include_proposed: bool = False
    ) -> Tuple[TableRef, ...]:
        return self.list_tables(include_proposed=include_proposed)

    def get_table(
        self, name: str, *, include_proposed: bool = False
    ) -> Optional[Table]:
        self._require_fixture("get_table")
        table = self._tables.get(name)
        if table is None or not self._visible(table.status, include_proposed):
            return None
        if include_proposed:
            return table
        # Filter proposed columns out of an accepted table, so a caller that did not ask for
        # proposals cannot receive one nested inside something it did ask for.
        return Table(
            ref=table.ref,
            columns=tuple(
                c for c in table.columns if c.status is ProposalStatus.ACCEPTED
            ),
            description=table.description,
            concepts=table.concepts,
            status=table.status,
        )

    async def aget_table(
        self, name: str, *, include_proposed: bool = False
    ) -> Optional[Table]:
        return self.get_table(name, include_proposed=include_proposed)

    def list_concepts(self, *, include_proposed: bool = False) -> Tuple[str, ...]:
        self._require_fixture("list_concepts")
        return tuple(
            name
            for name, concept in self._concepts.items()
            if self._visible(concept.status, include_proposed)
        )

    async def alist_concepts(
        self, *, include_proposed: bool = False
    ) -> Tuple[str, ...]:
        return self.list_concepts(include_proposed=include_proposed)

    def get_concept(
        self, name: str, *, include_proposed: bool = False
    ) -> Optional[Concept]:
        self._require_fixture("get_concept")
        concept = self._concepts.get(name)
        if concept is None or not self._visible(concept.status, include_proposed):
            return None
        return concept

    async def aget_concept(
        self, name: str, *, include_proposed: bool = False
    ) -> Optional[Concept]:
        return self.get_concept(name, include_proposed=include_proposed)

    def find_join_path(self, from_table: str, to_table: str) -> Optional[JoinPath]:
        """Breadth-first over the fixture's edges, shortest path wins.

        Returns ``None`` when no path exists. Raises when either endpoint is not a table this
        layer exposes, because "there is no path from a table I have never heard of" would be
        a misleading way to report a typo.
        """
        self._require_fixture("find_join_path")
        for endpoint in (from_table, to_table):
            if endpoint not in self._tables:
                raise SemanticReadError(
                    f"find_join_path: {endpoint!r} is not a table the semantic layer exposes. "
                    f"Known tables: {', '.join(sorted(self._tables))}."
                )
        if from_table == to_table:
            return JoinPath(from_table=from_table, to_table=to_table, edges=())

        adjacency: Dict[str, List[JoinEdge]] = {}
        for edge in self._edges:
            adjacency.setdefault(edge.left_table, []).append(edge)
            adjacency.setdefault(edge.right_table, []).append(
                JoinEdge(
                    left_table=edge.right_table,
                    right_table=edge.left_table,
                    on=edge.on,
                    cardinality=edge.cardinality,
                )
            )

        queue: List[Tuple[str, Tuple[JoinEdge, ...]]] = [(from_table, ())]
        seen = {from_table}
        while queue:
            current, path = queue.pop(0)
            for edge in adjacency.get(current, []):
                nxt = edge.right_table
                if nxt in seen:
                    continue
                extended = path + (edge,)
                if nxt == to_table:
                    return JoinPath(
                        from_table=from_table, to_table=to_table, edges=extended
                    )
                seen.add(nxt)
                queue.append((nxt, extended))
        return None

    async def afind_join_path(
        self, from_table: str, to_table: str
    ) -> Optional[JoinPath]:
        return self.find_join_path(from_table, to_table)

    def authoritative_source(self, concept: str) -> Optional[AuthoritativeSource]:
        """The fixture's recorded decision for a concept, or ``None`` if uncontested (G20)."""
        self._require_fixture("authoritative_source")
        payload = self._authoritative.get(concept)
        if payload is None:
            return None
        return AuthoritativeSource(
            concept=concept,
            table=payload["table"],
            alternatives=tuple(payload.get("alternatives", ())),
            rationale=payload.get("rationale", ""),
            status=ProposalStatus(payload.get("status", "accepted")),
        )

    async def aauthoritative_source(
        self, concept: str
    ) -> Optional[AuthoritativeSource]:
        """Async variant."""
        return await asyncio.to_thread(self.authoritative_source, concept)

    def list_business_areas(self, *, include_proposed: bool = False) -> Tuple[str, ...]:
        self._require_fixture("list_business_areas")
        areas = {
            concept.business_area
            for concept in self._concepts.values()
            if concept.business_area and self._visible(concept.status, include_proposed)
        }
        return tuple(sorted(areas))

    async def alist_business_areas(
        self, *, include_proposed: bool = False
    ) -> Tuple[str, ...]:
        return self.list_business_areas(include_proposed=include_proposed)
