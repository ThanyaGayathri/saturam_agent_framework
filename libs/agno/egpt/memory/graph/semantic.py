"""The semantic subgraph: resolved entities and the fact edges between them (SM-G7 #105).

Zep/Graphiti's tripartite pattern (research doc section on prior art) splits a raw episodic
subgraph from a resolved semantic one; SM-G6 (#104) shipped the first, this ticket names and reads
the second. Nothing here writes: SM-G2 (#100, ``graph/entities.py``) already lands ``(:Entity)``
nodes and SM-G3 (#101, ``graph/relations.py``) already lands ``(:Entity)-[:REL]->(:Entity)`` edges
-- the semantic subgraph *is* those two write paths' output. What was missing is a way to read it
back by its own shape rather than through ``traverse``'s relevance-ranked, tier-mixed search, which
scores episodes and facts together and answers "what's relevant to this text" rather than "what
does this entity connect to."

**Fact edges only, not the untripled ``(:Fact)`` node.** The acceptance criterion is "fact edges
queryable" -- a node carries no subject or object to filter on, so a query surface for it would be
a second, weaker copy of ``traverse``'s text search rather than a new capability. An untripled fact
is still reachable there.

**Closes a gap SM-G3 named and deferred.** ``relations.py``'s module docstring states that carrying
a fact's provenance forward across consolidation passes needs a read of the edge's current row,
"which this port does not expose" -- naming this ticket as the one that would add it.
:func:`~egpt.memory.substrates.GraphSubstrate.recall_facts` is that read. Wiring it into
``RelationExtractionPipeline`` so a re-extraction pass folds against the edge's live provenance
rather than only within-pass is not this ticket's to add; nothing here changes what that pipeline
writes.
"""

from typing import Optional, Sequence

from egpt.memory.types import MemoryOperationError

#: `require_recall_limit` / `require_time_window` are deliberately not imported or re-exported
#: here. `require_fact_filters` below checks only entities/relation/since/until against each
#: other; every caller (`cypher.recall_facts_statement`, `FakeGraph.recall_facts`) calls those two
#: directly from `episodic`, where they belong -- that is the one canonical home for both, so
#: re-exporting them from this module too would blur the module boundary this file exists to keep
#: (round-3 review, PR #102: the prior version imported both here without using either).
__all__ = [
    "require_fact_filters",
]


def require_fact_filters(
    entities: Optional[Sequence[str]],
    relation: Optional[str],
    since: Optional[int],
    until: Optional[int],
) -> None:
    """Refuse a fact recall that filters on nothing, and an entity or relation filter matching
    nothing by construction.

    Mirrors :func:`~egpt.memory.graph.episodic.require_recall_filters`, with one extra axis: a fact
    edge has a relation label an episode has none of, so ``relation`` joins ``entities`` as a way to
    narrow the read. The refusals are the same shape for the same reason -- an unfiltered
    ``MATCH`` on every fact edge in scope is the graph enumeration SM-G9 (#107) owns, and
    ``entities=[]`` reads equally as "any" and "none" so both would return a page a caller cannot
    tell from a genuine miss.
    """
    if entities is not None and not entities:
        raise MemoryOperationError(
            "entities is empty. Pass None for no entity filter; an empty list is ambiguous "
            "between 'any entity' and 'no entity', and both return an empty-looking page.",
            field_name="entities",
        )
    if relation is not None and not relation.strip():
        raise MemoryOperationError(
            "relation is blank. Pass None for no relation filter; a blank string matches no real "
            "relation label and returns a page indistinguishable from a genuine miss.",
            field_name="relation",
        )
    if entities is None and relation is None and since is None and until is None:
        raise MemoryOperationError(
            "recall_facts needs at least one of entities, relation, since or until. With none of "
            "them this enumerates every fact edge in scope, and graph enumeration semantics are "
            "SM-G9's (#107) to define.",
            field_name=None,
        )
