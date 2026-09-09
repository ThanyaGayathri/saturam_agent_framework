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
from typing import Any, Dict, List, Mapping, Optional, Sequence

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


def ontology_leg(store: Any, *, limit: Optional[int] = None) -> Leg:
    """A leg over the shipped ontology (K3): what a concept *is*, once, for every customer.

    Both of the store's own legs are asked -- vector and lexical -- and handed to the fusion
    as one ranked list, deduplicated by concept name at its best position. Passing them as two
    separate legs would let a concept both stores rank highly count twice in the outer fusion,
    which is double-counting one store's internal agreement as if it were corroboration
    between stores.
    """

    def leg(query: MemoryQuery) -> Sequence[UnifiedResult]:
        size = limit or query.limit or 5
        hits = list(store.search_vector(query.text, limit=size))
        hits += list(store.search_lexical(query.text, limit=size))
        return _concept_results(hits, size)

    return leg


def aontology_leg(store: Any, *, limit: Optional[int] = None) -> AsyncLeg:
    """Async variant of :func:`ontology_leg`."""

    async def leg(query: MemoryQuery) -> Sequence[UnifiedResult]:
        size = limit or query.limit or 5
        vector, lexical = await asyncio.gather(
            store.asearch_vector(query.text, limit=size),
            store.asearch_lexical(query.text, limit=size),
        )
        return _concept_results([*vector, *lexical], size)

    return leg


def _concept_results(hits: Sequence[Any], limit: int) -> List[UnifiedResult]:
    """One result per concept, keeping its best-ranked appearance across the store's legs."""
    seen: Dict[str, UnifiedResult] = {}
    for hit in hits:
        concept = hit.concept
        if concept.name in seen:
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
    return list(seen.values())[:limit]


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
        return _semantic_results(reader, query, limit or query.limit or 5)

    return leg


def asemantic_leg(reader: SemanticRead, *, limit: Optional[int] = None) -> AsyncLeg:
    """Async variant of :func:`semantic_leg`."""

    async def leg(query: MemoryQuery) -> Sequence[UnifiedResult]:
        return await asyncio.to_thread(
            _semantic_results, reader, query, limit or query.limit or 5
        )

    return leg


def _semantic_results(
    reader: SemanticRead, query: MemoryQuery, limit: int
) -> List[UnifiedResult]:
    terms = [term for term in _terms(query.text) if len(term) > 2]
    results: List[UnifiedResult] = []

    for ref in reader.list_tables():
        table_terms = set(_terms(ref.name))
        # Score by how much of the table's own name the query accounted for, so `crm_deal`
        # beats `crm_deal_stage_history` on the query "deal" -- the shorter name is the more
        # specific answer to it, and ranking by raw hit count would invert that.
        hits = sum(1 for term in terms if term in ref.name.lower())
        # Columns count too, at a lower weight and named in the result. "Which table has
        # email" is a question about a column, and the first version could not answer it.
        table = reader.get_table(ref.qualified)
        matched_columns = [
            column.name
            for column in (table.columns if table else ())
            if any(term in column.name.lower() for term in terms)
        ]
        if not hits and not matched_columns:
            continue
        via = f", via column(s) {', '.join(matched_columns)}" if matched_columns else ""
        results.append(
            UnifiedResult(
                content=(
                    f"{ref.schema}.{ref.name} is a table in the semantic layer"
                    f" ({ref.row_count} rows{via})."
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

    for name in reader.list_concepts():
        if any(term in name.lower() for term in terms):
            concept = reader.get_concept(name)
            if concept is None:
                continue
            # Rendered with the same function promotion uses to write memory's copy (K5).
            # Memory holds a copy derived from this very node; if the two legs rendered it
            # differently, fusion by content could never recognise them as one finding --
            # the P3 verification found exactly that, with the memory leg live and nothing
            # corroborated. Same node, same function, same sentence, one hit from two legs.
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
    legs: Mapping[str, AsyncLeg] = {
        **(
            {ONTOLOGY_LEG: aontology_leg(ontology, limit=query.limit)}
            if ontology
            else {}
        ),
        **(
            {SEMANTIC_LEG: asemantic_leg(semantic, limit=query.limit)}
            if semantic
            else {}
        ),
    }
    return await aunified_read(
        scope,
        query,
        memory=memory,
        knowledge=knowledge,
        kb_filters=kb_filters,
        legs=legs,
    )


def _terms(text: str) -> List[str]:
    """Query words, lower-cased, punctuation dropped."""
    return [
        "".join(char for char in word if char.isalnum() or char == "_").lower()
        for word in text.split()
    ]
