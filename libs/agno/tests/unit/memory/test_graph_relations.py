"""Relation/fact edge extraction into typed graph edges (SM-G3 #101).

The three acceptance criteria, each with a test that fails without the code: a typed edge reaches
the graph between two entities per interaction, it carries provenance back to the source episode,
and the pass runs on the consolidation lane and **never** on a read.

Entity resolution is out of scope here exactly as it is in ``test_graph_entities.py``: two
spellings of one entity address two different endpoint nodes, and a test asserting otherwise would
be asserting SM-G8's (#106) work.
"""

import asyncio
from typing import List, Optional, Tuple

import pytest

from egpt.memory.adapters.co_located import CoLocatedMemoryProvider
from egpt.memory.config import MemoryProviderConfig, MemoryProviderConfigError
from egpt.memory.graph.relations import (
    RELATION_EXTRACTOR_ID,
    ExtractedRelation,
    ExtractedRelations,
    LLMRelationExtractor,
    RelationExtractionPipeline,
    relation_edge_id_for,
)
from egpt.memory.graph.temporal import RuleBasedValidTimeExtractor, ValidTimeSpan
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import (
    NO_RELATIONS,
    FactWrite,
    MemoryEvent,
    MemoryOperationError,
    MemoryQuery,
    MemoryRecord,
    MemoryScopeRequiredError,
    PartialWriteError,
    Scope,
)
from tests.unit.memory._fakes import FakeGraph, FakeVector

ACME = Scope(tenant_id="acme", user_id="ada")
BOB = Scope(tenant_id="acme", user_id="bob")


class StubExtractor:
    """Returns whatever it was configured with, keyed on episode content.

    Deliberately not an LLM: what this file tests is the pipeline's decisions -- provenance,
    scope, merging, refusal -- none of which a model is involved in.
    """

    def __init__(self, by_content=None, default=()):
        self.by_content = by_content or {}
        self.default = default
        self.seen = []

    def extract(self, episode):
        self.seen.append(episode.id)
        return list(self.by_content.get(episode.content, self.default))

    async def aextract(self, episode):
        await asyncio.sleep(0)
        return self.extract(episode)


def relation(source="Ada", relation="works_at", target="Acme Corp", **kwargs):
    return ExtractedRelation(source=source, relation=relation, target=target, **kwargs)


def episode(content, episode_id="e1", scope=ACME, **kwargs):
    return MemoryRecord(id=episode_id, tier=MemoryTier.EPISODIC, content=content, scope=scope, **kwargs)


def pipeline(extractor=None, graph=None, **kwargs):
    graph = graph if graph is not None else FakeGraph()
    return (
        RelationExtractionPipeline(
            graph=graph,
            extractor=extractor if extractor is not None else StubExtractor(default=[relation()]),
            **kwargs,
        ),
        graph,
    )


def edge_for(graph, source="Ada", rel="works_at", target="Acme Corp", scope=ACME):
    return next(f for f in graph.facts if f.subject == source and f.predicate == rel and f.object == target)


# ---------------------------------------------------------------------------
# Schema boundary -- an empty field is refused at parse, not three frames deep
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("blank", ["", "   "])
@pytest.mark.parametrize("field", ["source", "relation", "target"])
def test_an_empty_field_is_refused_at_the_schema_boundary(field, blank):
    """A model answering with an empty or whitespace-only string must fail at parse time, not
    reach ``relation_edge_id_for`` three frames deeper -- where, if episodic facts already
    committed, the refusal surfaces as a ``PartialWriteError`` with the real cause buried in its
    chain. Whitespace-only is the case ``min_length=1`` alone misses -- ``" "`` has length 1 and
    passes it, then strips to ``""`` downstream -- which is why ``_strip_and_check`` strips
    *before* the length check runs rather than leaving the constraint to catch it alone."""
    from pydantic import ValidationError

    fields = {"source": "Ada", "relation": "works_at", "target": "Acme Corp", field: blank}

    with pytest.raises(ValidationError):
        ExtractedRelation(**fields)


# ---------------------------------------------------------------------------
# AC 1 -- typed edges created between entities
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("is_async", [False, True])
def test_a_relation_reaches_the_graph_as_a_typed_edge(is_async):
    """The criterion itself: a full SPO triple, landed through ``upsert_facts``."""
    pipe, graph = pipeline()
    call = (
        pipe.arun(ACME, [episode("Ada works at Acme")]) if is_async else pipe.run(ACME, [episode("Ada works at Acme")])
    )
    report = asyncio.run(call) if is_async else call

    assert report.edges_written == 1
    fact = edge_for(graph)
    assert fact.is_triple() is True
    assert fact.derived_by == RELATION_EXTRACTOR_ID
    assert graph.calls.count("aupsert_facts" if is_async else "upsert_facts") == 1


def test_the_edge_id_is_deterministic_in_the_triple():
    """Re-extraction across passes must upsert the same edge, not accumulate a duplicate one --
    ``upsert_facts``'s triple leg merges on ``(scope_key, id, live)``."""
    first = relation_edge_id_for(ACME, "Ada", "works_at", "Acme Corp")
    second = relation_edge_id_for(ACME, "Ada", "works_at", "Acme Corp")

    assert first == second
    assert first != relation_edge_id_for(ACME, "Ada", "reports_to", "Acme Corp")
    assert first != relation_edge_id_for(BOB, "Ada", "works_at", "Acme Corp")


@pytest.mark.parametrize("field", ["source", "relation", "target"])
def test_relation_edge_id_for_refuses_a_whitespace_only_argument(field):
    """A direct caller that bypasses ``ExtractedRelation``'s validator is unprotected against a
    blank argument until this function's own check -- pinned separately from the schema-boundary
    tests above, which exercise the pydantic path, not this one."""
    fields = {"source": "Ada", "relation": "works_at", "target": "Acme Corp", field: "   "}

    with pytest.raises(MemoryOperationError) as excinfo:
        relation_edge_id_for(ACME, **fields)

    assert excinfo.value.field_name == field


def test_relation_edge_id_for_normalizes_whitespace_before_deriving_the_id():
    """Documented in the function's own docstring: a caller storing ``" Ada "`` and one storing
    ``"Ada"`` must address the same edge, or a query built from the un-stripped form silently
    misses the row this function's own caller wrote."""
    assert relation_edge_id_for(ACME, " Ada ", "works_at", "Acme Corp") == relation_edge_id_for(
        ACME, "Ada", "works_at", "Acme Corp"
    )


# ---------------------------------------------------------------------------
# AC 2/3 -- bi-temporal valid-time (SM-G4 #102): extracted where present, and a
# point-in-time read over the written edge returns the correct state
# ---------------------------------------------------------------------------


class StubValidTimeExtractor:
    """Returns a fixed span regardless of content, so a test can assert the pipeline *uses* what
    the extractor returns without depending on the rule-based regex."""

    def __init__(self, span: ValidTimeSpan) -> None:
        self.span = span
        self.seen: List[Tuple[str, Optional[int]]] = []

    def extract(self, content: str, ingest_time: Optional[int] = None) -> ValidTimeSpan:
        self.seen.append((content, ingest_time))
        return self.span

    async def aextract(self, content: str, ingest_time: Optional[int] = None) -> ValidTimeSpan:
        await asyncio.sleep(0)
        return self.extract(content, ingest_time)


def test_the_default_valid_time_extractor_is_rule_based():
    pipe, _ = pipeline()

    assert isinstance(pipe.valid_time_extractor, RuleBasedValidTimeExtractor)


@pytest.mark.parametrize("is_async", [False, True])
def test_a_relation_edge_carries_the_valid_time_the_extractor_returns(is_async):
    """The criterion itself: what the extractor reports lands on the written edge's
    ``t_valid``/``t_invalid``, not left ``None`` the way every edge was before this ticket."""
    vt = StubValidTimeExtractor(
        ValidTimeSpan(t_valid=1704067200, t_invalid=1717200000, confidence=0.9, source="explicit")
    )
    pipe, graph = pipeline(valid_time_extractor=vt)
    episodes = [episode("Ada works at Acme since Jan 2024")]

    call = pipe.arun(ACME, episodes) if is_async else pipe.run(ACME, episodes)
    asyncio.run(call) if is_async else call

    fact = edge_for(graph)
    assert fact.record.t_valid == 1704067200
    assert fact.record.t_invalid == 1717200000


@pytest.mark.parametrize("is_async", [False, True])
def test_the_extractor_is_called_once_per_episode_content_not_once_per_relation(is_async):
    """Two relations from one episode share one span -- they were dated by the same sentence, so
    extracting per-relation would let two edges from one episode disagree about validity.

    Parametrized on ``is_async`` (round-3 review, PR #101): every other test in this file covering
    both halves does; this one didn't, leaving the async "once per episode" invariant untested even
    though ``StubValidTimeExtractor.aextract`` delegates to ``extract`` and records the same
    ``seen`` list."""
    vt = StubValidTimeExtractor(ValidTimeSpan(t_valid=100, source="ingest_fallback"))
    extractor = StubExtractor(default=[relation(relation="works_at"), relation(relation="reports_to")])
    pipe, graph = pipeline(extractor=extractor, valid_time_extractor=vt)
    episodes = [episode("Ada works at and reports to Acme", episode_id="e1")]

    call = pipe.arun(ACME, episodes) if is_async else pipe.run(ACME, episodes)
    asyncio.run(call) if is_async else call

    assert vt.seen == [("Ada works at and reports to Acme", None)]
    assert len(graph.facts) == 2
    assert {f.record.t_valid for f in graph.facts} == {100}


def test_a_relation_edge_falls_back_to_the_episodes_valid_time_as_ingest_time():
    """Spec section 5's "extracted valid-time, else ingest time": with nothing stated, the span is
    anchored on when the source episode itself happened, not on the pass's wall clock -- the two
    differ under batched/async consolidation (SM-L1 #122)."""
    pipe, graph = pipeline(valid_time_extractor=RuleBasedValidTimeExtractor())

    pipe.run(ACME, [episode("Ada works at Acme", t_valid=555)])

    assert edge_for(graph).record.t_valid == 555


def test_a_relation_edge_falls_back_to_the_episodes_created_at_when_it_has_no_valid_time():
    pipe, graph = pipeline(valid_time_extractor=RuleBasedValidTimeExtractor())

    pipe.run(ACME, [episode("Ada works at Acme", created_at=777)])

    assert edge_for(graph).record.t_valid == 777


@pytest.mark.parametrize(
    "content, expected_t_valid, expected_t_invalid",
    [
        ("Ada works at Acme since Jan 2024", 1704067200, None),
        ("Ada works at Acme from Jun 2024 until Sep 2024", 1717200000, 1725148800),
        ("Ada worked at Acme in 2019", 1546300800, None),
        ("Ada joined Acme on 2023-04-10", 1681084800, None),
    ],
)
def test_the_rule_based_extractor_reads_explicit_dates_out_of_episode_content(
    content, expected_t_valid, expected_t_invalid
):
    """End-to-end through the real (non-stub) extractor: SM-R10's regex+month-map rule, exercised
    the way ``run`` actually calls it -- one span per episode content."""
    pipe, graph = pipeline(
        valid_time_extractor=RuleBasedValidTimeExtractor(), extractor=StubExtractor(default=[relation()])
    )

    pipe.run(ACME, [episode(content)])

    fact = edge_for(graph)
    assert fact.record.t_valid == expected_t_valid
    assert fact.record.t_invalid == expected_t_invalid


@pytest.mark.parametrize(
    "at,visible", [(1704067100, False), (1704067200, True), (1717199999, True), (1717200000, False)]
)
@pytest.mark.parametrize("is_async", [False, True])
def test_true_at_t_returns_the_correct_edge_state_for_an_extracted_relation(is_async, at, visible):
    """AC 3, end-to-end: extraction through the consolidation lane, then a point-in-time read over
    the fused set. ``co_located``'s ``as_of`` filter and the Neo4j double's bi-temporal envelope
    predate this ticket (SM-G1 #99) -- this proves they compose correctly with what this ticket
    actually adds, the extracted ``t_valid``/``t_invalid`` on a relation edge.

    Parametrized on ``is_async`` (round-2 review, PR #101): the sync-only version left the full
    ``arun`` + ``aconsolidate`` + ``aread`` chain unexercised even though the pipeline's own
    ``is_async`` parametrization elsewhere covers ``arun`` in isolation."""
    extractor = StubExtractor(default=[relation()])
    graph = FakeGraph()
    pipe = RelationExtractionPipeline(
        graph=graph, extractor=extractor, valid_time_extractor=RuleBasedValidTimeExtractor()
    )
    memory = CoLocatedMemoryProvider(
        vector=FakeVector(),
        graph=graph,
        config=MemoryProviderConfig(provider="co_located"),
        relation_pipeline=pipe,
    )
    events = [MemoryEvent(content="Ada works at Acme from Jan 2024 until Jun 2024", scope=ACME)]

    if is_async:

        async def run():
            await memory.awrite(ACME, events)
            await memory.aconsolidate(ACME)
            return (await memory.aread(ACME, MemoryQuery(text="ada acme corp", as_of=at))).records

        found = asyncio.run(run())
    else:
        memory.write(ACME, events)
        memory.consolidate(ACME)
        found = memory.read(ACME, MemoryQuery(text="ada acme corp", as_of=at)).records

    assert bool(found) is visible


# ---------------------------------------------------------------------------
# Contradiction -> invalidation, not delete (SM-G5 #103)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("is_async", [False, True])
def test_a_contradicting_fact_invalidates_the_superseded_edge(is_async):
    """The ticket's own three acceptance criteria in one test: a later fact naming a different
    object for the same ``(subject, relation)`` retires the earlier edge -- ``t_invalid`` set via
    expire-and-reinsert, nothing deleted, and the report counts the retirement."""
    extractor = StubExtractor(
        by_content={
            "Ada works at Acme": [relation(target="Acme Corp")],
            "Ada works at Beta": [relation(target="Beta Inc")],
        }
    )
    pipe, graph = pipeline(extractor=extractor, valid_time_extractor=RuleBasedValidTimeExtractor())
    old_id = relation_edge_id_for(ACME, "Ada", "works_at", "Acme Corp")
    new_id = relation_edge_id_for(ACME, "Ada", "works_at", "Beta Inc")

    first = episode("Ada works at Acme", "e1", t_valid=100)
    second = episode("Ada works at Beta", "e2", t_valid=200)
    if is_async:
        asyncio.run(pipe.arun(ACME, [first]))
        report = asyncio.run(pipe.arun(ACME, [second]))
    else:
        pipe.run(ACME, [first])
        report = pipe.run(ACME, [second])

    assert report.edges_invalidated == 1
    # The async path actually calls the async verb (round-2 review, PR #110): `graph.rows`
    # mutating correctly does not prove `_awrite_batch` reached it through `ainvalidate` rather
    # than the sync `invalidate` -- `FakeGraph` would leave the same state either way, since
    # nothing here awaits between the check and the call. The verb itself is what distinguishes a
    # regression that silently drops back to the sync method on the async path.
    assert graph.calls.count("ainvalidate" if is_async else "invalidate") == 1
    rows = graph.rows["acme"]
    # No data loss: both the original row and its bounded copy are still in the graph.
    old_rows = [row for row in rows if row.id == old_id]
    assert len(old_rows) == 2
    assert any(row.t_invalid is None and row.is_current_belief() is False for row in old_rows), (
        "the original row is expired, never edited in place"
    )
    bounded_copy = next(row for row in old_rows if row.is_current_belief())
    assert bounded_copy.t_invalid == 200

    # Point-in-time: after t=200 only the new object is valid; the old edge is still there (see
    # ``test_the_superseded_edge_is_still_queryable_at_an_earlier_instant``) but no longer current.
    after = graph.recall_facts(ACME, entities=["Ada"], relation="works_at", since=200, until=300)
    assert {row.id for row in after} == {new_id}


@pytest.mark.parametrize("is_async", [False, True])
def test_two_candidates_contradicting_one_edge_in_the_same_batch_keep_the_earliest_t_valid(is_async):
    """``_contradicted``'s merge branch (round-3 review, PR #110): two candidates in **one**
    ``run``/``arun`` call, each naming a different object for the same ``(subject, relation)``,
    both retire the one pre-existing edge they share -- and the retirement keeps the **earlier**
    ``t_valid``, the instant that edge actually stopped being current, not whichever candidate the
    loop happened to check last.

    **The candidates' object names are chosen so the alphabetically-last one is also the
    temporally-last (highest ``t_valid``)** (round-4 review, PR #110): ``_merged`` sorts candidates
    by edge id -- alphabetical on the object name here -- before ``_contradicted`` ever iterates
    them, regardless of the episode list order this test passes in. "Alpha Inc" (``t_valid=150``,
    the true minimum) therefore processes *first*, and "Zeta LLC" (``t_valid=250``) processes
    *last*. That is what makes a genuine last-processed-wins bug diverge from the correct merge:
    last-processed-wins always lands on whichever candidate ``_contradicted`` reaches last --
    ``250`` here -- while the correct merge keeps the true minimum, ``150``, regardless of
    processing order. An earlier version of this test picked names ("Beta Inc" 250, "Gamma LLC"
    150) where the alphabetically-last candidate happened to *also* hold the true minimum, so
    last-processed-wins and min-wins produced the identical answer and the test passed against
    either implementation.

    Every other test in this file presents at most one candidate per existing edge, so this branch
    was previously reachable only by inspection of the code, not by any test failing without it.

    Also covers the gap that stood here before SM-L5 (#126): "Alpha Inc" and "Zeta LLC" are
    themselves two same-pass candidates naming the same ``(subject, relation)`` with different
    objects, so once the within-batch check exists, the earlier one ("Alpha Inc", ``t_valid=150``)
    is *also* contradicted by the later one -- on top of "Acme Corp" being contradicted by both.
    Before SM-L5 the two new candidates did not contradict each other and both landed as live
    edges; :func:`test_two_same_pass_candidates_contradicting_each_other_invalidate_the_earlier_one`
    isolates that behaviour on its own, without the pre-existing-edge contradiction this test is
    really about.
    """
    extractor = StubExtractor(
        by_content={
            "Ada works at Acme": [relation(target="Acme Corp")],
            "Ada works at Alpha": [relation(target="Alpha Inc")],
            "Ada works at Zeta": [relation(target="Zeta LLC")],
        }
    )
    pipe, graph = pipeline(extractor=extractor, valid_time_extractor=RuleBasedValidTimeExtractor())
    old_id = relation_edge_id_for(ACME, "Ada", "works_at", "Acme Corp")
    alpha_id = relation_edge_id_for(ACME, "Ada", "works_at", "Alpha Inc")

    pipe.run(ACME, [episode("Ada works at Acme", "e0", t_valid=100)])
    alpha = episode("Ada works at Alpha", "e1", t_valid=150)
    zeta = episode("Ada works at Zeta", "e2", t_valid=250)
    if is_async:
        report = asyncio.run(pipe.arun(ACME, [alpha, zeta]))
    else:
        report = pipe.run(ACME, [alpha, zeta])

    # Acme Corp (contradicted by both Alpha Inc and Zeta LLC, keeps the earliest) and Alpha Inc
    # (contradicted by Zeta LLC within this same batch) -- two edges, not one.
    assert report.edges_invalidated == 2
    bounds = graph.bounds_for_ids(ACME, [old_id, alpha_id])
    assert bounds[old_id].t_invalid == 150
    assert bounds[alpha_id].t_invalid == 250

    # Point-in-time, not an unbounded recall_facts: the Acme edge's bounded SCD-2 copy is still
    # "current belief" on the transaction axis (expired_at IS NULL) -- only a valid-time window
    # after both new candidates' t_valid actually excludes it.
    after = graph.recall_facts(ACME, entities=["Ada"], relation="works_at", since=150, until=300)
    assert {row.provenance.get("object") for row in after} == {"Alpha Inc", "Zeta LLC"}


# ---------------------------------------------------------------------------
# Same-pass candidates contradicting each other (SM-L5 #126)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("is_async", [False, True])
def test_two_same_pass_candidates_contradicting_each_other_invalidate_the_earlier_one(is_async):
    """The gap the module docstring named as SM-L5's, isolated with no pre-existing edge in play:
    two candidates in **one** ``run``/``arun`` call, same ``(subject, relation)``, different
    ``object`` -- before SM-L5 both landed as live edges with neither retiring the other; now the
    earlier-dated one is retired by the later, the same rule ``_contradicted`` already applied
    against pre-existing graph state."""
    extractor = StubExtractor(
        by_content={
            "Ada works at Acme": [relation(target="Acme Corp")],
            "Ada works at Beta": [relation(target="Beta Inc")],
        }
    )
    pipe, graph = pipeline(extractor=extractor, valid_time_extractor=RuleBasedValidTimeExtractor())
    acme_id = relation_edge_id_for(ACME, "Ada", "works_at", "Acme Corp")
    beta_id = relation_edge_id_for(ACME, "Ada", "works_at", "Beta Inc")

    acme = episode("Ada works at Acme", "e1", t_valid=100)
    beta = episode("Ada works at Beta", "e2", t_valid=200)
    if is_async:
        report = asyncio.run(pipe.arun(ACME, [acme, beta]))
    else:
        report = pipe.run(ACME, [acme, beta])

    assert report.edges_invalidated == 1
    assert graph.calls.count("ainvalidate" if is_async else "invalidate") == 1
    rows = graph.rows["acme"]
    acme_rows = [row for row in rows if row.id == acme_id]
    # No data loss: both the edge upsert_facts wrote and its invalidated bounded copy exist.
    assert len(acme_rows) == 2
    assert any(row.t_invalid is None and not row.is_current_belief() for row in acme_rows)
    bounded_copy = next(row for row in acme_rows if row.is_current_belief())
    assert bounded_copy.t_invalid == 200
    # Beta Inc, the winner, was written once and never itself retired.
    assert len([row for row in rows if row.id == beta_id]) == 1


def test_two_same_pass_candidates_starting_at_the_exact_same_instant_do_not_contradict():
    """Mirrors ``test_two_facts_starting_at_the_exact_same_instant_do_not_contradict`` for the
    within-batch check: a tie means neither is "earlier", so :meth:`_contradicted_within` must not
    arbitrarily pick a loser."""
    same_time = FactWrite(
        record=MemoryRecord(id="acme", tier=MemoryTier.SEMANTIC, content="x", scope=ACME, t_valid=100),
        subject="Ada",
        predicate="works_at",
        object="Acme Corp",
    )
    other = FactWrite(
        record=MemoryRecord(id="beta", tier=MemoryTier.SEMANTIC, content="y", scope=ACME, t_valid=100),
        subject="Ada",
        predicate="works_at",
        object="Beta Inc",
    )

    assert RelationExtractionPipeline._contradicted_within([same_time, other]) == {}


def test_a_same_pass_candidate_with_no_valid_time_cannot_contradict_a_sibling():
    """The within-batch mirror of ``test_a_candidate_with_no_valid_time_cannot_contradict_anything``
    -- unreachable through the pipeline for the same reason (``ValidTimeSpan.t_valid`` is never
    ``None``), exercised directly against the static predicate."""
    dated = FactWrite(
        record=MemoryRecord(id="acme", tier=MemoryTier.SEMANTIC, content="x", scope=ACME, t_valid=100),
        subject="Ada",
        predicate="works_at",
        object="Acme Corp",
    )
    undated = FactWrite(
        record=MemoryRecord(id="beta", tier=MemoryTier.SEMANTIC, content="y", scope=ACME, t_valid=None),
        subject="Ada",
        predicate="works_at",
        object="Beta Inc",
    )

    assert RelationExtractionPipeline._contradicted_within([dated, undated]) == {}
    assert RelationExtractionPipeline._contradicted_within([undated, dated]) == {}


def test_three_same_pass_candidates_chain_pairwise_not_just_adjacent():
    """Three candidates sharing one ``(subject, relation)``: the middle one loses to the last and
    beats the first, both true at once -- a pairwise comparison catches both; an implementation
    that only compared adjacent-in-sort-order entries would miss the first-vs-last pair."""
    first = FactWrite(
        record=MemoryRecord(id="acme", tier=MemoryTier.SEMANTIC, content="x", scope=ACME, t_valid=100),
        subject="Ada",
        predicate="works_at",
        object="Acme Corp",
    )
    middle = FactWrite(
        record=MemoryRecord(id="beta", tier=MemoryTier.SEMANTIC, content="y", scope=ACME, t_valid=200),
        subject="Ada",
        predicate="works_at",
        object="Beta Inc",
    )
    last = FactWrite(
        record=MemoryRecord(id="zeta", tier=MemoryTier.SEMANTIC, content="z", scope=ACME, t_valid=300),
        subject="Ada",
        predicate="works_at",
        object="Zeta LLC",
    )

    assert RelationExtractionPipeline._contradicted_within([first, middle, last]) == {"acme": 200, "beta": 300}


@pytest.mark.parametrize("is_async", [False, True])
def test_edges_invalidated_accumulates_across_scope_batches_in_one_pass(is_async):
    """``run``/``arun`` sum ``edges_invalidated`` across every scope batch a tenant-wide pass
    produces, not just the last one written (round-3 review, PR #110). The scope-isolation test
    proves scope filtering works; it does not prove two scopes that *both* legitimately retire an
    edge in the same pass are both counted -- the failure mode this test rules out is a `+=` that
    accidentally became a plain assignment when the loop grew a second scope."""
    extractor = StubExtractor(
        by_content={
            "Ada works at Acme": [relation(source="Ada", target="Acme Corp")],
            "Bob works at Acme": [relation(source="Bob", target="Acme Corp")],
            "Ada works at Beta": [relation(source="Ada", target="Beta Inc")],
            "Bob works at Beta": [relation(source="Bob", target="Beta Inc")],
        }
    )
    pipe, graph = pipeline(extractor=extractor, valid_time_extractor=RuleBasedValidTimeExtractor())

    pipe.run(ACME, [episode("Ada works at Acme", "e0a", t_valid=100, scope=ACME)])
    pipe.run(BOB, [episode("Bob works at Acme", "e0b", t_valid=100, scope=BOB)])

    tenant_wide = Scope(tenant_id="acme")
    contradicting = [
        episode("Ada works at Beta", "e1", t_valid=200, scope=ACME),
        episode("Bob works at Beta", "e2", t_valid=200, scope=BOB),
    ]
    if is_async:
        report = asyncio.run(pipe.arun(tenant_wide, contradicting))
    else:
        report = pipe.run(tenant_wide, contradicting)

    assert report.edges_invalidated == 2


@pytest.mark.parametrize("is_async", [False, True])
def test_the_superseded_edge_is_still_queryable_at_an_earlier_instant(is_async):
    """AC: "Old edge still queryable at earlier T" -- point-in-time reads before the new fact's
    ``t_valid`` must still see the old belief, exactly as any other bi-temporal edge does."""
    extractor = StubExtractor(
        by_content={
            "Ada works at Acme": [relation(target="Acme Corp")],
            "Ada works at Beta": [relation(target="Beta Inc")],
        }
    )
    pipe, graph = pipeline(extractor=extractor, valid_time_extractor=RuleBasedValidTimeExtractor())

    first = episode("Ada works at Acme", "e1", t_valid=100)
    second = episode("Ada works at Beta", "e2", t_valid=200)
    if is_async:
        asyncio.run(pipe.arun(ACME, [first]))
        asyncio.run(pipe.arun(ACME, [second]))
    else:
        pipe.run(ACME, [first])
        pipe.run(ACME, [second])

    before = graph.recall_facts(ACME, entities=["Ada"], relation="works_at", since=100, until=200)
    assert {row.provenance.get("object") for row in before} == {"Acme Corp"}
    after = graph.recall_facts(ACME, entities=["Ada"], relation="works_at", since=200, until=300)
    assert {row.provenance.get("object") for row in after} == {"Beta Inc"}


@pytest.mark.parametrize("is_async", [False, True])
def test_the_same_fact_re_extracted_does_not_contradict_itself(is_async):
    """Re-extraction upserts the same edge id (``relation_edge_id_for`` is deterministic in the
    triple) -- the object has not changed, so nothing is superseded."""
    pipe, graph = pipeline(
        extractor=StubExtractor(default=[relation()]), valid_time_extractor=RuleBasedValidTimeExtractor()
    )

    first = episode("Ada works at Acme", "e1", t_valid=100)
    second = episode("Ada works at Acme", "e2", t_valid=200)
    if is_async:
        asyncio.run(pipe.arun(ACME, [first]))
        report = asyncio.run(pipe.arun(ACME, [second]))
    else:
        pipe.run(ACME, [first])
        report = pipe.run(ACME, [second])

    assert report.edges_invalidated == 0
    assert len(graph.rows["acme"]) == 1


@pytest.mark.parametrize("is_async", [False, True])
def test_an_earlier_dated_fact_does_not_invalidate_a_later_live_one(is_async):
    """A candidate backfilling history (an earlier ``t_valid`` than an edge the graph already
    holds) is not a contradiction of the newer edge -- it did not happen after it, so it cannot be
    what superseded it. Guards against invalidating the wrong direction."""
    extractor = StubExtractor(
        by_content={
            "Ada works at Beta": [relation(target="Beta Inc")],
            "Ada works at Acme": [relation(target="Acme Corp")],
        }
    )
    pipe, graph = pipeline(extractor=extractor, valid_time_extractor=RuleBasedValidTimeExtractor())

    first = episode("Ada works at Beta", "e1", t_valid=200)
    second = episode("Ada works at Acme", "e2", t_valid=100)
    if is_async:
        asyncio.run(pipe.arun(ACME, [first]))
        report = asyncio.run(pipe.arun(ACME, [second]))
    else:
        pipe.run(ACME, [first])
        report = pipe.run(ACME, [second])

    assert report.edges_invalidated == 0
    live = graph.recall_facts(ACME, entities=["Ada"], relation="works_at")
    assert {row.provenance.get("object") for row in live} == {"Beta Inc", "Acme Corp"}


@pytest.mark.parametrize("is_async", [False, True])
def test_an_edge_already_closed_before_the_candidate_starts_does_not_contradict(is_async):
    """An existing edge whose own stated ``t_invalid`` already ends at or before the candidate's
    ``t_valid`` has nothing left to supersede -- the two describe adjacent, non-overlapping
    intervals, not a correction of one by the other.

    Explicit integer bounds via a stub extractor, not the rule-based regex (round-1 review, PR
    #110): parsing "from Jan 2024 until Jun 2024" / "since Jun 2024" into the *same* epoch was
    the intent, but if the pattern ever stopped matching both episodes would fall back to
    ``t_valid=<ingest_time>`` -- equal instants -- and the *same-instant* guard this test is not
    about would explain ``edges_invalidated == 0`` just as well, silently passing for the wrong
    reason. Two separate pipelines (one stub extractor + one stub valid-time extractor each) over
    one shared graph is what lets each write carry its own fixed, unambiguous span.
    """
    graph = FakeGraph()
    first = RelationExtractionPipeline(
        graph=graph,
        extractor=StubExtractor(default=[relation(target="Acme Corp")]),
        valid_time_extractor=StubValidTimeExtractor(ValidTimeSpan(t_valid=100, t_invalid=200, source="explicit")),
    )
    second = RelationExtractionPipeline(
        graph=graph,
        extractor=StubExtractor(default=[relation(target="Beta Inc")]),
        valid_time_extractor=StubValidTimeExtractor(ValidTimeSpan(t_valid=200, source="explicit")),
    )

    if is_async:
        asyncio.run(first.arun(ACME, [episode("Ada worked at Acme")]))
        report = asyncio.run(second.arun(ACME, [episode("Ada works at Beta")]))
    else:
        first.run(ACME, [episode("Ada worked at Acme")])
        report = second.run(ACME, [episode("Ada works at Beta")])

    assert report.edges_invalidated == 0
    assert len(graph.rows["acme"]) == 2


@pytest.mark.parametrize("is_async", [False, True])
def test_two_facts_starting_at_the_exact_same_instant_do_not_contradict(is_async):
    """The boundary of the ordering guard: an existing edge whose ``t_valid`` equals the
    candidate's own is not *strictly* older, so which one actually supersedes the other is
    ambiguous -- left alone rather than guessed at, deferred to SM-L5's fuller resolution."""
    extractor = StubExtractor(
        by_content={
            "Ada works at Acme": [relation(target="Acme Corp")],
            "Ada works at Beta": [relation(target="Beta Inc")],
        }
    )
    pipe, graph = pipeline(extractor=extractor, valid_time_extractor=RuleBasedValidTimeExtractor())

    first = episode("Ada works at Acme", "e1", t_valid=100)
    second = episode("Ada works at Beta", "e2", t_valid=100)
    if is_async:
        asyncio.run(pipe.arun(ACME, [first]))
        report = asyncio.run(pipe.arun(ACME, [second]))
    else:
        pipe.run(ACME, [first])
        report = pipe.run(ACME, [second])

    assert report.edges_invalidated == 0
    assert len(graph.rows["acme"]) == 2


def test_a_candidate_with_no_valid_time_cannot_contradict_anything():
    """``_contradicted``'s own guard: a candidate whose ``record.t_valid`` is unset carries no
    instant an existing edge could be said to have stopped being true at, so it is skipped rather
    than treated as always-superseding.

    Unreachable through the pipeline today -- ``ValidTimeSpan.t_valid`` is never ``None`` (its
    own ``__post_init__`` enforces that), so every candidate ``run``/``arun`` builds already
    satisfies the guard before it is ever checked. Exercised directly against the static
    predicate instead (round-1 review, PR #110), the only way this guard can actually be
    defeated -- by a hand-built ``FactWrite`` from a caller that bypasses the pipeline.
    """
    existing = [
        MemoryRecord(
            id="old",
            tier=MemoryTier.SEMANTIC,
            content="Ada works at Acme",
            scope=ACME,
            t_valid=100,
            provenance={"subject": "Ada", "predicate": "works_at", "object": "Acme Corp"},
        )
    ]
    candidate = FactWrite(
        record=MemoryRecord(id="new", tier=MemoryTier.SEMANTIC, content="Ada works at Beta", scope=ACME, t_valid=None),
        subject="Ada",
        predicate="works_at",
        object="Beta Inc",
    )

    assert RelationExtractionPipeline._contradicted(existing, [candidate]) == {}


def test_an_existing_edge_with_no_valid_time_is_eligible_to_be_superseded():
    """The mirror of the previous test: an *existing* edge with an unset ``t_valid`` is read as
    "always valid, since before any known instant" and so is treated as older than any dated
    candidate, not excluded (round-2 review, PR #110). Exercised the same way as the candidate-side
    guard -- directly against ``_contradicted`` -- since ``RuleBasedValidTimeExtractor`` can never
    produce a live edge with ``t_valid=None`` through the pipeline itself."""
    existing = [
        MemoryRecord(
            id="old",
            tier=MemoryTier.SEMANTIC,
            content="Ada works at Acme",
            scope=ACME,
            t_valid=None,
            provenance={"subject": "Ada", "predicate": "works_at", "object": "Acme Corp"},
        )
    ]
    candidate = FactWrite(
        record=MemoryRecord(id="new", tier=MemoryTier.SEMANTIC, content="Ada works at Beta", scope=ACME, t_valid=200),
        subject="Ada",
        predicate="works_at",
        object="Beta Inc",
    )

    assert RelationExtractionPipeline._contradicted(existing, [candidate]) == {"old": 200}


@pytest.mark.parametrize("is_async", [False, True])
def test_a_different_subject_under_the_same_relation_does_not_contradict(is_async):
    extractor = StubExtractor(
        by_content={
            "Ada works at Acme": [relation(source="Ada", target="Acme Corp")],
            "Bob works at Beta": [relation(source="Bob", target="Beta Inc")],
        }
    )
    pipe, graph = pipeline(extractor=extractor, valid_time_extractor=RuleBasedValidTimeExtractor())

    first = episode("Ada works at Acme", "e1", t_valid=100)
    second = episode("Bob works at Beta", "e2", t_valid=200)
    if is_async:
        asyncio.run(pipe.arun(ACME, [first]))
        report = asyncio.run(pipe.arun(ACME, [second]))
    else:
        pipe.run(ACME, [first])
        report = pipe.run(ACME, [second])

    assert report.edges_invalidated == 0
    assert len(graph.rows["acme"]) == 2


@pytest.mark.parametrize("is_async", [False, True])
def test_a_different_relation_for_the_same_pair_does_not_contradict(is_async):
    extractor = StubExtractor(
        by_content={
            "Ada works at Acme": [relation(relation="works_at", target="Acme Corp")],
            "Ada reports to Acme": [relation(relation="reports_to", target="Acme Corp")],
        }
    )
    pipe, graph = pipeline(extractor=extractor, valid_time_extractor=RuleBasedValidTimeExtractor())

    first = episode("Ada works at Acme", "e1", t_valid=100)
    second = episode("Ada reports to Acme", "e2", t_valid=200)
    if is_async:
        asyncio.run(pipe.arun(ACME, [first]))
        report = asyncio.run(pipe.arun(ACME, [second]))
    else:
        pipe.run(ACME, [first])
        report = pipe.run(ACME, [second])

    assert report.edges_invalidated == 0
    assert len(graph.rows["acme"]) == 2


@pytest.mark.parametrize("is_async", [False, True])
def test_contradiction_is_scoped_to_the_facts_own_scope(is_async):
    """A subject+relation match in a different scope must never be invalidated -- ``recall_facts``
    is itself scope-checked, but this pins that the pipeline actually relies on that rather than
    reading across scopes some other way.

    Different objects per scope, not the same one (round-1 review, PR #110): with one shared
    object, the ordinary object-mismatch skip already explains zero invalidations, and a scope
    leak would go unnoticed. Differing objects means only scope filtering stands between this and
    a false invalidation -- if ``recall_facts`` ever leaked BOB's edge into ACME's lookup, the
    object-mismatch condition would find a "contradiction" and this test would catch it.

    Asserted on ``graph.calls``, not on ``graph.facts`` (round-2 review, PR #110): ``graph.facts``
    holds the original, immutable ``FactWrite`` arguments every ``upsert_facts`` call was ever
    given -- ``invalidate`` writes to ``graph.rows``, never back to ``graph.facts`` -- so
    ``acme_edge.record.t_invalid is None`` was true whether or not invalidation ran, and this test
    was not actually exercising the scope guard it claimed to.
    """
    extractor = StubExtractor(
        by_content={
            "Ada works at Acme": [relation(target="Acme Corp")],
            "Ada works at Beta": [relation(target="Beta Inc")],
        }
    )
    pipe, graph = pipeline(extractor=extractor, valid_time_extractor=RuleBasedValidTimeExtractor())

    first = episode("Ada works at Acme", "e1", t_valid=100, scope=ACME)
    second = episode("Ada works at Beta", "e2", t_valid=200, scope=BOB)
    if is_async:
        asyncio.run(pipe.arun(ACME, [first]))
        report = asyncio.run(pipe.arun(BOB, [second]))
    else:
        pipe.run(ACME, [first])
        report = pipe.run(BOB, [second])

    assert report.edges_invalidated == 0
    assert ("ainvalidate" if is_async else "invalidate") not in graph.calls


def test_provenance_is_replaced_not_accumulated_on_a_second_pass():
    """The dominant assertion is the second one below (provenance replacement), not the first
    (id determinism) -- named for it after a review round noted the previous name buried it.

    Id determinism alone proves less than it looks like: ``FakeGraph.facts`` appends on every
    write, so the first assertion only shows both writes carry the *same* id, not that only one
    row exists in a real graph. That dedup claim is proven against a real backend instead -- see
    `TEST_LOG.md`'s Neo4j run for this cookbook."""
    pipe, graph = pipeline()

    pipe.run(ACME, [episode("Ada works at Acme", "e1")])
    pipe.run(ACME, [episode("Ada works at Acme", "e2")])

    assert len({f.record.id for f in graph.facts}) == 1
    # Replaces, not accumulates: the module docstring states `upsert_facts`'s triple leg overwrites
    # an edge's properties on re-write (`SET r = row.props`, no union). The second pass's write
    # carries only its own episode's provenance -- "e1" is not still on it -- and that is correct
    # per this pipeline's documented contract, not a bug: carrying it forward needs a read of the
    # edge's current row, which SM-G7 (#105) / SM-G9 (#107) own.
    assert graph.facts[-1].source_episode_ids == ["e2"]


# ---------------------------------------------------------------------------
# AC 2 -- provenance (source episode) retained on each edge
# ---------------------------------------------------------------------------


def test_provenance_names_the_interaction_the_relation_came_from():
    """One extraction call per episode is what makes ``source_episode_ids`` exact."""
    extractor = StubExtractor(by_content={"first": [relation()], "second": [relation()]})
    pipe, graph = pipeline(extractor=extractor)

    pipe.run(ACME, [episode("first", "e1"), episode("second", "e2")])

    fact = edge_for(graph)
    assert fact.source_episode_ids == ["e1", "e2"]
    assert extractor.seen == ["e1", "e2"], "extraction is per interaction, not per pass"


def test_provenance_is_deduplicated_when_the_same_edge_repeats_within_one_episode():
    extractor = StubExtractor(default=[relation(), relation()])
    pipe, graph = pipeline(extractor=extractor)

    pipe.run(ACME, [episode("x", "e1")])

    fact = edge_for(graph)
    assert fact.source_episode_ids == ["e1"]


@pytest.mark.parametrize("is_async", [False, True])
def test_a_relation_is_filed_under_its_own_episodes_scope_not_the_passs(is_async):
    """A pass scoped to a tenant legitimately scans every user's episodes.

    Filing Bob's relation under the tenant would let Ada read it -- the cross-user leak
    ``_by_scope`` prevents one tier up. Parametrized because ``arun`` calls
    ``graph.aupsert_facts``, a different double method than ``run``'s ``graph.upsert_facts``, and
    this is the only multi-scope coverage of it -- ``test_both_consolidate_halves_write_the_same_edges``
    uses a single scope throughout.
    """
    pipe, graph = pipeline()
    tenant_wide = Scope(tenant_id="acme")
    episodes = [episode("x", "e1", scope=ACME), episode("y", "e2", scope=BOB)]

    if is_async:
        asyncio.run(pipe.arun(tenant_wide, episodes))
    else:
        pipe.run(tenant_wide, episodes)

    filed = {f.record.scope for f in graph.facts}
    assert filed == {ACME, BOB}
    assert tenant_wide not in filed


def test_the_double_refuses_a_relation_written_under_a_foreign_scope():
    """The double must be stricter than the loosest real backend. Nothing in the pipeline can
    reach this -- ``_batches`` writes each fact under its own scope -- which is exactly why it is
    asserted directly against the double the provider tests rest on."""
    graph = FakeGraph()
    foreign = FactWrite(
        record=MemoryRecord(id="x", tier=MemoryTier.SEMANTIC, content="Ada works at Acme", scope=ACME),
        subject="Ada",
        predicate="works_at",
        object="Acme Corp",
    )

    with pytest.raises(ValueError):
        graph.upsert_facts(BOB, [foreign])


# ---------------------------------------------------------------------------
# AC 3 -- runs async, never on the read path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("is_async", [False, True])
def test_a_semantic_record_is_refused_rather_than_extracted_from(is_async):
    """A distilled fact's relations belong to the episodes it came from."""
    pipe, _ = pipeline()
    fact = MemoryRecord(id="f1", tier=MemoryTier.SEMANTIC, content="Acme uses Postgres", scope=ACME)

    with pytest.raises(MemoryOperationError) as excinfo:
        asyncio.run(pipe.arun(ACME, [fact])) if is_async else pipe.run(ACME, [fact])

    assert excinfo.value.field_name == "tier"


def test_a_superseded_episode_is_skipped_without_making_the_pass_incomplete():
    pipe, graph = pipeline()
    retired = episode("gone", "e1", expired_at=100)

    report = pipe.run(ACME, [retired, episode("here", "e2")])

    assert report.episodes_read == 1
    assert graph.facts, "the live episode still produced an edge"
    assert report.details["complete"] is True


def test_the_cap_truncates_and_says_so():
    pipe, _ = pipeline(max_episodes_per_pass=1)

    report = pipe.run(ACME, [episode("a", "e1"), episode("b", "e2")])

    assert report.episodes_read == 1
    assert report.details["complete"] is False


@pytest.mark.parametrize("is_async", [False, True])
def test_an_extractor_failing_mid_pass_writes_nothing(is_async):
    """All-or-nothing: the extractor raising on the second episode must not leave the first
    episode's candidate written -- there is no partial-batch write on this pipeline's own port
    call, so a mid-pass failure propagates before ``upsert_facts``/``aupsert_facts`` runs at all."""

    class RaisesOnSecondCall:
        def __init__(self):
            self.calls = 0

        def extract(self, episode):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("model timeout")
            return [relation()]

        async def aextract(self, episode):
            return self.extract(episode)

    pipe, graph = pipeline(extractor=RaisesOnSecondCall())
    episodes = [episode("first", "e1"), episode("second", "e2")]

    with pytest.raises(RuntimeError):
        asyncio.run(pipe.arun(ACME, episodes)) if is_async else pipe.run(ACME, episodes)

    assert graph.facts == []
    assert graph.calls == [], "the pipeline must not call upsert_facts before every episode is read"


@pytest.mark.parametrize("is_async", [False, True])
def test_a_pass_over_no_episodes_writes_nothing_and_completes(is_async):
    """Parity with ``test_graph_entities.py``'s empty-episodes coverage: a pipeline handed an
    empty list still ran -- it makes no model call and reports a clean, complete pass, which is
    what a caller needs to tell "nothing to extract" apart from "never wired"."""
    pipe, graph = pipeline()

    report = asyncio.run(pipe.arun(ACME, [])) if is_async else pipe.run(ACME, [])

    assert report.episodes_read == 0
    assert report.edges_written == 0
    assert report.details["complete"] is True
    assert graph.facts == []


def test_a_pass_that_reads_no_episode_is_refused_at_construction():
    with pytest.raises(MemoryOperationError) as excinfo:
        pipeline(max_episodes_per_pass=0)

    assert excinfo.value.field_name == "max_episodes_per_pass"


@pytest.mark.parametrize(
    "name, broken", [("graph", object()), ("extractor", object()), ("valid_time_extractor", object())]
)
def test_a_collaborator_missing_either_half_is_refused_at_construction(name, broken):
    with pytest.raises(MemoryOperationError) as excinfo:
        pipeline(**{name: broken})

    assert excinfo.value.field_name == name


def test_an_extractor_returning_none_is_refused_naming_the_extractor():
    """The Protocol contract ("return empty, not None") is documented, not enforced by the type
    system -- a custom extractor violating it must raise a named error, not a bare TypeError from
    iterating None several frames from the call site."""

    class ReturnsNone:
        def extract(self, episode):
            return None

        async def aextract(self, episode):
            return None

    pipe, _ = pipeline(extractor=ReturnsNone())

    with pytest.raises(MemoryOperationError) as excinfo:
        pipe.run(ACME, [episode("x")])

    assert excinfo.value.field_name == "extractor"


def test_the_pipeline_refuses_an_unscoped_pass():
    pipe, _ = pipeline()

    for verb, call in (("run", lambda: pipe.run(None, [])), ("arun", lambda: asyncio.run(pipe.arun(None, [])))):
        with pytest.raises(MemoryScopeRequiredError) as excinfo:
            call()
        assert excinfo.value.verb == verb


# ---------------------------------------------------------------------------
# Merge across episodes within one pass
# ---------------------------------------------------------------------------


def test_two_episodes_naming_the_same_edge_merge_into_one_write():
    extractor = StubExtractor(by_content={"first": [relation(confidence=0.4)], "second": [relation(confidence=0.9)]})
    pipe, graph = pipeline(extractor=extractor)

    report = pipe.run(ACME, [episode("first", "e1"), episode("second", "e2")])

    assert report.candidates == 2
    assert report.edges_written == 1
    fact = edge_for(graph)
    assert fact.confidence == 0.9, "the higher-confidence candidate wins the merge"
    # Order follows the confidence-sorted group (matching `EntityExtractionPipeline._merged`), not
    # episode arrival order -- so this is a set assertion, not a list one.
    assert set(fact.source_episode_ids) == {"e1", "e2"}


def test_two_spellings_of_a_relation_stay_two_edges():
    """Entity resolution is SM-G8's (#106), same boundary as ``entities.py``: two spellings of
    one entity are two different endpoints, so a triple naming either stays a different edge."""
    extractor = StubExtractor(default=[relation(source="Ada"), relation(source="ada")])
    pipe, graph = pipeline(extractor=extractor)

    pipe.run(ACME, [episode("x")])

    assert len(graph.facts) == 2


# ---------------------------------------------------------------------------
# LLMRelationExtractor
# ---------------------------------------------------------------------------


class _Response:
    def __init__(self, parsed=None, content=None):
        self.parsed = parsed
        self.content = content


class _FakeModel:
    """No call counter: ``LLMRelationExtractor`` calls ``deepcopy(self.model)`` per call, so a
    counter here would increment on the copy and never be visible on the reference a test holds --
    silently misleading rather than merely unused."""

    def __init__(self, response):
        self._response = response

    def response(self, messages, response_format):
        return self._response

    async def aresponse(self, messages, response_format):
        await asyncio.sleep(0)
        return self.response(messages, response_format)


def test_llm_extractor_reads_the_parsed_schema():
    model = _FakeModel(_Response(parsed=ExtractedRelations(relations=[relation()])))
    extractor = LLMRelationExtractor(model=model)

    got = extractor.extract(episode("Ada works at Acme"))

    assert [r.source for r in got] == ["Ada"]


def test_llm_extractor_reads_the_parsed_schema_async():
    """``extract``/``aextract`` call ``model.response``/``.aresponse`` respectively -- nothing
    else in this file drives ``aextract`` through ``LLMRelationExtractor`` itself (the pipeline
    tests exercise it only via ``StubExtractor``), so a typo swapping the two verbs would not be
    caught without this."""
    model = _FakeModel(_Response(parsed=ExtractedRelations(relations=[relation()])))
    extractor = LLMRelationExtractor(model=model)

    got = asyncio.run(extractor.aextract(episode("Ada works at Acme")))

    assert [r.source for r in got] == ["Ada"]


def test_llm_extractor_raises_when_no_content_and_parsed_is_a_plain_dict():
    """A provider that returns structured output as a plain ``dict`` rather than a typed
    ``ExtractedRelations`` matches neither ``_parse`` branch and falls through to
    ``response.content`` -- ``None`` here, so this is the "no content" refusal, not the
    unparseable-JSON one. OpenAI's strict JSON mode never produces this shape, so it is otherwise
    reachable only via a future non-OpenAI provider."""
    model = _FakeModel(_Response(parsed={"relations": []}))
    extractor = LLMRelationExtractor(model=model)

    with pytest.raises(MemoryOperationError) as excinfo:
        extractor.extract(episode("x"))

    assert excinfo.value.field_name == "response"


def test_llm_extractor_reads_raw_json_content_when_parsed_is_unset():
    model = _FakeModel(_Response(content=ExtractedRelations(relations=[relation()]).model_dump_json()))
    extractor = LLMRelationExtractor(model=model)

    got = extractor.extract(episode("x"))

    assert [r.target for r in got] == ["Acme Corp"]


def test_llm_extractor_refuses_an_unparseable_answer_rather_than_reporting_empty():
    model = _FakeModel(_Response(content="not json"))
    extractor = LLMRelationExtractor(model=model)

    with pytest.raises(MemoryOperationError) as excinfo:
        extractor.extract(episode("x"))

    assert excinfo.value.field_name == "response"


def test_llm_extractor_refuses_a_missing_model_at_construction():
    with pytest.raises(MemoryOperationError) as excinfo:
        LLMRelationExtractor(model=None)

    assert excinfo.value.field_name == "model"


def test_llm_extractor_appends_instructions_to_the_system_message():
    """Asserted on ``_messages`` directly rather than through ``extract``: the extractor
    deep-copies ``self.model`` per call, so a fake model's own recorded state is invisible to a
    test holding the original reference -- ``_FakeModel``'s docstring above explains why."""
    extractor = LLMRelationExtractor(model=_FakeModel(_Response()), instructions="Prefer 'reports_to' over 'manages'.")

    system_message = extractor._messages(episode("Ada reports to Sarah"))[0]

    assert "## Additional Instructions" in system_message.content
    assert "Prefer 'reports_to' over 'manages'." in system_message.content


def test_llm_extractor_omits_the_instructions_section_when_none_given():
    extractor = LLMRelationExtractor(model=_FakeModel(_Response()))

    system_message = extractor._messages(episode("Ada reports to Sarah"))[0]

    assert "## Additional Instructions" not in system_message.content


# ---------------------------------------------------------------------------
# Provider wiring
# ---------------------------------------------------------------------------


def provider(extractor=None):
    vector, graph = FakeVector(), FakeGraph()
    pipe = None
    if extractor is not None:
        pipe = RelationExtractionPipeline(graph=graph, extractor=extractor)
    return (
        CoLocatedMemoryProvider(
            vector=vector,
            graph=graph,
            config=MemoryProviderConfig(provider="co_located"),
            relation_pipeline=pipe,
        ),
        vector,
        graph,
    )


def test_a_read_never_extracts_relations():
    extractor = StubExtractor(default=[relation()])
    memory, _, _ = provider(extractor=extractor)
    memory.write(ACME, [MemoryEvent(content="Ada works at Acme", scope=ACME)])

    memory.read(ACME, MemoryQuery(text="ada"))
    asyncio.run(memory.aread(ACME, MemoryQuery(text="ada")))

    assert extractor.seen == []


@pytest.mark.parametrize("is_async", [False, True])
def test_consolidate_runs_the_wired_relation_pipeline(is_async):
    memory, _, _ = provider(extractor=StubExtractor(default=[relation()]))
    memory.write(ACME, [MemoryEvent(content="Ada works at Acme", scope=ACME)])

    report = asyncio.run(memory.aconsolidate(ACME)) if is_async else memory.consolidate(ACME)

    assert report.details["relations"] != NO_RELATIONS
    assert isinstance(report.details["relations"], dict)
    assert report.details["relations"]["extractor"] == RELATION_EXTRACTOR_ID


@pytest.mark.parametrize("is_async", [False, True])
def test_an_unwired_provider_reports_relations_as_not_extracted(is_async):
    """``_relations`` and ``_arelations`` each have their own unwired-branch check -- parametrized
    so both are exercised, matching the wired counterpart above."""
    memory, _, _ = provider(extractor=None)
    memory.write(ACME, [MemoryEvent(content="Ada works at Acme", scope=ACME)])

    report = asyncio.run(memory.aconsolidate(ACME)) if is_async else memory.consolidate(ACME)

    assert report.details["relations"] == NO_RELATIONS


def test_both_consolidate_halves_write_the_same_edges():
    """Parity asserted on durable state, not on the two methods existing."""
    outcomes = []
    for is_async in (False, True):
        memory, _, graph = provider(extractor=StubExtractor(default=[relation()]))
        memory.write(ACME, [MemoryEvent(content="Ada works at Acme", scope=ACME, id="e1")])
        if is_async:
            asyncio.run(memory.aconsolidate(ACME))
        else:
            memory.consolidate(ACME)
        outcomes.append({f.record.id: (f.subject, f.predicate, f.object) for f in graph.facts})

    assert outcomes[0] == outcomes[1]
    assert outcomes[0], "both halves must have written something, or this passes vacuously"


def test_a_pipeline_missing_a_half_is_refused_when_the_provider_is_built():
    class SyncOnly:
        def run(self, scope, episodes): ...

    with pytest.raises(MemoryProviderConfigError) as excinfo:
        CoLocatedMemoryProvider(vector=FakeVector(), graph=FakeGraph(), relation_pipeline=SyncOnly())

    assert excinfo.value.field_name == "options.relation_pipeline"


class _BoomPipeline:
    """Raises after being asked, so a test can put a failure *after* the fact write commits."""

    def run(self, scope, episodes):
        raise RuntimeError("model timeout")

    async def arun(self, scope, episodes):
        raise RuntimeError("model timeout")


@pytest.mark.parametrize("is_async", [False, True])
def test_a_relation_extraction_failure_after_commit_is_a_partial_write(is_async):
    """Facts committed before a failing relation pass must be retriable, not silently lost.

    Parametrized because ``consolidate`` and ``aconsolidate`` each wrap the relation-extraction
    call in their own ``try``/``except`` (``co_located.py``'s two loops) -- an unparametrized test
    here only exercises one of the two, matching ``test_graph_entities.py``'s own convention for
    this exact failure shape.
    """
    vector, graph = FakeVector(), FakeGraph()
    memory = CoLocatedMemoryProvider(
        vector=vector,
        graph=graph,
        config=MemoryProviderConfig(provider="co_located"),
        relation_pipeline=_BoomPipeline(),
    )
    memory.write(ACME, [MemoryEvent(content="Ada works at Acme", scope=ACME)])

    with pytest.raises(PartialWriteError) as excinfo:
        asyncio.run(memory.aconsolidate(ACME)) if is_async else memory.consolidate(ACME)

    assert excinfo.value.receipt.accepted >= 1
    assert excinfo.value.receipt.event_ids
