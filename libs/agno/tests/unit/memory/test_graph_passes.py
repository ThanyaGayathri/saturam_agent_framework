"""``select_current_episodes``, shared by every graph extraction pipeline (SM-G3 #101).

Both ``EntityExtractionPipeline`` and ``RelationExtractionPipeline`` cover this indirectly through
their own test suites, but it is shared code now -- a direct test protects against silent drift if
the helper changes in a way neither pipeline's fixtures happen to exercise.
"""

import pytest

from egpt.memory.graph.passes import select_current_episodes
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import MemoryOperationError, MemoryRecord, Scope

ACME = Scope(tenant_id="acme", user_id="ada")


def episode(episode_id, **kwargs):
    return MemoryRecord(id=episode_id, tier=MemoryTier.EPISODIC, content="x", scope=ACME, **kwargs)


def test_every_current_episode_is_selected_and_the_pass_is_complete():
    chosen, truncated = select_current_episodes(
        [episode("e1"), episode("e2")], max_episodes_per_pass=10, purpose="Entities"
    )

    assert [e.id for e in chosen] == ["e1", "e2"]
    assert truncated is False


def test_a_superseded_episode_is_dropped_without_truncating():
    """Unread, not skipped: dropping a superseded row must not report the pass incomplete, or a
    scheduler re-runs a pass with nothing left to do."""
    chosen, truncated = select_current_episodes(
        [episode("gone", expired_at=100), episode("here")], max_episodes_per_pass=10, purpose="Entities"
    )

    assert [e.id for e in chosen] == ["here"]
    assert truncated is False


def test_the_cap_truncates_and_reports_it():
    chosen, truncated = select_current_episodes(
        [episode("e1"), episode("e2")], max_episodes_per_pass=1, purpose="Entities"
    )

    assert [e.id for e in chosen] == ["e1"]
    assert truncated is True


def test_the_cap_exactly_matching_the_input_does_not_truncate():
    chosen, truncated = select_current_episodes(
        [episode("e1"), episode("e2")], max_episodes_per_pass=2, purpose="Entities"
    )

    assert [e.id for e in chosen] == ["e1", "e2"]
    assert truncated is False


def test_a_non_episodic_tier_is_refused_naming_the_callers_purpose():
    """``purpose`` names which pass rejected the tier -- asserted on the message, since the two
    pipelines that call this share one ``field_name`` and only the text tells them apart."""
    fact = MemoryRecord(id="f1", tier=MemoryTier.SEMANTIC, content="x", scope=ACME)

    with pytest.raises(MemoryOperationError, match="Relations are extracted") as excinfo:
        select_current_episodes([fact], max_episodes_per_pass=10, purpose="Relations")

    assert excinfo.value.field_name == "tier"


def test_an_empty_input_selects_nothing_and_is_not_truncated():
    chosen, truncated = select_current_episodes([], max_episodes_per_pass=10, purpose="Entities")

    assert chosen == []
    assert truncated is False
