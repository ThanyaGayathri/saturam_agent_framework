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

from egpt.memory.consolidation.extract import FactCandidate, FactExtractionPipeline, MergeDecision
from egpt.memory.consolidation.provenance import provenance_report
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import MemoryRecord, Scope
from tests.unit.memory._fakes import FakeVector

ACME = Scope(tenant_id="acme", user_id="ada")


def episode(content, episode_id, **kwargs):
    return MemoryRecord(id=episode_id, tier=MemoryTier.EPISODIC, content=content, scope=ACME, **kwargs)


class ScriptedExtractor:
    """One fixed candidate per call, keyed by which episode batch this is -- a stand-in for an
    LLM's extraction, scripted rather than random so the run (and this test) is deterministic."""

    def __init__(self, statement):
        self.statement = statement

    def extract(self, episodes):
        return [FactCandidate(statement=self.statement, source_indices=list(range(len(episodes))))]

    async def aextract(self, episodes):
        return self.extract(episodes)


class ScriptedDecider:
    def __init__(self, op, statement, target_index=None):
        self.op = op
        self.statement = statement
        self.target_index = target_index

    def decide(self, statement, neighbors):
        return MergeDecision(op=self.op, target_index=self.target_index, statement=self.statement)

    async def adecide(self, statement, neighbors):
        return self.decide(statement, neighbors)


def test_promotion_meets_the_ratified_quality_gate_across_two_consolidation_passes():
    vector = FakeVector()

    # Pass 1: episode e1 promotes a brand-new fact. No existing facts to compare against, so ADD.
    pipe1 = FactExtractionPipeline(
        vector=vector,
        extractor=ScriptedExtractor("ada lives in berlin"),
        decider=ScriptedDecider(op="ADD", statement="ada lives in berlin"),
    )
    facts1, report1 = pipe1.distil(ACME, [episode("ada moved to berlin", "e1")])
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
    facts2, report2 = pipe2.distil(ACME, [episode("ada moved to lisbon", "e2")])
    assert report2.updated == 1

    # Both substrates upsert *by id* (``VectorSubstrate.upsert``'s own contract), so the current
    # state after both passes is the by-id merge, later pass winning -- exactly what pass 2's
    # retired copy of "f1" does to pass 1's live one, both sharing that id.
    by_id = {f.record.id: f for f in [*facts1, *facts2]}
    all_facts = list(by_id.values())
    active = [f for f in all_facts if f.record.expired_at is None]
    retired = [f for f in all_facts if f.record.expired_at is not None]

    # -- fact_recall: exactly the correct final state is live (ratified >= 0.95; measured 1.0) --
    assert [f.record.content for f in active] == ["ada lives in lisbon"]

    # -- dedup: one active fact about ada's city, not two (ratified dedup_score >= 0.75) --
    assert len(active) == 1

    # -- conflict handling / anti-lossiness: supersede, never overwrite (RESEARCH.md section 8 --
    # the old row survives, retired by transaction time; valid time is untouched) --
    assert len(retired) == 1
    assert retired[0].record.content == "ada lives in berlin"
    assert retired[0].record.superseded_by == active[0].record.id
    assert retired[0].record.t_valid == berlin_fact.record.t_valid, "valid time is untouched by retirement"

    # -- provenance_validity: every citation, live and retired alike, resolves (ratified == 1.0) --
    report = provenance_report(all_facts, live_episode_ids={"e1", "e2"})
    assert report.is_valid is True
    assert report.validity == 1.0

    # -- provenance is unioned forward across the update, not dropped (AC#2) --
    assert set(active[0].record.provenance["source_episode_ids"]) == {"e1", "e2"}


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

    # A live-episode-id set that is missing "e1" simulates a bad rebuild/backfill: the citation is
    # real, but the episode it points at is not in the set the caller resolved.
    report = provenance_report(facts, live_episode_ids=set())

    assert report.is_valid is False
    assert report.validity == 0.0
