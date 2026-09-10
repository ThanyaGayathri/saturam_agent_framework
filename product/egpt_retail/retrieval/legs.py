"""K10 -- the two retrieval legs a retail deployment adds, and the one call that uses all four.

The gap report's finding was that every capability retrieved its own way: memory through the
provider, prose through ``Knowledge``, the ontology through its own vector index, and the
semantic layer not at all. Four call sites, four rankings, and no way to ask one question of
all of them -- so a question needing a domain rule *and* a table name got a good answer from
whichever store the caller happened to think of.

The framework closes the general half: ``unified_read`` fans out and fuses by Reciprocal Rank
Fusion, which is what makes ranking incomparable stores together defensible -- it reads each
leg's *position*, never its score, so a vector similarity, a Neo4j full-text score and a
memory record's fused score never have to agree on a scale.

This module closes the retail half: it turns the ontology store and the semantic graph into
legs of that shape.

**Why the semantic leg is lexical and not embedded.** The semantic layer holds table and
column names, which are identifiers rather than prose -- ``crm_deal_stage_history`` is not a
sentence and does not embed like one. Matching it by substring against the query is crude,
and it is the right kind of crude: it can miss a table the user described in words it does
not contain, and the ontology leg is there for exactly that. What it cannot do is invent a
table name, which a fuzzy embedded match over identifiers absolutely can.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Protocol, Sequence, runtime_checkable

from egpt.memory.provider import MemoryProvider
from egpt.memory.retrieval.unified import (
    AsyncLeg,
    KnowledgeRetrievable,
    Leg,
    RetrievalSource,
    UnifiedResult,
    UnifiedSlice,
    aunified_read,
    unified_read,
)
from egpt.memory.types import MemoryQuery, Scope

from egpt_retail.contracts.semantic import SemanticRead
from egpt_retail.semantic.promotion import promotable_text

__all__ = [
    "ONTOLOGY_LEG",
    "SEMANTIC_LEG",
    "aretail_unified_read",
    "ontology_leg",
    "retail_legs",
    "retail_unified_read",
    "semantic_leg",
]

#: Leg names. Constants because they are what a provenance record and a review screen render,
#: and a name typed twice in two files is a label that disagrees with itself.
ONTOLOGY_LEG = "ontology"
SEMANTIC_LEG = "semantic"


@runtime_checkable
class ColumnIndexed(Protocol):
    """A reader that can hand over every table's columns in one call.

    Not part of ``SemanticRead``: the contract is about what the layer *means*, and this is
    an optimisation the concrete graph store offers. Declared as a protocol rather than
    reached for with ``getattr`` so mypy checks the shape, and so a reader without it -- the
    P0 stub -- takes the documented fallback instead of failing at runtime.
    """

    def column_index(self) -> Dict[str, List[str]]: ...


def ontology_leg(store: Any, *, limit: Optional[int] = None) -> Leg:
    """A leg over the shipped ontology (K3): what a concept *is*, once, for every customer.

    Both of the store's own legs are asked -- vector and lexical -- and handed to the fusion
    as one ranked list, deduplicated by concept name at its best position. Passing them as two
    separate legs would let a concept both stores rank highly count twice in the outer fusion,
    which is double-counting one store's internal agreement as if it were corroboration
    between stores.
    """

    def leg(query: MemoryQuery) -> Sequence[UnifiedResult]:
        size = _size(limit, query)
        if size == 0:
            return []
        hits = list(store.search_vector(query.text, limit=size))
        hits += list(store.search_lexical(query.text, limit=size))
        return _concept_results(hits, size)

    return leg


def aontology_leg(store: Any, *, limit: Optional[int] = None) -> AsyncLeg:
    """Async variant of :func:`ontology_leg`."""

    async def leg(query: MemoryQuery) -> Sequence[UnifiedResult]:
        size = _size(limit, query)
        if size == 0:
            return []
        vector, lexical = await asyncio.gather(
            store.asearch_vector(query.text, limit=size),
            store.asearch_lexical(query.text, limit=size),
        )
        return _concept_results([*vector, *lexical], size)

    return leg


def _concept_results(hits: Sequence[Any], limit: int) -> List[UnifiedResult]:
    """One result per concept, keeping its best appearance, ranked before it is truncated.

    Three things had to be true here and only one was. The store's two legs are concatenated,
    so a concept the lexical leg ranked first arrives *after* every vector hit: without a
    final sort it is handed to the outer fusion in the wrong position, and under ``limit`` it
    is dropped entirely -- silently, and exactly for the exact-match case lexical search
    exists to serve. And dedup kept whichever copy came first, which is always the vector
    one, so a concept both legs found reported the vector score even when lexical scored it
    higher.

    The ``Leg`` contract says results are ranked best-first. RRF reads position, so a leg that
    returns them in the wrong order is not merely less useful -- it is feeding the fusion a
    claim about ranking that is not true.
    """
    seen: Dict[str, UnifiedResult] = {}
    for hit in hits:
        concept = hit.concept
        previous = seen.get(concept.name)
        if previous is not None and (previous.score or 0.0) >= hit.score:
            continue
        seen[concept.name] = UnifiedResult(
            content=f"{concept.name}: {concept.description}",
            source=RetrievalSource.KB,
            score=hit.score,
            id=f"ontology:{concept.name}",
            metadata={
                "concept": concept.name,
                "leg": hit.leg,
                "synonyms": list(getattr(concept, "synonyms", ()) or ()),
            },
            origin=ONTOLOGY_LEG,
        )
    ranked = sorted(
        seen.values(), key=lambda r: (-(r.score or 0.0), r.metadata["concept"])
    )
    return ranked[:limit]


def semantic_leg(reader: SemanticRead, *, limit: Optional[int] = None) -> Leg:
    """A leg over the customer's semantic layer (K5): which of *their* tables is which concept.

    Answers "what is this called here", which no other leg can: the ontology knows what a Quote
    is and the prose knows the rules about quoting, but only this store knows the table is
    called ``crm_deal``.

    Only accepted rows are read. ``include_proposed`` is not plumbed through on purpose -- a
    retrieval path is exactly where an unreviewed mapping would stop looking unreviewed, and
    the caller wanting to see proposals is a review screen, which reads the proposals store.
    """

    def leg(query: MemoryQuery) -> Sequence[UnifiedResult]:
        size = _size(limit, query)
        return _semantic_results(reader, query, size) if size else []

    return leg


def asemantic_leg(reader: SemanticRead, *, limit: Optional[int] = None) -> AsyncLeg:
    """Async variant of :func:`semantic_leg`."""

    async def leg(query: MemoryQuery) -> Sequence[UnifiedResult]:
        size = _size(limit, query)
        if size == 0:
            return []
        return await asyncio.to_thread(_semantic_results, reader, query, size)

    return leg


def _column_index(reader: SemanticRead, tables: Sequence[Any]) -> Dict[str, List[str]]:
    """Every table's columns, in one query where the reader supports it.

    The fallback is one ``get_table`` per table, which is what this did for every reader --
    on the live query path, once per retrieval call. Round 1 on the P3 PR caught it: a
    fifty-table warehouse meant fifty-one round trips to answer one question.
    """
    if isinstance(reader, ColumnIndexed):
        return reader.column_index()
    index: Dict[str, List[str]] = {}
    for ref in tables:
        table = reader.get_table(ref.qualified)
        index[ref.qualified] = [
            column.name for column in (table.columns if table else ())
        ]
    return index


def _semantic_results(
    reader: SemanticRead, query: MemoryQuery, limit: int
) -> List[UnifiedResult]:
    terms = [term for term in _terms(query.text) if len(term) > 2]
    results: List[UnifiedResult] = []

    tables = list(reader.list_tables())
    index = _column_index(reader, tables)

    for ref in tables:
        # Split the *table's* name on underscores, which is how table names are actually
        # segmented -- `_terms` splits on whitespace, so this set was always a single element
        # and the divisor below was always 1, making the documented specificity ranking dead
        # code that happened to look right because the tie-break sorted the same way.
        table_terms = {part for part in ref.name.lower().split("_") if part}
        # Score by how much of the table's own name the query accounted for, so `crm_deal`
        # beats `crm_deal_stage_history` on the query "deal" -- the shorter name is the more
        # specific answer to it, and ranking by raw hit count would invert that.
        hits = sum(1 for term in terms if term in ref.name.lower())
        # Columns count too, at a lower weight and named in the result. "Which table has
        # email" is a question about a column, and the first version could not answer it.
        matched_columns = [
            column
            for column in index.get(ref.qualified, ())
            if any(term in column.lower() for term in terms)
        ]
        if not hits and not matched_columns:
            continue
        via = f", via column(s) {', '.join(matched_columns)}" if matched_columns else ""
        # `row_count` is Optional on TableRef, and "(None rows)" was reaching text a model
        # reads. An unknown count says so; it does not render as a Python repr.
        rows = (
            f"{ref.row_count} rows"
            if ref.row_count is not None
            else "row count unknown"
        )
        results.append(
            UnifiedResult(
                content=(
                    f"{ref.schema}.{ref.name} is a table in the semantic layer"
                    f" ({rows}{via})."
                ),
                source=RetrievalSource.KB,
                score=hits / max(len(table_terms), 1) + 0.25 * bool(matched_columns),
                id=f"semantic:{ref.schema}.{ref.name}",
                metadata={
                    "table": ref.name,
                    "schema": ref.schema,
                    "columns": matched_columns,
                },
                origin=SEMANTIC_LEG,
            )
        )

    # Only the names that matched are fetched, and the match is decided before any I/O --
    # the loop below used to call get_concept for every matching name with no ceiling, so a
    # one-word query against a rich ontology issued a round trip per concept on the
    # interactive path. Capped at the limit, since anything past it is discarded anyway.
    matching = [
        name
        for name in reader.list_concepts()
        if any(term in name.lower() for term in terms)
    ]
    for name in matching[:limit]:
        concept = reader.get_concept(name)
        if concept is not None:
            # Rendered with the same function promotion uses to write memory's copy (K5).
            # Memory holds a copy derived from this very node; if the two legs rendered it
            # differently, fusion by content could never recognise them as one finding.
            results.append(
                UnifiedResult(
                    content=promotable_text(concept),
                    source=RetrievalSource.KB,
                    score=1.0,
                    id=f"semantic:concept:{concept.name}",
                    metadata={"concept": concept.name},
                    origin=SEMANTIC_LEG,
                )
            )

    results.sort(key=lambda r: (-(r.score or 0.0), r.id or ""))
    return results[:limit]


def retail_legs(
    *,
    ontology: Any = None,
    semantic: Optional[SemanticRead] = None,
    limit: Optional[int] = None,
) -> Dict[str, Leg]:
    """The extra legs this deployment has, ready to pass to ``unified_read``.

    A store left as ``None`` produces no leg at all rather than an empty one. That is the
    difference between "asked and found nothing" and "never asked", and it is the distinction
    the verification scenario turns on: disabling a leg has to be visible.
    """
    legs: Dict[str, Leg] = {}
    if ontology is not None:
        legs[ONTOLOGY_LEG] = ontology_leg(ontology, limit=limit)
    if semantic is not None:
        legs[SEMANTIC_LEG] = semantic_leg(semantic, limit=limit)
    return legs


def retail_unified_read(
    scope: Scope,
    query: MemoryQuery,
    *,
    memory: Optional[MemoryProvider] = None,
    knowledge: Optional[KnowledgeRetrievable] = None,
    ontology: Any = None,
    semantic: Optional[SemanticRead] = None,
    kb_filters: Any = None,
) -> UnifiedSlice:
    """One question, asked of every store this deployment has.

    Every capability in both lanes should call this rather than retrieving its own way. The
    result's ``fused()`` is the ranking; ``legs()`` is what each store said on its own, which
    is what a provenance record needs and what makes it possible to show that a leg was
    contributing rather than decorating.
    """
    return unified_read(
        scope,
        query,
        memory=memory,
        knowledge=knowledge,
        kb_filters=kb_filters,
        legs=retail_legs(ontology=ontology, semantic=semantic, limit=query.limit),
    )


async def aretail_unified_read(
    scope: Scope,
    query: MemoryQuery,
    *,
    memory: Optional[MemoryProvider] = None,
    knowledge: Optional[KnowledgeRetrievable] = None,
    ontology: Any = None,
    semantic: Optional[SemanticRead] = None,
    kb_filters: Any = None,
) -> UnifiedSlice:
    """Async variant. Every leg runs concurrently."""
    # `is not None`, matching the sync twin: a store object that happens to be falsy -- an
    # empty collection-like stand-in in a test, say -- is still a wired leg, and dropping it
    # would silently turn off a leg the caller passed.
    legs: Dict[str, AsyncLeg] = {}
    if ontology is not None:
        legs[ONTOLOGY_LEG] = aontology_leg(ontology, limit=query.limit)
    if semantic is not None:
        legs[SEMANTIC_LEG] = asemantic_leg(semantic, limit=query.limit)
    return await aunified_read(
        scope,
        query,
        memory=memory,
        knowledge=knowledge,
        kb_filters=kb_filters,
        legs=legs,
    )


def _size(limit: Optional[int], query: MemoryQuery) -> int:
    """How many results this leg may return.

    ``limit=0`` means *nothing*, matching what the framework's own KB leg does with it
    (``unified.py`` returns early on ``query.limit == 0``). The first version wrote
    ``limit or query.limit or 5``, so a caller asking for zero results from every leg got
    five from these two and none from the built-ins -- the same request answered two ways in
    one fan-out.
    """
    for candidate in (limit, query.limit):
        if candidate is not None:
            return max(0, candidate)
    return 5


def _terms(text: str) -> List[str]:
    """Query words, lower-cased, punctuation dropped."""
    return [
        "".join(char for char in word if char.isalnum() or char == "_").lower()
        for word in text.split()
    ]
