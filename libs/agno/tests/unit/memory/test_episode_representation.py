"""The episode representation at the seam (SM-E1 #108).

Every assertion here is on a projection, not on a store: SM-E1 defines what an episode *is* to the
seam, and persisting one is SM-V1's (#95). The pinned-limitation tests at the end fail when a
sibling ticket's behaviour arrives in this module, which is where it would go unnoticed.
"""

import importlib
import importlib.util
from dataclasses import replace

import egpt.memory as memory_package
import pytest
from egpt.db.schemas.memory import EpisodicRecord as PersistedEpisodicRecord
from egpt.memory.schemas.episodic import (
    EPISODE_KEYS,
    PROVENANCE_KEYS,
    TOPICS_KEY,
    EpisodicRecord,
    episode_details,
    episode_from_event,
    episode_to_record,
    graph_node_id_for,
)
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import MemoryEvent, MemoryOperationError, Scope, escape_key_slot
from egpt.utils.dttm import now_epoch_s

SCOPE = Scope(tenant_id="acme", agent_id="assistant", user_id="ada", team_id="core", session_id="s1")


def an_event(**overrides):
    defaults = dict(
        content="shipped v2 to production",
        scope=SCOPE,
        tier=MemoryTier.EPISODIC,
        id="ep-1",
        topics=["deploy"],
        occurred_at=1_700_000_000,
        metadata={"channel": "slack"},
    )
    defaults.update(overrides)
    return MemoryEvent(**defaults)


def a_full_episode() -> EpisodicRecord:
    """An episode with every SM-R12 section 4.3 field set, so a dropped one is visible."""
    episode = episode_from_event(an_event())
    return replace(
        episode,
        location="ci-runner",
        cause="merge to main",
        importance=0.9,
        last_accessed_at=1_700_000_500,
        entity_ids=["entity-1"],
        revision=2,
        supersedes=["ep-0"],
        boundary_method="surprise",
        boundary_trigger=0.42,
        boundary_confidence=0.8,
        refined=True,
        segmenter_version="v1",
    )


# -- the schema is SM-C3's, not a second one ---------------------------------------------------


def test_the_exported_episode_is_the_persisted_row():
    """A fork would give the seam and the database two episode shapes to drift apart."""
    assert EpisodicRecord is PersistedEpisodicRecord


#: Every name this ticket adds to ``egpt.memory.__all__``, with the module that defines it. The
#: whole set rather than the constants alone: an ``is``-identity check is what catches a re-export
#: that starts wrapping rather than forwarding.
EXPORTED_SURFACE = [
    ("EPISODE_KEYS", "egpt.memory.schemas.episodic"),
    ("PROVENANCE_KEYS", "egpt.memory.schemas.episodic"),
    ("TOPICS_KEY", "egpt.memory.schemas.episodic"),
    ("EpisodicRecord", "egpt.memory.schemas.episodic"),
    ("episode_details", "egpt.memory.schemas.episodic"),
    ("episode_from_event", "egpt.memory.schemas.episodic"),
    ("episode_to_record", "egpt.memory.schemas.episodic"),
    ("graph_node_id_for", "egpt.memory.schemas.episodic"),
    # Promoted from `types._escape` by this PR, and half of the pair `graph_node_id_for`
    # exports: a caller extending the slot encoding needs both from the same boundary.
    ("escape_key_slot", "egpt.memory.types"),
]


@pytest.mark.parametrize("name, module", EXPORTED_SURFACE)
def test_the_episode_surface_is_reachable_from_the_package(name, module):
    """Exported symmetrically or not at all: a caller who cannot write
    ``from egpt.memory import TOPICS_KEY`` reaches into a submodule or hard-codes the string, and a
    later rename then misses them."""
    assert name in memory_package.__all__
    assert getattr(memory_package, name) is getattr(importlib.import_module(module), name)


# -- acceptance criteria -----------------------------------------------------------------------


def test_timestamp_context_importance_and_decay_reach_the_reader():
    """AC: timestamp + context + importance + decay fields present -- through the projection, so
    a field that exists on the row but is dropped on the way out still fails."""
    record = episode_to_record(a_full_episode())

    assert record.t_valid == 1_700_000_000  # valid time: when it happened
    assert record.created_at is not None  # transaction time: when it was recorded
    assert record.metadata["occurred_at"] == 1_700_000_000
    assert record.metadata["location"] == "ci-runner"
    assert record.metadata["cause"] == "merge to main"
    assert record.metadata["importance"] == 0.9
    assert record.metadata["last_accessed_at"] == 1_700_000_500


def test_every_episode_key_is_present_even_when_the_episode_carries_nothing():
    """The floor: a consumer that branches on ``importance`` must not raise ``KeyError`` against a
    bare episode, which is the divergence ``FUSION_KEYS`` exists to prevent on the read path."""
    record = episode_to_record(episode_from_event(an_event(topics=None, metadata={})))

    assert set(EPISODE_KEYS) <= set(record.metadata)
    assert set(PROVENANCE_KEYS) <= set(record.provenance)


def test_episode_details_carries_the_floor_and_admits_extras():
    details = episode_details(importance=0.7, deployment="mode_a")

    # Equality, not containment: a key dropped from EPISODE_KEYS also leaves the reserved set, and
    # caller metadata would then quietly shadow the episode's own field.
    assert set(episode_details()) == set(EPISODE_KEYS)
    assert details["importance"] == 0.7
    assert details["deployment"] == "mode_a"


def test_create_then_read_preserves_the_event():
    """AC: create/read verified. Content, identity, scope, topics and caller metadata all survive
    the trip through the stored shape."""
    event = an_event()
    record = episode_to_record(episode_from_event(event))

    assert record.id == event.id
    assert record.content == event.content
    assert record.scope == event.scope
    assert record.tier is MemoryTier.EPISODIC
    assert record.topics == ["deploy"]
    assert record.metadata["channel"] == "slack"


def test_the_episode_links_to_its_graph_node():
    """AC: links to the graph episodic node. Derived from the scope and the id, so the row and the
    node cannot disagree about which node an episode is."""
    episode = episode_from_event(an_event())

    assert episode.graph_node_id == graph_node_id_for(SCOPE, "ep-1")
    # Spelled out rather than deferring to the function under test, which would be tautological:
    # this is the assertion that pins the separator and the sixth-slot encoding.
    assert episode.graph_node_id == SCOPE.key() + "/" + escape_key_slot("ep-1")
    assert episode_to_record(episode).provenance["graph_node_id"] == episode.graph_node_id


def test_the_graph_node_id_separates_slots_that_would_otherwise_collide():
    """Fixed arity is not injectivity: an id or a slot containing the separator must not merge two
    addresses into one, or two episodes resolve to the same node."""
    first = graph_node_id_for(Scope(tenant_id="acme", session_id="s1/ep"), "1")
    second = graph_node_id_for(Scope(tenant_id="acme", session_id="s1"), "ep/1")

    assert first != second
    # And the address stays six slots wide whatever either side contains -- the arity half of the
    # same guarantee, which is what makes the escaping load-bearing rather than decorative.
    assert len(first.split("/")) == 6
    assert len(second.split("/")) == 6


@pytest.mark.skipif(
    importlib.util.find_spec("neo4j") is None,
    reason="the Neo4j package imports its driver at module level",
)
def test_the_graph_merge_key_has_not_changed_shape():
    """The drift guard: SM-G1 merges an episode node on ``{scope_key, id, live: true}``, and
    :func:`graph_node_id_for` addresses the first two. If that key changes shape, the link stops
    resolving and nothing else in the suite would say so.

    It asserts on the *statement*, not on ``graph_node_id`` -- the pointer is a vector-side field
    on ``EpisodicRecord`` and is not a property of the graph node at all (it appears nowhere in
    ``cypher.ROW_PROPERTIES``). What the pointer's own format must be is
    ``test_the_episode_links_to_its_graph_node``, which needs no driver.

    Skipped per test rather than per module: everything else here is pure core and must still run
    on the ``unit-drivers-absent`` lane."""
    from egpt.integrations.memory.graph.neo4j import cypher

    statement, _ = cypher.upsert_episodes_statement(SCOPE)

    # The whole merge key, not a substring of it. The live marker is what confines the MERGE to the
    # currently-believed row, and dropping it would leave the id and scope assertions passing while
    # by-id upsert quietly stopped being idempotent. Built from `cypher.LIVE` rather than the
    # literal, because renaming the marker is fine and removing it is not.
    assert f"MERGE (n:{cypher.EPISODE} {{scope_key: row.scope_key, id: row.id, {cypher.LIVE}: true}})" in statement
    assert "graph_node_id" not in cypher.ROW_PROPERTIES


# -- boundary refusals -------------------------------------------------------------------------


def test_an_event_without_an_id_is_refused():
    """One logical episode has one id across both substrates; minting a second here surfaces the
    episode twice after fusion."""
    with pytest.raises(MemoryOperationError, match="dedup key"):
        episode_from_event(an_event(id=None))


def test_a_whitespace_only_id_is_refused_as_a_missing_id():
    """Not as a missing graph node, which is what the derivation would have called it: the id is
    what is absent, and it is the id the caller has to supply."""
    with pytest.raises(MemoryOperationError, match="dedup key"):
        episode_from_event(an_event(id="   "))


def test_a_non_episodic_event_is_refused():
    with pytest.raises(MemoryOperationError, match="semantic"):
        episode_from_event(an_event(tier=MemoryTier.SEMANTIC))


def test_a_blank_episode_id_has_no_graph_node():
    with pytest.raises(MemoryOperationError, match="episode id is required"):
        graph_node_id_for(SCOPE, "   ")


def test_an_episode_id_that_is_not_a_string_has_no_graph_node():
    """The type guard is live, not dead: without it this is an ``AttributeError`` from inside
    ``strip()`` rather than a named refusal, and this function is exported from ``egpt.memory``
    where nothing enforces the annotation at runtime."""
    with pytest.raises(MemoryOperationError, match="episode id is required"):
        graph_node_id_for(SCOPE, 123)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "field, episode",
    [
        (TOPICS_KEY, replace(episode_from_event(an_event()), metadata={TOPICS_KEY: "deploy"})),
        ("entity_ids", replace(episode_from_event(an_event()), entity_ids="e1")),
        ("supersedes", replace(episode_from_event(an_event()), supersedes="ep-0")),
        ("superseded_by", replace(episode_from_event(an_event()), superseded_by="ep-2")),
    ],
)
def test_a_collection_field_holding_a_string_is_refused(field, episode):
    """Every list this projection copies, not only the one a caller can set through metadata:
    ``list("deploy")`` is six one-character entries and raises nothing, and the row model leaves a
    non-list value alone, so a direct database insert arrives here with a bare string."""
    with pytest.raises(MemoryOperationError, match=field):
        episode_to_record(episode)


@pytest.mark.parametrize("topics", ["deploy", 42])
def test_non_list_topics_are_refused_on_the_way_in(topics):
    """The write path, which the read-path cases above cannot reach: a string copied in becomes a
    list of characters, and the read guard then accepts it as a well-formed list, so the corruption
    survives the round trip undetected. A non-iterable is a bare ``TypeError`` without this."""
    with pytest.raises(MemoryOperationError, match=TOPICS_KEY):
        episode_from_event(an_event(topics=topics))


def test_topics_survive_the_write_path_as_a_copy():
    """The guard must still copy: sharing the caller's list lets a later append reach the stored
    episode through an event the caller still holds."""
    event = an_event(topics=["deploy"])
    episode = episode_from_event(event)

    event.topics.append("rollback")

    assert episode.metadata[TOPICS_KEY] == ["deploy"]


@pytest.mark.parametrize("reserved", ["importance", "occurred_at", "topics"])
def test_caller_metadata_may_not_shadow_an_episode_field_on_write(reserved):
    with pytest.raises(MemoryOperationError, match=reserved):
        episode_from_event(an_event(metadata={reserved: "mine"}))


@pytest.mark.parametrize("reserved", ["importance", "occurred_at"])
def test_caller_metadata_may_not_shadow_an_episode_field_on_read(reserved):
    """The same rule on the way out. A row written outside the seam is the only way to get here,
    and silently overwriting its key would hand the reader the wrong value under the right name."""
    episode = replace(episode_from_event(an_event()), metadata={reserved: "mine"})

    with pytest.raises(MemoryOperationError, match=reserved):
        episode_to_record(episode)


def test_a_graph_node_id_belonging_to_another_episode_is_refused():
    episode = replace(episode_from_event(an_event()), graph_node_id=graph_node_id_for(SCOPE, "ep-2"))

    with pytest.raises(MemoryOperationError, match="graph_node_id"):
        episode_to_record(episode)


def test_an_episode_with_no_graph_link_still_projects():
    """SM-R12 section 9 makes cross-tier references advisory: a deployment with no graph substrate
    has no node, and a reader tolerates the missing target."""
    record = episode_to_record(replace(episode_from_event(an_event()), graph_node_id=None))

    assert record.provenance["graph_node_id"] is None


# -- the supersede fork ------------------------------------------------------------------------


def test_no_replacement_leaves_the_envelope_id_unset():
    """The third case in the table, named rather than left implicit: an episode nothing has
    replaced must not acquire a replacement id from an empty list."""
    record = episode_to_record(episode_from_event(an_event()))

    assert record.superseded_by is None
    assert record.provenance["superseded_by"] == []
    assert record.is_current_belief()


def test_one_replacement_narrows_to_the_envelope_id():
    episode = replace(episode_from_event(an_event()), superseded_by=["ep-2"])

    assert episode_to_record(episode).superseded_by == "ep-2"


def test_a_split_names_no_single_replacement_and_loses_neither():
    """An SM-R6 re-cut can split one episode into several. Naming one of them the replacement
    sends a reader following the chain down one branch of a fork."""
    episode = replace(episode_from_event(an_event()), superseded_by=["ep-2", "ep-3"])
    record = episode_to_record(episode)

    assert record.superseded_by is None
    assert record.provenance["superseded_by"] == ["ep-2", "ep-3"]


def test_the_projection_hands_out_copies():
    """A record built from a row the store is holding must not let a consumer write back through
    the read path -- the reason ``MemoryRecord.detached`` exists."""
    episode = replace(a_full_episode(), superseded_by=["ep-2"])
    record = episode_to_record(episode)

    record.provenance["entity_ids"].append("entity-2")
    record.provenance["supersedes"].append("ep-9")
    record.provenance["superseded_by"].append("ep-9")

    assert episode.entity_ids == ["entity-1"]
    assert episode.supersedes == ["ep-0"]
    assert episode.superseded_by == ["ep-2"]


# -- valid time --------------------------------------------------------------------------------


def test_an_undated_event_is_valid_from_now():
    """The decision ``CoLocatedMemoryProvider._prepare`` already makes, made the same way here: an
    episode with no valid-time lower bound would be returned by a point-in-time read of any past
    instant, including before it happened."""
    before = now_epoch_s()
    episode = episode_from_event(an_event(occurred_at=None))

    assert episode.occurred_at is not None
    assert episode.t_valid == episode.occurred_at
    assert before <= episode.t_valid <= now_epoch_s()


def test_valid_time_is_when_it_happened_not_when_it_was_recorded():
    episode = episode_from_event(an_event(occurred_at=1_600_000_000))

    assert episode.t_valid == 1_600_000_000
    assert episode.created_at is not None and episode.created_at > episode.t_valid


# -- pinned limitations ------------------------------------------------------------------------


def test_importance_is_not_scored_here():
    """Pins SM-E5 (#112). Salience is LLM-assigned on the async lane; a value invented at build
    time would rank episodes against each other on nothing. Delete this test when SM-E5 lands."""
    assert episode_from_event(an_event()).importance == 0.5


def test_role_is_not_preserved_here():
    """Pins SM-E2 (#109). ``MemoryEvent.role`` has no column on ``EpisodicRecord`` and no field on
    ``MemoryRecord``, so carrying it would mean choosing a metadata convention on behalf of the
    capture path -- which is the only caller that has a role to place, and owns how a conversation
    frame is represented. ``adapters/seed.py`` reads ``event.role`` twice, so the value is real and
    the deferral has to be visible. Delete this test when SM-E2 lands."""
    episode = episode_from_event(an_event(role="user"))

    assert "role" not in episode.metadata
    assert "role" not in episode_to_record(episode).metadata


def test_no_boundary_metadata_is_assigned_here():
    """Pins SM-E3 (#110). This module carries SM-R6's segmentation fields; deciding where an
    episode starts and stops is SM-E3's. Delete this test when SM-E3 lands."""
    episode = episode_from_event(an_event())

    assert episode.boundary_method is None
    assert episode.boundary_trigger is None
    assert episode.boundary_confidence is None
    assert episode.segmenter_version is None
    assert episode.revision == 0


def test_the_graph_link_is_derived_and_not_resolved():
    """Pins SM-E10 (#117) and SM-G6 (#104). The link addresses the node SM-G1 would write; nothing
    here writes it, reads it back, or propagates valid time to it. Delete this test when SM-E10 /
    SM-G6 lands."""
    episode = episode_from_event(an_event())

    assert episode.graph_node_id == graph_node_id_for(episode_to_record(episode).scope, episode.id)
    assert episode.entity_ids == []  # linking an episode to the entities it mentions is SM-G6's
