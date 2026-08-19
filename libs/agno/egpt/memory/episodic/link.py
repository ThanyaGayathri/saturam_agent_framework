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
from typing import FrozenSet, List, Optional, Tuple

from egpt.memory.schemas.episodic import EpisodicRecord, graph_node_id_for
from egpt.memory.substrates import GraphSubstrate
from egpt.memory.types import MemoryRecord, Scope

__all__ = ["EpisodeLink", "aresolve_episode_link", "episode_link_drift", "resolve_episode_link"]


@dataclass(frozen=True)
class EpisodeLink:
    """What the graph currently reports for one episode id, next to nothing yet from the row.

    ``None`` is used rather than ``frozenset()`` for ``entity_ids`` when it could not be
    determined -- an empty frozenset would read as "checked and names nothing", which is a
    different claim from "could not be checked". See :func:`resolve_episode_link` for the cases
    that produce it.
    """

    graph_node_id: str
    exists: bool
    t_valid: Optional[int] = None
    t_invalid: Optional[int] = None
    entity_ids: Optional[FrozenSet[str]] = None


def _window(t_valid: int) -> Tuple[int, int]:
    """The half-open one-second window that finds the row at exactly this instant.

    ``t_valid`` is epoch-seconds, so ``t_valid + 1`` is the smallest ``[since, until)`` that
    still contains it. Centralised because a caller windowing at sub-second precision later needs
    to widen this, not hunt down every ``+ 1`` this module wrote separately.
    """
    return t_valid, t_valid + 1


def _entity_ids_of(episode_id: str, hits: List[MemoryRecord]) -> Optional[FrozenSet[str]]:
    """Linear scan over one instant's worth of episodes -- fine, since a one-second window over
    episodic data returns O(1) rows in any realistic corpus; this is not a hot path over a page."""
    for record in hits:
        if record.id == episode_id:
            # `.get("entity_ids")` (no default) returns None for both "no such key" and "key
            # present but set to None" -- `or []` folds both into an empty list rather than
            # propagating None into `frozenset(...)`, which would raise. `.get("entity_ids", [])`
            # would NOT catch the second case: a present-but-None value passes straight through.
            return frozenset(record.provenance.get("entity_ids") or [])
    return None


def _fallback_window(
    entity_ids: Optional[FrozenSet[str]], hit_t_valid: Optional[int], episode_t_valid: int
) -> Optional[Tuple[int, int]]:
    """A second window to try, or ``None`` when the first attempt is trusted as final.

    The first window is built from the *caller's* ``episode.t_valid`` -- correct when the two
    views agree, but blind exactly when they do not: if the graph has since moved the row to a
    new ``t_valid`` (an update, not an absence), that window looks at the wrong instant and finds
    nothing, so :func:`_entity_ids_of` returns ``None`` -- which :func:`episode_link_drift` then
    reads as "not checked" and silently skips, masking real entity drift behind the reported
    ``t_valid`` drift instead of alongside it. Retrying at the graph's own ``hit_t_valid`` closes
    that gap without touching the case this is not about: ``entity_ids is not None`` (found first
    try) or ``hit_t_valid is None`` (nothing to retry at, matching the row's own unwindowable
    case) both return ``None`` here, same as the two views agreeing.

    **Does not close every case.** ``hit_t_valid`` is itself a snapshot from the ``bounds_for_ids``
    call moments earlier in :func:`resolve_episode_link`; if a second writer lands between that
    call and this one such that the row's *new* ``t_valid`` happens to equal ``episode_t_valid``
    again, this guard reads "no drift" and returns ``None``, and the entity check is skipped for
    that resolve. There is no snapshot isolation across the two round trips this module makes, so
    a caller needing a stronger guarantee than "usually closes the gap, cannot close a race between
    its own two reads" must treat ``entity_ids is None`` on an existing link as "undetermined", not
    "agreed" -- exactly as :func:`episode_link_drift` already does.
    """
    if entity_ids is not None or hit_t_valid is None or hit_t_valid == episode_t_valid:
        return None
    return _window(hit_t_valid)


def resolve_episode_link(scope: Scope, episode: EpisodicRecord, graph: GraphSubstrate) -> EpisodeLink:
    """Look up what the graph currently believes about ``episode``, or that it has no opinion.

    Two round trips, not one: :meth:`GraphSubstrate.bounds_for_ids` carries no mention data (it is
    shared with facts and relations, neither of which have any), so the entity half needs
    :meth:`GraphSubstrate.recall_episodes` regardless. The second call is skipped when the first
    finds nothing live-or-expired to resolve, since a window query for an id the graph has never
    seen only spends a round trip to learn what ``bounds_for_ids`` already said. A third,
    conditional call retries the entity half at the graph's own ``t_valid`` when the row's own
    window missed it because the graph had already moved on -- see :func:`_fallback_window`.
    """
    node_id = episode.graph_node_id or graph_node_id_for(scope, episode.id)
    bounds = graph.bounds_for_ids(scope, [episode.id])
    hit = bounds.get(episode.id)
    if hit is None:
        return EpisodeLink(graph_node_id=node_id, exists=False)
    entity_ids: Optional[FrozenSet[str]] = None
    if episode.t_valid is not None:
        since, until = _window(episode.t_valid)
        entity_ids = _entity_ids_of(episode.id, graph.recall_episodes(scope, since=since, until=until))
        fallback = _fallback_window(entity_ids, hit.t_valid, episode.t_valid)
        if fallback is not None:
            since, until = fallback
            entity_ids = _entity_ids_of(episode.id, graph.recall_episodes(scope, since=since, until=until))
    return EpisodeLink(
        graph_node_id=node_id,
        exists=True,
        t_valid=hit.t_valid,
        t_invalid=hit.t_invalid,
        entity_ids=entity_ids,
    )


async def aresolve_episode_link(scope: Scope, episode: EpisodicRecord, graph: GraphSubstrate) -> EpisodeLink:
    """Async variant of :func:`resolve_episode_link`."""
    node_id = episode.graph_node_id or graph_node_id_for(scope, episode.id)
    bounds = await graph.abounds_for_ids(scope, [episode.id])
    hit = bounds.get(episode.id)
    if hit is None:
        return EpisodeLink(graph_node_id=node_id, exists=False)
    entity_ids: Optional[FrozenSet[str]] = None
    if episode.t_valid is not None:
        since, until = _window(episode.t_valid)
        entity_ids = _entity_ids_of(episode.id, await graph.arecall_episodes(scope, since=since, until=until))
        fallback = _fallback_window(entity_ids, hit.t_valid, episode.t_valid)
        if fallback is not None:
            since, until = fallback
            entity_ids = _entity_ids_of(episode.id, await graph.arecall_episodes(scope, since=since, until=until))
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

    ``entity_ids is None`` on an *existing* link is not read as agreement -- it means the check
    was skipped, not passed, and there is no drift entry for it. Two cases remain even after
    :func:`_fallback_window` closes the "graph moved on" one: the episode itself carries no
    ``t_valid`` (nothing to window at all), or the episode has since been expired in the graph, so
    ``recall_episodes``'s current-belief filter has nothing left to return at any instant.
    """
    if not link.exists:
        return []
    drift = []
    if link.t_valid != episode.t_valid:
        drift.append("t_valid")
    if link.t_invalid != episode.t_invalid:
        drift.append("t_invalid")
    if link.entity_ids is not None and link.entity_ids != frozenset(episode.entity_ids):
        drift.append("entity_ids")
    return drift
