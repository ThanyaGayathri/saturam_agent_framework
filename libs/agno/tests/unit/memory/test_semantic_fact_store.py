"""The semantic fact store and its vector-leg projection (SM-S1 #118).

``fact_record`` closes the gap the ADR's tier/substrate split names implicitly: a fact retrieved
through ``VectorSubstrate.search`` must carry the same subject/predicate/object/confidence/
derived_by a ``GraphSubstrate.recall_facts`` read already does. ``SemanticFactStore`` is the
reusable dual-write SM-L3 (#124) depends on.
"""

import asyncio

import pytest

from egpt.memory.schemas.semantic import FACT_METADATA_KEYS, SPO_PROVENANCE_KEYS, fact_record
from egpt.memory.semantic import SemanticFactStore
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import (
    FactWrite,
    MemoryOperationError,
    MemoryRecord,
    MemoryScopeRequiredError,
    PartialWriteError,
    Scope,
    WriteReceipt,
)
from tests.unit.memory._fakes import FakeGraph, FakeVector

ACME = Scope(tenant_id="acme", user_id="ada")
ACME_BOB = Scope(tenant_id="acme", user_id="bob")


def fact(
    id_="f1",
    subject=None,
    predicate=None,
    object=None,
    confidence=1.0,
    source_episode_ids=(),
    derived_by=None,
    scope=ACME,
    content="ada works at acme",
    record_provenance=None,
):
    return FactWrite(
        record=MemoryRecord(
            id=id_,
            tier=MemoryTier.SEMANTIC,
            content=content,
            scope=scope,
            provenance=dict(record_provenance or {}),
        ),
        subject=subject,
        predicate=predicate,
        object=object,
        confidence=confidence,
        source_episode_ids=list(source_episode_ids),
        derived_by=derived_by,
    )


# -- fact_record --------------------------------------------------------------


def test_a_triple_carries_spo_in_provenance():
    record = fact_record(fact(subject="ada", predicate="works_at", object="acme"))
    assert record.provenance["subject"] == "ada"
    assert record.provenance["predicate"] == "works_at"
    assert record.provenance["object"] == "acme"
    assert set(SPO_PROVENANCE_KEYS) <= record.provenance.keys()


def test_an_untripled_fact_carries_no_spo_keys():
    """`is_triple()` is false with any SPO slot missing -- the projection must not half-fill."""
    record = fact_record(fact(subject="ada", predicate=None, object="acme"))
    assert not any(key in record.provenance for key in SPO_PROVENANCE_KEYS)


def test_confidence_and_derived_by_always_land_in_metadata():
    record = fact_record(fact(confidence=0.75, derived_by="sm_l3.extract"))
    assert record.metadata["confidence"] == 0.75
    assert record.metadata["derived_by"] == "sm_l3.extract"
    assert set(FACT_METADATA_KEYS) <= record.metadata.keys()


def test_derived_by_none_is_still_present_as_a_key():
    """Present-but-None is not the same as absent -- a reader checking `"derived_by" in metadata`
    must get the same answer whether or not a strategy was named."""
    record = fact_record(fact(derived_by=None))
    assert "derived_by" in record.metadata
    assert record.metadata["derived_by"] is None


def test_source_episode_ids_defaults_into_provenance():
    record = fact_record(fact(source_episode_ids=["e1", "e2"]))
    assert record.provenance["source_episode_ids"] == ["e1", "e2"]


def test_source_episode_ids_already_on_the_record_is_not_overwritten():
    """Matches `Neo4jGraphSubstrate._fact_batches`'s `setdefault` exactly -- `_distil` populates
    `record.provenance["source_episode_ids"]` directly, ahead of `FactWrite.source_episode_ids`
    carrying the identical list, and this projection must not disagree with itself about which
    wins."""
    f = fact(source_episode_ids=["from-fact-write"], record_provenance={"source_episode_ids": ["from-record"]})
    record = fact_record(f)
    assert record.provenance["source_episode_ids"] == ["from-record"]


def test_the_original_fact_record_is_not_mutated():
    f = fact(subject="ada", predicate="works_at", object="acme", confidence=0.5)
    fact_record(f)
    assert f.record.provenance == {}
    assert f.record.metadata == {}


def test_other_provenance_keys_survive_the_projection():
    f = fact(record_provenance={"entity_ids": ["acme/*/*/*/*/entity/ada"]})
    record = fact_record(f)
    assert record.provenance["entity_ids"] == ["acme/*/*/*/*/entity/ada"]


# -- SemanticFactStore ----------------------------------------------------------


def store():
    vector = FakeVector()
    graph = FakeGraph()
    return SemanticFactStore(vector=vector, graph=graph), vector, graph


def test_write_reaches_both_substrates():
    s, vector, graph = store()
    receipt = s.write(ACME, [fact(subject="ada", predicate="works_at", object="acme")])

    assert receipt.accepted == 1
    assert receipt.event_ids == ["f1"]
    assert "upsert" in vector.calls
    assert "upsert_facts" in graph.calls


def test_the_vector_row_carries_the_same_shape_recall_facts_returns():
    """The acceptance criterion itself: retrievable via both paths, with the same content."""
    s, vector, graph = store()
    s.write(ACME, [fact(subject="ada", predicate="works_at", object="acme", confidence=0.9, derived_by="x")])

    stored = vector.rows["acme"]["f1"]
    assert stored.provenance["subject"] == "ada"
    assert stored.metadata["confidence"] == 0.9
    assert stored.metadata["derived_by"] == "x"

    graph_row = graph.rows["acme"][0]
    assert graph_row.provenance["subject"] == "ada"


def test_an_empty_batch_is_a_no_op():
    s, vector, graph = store()
    receipt = s.write(ACME, [])

    assert receipt == WriteReceipt()
    assert vector.calls == []
    assert graph.calls == []


def test_a_graph_only_deployment_still_writes():
    vector = FakeVector()
    s = SemanticFactStore(vector=None, graph=FakeGraph())
    receipt = s.write(ACME, [fact()])
    assert receipt.accepted == 1
    assert vector.calls == []


def test_a_vector_only_deployment_still_writes():
    s = SemanticFactStore(vector=FakeVector(), graph=None)
    receipt = s.write(ACME, [fact()])
    assert receipt.accepted == 1


def test_a_no_substrate_deployment_returns_a_receipt_reflecting_the_batch_not_a_commit():
    """Neither substrate is wired, so nothing is written -- but `accepted` describes the batch
    size, matching every other write on this seam, not how many rows landed anywhere."""
    s = SemanticFactStore(vector=None, graph=None)
    receipt = s.write(ACME, [fact()])
    assert receipt.accepted == 1
    assert receipt.tiers_written == [MemoryTier.SEMANTIC]


async def test_an_async_no_substrate_deployment_returns_a_receipt_reflecting_the_batch():
    s = SemanticFactStore(vector=None, graph=None)
    receipt = await s.awrite(ACME, [fact()])
    assert receipt.accepted == 1


def test_facts_are_grouped_and_written_under_their_own_scope_not_a_wider_call_scope():
    s, vector, graph = store()
    tenant_wide = Scope(tenant_id="acme")
    s.write(
        tenant_wide,
        [
            fact(id_="ada-fact", scope=ACME, content="ada's fact"),
            fact(id_="bob-fact", scope=ACME_BOB, content="bob's fact"),
        ],
    )

    assert vector.rows["acme"]["ada-fact"].scope == ACME
    assert vector.rows["acme"]["bob-fact"].scope == ACME_BOB


def test_a_duplicate_id_in_one_batch_is_refused():
    s, vector, graph = store()
    with pytest.raises(MemoryOperationError, match="Duplicate"):
        s.write(ACME, [fact(id_="dup"), fact(id_="dup", content="second")])
    assert vector.calls == []
    assert graph.calls == []


def test_a_refusal_before_any_commit_propagates_bare():
    class RefusingGraph(FakeGraph):
        def upsert_facts(self, scope, facts):
            raise MemoryOperationError("refused", field_name="content")

    s = SemanticFactStore(vector=None, graph=RefusingGraph())
    with pytest.raises(MemoryOperationError):
        s.write(ACME, [fact()])


def test_a_refusal_after_the_vector_leg_committed_is_a_partial_write():
    vector = FakeVector()

    class RefusingGraph(FakeGraph):
        def upsert_facts(self, scope, facts):
            raise MemoryOperationError("refused", field_name="content")

    s = SemanticFactStore(vector=vector, graph=RefusingGraph())
    with pytest.raises(PartialWriteError) as excinfo:
        s.write(ACME, [fact()])
    assert excinfo.value.receipt.event_ids == ["f1"]
    assert vector.rows["acme"], "the vector leg had already committed"


def test_the_partial_write_preserves_which_substrate_refused_and_why():
    """The refusal must survive as `__cause__` -- `from None` would discard which substrate
    refused and why, leaving only "partially applied" with no way to diagnose it."""
    vector = FakeVector()

    class RefusingGraph(FakeGraph):
        def upsert_facts(self, scope, facts):
            raise MemoryOperationError("refused for a specific reason", field_name="content")

    s = SemanticFactStore(vector=vector, graph=RefusingGraph())
    with pytest.raises(PartialWriteError) as excinfo:
        s.write(ACME, [fact()])
    assert isinstance(excinfo.value.__cause__, MemoryOperationError)
    assert "refused for a specific reason" in str(excinfo.value.__cause__)


async def test_the_async_partial_write_preserves_which_substrate_refused_and_why():
    class RefusingGraph(FakeGraph):
        async def aupsert_facts(self, scope, facts):
            raise MemoryOperationError("refused for a specific reason", field_name="content")

    s = SemanticFactStore(vector=FakeVector(), graph=RefusingGraph())
    with pytest.raises(PartialWriteError) as excinfo:
        await s.awrite(ACME, [fact()])
    assert isinstance(excinfo.value.__cause__, MemoryOperationError)
    assert "refused for a specific reason" in str(excinfo.value.__cause__)


def test_a_non_semantic_fact_is_refused():
    s, vector, graph = store()
    non_semantic = FactWrite(
        record=MemoryRecord(id="e1", tier=MemoryTier.EPISODIC, content="oops", scope=ACME),
    )
    with pytest.raises(MemoryOperationError, match="SEMANTIC"):
        s.write(ACME, [non_semantic])
    assert vector.calls == []
    assert graph.calls == []


async def test_an_async_non_semantic_fact_is_refused():
    s, vector, graph = store()
    non_semantic = FactWrite(
        record=MemoryRecord(id="e1", tier=MemoryTier.EPISODIC, content="oops", scope=ACME),
    )
    with pytest.raises(MemoryOperationError, match="SEMANTIC"):
        await s.awrite(ACME, [non_semantic])
    assert vector.calls == []
    assert graph.calls == []


def test_write_requires_a_scope():
    s, _, _ = store()
    with pytest.raises(MemoryScopeRequiredError):
        s.write(None, [fact()])


async def test_awrite_requires_a_scope():
    """The async twin of `test_write_requires_a_scope` -- every scope guard needs both."""
    s, _, _ = store()
    with pytest.raises(MemoryScopeRequiredError):
        await s.awrite(None, [fact()])


def test_write_makes_the_vector_row_match_the_graph_shape():
    """Sync half: same assertion `test_the_vector_row_carries_the_same_shape_recall_facts_returns`
    makes, restated here so the sync/async pair is visible side by side."""
    s, vector, graph = store()
    facts = [fact(subject="ada", predicate="works_at", object="acme", confidence=0.9, derived_by="x")]

    receipt = s.write(ACME, facts)

    assert receipt.accepted == 1
    stored = vector.rows["acme"]["f1"]
    assert stored.provenance["subject"] == "ada"
    assert stored.metadata["confidence"] == 0.9


async def test_awrite_makes_the_vector_row_match_the_graph_shape():
    """The async twin: same batch, same receipt shape, same stored row as the sync half above."""
    s, vector, graph = store()
    facts = [fact(subject="ada", predicate="works_at", object="acme", confidence=0.9, derived_by="x")]

    receipt = await s.awrite(ACME, facts)

    assert receipt.accepted == 1
    stored = vector.rows["acme"]["f1"]
    assert stored.provenance["subject"] == "ada"
    assert stored.metadata["confidence"] == 0.9


async def test_awrite_calls_the_async_verbs():
    """`awrite` must delegate to `aupsert`/`aupsert_facts`, not silently block on a sync call."""
    s, vector, graph = store()
    await s.awrite(ACME, [fact()])

    assert "aupsert" in vector.calls
    assert "aupsert_facts" in graph.calls


async def test_awrite_writes_vector_and_graph_concurrently_not_sequentially():
    """Major finding: `awrite` awaited vector then graph in series. A slow leg must not block
    starting the other -- if the graph write starts only after the vector write's coroutine has
    already finished awaiting, they were not concurrent."""
    started = []
    released = asyncio.Event()

    class SlowVector(FakeVector):
        async def aupsert(self, scope, records):
            started.append("vector")
            await released.wait()
            return await super().aupsert(scope, records)

    class SlowGraph(FakeGraph):
        async def aupsert_facts(self, scope, facts):
            started.append("graph")
            await released.wait()
            return await super().aupsert_facts(scope, facts)

    s = SemanticFactStore(vector=SlowVector(), graph=SlowGraph())
    task = asyncio.ensure_future(s.awrite(ACME, [fact()]))
    # Both legs must have registered as *started* before either is allowed to finish -- a
    # sequential implementation starts the graph leg only after the vector leg's `await` returns,
    # so `started` would still be `["vector"]` at this point under the old code.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert sorted(started) == ["graph", "vector"]
    released.set()
    await task


def test_an_empty_awrite_batch_is_a_no_op():
    s, vector, graph = store()
    receipt = asyncio.run(s.awrite(ACME, []))
    assert receipt == WriteReceipt()
    assert vector.calls == []
    assert graph.calls == []
