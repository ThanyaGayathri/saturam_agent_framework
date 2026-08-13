"""CoALA memory tiers and their per-tier policy (SM-C2 #90).

Four tiers, from arXiv:2309.02427; ADR 0003 section B.5 makes the names canonical and reserves
"tier" for these four. "Substrate" (vector / graph) is the orthogonal *where is it stored* axis:
episodic and semantic each live in both, so the axes cannot be collapsed.

:data:`TIER_POLICY` is the one place every per-tier decision lives, and it **replaces** SM-C1's
``SUBSTRATES_BY_TIER`` rather than sitting beside it -- two tables holding one decision is the
divergence this ticket closes.

Decay, TTL and retention are deliberately absent (SM-R5 #80, SM-E9 #116), as is which tiers a
*tenant* gets (SM-C5 #93): no backend stores a tier yet, so either would be config read by nobody.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, FrozenSet, Iterable, Optional, Tuple

from egpt.memory.errors import MemoryOperationError

__all__ = [
    "CONSOLIDATION_SOURCE",
    "CONSOLIDATION_TARGET",
    "DEFAULT_WRITE_TIER",
    "GraphWriteShape",
    "PERSISTENT_TIERS",
    "TIER_POLICY",
    "MemoryTier",
    "TierPolicy",
    "graph_shape_for_tier",
    "policy_for_tier",
    "routes_to",
    "substrates_for_tier",
]


class MemoryTier(str, Enum):
    """Which kind of memory a record, event, or query concerns.

    ``str`` mixin so a config value validates by construction and a serialized record round-trips.
    """

    WORKING = "working"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"


class GraphWriteShape(str, Enum):
    """Which graph port a write to a tier goes through.

    Not a naming difference: ``upsert_fact`` carries SPO and ``source_episode_ids`` provenance that
    ``upsert_episode`` has nowhere to put, so the wrong one silently drops structure (finding F9).
    """

    EPISODE = "episode"
    FACT = "fact"


@dataclass(frozen=True)
class TierPolicy:
    """Everything the seam decides *because of* a record's tier.

    Frozen, and :data:`TIER_POLICY` is the only instantiation site: a policy assembled per call
    lets two code paths disagree about one tier, which is what this table replaces.
    """

    tier: MemoryTier
    #: Where a write to this tier lands; empty means nothing persists it. Substrate *names* rather
    #: than the ``Substrate`` enum, which lives in ``types`` and imports this module. It is a
    #: ``str`` enum, so its members still match.
    substrates: FrozenSet[str]
    #: Non-``None`` exactly when the tier routes to the graph.
    graph_shape: Optional[GraphWriteShape]
    searched_by_default: bool
    #: Exactly one tier may claim the unnamed write.
    is_write_default: bool
    consolidates_from: bool
    consolidates_into: bool
    #: The ticket that builds this tier out, quoted in refusals so a caller learns where it went.
    owner: str


#: Every per-tier decision, in one table. The substrate columns are ADR 0003 section 1's ratified
#: mapping with its two qualifications: ``episodic -> graph`` is intended but not yet realised
#: (no episode node type exists -- SM-G1 #99 settles it), and ``procedural`` is vector-only
#: because SM-R12 shipped ``ProceduralRecord`` as ``Embeddable``.
TIER_POLICY: Dict[MemoryTier, TierPolicy] = {
    MemoryTier.WORKING: TierPolicy(
        tier=MemoryTier.WORKING,
        substrates=frozenset(),
        graph_shape=None,
        # Out of the default read set: it is the in-context buffer SM-C6 owns, so including it
        # would serve the caller back state they already hold.
        searched_by_default=False,
        is_write_default=False,
        consolidates_from=False,
        consolidates_into=False,
        owner="SM-C6 (#94)",
    ),
    MemoryTier.EPISODIC: TierPolicy(
        tier=MemoryTier.EPISODIC,
        substrates=frozenset({"vector", "graph"}),
        graph_shape=GraphWriteShape.EPISODE,
        searched_by_default=True,
        is_write_default=True,  # writes are raw and non-lossy; distilling is `consolidate`'s job
        consolidates_from=True,
        consolidates_into=False,
        owner="SM-E1 (#108)",
    ),
    MemoryTier.SEMANTIC: TierPolicy(
        tier=MemoryTier.SEMANTIC,
        substrates=frozenset({"vector", "graph"}),
        graph_shape=GraphWriteShape.FACT,
        searched_by_default=True,
        is_write_default=False,
        consolidates_from=False,
        consolidates_into=True,
        owner="SM-S1 (#118)",
    ),
    MemoryTier.PROCEDURAL: TierPolicy(
        tier=MemoryTier.PROCEDURAL,
        substrates=frozenset({"vector"}),
        graph_shape=None,
        searched_by_default=True,
        is_write_default=False,
        consolidates_from=False,
        consolidates_into=False,
        owner="SM-S2 (#119)",
    ),
}


def _validate_policy(policy: Dict[MemoryTier, TierPolicy]) -> None:
    """Check the table at import: by the time a bad row reaches a substrate it is a write in the
    wrong place, three frames down."""
    missing = [tier.value for tier in MemoryTier if tier not in policy]
    if missing:
        raise MemoryOperationError(
            f"TIER_POLICY has no row for {missing}. A new tier must be added to the table in the "
            "same change that adds the enum member.",
            field_name="tier",
        )
    for tier, row in policy.items():
        if row.tier is not tier:
            raise MemoryOperationError(f"TIER_POLICY[{tier.value!r}] carries tier {row.tier!r}.", field_name="tier")
        unknown = sorted(row.substrates - {"vector", "graph"})
        if unknown:
            raise MemoryOperationError(
                f"TIER_POLICY[{tier.value!r}] routes to unknown substrate(s) {unknown}.", field_name="substrates"
            )
        # Both directions: no shape on a graph-routed tier silently writes nothing, and a shape on
        # a tier the graph does not store is one nobody applies.
        if (row.graph_shape is not None) != ("graph" in row.substrates):
            raise MemoryOperationError(
                f"TIER_POLICY[{tier.value!r}] has graph_shape={row.graph_shape!r} but substrates "
                f"{sorted(row.substrates)}: a tier has a graph shape exactly when it routes to the graph.",
                field_name="graph_shape",
            )
        if (row.consolidates_from or row.consolidates_into) and not row.substrates:
            raise MemoryOperationError(
                f"TIER_POLICY[{tier.value!r}] takes part in consolidation but persists nowhere.",
                field_name="substrates",
            )
    defaults = [t.value for t, row in policy.items() if row.is_write_default]
    if len(defaults) != 1:
        raise MemoryOperationError(
            f"Exactly one tier is the default write target; the table names {defaults}.", field_name="tier"
        )
    targets = [t.value for t, row in policy.items() if row.consolidates_into]
    if len(targets) != 1:
        raise MemoryOperationError(
            f"Exactly one tier is the consolidation target; the table names {targets}.", field_name="tier"
        )
    # Exactly one, not "at least one": two tiers sharing one bounded pass is the scheduler's
    # question (SM-L1 #122), and a second source added without it starves whichever came second.
    sources = [t.value for t, row in policy.items() if row.consolidates_from]
    if len(sources) != 1:
        raise MemoryOperationError(
            f"Exactly one tier is the consolidation source; the table names {sources}.", field_name="tier"
        )


_validate_policy(TIER_POLICY)


def policy_for_tier(tier: MemoryTier) -> TierPolicy:
    """The policy row for ``tier``. Validation makes a missing row unreachable for an enum member,
    so the refusal catches a caller passing something that is not one -- named, not a ``KeyError``
    three frames down."""
    try:
        return TIER_POLICY[tier]
    except (KeyError, TypeError):
        raise MemoryOperationError(
            f"Tier {tier!r} has no entry in TIER_POLICY, so there is no routing for it. A new tier "
            "must be added to that table in the same change that adds the enum member.",
            field_name="tier",
        ) from None


def substrates_for_tier(tier: MemoryTier) -> FrozenSet[str]:
    """The substrate names a write to ``tier`` should reach. Empty for ``WORKING``."""
    return policy_for_tier(tier).substrates


def graph_shape_for_tier(tier: MemoryTier) -> Optional[GraphWriteShape]:
    """How a write to ``tier`` is shaped on the graph, or ``None`` if it never reaches one."""
    return policy_for_tier(tier).graph_shape


def routes_to(substrate: str, tiers: Iterable[MemoryTier]) -> bool:
    """Whether **any** of ``tiers`` reaches ``substrate`` -- the read path's counterpart to
    :func:`substrates_for_tier`. A leg no requested tier routes to is a guaranteed-empty round
    trip, and a network hop in Mode B."""
    return any(substrate in policy_for_tier(tier).substrates for tier in tiers)


#: The tiers a provider searches when a query names none.
PERSISTENT_TIERS: Tuple[MemoryTier, ...] = tuple(t for t, row in TIER_POLICY.items() if row.searched_by_default)

#: Where an event that names no tier lands.
DEFAULT_WRITE_TIER: MemoryTier = next(t for t, row in TIER_POLICY.items() if row.is_write_default)

#: The tier ``consolidate`` reads, and the tier it writes.
CONSOLIDATION_SOURCE: MemoryTier = next(t for t, row in TIER_POLICY.items() if row.consolidates_from)
CONSOLIDATION_TARGET: MemoryTier = next(t for t, row in TIER_POLICY.items() if row.consolidates_into)
