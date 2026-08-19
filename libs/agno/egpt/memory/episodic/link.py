"""Resolving an episode's graph link, and checking it stays in step (SM-E10 #117).

SM-E1 derives the link -- ``schemas.episodic.graph_node_id_for`` names the ``(:Episode)`` node,
``EpisodicRecord.entity_ids`` names the ``MENTIONS`` targets -- and SM-G6 makes the graph side
write both. Neither side reads the other back: this module is that resolve, and the comparison
that turns it into a pass/fail for a caller who wants to know whether they still agree.

``exists=False`` is not itself a defect. SM-R12 section 9 makes the cross-tier reference
advisory: a vector-only deployment has no graph leg to resolve against, and a write whose graph
half has not landed yet (or failed and is mid-retry, see ``co_located.write``'s
``PartialWriteError``) is a timing gap, not drift.

``bounds_for_ids`` (not ``traverse``) is the existence/time check, matching how the provider's own
point-in-time reconciliation reads the graph's authority for facts (``_reconcile_bounds``): it
answers by id across every revision, current or expired, so a caller does not need current belief
to hold first. ``recall_episodes`` is the only port method that returns ``MENTIONS`` targets, so
the entity half needs it regardless -- windowed to the row's own ``t_valid`` rather than filtered
by entity name, because ``EpisodicRecord`` only carries the derived ids
(:func:`~egpt.memory.graph.episodic.entity_node_id_for`'s output), not the original names
``recall_episodes(entities=...)`` filters on.
"""

from dataclasses import dataclass
from typing import FrozenSet, List, Optional

from egpt.memory.schemas.episodic import EpisodicRecord, graph_node_id_for
from egpt.memory.substrates import GraphSubstrate
from egpt.memory.types import MemoryRecord, Scope

__all__ = ["EpisodeLink", "aresolve_episode_link", "episode_link_drift", "resolve_episode_link"]


@dataclass(frozen=True)
class EpisodeLink:
    """What the graph currently reports for one episode id, next to nothing yet from the row.

    ``entity_ids`` is ``None`` rather than empty when it could not be determined -- an episode
    with no ``t_valid`` cannot be windowed, and reporting an empty set there would read as "names
    nothing" for a case that is actually "not checked", the same false-empty :func:`_require_list`
    guards on the write side.
    """

    graph_node_id: str
    exists: bool
    t_valid: Optional[int] = None
    t_invalid: Optional[int] = None
    entity_ids: Optional[FrozenSet[str]] = None


def _entity_ids_of(episode_id: str, hits: List[MemoryRecord]) -> Optional[FrozenSet[str]]:
    for record in hits:
        if record.id == episode_id:
            return frozenset(record.provenance.get("entity_ids") or [])
    return None


def resolve_episode_link(scope: Scope, episode: EpisodicRecord, graph: GraphSubstrate) -> EpisodeLink:
    """Look up what the graph currently believes about ``episode``, or that it has no opinion.

    Two round trips, not one: :meth:`GraphSubstrate.bounds_for_ids` carries no mention data (it is
    shared with facts and relations, neither of which have any), so the entity half needs
    :meth:`GraphSubstrate.recall_episodes` regardless. The second call is skipped when the first
    finds nothing live-or-expired to resolve, since a window query for an id the graph has never
    seen only spends a round trip to learn what ``bounds_for_ids`` already said.
    """
    node_id = graph_node_id_for(scope, episode.id)
    bounds = graph.bounds_for_ids(scope, [episode.id])
    hit = bounds.get(episode.id)
    if hit is None:
        return EpisodeLink(graph_node_id=node_id, exists=False)
    entity_ids: Optional[FrozenSet[str]] = None
    if episode.t_valid is not None:
        window = graph.recall_episodes(scope, since=episode.t_valid, until=episode.t_valid + 1)
        entity_ids = _entity_ids_of(episode.id, window)
    return EpisodeLink(
        graph_node_id=node_id,
        exists=True,
        t_valid=hit.t_valid,
        t_invalid=hit.t_invalid,
        entity_ids=entity_ids,
    )


async def aresolve_episode_link(scope: Scope, episode: EpisodicRecord, graph: GraphSubstrate) -> EpisodeLink:
    """Async variant of :func:`resolve_episode_link`."""
    node_id = graph_node_id_for(scope, episode.id)
    bounds = await graph.abounds_for_ids(scope, [episode.id])
    hit = bounds.get(episode.id)
    if hit is None:
        return EpisodeLink(graph_node_id=node_id, exists=False)
    entity_ids: Optional[FrozenSet[str]] = None
    if episode.t_valid is not None:
        window = await graph.arecall_episodes(scope, since=episode.t_valid, until=episode.t_valid + 1)
        entity_ids = _entity_ids_of(episode.id, window)
    return EpisodeLink(
        graph_node_id=node_id,
        exists=True,
        t_valid=hit.t_valid,
        t_invalid=hit.t_invalid,
        entity_ids=entity_ids,
    )


def episode_link_drift(episode: EpisodicRecord, link: EpisodeLink) -> List[str]:
    """Field names where ``link`` disagrees with ``episode``, empty when they agree or ``link`` is absent.

    An absent link is not drift (see the module docstring); it is reported as ``exists`` on
    ``link`` itself, for a caller who wants to distinguish "not there yet" from "there and wrong".
    """
    if not link.exists:
        return []
    drift = []
    if link.t_valid != episode.t_valid:
        drift.append("t_valid")
    if link.entity_ids is not None and link.entity_ids != frozenset(episode.entity_ids):
        drift.append("entity_ids")
    return drift
