"""Resolving an episode's graph link, and its drift check (SM-E10 #117).

The port methods themselves (``upsert_episodes``, ``bounds_for_ids``, ``recall_episodes``) are
each already covered where they are defined -- SM-G6 (#104) for the write and mention-replace
behaviour, SM-R10/SM-G1 for bounds. This file is the one that drives them together through the
seam a caller actually uses: write an episode, then ask whether the graph agrees with it.
"""

from dataclasses import replace

from egpt.memory.episodic.link import EpisodeLink, aresolve_episode_link, episode_link_drift, resolve_episode_link
from egpt.memory.schemas.episodic import episode_from_event
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import MemoryEvent, Scope
from tests.unit.memory._fakes import FakeGraph

ACME = Scope(tenant_id="acme", user_id="ada")


def an_event(**overrides):
    defaults = dict(
        content="ada moved to berlin",
        scope=ACME,
        tier=MemoryTier.EPISODIC,
        id="ep-1",
        occurred_at=100,
        entities=["Berlin"],
    )
    defaults.update(overrides)
    return MemoryEvent(**defaults)


def test_an_episode_the_graph_has_never_seen_does_not_resolve():
    episode = episode_from_event(an_event())
    graph = FakeGraph()

    link = resolve_episode_link(ACME, episode, graph)

    assert link.exists is False
    assert link.graph_node_id == episode.graph_node_id
    assert link.t_valid is None
    assert link.entity_ids is None


def test_a_written_episode_resolves_to_its_node_and_edges():
    """AC: an episode resolves to its graph node + edges."""
    event = an_event()
    episode = episode_from_event(event)
    graph = FakeGraph()
    graph.upsert_episodes(ACME, [event])

    link = resolve_episode_link(ACME, episode, graph)

    assert link.exists is True
    assert link.t_valid == episode.t_valid == 100
    assert link.t_invalid is None
    assert link.entity_ids == frozenset(episode.entity_ids)
    assert episode_link_drift(episode, link) == []


async def test_the_async_resolve_agrees_with_the_sync_one():
    event = an_event()
    episode = episode_from_event(event)
    graph = FakeGraph()
    await graph.aupsert_episodes(ACME, [event])

    link = await aresolve_episode_link(ACME, episode, graph)

    assert link == resolve_episode_link(ACME, episode, graph)


def test_an_update_that_changes_time_and_entities_keeps_the_two_views_in_step():
    """AC: consistency maintained on updates. ``write`` is upsert-by-id (co_located.py), so a
    second event with the same id is the only update path that exists today; this drives it the
    same way and checks the resolve reflects the *new* state, not the one it replaced."""
    graph = FakeGraph()
    graph.upsert_episodes(ACME, [an_event()])

    second = an_event(content="ada moved to paris", occurred_at=200, entities=["Paris"])
    graph.upsert_episodes(ACME, [second])
    updated_episode = episode_from_event(second)

    link = resolve_episode_link(ACME, updated_episode, graph)

    assert link.t_valid == 200
    assert link.entity_ids == frozenset(updated_episode.entity_ids)
    assert episode_link_drift(updated_episode, link) == []

    # The entity set was *replaced*, not unioned -- resolving against the pre-update row's own
    # entity_ids must show the drift the stale mention would otherwise hide.
    stale_episode = episode_from_event(an_event())
    assert episode_link_drift(stale_episode, link) == ["t_valid", "entity_ids"]


def test_an_absent_link_is_not_reported_as_drift():
    """SM-R12 section 9: the cross-tier reference is advisory, so a vector-only deployment (no
    graph leg) is not a defect ``episode_link_drift`` should flag."""
    episode = episode_from_event(an_event())
    link = EpisodeLink(graph_node_id=episode.graph_node_id, exists=False)

    assert episode_link_drift(episode, link) == []


def test_entity_ids_is_none_rather_than_empty_when_t_valid_is_unset():
    """An episode with no ``t_valid`` cannot be windowed, so the mention half is undetermined --
    reporting it as an empty set would read as "names nothing", which is a different claim."""
    episode = replace(episode_from_event(an_event()), t_valid=None)
    graph = FakeGraph()
    graph.upsert_episodes(ACME, [an_event()])

    link = resolve_episode_link(ACME, episode, graph)

    assert link.exists is True
    assert link.entity_ids is None
    # The graph still reports its own t_valid (100, from the written event) -- comparing it
    # against the row's cleared field is a real drift, not something the None short-circuit hides.
    assert episode_link_drift(episode, link) == ["t_valid"]
