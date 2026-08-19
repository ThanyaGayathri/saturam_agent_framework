"""Resolving an episode's graph link, and its drift check (SM-E10 #117).

The port methods themselves (``upsert_episodes``, ``bounds_for_ids``, ``recall_episodes``) are
each already covered where they are defined -- SM-G6 (#104) for the write and mention-replace
behaviour, SM-R10/SM-G1 for bounds. This file is the one that drives them together through the
seam a caller actually uses: write an episode, then ask whether the graph agrees with it.
"""

from dataclasses import replace

from egpt.memory.episodic.link import EpisodeLink, aresolve_episode_link, episode_link_drift, resolve_episode_link
from egpt.memory.schemas.episodic import episode_from_event, graph_node_id_for
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


def test_resolving_with_a_stale_snapshot_still_finds_the_current_entities():
    """The regression this pins: a caller holding the *pre-update* row (the one
    ``episode_from_event`` would have built before the second write landed) windows at the stale
    ``t_valid`` first, finds nothing there, and must retry at the graph's own ``t_valid`` rather
    than reporting the entity half as agreeing by silently skipping it. Drives
    ``resolve_episode_link`` itself with the stale row -- not just ``episode_link_drift`` against
    an already-resolved link -- so it exercises the windowed-query path the masking lived in."""
    graph = FakeGraph()
    graph.upsert_episodes(ACME, [an_event()])
    graph.upsert_episodes(ACME, [an_event(content="ada moved to paris", occurred_at=200, entities=["Paris"])])

    stale_episode = episode_from_event(an_event())  # t_valid=100, entities=["Berlin"]
    link = resolve_episode_link(ACME, stale_episode, graph)

    # Found via the fallback window, not left `None` -- the graph's *current* mentions, which
    # disagree with the stale row's own, same as `t_valid` does.
    assert link.entity_ids is not None
    assert set(episode_link_drift(stale_episode, link)) == {"t_valid", "entity_ids"}


async def test_the_async_fallback_agrees_with_the_sync_one():
    graph = FakeGraph()
    await graph.aupsert_episodes(ACME, [an_event()])
    await graph.aupsert_episodes(ACME, [an_event(content="ada moved to paris", occurred_at=200, entities=["Paris"])])
    stale_episode = episode_from_event(an_event())

    link = await aresolve_episode_link(ACME, stale_episode, graph)

    assert link == resolve_episode_link(ACME, stale_episode, graph)


def test_a_graph_side_invalidation_is_reported_as_drift():
    """AC: consistency maintained on updates, for the invalidation path -- not only content
    re-upsert. ``t_invalid`` is populated on ``EpisodeLink`` (from ``bounds_for_ids``) precisely so
    this is checkable; before this test it was populated but never compared."""
    graph = FakeGraph()
    graph.upsert_episodes(ACME, [an_event()])
    episode = episode_from_event(an_event())  # t_invalid=None

    graph.invalidate(ACME, episode.id, t_invalid=500)
    link = resolve_episode_link(ACME, episode, graph)

    assert link.t_invalid == 500
    assert "t_invalid" in episode_link_drift(episode, link)


def test_another_tenants_episode_with_the_same_id_does_not_resolve():
    """Isolation for the resolve seam itself, not only for the substrate it calls: a tenant-scoped
    id collision must not read as a match."""
    graph = FakeGraph()
    other_scope = Scope(tenant_id="other-tenant", user_id="ada")
    graph.upsert_episodes(other_scope, [an_event()])

    link = resolve_episode_link(ACME, episode_from_event(an_event()), graph)

    assert link.exists is False


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


def test_a_zero_entity_episode_resolves_with_an_empty_not_a_none_entity_set():
    """The window *did* run and found the row -- `entity_ids=frozenset()` ("checked, names
    nothing") must not collapse into the `None` ("not checked") every other case here guards."""
    event = an_event(entities=[])
    episode = episode_from_event(event)
    graph = FakeGraph()
    graph.upsert_episodes(ACME, [event])

    link = resolve_episode_link(ACME, episode, graph)

    assert link.entity_ids == frozenset()
    assert episode_link_drift(episode, link) == []


def test_resolve_falls_back_to_the_derived_graph_node_id_when_the_row_carries_none():
    """A row predating SM-E1, or built by hand without going through `episode_from_event`, can
    carry `graph_node_id=None` -- the `or graph_node_id_for(...)` fallback must still label the
    link with the address the graph actually uses, not leave it `None`."""
    episode = replace(episode_from_event(an_event()), graph_node_id=None)
    graph = FakeGraph()

    link = resolve_episode_link(ACME, episode, graph)

    assert link.graph_node_id == graph_node_id_for(ACME, episode.id)
