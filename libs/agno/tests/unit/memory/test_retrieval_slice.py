"""Token-bounded ``MemorySlice`` assembly and context injection (SM-H6 #136).

``bound_by_tokens`` operates on a slice ``read``/``aread`` already ranked (SM-H2 #132 owns the
ranking); these tests build ``MemorySlice`` fixtures directly, already ranked, rather than
exercising a real fan-out -- ``test_retrieval_orchestrator.py``/``test_fuse.py`` already cover how
a ranking is produced. ``assemble_bounded_context`` and the ``MemoryProvider.assemble_context``
wiring are covered against a real ``WorkingMemory``/``CoLocatedMemoryProvider`` pair, matching
``test_working_memory.py``'s own provider-hook tests.
"""

from typing import List

import pytest

from egpt.memory.adapters.co_located import CoLocatedMemoryProvider
from egpt.memory.retrieval.slice import assemble_bounded_context, bound_by_tokens
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import MemoryEvent, MemoryQuery, MemoryRecord, MemorySlice, Scope, render_context_lines
from egpt.memory.working import WorkingMemory
from egpt.utils.tokens import count_text_tokens
from tests.unit.memory._fakes import FakeVector

ACME = Scope(tenant_id="acme", user_id="u1")


def record(id: str, content: str, tier: MemoryTier = MemoryTier.SEMANTIC) -> MemoryRecord:
    return MemoryRecord(id=id, tier=tier, content=content, scope=ACME)


def slice_of(records: List[MemoryRecord], **fusion) -> MemorySlice:
    by_tier = {}
    for r in records:
        by_tier.setdefault(r.tier, []).append(r)
    return MemorySlice(scope=ACME, query=MemoryQuery(text=""), records=records, by_tier=by_tier, fusion=fusion)


def rendered_tokens(record: MemoryRecord, model_id: str = "gpt-4o") -> int:
    """A record's *independent* rendered cost -- deliberately the naive, pre-fix oracle
    (`sum(rendered_tokens(r) for r in records)` is what round-3 review flagged as wrong once more
    than one record joins). Still correct as a budget for a *single* record, since there is no
    join boundary to misprice with only one piece -- used by tests that want exactly one record to
    fit and no more."""
    return count_text_tokens("\n".join(render_context_lines(record.tier.value, record.content)), model_id)


def joined_cost(records: List[MemoryRecord], model_id: str = "gpt-4o", preamble: str = "") -> int:
    """The real cost of rendering `records` (in ranked order) as one joined string, optionally
    preceded by `preamble` -- computed independently of `bound_by_tokens`'s own algorithm, via
    `MemorySlice.as_context` (already-trusted rendering) tokenized whole, so these tests cannot
    share whatever bug the implementation might reintroduce. The one true budget oracle for any
    test that needs more than one record to fit -- `rendered_tokens` summed across records is
    exactly the additive-sum bug PR #147 round-3 review found."""
    text = slice_of(records).as_context()
    if preamble:
        text = "\n".join(part for part in (preamble, text) if part)
    return count_text_tokens(text, model_id)


def buffer(**kwargs) -> WorkingMemory:
    kwargs.setdefault("max_messages", 40)
    return WorkingMemory(scope=ACME, **kwargs)


# ---------------------------------------------------------------------------
# bound_by_tokens
# ---------------------------------------------------------------------------


def test_bound_by_tokens_keeps_the_ranked_prefix_that_fits():
    a, b, c = record("a", "first ranked"), record("b", "second ranked"), record("c", "third ranked")
    original = slice_of([a, b, c])
    budget = joined_cost([a, b])

    bounded = bound_by_tokens(original, max_tokens=budget)

    assert [r.id for r in bounded.records] == ["a", "b"]
    assert bounded.fusion["dropped_by_tokens"] == 1
    assert bounded.fusion["tokens_used"] == budget
    assert bounded.fusion["token_budget"] == budget


def test_bound_by_tokens_prices_the_real_joined_string_not_a_sum_of_independent_costs():
    """PR #147 round-3 review: two blocks tokenized independently and then joined by ``"\\n"``
    can cost strictly more once actually joined -- neither tokenizer (real BPE, or this repo's
    char-count fallback when `tiktoken` is absent) is additive across a boundary neither half's
    independent tokenization ever saw. Asserted behaviourally against `joined_cost` -- an oracle
    built from `MemorySlice.as_context` + one whole-string tokenize call, independent of
    `bound_by_tokens`'s own loop -- rather than by asserting a hardcoded token count, so this
    holds under either tokenizer."""
    a, b = record("a", "first ranked"), record("b", "second ranked")
    naive_sum = rendered_tokens(a) + rendered_tokens(b)
    real_joined = joined_cost([a, b])
    assert real_joined >= naive_sum  # joining a second block never gets cheaper than the parts

    bounded = bound_by_tokens(slice_of([a, b]), max_tokens=naive_sum)

    # Whatever was actually kept must fit its own real joined cost -- never merely "assumed to
    # fit" because independent per-record costs happened to sum to no more than the budget.
    assert joined_cost(bounded.records) <= naive_sum


def test_bound_by_tokens_stops_at_the_first_overflow_rather_than_skipping_ahead():
    """A later, smaller record must not be promoted past an earlier one that does not fit -- that
    would silently rank a worse-ranked record above a better one the caller asked to see first."""
    a = record("a", "a somewhat longer first entry that costs more tokens")
    b = record("b", "tiny")
    original = slice_of([a, b])
    budget = rendered_tokens(a) - 1  # `a` alone does not fit, `b` alone would

    bounded = bound_by_tokens(original, max_tokens=budget)

    assert bounded.records == []
    assert bounded.fusion["dropped_by_tokens"] == 2


def test_bound_by_tokens_zero_budget_returns_an_empty_slice_not_a_refusal():
    original = slice_of([record("a", "anything")])

    bounded = bound_by_tokens(original, max_tokens=0)

    assert bounded.records == []
    assert bounded.fusion["tokens_used"] == 0


def test_bound_by_tokens_groups_the_kept_records_by_tier():
    """`budget` fits only `a`, so a `by_tier` built from the *original* slice (rather than
    re-derived from `kept`) would still list `b` under `EPISODIC` -- this is what distinguishes
    the fix from a `by_tier = dict(memory_slice.by_tier)` shortcut that looks right when nothing
    was dropped."""
    a = record("a", "a fact", tier=MemoryTier.SEMANTIC)
    b = record("b", "an episode", tier=MemoryTier.EPISODIC)
    original = slice_of([a, b])
    budget = rendered_tokens(a)

    bounded = bound_by_tokens(original, max_tokens=budget)

    assert bounded.by_tier == {MemoryTier.SEMANTIC: [a]}


def test_bound_by_tokens_preserves_the_upstream_fusion_diagnostics():
    """The SM-H2 fusion method that produced the ranking must still be readable after the token
    cut -- a caller inspecting `fusion["method"]` should not see it disappear."""
    original = slice_of([record("a", "x")], method="rrf", fused=5)

    bounded = bound_by_tokens(original, max_tokens=100)

    assert bounded.fusion["method"] == "rrf"
    assert bounded.fusion["fused"] == 5


@pytest.mark.parametrize("bad", [-1, True, 1.5, "10"])
def test_bound_by_tokens_rejects_a_non_nonnegative_int_budget(bad):
    with pytest.raises(ValueError):
        bound_by_tokens(slice_of([]), max_tokens=bad)


def test_bound_by_tokens_does_not_mutate_the_input_slice():
    original = slice_of([record("a", "x"), record("b", "y")])

    bound_by_tokens(original, max_tokens=0)

    assert len(original.records) == 2


# ---------------------------------------------------------------------------
# assemble_bounded_context
# ---------------------------------------------------------------------------


def test_assemble_bounded_context_spends_the_working_half_first_never_truncating_it():
    wm = buffer()
    wm.set_goals(["a goal that must always survive"])
    persistent = slice_of([record("a", "a persistent fact")])
    working_only = wm.as_context()
    budget = count_text_tokens(working_only, "gpt-4o")  # exactly enough for working, nothing left

    rendered = assemble_bounded_context(wm, persistent, max_tokens=budget)

    assert rendered == working_only
    assert "a persistent fact" not in rendered


def test_assemble_bounded_context_gives_the_persistent_half_whatever_budget_remains():
    wm = buffer()
    wm.note("a note")
    persistent = slice_of([record("a", "a persistent fact")])
    working_only = wm.as_context()
    # `joined_cost`, not `count_text_tokens(working_only) + rendered_tokens(...)`: the working/
    # persistent join costs its own token (PR #147 round-3 review), so a budget built from the
    # additive sum can be one token short of what the real render needs to fit both halves.
    full_budget = joined_cost(persistent.records, preamble=working_only)

    rendered = assemble_bounded_context(wm, persistent, max_tokens=full_budget)

    assert "a persistent fact" in rendered
    assert rendered == wm.as_context(persistent)


def test_assemble_bounded_context_prices_the_working_persistent_join_separator():
    """PR #147 round-3 review: the join between the working half and the persistent half costs a
    token neither half's standalone count includes. This picks content where the naive
    ``spent + persistent_cost`` arithmetic (the pre-fix implementation) under-counts the real
    joined cost by exactly that separator's token -- found by brute-force search over short goal/
    content lengths against this environment's own tokenizer, rather than a value hardcoded
    against one specific tokenizer. At ``max_tokens=naive_budget``, the record must not be
    admitted if doing so would make the *real* rendered string cost more than the caller asked
    for -- the pre-fix arithmetic admits it anyway (`remaining` computed from the standalone
    working cost leaves exactly the record's own standalone cost, which fits it by that same
    flawed measure), silently handing back a string over budget."""
    wm = buffer()
    for goal_len, content_len in [(1, 4), (1, 8), (1, 12), (2, 3), (2, 4)]:
        wm.set_goals(["g" * goal_len])
        persistent = slice_of([record("a", "c" * content_len)])
        working_only = wm.as_context()
        naive_budget = count_text_tokens(working_only, "gpt-4o") + rendered_tokens(persistent.records[0])
        real_budget = joined_cost(persistent.records, preamble=working_only)
        if real_budget > naive_budget:
            break
    else:
        raise AssertionError("no (goal_len, content_len) pair produced a join-boundary gap to test against")

    rendered = assemble_bounded_context(wm, persistent, max_tokens=naive_budget)

    assert count_text_tokens(rendered, "gpt-4o") <= naive_budget


def test_assemble_bounded_context_rejects_a_bad_budget():
    with pytest.raises(ValueError):
        assemble_bounded_context(buffer(), slice_of([]), max_tokens=-1)


# ---------------------------------------------------------------------------
# MemoryProvider.assemble_context(max_tokens=...) -- the public wiring
# ---------------------------------------------------------------------------


def test_provider_assemble_context_max_tokens_none_keeps_the_pre_sm_h6_unbounded_default():
    provider = CoLocatedMemoryProvider(vector=FakeVector())
    provider.write(ACME, [MemoryEvent(content="a stored episode", scope=ACME)])
    wm = buffer()

    rendered = provider.assemble_context(ACME, wm, MemoryQuery(text="a stored episode"))

    assert "a stored episode" in rendered


def test_provider_assemble_context_max_tokens_bounds_the_persistent_half():
    provider = CoLocatedMemoryProvider(vector=FakeVector())
    provider.write(ACME, [MemoryEvent(content="a stored episode about pricing", scope=ACME)])
    wm = buffer()
    wm.set_goals(["keep this goal"])
    query = MemoryQuery(text="a stored episode about pricing")
    working_only = wm.as_context()
    budget = count_text_tokens(working_only, "gpt-4o")  # working fits, nothing left for persistent

    rendered = provider.assemble_context(ACME, wm, query, max_tokens=budget)

    assert rendered == working_only
    assert "pricing" not in rendered


@pytest.mark.asyncio
async def test_provider_aassemble_context_max_tokens_mirrors_the_sync_wiring():
    provider = CoLocatedMemoryProvider(vector=FakeVector())
    await provider.awrite(ACME, [MemoryEvent(content="an async stored episode", scope=ACME)])
    wm = buffer()
    query = MemoryQuery(text="an async stored episode")
    working_only = wm.as_context()
    budget = count_text_tokens(working_only, "gpt-4o")

    rendered = await provider.aassemble_context(ACME, wm, query, max_tokens=budget)

    assert rendered == working_only
    assert "async stored episode" not in rendered


def test_provider_assemble_context_rejects_a_bad_max_tokens():
    provider = CoLocatedMemoryProvider(vector=FakeVector())
    with pytest.raises(ValueError):
        provider.assemble_context(ACME, buffer(), max_tokens=-1)
