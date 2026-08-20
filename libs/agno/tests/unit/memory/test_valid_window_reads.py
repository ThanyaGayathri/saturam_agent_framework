"""A windowed read, end to end through the co-located provider (SM-V4 #98).

The acceptance criteria are behavioural, so they are asserted behaviourally: the window narrows
**in-search** rather than after the page comes back, tenant isolation survives a windowed read, and
the two halves of every verb decide it identically.

Assertions are on what the substrate was **asked for** (``FakeVector.search_windows``) as well as
on what came back. A provider that dropped the window on the way down still returns the right rows
whenever the corpus happens to sit inside it, so a result-only assertion cannot tell in-search
filtering from post-filtering -- which is the entire decision this ticket rests on.
"""

import asyncio

import pytest

from egpt.memory.adapters.co_located import CoLocatedMemoryProvider
from egpt.memory.config import MemoryProviderConfig
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import (
    FUSION_KEYS,
    MemoryQuery,
    MemoryRecord,
    Scope,
    ValidWindow,
)
from tests.unit.memory._fakes import FakeGraph, FakeVector

ACME = Scope(tenant_id="acme", user_id="u1")
OTHER = Scope(tenant_id="globex", user_id="u1")


def provider(**config_kwargs):
    vector, graph = FakeVector(), FakeGraph()
    config = MemoryProviderConfig(provider="co_located", **config_kwargs)
    return CoLocatedMemoryProvider(vector=vector, graph=graph, config=config), vector, graph


def record(rid, t_valid, t_invalid=None, scope=ACME, content="alpha beta"):
    return MemoryRecord(
        id=rid,
        tier=MemoryTier.SEMANTIC,
        content=content,
        scope=scope,
        t_valid=t_valid,
        t_invalid=t_invalid,
        created_at=1_000,
    )


def seeded(vector, *records, scope=ACME):
    vector.upsert(scope, records)
    return vector


# -- AC 1: the window is applied in-search -----------------------------------


def test_the_window_reaches_the_substrate_rather_than_being_applied_afterwards():
    """AC 1. The provider must hand the window down, not filter the page it gets back.

    Asserted on the *request*: the returned records would be identical either way here, which is
    precisely why a result-only assertion cannot distinguish the two.
    """
    prov, vector, _ = provider()
    seeded(vector, record("a", 100), record("b", 500))
    prov.read(ACME, MemoryQuery(text="alpha beta", valid_from=400, valid_to=600))
    assert vector.search_windows == [ValidWindow(valid_from=400, valid_to=600)]


def test_a_read_with_no_window_asks_for_no_window():
    """The absence of a filter is a request too: a window synthesised from ``now`` would narrow
    every unwindowed read to the present and hide the past."""
    prov, vector, _ = provider()
    seeded(vector, record("a", 100))
    prov.read(ACME, MemoryQuery(text="alpha beta"))
    assert vector.search_windows == [None]


def test_the_window_does_not_spend_the_limit_on_out_of_window_rows():
    """AC 1, and SM-V4 decision D1: post-filtering returns a short page, not a filtered one.

    Five out-of-window rows rank above the one in-window row. With ``limit=1`` a post-filtering
    provider returns **nothing** -- the limit is spent before the filter runs. Filtering in-search
    returns the one row that was asked for.
    """
    prov, vector, _ = provider()
    seeded(
        vector,
        # Higher word overlap, so these rank first -- and all fall outside the window.
        *[record(f"old{n}", 100, 200, content="alpha beta gamma") for n in range(5)],
        record("wanted", 500, content="alpha beta gamma"),
    )
    result = prov.read(ACME, MemoryQuery(text="alpha beta gamma", valid_from=400, valid_to=600, limit=1))
    assert [r.id for r in result.records] == ["wanted"]


def test_an_out_of_window_record_is_excluded_from_the_answer():
    """The plain precision property, so a passing in-search assertion cannot be vacuous."""
    prov, vector, _ = provider()
    seeded(vector, record("inside", 500), record("outside", 100, 200))
    result = prov.read(ACME, MemoryQuery(text="alpha beta", valid_from=400, valid_to=600))
    assert [r.id for r in result.records] == ["inside"]


@pytest.mark.parametrize(
    ("t_valid", "t_invalid", "included"),
    [
        (None, None, True),  # unstamped bounds are open, so the record is a candidate
        (400, None, True),  # starts exactly at the inclusive lower bound
        (599, None, True),  # starts inside, just under the exclusive upper bound
        (600, None, False),  # starts exactly at the exclusive upper bound
        (100, 400, False),  # ends exactly at the inclusive lower bound
        (100, 401, True),  # ends just inside
    ],
)
def test_the_window_boundaries_are_half_open_end_to_end(t_valid, t_invalid, included):
    """The boundary cases, through the provider rather than against the predicate directly -- the
    unit-level agreement is ``test_valid_window.py``; this is that the wiring preserves it."""
    prov, vector, _ = provider()
    seeded(vector, record("r", t_valid, t_invalid))
    result = prov.read(ACME, MemoryQuery(text="alpha beta", valid_from=400, valid_to=600))
    assert bool(result.records) is included


# -- AC 2: no cross-tenant leakage -------------------------------------------


def test_a_windowed_read_does_not_cross_tenants():
    """AC 2. The window is a second predicate, not a replacement for the scope one -- a filter that
    widened the scope while narrowing the time would be a leak that looks like a feature."""
    prov, vector, _ = provider()
    seeded(vector, record("theirs", 500, scope=OTHER), scope=OTHER)
    seeded(vector, record("ours", 500))
    result = prov.read(ACME, MemoryQuery(text="alpha beta", valid_from=400, valid_to=600))
    assert [r.id for r in result.records] == ["ours"]


def test_a_windowed_read_in_an_empty_tenant_returns_nothing_rather_than_the_other_tenants_rows():
    """The single-tenant version of the leak test passes on a broken predicate, so the tenant with
    no matching rows is the one that has to be asserted."""
    prov, vector, _ = provider()
    seeded(vector, record("theirs", 500, scope=OTHER), scope=OTHER)
    result = prov.read(ACME, MemoryQuery(text="alpha beta", valid_from=400, valid_to=600))
    assert result.records == []


def test_the_window_composes_with_the_narrower_scope_slots():
    """Every set slot still applies. A window is orthogonal to scope, so narrowing one must not
    widen the other."""
    prov, vector, _ = provider()
    theirs = Scope(tenant_id="acme", user_id="u2")
    seeded(vector, record("u2", 500, scope=theirs), scope=theirs)
    seeded(vector, record("u1", 500))
    result = prov.read(ACME, MemoryQuery(text="alpha beta", valid_from=400, valid_to=600))
    assert [r.id for r in result.records] == ["u1"]


# -- diagnostics --------------------------------------------------------------


def test_the_slice_reports_how_many_the_window_dropped():
    """Separate from ``dropped_by_validity``: that one says the world moved on, this one says the
    caller asked a narrower question, and they are different diagnoses.

    Non-zero here means reconciliation tightened a bound after the substrate had already filtered,
    so it is the observable for the graph-authority half of the rule.

    Since SM-G9 (#107) both legs narrow in-search, so the only remaining way a row reaches the
    merged set looking in-window and leaves it outside is the graph-authority path this docstring
    already names: the vector leg's own copy of the bounds is stale, and the graph's authoritative
    ``bounds_for_ids`` (ADR 0003 D8) tightens them after the fact. Seeded with the *same* id under
    both substrates -- the vector leg's stale ``t_valid=500, t_invalid=None`` passes the window, and
    the graph's fresher ``[100, 400)`` does not (it ends at the window's inclusive lower bound).
    """
    prov, vector, graph = provider()
    seeded(vector, record("inside", 500))
    graph.rows.setdefault(ACME.tenant_id, []).append(record("inside", 100, 400))

    result = prov.read(ACME, MemoryQuery(text="alpha beta", valid_from=400, valid_to=600))

    assert result.records == []
    assert result.fusion["dropped_outside_window"] == 1
    # Not double-counted: one filter runs, so the point-predicate key stays zero for a windowed
    # read. Reporting the drop under both would tell a caller two rows went when one did.
    assert result.fusion["dropped_by_validity"] == 0


def test_an_unwindowed_read_still_reports_under_the_original_key():
    """``dropped_by_validity`` must keep its meaning for every caller that never sets a window --
    the new key is an attribution, not a rename."""
    prov, vector, _ = provider()
    seeded(vector, record("current", 500), record("expired", 100, 200))
    plain = prov.read(ACME, MemoryQuery(text="alpha beta"))
    assert [r.id for r in plain.records] == ["current"]
    assert plain.fusion["dropped_by_validity"] == 1
    assert plain.fusion["dropped_outside_window"] == 0


def test_a_future_dated_record_does_not_answer_an_unwindowed_read():
    """The point predicate checks **both** bounds. Checking only ``t_invalid`` lets a record whose
    valid time has not started answer about the present -- and that is the half a rewrite onto the
    shared rule is most likely to drop, since only a future-dated record can observe it.
    """
    prov, vector, _ = provider()
    seeded(vector, record("not_yet", 4_000_000_000), record("now", 100))
    result = prov.read(ACME, MemoryQuery(text="alpha beta"))
    assert [r.id for r in result.records] == ["now"]


def test_dropped_outside_window_is_in_the_diagnostics_floor():
    """A key one adapter reports and another omits raises ``KeyError`` on whichever the consumer
    was not written against -- the floor exists so a provider-agnostic reader never has to ask."""
    assert "dropped_outside_window" in FUSION_KEYS
    prov, vector, _ = provider()
    seeded(vector, record("a", 500))
    result = prov.read(ACME, MemoryQuery(text="alpha beta"))
    for key in FUSION_KEYS:
        assert key in result.fusion, key


# -- sync/async parity --------------------------------------------------------


@pytest.mark.parametrize("windowed", [True, False])
def test_both_halves_of_read_decide_the_window_identically(windowed):
    """The async twin must make the same decision, not merely exist. Asserted on the request and
    the result together, since a half that forwarded the window but ignored the answer would pass
    either one alone."""
    bounds = {"valid_from": 400, "valid_to": 600} if windowed else {}
    query = MemoryQuery(text="alpha beta", **bounds)

    sync_prov, sync_vector, _ = provider()
    seeded(sync_vector, record("inside", 500), record("outside", 100, 200))
    sync_result = sync_prov.read(ACME, query)

    async_prov, async_vector, _ = provider()
    seeded(async_vector, record("inside", 500), record("outside", 100, 200))
    async_result = asyncio.run(async_prov.aread(ACME, query))

    assert sync_vector.search_windows == async_vector.search_windows
    assert [r.id for r in sync_result.records] == [r.id for r in async_result.records]
    assert sync_result.fusion["dropped_outside_window"] == async_result.fusion["dropped_outside_window"]


@pytest.mark.parametrize("method", ["read_tier", "aread_tier"])
def test_the_tier_narrowed_read_carries_the_window_too(method):
    """``read_tier`` rebuilds the query with ``replace``; a field added to ``MemoryQuery`` and not
    carried through would drop the filter on exactly one of the two read verbs."""
    prov, vector, _ = provider()
    seeded(vector, record("inside", 500), record("outside", 100, 200))
    query = MemoryQuery(text="alpha beta", valid_from=400, valid_to=600)
    call = getattr(prov, method)(ACME, MemoryTier.SEMANTIC, query)
    records = asyncio.run(call) if method.startswith("a") else call
    assert vector.search_windows == [ValidWindow(valid_from=400, valid_to=600)]
    assert [r.id for r in records] == ["inside"]


# -- the graph leg narrows in-search (SM-G9 #107) -----------------------------


def test_the_graph_leg_now_narrows_to_the_valid_time_window():
    """**Supersedes the prior pin.** Owner: SM-G9 (#107).

    ``GraphSubstrate.traverse`` used to receive the window and ignore it, so its contribution to a
    windowed read could come back short -- out-of-window rows consumed its ``limit`` and the
    provider dropped them afterwards. ``test_the_graph_leg_does_not_narrow_to_the_valid_time_window``
    pinned that gap and named exactly this replacement once the predicate landed in the traversal:
    "delete it and assert the graph leg narrows like the vector one."

    Asserted on the *request* (``graph.traverse_windows``), mirroring
    ``test_the_window_reaches_the_substrate_rather_than_being_applied_afterwards`` for the vector
    leg -- a result-only assertion cannot distinguish in-search filtering from a corpus that happens
    to sit inside the window.
    """
    prov, _, graph = provider()
    inside = record("inside", 500, content="alpha beta gamma")
    outside = record("outside", 100, 200, content="alpha beta gamma")
    # `FakeGraph._bucket` keys by tenant, like every real backend partitions by it. Keying by
    # record id instead leaves the graph leg empty and the test asserting nothing -- which is what
    # the first version of this setup did.
    graph.rows.setdefault(ACME.tenant_id, []).extend([inside, outside])

    windowed = MemoryQuery(text="alpha beta gamma", valid_from=400, valid_to=600, limit=5)

    traversed = graph.traverse(ACME, windowed)
    assert {r.id for r in traversed} == {"inside"}
    assert graph.traverse_windows[-1] == ValidWindow(valid_from=400, valid_to=600)

    # The graph leg no longer contributes an out-of-window row for the provider to drop.
    result = prov.read(ACME, windowed)
    assert [r.id for r in result.records] == ["inside"]
    assert result.fusion["dropped_outside_window"] == 0
    assert result.fusion["dropped_by_validity"] == 0
