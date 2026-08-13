"""The CoALA tier model and its per-tier policy (SM-C2 #90).

Two kinds of test here, and the split matters. The first kind pins the table against **ADR 0003
section 1's ratified mapping**, so a row cannot drift from the decision that produced it. The
second exercises the import-time validator, because the table is read as settled everywhere else --
by the time a bad row reaches a substrate it is a write in the wrong place, three frames down.

The validator raises a plain ``ValueError`` and ``policy_for_tier`` raises ``MemoryOperationError``,
and the tests assert that difference: a malformed table is a defect in the module, a non-tier
argument is a bad request, and the error types say which.
"""

from dataclasses import replace

import pytest

from egpt.memory.errors import MemoryOperationError
from egpt.memory.tiers import (
    CONSOLIDATION_SOURCE,
    CONSOLIDATION_TARGET,
    DEFAULT_WRITE_TIER,
    PERSISTENT_TIERS,
    TIER_POLICY,
    GraphWriteShape,
    MemoryTier,
    _validate_policy,
    graph_shape_for_tier,
    policy_for_tier,
    routes_to,
    substrates_for_tier,
)
from egpt.memory.types import MemoryEvent, MemoryQuery, Scope

ACME = Scope(tenant_id="acme")


# ---------------------------------------------------------------------------
# The table is ADR 0003 section 1, not a fresh opinion
# ---------------------------------------------------------------------------


#: Verbatim from ADR 0003 section 1's ratified table, including the two qualifications its prose
#: carries: episodic -> graph is intended-not-yet-realised, and procedural is vector-only because
#: SM-R12 shipped `ProceduralRecord` as `Embeddable`.
RATIFIED = {
    MemoryTier.WORKING: frozenset(),
    MemoryTier.EPISODIC: frozenset({"vector", "graph"}),
    MemoryTier.SEMANTIC: frozenset({"vector", "graph"}),
    MemoryTier.PROCEDURAL: frozenset({"vector"}),
}


@pytest.mark.parametrize("tier", list(MemoryTier))
def test_the_substrate_mapping_is_the_one_the_adr_ratified(tier):
    assert substrates_for_tier(tier) == RATIFIED[tier]


def test_the_four_coala_tiers_are_modelled_and_no_more():
    """The taxonomy is arXiv:2309.02427's four. A fifth would need an ADR, not a table edit."""
    assert [t.value for t in MemoryTier] == ["working", "episodic", "semantic", "procedural"]


def test_the_episodic_tier_is_present_and_routed_even_though_no_backend_stores_one_yet():
    """Issue #90's third criterion: the tier exists as a hook, with its routing already decided.

    Its owner is named in the policy rather than in each adapter's error text, so a caller learns
    where the tier went from whichever refusal they hit.
    """
    policy = policy_for_tier(MemoryTier.EPISODIC)

    assert policy.substrates and policy.graph_shape is GraphWriteShape.EPISODE
    assert policy.owner == "SM-E1 (#108)"


def test_working_is_the_only_tier_with_no_persistent_substrate():
    """It is the in-context buffer SM-C6 (#94) owns; everything else has somewhere to land."""
    assert [t.value for t in MemoryTier if not substrates_for_tier(t)] == ["working"]


# ---------------------------------------------------------------------------
# The derived constants, and the literals they replaced
# ---------------------------------------------------------------------------


def test_the_default_read_set_is_every_persistent_tier():
    assert PERSISTENT_TIERS == (MemoryTier.EPISODIC, MemoryTier.SEMANTIC, MemoryTier.PROCEDURAL)
    assert MemoryQuery(text="q").resolved_tiers() == PERSISTENT_TIERS


def test_an_event_naming_no_tier_lands_where_the_table_says():
    """`MemoryEvent.tier` used to hard-code EPISODIC; the table is now the one place it is said."""
    assert MemoryEvent(content="x", scope=ACME).tier is DEFAULT_WRITE_TIER
    assert DEFAULT_WRITE_TIER is MemoryTier.EPISODIC


def test_consolidation_reads_episodic_and_writes_semantic():
    """The pass both adapters run, stated once rather than as a literal in each."""
    assert (CONSOLIDATION_SOURCE, CONSOLIDATION_TARGET) == (MemoryTier.EPISODIC, MemoryTier.SEMANTIC)


@pytest.mark.parametrize(
    "tier,shape",
    [
        (MemoryTier.WORKING, None),
        (MemoryTier.EPISODIC, GraphWriteShape.EPISODE),
        (MemoryTier.SEMANTIC, GraphWriteShape.FACT),
        (MemoryTier.PROCEDURAL, None),
    ],
)
def test_the_graph_write_shape_is_policy_not_an_adapter_branch(tier, shape):
    """A fact carries SPO and provenance an episode does not, so the two are different writes."""
    assert graph_shape_for_tier(tier) is shape


def test_routes_to_asks_the_question_the_read_path_actually_has():
    """ "Does any requested tier reach this leg" -- the check that skips a guaranteed-empty hop."""
    assert routes_to("graph", [MemoryTier.SEMANTIC, MemoryTier.PROCEDURAL])
    assert not routes_to("graph", [MemoryTier.PROCEDURAL])
    assert not routes_to("vector", [MemoryTier.WORKING])
    assert not routes_to("graph", []), "no tiers means no leg, not every leg"


def test_an_unmapped_tier_names_itself_rather_than_raising_a_bare_key_error():
    """A `KeyError` three frames down does not say which table is missing a row."""
    with pytest.raises(MemoryOperationError, match="TIER_POLICY") as exc:
        substrates_for_tier("not a tier")  # type: ignore[arg-type]

    assert exc.value.field_name == "tier"


# ---------------------------------------------------------------------------
# The validator: a bad row is caught at import, not at a write
# ---------------------------------------------------------------------------


def _table(**overrides):
    """A copy of the real table with rows replaced, for feeding the validator a bad one."""
    table = dict(TIER_POLICY)
    for tier, changes in overrides.items():
        table[tier] = replace(table[tier], **changes)
    return table


def test_the_real_table_passes_its_own_validator():
    _validate_policy(dict(TIER_POLICY))


def test_a_malformed_table_is_not_reported_as_a_request_refusal():
    """`MemoryOperationError` **is** a `ValueError`, so every `pytest.raises(ValueError)` below
    would pass against the old type too. This is the assertion that can tell them apart: a bad
    table literal is a defect in this module, not a caller asking for something unserveable."""
    table = dict(TIER_POLICY)
    del table[MemoryTier.PROCEDURAL]

    with pytest.raises(ValueError) as exc:
        _validate_policy(table)

    assert not isinstance(exc.value, MemoryOperationError)


def test_a_tier_with_no_row_is_refused():
    table = dict(TIER_POLICY)
    del table[MemoryTier.PROCEDURAL]

    with pytest.raises(ValueError, match="no row for"):
        _validate_policy(table)


def test_a_row_filed_under_the_wrong_tier_is_refused():
    """Every lookup is by key, so a mismatched row routes one tier's writes using another's rules."""
    table = dict(TIER_POLICY)
    table[MemoryTier.PROCEDURAL] = TIER_POLICY[MemoryTier.SEMANTIC]

    with pytest.raises(ValueError, match="carries tier"):
        _validate_policy(table)


def test_a_substrate_name_no_port_answers_to_is_refused():
    with pytest.raises(ValueError, match="unknown substrate"):
        _validate_policy(_table(**{MemoryTier.PROCEDURAL: {"substrates": frozenset({"redis"})}}))


def test_a_graph_routed_tier_without_a_write_shape_is_refused():
    """`_graph_write` dispatches on the shape, so a missing one writes nothing and reports success."""
    with pytest.raises(ValueError, match="graph shape exactly when"):
        _validate_policy(_table(**{MemoryTier.SEMANTIC: {"graph_shape": None}}))


def test_a_write_shape_on_a_tier_the_graph_does_not_store_is_refused():
    """The other direction of the same rule: a shape nobody will ever apply."""
    with pytest.raises(ValueError, match="graph shape exactly when"):
        _validate_policy(_table(**{MemoryTier.PROCEDURAL: {"graph_shape": GraphWriteShape.FACT}}))


def test_a_tier_that_consolidates_but_persists_nowhere_is_refused():
    with pytest.raises(ValueError, match="persists nowhere"):
        _validate_policy(_table(**{MemoryTier.WORKING: {"consolidates_from": True}}))


@pytest.mark.parametrize(
    "flag,message", [("is_write_default", "default write target"), ("consolidates_into", "target")]
)
def test_two_tiers_claiming_a_role_only_one_can_hold_is_refused(flag, message):
    """`next(...)` picks the first, so a second claimant is silently ignored rather than wrong-and-loud."""
    with pytest.raises(ValueError, match=message):
        _validate_policy(_table(**{MemoryTier.PROCEDURAL: {flag: True}}))


@pytest.mark.parametrize(
    "flag,message", [("is_write_default", "default write target"), ("consolidates_from", "source")]
)
def test_no_tier_claiming_a_role_is_refused(flag, message):
    with pytest.raises(ValueError, match=message):
        _validate_policy(_table(**{DEFAULT_WRITE_TIER: {flag: False}}))


def test_a_second_consolidation_source_is_refused_rather_than_silently_starved():
    """Two sources share one bounded pass, and deciding how is the scheduler's -- SM-L1 (#122).

    Until that decision exists, `consolidate` scans a single tier, so a second source added here
    would simply never be read.
    """
    with pytest.raises(ValueError, match="consolidation source"):
        _validate_policy(_table(**{MemoryTier.PROCEDURAL: {"consolidates_from": True}}))
