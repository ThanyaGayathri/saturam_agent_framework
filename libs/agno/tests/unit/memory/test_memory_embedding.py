"""The memory embedding pipeline (SM-V2 #96).

No model and no database: every candidate embedder is a double, because what this ticket decides is
*which* embedder a mode gets, *how many* calls a batch costs, and *what* a stored vector is tagged
with -- none of which needs a real model, and all of which a real model would make slow and
non-deterministic to assert.

The doubles are stricter than the loosest real embedder: they record how they were called, refuse
whichever call shape they did not declare -- a list if they are single-text, a bare string if they
are batch-only -- and the async half genuinely suspends.
"""

import asyncio

import pytest

from egpt.memory.config import MemoryProviderConfigError
from egpt.memory.embedding import MODE_DEFAULTS, OFFLINE_CAPABLE, MemoryEmbedder, resolve_memory_embedder
from egpt.providers.model_provider import ProviderMode

DIM = 4


class Recorder:
    """A single-text embedder that records every call it received.

    It declares no ``accepts_text_batches``, matching every embedder in the tree but one, and
    ``get_embedding`` **refuses a list** rather than quietly embedding its repr. A real single-text
    embedder handed a list returns one vector for the concatenation, which is silently wrong -- a
    double that tolerated it could not tell the pipeline's chunking from a bug in it.

    ``enable_batch`` is set here precisely because it must **not** be what the pipeline reads: this
    is the ``AzureOpenAIEmbedder`` shape, which carries the flag for the separate async batch API
    while its ``get_embedding`` still takes one string. A pipeline reading the flag would hand this
    double a list and get a ``TypeError``.
    """

    id = "recorder-v1"
    dimensions = DIM
    enable_batch = True
    batch_size = 100
    offline_capable = True

    def __init__(self):
        self.calls = []

    def _vector(self, text):
        return [float(len(text)), 0.0, 0.0, 0.0]

    def get_embedding(self, text):
        if not isinstance(text, str):
            raise TypeError(f"{type(self).__name__} takes one string; got {type(text).__name__}.")
        self.calls.append(text)
        return self._vector(text)

    async def async_get_embedding(self, text):
        await asyncio.sleep(0)
        return self.get_embedding(text)


class BatchRecorder(Recorder):
    """A batching embedder: takes a list, returns one vector per element, in order.

    **Strictly list-only** -- a bare string is refused. Real batch-only clients are, and a double
    that accepted both could not distinguish "the pipeline always sends a list" from "the pipeline
    sends a string whenever the chunk happens to hold one item", which is the shape that made
    ``embed_query`` take the wrong branch for every batching embedder.
    """

    id = "batch-recorder-v1"
    accepts_text_batches = True
    batch_size = 2

    def get_embedding(self, text):
        if isinstance(text, str):
            raise TypeError(f"{type(self).__name__} takes a list of strings; got a bare str.")
        texts = list(text)
        self.calls.append(list(texts))
        return [self._vector(one) for one in texts]


def pipeline(embedder=None, mode=ProviderMode.FRONTIER, **kwargs):
    return resolve_memory_embedder(mode, embedder if embedder is not None else Recorder(), **kwargs)


# -- criterion 2: the embedder is selectable per mode --------------------------


def test_each_mode_has_the_embedder_SM_R4_chose(monkeypatch):
    """Asserted against ``DECISION.md`` section 1, which is the artifact of record -- the SM-R4 PR
    body still says ``gte-base`` for Mode A and ADR 0003's Context section flags that discrepancy."""
    from egpt.memory import embedding

    seen = {}

    def _fake(key, options):
        seen["key"], seen["options"] = key, options
        return Recorder()

    monkeypatch.setattr(embedding, "_construct", _fake)

    resolve_memory_embedder(ProviderMode.LOCAL, dimensions=DIM)
    assert seen["key"] == "sentence_transformer"
    assert seen["options"]["id"] == "BAAI/bge-base-en-v1.5"

    resolve_memory_embedder(ProviderMode.FRONTIER, dimensions=DIM)
    assert seen["key"] == "azure_openai"
    assert seen["options"]["id"] == "text-embedding-3-large"


def test_the_mode_defaults_carry_the_dimension_SM_R4_sized_the_column_for():
    """768 for Mode A and 1024 for Mode B, both inside pgvector's 2000-dim ``vector`` limit -- which
    is why Mode B is the truncated 3-large and not its native 3072 (DECISION.md section 4)."""
    assert MODE_DEFAULTS[ProviderMode.LOCAL]["dimensions"] == 768
    assert MODE_DEFAULTS[ProviderMode.FRONTIER]["dimensions"] == 1024
    assert all(default["dimensions"] <= 2000 for default in MODE_DEFAULTS.values())


def test_the_mode_default_tells_its_embedder_the_dimension(monkeypatch):
    """The default's width is a **constructor argument**, not only a recorded number.

    ``SentenceTransformerEmbedder.dimensions`` is 384 whatever model it loads, so a default that
    named bge-base and recorded 768 built an embedder declaring 384 -- and the pipeline refused its
    own default at the disagreement check. Every double here declares the width it returns, which
    is precisely what the real class does not do, so no double could have caught it; the cookbook
    did. This is the regression test for that.
    """
    from egpt.memory import embedding

    seen = {}
    monkeypatch.setattr(embedding, "_construct", lambda key, options: seen.update(options) or Recorder())
    for mode, default in embedding.MODE_DEFAULTS.items():
        resolve_memory_embedder(mode, dimensions=DIM)
        assert seen["dimensions"] == default["dimensions"]


def test_a_caller_option_beats_the_mode_default(monkeypatch):
    """Pinning a different revision must not mean giving up the mode default's other choices."""
    from egpt.memory import embedding

    seen = {}
    monkeypatch.setattr(embedding, "_construct", lambda key, options: seen.update(options) or Recorder())
    resolve_memory_embedder(ProviderMode.LOCAL, embedder_options={"id": "BAAI/bge-base-en-v1.0"}, dimensions=DIM)
    assert seen["id"] == "BAAI/bge-base-en-v1.0"


def test_a_caller_dimension_option_is_the_width_the_pipeline_resolves_to(monkeypatch):
    """``embedder_options={"dimensions": N}`` builds the embedder at N, so N is the width -- not the mode
    default. Taking the default here refused the caller's own setting while quoting a number they
    never passed ("dimensions is 768 but ... is 512"), leaving them nothing to map back to an
    argument.

    The double reports whatever it was constructed with, so it stands in for a real embedder that
    honours the option."""
    from egpt.memory import embedding

    class Sized(Recorder):
        def __init__(self, dimensions, **rest):
            super().__init__()
            self.dimensions = dimensions

        def _vector(self, text):
            return [0.0] * self.dimensions

    monkeypatch.setattr(embedding, "_construct", lambda key, options: Sized(**options))
    resolved = resolve_memory_embedder(ProviderMode.LOCAL, embedder_options={"dimensions": 512, "id": "x"})
    assert resolved.dimensions == 512
    assert "|d512|" in resolved.embedding_version


def test_a_mode_that_is_not_a_ProviderMode_is_refused():
    """Coerced nowhere: ``config.mode`` is already coerced at the boundary, so a raw string here
    means a caller bypassed it, and Mode A's air-gap check keys on identity."""
    with pytest.raises(MemoryProviderConfigError) as raised:
        resolve_memory_embedder("local", Recorder())
    assert raised.value.field_name == "mode"


# -- criterion 2 (carried forward from SM-C1): Mode A refuses a remote embedder --


def test_huggingface_is_refused_under_mode_A():
    """The named trap: ``HuggingfaceCustomEmbedder`` is a remote HF Inference API client despite
    sitting in the self-hostable list, so selecting it in an air-gapped deployment breaks the air
    gap silently (ADR 0003 A.3c). Refused at config validation, which is what A.3c asks for over
    documenting it."""
    with pytest.raises(MemoryProviderConfigError) as raised:
        resolve_memory_embedder(ProviderMode.LOCAL, "huggingface")
    assert raised.value.field_name == "embedder"
    assert "huggingface" in str(raised.value).lower()


@pytest.mark.parametrize("key", ["openai", "azure_openai", "cohere", "jina", "voyageai"])
def test_every_other_remote_embedder_is_refused_under_mode_A_too(key):
    """The reason this is parametrised over five siblings rather than asserting ``huggingface``
    alone: a check that rejects only the one name in the ADR leaves every equally-remote embedder
    accepted, which is the same defect with a different name in the message. The rule is the
    property A.3c states -- offline capability -- and ``huggingface`` is one instance of it."""
    with pytest.raises(MemoryProviderConfigError) as raised:
        resolve_memory_embedder(ProviderMode.LOCAL, key)
    assert raised.value.field_name == "embedder"


@pytest.mark.parametrize("key", sorted(OFFLINE_CAPABLE))
def test_the_offline_capable_embedders_pass_mode_A(key, monkeypatch):
    """The complement of the refusal. Without this the allow-list could be empty and every test
    above would still pass, which would refuse Mode A entirely."""
    from egpt.memory import embedding

    monkeypatch.setattr(embedding, "_construct", lambda k, o: Recorder())
    assert resolve_memory_embedder(ProviderMode.LOCAL, key).dimensions == DIM


def test_a_remote_embedder_is_not_constructed_before_being_refused(monkeypatch):
    """Refused **before** construction, not after. Building a remote client to then reject it reads
    a token off the environment and can open a connection -- which is the thing the air gap forbids,
    so a refusal that happens afterwards has already done the damage it exists to prevent."""
    from egpt.memory import embedding

    def _explode(key, options):
        raise AssertionError(f"constructed {key!r} before the air-gap check")

    monkeypatch.setattr(embedding, "_construct", _explode)
    with pytest.raises(MemoryProviderConfigError):
        resolve_memory_embedder(ProviderMode.LOCAL, "openai")


def test_an_injected_instance_is_checked_too():
    """Passing an instance is a supported path -- it is how a deployment supplies ``device`` or
    ``trust_remote_code``, which SM-R4 section 5 records ``SentenceTransformerEmbedder`` cannot take
    from the registry. A rule that held only for the spelling naming a key would leave the trap open
    on the path a deployment actually uses."""

    class Remote:
        dimensions = DIM

        def get_embedding(self, text):
            return [0.0] * DIM

        async def async_get_embedding(self, text):
            return [0.0] * DIM

    with pytest.raises(MemoryProviderConfigError) as raised:
        resolve_memory_embedder(ProviderMode.LOCAL, Remote())
    assert raised.value.field_name == "embedder"


def test_an_instance_declaring_itself_offline_is_accepted():
    """A private in-process model outside this tree is genuinely offline and no module path can tell.
    The declaration is explicit, which is the opposite of the silence A.3c actually forbids."""
    assert resolve_memory_embedder(ProviderMode.LOCAL, Recorder()).dimensions == DIM


def test_mode_B_accepts_a_remote_embedder():
    """Mode B is the deployment where reaching a provider is the point. A check that fired in both
    modes would be a check nothing could satisfy."""

    class Remote(Recorder):
        offline_capable = False

    assert resolve_memory_embedder(ProviderMode.FRONTIER, Remote()).dimensions == DIM


# -- criterion 1: the same embedder serves write and read ----------------------


def test_write_and_query_go_through_the_one_resolved_embedder():
    """SM-R4 decision 2. Vectors from two models are not comparable, and the failure is silent --
    nothing raises, retrieval just gets worse -- so the seam is that both call the same object."""
    recorder = Recorder()
    resolved = pipeline(recorder)
    resolved.embed_texts(["stored one", "stored two"])
    resolved.embed_query("a question")
    assert recorder.calls == ["stored one", "stored two", "a question"]


def test_the_async_half_uses_the_same_embedder_as_the_sync_half():
    recorder = Recorder()
    resolved = pipeline(recorder)
    asyncio.run(resolved.aembed_texts(["stored"]))
    asyncio.run(resolved.aembed_query("asked"))
    assert recorder.calls == ["stored", "asked"]


def test_a_query_vector_equals_the_write_vector_for_the_same_text():
    """The property that makes similarity meaningful: one text, one vector, whichever path took it."""
    resolved = pipeline()
    assert resolved.embed_query("same text") == resolved.embed_texts(["same text"])[0]


def test_the_two_halves_agree_vector_for_vector():
    """Asserted on the values, not on both paths merely returning something the right shape."""
    resolved = pipeline()
    assert asyncio.run(resolved.aembed_texts(["a", "bb"])) == resolved.embed_texts(["a", "bb"])


# -- criterion 4: batch embedding on write-back --------------------------------


def test_a_batching_embedder_is_called_once_per_chunk_not_once_per_record():
    """The criterion, asserted on what the embedder was **asked for** rather than on the vectors
    that came back -- which are identical either way, so a per-record loop would pass a
    result-only assertion while making one API call per memory."""
    recorder = BatchRecorder()
    pipeline(recorder).embed_texts(["a", "b", "c", "d", "e"])
    assert recorder.calls == [["a", "b"], ["c", "d"], ["e"]]


def test_the_async_half_batches_identically():
    """The one that matters most: SM-V1's async upsert embedded sequentially and said so, so a
    batching sync half beside a per-record async half is the exact divergence to expect here."""
    recorder = BatchRecorder()
    asyncio.run(pipeline(recorder).aembed_texts(["a", "b", "c", "d", "e"]))
    assert recorder.calls == [["a", "b"], ["c", "d"], ["e"]]


def test_an_embedder_that_did_not_declare_batching_gets_one_text_per_call():
    """Undeclared means per-text. Handing such an embedder a list gets one vector for the
    concatenation -- silently wrong, and attached to whichever record happened to be first."""
    recorder = Recorder()
    pipeline(recorder).embed_texts(["a", "b", "c"])
    assert recorder.calls == ["a", "b", "c"]


def test_batching_is_not_read_from_enable_batch():
    """``enable_batch`` gates a *separate* async batch API everywhere else in this tree -- every
    vectordb reads it as ``enable_batch and hasattr(..., 'async_get_embeddings_batch_and_usage')``.
    ``AzureOpenAIEmbedder`` carries it while its ``get_embedding`` still takes one string and
    returns ``data[0].embedding``, so a pipeline reading the flag would send Mode B a list and get
    one vector for N texts.

    ``Recorder`` is that shape: ``enable_batch = True``, no ``accepts_text_batches``, and a
    ``get_embedding`` that refuses a list. Reverting the pipeline to the flag raises ``TypeError``
    here."""
    recorder = Recorder()
    assert recorder.enable_batch is True
    pipeline(recorder).embed_texts(["a", "b", "c"])
    assert recorder.calls == ["a", "b", "c"]


def test_the_mode_a_default_embedder_declares_the_capability_so_criterion_4_is_reachable():
    """Criterion 4 is 'batch embedding on write-back', and it is only met if the embedder a default
    Mode A deployment actually gets declares the capability the pipeline reads. Asserted on the
    shipped class rather than on a double, because a double declaring it proves nothing about what
    a deployment runs -- which is exactly how this shipped unreachable."""
    from egpt.integrations.knowledge.embedder.sentence_transformer import SentenceTransformerEmbedder

    assert SentenceTransformerEmbedder.accepts_text_batches is True


def test_the_mode_b_default_embedder_declares_the_capability_too():
    """Round-2 review finding: the ticket's AC4 has no Mode-only qualifier, and this was unmet for
    Mode B -- ``AzureOpenAIEmbedder`` had no capability declared and no way to accept a list. Fixed
    in ``azure_openai.py`` alongside this test, mirroring the Mode A shape rather than scoping the
    criterion down to exclude Mode B."""
    from egpt.integrations.knowledge.embedder.azure_openai import AzureOpenAIEmbedder

    assert AzureOpenAIEmbedder.accepts_text_batches is True


def test_a_batching_embedder_gets_a_list_even_for_a_single_text():
    """Every ``embed_query`` is one text. Unwrapping by chunk length rather than by declared shape
    sent a bare string to a batch-only embedder on every read while writes batched fine -- so the
    query path failed for exactly the embedders the write path worked for."""
    recorder = BatchRecorder()
    pipeline(recorder).embed_query("alone")
    assert recorder.calls == [["alone"]]


def test_the_async_query_path_uses_the_same_call_shape():
    recorder = BatchRecorder()
    asyncio.run(pipeline(recorder).aembed_query("alone"))
    assert recorder.calls == [["alone"]]


def test_the_batch_comes_back_in_order():
    """Order is the whole contract: vectors are zipped against records positionally, so a shuffled
    batch files every memory under its neighbour's vector with nothing downstream able to see it."""
    assert pipeline(BatchRecorder()).embed_texts(["a", "bb", "cccc"]) == [
        [1.0, 0.0, 0.0, 0.0],
        [2.0, 0.0, 0.0, 0.0],
        [4.0, 0.0, 0.0, 0.0],
    ]


def test_an_embedder_returning_the_wrong_count_for_a_batch_is_refused():
    """A batching embedder that returns one vector for the concatenation is the failure this guards.
    Left unchecked, ``zip`` truncates silently and the surviving records get the wrong vectors."""

    class Miscounting(BatchRecorder):
        def get_embedding(self, text):
            return [self._vector("only one")]

    with pytest.raises(MemoryProviderConfigError) as raised:
        pipeline(Miscounting()).embed_texts(["a", "b"])
    assert raised.value.field_name == "embedder"


def test_a_2d_numpy_return_is_read_as_a_batch_not_as_one_wide_vector():
    """A numpy row is an ``ndarray`` -- neither ``list`` nor ``tuple`` -- so an isinstance-only test
    read N vectors as one very wide one. And the guard never got that far: ``if result`` on a 2D
    array raises numpy's ``truth value ... is ambiguous`` first, escaping a seam whose refusals are
    ``MemoryProviderConfigError``. Both shipped embedders call ``.tolist()``; a custom one need
    not."""
    numpy = pytest.importorskip("numpy", reason="the 2D-array shape under test is numpy's")

    class NumpyBatch(BatchRecorder):
        def get_embedding(self, text):
            return numpy.zeros((len(list(text)), DIM))

    assert pipeline(NumpyBatch()).embed_texts(["a", "b", "c"]) == [[0.0] * DIM] * 3


def test_an_embedder_returning_none_is_refused_as_a_config_error_not_a_typeerror():
    """An embedder swallowing its own failure returns ``None``. Iterating it raised a bare
    ``TypeError`` through a seam that documents ``MemoryProviderConfigError``, so a caller narrowing
    to this seam's type never saw it."""

    class ReturnsNone(Recorder):
        def get_embedding(self, text):
            return None

    with pytest.raises(MemoryProviderConfigError) as raised:
        pipeline(ReturnsNone()).embed_texts(["a"])
    assert raised.value.field_name == "embedder"


def test_an_embedder_returning_a_lengthless_object_is_refused():
    """The sibling of the ``None`` case: anything with no ``len`` is neither a vector nor a batch of
    them, and reaching ``result[0]`` on it raises whatever that object raises."""

    class Lengthless(Recorder):
        def get_embedding(self, text):
            return object()

    with pytest.raises(MemoryProviderConfigError) as raised:
        pipeline(Lengthless()).embed_texts(["a"])
    assert raised.value.field_name == "embedder"


def test_an_empty_batch_costs_no_call():
    recorder = Recorder()
    assert pipeline(recorder).embed_texts([]) == []
    assert recorder.calls == []


def test_a_batch_size_of_zero_does_not_spin():
    """A non-positive ``batch_size`` makes the chunk loop either spin forever or send everything at
    once. Falling back is what stops a misconfigured embedder from hanging a write."""

    class ZeroBatch(BatchRecorder):
        batch_size = 0

    assert len(pipeline(ZeroBatch()).embed_texts(["a", "b", "c"])) == 3


def test_the_batch_size_fallback_logs_a_warning(monkeypatch):
    """The fallback above is silent to a caller with nothing watching stderr. A batching embedder
    with a bad ``batch_size`` degrading to per-text calls with no observable signal is a real
    deployment surprise -- pinned here so the log is not quietly dropped later."""
    from egpt.memory import embedding

    class ZeroBatch(BatchRecorder):
        batch_size = 0

    seen = []
    monkeypatch.setattr(embedding, "log_warning", lambda msg: seen.append(msg))
    resolved = pipeline(ZeroBatch())
    assert resolved._batch_size() == 1
    assert len(seen) == 1
    assert "ZeroBatch" in seen[0]


def test_a_non_batching_embedders_batch_size_fallback_logs_nothing():
    """The log is conditioned on ``_accepts_batches()``: a non-batching embedder is always one text
    per call regardless of ``batch_size``, so a log there would fire on every deployment that never
    declared the capability at all -- noise, not a signal. ``_batch_size`` is never even reached
    through ``_chunks`` for such an embedder; called directly here to test the guard in isolation."""
    from egpt.memory import embedding

    class ZeroBatchNonBatching(Recorder):
        batch_size = 0

    seen = []
    embedding_module_warning = embedding.log_warning
    try:
        embedding.log_warning = lambda msg: seen.append(msg)
        resolved = pipeline(ZeroBatchNonBatching())
        assert resolved._batch_size() == 1
    finally:
        embedding.log_warning = embedding_module_warning
    assert seen == []


# -- criterion 3: the dimension matches the vector store -----------------------


def test_an_unstated_dimension_is_taken_from_the_embedder():
    assert pipeline().dimensions == DIM


def test_a_stated_dimension_disagreeing_with_the_embedder_is_refused():
    """Caught here rather than by Postgres on the first insert: by then the table exists at the
    wrong width and every write fails until it is dropped."""
    with pytest.raises(MemoryProviderConfigError) as raised:
        pipeline(dimensions=DIM + 1)
    assert raised.value.field_name == "dimensions"


def test_an_embedder_declaring_no_dimension_needs_an_explicit_one():
    class NoDim(Recorder):
        dimensions = None

    with pytest.raises(MemoryProviderConfigError) as raised:
        resolve_memory_embedder(ProviderMode.FRONTIER, NoDim())
    assert raised.value.field_name == "dimensions"


@pytest.mark.parametrize("bad", [0, -1, True, 3.5, "4"])
def test_a_dimension_that_is_not_a_positive_integer_is_refused(bad):
    """``True`` is in the list because ``bool`` is an ``int`` subclass, so ``dimensions=True``
    otherwise builds a ``vector(1)`` column."""

    class NoDim(Recorder):
        dimensions = None

    with pytest.raises(MemoryProviderConfigError) as raised:
        resolve_memory_embedder(ProviderMode.FRONTIER, NoDim(), dimensions=bad)
    assert raised.value.field_name == "dimensions"


def test_the_refusal_names_the_field_the_caller_actually_passed():
    """The vector store spells this ``dim``. A caller who passed ``dim=`` and is told ``dimensions``
    cannot map the refusal back to the argument that caused it."""
    with pytest.raises(MemoryProviderConfigError) as raised:
        pipeline(dimensions=DIM + 1, dimensions_field="dim")
    assert raised.value.field_name == "dim"


def test_a_vector_of_the_wrong_width_is_refused_at_the_pipeline():
    """An embedder whose ``dimensions`` disagrees with what it actually returns otherwise reaches
    Postgres as ``expected 4 dimensions, not 2`` -- naming the column instead of the model."""

    class Lying(Recorder):
        def _vector(self, text):
            return [0.0, 0.0]

    with pytest.raises(MemoryProviderConfigError) as raised:
        pipeline(Lying()).embed_texts(["a"])
    assert raised.value.field_name == "dimensions"


def test_the_async_half_refuses_a_wrong_width_too():
    class Lying(Recorder):
        def _vector(self, text):
            return [0.0, 0.0]

    with pytest.raises(MemoryProviderConfigError):
        asyncio.run(pipeline(Lying()).aembed_texts(["a"]))


def test_a_wrong_width_batch_is_refused_after_its_own_chunk_not_after_the_whole_call():
    """Checked per chunk, not once over the accumulated batch -- a wrong-dimension embedder is
    refused as soon as its own first chunk comes back, not after every later chunk has also paid
    for a call whose result is then discarded. Two chunks of size 2 (``batch_size``); the second
    embedder call must never happen."""

    class LyingAfterFirstChunk(BatchRecorder):
        def get_embedding(self, text):
            if isinstance(text, str):
                raise TypeError("batch-only")
            self.calls.append(list(text))
            return [[0.0, 0.0] for _ in text]  # wrong width on every call, including the first

    recorder = LyingAfterFirstChunk()
    with pytest.raises(MemoryProviderConfigError) as raised:
        pipeline(recorder).embed_texts(["a", "b", "c", "d"])
    assert raised.value.field_name == "dimensions"
    assert len(recorder.calls) == 1  # refused after the first chunk, never asked for the second


def test_the_async_half_is_also_checked_per_chunk():
    class LyingAfterFirstChunk(BatchRecorder):
        def get_embedding(self, text):
            if isinstance(text, str):
                raise TypeError("batch-only")
            self.calls.append(list(text))
            return [[0.0, 0.0] for _ in text]

    recorder = LyingAfterFirstChunk()
    with pytest.raises(MemoryProviderConfigError):
        asyncio.run(pipeline(recorder).aembed_texts(["a", "b", "c", "d"]))
    assert len(recorder.calls) == 1


# -- the version tag (D8) ------------------------------------------------------


def test_the_version_tag_carries_model_dimension_metric_and_revision():
    """SM-R4 section 7's shape. It is what a re-embedding migration keys its cutover on, so a tag
    missing the dimension cannot distinguish the two columns a migration runs across."""
    assert pipeline().embedding_version == "recorder-v1|d4|cos|v1"


def test_the_tag_splits_into_four_fields_for_a_huggingface_model_id():
    """The tag is a parsed value, and every Mode A id is ``org/name`` -- so a ``/`` separator gives
    five fields for the one model family this ticket actually ships, and a migration splitting it
    reads ``'bge-base-en-v1.5'`` as the dimension. Asserted on the id that caused it rather than on
    the double's flat ``recorder-v1``, which cannot fail this way and so cannot catch it."""

    class HuggingFaceId(Recorder):
        id = "BAAI/bge-base-en-v1.5"

    tag = resolve_memory_embedder(ProviderMode.FRONTIER, HuggingFaceId()).embedding_version
    assert tag.split("|") == ["BAAI/bge-base-en-v1.5", "d4", "cos", "v1"]


def test_a_model_id_containing_the_separator_is_refused_not_silently_joined():
    """Closing the class rather than the case: the tag parses into four fields only while no field
    contains the separator. Refused at construction, since a mis-split tag makes a migration
    mis-key rather than fail -- it reads some other field as the dimension and proceeds."""

    class Pipey(Recorder):
        id = "org|weird/name"

    with pytest.raises(MemoryProviderConfigError) as caught:
        resolve_memory_embedder(ProviderMode.FRONTIER, Pipey())
    assert caught.value.field_name == "embedder"


def test_the_tag_moves_with_the_dimension():
    """Derived, not restated. A tag written beside the dimension rather than from it drifts the
    first time the model changes, and the migration keyed on it then cuts over to itself."""

    class Wide(Recorder):
        dimensions = 768

    assert "|d768|" in resolve_memory_embedder(ProviderMode.FRONTIER, Wide()).embedding_version


def test_an_embedder_without_an_id_is_tagged_by_its_class_not_a_shared_placeholder():
    """Two different embedders must not tag their vectors alike -- a migration would then read one
    model's vectors as already converted."""

    class Anonymous(Recorder):
        id = None

    assert resolve_memory_embedder(ProviderMode.FRONTIER, Anonymous()).embedding_version.startswith("Anonymous|")


# -- resolution refusals -------------------------------------------------------


def test_an_unknown_embedder_name_is_refused_as_a_config_fault():
    """Checked against the registry rather than caught from ``construct``: that signals an unknown
    name with ``ValueError``, and so does any embedder validating its own constructor arguments, so
    catching would relabel a bad ``device`` as a bad embedder name."""
    with pytest.raises(MemoryProviderConfigError) as raised:
        resolve_memory_embedder(ProviderMode.FRONTIER, "not-an-embedder")
    assert raised.value.field_name == "embedder"


def test_options_beside_a_built_instance_are_refused():
    """Silently dropping them leaves a deployment's ``device`` or ``trust_remote_code`` inert with
    nothing to show for it."""
    with pytest.raises(MemoryProviderConfigError) as raised:
        resolve_memory_embedder(ProviderMode.FRONTIER, Recorder(), embedder_options={"id": "x"})
    assert raised.value.field_name == "embedder_options"


def test_the_parameter_name_matches_its_own_error_message():
    """The refusal above names ``embedder_options`` -- so does the parameter, not a shorter
    ``options`` a caller reading that message would not find. A keyword-only call with the wrong
    name raises ``TypeError`` from Python itself, which is the regression this pins."""
    import inspect

    params = inspect.signature(resolve_memory_embedder).parameters
    assert "embedder_options" in params
    assert "options" not in params


def test_an_empty_embedder_name_is_refused():
    with pytest.raises(MemoryProviderConfigError) as raised:
        resolve_memory_embedder(ProviderMode.FRONTIER, "   ")
    assert raised.value.field_name == "embedder"


@pytest.mark.parametrize("missing", ["get_embedding", "async_get_embedding"])
def test_an_embedder_missing_either_half_is_refused(missing):
    """Both halves of the memory path embed, so one missing leaves the other half broken -- and it
    surfaces on the first async read rather than at construction."""

    class Half(Recorder):
        pass

    setattr(Half, missing, None)
    with pytest.raises(MemoryProviderConfigError) as raised:
        resolve_memory_embedder(ProviderMode.FRONTIER, Half())
    assert raised.value.field_name == "embedder"


def test_a_construction_failure_arrives_as_a_config_error(monkeypatch):
    """Callers of ``build_memory_provider`` assert on ``MemoryProviderConfigError``; an embedder's
    raw ``RuntimeError`` would make the seam's contract depend on which model was named."""
    from egpt.registry import discovery

    monkeypatch.setattr(discovery, "names", lambda kind: ["ollama"])

    def _boom(kind, name, **config):
        raise RuntimeError("no such model on the server")

    monkeypatch.setattr(discovery, "construct", _boom)
    with pytest.raises(MemoryProviderConfigError) as raised:
        resolve_memory_embedder(ProviderMode.LOCAL, "ollama")
    assert raised.value.field_name == "embedder"


def test_a_missing_sdk_propagates_as_an_ImportError(monkeypatch):
    """The embedder's own "pip install sentence-transformers" is more useful than anything this
    frame could add, and re-labelling it a config fault sends an operator to the wrong file."""
    from egpt.registry import discovery

    monkeypatch.setattr(discovery, "names", lambda kind: ["ollama"])

    def _boom(kind, name, **config):
        raise ImportError("`ollama` not installed")

    monkeypatch.setattr(discovery, "construct", _boom)
    with pytest.raises(ImportError):
        resolve_memory_embedder(ProviderMode.LOCAL, "ollama")


def test_the_wrapper_is_constructible_directly():
    """``MemoryEmbedder`` is public: SM-V4's Qdrant substrate (#98) takes one rather than
    re-resolving, so it must be usable without going through the resolver."""
    assert MemoryEmbedder(embedder=Recorder(), dimensions=DIM, embedding_version="x|d4|cos|v1").embed_query("a")
