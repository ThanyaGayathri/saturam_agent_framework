"""Consolidation quality, measured against the ratified CI gate (SM-L7 #128, ADR 0003 section A.7).

AC#4 is "consolidation quality measured on the eval harness". ``benchmarks/memory_consolidation``
measures five *prototype* strategies against a labelled synthetic corpus with a replay-cached LLM --
none of them is the code this repo ships (G6/G7 in the SM-L7 gap analysis). Building a full adapter
that drives an LLM (real or replay) through that harness is a separate, sizable integration this
ticket does not take on; what this file does instead is measure the ratified gate's referential and
structural criteria -- ``provenance_validity``, supersede-not-overwrite, recall of the correct final
state -- against the **real** :class:`~egpt.memory.consolidation.extract.FactExtractionPipeline`,
run twice in sequence the way a production consolidation pass would (write, then a later pass reads
what the first one wrote), using scripted (not random) merge decisions -- the same "doubles, never a
real model" convention :mod:`tests.unit.memory.test_consolidation_extract` uses, since what a
labelled *fact-recall* or *fact-precision* score needs is an LLM in the loop, which is exactly the
piece the standalone harness already exists to evaluate.

Scenario, small and hand-labelled: two episodes report ada's city changing (berlin -> lisbon) --
the same "changed preference is an UPDATE, not a second contradictory ADD" case RESEARCH.md section
9 grades ``conflict_accuracy`` on. Gold: one active fact ("ada lives in lisbon"), one retired
predecessor, both citing a resolvable episode.
"""

from collections import Counter
from typing import List, Optional

import pytest

from egpt.memory.consolidation.extract import FactCandidate, FactExtractionPipeline, MergeDecision
from egpt.memory.consolidation.provenance import provenance_report
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import MemoryRecord, Scope
from tests.unit.memory._fakes import FakeVector

ACME = Scope(tenant_id="acme", user_id="ada")


def episode(content: str, episode_id: str, **kwargs) -> MemoryRecord:
    return MemoryRecord(id=episode_id, tier=MemoryTier.EPISODIC, content=content, scope=ACME, **kwargs)


# Kept local rather than moved to `_fakes.py` (round-3 review raised the question): `_fakes.py`'s
# doubles are the substrate ports (`VectorSubstrate`/`GraphSubstrate`) every consolidation test
# shares, while these two are `FactExtractor`/`MergeDecider` doubles specific to one scripted
# scenario -- the same shape `StubExtractor`/`StubDecider` in `test_consolidation_extract.py`
# already take, kept local to that file for an identical reason. If a second file needs a scripted
# (not stubbed-per-candidate) double like this one, that repetition is the signal to promote it;
# one file using it once is not.
class ScriptedExtractor:
    """One fixed candidate per call, keyed by which episode batch this is -- a stand-in for an
    LLM's extraction, scripted rather than random so the run (and this test) is deterministic."""

    def __init__(self, statement: str) -> None:
        self.statement = statement

    def extract(self, episodes: List[MemoryRecord]) -> List[FactCandidate]:
        return [FactCandidate(statement=self.statement, source_indices=list(range(len(episodes))))]

    async def aextract(self, episodes: List[MemoryRecord]) -> List[FactCandidate]:
        return self.extract(episodes)


class ScriptedDecider:
    """A scripted (not model-driven) merge decision -- `decide`/`adecide` deliberately ignore
    `statement`/`neighbors` and return the one op/target/statement given at construction, so a
    future reader should not "fix" the unused-argument shape into reading them; that would break
    the determinism this double exists to provide."""

    def __init__(self, op: str, statement: str, target_index: Optional[int] = None) -> None:
        self.op = op
        self.statement = statement
        self.target_index = target_index

    def decide(self, statement: str, neighbors: list) -> MergeDecision:
        return MergeDecision(op=self.op, target_index=self.target_index, statement=self.statement)

    async def adecide(self, statement: str, neighbors: list) -> MergeDecision:
        return self.decide(statement, neighbors)


def test_promotion_meets_the_ratified_quality_gate_across_two_consolidation_passes():
    vector = FakeVector()

    # Pass 1: episode e1 promotes a brand-new fact. No existing facts to compare against, so ADD.
    pipe1 = FactExtractionPipeline(
        vector=vector,
        extractor=ScriptedExtractor("ada lives in berlin"),
        decider=ScriptedDecider(op="ADD", statement="ada lives in berlin"),
    )
    # `created_at` set explicitly on both episodes (round-3 review) -- `t_valid` below is derived
    # from it (`extract.py`'s `ts = min(episode.created_at ...)`), and pinning it directly here,
    # rather than relying on `MemoryRecord`'s own default-to-now fallback, is what makes the
    # retirement invariant below testable independent of that default's continued existence.
    facts1, report1 = pipe1.distil(ACME, [episode("ada moved to berlin", "e1", created_at=1000)])
    assert report1.added == 1
    berlin_fact = facts1[0]
    # Persist what pass 1 wrote -- the caller's job in production (SemanticFactStore.write), not
    # the pipeline's own (it only decides, per extract.py's module docstring).
    vector.upsert(ACME, [berlin_fact.record])

    # Pass 2, later: episode e2 reports the same fact changed -- an UPDATE against what pass 1
    # already stored, not a second, contradictory ADD.
    pipe2 = FactExtractionPipeline(
        vector=vector,
        extractor=ScriptedExtractor("ada lives in lisbon"),
        decider=ScriptedDecider(op="UPDATE", target_index=0, statement="ada lives in lisbon"),
    )
    facts2, report2 = pipe2.distil(ACME, [episode("ada moved to lisbon", "e2", created_at=2000)])
    assert report2.updated == 1

    # Both substrates upsert *by id* (``VectorSubstrate.upsert``'s own contract), so the current
    # state after both passes is the by-id merge, later pass winning. Exactly one id collision is
    # expected here -- pass 2's retired copy of the berlin fact reuses pass 1's live one's id, by
    # design (that *is* the update/supersede scenario) -- so a blanket "no collisions" assertion
    # would fail against this test's own scenario. Pin the collision set to exactly that one id
    # instead: an *unexpected* second collision (e.g. the new lisbon fact's id colliding with
    # something) would still be caught, without a false alarm on the expected one.
    all_raw = [*facts1, *facts2]
    ids = [f.record.id for f in all_raw]
    id_counts = Counter(ids)
    duplicate_ids = {i for i, count in id_counts.items() if count > 1}
    assert duplicate_ids == {berlin_fact.record.id}, (
        f"expected only the retired berlin fact to reuse pass 1's id, got duplicates: {duplicate_ids}"
    )
    by_id = {f.record.id: f for f in all_raw}
    all_facts = list(by_id.values())
    active = [f for f in all_facts if f.record.expired_at is None]
    retired = [f for f in all_facts if f.record.expired_at is not None]

    # -- fact_recall: exactly the correct final state is live. The ratified gate's `>= 0.95`
    # threshold is a labelled, corpus-scale metric (benchmarks/memory_consolidation); this 1/1
    # sample cannot exercise that threshold meaningfully -- it pins exact equality on the pipeline's
    # own decision, not a recall score at scale. --
    assert [f.record.content for f in active] == ["ada lives in lisbon"]

    # -- dedup: one active fact about ada's city, not two --
    assert len(active) == 1

    # -- conflict handling / anti-lossiness: supersede, never overwrite (RESEARCH.md section 8 --
    # the old row survives, retired by transaction time; valid time is untouched) --
    assert len(retired) == 1
    assert retired[0].record.content == "ada lives in berlin"
    assert retired[0].record.superseded_by == active[0].record.id
    # Pinned to the concrete value derived from `created_at=1000` above, not just "not None"
    # (round-3 review) -- independent of `MemoryRecord`'s own default-to-now fallback continuing
    # to exist.
    assert berlin_fact.record.t_valid == 1000
    assert retired[0].record.t_valid == berlin_fact.record.t_valid, "valid time is untouched by retirement"

    # -- provenance_validity: every citation, live and retired alike, resolves (ratified == 1.0).
    # `report.checked` is asserted too (round-2 review) -- without it, a regression that
    # short-circuited the batch to empty before reaching provenance_report would still pass both
    # `is_valid`/`validity` vacuously, since an empty batch is defined as valid by design. --
    report = provenance_report(all_facts, resolved_episode_ids={"e1", "e2"})
    assert report.checked == len(all_facts)
    assert report.dangling == ()
    assert report.is_valid is True
    assert report.validity == 1.0

    # -- provenance is unioned forward across the update, not dropped (AC#2). Asserted on
    # `FactWrite.source_episode_ids`, the canonical field `provenance_report` itself reads --
    # `record.provenance["source_episode_ids"]` is a projection of it and would raise `KeyError`
    # rather than fail cleanly if that projection's key ever changed. --
    assert set(active[0].source_episode_ids) == {"e1", "e2"}


def test_a_dangling_citation_would_fail_the_gate():
    """The negative case: if a fact cited an episode that was never written (or was hard-deleted,
    which this codebase's own policy forbids -- ``episodic.forget`` archives, never deletes), the
    ratified gate catches it. Pinned directly so a change to ``provenance_report`` that made this
    silently pass would be caught here, not only in ``test_consolidation_provenance.py``."""
    vector = FakeVector()
    pipe = FactExtractionPipeline(
        vector=vector,
        extractor=ScriptedExtractor("ada lives in berlin"),
        decider=ScriptedDecider(op="ADD", statement="ada lives in berlin"),
    )
    facts, _ = pipe.distil(ACME, [episode("ada moved to berlin", "e1")])

    # A resolved-episode-id set that is missing "e1" simulates a bad rebuild/backfill: the citation
    # is real, but the episode it points at is not in the set the caller resolved.
    report = provenance_report(facts, resolved_episode_ids=set())

    assert report.is_valid is False
    assert report.validity == 0.0


# ---------------------------------------------------------------------------
# AC#4 -- the four remaining ratified metrics (ADR 0003 section A.7:
# provenance_precision >= 0.80, conflict_accuracy >= 0.90, dedup_score >= 0.75,
# fact_recall >= 0.95) need a labelled corpus and gold target facts, which this file's small
# hand-labelled scenario is not built to provide at a meaningful scale -- that harness adapter is
# tracked separately (aicoe-tracker#194), not decided here.
#
# `xfail(strict=False)`, not `skip` (round-3 review): `skip` is invisible in most CI dashboards
# unless actively searched, while `xfail` surfaces as a distinct, counted status now and flips to
# `xpass` -- a visible signal, not a silent green -- the day #194 ships a real body and someone
# forgets to remove the marker. `strict=False` keeps that `xpass` from failing the build outright;
# the marker's own absence-of-removal is a nit to catch in review at that point, not a reason to
# turn a currently-honest gap into a red CI run today. Each stub's body raises for real (`pytest.
# fail`) rather than being empty, so it genuinely `xfail`s now instead of trivially `xpass`ing
# before any implementation exists.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(reason="needs a labelled-corpus harness adapter -- aicoe-tracker#194", strict=False)
def test_provenance_precision_meets_the_ratified_threshold():
    """ADR 0003 section A.7: provenance_precision >= 0.80 against gold source_episode_ids."""
    pytest.fail("not implemented -- aicoe-tracker#194")


@pytest.mark.xfail(reason="needs a labelled-corpus harness adapter -- aicoe-tracker#194", strict=False)
def test_conflict_accuracy_meets_the_ratified_threshold():
    """ADR 0003 section A.7: conflict_accuracy >= 0.90 via the assertion judge (recency-aware)."""
    pytest.fail("not implemented -- aicoe-tracker#194")


@pytest.mark.xfail(reason="needs a labelled-corpus harness adapter -- aicoe-tracker#194", strict=False)
def test_dedup_score_meets_the_ratified_threshold():
    """ADR 0003 section A.7: dedup_score >= 0.75 against gold near-duplicate groupings."""
    pytest.fail("not implemented -- aicoe-tracker#194")


@pytest.mark.xfail(reason="needs a labelled-corpus harness adapter -- aicoe-tracker#194", strict=False)
def test_fact_recall_meets_the_ratified_threshold():
    """ADR 0003 section A.7: fact_recall >= 0.95 against gold target facts."""
    pytest.fail("not implemented -- aicoe-tracker#194")


# ============================================================================
# The production call site (aicoe-tracker#194 item 1)
# ============================================================================


def _provider_with_flag(flag):
    from egpt.memory.adapters.co_located import CoLocatedMemoryProvider
    from tests.unit.memory._fakes import FakeGraph, FakeVector

    return CoLocatedMemoryProvider(vector=FakeVector(), graph=FakeGraph(), check_provenance_on_consolidate=flag)


def _dangling_fact(scope):
    from egpt.memory.tiers import MemoryTier
    from egpt.memory.types import FactWrite, MemoryRecord

    return FactWrite(
        record=MemoryRecord(id="f1", tier=MemoryTier.SEMANTIC, content="x", scope=scope),
        derived_by="test",
        source_episode_ids=["no-such-episode"],
    )


def test_provenance_check_is_off_by_default(caplog):
    """SM-L7 shipped `provenance_report` with no caller; #194 item 1 wires one. It must stay opt-in:
    the check needs the scope's *full* resolved episode set, which is an unbounded scan per pass on
    top of the bounded one consolidation already does."""
    # Constructed WITHOUT the kwarg -- passing `False` explicitly would pass even if the default
    # flipped to True, which is exactly the regression this test exists to catch.
    from egpt.memory.adapters.co_located import CoLocatedMemoryProvider
    from egpt.memory.types import Scope
    from tests.unit.memory._fakes import FakeGraph, FakeVector

    provider = CoLocatedMemoryProvider(vector=FakeVector(), graph=FakeGraph())
    assert provider.check_provenance_on_consolidate is False
    scope = Scope(tenant_id="acme", user_id="u1")
    with caplog.at_level("WARNING"):
        provider._check_provenance(scope, [_dangling_fact(scope)])
    assert "does not resolve" not in caplog.text


def test_provenance_check_warns_on_a_dangling_citation_when_enabled(caplog):
    """The ratified `provenance_validity == 1.0` gate, at a production call site."""
    from egpt.memory.types import Scope

    provider = _provider_with_flag(True)
    scope = Scope(tenant_id="acme", user_id="u1")
    with caplog.at_level("WARNING"):
        provider._check_provenance(scope, [_dangling_fact(scope)])
    assert "does not resolve" in caplog.text
    assert "f1" in caplog.text


def test_provenance_check_is_silent_when_every_citation_resolves(caplog):
    """A clean pass must not log -- an always-warning gate is one an operator learns to ignore."""
    from egpt.memory.tiers import MemoryTier
    from egpt.memory.types import FactWrite, MemoryRecord, Scope

    provider = _provider_with_flag(True)
    scope = Scope(tenant_id="acme", user_id="u1")
    provider.vector.upsert(scope, [MemoryRecord(id="e1", tier=MemoryTier.EPISODIC, content="ep", scope=scope)])
    fact = FactWrite(
        record=MemoryRecord(id="f1", tier=MemoryTier.SEMANTIC, content="x", scope=scope),
        derived_by="test",
        source_episode_ids=["e1"],
    )
    with caplog.at_level("WARNING"):
        provider._check_provenance(scope, [fact])
    assert "does not resolve" not in caplog.text


def test_provenance_check_never_raises_when_episode_resolution_fails(caplog):
    """It is a measurement bolted onto a pass that has already committed facts: it must not be able
    to turn a successful consolidation into a failed one."""
    from egpt.memory.types import Scope

    provider = _provider_with_flag(True)
    scope = Scope(tenant_id="acme", user_id="u1")

    def boom(*args, **kwargs):
        raise RuntimeError("substrate down")

    provider.vector.scan = boom
    with caplog.at_level("WARNING"):
        provider._check_provenance(scope, [_dangling_fact(scope)])  # must not raise
    assert "Provenance check skipped" in caplog.text


# ---------------------------------------------------------------------------
# The production call sites, driven for real (round-2 review, PR #178)
#
# The four tests above call `_check_provenance` directly -- they pin the helper thoroughly but not
# a single production call site, so a mutation that deleted both `consolidate`/`aconsolidate`'s
# calls to it left every one of them green. These two go through `provider.consolidate(scope)` /
# `await provider.aconsolidate(scope)` for real, with `max_episodes_per_pass=1` forcing two passes
# via the resumption cursor -- the same shape as
# `test_promotion_meets_the_ratified_quality_gate_across_two_consolidation_passes` above, except
# through the provider rather than the pipeline directly. This also closes the untested
# full-vs-batch scan bound: union-forward provenance means pass 2's fact legitimately cites pass
# 1's episode, which only an unbounded scan (not the per-pass `limit=max_episodes_per_pass` bound)
# resolves without a false "dangling" warning.
# ---------------------------------------------------------------------------


def _provider_with_pipeline(vector, graph, extractor, decider):
    from egpt.memory.adapters.co_located import CoLocatedMemoryProvider

    pipeline = FactExtractionPipeline(vector=vector, extractor=extractor, decider=decider)
    return CoLocatedMemoryProvider(
        vector=vector,
        graph=graph,
        check_provenance_on_consolidate=True,
        fact_pipeline=pipeline,
        max_episodes_per_pass=1,
    )


def test_consolidate_runs_the_real_provenance_check_across_two_passes(caplog):
    """Deleting `consolidate`'s `self._check_provenance(scope, facts)` call must fail this test --
    unlike the four helper-level tests above, this one cannot pass without the wiring."""
    from tests.unit.memory._fakes import FakeGraph, FakeVector

    vector = FakeVector()
    graph = FakeGraph()
    provider = _provider_with_pipeline(
        vector,
        graph,
        extractor=ScriptedExtractor("ada lives in berlin"),
        decider=ScriptedDecider(op="ADD", statement="ada lives in berlin"),
    )
    vector.upsert(ACME, [episode("ada moved to berlin", "e1", created_at=1000)])

    with caplog.at_level("WARNING"):
        provider.consolidate(ACME)
    assert "does not resolve" not in caplog.text

    # Pass 2: a second episode, and a scripted UPDATE against pass 1's fact -- the pipeline unions
    # `source_episode_ids` forward (extract.py), so pass 2's fact legitimately cites "e1" too, an
    # episode outside this pass's own `max_episodes_per_pass=1` batch.
    provider.fact_pipeline.decider = ScriptedDecider(op="UPDATE", target_index=0, statement="ada lives in lisbon")
    provider.fact_pipeline.extractor = ScriptedExtractor("ada lives in lisbon")
    vector.upsert(ACME, [episode("ada moved to lisbon", "e2", created_at=2000)])

    caplog.clear()
    with caplog.at_level("WARNING"):
        provider.consolidate(ACME)

    # The check resolved the FULL episode set, not the pass-2 batch alone -- the untested
    # correctness property Critical-2 flagged: `limit=self.max_episodes_per_pass` at this call
    # site would report the "e1" citation as dangling, since pass 2's own scan only covers "e2".
    assert vector.scan_limits[-1] is None, "the provenance check must resolve against an unbounded scan"
    assert "does not resolve" not in caplog.text


async def test_aconsolidate_runs_the_real_provenance_check_across_two_passes(caplog):
    """Async twin -- pins `aconsolidate`'s call to `_acheck_provenance` (not the sync helper) the
    same way the sync test above pins `consolidate`'s call to `_check_provenance`."""
    from tests.unit.memory._fakes import FakeGraph, FakeVector

    vector = FakeVector()
    graph = FakeGraph()
    provider = _provider_with_pipeline(
        vector,
        graph,
        extractor=ScriptedExtractor("ada lives in berlin"),
        decider=ScriptedDecider(op="ADD", statement="ada lives in berlin"),
    )
    await vector.aupsert(ACME, [episode("ada moved to berlin", "e1", created_at=1000)])

    with caplog.at_level("WARNING"):
        await provider.aconsolidate(ACME)
    assert "does not resolve" not in caplog.text

    provider.fact_pipeline.decider = ScriptedDecider(op="UPDATE", target_index=0, statement="ada lives in lisbon")
    provider.fact_pipeline.extractor = ScriptedExtractor("ada lives in lisbon")
    await vector.aupsert(ACME, [episode("ada moved to lisbon", "e2", created_at=2000)])

    caplog.clear()
    with caplog.at_level("WARNING"):
        await provider.aconsolidate(ACME)

    assert vector.scan_limits[-1] is None, "the async provenance check must resolve against an unbounded ascan"
    assert "does not resolve" not in caplog.text
