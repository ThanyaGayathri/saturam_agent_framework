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
from typing import Tuple

__all__ = ["PERSISTENT_TIERS", "MemoryTier"]


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
