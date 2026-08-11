"""Envelope types for the Symbiotic Memory seam (SM-C1, phase 1).

These types are the contract SM-C2/C3/C5 and every substrate backend build against, so the tests
here pin the properties that are expensive to discover later: tenant scoping cannot be skipped,
timestamps are always epoch seconds, and tier resolution happens in exactly one place.
"""

from datetime import datetime, timezone

import pytest

from egpt.memory.tiers import PERSISTENT_TIERS, MemoryTier
from egpt.memory.types import (
    ConsolidationReport,
    MemoryEvent,
    MemoryQuery,
    MemoryRecord,
    MemorySlice,
    Scope,
    Substrate,
    WriteReceipt,
)


def a_scope(**overrides):
    defaults = {"tenant_id": "acme", "user_id": "u1"}
    defaults.update(overrides)
    return Scope(**defaults)


# ---------------------------------------------------------------------------
# Scope: tenancy is not optional
# ---------------------------------------------------------------------------


def test_scope_requires_a_tenant_id():
    """No positional-optional tenant: an unscoped call must not be constructible."""
    with pytest.raises(TypeError):
        Scope()  # type: ignore[call-arg]


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_scope_rejects_a_blank_tenant_id(blank):
    """Blank is not "no tenant", it is *every* tenant -- the widest scope, not the narrowest."""
    with pytest.raises(ValueError, match="tenant_id"):
        Scope(tenant_id=blank)


def test_scope_rejects_a_non_string_tenant_id():
    with pytest.raises(ValueError, match="tenant_id"):
        Scope(tenant_id=123)  # type: ignore[arg-type]


def test_scope_key_is_fixed_arity_so_partial_scopes_cannot_collide():
    """Two scopes differing only in *which* slot is set must not produce the same key."""
    agent_only = Scope(tenant_id="acme", agent_id="a1").key()
    user_only = Scope(tenant_id="acme", user_id="a1").key()

    assert agent_only != user_only
    assert agent_only == "acme/a1/*/*/*"
    assert user_only == "acme/*/a1/*/*"


def test_scope_is_frozen():
    """Frozen so concurrent requests cannot share mutable scope."""
    scope = a_scope()
    with pytest.raises(Exception):
        scope.tenant_id = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Timestamps: epoch seconds everywhere, so SM-C3 maps 1:1 onto SM-R12's spine
# ---------------------------------------------------------------------------


def test_record_normalizes_every_temporal_field_to_epoch_seconds():
    dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
    expected = int(dt.timestamp())

    record = MemoryRecord(
        id="r1",
        tier=MemoryTier.SEMANTIC,
        content="ships on fridays",
        scope=a_scope(),
        t_valid=dt,  # type: ignore[arg-type]
        t_invalid=dt.isoformat(),  # type: ignore[arg-type]
        created_at=float(expected),  # type: ignore[arg-type]
        expired_at=expected,
    )

    for value in (record.t_valid, record.t_invalid, record.created_at, record.expired_at):
        assert isinstance(value, int)
        assert value == expected


def test_record_leaves_unset_timestamps_as_none():
    """``None`` means open-ended, and must not be coerced into an epoch."""
    record = MemoryRecord(id="r1", tier=MemoryTier.EPISODIC, content="hi", scope=a_scope())

    assert record.t_valid is None
    assert record.t_invalid is None
    assert record.expired_at is None


def test_current_belief_tracks_transaction_time_not_valid_time():
    """A row can be current belief *and* describe a fact that has since become false."""
    superseded_fact = MemoryRecord(
        id="r1", tier=MemoryTier.SEMANTIC, content="x", scope=a_scope(), t_invalid=100, expired_at=200
    )
    now_false_but_believed = MemoryRecord(
        id="r2", tier=MemoryTier.SEMANTIC, content="x", scope=a_scope(), t_invalid=100
    )

    assert superseded_fact.is_current_belief() is False
    assert now_false_but_believed.is_current_belief() is True


def test_event_normalizes_but_does_not_stamp_occurred_at():
    """The write path stamps valid time; an event built earlier must not date itself."""
    unstamped = MemoryEvent(content="hi", scope=a_scope())
    assert unstamped.occurred_at is None

    dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
    stamped = MemoryEvent(content="hi", scope=a_scope(), occurred_at=dt)  # type: ignore[arg-type]
    assert stamped.occurred_at == int(dt.timestamp())


def test_query_normalizes_as_of():
    dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert MemoryQuery(text="q", as_of=dt).as_of == int(dt.timestamp())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Tier resolution: one implementation, so both substrate legs filter identically
# ---------------------------------------------------------------------------


def test_unspecified_tiers_resolve_to_the_persistent_tiers_only():
    """WORKING is in-context state the caller already holds -- retrieval must not return it."""
    resolved = MemoryQuery(text="q").resolved_tiers()

    assert resolved == PERSISTENT_TIERS
    assert MemoryTier.WORKING not in resolved


def test_requested_tiers_are_honored_verbatim_including_working():
    """Excluded by default, but reachable when asked for by name."""
    assert MemoryQuery(text="q", tiers=[MemoryTier.WORKING]).resolved_tiers() == (MemoryTier.WORKING,)


def test_event_defaults_to_the_episodic_tier():
    """Raw, non-lossy write-back; distillation into SEMANTIC is consolidation's job."""
    assert MemoryEvent(content="hi", scope=a_scope()).tier is MemoryTier.EPISODIC


# ---------------------------------------------------------------------------
# Slice rendering
# ---------------------------------------------------------------------------


def test_empty_slice_renders_as_empty_string_not_a_stray_header():
    assert MemorySlice(scope=a_scope()).as_context() == ""


def test_slice_renders_one_tagged_line_per_record():
    scope = a_scope()
    slice_ = MemorySlice(
        scope=scope,
        records=[
            MemoryRecord(id="1", tier=MemoryTier.SEMANTIC, content="prefers dark mode", scope=scope),
            MemoryRecord(id="2", tier=MemoryTier.EPISODIC, content="asked about billing", scope=scope),
        ],
    )

    assert slice_.as_context() == "[semantic] prefers dark mode\n[episodic] asked about billing"


# ---------------------------------------------------------------------------
# Enums serialize as their values (config values validate by construction)
# ---------------------------------------------------------------------------


def test_enums_are_string_valued():
    assert MemoryTier("episodic") is MemoryTier.EPISODIC
    assert Substrate("graph") is Substrate.GRAPH
    assert MemoryTier.SEMANTIC.value == "semantic"


def test_receipt_and_report_default_to_empty_not_none():
    """Callers aggregate these; a None default would make every call site defensive."""
    receipt = WriteReceipt()
    report = ConsolidationReport(scope=a_scope())

    assert receipt.accepted == 0
    assert receipt.event_ids == []
    assert receipt.tiers_written == []
    assert report.episodic_scanned == 0
    assert report.contradictions_resolved == 0
    assert report.details == {}
