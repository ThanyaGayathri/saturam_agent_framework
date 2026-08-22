"""The vector-leg projection of a semantic fact (SM-S1 #118).

``Neo4jGraphSubstrate._fact_batches`` derives its persisted edge shape from ``FactWrite`` directly:
subject/predicate/object become the edge's endpoints, and ``confidence``/``derived_by`` are merged
into the row's ``metadata`` (``egpt/integrations/memory/graph/neo4j/neo4j.py``). Read back through
``GraphSubstrate.recall_facts``, a fact therefore carries ``provenance["subject"|"predicate"|
"object"]`` and ``metadata["confidence"|"derived_by"]``.

``VectorSubstrate.upsert`` has no equivalent derivation: ``CoLocatedMemoryProvider.consolidate``
passed ``fact.record`` to it verbatim, so the same fact read back through ``VectorSubstrate.search``
carried none of that -- two retrieval paths for one fact, returning two different shapes. SM-S1's
"retrievable via both paths" acceptance criterion is that they must not.

``fact_record`` is the missing derivation, written once so the vector leg matches what the graph
leg already does rather than reimplementing it beside it (a second implementation of one decision
is how the two drift, per ``docs/adr/0003-symbiotic-memory.md`` section 1's ratified split of tier
from substrate). Nothing here writes -- pure construction, exactly like
``egpt.memory.schemas.episodic``'s projections, and exempt from CLAUDE.md's async-twin rule for the
same reason.
"""

from dataclasses import replace
from typing import Tuple

from egpt.memory.types import FactWrite, MemoryRecord

__all__ = [
    "FACT_METADATA_KEYS",
    "SPO_PROVENANCE_KEYS",
    "fact_record",
]

#: The two keys :func:`fact_record` always sets on ``MemoryRecord.metadata`` -- matching
#: ``Neo4jGraphSubstrate._fact_batches``'s ``{**record.metadata, "confidence": ..., "derived_by":
#: ...}``. Always present, never omitted: ``FactWrite.confidence`` defaults to ``1.0`` and is never
#: ``None``, and ``derived_by`` is stored even when it is ``None`` so a reader checking
#: ``"derived_by" in metadata`` gets the same answer from both legs -- present-but-``None`` is not
#: the same as absent.
FACT_METADATA_KEYS: Tuple[str, ...] = ("confidence", "derived_by")

#: The three keys :func:`fact_record` adds to ``MemoryRecord.provenance`` when the fact carries a
#: full triple -- matching ``Neo4jGraphSubstrate._record``'s read-side reconstruction of
#: ``recall_facts`` rows from the edge's own subject/predicate/object, which an untripled fact has
#: none of. Left unset (not set to ``None``) for a fact that is not a triple, so
#: ``"subject" in provenance`` agrees with :meth:`FactWrite.is_triple` on both legs.
SPO_PROVENANCE_KEYS: Tuple[str, ...] = ("subject", "predicate", "object")


def fact_record(fact: FactWrite) -> MemoryRecord:
    """The ``MemoryRecord`` a fact writes to the vector substrate -- ``fact.record``, enriched.

    A detached copy, never a mutation of ``fact.record``: the same ``FactWrite`` is also handed to
    ``GraphSubstrate.upsert_facts`` (``CoLocatedMemoryProvider.consolidate`` does this for one
    batch), and that call must not see the vector leg's enrichment folded into what it receives --
    it derives its own edge shape from ``fact.subject``/``fact.confidence``/etc. directly and does
    not read ``record.provenance``/``record.metadata`` for SPO or confidence.

    ``source_episode_ids`` is ``setdefault``, not overwritten -- matching ``_fact_batches`` exactly,
    so a caller that already populated ``record.provenance["source_episode_ids"]`` directly (as
    ``CoLocatedMemoryProvider._distil`` does, ahead of ``FactWrite.source_episode_ids`` carrying the
    identical list) keeps that value rather than this projection silently replacing it.
    """
    provenance = dict(fact.record.provenance)
    if fact.is_triple():
        provenance["subject"] = fact.subject
        provenance["predicate"] = fact.predicate
        provenance["object"] = fact.object
    if fact.source_episode_ids:
        provenance.setdefault("source_episode_ids", list(fact.source_episode_ids))
    metadata = dict(fact.record.metadata)
    metadata["confidence"] = fact.confidence
    metadata["derived_by"] = fact.derived_by
    return replace(fact.record, provenance=provenance, metadata=metadata)
