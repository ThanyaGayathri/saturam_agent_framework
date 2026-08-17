"""Entity extraction into graph nodes (SM-G2 #100).

The three acceptance criteria, each with a test that fails without the code: entities reach graph
**nodes** per interaction, they arrive **embedded and dedup-ready** for SM-G8 (#106), and the pass
runs on the consolidation lane and **never** on a read.

The boundaries are pinned as well as the behaviour. Two spellings of one entity staying two nodes
is SM-G8's to fix, and a test asserting they merge here would be asserting work this ticket
deliberately did not do -- so ``test_two_spellings_stay_two_candidates`` fails when someone starts
doing it here instead.
"""

import asyncio

import pytest

from egpt.learn.curate import Curator
from egpt.memory.adapters.co_located import CoLocatedMemoryProvider
from egpt.memory.config import MemoryProviderConfig, MemoryProviderConfigError
from egpt.memory.graph.entities import (
    ENTITY_KEYS,
    EXTRACTOR_ID,
    EntityExtractionPipeline,
    EntityExtractor,
    ExtractedEntities,
    ExtractedEntity,
    LLMEntityExtractor,
    embedding_text,
)
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import (
    CONSOLIDATION_KEYS,
    NO_ENTITIES,
    EntityWrite,
    MemoryEvent,
    MemoryOperationError,
    MemoryQuery,
    MemoryRecord,
    MemoryScopeRequiredError,
    Scope,
    normalize_entity_name,
)
from egpt.utils.dttm import now_epoch_s
from tests.unit.memory._fakes import FakeGraph, FakeVector

ACME = Scope(tenant_id="acme", user_id="ada")
BOB = Scope(tenant_id="acme", user_id="bob")


class StubExtractor:
    """Returns whatever it was configured with, keyed on episode content.

    Deliberately not an LLM: what this file tests is the pipeline's decisions -- provenance, scope,
    merging, embedding, refusal -- none of which a model is involved in.
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


class StubEmbedder:
    """A deterministic vector per text, so a test can tell which string was embedded."""

    id = "stub-embedder"

    def __init__(self, dims=(3,)):
        self.dims = list(dims)
        self.texts = []

    def _dim(self):
        return self.dims[min(len(self.texts) - 1, len(self.dims) - 1)]

    def get_embedding(self, text):
        self.texts.append(text)
        return [float(len(text))] * self._dim()

    async def async_get_embedding(self, text):
        await asyncio.sleep(0)
        return self.get_embedding(text)


def entity(name="Acme Corp", entity_type="company", **kwargs):
    return ExtractedEntity(name=name, entity_type=entity_type, **kwargs)


def episode(content, episode_id="e1", scope=ACME, **kwargs):
    return MemoryRecord(id=episode_id, tier=MemoryTier.EPISODIC, content=content, scope=scope, **kwargs)


def pipeline(extractor=None, embedder=None, graph=None, **kwargs):
    graph = graph if graph is not None else FakeGraph()
    return (
        EntityExtractionPipeline(
            graph=graph,
            extractor=extractor if extractor is not None else StubExtractor(default=[entity()]),
            embedder=embedder if embedder is not None else StubEmbedder(),
            **kwargs,
        ),
        graph,
    )


# ---------------------------------------------------------------------------
# AC 1 -- entities extracted per interaction into graph nodes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("is_async", [False, True])
def test_an_entity_reaches_the_graph_as_a_node(is_async):
    """The criterion itself. Without ``upsert_entities`` an entity can only reach the graph as the
    endpoint of a relation, which is SM-G3's (#101) verb and carries no type or embedding."""
    pipe, graph = pipeline()
    call = (
        pipe.arun(ACME, [episode("Acme ships things")]) if is_async else pipe.run(ACME, [episode("Acme ships things")])
    )
    report = asyncio.run(call) if is_async else call

    assert report.entities_written == 1
    (stored,) = list(graph.entities.values())
    assert (stored.name, stored.entity_type) == ("Acme Corp", "company")
    assert stored.derived_by == EXTRACTOR_ID
    assert graph.calls.count("aupsert_entities" if is_async else "upsert_entities") == 1


def test_provenance_names_the_interaction_the_entity_came_from():
    """One extraction call per episode is what makes ``source_episode_ids`` exact.

    A batched extractor would have to attribute each entity back itself, and SM-R7 section 7 makes
    that trail load-bearing -- it is what a fact's audit follows.
    """
    extractor = StubExtractor(by_content={"first": [entity()], "second": [entity()]})
    pipe, graph = pipeline(extractor=extractor)

    pipe.run(ACME, [episode("first", "e1"), episode("second", "e2")])

    (stored,) = list(graph.entities.values())
    assert stored.source_episode_ids == ["e1", "e2"]
    assert extractor.seen == ["e1", "e2"], "extraction is per interaction, not per pass"


def test_an_entity_is_filed_under_its_own_episodes_scope_not_the_passs():
    """A pass scoped to a tenant legitimately scans every user's episodes.

    Filing Bob's entity under the tenant would let Ada read it -- the cross-user leak
    ``_by_scope`` prevents one tier up for facts.
    """
    pipe, graph = pipeline()
    tenant_wide = Scope(tenant_id="acme")

    pipe.run(tenant_wide, [episode("x", "e1", scope=ACME), episode("y", "e2", scope=BOB)])

    filed = {key[0] for key in graph.entities}
    assert filed == {ACME.key(), BOB.key()}
    assert tenant_wide.key() not in filed


def test_a_semantic_record_is_refused_rather_than_extracted_from():
    """A distilled fact's entities belong to the episodes it came from.

    Extracting from it again would attribute them to a derived row, so the provenance would name
    something that is not an interaction.
    """
    pipe, _ = pipeline()
    fact = MemoryRecord(id="f1", tier=MemoryTier.SEMANTIC, content="Acme uses Postgres", scope=ACME)

    with pytest.raises(MemoryOperationError) as excinfo:
        pipe.run(ACME, [fact])

    assert excinfo.value.field_name == "tier"


def test_a_superseded_episode_is_skipped_without_making_the_pass_incomplete():
    """Its entities belong to its live revision, and reporting the pass incomplete for one would
    tell a scheduler to re-run a pass with nothing left to do."""
    pipe, graph = pipeline()
    retired = episode("gone", "e1", expired_at=100)

    report = pipe.run(ACME, [retired, episode("here", "e2")])

    assert report.episodes_read == 1
    assert graph.entities, "the live episode still produced a node"
    assert report.details["complete"] is True


def test_the_cap_truncates_and_says_so():
    """Each episode is one model call, so an uncapped pass over a drained tier is a bill.

    ``complete=False`` is the only signal a scheduler has that a tail is waiting -- nothing marks
    an episode consumed until SM-L1 (#122) brings a cursor.
    """
    pipe, _ = pipeline(max_episodes_per_pass=1)

    report = pipe.run(ACME, [episode("a", "e1"), episode("b", "e2")])

    assert report.episodes_read == 1
    assert report.details["complete"] is False


def test_a_pass_that_reads_no_episode_is_refused_at_construction():
    """Zero would report a clean run having done nothing, which is the silently-empty class."""
    with pytest.raises(MemoryOperationError) as excinfo:
        pipeline(max_episodes_per_pass=0)

    assert excinfo.value.field_name == "max_episodes_per_pass"


@pytest.mark.parametrize(
    "name, broken",
    [
        ("graph", object()),
        ("extractor", object()),
        ("embedder", object()),
    ],
)
def test_a_collaborator_missing_either_half_is_refused_at_construction(name, broken):
    """Both halves, checked by one loop, so a collaborator added later cannot be the unchecked one.

    A pipeline whose async twin is absent blocks the consolidation lane's event loop on the first
    pass -- a failure that surfaces as latency, not as an error.
    """
    with pytest.raises(MemoryOperationError) as excinfo:
        pipeline(**{name: broken})

    assert excinfo.value.field_name == name


def test_the_pipeline_refuses_an_unscoped_pass():
    """``require_scope`` on both verbs: the pipeline writes, and an unscoped write is tenant-wide."""
    pipe, _ = pipeline()

    for verb, call in (("run", lambda: pipe.run(None, [])), ("arun", lambda: asyncio.run(pipe.arun(None, [])))):
        with pytest.raises(MemoryScopeRequiredError) as excinfo:
            call()
        assert excinfo.value.verb == verb


# ---------------------------------------------------------------------------
# AC 2 -- embedded and dedup-ready
# ---------------------------------------------------------------------------


def test_every_written_entity_carries_a_vector_and_what_produced_it():
    """ "Dedup-ready" is what SM-G8 (#106) matches over, and an unembedded candidate is not."""
    pipe, graph = pipeline()

    pipe.run(ACME, [episode("x")])

    (stored,) = list(graph.entities.values())
    assert stored.embedding == [float(len(embedding_text(stored)))] * 3
    assert stored.embedding_version == "stub-embedder/d3"


def test_the_embedded_text_is_more_than_the_name():
    """A bare name gives SM-G8 "atlas" the project and "atlas" the person at cosine 1.0."""
    embedder = StubEmbedder()
    pipe, _ = pipeline(
        extractor=StubExtractor(default=[entity(name="Atlas", entity_type="project", description="the migration")]),
        embedder=embedder,
    )

    pipe.run(ACME, [episode("x")])

    assert embedder.texts == ["Atlas | project | the migration"]


def test_the_key_is_the_normalised_name_and_is_derived_not_accepted():
    """A caller-chosen grouping key is a caller-chosen duplicate."""
    write = EntityWrite(name="  Acme Corp.  ", entity_type="company", scope=ACME)

    assert write.name == "Acme Corp."
    assert write.key == "acme corp"


def test_the_two_set_valued_fields_are_sorted_and_deduplicated():
    """Both are sets by meaning, and an extractor's ordering is whatever the model emitted.

    Left as-is, two identical candidates compare unequal and rewrite the node for nothing -- and
    the graph's own union then stores the duplicate the comprehension was supposed to drop.
    """
    write = EntityWrite(
        name="Acme",
        entity_type="company",
        scope=ACME,
        aliases=["zeta", "  alpha  ", "zeta", "", "   "],
        source_episode_ids=["e2", "e1", "e2"],
    )

    assert write.aliases == ["alpha", "zeta"]
    assert write.source_episode_ids == ["e1", "e2"]


def test_the_double_refuses_an_entity_written_under_a_foreign_scope():
    """The double must be **stricter than the loosest real backend**, and the Neo4j one refuses.

    Nothing in the pipeline can reach this -- ``_batches`` writes each entity under its own scope --
    which is exactly why it is asserted directly: an unexercised guard in the double is a guard
    that quietly stops holding, and every provider test here rests on the double.
    """
    graph = FakeGraph()

    with pytest.raises(ValueError, match="Batch per scope"):
        graph.upsert_entities(ACME, [EntityWrite(name="Acme", entity_type="company", scope=BOB)])


def test_two_spellings_stay_two_candidates():
    """The SM-G8 (#106) boundary, pinned so it is noticed when it moves.

    "Acme" and "Acme Corporation" share no normalised key, so nothing here can merge them -- and
    the graph merges on the surface name, matching the endpoint nodes ``upsert_facts`` creates.
    Making them one node is entity resolution, and doing it on the write path would decide that
    policy where nothing can measure it.
    """
    pipe, graph = pipeline(extractor=StubExtractor(default=[entity(name="Acme"), entity(name="Acme Corporation")]))

    pipe.run(ACME, [episode("x")])

    assert {key[1] for key in graph.entities} == {"Acme", "Acme Corporation"}


def test_one_spelling_seen_twice_is_one_node_with_both_episodes():
    """The half this ticket *does* own: exact-match candidates fold into one write."""
    pipe, graph = pipeline(
        extractor=StubExtractor(by_content={"a": [entity(aliases=["ACME"])], "b": [entity(aliases=["acme"])]})
    )

    report = pipe.run(ACME, [episode("a", "e1"), episode("b", "e2")])

    assert (report.candidates, report.entities_written) == (2, 1)
    (stored,) = list(graph.entities.values())
    assert stored.source_episode_ids == ["e1", "e2"]
    assert stored.aliases == ["ACME", "acme"]


def test_a_second_pass_adds_to_the_provenance_the_first_one_wrote():
    """The union has to hold **across** passes, not only within one.

    Within a pass ``_merged`` folds the mentions before the write, so a store that replaced
    provenance would still look right. It is the next pass -- a scheduler re-running the lane over
    newer episodes -- that erases the first one's trail, and the double has to model that or no
    provider test can see it.
    """
    graph = FakeGraph()
    pipe, _ = pipeline(graph=graph, extractor=StubExtractor(default=[entity(aliases=["Acme"])]))

    pipe.run(ACME, [episode("first", "e1")])
    pipe.run(ACME, [episode("second", "e2")])

    (stored,) = list(graph.entities.values())
    assert stored.source_episode_ids == ["e1", "e2"]
    assert stored.aliases == ["Acme"], "a re-sighting must not duplicate an alias it already had"


def test_the_head_of_a_merged_group_is_the_most_confident():
    """One corpus produces one description, not a different one per run.

    Something has to decide which mention's description the node keeps. Insertion order is the
    tempting answer and is scan order, which is driven by uuid ids -- so the same episodes would
    describe the entity differently on each pass.
    """
    pipe, graph = pipeline(
        extractor=StubExtractor(
            by_content={
                "a": [entity(description="a guess", confidence=0.2)],
                "b": [entity(description="what it actually is", confidence=0.9)],
            }
        )
    )

    pipe.run(ACME, [episode("a", "e1"), episode("b", "e2")])

    (stored,) = list(graph.entities.values())
    assert stored.description == "what it actually is"
    assert stored.confidence == 0.9


def test_entity_normalization_matches_the_curator():
    """One rule for one concept. SM-G8 (#106) is assigned to reuse ``Curator._normalize``, so a
    second rule here is how two entities that should have merged stop merging."""
    curator = Curator.__new__(Curator)
    corpus = ["Acme Corp.", "  ACME   corp ", "Project-Atlas", "n8n!!", "Ada's Team", "MULTI   space"]

    for text in corpus:
        assert normalize_entity_name(text) == curator._normalize(text), text


def test_a_mixed_dimension_pass_is_refused():
    """Two dimensions in one column family cannot be searched together, and left alone the failure
    surfaces at SM-G8's match rather than at the write that caused it."""
    pipe, _ = pipeline(
        extractor=StubExtractor(default=[entity(name="One"), entity(name="Two")]),
        embedder=StubEmbedder(dims=(3, 4)),
    )

    with pytest.raises(MemoryOperationError) as excinfo:
        pipe.run(ACME, [episode("x")])

    assert excinfo.value.field_name == "embedding"


@pytest.mark.parametrize(
    "field_name, kwargs",
    [
        ("name", {"name": "   "}),
        ("name", {"name": "!!!"}),
        ("entity_type", {"entity_type": ""}),
        ("scope", {"scope": "acme"}),
        ("confidence", {"confidence": 1.5}),
        ("confidence", {"confidence": True}),
        ("embedding", {"embedding": []}),
        ("embedding", {"embedding": ["0.1"]}),
    ],
)
def test_an_entity_write_refuses_what_it_cannot_store(field_name, kwargs):
    """Asserted on the narrow class **and** the field it carries: ``pytest.raises(ValueError)``
    would pass whether or not the envelope's own error was raised."""
    fields = {"name": "Acme", "entity_type": "company", "scope": ACME, **kwargs}

    with pytest.raises(MemoryOperationError) as excinfo:
        EntityWrite(**fields)

    assert excinfo.value.field_name == field_name


def test_with_embedding_validates_rather_than_assigning():
    """Assigning the field directly skips every check, so an embedder returning strings stores a
    vector no similarity can read."""
    write = EntityWrite(name="Acme", entity_type="company", scope=ACME)

    assert write.with_embedding([0.1, 0.2], "v1").embedding == [0.1, 0.2]
    with pytest.raises(MemoryOperationError) as excinfo:
        write.with_embedding(["nope"], "v1")
    assert excinfo.value.field_name == "embedding"


# ---------------------------------------------------------------------------
# AC 3 -- runs on the async lane, never on the read path
# ---------------------------------------------------------------------------


def provider(extractor=None, embedder=None):
    vector, graph = FakeVector(), FakeGraph()
    pipe = None
    if extractor is not None:
        pipe = EntityExtractionPipeline(
            graph=graph, extractor=extractor, embedder=embedder if embedder is not None else StubEmbedder()
        )
    return (
        CoLocatedMemoryProvider(
            vector=vector,
            graph=graph,
            config=MemoryProviderConfig(provider="co_located"),
            entity_pipeline=pipe,
        ),
        vector,
        graph,
    )


def test_a_read_never_extracts():
    """The criterion: extraction is an LLM pass, and a read path that ran one would pay a model
    call per query. Asserted on what the extractor was *asked*, because a result-only assertion
    cannot see a call that returned nothing."""
    extractor = StubExtractor(default=[entity()])
    memory, _, _ = provider(extractor=extractor)
    memory.write(ACME, [MemoryEvent(content="Acme ships things", scope=ACME)])

    memory.read(ACME, MemoryQuery(text="acme"))
    asyncio.run(memory.aread(ACME, MemoryQuery(text="acme")))

    assert extractor.seen == []


@pytest.mark.parametrize("is_async", [False, True])
def test_consolidate_runs_the_pass_and_reports_it(is_async):
    extractor = StubExtractor(default=[entity()])
    memory, _, graph = provider(extractor=extractor)
    memory.write(ACME, [MemoryEvent(content="Acme ships things", scope=ACME)])

    report = asyncio.run(memory.aconsolidate(ACME)) if is_async else memory.consolidate(ACME)

    assert extractor.seen, "the consolidation lane is where extraction runs"
    assert graph.entities
    assert report.details["entities"]["extractor"] == EXTRACTOR_ID
    assert set(report.details["entities"]) >= set(ENTITY_KEYS)


def test_consolidate_extracts_from_what_it_kept_rather_than_from_what_it_scanned():
    """``_visible_episodes`` drops episodes that are not valid at the pass instant.

    Extraction runs over the survivors. Handing it the raw scan instead would put nodes in the
    graph sourced from a row no read returns -- and ``_selected``'s own current-belief check cannot
    see it, because a future-dated episode is current belief and simply not true yet.
    """
    extractor = StubExtractor(default=[entity()])
    memory, vector, _ = provider(extractor=extractor)
    later = now_epoch_s() + 3600
    vector.upsert(
        ACME,
        [MemoryRecord(id="e1", tier=MemoryTier.EPISODIC, content="Acme will ship", scope=ACME, t_valid=later)],
    )

    report = memory.consolidate(ACME)

    assert report.episodic_scanned == 1
    assert extractor.seen == [], "an episode not yet valid is not an interaction to extract from"


def test_a_provider_with_no_pipeline_says_so_rather_than_omitting_the_key():
    """The diagnostics floor. Without the key a scheduler cannot tell "extracted nothing" from
    "never ran", and only one of those needs a pipeline wiring."""
    memory, _, _ = provider()
    memory.write(ACME, [MemoryEvent(content="Acme ships things", scope=ACME)])

    # Named literally as well as through the tuple: `set(details) >= set(KEYS)` still passes when
    # the key is dropped from the tuple *and* from the payload together, which is the way a floor
    # is usually lowered.
    assert "entities" in CONSOLIDATION_KEYS
    for report in (memory.consolidate(ACME), asyncio.run(memory.aconsolidate(ACME))):
        assert set(report.details) >= set(CONSOLIDATION_KEYS)
        assert report.details["entities"] == NO_ENTITIES


def test_both_consolidate_halves_write_the_same_nodes():
    """Parity asserted on durable state, not on the two methods existing."""
    outcomes = []
    for is_async in (False, True):
        memory, _, graph = provider(extractor=StubExtractor(default=[entity()]))
        memory.write(ACME, [MemoryEvent(content="Acme ships things", scope=ACME, id="e1")])
        if is_async:
            asyncio.run(memory.aconsolidate(ACME))
        else:
            memory.consolidate(ACME)
        outcomes.append({key: (value.entity_type, value.source_episode_ids) for key, value in graph.entities.items()})

    assert outcomes[0] == outcomes[1]
    assert outcomes[0], "both halves must have written something, or this passes vacuously"


def test_a_pipeline_missing_a_half_is_refused_when_the_provider_is_built():
    """``consolidate`` and ``aconsolidate`` each drive one verb, so a sync-only pipeline is a
    latency bug on the async lane rather than an error."""

    class SyncOnly:
        def run(self, scope, episodes): ...

    with pytest.raises(MemoryProviderConfigError) as excinfo:
        CoLocatedMemoryProvider(vector=FakeVector(), graph=FakeGraph(), entity_pipeline=SyncOnly())

    assert excinfo.value.field_name == "options.entity_pipeline"


# ---------------------------------------------------------------------------
# The default extractor's answer handling
# ---------------------------------------------------------------------------


class _Answer:
    def __init__(self, parsed=None, content=None):
        self.parsed = parsed
        self.content = content


@pytest.mark.parametrize(
    "answer",
    [
        _Answer(parsed=ExtractedEntities(entities=[entity()])),
        _Answer(content='{"entities": [{"name": "Acme Corp", "entity_type": "company"}]}'),
        _Answer(parsed='{"entities": [{"name": "Acme Corp", "entity_type": "company"}]}'),
    ],
)
def test_the_default_extractor_reads_every_shape_a_provider_answers_in(answer):
    """A provider may fill ``parsed`` with the model, with the raw JSON, or neither."""
    (extracted,) = LLMEntityExtractor._parse(answer, "e1")

    assert (extracted.name, extracted.entity_type) == ("Acme Corp", "company")


@pytest.mark.parametrize("answer", [_Answer(content="I found Acme Corp."), _Answer()])
def test_an_unreadable_answer_is_refused_rather_than_read_as_no_entities(answer):
    """A model answering in prose is not an episode with no entities, and reporting the two the
    same way makes a broken deployment look like a quiet corpus."""
    with pytest.raises(MemoryOperationError) as excinfo:
        LLMEntityExtractor._parse(answer, "e1")

    assert excinfo.value.field_name == "response"


def test_the_default_extractor_needs_a_model():
    with pytest.raises(MemoryOperationError) as excinfo:
        LLMEntityExtractor(model=None)

    assert excinfo.value.field_name == "model"


def test_the_default_extractor_reuses_the_seed_prompt():
    """One text for one job. Two would drift, and an entity the learn store keeps while the graph
    does not is a node missing from every multi-hop path."""
    from egpt.learn.stores.entity_memory import ENTITY_EXTRACTION_INSTRUCTIONS

    extractor = LLMEntityExtractor(model=object(), instructions="Prefer proper nouns.")
    system, user = extractor._messages(episode("Acme ships things"))

    assert ENTITY_EXTRACTION_INSTRUCTIONS in system.content
    assert "Prefer proper nouns." in system.content
    assert "Acme ships things" in user.content


def test_the_stubs_satisfy_the_extractor_port():
    """Non-vacuity: every behavioural test above drives a double, so a double that did not satisfy
    the port would be testing a shape no backend has to implement."""
    assert isinstance(StubExtractor(), EntityExtractor)
    assert isinstance(LLMEntityExtractor(model=object()), EntityExtractor)
