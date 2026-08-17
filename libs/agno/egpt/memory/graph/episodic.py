"""The episodic subgraph: raw episodes as nodes, linked to the entities they mention (SM-G6 #104).

SM-G1 (#99) shipped the ``(:Episode)`` node and left the mention links to this ticket. What they add
is the thing ADR 0003 section 1 called episodic-to-graph "intended, not yet realised": an episode
that reaches the graph only as a flat node is a row in a different database, while an episode joined
to its entities is reachable from the semantic side and back.

**Non-lossy is the point** (research doc section 3, Zep's episodic subgraph): nothing here distils,
summarises or scores. The node holds the episode's own content and timestamp; the edges say which
entities it names. Turning those entities into semantic facts is consolidation (SM-L*), and
*deciding* which entities a piece of content names is extraction -- SM-G2 (#100). This module is
handed the list and links it.

Every rule below is here, once, rather than in each backend: the mention set an episode carries and
the entity node it addresses have to be identical across substrates, or the vector row's
``entity_ids`` and the graph's edges describe different graphs. Backends route through
:func:`mention_rows`, and ``test_episodic_subgraph.py`` asserts the conformance over both the
reference double and Neo4j.
"""

from typing import Any, Dict, List, Optional, Sequence

from egpt.memory.types import MAX_LIMIT, MemoryOperationError, Scope, escape_key_slot

__all__ = [
    "MENTIONS",
    "entity_node_id_for",
    "entity_node_id_prefix",
    "mention_link_id",
    "mention_rows",
    "normalized_mentions",
    "require_recall_filters",
    "require_recall_limit",
    "require_time_window",
]

#: The edge type from an episode node to an entity node it names. One constant, because a backend
#: that spelled it differently would write a subgraph nothing else can read.
MENTIONS = "MENTIONS"

#: Distinguishes an entity node id from an episode one. Both extend the same five-slot scope key,
#: so without it an episode whose id happened to equal an entity's name would address one node.
_ENTITY_SEGMENT = "entity"


def entity_node_id_for(scope: Scope, name: str) -> str:
    """The id of the ``(:Entity)`` node a mention addresses, per SM-G1's entity merge identity.

    That backend ``MERGE``s an entity on ``(scope_key, name)`` -- the **full** scope, not the
    tenant, so two users of one tenant do not share a node for the same name. This spells that pair
    as one string the way :func:`~egpt.memory.schemas.episodic.graph_node_id_for` spells the
    episode's own node, escaped by :func:`~egpt.memory.types.escape_key_slot`.

    It lives here rather than beside that function because it is a property of the **link**: the
    episode row's ``entity_ids`` and the graph's ``MENTIONS`` edges cannot disagree about which
    entity a mention is if neither side gets to choose, and both reach it through this module.
    It is **not** an entity-resolution key -- ``"Berlin"`` and ``"berlin"`` give two ids, and
    deciding they are one is SM-G8's (#106).

    The extra segment is what keeps the two id spaces disjoint: ``scope.key()`` is five escaped
    slots, so an episode node id is always six segments and an entity node id always seven.
    """
    if not isinstance(name, str) or not name.strip():
        raise MemoryOperationError(
            f"An entity name is required to address its graph node; got {name!r}.",
            field_name="entities",
        )
    return f"{entity_node_id_prefix(scope)}{escape_key_slot(name.strip())}"


def entity_node_id_prefix(scope: Scope) -> str:
    """What every entity node id in ``scope`` starts with -- the half a reader can check.

    The name half is escaped and not recoverable as an identity, so this is what
    :func:`~egpt.memory.schemas.episodic.episode_to_record` verifies a stored ``entity_ids`` against:
    an id built under another scope is pointing at another tenant's entity node, which is the same
    defect the ``graph_node_id`` check beside it catches.
    """
    return f"{scope.key()}/{_ENTITY_SEGMENT}/"


def normalized_mentions(entities: Optional[Sequence[str]], *, episode_id: str) -> List[str]:
    """The entity names an episode mentions: whitespace-trimmed, de-duplicated, order preserved.

    ``None`` means the caller said nothing about entities and yields ``[]``. A ``str`` is refused
    rather than iterated -- ``list("berlin")`` is six one-character entities and raises nothing,
    which is the same corruption ``schemas.episodic._require_list`` exists to catch on the
    projections.

    **Trimmed but not case-folded, and de-duplicated exactly.** So ``"Berlin"`` and ``"berlin"``
    remain two entities. That is deliberate: deciding they are one is entity resolution, which is
    SM-G8's (#106), and folding here would make the graph's identity depend on a policy nobody has
    written -- with no way to recover the original surface form once merged.

    Order is preserved rather than sorted, because it is the order the episode names them in and
    nothing downstream has re-derived it.
    """
    if entities is None:
        return []
    if isinstance(entities, str) or not isinstance(entities, (list, tuple)):
        raise MemoryOperationError(
            f"Episode {episode_id!r} carries entities={entities!r}, which is a "
            f"{type(entities).__name__} rather than a list. Iterating a string would split it into "
            "one entity per character instead of failing.",
            field_name="entities",
        )
    seen: Dict[str, None] = {}
    for value in entities:
        if not isinstance(value, str) or not value.strip():
            raise MemoryOperationError(
                f"Episode {episode_id!r} mentions {value!r}, which is not a non-empty entity name. "
                "An unnamed entity would merge every such mention onto one node.",
                field_name="entities",
            )
        seen.setdefault(value.strip(), None)
    return list(seen)


def mention_link_id(scope: Scope, episode_id: str, name: str) -> str:
    """The identity of one ``MENTIONS`` edge: which episode names which entity, in which scope.

    A backend needs this because ``MERGE`` on a bare relationship pattern is a check-then-act race
    -- the reason SM-G1's node writes ``MERGE`` on a constrained key rather than matching first. An
    edge keyed on this, with a uniqueness constraint behind it, makes "one edge per (episode,
    entity)" the database's guarantee rather than the statement's.

    Injective by construction: the entity id's last segment and the episode id are both escaped by
    :func:`~egpt.memory.types.escape_key_slot`, so neither can contain the ``/`` that joins them.
    """
    return f"{entity_node_id_for(scope, name)}/{escape_key_slot(episode_id)}"


def mention_rows(scope: Scope, episode_id: str, entities: Optional[Sequence[str]]) -> List[Dict[str, Any]]:
    """One backend-agnostic row per mention, in the order the episode names the entities.

    ``name`` is what a graph merges the entity node on; the id the episode row stores is derived
    from it by :func:`entity_node_id_for`, so neither view gets to choose independently.

    ``position`` is why the order is worth keeping: a graph returns an episode's edges in whatever
    order it stores them, so without it the mention list read back from the subgraph would be a
    different permutation of ``EpisodicRecord.entity_ids`` on every read -- two views of one thing
    that never quite agree, and no way to tell that from a real disagreement.

    ``scope_key`` travels on the row rather than being bound per statement, matching how the graph
    backend writes every other row: one statement then carries a whole batch and each row lands
    under the scope it was built with.
    """
    return [
        {
            "scope_key": scope.key(),
            "link_id": mention_link_id(scope, episode_id, name),
            "episode_id": episode_id,
            "name": name,
            "position": position,
        }
        for position, name in enumerate(normalized_mentions(entities, episode_id=episode_id))
    ]


def require_time_window(since: Optional[int], until: Optional[int]) -> None:
    """Refuse a window that selects no instant. Valid time is half-open: ``since <= t < until``.

    ``until == since`` is empty rather than "that one second", so it is refused with the inverted
    case: a caller asking for one instant gets an empty page that looks like a genuine miss.
    """
    for name, value in (("since", since), ("until", until)):
        if value is None:
            continue
        # `bool` is an `int` subclass, so `since=True` would silently mean epoch second 1 -- the
        # same coercion `types._epoch_or_none` refuses on every other timestamp at this seam.
        if isinstance(value, bool) or not isinstance(value, int):
            raise MemoryOperationError(
                f"{name} must be epoch seconds as an int; got {value!r}.",
                field_name=name,
            )
    if since is not None and until is not None and until <= since:
        raise MemoryOperationError(
            f"until ({until}) is not after since ({since}), so the window is valid at no instant. "
            "The window is half-open: since <= t_valid < until.",
            field_name="until",
        )


def require_recall_limit(limit: Optional[int]) -> None:
    """Range-check the bound, on the rule ``MemoryQuery.limit`` already states.

    Derived from that constant rather than restated, so a change to the ceiling reaches both.

    Both halves matter, and neither is theoretical here. A **negative** limit is a bare slice, so
    ``limit=-1`` on an in-memory backend returns everything except the last row -- a caller
    computing ``limit = budget - used`` and going negative gets a nearly-full page with the most
    recent episode silently missing -- while Cypher rejects it outright, so the double and the
    engine would disagree about the same call. **Above the ceiling** is a full scan wearing a page
    request, which is the enumeration :func:`require_recall_filters` refuses in its other form.
    """
    if limit is None:
        return
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise MemoryOperationError(
            f"limit must be an integer >= 0 or None for unlimited; got {limit!r}.",
            field_name="limit",
        )
    if limit < 0:
        raise MemoryOperationError(
            f"limit is {limit}. A negative limit is not a smaller page -- as a slice it truncates "
            "from the end, so the rows a caller most wanted are the ones dropped. Pass 0 for no "
            "rows or None for unlimited.",
            field_name="limit",
        )
    if limit > MAX_LIMIT:
        raise MemoryOperationError(
            f"limit is {limit}, above the {MAX_LIMIT} ceiling MemoryQuery.limit already enforces. "
            "A page that large is an enumeration of the scope, which is SM-G9's (#107) to define.",
            field_name="limit",
        )


def require_recall_filters(entities: Optional[Sequence[str]], since: Optional[int], until: Optional[int]) -> None:
    """Refuse a recall that filters on nothing, and an entity filter that matches nothing.

    An unfiltered call is an enumeration of every episode in scope. What enumeration means over a
    temporal graph -- current belief or every revision, nodes or edges -- is SM-G9's (#107) to
    define, and ``test_the_graph_is_not_enumerable`` pins its absence; a verb that backed into it
    by accepting three ``None``s would settle that question without anyone deciding it.

    ``entities=[]`` is refused for the reason ``entities=None`` is not: an empty anchor set can be
    read as "any entity" or "no entity", and either reading returns a page a caller cannot tell
    from a genuine miss.
    """
    if entities is not None and not entities:
        raise MemoryOperationError(
            "entities is empty. Pass None for no mention filter; an empty list is ambiguous "
            "between 'any entity' and 'no entity', and both return an empty-looking page.",
            field_name="entities",
        )
    if entities is None and since is None and until is None:
        # No single field, unlike the refusal above: supplying `since` or `until` resolves this
        # equally well, so naming `entities` would send a caller narrowing that one field when any
        # of the three would do.
        raise MemoryOperationError(
            "recall_episodes needs at least one of entities, since or until. With none of them "
            "this enumerates every episode in scope, and graph enumeration semantics are SM-G9's "
            "(#107) to define.",
            field_name=None,
        )
