"""Relation/fact edge extraction into typed graph edges (SM-G3 #101).

The three acceptance criteria, each with a test that fails without the code: a typed edge reaches
the graph between two entities per interaction, it carries provenance back to the source episode,
and the pass runs on the consolidation lane and **never** on a read.

Entity resolution is out of scope here exactly as it is in ``test_graph_entities.py``: two
spellings of one entity address two different endpoint nodes, and a test asserting otherwise would
be asserting SM-G8's (#106) work.
"""

import asyncio

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
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import (
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


def test_a_second_pass_upserts_the_same_edge_rather_than_duplicating_it():
    pipe, graph = pipeline()

    pipe.run(ACME, [episode("Ada works at Acme", "e1")])
    pipe.run(ACME, [episode("Ada works at Acme", "e2")])

    assert len({f.record.id for f in graph.facts}) == 1


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


def test_a_relation_is_filed_under_its_own_episodes_scope_not_the_passs():
    """A pass scoped to a tenant legitimately scans every user's episodes.

    Filing Bob's relation under the tenant would let Ada read it -- the cross-user leak
    ``_by_scope`` prevents one tier up.
    """
    pipe, graph = pipeline()
    tenant_wide = Scope(tenant_id="acme")

    pipe.run(tenant_wide, [episode("x", "e1", scope=ACME), episode("y", "e2", scope=BOB)])

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


def test_a_pass_that_reads_no_episode_is_refused_at_construction():
    with pytest.raises(MemoryOperationError) as excinfo:
        pipeline(max_episodes_per_pass=0)

    assert excinfo.value.field_name == "max_episodes_per_pass"


@pytest.mark.parametrize("name, broken", [("graph", object()), ("extractor", object())])
def test_a_collaborator_missing_either_half_is_refused_at_construction(name, broken):
    with pytest.raises(MemoryOperationError) as excinfo:
        pipeline(**{name: broken})

    assert excinfo.value.field_name == name


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
    def __init__(self, response):
        self._response = response
        self.calls = 0

    def response(self, messages, response_format):
        self.calls += 1
        return self._response

    async def aresponse(self, messages, response_format):
        await asyncio.sleep(0)
        return self.response(messages, response_format)


def test_llm_extractor_reads_the_parsed_schema():
    model = _FakeModel(_Response(parsed=ExtractedRelations(relations=[relation()])))
    extractor = LLMRelationExtractor(model=model)

    got = extractor.extract(episode("Ada works at Acme"))

    assert [r.source for r in got] == ["Ada"]


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

    assert report.details["relations"] != "not extracted -- no relation pipeline wired (SM-G3 #101)"
    assert isinstance(report.details["relations"], dict)
    assert report.details["relations"]["extractor"] == RELATION_EXTRACTOR_ID


def test_an_unwired_provider_reports_relations_as_not_extracted():
    memory, _, _ = provider(extractor=None)
    memory.write(ACME, [MemoryEvent(content="Ada works at Acme", scope=ACME)])

    report = memory.consolidate(ACME)

    assert report.details["relations"] == "not extracted -- no relation pipeline wired (SM-G3 #101)"


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


def test_a_relation_extraction_failure_after_commit_is_a_partial_write():
    """Facts committed before a failing relation pass must be retriable, not silently lost."""

    class Boom:
        def run(self, scope, episodes):
            raise RuntimeError("model timeout")

        async def arun(self, scope, episodes):
            raise RuntimeError("model timeout")

    vector, graph = FakeVector(), FakeGraph()
    memory = CoLocatedMemoryProvider(
        vector=vector,
        graph=graph,
        config=MemoryProviderConfig(provider="co_located"),
        relation_pipeline=Boom(),
    )
    memory.write(ACME, [MemoryEvent(content="Ada works at Acme", scope=ACME)])

    with pytest.raises(PartialWriteError) as excinfo:
        memory.consolidate(ACME)

    assert excinfo.value.receipt.accepted >= 1
