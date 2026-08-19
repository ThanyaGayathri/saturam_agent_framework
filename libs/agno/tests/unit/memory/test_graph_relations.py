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


def test_the_extractor_is_called_once_per_episode_content_not_once_per_relation():
    """Two relations from one episode share one span -- they were dated by the same sentence, so
    extracting per-relation would let two edges from one episode disagree about validity."""
    vt = StubValidTimeExtractor(ValidTimeSpan(t_valid=100, source="ingest_fallback"))
    extractor = StubExtractor(default=[relation(relation="works_at"), relation(relation="reports_to")])
    pipe, graph = pipeline(extractor=extractor, valid_time_extractor=vt)

    pipe.run(ACME, [episode("Ada works at and reports to Acme", episode_id="e1")])

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
