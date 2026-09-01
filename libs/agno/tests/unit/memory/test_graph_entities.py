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
import inspect

import pytest

from egpt.learn.curate import Curator
from egpt.memory.adapters.co_located import CoLocatedMemoryProvider
from egpt.memory.config import MemoryProviderConfig, MemoryProviderConfigError
from egpt.memory.graph.entities import (
    ENTITY_EXTRACTOR_ID,
    ENTITY_KEYS,
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
    PartialWriteError,
    Scope,
    consolidation_details,
    entity_extraction_complete,
    entity_extraction_ran,
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


class ApiFailingUsageEmbedder(StubEmbedder):
    """Declares ``get_embedding_and_usage`` but it fails the way a real API call fails -- a bare
    exception with no try/except of its own, matching real embedders' ``get_embedding_and_usage``
    (unlike their paired ``get_embedding``, which does swallow a failure). ``run``/``arun`` must
    still complete by falling back to the plain call, not surface the raw exception (major finding,
    PR #159 review)."""

    def get_embedding_and_usage(self, text):
        raise ConnectionError("simulated API failure")

    async def async_get_embedding_and_usage(self, text):
        await asyncio.sleep(0)
        raise ConnectionError("simulated API failure")


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
    assert stored.derived_by == ENTITY_EXTRACTOR_ID
    assert graph.calls.count("aupsert_entities" if is_async else "upsert_entities") == 1


@pytest.mark.parametrize("is_async", [False, True])
def test_a_metered_embedding_api_failure_still_lands_the_entity(is_async):
    """Major finding, PR #159 review: `_metered_embedding`/`_ametered_embedding` only caught
    `NotImplementedError`, so a real API failure on the metered call (no try/except of its own,
    unlike `get_embedding`) used to propagate raw instead of falling back -- crashing extraction
    for an embedder failure the non-metered path would have absorbed."""
    pipe, graph = pipeline(embedder=ApiFailingUsageEmbedder())
    call = (
        pipe.arun(ACME, [episode("Acme ships things")]) if is_async else pipe.run(ACME, [episode("Acme ships things")])
    )
    report = asyncio.run(call) if is_async else call

    assert report.entities_written == 1
    (stored,) = list(graph.entities.values())
    assert stored.embedding is not None


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


@pytest.mark.parametrize("is_async", [False, True])
def test_a_semantic_record_is_refused_rather_than_extracted_from(is_async):
    """A distilled fact's entities belong to the episodes it came from.

    Extracting from it again would attribute them to a derived row, so the provenance would name
    something that is not an interaction.

    Parametrized to match this file's convention (review finding on PR #89, round 2): the guard
    lives in ``_selected``, which both ``run`` and ``arun`` call identically, but an unparametrized
    test here reads as an intentional gap in a file that otherwise covers both halves everywhere.
    """
    pipe, _ = pipeline()
    fact = MemoryRecord(id="f1", tier=MemoryTier.SEMANTIC, content="Acme uses Postgres", scope=ACME)

    with pytest.raises(MemoryOperationError) as excinfo:
        asyncio.run(pipe.arun(ACME, [fact])) if is_async else pipe.run(ACME, [fact])

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


def test_an_extractor_returning_none_is_refused_naming_the_extractor():
    """The Protocol contract ("return empty, not None") is documented, not enforced by the type
    system -- a custom extractor violating it must raise a named error, not a bare TypeError from
    iterating None several frames from the call site. Mirrors
    ``RelationExtractionPipeline``'s guard (SM-G3)."""

    class ReturnsNone:
        def extract(self, episode):
            return None

        async def aextract(self, episode):
            return None

    pipe, _ = pipeline(extractor=ReturnsNone())

    with pytest.raises(MemoryOperationError) as excinfo:
        pipe.run(ACME, [episode("x")])

    assert excinfo.value.field_name == "extractor"


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


def test_the_entity_write_filters_its_own_name_out_of_aliases_at_construction():
    """B-6.1 (review finding on PR #89, round 2): the filter used to live only at write time
    (``Neo4jGraphSubstrate._entity_rows``, ``FakeGraph._upsert_entity``), so code reading
    ``EntityWrite.aliases`` between construction and write -- ``_merged``'s concatenation in
    ``memory/graph/entities.py`` -- saw the entity's own name as an alias of itself."""
    write = EntityWrite(name="Acme", entity_type="company", scope=ACME, aliases=["Acme", "Acme Corp", "acme"])

    assert write.aliases == ["Acme Corp", "acme"], "the exact-case own name is filtered; 'acme' is not"


def test_the_double_refuses_an_entity_written_under_a_foreign_scope():
    """The double must be **stricter than the loosest real backend**, and the Neo4j one refuses.

    Nothing in the pipeline can reach this -- ``_batches`` writes each entity under its own scope --
    which is exactly why it is asserted directly: an unexercised guard in the double is a guard
    that quietly stops holding, and every provider test here rests on the double.

    Asserted on ``MemoryOperationError``, not the base ``ValueError`` it subclasses (review finding
    on PR #89): the real ``Neo4jGraphSubstrate._reject_foreign_scope`` raises the narrow type, and
    ``pytest.raises(ValueError)`` would have passed whether or not the double matched it.
    """
    graph = FakeGraph()

    with pytest.raises(MemoryOperationError, match="Batch per scope") as excinfo:
        graph.upsert_entities(ACME, [EntityWrite(name="Acme", entity_type="company", scope=BOB)])
    assert excinfo.value.field_name == "scope"


def test_the_double_does_not_store_an_entitys_own_name_as_its_alias():
    """Parity with ``Neo4jGraphSubstrate._entity_rows`` (review finding on PR #89): a test asserting
    the entity's name is absent from its stored aliases would pass on the real backend and fail on
    the double, since only the real backend filtered it before this fix."""
    graph = FakeGraph()

    graph.upsert_entities(
        ACME, [EntityWrite(name="Acme Corp", entity_type="company", scope=ACME, aliases=["Acme Corp", "Acme"])]
    )

    assert graph.entities[(ACME.key(), "Acme Corp")].aliases == ["Acme"]


def test_the_double_records_exactly_one_verb_per_async_entity_write():
    """Review finding on PR #89, round 3: ``aupsert_entities`` used to delegate to
    ``self.upsert_entities``, which appends its own name too -- one async call left both
    ``"aupsert_entities"`` and ``"upsert_entities"`` in ``self.calls``, unlike every other verb's
    single-name-per-call contract.
    """
    graph = FakeGraph()

    asyncio.run(graph.aupsert_entities(ACME, [EntityWrite(name="Acme", entity_type="company", scope=ACME)]))

    assert graph.calls == ["aupsert_entities"]


def test_the_doubles_two_entity_halves_agree():
    """Sync/async parity on the double itself, not only against the real backend: both verbs must
    leave identical stored state and identical batch-size bookkeping."""
    sync_graph, async_graph = FakeGraph(), FakeGraph()
    entity = EntityWrite(name="Acme", entity_type="company", scope=ACME, source_episode_ids=["e1"])

    sync_graph.upsert_entities(ACME, [entity])
    asyncio.run(async_graph.aupsert_entities(ACME, [entity]))

    assert sync_graph.entities == async_graph.entities
    assert sync_graph.entity_batch_sizes == async_graph.entity_batch_sizes == [1]


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


def test_entity_normalization_additionally_strips_a_punctuation_stripped_edge():
    """The one point where the two diverge, made explicit rather than left for the corpus above to
    miss (review finding on PR #89: the prior corpus did not cover a leading/trailing-punctuation
    input, so the "matches the Curator" claim was weaker than it read).

    ``Curator._normalize`` drops interior punctuation but never re-strips the result, so
    ``"! hello !"`` -- punctuation-stripped to ``" hello "`` -- keeps its surrounding spaces.
    ``normalize_entity_name`` strips again, because this is a merge key and a key differing only in
    whitespace is a key SM-G8 (#106) would have to resolve rather than one that never split.
    """
    curator = Curator.__new__(Curator)
    text = "! hello !"

    assert curator._normalize(text) == " hello "
    assert normalize_entity_name(text) == "hello"


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
    assert report.details["entities"]["extractor"] == ENTITY_EXTRACTOR_ID
    assert set(report.details["entities"]) >= set(ENTITY_KEYS)


class BoomExtractor:
    """Raises after being asked, so a test can put a failure *after* the fact write commits."""

    def extract(self, episode):
        raise RuntimeError("extraction blew up")

    async def aextract(self, episode):
        raise RuntimeError("extraction blew up")


@pytest.mark.parametrize("is_async", [False, True])
def test_an_extraction_failure_after_facts_commit_is_a_partial_write(is_async):
    """Review finding (PR #89): extraction ran outside the fact-write ``try``, so a failure here
    propagated as a bare exception with no ``receipt`` -- breaking ``PartialWriteError``'s retry
    contract exactly where a caller most needs it, since the fact write already landed.
    """
    memory, _, graph = provider(extractor=BoomExtractor())
    memory.write(ACME, [MemoryEvent(content="Acme ships things", scope=ACME, id="e1")])

    with pytest.raises(PartialWriteError) as excinfo:
        asyncio.run(memory.aconsolidate(ACME)) if is_async else memory.consolidate(ACME)

    # The fact write is what makes this a *partial* write rather than a plain refusal: something
    # is already committed, so the caller needs the receipt's ids to retry safely.
    assert excinfo.value.receipt.accepted >= 1
    assert excinfo.value.receipt.event_ids


def test_an_extraction_failure_on_an_empty_tier_never_reaches_the_extractor():
    """In this scenario (empty tier scan), ``episodes`` is empty, so ``_selected`` never iterates
    and the extractor is never called -- so an empty-tier ``consolidate`` cannot actually reach a
    failing extractor. Asserted on the extractor never being called (a call would raise and fail
    this test), so this stays true regardless of how ``_entities`` decides to run the pipeline.

    ``_entities`` still runs the pipeline here (review finding on PR #89, round 3: it no longer
    short-circuits on empty ``episodes``, only on an unwired pipeline), so the report is the
    pipeline's own dict, not the ``NO_ENTITIES`` sentinel -- that sentinel means "never configured",
    which is not this case.
    """
    extractor = BoomExtractor()
    memory, _, _ = provider(extractor=extractor)

    report = memory.consolidate(ACME)  # nothing written; must not raise

    assert isinstance(report.details["entities"], dict)
    assert report.details["entities"]["complete"] is True


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
    # The pipeline still ran (on zero episodes) -- a wired pipeline handed nothing to extract from
    # is not "never configured", so the report is the pipeline's dict, not NO_ENTITIES.
    assert isinstance(report.details["entities"], dict)


@pytest.mark.parametrize("is_async", [False, True])
def test_consolidate_extracts_from_the_kept_set_even_when_a_fact_is_written(is_async):
    """The same property as above, on the path the fact write actually takes.

    The prior test's scenario distils no fact -- ``episodes`` and ``scanned`` diverge, but
    extraction runs on the branch that never wrote anything, so it could not tell a raw-scan
    regression from a correct one. Here a distillable episode makes ``facts`` non-empty, so
    extraction runs on the branch guarded by the ``PartialWriteError`` fix, which is the one a
    raw-scan regression would actually reach.
    """
    extractor = StubExtractor(default=[entity()])
    memory, vector, _ = provider(extractor=extractor)
    later = now_epoch_s() + 3600
    vector.upsert(
        ACME,
        [
            MemoryRecord(id="future", tier=MemoryTier.EPISODIC, content="not yet true", scope=ACME, t_valid=later),
            MemoryRecord(id="now", tier=MemoryTier.EPISODIC, content="Acme ships things", scope=ACME),
        ],
    )

    (asyncio.run(memory.aconsolidate(ACME)) if is_async else memory.consolidate(ACME))

    assert extractor.seen == ["now"], "the not-yet-valid episode must not reach the extractor"


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


@pytest.mark.parametrize("is_async", [False, True])
def test_a_wired_pipeline_with_nothing_to_extract_is_not_no_entities(is_async):
    """The two cases ``NO_ENTITIES`` used to collapse (review finding on PR #89, round 3):

    * no pipeline configured -- wiring one would enable extraction
    * a pipeline **is** wired, but this pass's episode set is empty -- a scheduler retrying later
      (once an episode exists to extract from) is exactly the right move, and it cannot tell that
      apart from "never configured" if both read as the same sentinel string.

    Nothing is written, so the tier ``consolidate`` scans is empty and ``episodes`` is ``[]`` --
    the case the fix is about.
    """
    memory, _, _ = provider(extractor=StubExtractor())

    report = asyncio.run(memory.aconsolidate(ACME)) if is_async else memory.consolidate(ACME)

    assert report.details["entities"] != NO_ENTITIES
    assert isinstance(report.details["entities"], dict)


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


def test_entity_write_reaches_the_top_level_package():
    """``EntityWrite``, ``NO_ENTITIES`` and ``normalize_entity_name`` are peers of ``FactWrite``,
    ``NO_SUPERSEDE`` and ``CONSOLIDATION_KEYS``, all re-exported from ``egpt.memory`` (review
    finding on PR #89, round 2). Missing them breaks the import pattern every other symbol in
    ``egpt.memory.types.__all__`` follows here.
    """
    from egpt.memory import NO_ENTITIES as TopLevelNoEntities
    from egpt.memory import EntityWrite as TopLevelEntityWrite
    from egpt.memory import normalize_entity_name as top_level_normalize

    assert TopLevelEntityWrite is EntityWrite
    assert TopLevelNoEntities is NO_ENTITIES
    assert top_level_normalize is normalize_entity_name


# ---------------------------------------------------------------------------
# The two-cap discriminant (review finding on PR #89, round 3)
# ---------------------------------------------------------------------------


def test_entity_extraction_ran_tells_unwired_from_wired_but_empty():
    """The discriminant the ``NO_ENTITIES``-collapse fix above needs a public name for."""
    assert entity_extraction_ran(consolidation_details(derived_by=None, entities=NO_ENTITIES)) is False
    assert entity_extraction_ran(consolidation_details(derived_by="x", entities={"complete": True})) is True


def test_entity_extraction_complete_is_true_when_no_pipeline_is_wired():
    """No pipeline means no tail to be incomplete -- a wiring fact, not a retry signal."""
    details = consolidation_details(derived_by=None, entities=NO_ENTITIES)

    assert entity_extraction_complete(details) is True


def test_entity_extraction_complete_sees_the_two_cap_hazard():
    """The scenario the finding named directly: the provider's own episode cap (5000 default) was
    not hit, so the top-level ``ConsolidationReport.details["complete"]`` is ``True`` -- but the
    pipeline's independent, much lower cap (200 default) was, so a caller reading only the
    top-level key would report a clean pass with entities silently left unextracted.
    """
    details = consolidation_details(
        derived_by="co_located.deterministic_dedup", complete=True, entities={"complete": False}
    )

    assert details["complete"] is True  # the trap: this alone looks clean
    assert entity_extraction_ran(details) is True
    assert entity_extraction_complete(details) is False


def test_entity_extraction_complete_is_true_when_the_pipeline_drained_its_own_cap():
    details = consolidation_details(derived_by="x", entities={"complete": True})

    assert entity_extraction_complete(details) is True


# ---------------------------------------------------------------------------
# Boundary: an entity's embedding is indexed on the graph node, never copied
# into the VectorSubstrate (SM-X9 #145)
# ---------------------------------------------------------------------------


def test_the_pipeline_has_no_vector_substrate_collaborator():
    """Cheap structural second line of defence for the boundary the test below exercises.

    Pinned on the constructor signature so a future dual-write is a deliberate edit to this list,
    rather than a fourth optional argument the "one loop over all three" collaborator-check above
    would let through. ``RelationExtractionPipeline`` gets no equivalent pin even though ADR 0003
    section 1 states a *stronger* prohibition for semantic-relation (``-- Graph edge. Not
    embedded.``): relations carry no embedding at all, so there is no embedded value for a
    dual-write to copy, and the behavioural test below is what would catch one anyway.
    """
    sig = inspect.signature(EntityExtractionPipeline.__init__)
    params = set(sig.parameters) - {"self"}
    unknown = sorted(params - {"graph", "extractor", "embedder", "embedding_version", "max_episodes_per_pass"})

    assert not unknown, (
        f"EntityExtractionPipeline grew {unknown}. If any of these is a substrate, the "
        "entity-embedding boundary is being re-decided; if it is a collaborator, add it to the "
        "both-halves check in __init__ so the refusal test above covers it too."
    )
    # The annotation half is what catches a vector collaborator smuggled in under a name this
    # list happens to allow -- an exact-set or `"vector" not in params` check would not see it.
    # Substring match, not `"VectorSubstrate"` exactly, is deliberate: over-triggering (on e.g. a
    # future `VectorClock`/`VectorDb` parameter) is the right bias for a boundary pin -- it forces
    # a human look rather than silently passing a name that merely doesn't collide today.
    vector_typed = sorted(name for name, p in sig.parameters.items() if "Vector" in str(p.annotation))
    assert not vector_typed, (
        f"{vector_typed} is annotated as a Vector type. The entity-embedding boundary is being "
        "re-decided: entities are indexed on the graph node, not written to a VectorSubstrate."
    )


@pytest.mark.parametrize("is_async", [False, True])
def test_an_extracted_entity_never_lands_in_the_vector_substrate(is_async):
    """ADR 0003 section 1 marks semantic-entity as vector-backed as well as graph-backed, which
    the code satisfies by embedding every entity and indexing it on the **graph** node
    (``cypher.py:1535``, ``CREATE VECTOR INDEX ... FOR (n:Entity) ON (n.embedding)``, queried via
    ``db.index.vector.queryNodes``) -- not by a second row in the ``VectorSubstrate``. The ADR's
    vector mark is satisfied by that index, not contradicted by this boundary.

    Driven through the provider because that is the only object holding both a ``vector`` and an
    ``entity_pipeline``, and therefore the only place a dual-write could be introduced
    (``co_located.py:_entities``/``_aentities``). The entity name is deliberately absent from the
    episode text, so a semantic fact distilled from the same episode cannot be mistaken for it.
    """
    extractor = StubExtractor(default=[entity(name="Zephyr Holdings")])
    memory, vector, graph = provider(extractor=extractor)
    memory.write(ACME, [MemoryEvent(content="Acme ships things", scope=ACME)])

    if is_async:
        asyncio.run(memory.aconsolidate(ACME))
    else:
        memory.consolidate(ACME)

    assert graph.entities, "non-vacuity: the entity did land on the graph"
    rows = [r for bucket in vector.rows.values() for r in bucket.values()]
    # Content-independent: `MemoryRecord` (types.py:448-461) has no dedicated `embedding` field,
    # so any dual-write that puts a real, usable embedding into the vector substrate has to carry
    # it in `metadata` -- whatever the row's `content` says (the display name, `entity_type`,
    # `description`, or nothing at all). Checking `content` for the entity's name only catches the
    # one shape that happens to repeat it there.
    assert [r.id for r in rows if "embedding" in (r.metadata or {})] == [], (
        "an entity's embedding reached the VectorSubstrate; entities are embedded onto the graph node only"
    )


# ---------------------------------------------------------------------------
# Cost metering wiring (SM-X8, aicoe-tracker#144; coverage gap flagged on PR #159 review, finding
# #4) -- `LLMEntityExtractor.extract`/`.aextract` call `meter_extraction_cost` after every model
# call. Only the standalone function is unit-tested elsewhere (test_memory_metrics.py); this pins
# that the real call site actually wires scope/model/response_usage through.
# ---------------------------------------------------------------------------


class _MeteredAnswer:
    def __init__(self, entities, input_tokens=100, output_tokens=20):
        self.parsed = ExtractedEntities(entities=entities)
        self.content = None
        self.response_usage = _Usage(input_tokens, output_tokens)


class _Usage:
    def __init__(self, input_tokens=0, output_tokens=0, cache_read_tokens=0, reasoning_tokens=0):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_tokens = cache_read_tokens
        self.reasoning_tokens = reasoning_tokens


class _FakeModel:
    """A minimal double for a live ``Model`` -- unregistered, so cost resolves to ``cost=None``,
    but the wiring (tenant_id, kind="extraction") is what these tests pin, not the price."""

    id = "fake-extraction-model"

    def response(self, messages, response_format=None):
        return _MeteredAnswer([entity()])

    async def aresponse(self, messages, response_format=None):
        await asyncio.sleep(0)
        return self.response(messages, response_format=response_format)


@pytest.fixture(autouse=True)
def _clean_default_aggregator_for_metering_tests():
    from egpt.memory.metrics import reset_default_aggregator

    reset_default_aggregator()
    yield
    reset_default_aggregator()


def test_extract_meters_the_extraction_cost():
    extractor = LLMEntityExtractor(model=_FakeModel())

    extractor.extract(episode("Acme ships things"))

    from egpt.memory.metrics import get_default_aggregator

    entries = get_default_aggregator().costs
    assert len(entries) == 1
    assert entries[0].kind == "extraction"
    assert entries[0].tenant_id == ACME.tenant_id
    assert entries[0].input_tokens == 100


async def test_aextract_meters_the_extraction_cost():
    extractor = LLMEntityExtractor(model=_FakeModel())

    await extractor.aextract(episode("Acme ships things"))

    from egpt.memory.metrics import get_default_aggregator

    entries = get_default_aggregator().costs
    assert len(entries) == 1
    assert entries[0].kind == "extraction"
    assert entries[0].tenant_id == ACME.tenant_id
