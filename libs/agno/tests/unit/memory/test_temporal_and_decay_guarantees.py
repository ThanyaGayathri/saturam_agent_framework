"""Bi-temporal + decay behavior tests (SM-X11 #147).

The three primitives this locks -- point-in-time queries (SM-G4), invalidate-not-delete
(SM-G5), and decay/reinforcement (SM-E4) -- already carry extensive unit coverage of their own
(``test_provider_co_located.py``, ``test_episodic_decay.py``, ``test_episodic_forget.py``,
``test_graph_substrate.py``). This file does not repeat that: it covers what none of those files
does -- a belief chain longer than one supersession, and the row-vs-edge boundary ADR 0003
section B.6 rules on (episodic archival/reinforcement and semantic-fact invalidation are
independent write authorities that must never blur into each other).
"""

import pytest

from egpt.memory.adapters.co_located import CoLocatedMemoryProvider
from egpt.memory.config import MemoryProviderConfig
from egpt.memory.episodic.decay import reinforce
from egpt.memory.episodic.forget import DEFAULT_PRUNE_THRESHOLD, is_prune_eligible
from egpt.memory.graph.relations import ExtractedRelation, RelationExtractionPipeline
from egpt.memory.graph.temporal import ValidTimeSpan
from egpt.memory.schemas.episodic import EpisodicRecord
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import MemoryEvent, MemoryQuery, Scope
from tests.unit.memory._fakes import FakeGraph, FakeVector

ACME = Scope(tenant_id="acme", user_id="u1")


def event(content, **kwargs):
    return MemoryEvent(content=content, scope=kwargs.pop("scope", ACME), **kwargs)


class _RelationStub:
    """Same shape as ``test_provider_co_located.py``'s local stub -- kept local here too per
    that file's own note ("move to conftest.py once a second file needs a stub RelationExtractor"):
    this is that second file, but the stub differs enough (keyed by episode id, not content, since
    this file's three episodes share no distinguishing substring a content-keyed stub could use)
    that consolidating it would need to change both call sites, which is out of this ticket's
    scope."""

    def __init__(self, by_id):
        self.by_id = by_id

    def extract(self, episode):
        return list(self.by_id.get(episode.id, ()))

    async def aextract(self, episode):
        return self.extract(episode)


class _FixedValidTime:
    def __init__(self, by_content):
        self.by_content = by_content

    def extract(self, content, ingest_time=None):
        return self.by_content[content]

    async def aextract(self, content, ingest_time=None):
        return self.extract(content, ingest_time)


def _three_state_employer_chain():
    """Ada works at Acme (t=100), then Beta (t=200), then Gamma (t=300) -- one contradiction
    per consolidation pass, so the graph accumulates three edges for one (subject, predicate)."""
    extractor = _RelationStub(
        by_id={
            "e1": [ExtractedRelation(source="Ada", relation="works_at", target="Acme Corp")],
            "e2": [ExtractedRelation(source="Ada", relation="works_at", target="Beta Inc")],
            "e3": [ExtractedRelation(source="Ada", relation="works_at", target="Gamma LLC")],
        }
    )
    valid_time = _FixedValidTime(
        by_content={
            "Ada joined Acme": ValidTimeSpan(t_valid=100),
            "Ada moved to Beta": ValidTimeSpan(t_valid=200),
            "Ada moved to Gamma": ValidTimeSpan(t_valid=300),
        }
    )
    vector, graph = FakeVector(), FakeGraph()
    pipeline = RelationExtractionPipeline(graph=graph, extractor=extractor, valid_time_extractor=valid_time)
    p = CoLocatedMemoryProvider(
        vector=vector, graph=graph, config=MemoryProviderConfig(provider="co_located"), relation_pipeline=pipeline
    )

    p.write(ACME, [event("Ada joined Acme", id="e1")])
    p.consolidate(ACME)
    p.write(ACME, [event("Ada moved to Beta", id="e2")])
    p.consolidate(ACME)
    p.write(ACME, [event("Ada moved to Gamma", id="e3")])
    report = p.consolidate(ACME)

    return p, graph, report


def _relation_records(p, as_of):
    """Only the graph-relation edges for (Ada, works_at, *) -- the provider's own consolidation
    also writes a deduplicated *episode-shaped* semantic fact per episode (unrelated to the
    relation pipeline), and the fake vector's fuzzy word-overlap match returns those too for any
    query sharing a word with them ("ada"). Filtering on the relation provenance this pipeline
    stamps is what isolates the chain under test from that unrelated dedup path."""
    records = p.read(ACME, MemoryQuery(text="ada works_at", tiers=[MemoryTier.SEMANTIC], as_of=as_of)).records
    return [r for r in records if r.provenance.get("predicate") == "works_at"]


def _current_employer(p, as_of):
    records = _relation_records(p, as_of)
    assert len(records) == 1, f"expected exactly one current belief at as_of={as_of}, got {records}"
    return records[0].content


# ---------------------------------------------------------------------------
# AC1: point-in-time query correct, across a chain longer than one supersession
# ---------------------------------------------------------------------------


def test_point_in_time_reconstructs_every_link_of_a_three_step_belief_chain():
    p, _, report = _three_state_employer_chain()

    # Two contradictions along the chain: Beta supersedes Acme, Gamma supersedes Beta.
    assert report.contradictions_resolved == 1  # this pass's own consolidate() call only sees the last one

    assert _current_employer(p, as_of=150) == "Ada works_at Acme Corp"
    assert _current_employer(p, as_of=250) == "Ada works_at Beta Inc"
    assert _current_employer(p, as_of=350) == "Ada works_at Gamma LLC"
    assert _current_employer(p, as_of=None) == "Ada works_at Gamma LLC"  # current belief, no as_of


def test_a_point_in_time_query_between_two_valid_windows_sees_nothing():
    """t=100 is Acme's open bound; querying strictly before it must find no belief at all, not
    the nearest one -- a chain regression that silently widened a window would slip past a test
    that only ever asks inside a valid range."""
    p, _, _ = _three_state_employer_chain()

    assert _relation_records(p, as_of=50) == []


# ---------------------------------------------------------------------------
# AC2: invalidate-not-delete -- every superseded state stays independently queryable
# ---------------------------------------------------------------------------


def test_every_superseded_state_in_the_chain_remains_queryable_and_untouched():
    p, graph, _ = _three_state_employer_chain()

    # Relation edges only -- `graph.rows` also holds the episodes themselves and the provider's
    # own episode-shaped dedup facts (see `_relation_records`'s docstring).
    rows = [r for r in graph.rows["acme"] if r.provenance.get("predicate") == "works_at"]

    # SCD-2 expire-and-reinsert (finding F10 / SM-R10 section 6): each of the chain's two
    # contradictions (Acme->Beta, Beta->Gamma) expires the live row *and* inserts a bounded copy
    # rather than editing it -- three original facts plus two bounded copies, nothing deleted.
    assert len(rows) == 5

    # `is_current_belief()` alone (`expired_at IS NULL`) is only the transaction-time half of
    # SM-R10 section 7's 5a predicate -- a bounded copy is never re-expired transactionally, so it
    # stays "current belief" in that sense even though its valid-time window has closed. The row
    # presently true in the world needs both halves: `expired_at IS NULL` and `t_invalid IS NULL`.
    current = [r for r in rows if r.is_current_belief()]
    assert len(current) == 3  # Gamma (never bounded) + the two bounded copies of Acme/Beta
    open_ended = [r for r in current if r.t_invalid is None]
    assert [r.content for r in open_ended] == ["Ada works_at Gamma LLC"]

    # The bound that retired each superseded employer was inserted, not edited in place: the
    # bounded copy exists with the tight t_invalid, and the row it was copied from is untouched.
    acme_bounded = [r for r in rows if r.content == "Ada works_at Acme Corp" and r.t_invalid == 200]
    beta_bounded = [r for r in rows if r.content == "Ada works_at Beta Inc" and r.t_invalid == 300]
    assert len(acme_bounded) == 1
    assert len(beta_bounded) == 1

    # Both superseded states remain reachable, independent of each other's bound.
    assert _current_employer(p, as_of=150) == "Ada works_at Acme Corp"
    assert _current_employer(p, as_of=250) == "Ada works_at Beta Inc"


# ---------------------------------------------------------------------------
# Row vs edge boundary (ADR 0003 section B.6): episodic lifecycle and semantic-fact
# invalidation are independent write authorities -- neither may blur into the other.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("half", ["sync", "async"])
async def test_invalidating_a_semantic_fact_leaves_its_source_episodes_row_untouched(half):
    """The graph is sole authority for semantic contradiction (ADR 0003 section B.6); the
    episode a fact was derived from has no graph edge owning *its* validity, so invalidating the
    fact must not reach back and alter the episode's own bi-temporal or decay state."""
    p, graph, _ = _three_state_employer_chain()

    # Filtered on id, not just tier + text: the fake vector's word-overlap match also returns the
    # chain's other two episodes for any query sharing a word with them ("ada").
    before = [
        r
        for r in p.read(ACME, MemoryQuery(text="ada joined acme", tiers=[MemoryTier.EPISODIC])).records
        if r.id == "e1"
    ]
    assert len(before) == 1
    source_episode = before[0]

    # A further, unrelated invalidation of the *current* semantic belief -- something the graph's
    # sole authority is free to do independently of any episode.
    edge_id = _edge_id_for(graph, "Ada works_at Gamma LLC")
    if half == "sync":
        graph.invalidate(ACME, edge_id, t_invalid=400, at=410)
    else:
        await graph.ainvalidate(ACME, edge_id, t_invalid=400, at=410)

    after = [
        r
        for r in p.read(ACME, MemoryQuery(text="ada joined acme", tiers=[MemoryTier.EPISODIC])).records
        if r.id == "e1"
    ]
    assert len(after) == 1
    after_episode = after[0]

    assert after_episode.expired_at is None
    assert source_episode.expired_at is None
    assert after_episode.t_valid == source_episode.t_valid
    assert after_episode.t_invalid == source_episode.t_invalid
    assert after_episode.metadata.get("importance") == source_episode.metadata.get("importance")
    assert after_episode.metadata.get("last_accessed_at") == source_episode.metadata.get("last_accessed_at")


def _edge_id_for(graph, content):
    for row in graph.rows["acme"]:
        if row.content == content and row.is_current_belief():
            return row.id
    raise AssertionError(f"no current-belief row with content {content!r}")


# ---------------------------------------------------------------------------
# AC3: decay + reinforcement -- reinforcement's actual, documented consequence
# ---------------------------------------------------------------------------


def _stale_episode(**overrides) -> EpisodicRecord:
    defaults = dict(id="ep-1", tenant_id="acme", content="shipped v2 to production", created_at=0)
    defaults.update(overrides)
    return EpisodicRecord(**defaults)


def test_reinforcing_a_prune_eligible_episode_makes_it_ineligible_again():
    """The issue's own testing note: 'access reinforces'. Both ``reinforce`` (SM-E4) and
    ``is_prune_eligible`` (SM-E9) already have exhaustive unit coverage in isolation, but nothing
    composes them -- this is the actual behavioral guarantee 'reinforcement' names: an episode
    that decayed below the prune floor is rescued by a confirmed access, not just its timestamp."""
    day = 24 * 3600
    old = _stale_episode(last_accessed_at=0, importance=0.0)
    now = int(25 * day)  # 5 half-lives at importance 0 (base 5-day half-life): 0.5**5 < 0.05

    assert is_prune_eligible(old, now=now, prune_threshold=DEFAULT_PRUNE_THRESHOLD) is True

    accessed = reinforce(old, at=now)

    assert is_prune_eligible(accessed, now=now, prune_threshold=DEFAULT_PRUNE_THRESHOLD) is False


def test_reinforcement_does_not_rescue_an_episode_that_is_already_archived():
    """SM-E9's own rule (``forget.is_prune_eligible``): an archived episode is never eligible
    again, regardless of its decay score -- reinforcing it after the fact must not resurrect it
    into eligibility either way, since archival and decay-scoring are different axes."""
    day = 24 * 3600
    archived = _stale_episode(last_accessed_at=0, importance=0.0, expired_at=int(day))
    now = int(25 * day)

    reinforced = reinforce(archived, at=now)

    assert is_prune_eligible(reinforced, now=now, prune_threshold=DEFAULT_PRUNE_THRESHOLD) is False
