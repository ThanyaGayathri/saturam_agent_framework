"""CoALA memory tiers — the *what kind of memory* axis (SM-C1).

Four tiers, from arXiv:2309.02427 via ``docs/MEMORY-RESEARCH-episodic-and-neurosymbolic.md``
section 1. ADR 0003 section B.5 makes these names canonical and rules that "tier" is said only
of these four — "substrate" (vector / graph, see ``egpt.memory.types.Substrate``) is the
orthogonal *where is it stored* axis. Episodic and semantic each live in **both** substrates,
which is why the two axes cannot be collapsed.

This module deliberately holds the enum and nothing else. **SM-C2 (#90) owns per-tier read/write
policy and the mapping of the six ``learn`` stores into these tiers** and extends this file; SM-C1
ships only the mechanism the seam needs (``MemoryQuery.tiers``, ``read_tier`` / ``write_tier``).

Tier -> substrate mapping (ADR 0003 section 1, ratified):

===========  ======  =====  ===========================================================
Tier         Vector  Graph  Notes
===========  ======  =====  ===========================================================
working      --      --     Context only; not embedded, not bi-temporal. SM-C6 (#94).
episodic     yes     (yes)  Raw non-lossy events. Graph presence is a pointer today --
                            the episode node type is SM-G1's to define.
semantic     yes     yes    Facts / entities / relations. Relations are the primary
                            bi-temporal surface and are graph-only.
procedural   yes     --     Rules / prompt fragments. Retrieved by similarity.
===========  ======  =====  ===========================================================
"""

from enum import Enum
from typing import Dict, FrozenSet, Tuple

__all__ = ["PERSISTENT_TIERS", "SUBSTRATES_BY_TIER", "MemoryTier", "substrates_for_tier"]


class MemoryTier(str, Enum):
    """Which kind of memory a record, event, or query concerns.

    ``str`` mixin so a config value ("episodic") validates by construction and a serialized
    record round-trips without a custom encoder -- the same choice ``ProviderMode`` and
    ``egpt.metrics.ModelType`` make.
    """

    WORKING = "working"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"


#: The tiers a provider searches when a query names none.
#:
#: ``WORKING`` is excluded because it has no persistent substrate -- it is the in-context buffer
#: SM-C6 (#94) owns, so a retrieval that silently included it would be reading state the caller
#: already holds. A caller that wants it must ask for it by name.
PERSISTENT_TIERS: Tuple[MemoryTier, ...] = (
    MemoryTier.EPISODIC,
    MemoryTier.SEMANTIC,
    MemoryTier.PROCEDURAL,
)


#: Which substrates a tier is written to, from the ratified mapping in ADR 0003 section 1.
#:
#: Substrate names are strings here rather than the ``Substrate`` enum, because that enum lives in
#: ``types``, which imports this module. The provider translates.
#:
#: Two rows carry qualifications the ADR states explicitly:
#:
#: * ``episodic`` -> graph is **intended, not yet realised**. SM-R12's schema defines no episode
#:   node; ``graph_node_id`` is an untyped pointer with no referent. SM-G1 (#99) defines the node
#:   type, or rules that episodes reach the graph only through their entities.
#: * ``procedural`` -> vector only. SM-R11's map said "rules/prompt, no substrate"; SM-R12 shipped
#:   ``ProceduralRecord`` as ``Embeddable``. The ADR ratified the shipped schema: procedural rules
#:   are retrieved by similarity, and the field exists.
#:
#: **SM-C2 (#90) turns this into full per-tier read/write policy.** This is the write-routing
#: subset the seam needs to not guess, encoded once instead of inline in an adapter.
SUBSTRATES_BY_TIER: Dict[MemoryTier, FrozenSet[str]] = {
    MemoryTier.WORKING: frozenset(),  # in-context only; nothing persists
    MemoryTier.EPISODIC: frozenset({"vector", "graph"}),
    MemoryTier.SEMANTIC: frozenset({"vector", "graph"}),
    MemoryTier.PROCEDURAL: frozenset({"vector"}),
}


def substrates_for_tier(tier: MemoryTier) -> FrozenSet[str]:
    """The substrate names a write to ``tier`` should reach. Empty for ``WORKING``."""
    return SUBSTRATES_BY_TIER[tier]
