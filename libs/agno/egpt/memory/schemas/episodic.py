"""The episode as the memory seam builds and returns it (SM-E1 #108).

The persisted shape is SM-C3's :class:`~egpt.db.schemas.memory.EpisodicRecord`, re-exported here
rather than redefined -- two episode dataclasses is the divergence SM-C3 (#91) avoided when it
promoted SM-R12's model, and SM-E5 (#112) and SM-G6 (#104) both name this module as where they add
to the episode.

Net-new is the pair of projections between that row and the seam envelope
(:mod:`egpt.memory.types`), plus the referent of ``EpisodicRecord.graph_node_id``: ADR 0003
section 1 recorded it as *"an untyped pointer with no defined referent"* because no episode node
type existed. SM-G1 (#99) shipped ``(:Episode)``, so :func:`graph_node_id_for` names it.

Nothing here reads or writes a store, so there are no async twins -- these are the
pure-construction functions CLAUDE.md exempts. Persisting an episode is SM-V1's (#95); capturing
one from a run is SM-E2's (#109).
"""

from typing import Any, Dict, List, Optional, Tuple

from egpt.db.schemas.memory import EpisodicRecord
from egpt.memory.graph.episodic import entity_node_id_for, entity_node_id_prefix, normalized_mentions
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import (
    MemoryEvent,
    MemoryOperationError,
    MemoryRecord,
    Scope,
    escape_key_slot,
)
from egpt.utils.dttm import now_epoch_s

__all__ = [
    "EPISODE_KEYS",
    "PROVENANCE_KEYS",
    "TOPICS_KEY",
    "EpisodicRecord",
    "entity_node_id_for",
    "episode_details",
    "episode_from_event",
    "episode_to_record",
    "graph_node_id_for",
]


#: The episode fields a reader gets in ``MemoryRecord.metadata``, and the floor
#: :func:`episode_details` fills. The same contract :data:`~egpt.memory.types.FUSION_KEYS` provides
#: on the read path: without a floor, a consumer that branches on ``importance`` raises ``KeyError``
#: against whichever episode did not happen to carry one.
#:
#: These are the SM-R12 section 4.3 fields that survive the projection -- context (when/where/why),
#: SM-R5's decay inputs, and SM-R6's segmentation metadata. They are *data*: scoring on them is
#: SM-E4 (#111) / SM-E5 (#112), and segmenting is SM-E3 (#110).
EPISODE_KEYS: Tuple[str, ...] = (
    "occurred_at",
    "location",
    "cause",
    "importance",
    "last_accessed_at",
    "revision",
    "refined",
    "boundary_method",
    "boundary_trigger",
    "boundary_confidence",
    "segmenter_version",
)

#: ``topics`` has no column on ``EpisodicRecord`` but is first-class on both ``MemoryEvent`` and
#: ``MemoryRecord``, so the row carries it in ``metadata`` and the projection restores it. Dropping
#: it instead would lose caller data on a round trip through the episode tier.
TOPICS_KEY = "topics"

_RESERVED_METADATA_KEYS = frozenset(EPISODE_KEYS) | {TOPICS_KEY}

#: The reference keys :func:`episode_to_record` always puts in ``MemoryRecord.provenance``. Empty
#: rather than absent when the episode has none, for the reason :data:`EPISODE_KEYS` is a floor.
PROVENANCE_KEYS: Tuple[str, ...] = ("entity_ids", "graph_node_id", "supersedes", "superseded_by")


def graph_node_id_for(scope: Scope, episode_id: str) -> str:
    """The id of the ``(:Episode)`` node this episode is stored as, per SM-G1's write identity.

    That backend ``MERGE``s an episode node on ``(scope_key, id, live: true)``. This addresses the
    first two: the scope key with the episode id appended as a sixth slot, escaped by
    :func:`~egpt.memory.types.escape_key_slot`, because fixed arity without escaping is not
    injectivity (an id containing ``/`` would otherwise read as two slots). The ``live`` marker is
    deliberately not encoded -- it selects which *revision* is current, and one logical episode has
    many under SCD-2, so a pointer that pinned it would name a row rather than the episode.

    Derived, never free-form: the vector row and the graph node cannot disagree about which node an
    episode is if neither side gets to choose. Whether the node *exists* is a different question --
    a deployment with no graph substrate has none, and SM-R12 section 9 makes cross-tier references
    advisory, so a reader tolerates a missing target. Resolving this id against the graph and
    checking the two views still agree is ``egpt.memory.episodic.link.resolve_episode_link``
    (SM-E10 #117).
    """
    if not isinstance(episode_id, str) or not episode_id.strip():
        raise MemoryOperationError(
            f"An episode id is required to address its graph node; got {episode_id!r}.",
            field_name="id",
        )
    return f"{scope.key()}/{escape_key_slot(episode_id)}"


def episode_details(
    occurred_at: Optional[int] = None,
    location: Optional[str] = None,
    cause: Optional[str] = None,
    importance: float = 0.5,
    last_accessed_at: Optional[int] = None,
    revision: int = 0,
    refined: bool = False,
    boundary_method: Optional[str] = None,
    boundary_trigger: Optional[float] = None,
    boundary_confidence: Optional[float] = None,
    segmenter_version: Optional[str] = None,
    **extra: Any,
) -> Dict[str, Any]:
    """Build a metadata payload carrying every key in :data:`EPISODE_KEYS`.

    Extra keys are free -- the contract is a floor, not a ceiling.
    """
    details: Dict[str, Any] = {
        "occurred_at": occurred_at,
        "location": location,
        "cause": cause,
        "importance": importance,
        "last_accessed_at": last_accessed_at,
        "revision": revision,
        "refined": refined,
        "boundary_method": boundary_method,
        "boundary_trigger": boundary_trigger,
        "boundary_confidence": boundary_confidence,
        "segmenter_version": segmenter_version,
    }
    details.update(extra)
    return details


def _require_list(value: Any, name: str, episode_id: str) -> List[str]:
    """Refuse a list that is not one, rather than copying it into something else.

    ``list("deploy")`` is six one-character topics and raises nothing. **Every list this module
    copies goes through here, on both projections** -- a string reaching the write path is copied
    into a list of characters, which the read path then accepts as a well-formed list, so the
    corruption survives a round trip with nothing to detect it. On the read side the same value
    arrives from ``MemoryRow.__post_init__``, which copies a value that is already a list and
    leaves any other alone, so a row inserted straight into the database keeps its bare string.
    """
    if not isinstance(value, list):
        raise MemoryOperationError(
            f"Episode {episode_id!r} carries {name}={value!r}, which is a {type(value).__name__} "
            "rather than a list. Copying it would split it into characters instead of failing.",
            field_name=name,
        )
    return list(value)


def _reject_reserved_metadata(metadata: Dict[str, Any], where: str) -> None:
    """Refuse caller metadata that would collide with an episode field.

    Called on both projections, because the collision is the same one in both directions: the
    episode's own ``importance`` and the caller's would occupy one key, and whichever the
    projection wrote last would silently win. Naming the keys lets the caller move them.

    Ordering differs by direction, which changes only *which* refusal a caller sees:
    :func:`episode_to_record` pops ``topics`` before calling this, so a row carrying a non-list
    ``metadata["topics"]`` is refused by :func:`_require_list` naming the type, not here naming the
    reserved key. Both are ``MemoryOperationError``; the first is the more useful message.
    """
    clashing = sorted(key for key in metadata if key in _RESERVED_METADATA_KEYS)
    if clashing:
        raise MemoryOperationError(
            f"{where} metadata uses {clashing}, which the episode projection fills from the record's "
            f"own fields. Reserved keys: {sorted(_RESERVED_METADATA_KEYS)}.",
            field_name="metadata",
        )


def episode_from_event(event: MemoryEvent) -> EpisodicRecord:
    """Project a write-back event into the episode row that stores it.

    The time decisions are the ones ``CoLocatedMemoryProvider._prepare`` already makes, made here
    too rather than differently: ``occurred_at`` is valid time and defaults to now when the caller
    did not say when it happened, and ``created_at`` is the transaction time of this write.

    ``importance`` is left at the row default. Scoring it is SM-E5's (#112), and a value invented
    here would rank episodes against each other on nothing.

    ``MemoryEvent.role`` is **not carried**. Neither ``EpisodicRecord`` nor ``MemoryRecord`` has a
    role field, so keeping it would mean inventing a metadata convention on behalf of SM-E2 (#109),
    which owns the capture path and is the only caller with a role to place. Pinned by
    ``test_role_is_not_preserved_here`` so the choice is made there rather than inherited from here.

    ``entity_ids`` is filled from ``MemoryEvent.entities`` (SM-G6 #104), through the same
    :func:`~egpt.memory.graph.episodic.entity_node_id_for` the graph's ``MENTIONS`` edges are built
    with -- so the row's list and the subgraph's edges name one set of entity nodes rather than two.
    """
    if event.tier is not MemoryTier.EPISODIC:
        raise MemoryOperationError(
            f"Only an episodic event is an episode; this one is {event.tier.value!r}. Route it by "
            "tier (egpt.memory.tiers) rather than storing it in the wrong shape.",
            field_name="tier",
        )
    if not event.id or not event.id.strip():
        # The same requirement, and the same reason, as `Neo4jGraphSubstrate._episode_rows`: one
        # logical episode has one id across both substrates, so minting a second one here would
        # surface the episode twice after fusion (finding F1). Whitespace-only is refused here
        # rather than left to `graph_node_id_for`, which would name the graph node instead of the
        # id that is actually missing. It is refused, not stripped: the graph substrate stores
        # `event.id` verbatim, so normalising here would make the row and the node disagree.
        raise MemoryOperationError(
            "MemoryEvent.id is required here: it is the cross-substrate dedup key, and the row and "
            "the graph node are addressed by it. The provider stamps one before the call.",
            field_name="id",
        )
    _reject_reserved_metadata(event.metadata, "MemoryEvent")

    now = now_epoch_s()
    occurred_at = event.occurred_at if event.occurred_at is not None else now
    metadata = dict(event.metadata)
    if event.topics is not None:
        metadata[TOPICS_KEY] = _require_list(event.topics, TOPICS_KEY, event.id)
    scope = event.scope
    return EpisodicRecord(
        id=event.id,
        tenant_id=scope.tenant_id,
        user_id=scope.user_id,
        agent_id=scope.agent_id,
        team_id=scope.team_id,
        session_id=scope.session_id,
        created_at=now,
        metadata=metadata,
        content=event.content,
        occurred_at=occurred_at,
        t_valid=occurred_at,
        entity_ids=[
            entity_node_id_for(scope, name) for name in normalized_mentions(event.entities, episode_id=event.id)
        ],
        graph_node_id=graph_node_id_for(scope, event.id),
    )


def _scope_of(episode: EpisodicRecord) -> Scope:
    return Scope(
        tenant_id=episode.tenant_id,
        user_id=episode.user_id,
        agent_id=episode.agent_id,
        team_id=episode.team_id,
        session_id=episode.session_id,
    )


def episode_to_record(episode: EpisodicRecord) -> MemoryRecord:
    """Project a stored episode into the record the seam returns.

    ``superseded_by`` narrows from the row's list to the envelope's single id, and only when there
    is exactly one: an SM-R6 re-cut can split one episode into several, and naming one of them "the
    replacement" would send a reader following the supersede chain down one branch of a fork. The
    full list is always in ``provenance``, so nothing is lost either way.
    """
    metadata = dict(episode.metadata)
    raw_topics = metadata.pop(TOPICS_KEY, None)
    topics: Optional[List[str]] = None if raw_topics is None else _require_list(raw_topics, TOPICS_KEY, episode.id)
    _reject_reserved_metadata(metadata, f"Episode {episode.id!r}")
    scope = _scope_of(episode)
    if episode.graph_node_id is not None:
        expected = graph_node_id_for(scope, episode.id)
        if episode.graph_node_id != expected:
            raise MemoryOperationError(
                f"Episode {episode.id!r} carries a graph_node_id that does not address its own "
                "graph node. The link is derived from the scope and the episode id "
                "(graph_node_id_for), so a different value points at another episode's node.",
                field_name="graph_node_id",
            )
    entity_ids = _require_list(episode.entity_ids, "entity_ids", episode.id)
    # The same check as `graph_node_id` above, one link over: an entity id is derived from the
    # scope, so one built elsewhere addresses another tenant's entity node. Only the prefix is
    # checkable -- the name half is escaped and carries no independent identity to re-derive from.
    foreign = [value for value in entity_ids if not value.startswith(entity_node_id_prefix(scope))]
    if foreign:
        raise MemoryOperationError(
            f"Episode {episode.id!r} carries {len(foreign)} entity_id(s) built under another scope, "
            "so they address another scope's entity nodes. The ids are derived from the scope and "
            "the entity name (entity_node_id_for).",
            field_name="entity_ids",
        )

    metadata.update(
        episode_details(
            occurred_at=episode.occurred_at,
            location=episode.location,
            cause=episode.cause,
            importance=episode.importance,
            last_accessed_at=episode.last_accessed_at,
            revision=episode.revision,
            refined=episode.refined,
            boundary_method=episode.boundary_method,
            boundary_trigger=episode.boundary_trigger,
            boundary_confidence=episode.boundary_confidence,
            segmenter_version=episode.segmenter_version,
        )
    )
    superseded_by = _require_list(episode.superseded_by, "superseded_by", episode.id)
    return MemoryRecord(
        id=episode.id,
        tier=MemoryTier.EPISODIC,
        content=episode.content,
        scope=scope,
        topics=topics,
        provenance={
            "entity_ids": entity_ids,
            "graph_node_id": episode.graph_node_id,
            "supersedes": _require_list(episode.supersedes, "supersedes", episode.id),
            "superseded_by": superseded_by,
        },
        t_valid=episode.t_valid,
        t_invalid=episode.t_invalid,
        created_at=episode.created_at,
        expired_at=episode.expired_at,
        superseded_by=superseded_by[0] if len(superseded_by) == 1 else None,
        metadata=metadata,
    )
