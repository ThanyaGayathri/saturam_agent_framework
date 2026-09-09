"""K5 -- promoting selected semantic facts into memory as a derived copy (D11).

The rule this exists to enforce: **the graph is the source of truth, memory holds a copy that
can be rebuilt from it.** D11 says so, the alignment table repeats it, and the reason is
specific — memory decays and consolidates. Those are the right semantics for what a system
learned about a person and exactly the wrong ones for a definition. A scoping filter that
faded, or two concept descriptions that a consolidation pass merged into one, would corrupt
every answer built on them and never report it.

So promotion is deliberately **one-way and idempotent**. It reads the graph and writes memory;
nothing here reads memory to update the graph. Delete every promoted row and run it again and
the copy is identical -- which is the proof that the graph is authoritative, and is exactly the
scenario K5 asks for.

Idempotence is not achieved by deleting first. Each promoted row carries a **stable id derived
from the concept name**, and the vector substrate upserts on ``(tenant_id, id)``
(``pgvector/sql.py:525``), so a second run overwrites the first row rather than adding a
second. Promotion runs after every harvest; an appending version would accumulate one copy of
the semantic layer per nightly run, and recall would return the same concept five times with
the oldest description among them.

**What gets promoted, and what does not.** A concept's name, description, synonyms and
business area: the things an agent benefits from having in recall without a graph round trip.
Not the SQL filter, not personal-data fields, not compliance tags. Those govern what a query
may touch and who may see the result, and a governing value that reaches an agent through a
decaying store is a governing value that can quietly stop governing. They stay in the graph
and are read directly, every time, by the executor that needs them.

Promoted rows are written under the scheduler identity from ``MEMORY_SCOPE.md`` — this is
system knowledge about the deployment, not something a person told the assistant.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, List, Optional, Sequence, Tuple

from egpt.memory.types import MemoryEvent, MemoryTier

from egpt_retail.contracts.semantic import Concept

__all__ = [
    "PROMOTED_TOPIC",
    "PROMOTED_TIER",
    "promoted_event_id",
    "PromotionReport",
    "apromote_concepts",
    "promotable_text",
    "promote_concepts",
]

#: Tags every promoted row, so a rebuild can find and replace exactly what it wrote and
#: nothing a person contributed. Without it, "delete the memory copy" is indistinguishable
#: from "delete the user's memories", and the rebuild scenario would be untestable.
PROMOTED_TOPIC = "semantic_layer"

#: Semantic, not episodic. D11 says the promoted copy lands in *semantic* memory, and the tier
#: is the difference between "a durable fact about this customer" and "something that happened
#: once". A definition in the episodic tier would be recalled as an event.
PROMOTED_TIER = MemoryTier.SEMANTIC


def promoted_event_id(concept_name: str) -> str:
    """The stable id one concept's promoted row always carries.

    Deterministic, so the substrate's ``ON CONFLICT (tenant_id, id) DO UPDATE`` turns a second
    promotion into an overwrite. This is the whole of the idempotence guarantee -- there is no
    delete-then-write step, which is what makes promotion safe to run mid-flight.
    """
    return f"{PROMOTED_TOPIC}:{concept_name}"


@dataclass(frozen=True)
class PromotionReport:
    """What one promotion run wrote."""

    promoted: int
    skipped: Tuple[str, ...] = ()
    scope_key: str = ""

    def __str__(self) -> str:
        tail = f", {len(self.skipped)} skipped" if self.skipped else ""
        return f"{self.promoted} concept(s) promoted{tail}"


def promotable_text(concept: Concept) -> str:
    """The sentence a promoted concept becomes.

    One line per concept, carrying name, description, synonyms and area — enough for recall to
    surface the right concept in conversation, and deliberately not enough to act on. Anything
    that governs a query is read from the graph.
    """
    parts = [f"{concept.name}: {concept.description}"]
    if concept.synonyms:
        parts.append(f"Also called: {', '.join(concept.synonyms)}.")
    if concept.business_area:
        parts.append(f"Business area: {concept.business_area}.")
    return " ".join(parts)


def promote_concepts(
    reader: Any,
    provider: Any,
    scope: Any,
    *,
    concepts: Optional[Sequence[str]] = None,
) -> PromotionReport:
    """Rebuild the memory copy of the semantic layer from the graph.

    Args:
        reader: Anything satisfying ``SemanticRead`` — the graph store in a deployment, the
            stub in a test. Promotion does not care which, which is the contract working.
        provider: The memory provider to write through.
        scope: The scope to write under. The scheduler identity, per ``MEMORY_SCOPE.md``.
        concepts: Which concepts to promote. ``None`` means every accepted one.

    Returns:
        What was written. A concept the reader cannot resolve is skipped and named rather than
        failing the run — one unresolvable concept should not stop the other forty from being
        rebuilt.
    """
    names = list(concepts) if concepts is not None else list(reader.list_concepts())
    resolved: List[Concept] = []
    skipped: List[str] = []
    for name in names:
        concept = reader.get_concept(name)
        if concept is None:
            skipped.append(name)
            continue
        resolved.append(concept)

    if resolved:
        provider.write_tier(scope, PROMOTED_TIER, _events(scope, resolved))
    return PromotionReport(
        promoted=len(resolved),
        skipped=tuple(skipped),
        scope_key=scope.key() if hasattr(scope, "key") else "",
    )


async def apromote_concepts(
    reader: Any,
    provider: Any,
    scope: Any,
    *,
    concepts: Optional[Sequence[str]] = None,
) -> PromotionReport:
    """Async variant."""
    return await asyncio.to_thread(
        promote_concepts, reader, provider, scope, concepts=concepts
    )


def _events(scope: Any, concepts: Sequence[Concept]) -> List[MemoryEvent]:
    """One event per concept, each with the id that makes a re-run an overwrite."""
    return [
        MemoryEvent(
            content=promotable_text(concept),
            scope=scope,
            tier=PROMOTED_TIER,
            id=promoted_event_id(concept.name),
            topics=[PROMOTED_TOPIC, concept.name],
            metadata={
                "source": "semantic_layer",
                "concept": concept.name,
                "derived": True,
                "rebuildable_from": "graph",
            },
        )
        for concept in concepts
    ]
