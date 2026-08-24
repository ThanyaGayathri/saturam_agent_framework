"""Procedural memory: reflection-driven skill/rule synthesis (SM-S2 #119).

Three acceptance criteria, each with a test that fails without the code: a procedure is
synthesized from a cluster of episodes once their importance crosses a threshold (mirroring
SM-L6's own gate), the write lands on the PROCEDURAL tier through the ordinary write path, and it
is retrieved and injected through the ordinary read path for a matching query -- the ticket's own
testing note.

``ProceduralReflectionPipeline`` is tested standalone first (no provider): it is stateless across
calls, unlike ``ReflectionPipeline`` -- see the module docstring for why. The wiring section pins
that a written procedure needs nothing new to be retrievable and injectable.
"""

import asyncio
from unittest.mock import MagicMock

import pytest

from egpt.memory.adapters.co_located import CoLocatedMemoryProvider
from egpt.memory.config import MemoryProviderConfig
from egpt.memory.procedural import (
    PROCEDURAL_REFLECTION_KEYS,
    SYNTHESIZER_ID,
    LLMProcedureSynthesizer,
    ProceduralReflectionPipeline,
    ProcedureSynthesizer,
    SynthesizedProcedure,
)
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import (
    MemoryEvent,
    MemoryOperationError,
    MemoryQuery,
    MemoryRecord,
    MemoryScopeRequiredError,
    Scope,
)
from egpt.models.base import Model
from egpt.models.response import ModelResponse
from tests.unit.memory._fakes import FakeVector

ACME = Scope(tenant_id="acme", user_id="ada")


def episode(content, episode_id="e1", scope=ACME, importance=None, **kwargs):
    metadata = kwargs.pop("metadata", {})
    if importance is not None:
        metadata = {**metadata, "importance": importance}
    return MemoryRecord(
        id=episode_id, tier=MemoryTier.EPISODIC, content=content, scope=scope, metadata=metadata, **kwargs
    )


class StubSynthesizer:
    """Returns whatever it was configured with, and records what it was shown -- the same double
    ``test_consolidation_reflect.py`` uses, so this file tests the pipeline's own decisions, not a
    model."""

    def __init__(self, result=None):
        self.result = result
        self.calls = []

    def synthesize(self, episodes):
        self.calls.append(tuple(e.id for e in episodes))
        return self.result

    async def asynthesize(self, episodes):
        await asyncio.sleep(0)
        return self.synthesize(episodes)


class SyncOnly:
    def synthesize(self, episodes):
        return None


class AsyncOnly:
    async def asynthesize(self, episodes):
        return None


def procedure(title="Ask before deploying", content="Confirm with the on-call before a Friday deploy", **kwargs):
    kwargs.setdefault("kind", "rule")
    return SynthesizedProcedure(has_procedure=True, title=title, content=content, **kwargs)


# ---------------------------------------------------------------------------
# AC 1 -- a procedure is synthesized once accumulated importance crosses the threshold
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("is_async", [False, True])
def test_empty_episodes_returns_untriggered_report(is_async):
    pipe = ProceduralReflectionPipeline(StubSynthesizer(), importance_threshold=1.0)

    call = pipe.arun(ACME, []) if is_async else pipe.run(ACME, [])
    report = asyncio.run(call) if is_async else call

    assert report.details["triggered"] is False
    assert report.write is None
    assert report.accumulated_importance == 0.0


@pytest.mark.parametrize("is_async", [False, True])
def test_below_threshold_never_synthesizes(is_async):
    synth = StubSynthesizer(result=procedure())
    pipe = ProceduralReflectionPipeline(synth, importance_threshold=5.0)
    episodes = [episode("a", "e1", importance=0.2), episode("b", "e2", importance=0.3)]

    call = pipe.arun(ACME, episodes) if is_async else pipe.run(ACME, episodes)
    report = asyncio.run(call) if is_async else call

    assert report.details["triggered"] is False
    assert report.write is None
    assert report.accumulated_importance == pytest.approx(0.5)
    assert synth.calls == [], "the synthesizer must not be asked before the threshold is crossed"


@pytest.mark.parametrize("is_async", [False, True])
def test_crossing_the_threshold_synthesizes_and_cites_every_source(is_async):
    synth = StubSynthesizer(result=procedure())
    pipe = ProceduralReflectionPipeline(synth, importance_threshold=1.0)
    episodes = [episode("a", "e1", importance=0.6), episode("b", "e2", importance=0.6)]

    call = pipe.arun(ACME, episodes) if is_async else pipe.run(ACME, episodes)
    report = asyncio.run(call) if is_async else call

    assert report.details["triggered"] is True
    assert report.accumulated_importance == pytest.approx(1.2)
    assert report.episodes_considered == 2
    write = report.write
    assert write is not None
    assert write.tier is MemoryTier.PROCEDURAL
    assert write.metadata["source_episode_ids"] == ["e1", "e2"]
    assert write.metadata["derived_by"] == SYNTHESIZER_ID
    assert write.metadata["kind"] == "rule"
    assert "Ask before deploying" in write.content
    assert synth.calls == [("e1", "e2")]


def test_a_decline_still_reports_triggered():
    """A synthesizer reporting no procedure is still a decision, distinguishable in the details
    from never having crossed the threshold at all."""
    synth = StubSynthesizer(result=None)
    pipe = ProceduralReflectionPipeline(synth, importance_threshold=1.0)

    report = pipe.run(ACME, [episode("a", "e1", importance=0.6), episode("b", "e2", importance=0.6)])

    assert report.details["triggered"] is True
    assert report.write is None


def test_negative_importance_is_clamped_not_subtracted():
    """Same guard SM-L6's `_clamped_importance` provides, reused directly here (module docstring)
    rather than reimplemented -- pinned so a future edit cannot silently stop sharing it."""
    synth = StubSynthesizer(result=procedure())
    pipe = ProceduralReflectionPipeline(synth, importance_threshold=1.0)

    report = pipe.run(ACME, [episode("a", "e1", importance=0.6), episode("b", "e2", importance=-0.6)])

    assert report.accumulated_importance == pytest.approx(0.6), "the negative contributes 0.0, not -0.6"
    assert report.details["triggered"] is False


def test_no_cross_call_accumulation():
    """Unlike `ReflectionPipeline`, this pipeline is stateless across calls (module docstring): a
    sub-threshold call must not leave anything behind for the next one to build on."""
    synth = StubSynthesizer(result=procedure())
    pipe = ProceduralReflectionPipeline(synth, importance_threshold=1.0)

    first = pipe.run(ACME, [episode("a", "e1", importance=0.6)])
    second = pipe.run(ACME, [episode("b", "e2", importance=0.6)])

    assert first.details["triggered"] is False
    assert second.details["triggered"] is False, "each call is judged on its own batch alone"
    assert synth.calls == []


def test_a_non_episodic_source_is_refused():
    pipe = ProceduralReflectionPipeline(StubSynthesizer())
    semantic_row = MemoryRecord(id="f1", tier=MemoryTier.SEMANTIC, content="a fact", scope=ACME)

    with pytest.raises(MemoryOperationError) as excinfo:
        pipe.run(ACME, [semantic_row])
    assert excinfo.value.field_name == "tier"


# ---------------------------------------------------------------------------
# Construction guards
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [SyncOnly(), AsyncOnly(), object()])
def test_a_synthesizer_missing_either_half_is_refused_at_construction(bad):
    with pytest.raises(MemoryOperationError) as excinfo:
        ProceduralReflectionPipeline(bad)
    assert excinfo.value.field_name == "synthesizer"


@pytest.mark.parametrize("bad", [0, -1, "5", True])
def test_importance_threshold_is_validated(bad):
    with pytest.raises(MemoryOperationError) as excinfo:
        ProceduralReflectionPipeline(StubSynthesizer(), importance_threshold=bad)
    assert excinfo.value.field_name == "importance_threshold"


@pytest.mark.parametrize("bad", [0, -1, 1.5, True])
def test_max_episodes_per_pass_is_validated(bad):
    with pytest.raises(MemoryOperationError) as excinfo:
        ProceduralReflectionPipeline(StubSynthesizer(), max_episodes_per_pass=bad)
    assert excinfo.value.field_name == "max_episodes_per_pass"


@pytest.mark.parametrize("is_async", [False, True])
def test_max_episodes_per_pass_caps_what_the_synthesizer_sees(is_async):
    """Regression: an uncapped pass sent every episode to the synthesizer regardless of how many
    there were, risking a model context-length failure on a large backlog."""
    synth = StubSynthesizer(result=procedure())
    pipe = ProceduralReflectionPipeline(synth, importance_threshold=0.01, max_episodes_per_pass=2)
    episodes = [episode(str(i), f"e{i}", importance=0.1) for i in range(5)]

    call = pipe.arun(ACME, episodes) if is_async else pipe.run(ACME, episodes)
    report = asyncio.run(call) if is_async else call

    assert report.episodes_considered == 2
    assert len(synth.calls[0]) == 2


@pytest.mark.parametrize("is_async", [False, True])
def test_run_refuses_an_unscoped_call(is_async):
    pipe = ProceduralReflectionPipeline(StubSynthesizer())
    with pytest.raises(MemoryScopeRequiredError):
        asyncio.run(pipe.arun(None, [episode("a")])) if is_async else pipe.run(None, [episode("a")])


def test_details_carry_the_procedural_reflection_floor():
    synth = StubSynthesizer(result=procedure())
    pipe = ProceduralReflectionPipeline(synth, importance_threshold=1.0)

    report = pipe.run(ACME, [episode("a", "e1", importance=0.6), episode("b", "e2", importance=0.6)])

    assert set(report.details) >= set(PROCEDURAL_REFLECTION_KEYS)
    assert report.details["synthesizer"] == SYNTHESIZER_ID


def test_procedure_synthesizer_is_runtime_checkable():
    assert isinstance(StubSynthesizer(), ProcedureSynthesizer)
    assert not isinstance(SyncOnly(), ProcedureSynthesizer)


# ---------------------------------------------------------------------------
# Idempotence -- a re-run over the same source set upserts, never duplicates
# ---------------------------------------------------------------------------


def test_the_procedure_id_is_derived_from_its_source_set():
    pipe = ProceduralReflectionPipeline(StubSynthesizer())
    batch = [episode("a", "e1"), episode("b", "e2")]

    first = pipe._to_write(ACME, batch, procedure())
    second = pipe._to_write(ACME, list(reversed(batch)), procedure(content="a different phrasing entirely"))

    assert first.id == second.id, "the id must depend on the source set, not content or order"


def test_to_write_refuses_a_blank_title_or_content():
    """`_to_write`'s own re-check, for a caller reaching it through a `ProcedureSynthesizer` other
    than `LLMProcedureSynthesizer` -- whose `_extract` already strips before this point, but the
    Protocol makes a custom synthesizer's path part of the public contract, so `_to_write` cannot
    assume its input was already validated."""
    pipe = ProceduralReflectionPipeline(StubSynthesizer())
    batch = [episode("a", "e1")]

    with pytest.raises(MemoryOperationError) as excinfo:
        pipe._to_write(ACME, batch, SynthesizedProcedure(has_procedure=True, kind="rule", title="", content="x"))
    assert excinfo.value.field_name == "content"


def test_to_write_refuses_a_whitespace_only_title_or_content():
    """Regression: bare truthiness (`if not procedure.title`) lets `"   "` through unchallenged,
    since a whitespace-only string is truthy -- this is the case `_to_write`'s own re-check must
    catch for a custom synthesizer that does not strip, since only `LLMProcedureSynthesizer._extract`
    does."""
    pipe = ProceduralReflectionPipeline(StubSynthesizer())
    batch = [episode("a", "e1")]

    with pytest.raises(MemoryOperationError) as excinfo:
        pipe._to_write(ACME, batch, SynthesizedProcedure(has_procedure=True, kind="rule", title="   ", content="x"))
    assert excinfo.value.field_name == "content"


def test_to_write_refuses_an_unknown_kind():
    pipe = ProceduralReflectionPipeline(StubSynthesizer())
    batch = [episode("a", "e1")]

    with pytest.raises(MemoryOperationError) as excinfo:
        pipe._to_write(ACME, batch, SynthesizedProcedure(has_procedure=True, kind="wizardry", title="t", content="c"))
    assert excinfo.value.field_name == "kind"


# ---------------------------------------------------------------------------
# LLMProcedureSynthesizer._extract -- against a mocked Model, the same double
# `test_consolidation_reflect.py` uses for `LLMInsightSynthesizer`.
# ---------------------------------------------------------------------------


def _model_with_procedure(has_procedure=True, kind="rule", title="A title", content="A body") -> MagicMock:
    model = MagicMock(spec=Model)
    model.supports_native_structured_outputs = True
    model.supports_json_schema_outputs = False
    parsed = SynthesizedProcedure(has_procedure=has_procedure, kind=kind, title=title, content=content)
    model.response = MagicMock(return_value=ModelResponse(content=parsed, parsed=parsed))
    return model


def test_whitespace_only_title_or_content_is_refused_not_accepted_as_blank():
    synth = LLMProcedureSynthesizer(model=_model_with_procedure(title="   ", content="real content"))

    with pytest.raises(MemoryOperationError) as excinfo:
        synth.synthesize([episode("a", "e1")])
    assert excinfo.value.field_name == "content"


def test_an_unknown_kind_is_refused():
    synth = LLMProcedureSynthesizer(model=_model_with_procedure(kind="wizardry"))

    with pytest.raises(MemoryOperationError) as excinfo:
        synth.synthesize([episode("a", "e1")])
    assert excinfo.value.field_name == "kind"


def test_a_real_title_and_content_are_accepted():
    synth = LLMProcedureSynthesizer(model=_model_with_procedure(title="Title", content="Body"))

    result = synth.synthesize([episode("a", "e1")])

    assert result.title == "Title"
    assert result.content == "Body"
    assert result.kind == "rule"


def test_a_decline_is_collapsed_to_none():
    synth = LLMProcedureSynthesizer(model=_model_with_procedure(has_procedure=False))

    assert synth.synthesize([episode("a", "e1")]) is None


@pytest.mark.parametrize("is_async", [False, True])
def test_synthesize_refuses_empty_episodes(is_async):
    synth = LLMProcedureSynthesizer(model=_model_with_procedure())

    with pytest.raises(MemoryOperationError) as excinfo:
        asyncio.run(synth.asynthesize([])) if is_async else synth.synthesize([])
    assert excinfo.value.field_name == "episodes"


# ---------------------------------------------------------------------------
# AC 2 + 3 -- a written procedure is retrievable and injected through the ordinary read path,
# with nothing new required for either (module docstring). Wiring against a real provider, the
# same shared `FakeVector` double the semantic fact store and reflection tests use.
# ---------------------------------------------------------------------------


def _provider():
    vector = FakeVector()
    return CoLocatedMemoryProvider(vector=vector, config=MemoryProviderConfig(provider="co_located")), vector


@pytest.mark.parametrize("is_async", [False, True])
def test_a_written_procedure_is_retrieved_and_injected_for_a_matching_query(is_async):
    """The ticket's own testing note: a learned rule is retrieved and injected for a matching
    task. No tier is named on the read -- PROCEDURAL is in PERSISTENT_TIERS by default."""
    memory, _ = _provider()
    synth = StubSynthesizer(result=procedure(title="Ship on Fridays carefully", content="Get sign-off first"))
    pipe = ProceduralReflectionPipeline(synth, importance_threshold=0.5)
    episodes = [episode("A Friday deploy went wrong last week", "e1", importance=0.9)]

    report = pipe.run(ACME, episodes)
    assert report.write is not None
    if is_async:
        asyncio.run(memory.awrite(ACME, [report.write]))
        slice_ = asyncio.run(memory.aread(ACME, MemoryQuery(text="deploying on a Friday")))
    else:
        memory.write(ACME, [report.write])
        slice_ = memory.read(ACME, MemoryQuery(text="deploying on a Friday"))

    matching = [r for r in slice_.records if r.tier is MemoryTier.PROCEDURAL]
    assert matching, "the procedure must come back through the ordinary default-tier read"
    assert "Ship on Fridays carefully" in matching[0].content
    assert matching[0].metadata["kind"] == "rule"
    assert matching[0].metadata["source_episode_ids"] == ["e1"]

    context = slice_.as_context()
    assert "[procedural]" in context
    assert "Ship on Fridays carefully" in context


def test_a_plain_procedural_event_needs_no_new_write_path():
    """PROCEDURAL is vector-only (TIER_POLICY): a plain `MemoryEvent` reaches `self.vector.upsert`
    through the ordinary `write`, with no graph leg to route around."""
    memory, vector = _provider()

    memory.write(
        ACME,
        [MemoryEvent(content="Always confirm before a prod deploy", scope=ACME, tier=MemoryTier.PROCEDURAL)],
    )

    assert "upsert" in vector.calls
    stored = next(iter(vector.rows[ACME.tenant_id].values()))
    assert stored.tier is MemoryTier.PROCEDURAL
