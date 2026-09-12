"""Boundary validation, row mapping, and request-shape proof for the Azure AI Search memory
substrate (AZ-B, migration plan gap #3).

**No live Azure AI Search service anywhere in this file.** The environment this substrate was
built in has no Azure CLI, no subscription, and no reachable Azure AI Search resource -- see
``cookbook/95_azure_migration/TEST_LOG.md`` for the explicit list of what that means is and is not
proven. What is checked here, entirely against a mocked SDK, mirrors what
``test_pgvector_memory_substrate.py`` checks without a database: everything the substrate decides
*before* a request is built (construction refusals, document shape, filter shape) and *after* a
response is mapped back (row -> ``MemoryRecord``). The security-critical property -- that a
:class:`~egpt.memory.types.Scope` reaches every request as a real ``$filter`` clause, and that a
narrower or differently-scoped call cannot see another tenant's clause -- is asserted directly on
the ``filter=`` keyword argument the mocked ``SearchClient.search`` receives, which is the actual
request shape this backend would send to a real service.

``azure-search-documents`` is a hard dependency of this test file (imported transitively through
the substrate), pinned via the ``azure-ai-search`` extra in ``libs/agno/pyproject.toml`` --
gated with ``pytest.importorskip`` per this repo's optional-dependency convention, so the
``unit-drivers-absent`` job (which does not install it) skips rather than fails.
"""

import asyncio
import math
from hashlib import blake2b

import pytest

azure_search_documents = pytest.importorskip(
    "azure.search.documents", reason="azure-search-documents is an optional integration dependency"
)

from unittest.mock import AsyncMock, MagicMock, patch  # noqa: E402

from egpt.integrations.memory.vector.azure_ai_search import AzureAISearchMemorySubstrate  # noqa: E402
from egpt.integrations.memory.vector.azure_ai_search.azure_ai_search import (  # noqa: E402
    MAX_VECTOR_DIM,
    _document_key,
    _is_retryable,
    _lexical_similarity,
)
from egpt.memory.config import MemoryProviderConfigError  # noqa: E402
from egpt.memory.substrates import HybridSearchSubstrate, VectorSubstrate  # noqa: E402
from egpt.memory.tiers import MemoryTier  # noqa: E402
from egpt.memory.types import MemoryOperationError, MemoryQuery, MemoryRecord, Scope  # noqa: E402
from egpt.providers.model_provider import ProviderMode  # noqa: E402

DIM = 8
ENDPOINT = "https://example.search.windows.net"
ACME = Scope(tenant_id="acme", user_id="ada")
GLOBEX = Scope(tenant_id="globex", user_id="wile")


class StubEmbedder:
    """Deterministic, dimension-declaring, and async-capable -- matches
    ``test_pgvector_memory_substrate.py``'s own stub exactly, so the two backends' tests are
    checking the same embedder contract."""

    dimensions = DIM
    offline_capable = True

    def get_embedding(self, text):
        vector = [0.0] * DIM
        for token in text.lower().split():
            vector[int.from_bytes(blake2b(token.encode(), digest_size=4).digest(), "big") % DIM] += 1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    async def async_get_embedding(self, text):
        await asyncio.sleep(0)
        return self.get_embedding(text)


class _WideEmbedder(StubEmbedder):
    dimensions = MAX_VECTOR_DIM + 1


class _Response:
    """The minimum ``HttpResponseError`` needs to populate ``status_code`` -- azure-core reads the
    status, the reason and ``text()`` off whatever is passed as ``response=``. A ``MagicMock`` here
    would work but puts its own ``repr`` into the exception's message, which several assertions
    below read."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.reason = "stub"

    def text(self, encoding=None) -> str:
        return ""


def http_error(message: str, status_code):
    """An ``HttpResponseError`` as the SDK would raise it, carrying a status code. ``None`` builds
    the status-less form azure-core produces when it never saw a response."""
    from azure.core.exceptions import HttpResponseError

    if status_code is None:
        return HttpResponseError(message)
    return HttpResponseError(message, response=_Response(status_code))


class _IndexingResult:
    """One entry of ``merge_or_upload_documents``' return value. The real
    ``azure.search.documents.models.IndexingResult`` is a generated model with a read-only
    ``succeeded``; only the four attributes this backend reads are needed here."""

    def __init__(self, key, succeeded, error_message=None, status_code=200):
        self.key = key
        self.succeeded = succeeded
        self.error_message = error_message
        self.status_code = status_code


def record(id_="e1", content="a memory", scope=ACME, **kwargs):
    return MemoryRecord(id=id_, tier=MemoryTier.EPISODIC, content=content, scope=scope, **kwargs)


def hit(id_="e1", content="hello world", scope=ACME, score=0.9, **kwargs):
    """A row as the SDK's ``search()`` would hand it back: a plain dict carrying ``@search.score``
    alongside the stored fields, matching what ``azure.search.documents.SearchItemPaged`` yields."""
    base = {
        "@search.score": score,
        "id": id_,
        "tier": "episodic",
        "content": content,
        "tenant_id": scope.tenant_id,
        "agent_id": scope.agent_id,
        "user_id": scope.user_id,
        "team_id": scope.team_id,
        "session_id": scope.session_id,
        "topics_json": None,
        "provenance_json": None,
        "metadata_json": None,
        "embedding_version": "stub",
        "t_valid": None,
        "t_invalid": None,
        "created_at": 100,
        "expired_at": None,
        "superseded_by": None,
    }
    base.update(kwargs)
    return base


@pytest.fixture
def substrate():
    """A substrate that never reaches a network: schema init is mocked at construction, and every
    read/write test below mocks the specific client method it exercises."""
    with patch("azure.search.documents.indexes.SearchIndexClient.create_or_update_index") as create:
        create.return_value = MagicMock()
        return AzureAISearchMemorySubstrate(
            endpoint=ENDPOINT, api_key="fake-key", index_name="egpt-memory-test", embedder=StubEmbedder()
        )


# -- the ports ------------------------------------------------------------------


def test_the_class_carries_every_vector_substrate_method():
    expected = sorted(name for name in vars(VectorSubstrate) if not name.startswith("_"))
    missing = [name for name in expected if not callable(getattr(AzureAISearchMemorySubstrate, name, None))]
    assert missing == []


def test_the_class_carries_every_hybrid_search_substrate_method():
    expected = sorted(name for name in vars(HybridSearchSubstrate) if not name.startswith("_"))
    missing = [name for name in expected if not callable(getattr(AzureAISearchMemorySubstrate, name, None))]
    assert missing == []


def test_the_class_satisfies_both_ports_structurally(substrate):
    assert isinstance(substrate, VectorSubstrate)
    assert isinstance(substrate, HybridSearchSubstrate)


# -- construction refusals -------------------------------------------------------


def test_a_missing_endpoint_is_refused(monkeypatch):
    monkeypatch.delenv("EGPT_MEMORY_AZURE_SEARCH_ENDPOINT", raising=False)
    with pytest.raises(MemoryProviderConfigError, match="endpoint"):
        AzureAISearchMemorySubstrate(endpoint=None, api_key="k", embedder=StubEmbedder())


def test_a_missing_credential_is_refused(monkeypatch):
    monkeypatch.delenv("EGPT_MEMORY_AZURE_SEARCH_API_KEY", raising=False)
    with pytest.raises(MemoryProviderConfigError, match="credential"):
        AzureAISearchMemorySubstrate(endpoint=ENDPOINT, api_key=None, embedder=StubEmbedder())


@pytest.mark.parametrize("name", ["Bad_Name", "-startswithdash", "a", "UPPER"])
def test_a_bad_index_name_is_refused(name):
    with pytest.raises(MemoryProviderConfigError, match="index_name"):
        AzureAISearchMemorySubstrate(endpoint=ENDPOINT, api_key="k", index_name=name, embedder=StubEmbedder())


@pytest.mark.parametrize(
    "setting, field",
    [
        ({"m": 1}, "m"),
        ({"m": 11}, "m"),
        ({"ef_construction": 50}, "ef_construction"),
        ({"ef_search": 5000}, "ef_search"),
    ],
)
def test_hnsw_params_outside_azures_own_bounds_are_refused(setting, field):
    """``create_or_update_index`` is mocked even though ``_checked_hnsw_params`` is expected to
    raise before ``init_schema`` is reached: without the mock, a validator regression that let a bad
    value through would leave this test making a real network call and failing with whatever the
    sandbox does to an outbound connection, instead of failing cleanly on the assertion. Matches the
    ``substrate`` fixture and every sibling refusal test."""
    with patch("azure.search.documents.indexes.SearchIndexClient.create_or_update_index") as create:
        create.return_value = MagicMock()
        with pytest.raises(MemoryProviderConfigError) as raised:
            AzureAISearchMemorySubstrate(endpoint=ENDPOINT, api_key="k", embedder=StubEmbedder(), **setting)
    assert raised.value.field_name == field


def test_a_dimension_above_azures_own_ceiling_is_refused():
    with pytest.raises(MemoryProviderConfigError, match="dim"):
        AzureAISearchMemorySubstrate(endpoint=ENDPOINT, api_key="k", embedder=_WideEmbedder())


@pytest.mark.parametrize("status", [400, 409])
def test_a_rejected_index_definition_is_translated_as_a_configuration_error(status):
    """A rejected ``create_or_update_index`` (Azure refuses in-place vector-dimension changes) must
    surface as ``MemoryProviderConfigError`` naming ``dim``, not as a bare SDK exception three
    layers from the setting that caused it -- matches
    ``PgVectorMemorySubstrate``'s own ``_reject_schema_drift`` translation."""
    with patch("azure.search.documents.indexes.SearchIndexClient.create_or_update_index") as create:
        create.side_effect = http_error("cannot change vector_search_dimensions", status)
        with pytest.raises(MemoryProviderConfigError) as raised:
            AzureAISearchMemorySubstrate(endpoint=ENDPOINT, api_key="k", embedder=StubEmbedder())
    assert raised.value.field_name == "dim"


@pytest.mark.parametrize("status", [401, 403, 503, None])
def test_a_non_conflict_failure_during_schema_init_is_not_reported_as_schema_drift(status):
    """An auth failure, an outage, or a status-less transport error on the DDL call is not drift.
    Reporting it as drift sends an operator off to plan a new-index-and-re-embed migration for what
    is a wrong key or a bad five minutes -- so only 400/409 reach ``_reject_schema_drift`` and
    everything else is classified by :meth:`_translated`'s retryability rule like any other call."""
    with patch("azure.search.documents.indexes.SearchIndexClient.create_or_update_index") as create:
        create.side_effect = http_error("Forbidden", status)
        with pytest.raises(BaseException) as raised:
            AzureAISearchMemorySubstrate(endpoint=ENDPOINT, api_key="k", embedder=StubEmbedder())
    assert not isinstance(raised.value, MemoryProviderConfigError)
    assert "migration" not in str(raised.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status, expected",
    [(400, MemoryProviderConfigError), (403, MemoryOperationError)],
)
async def test_ainit_schema_classifies_a_failure_exactly_as_init_schema_does(status, expected):
    """The async twin of the two tests above, on the async index client -- the classification lives
    in both ``init_schema`` and ``ainit_schema``, so a fix applied to one only would leave the async
    deployment misreporting auth failures as drift."""
    with patch("azure.search.documents.indexes.SearchIndexClient.create_or_update_index") as create:
        create.return_value = MagicMock()
        substrate = AzureAISearchMemorySubstrate(endpoint=ENDPOINT, api_key="k", embedder=StubEmbedder())
    with patch(
        "azure.search.documents.indexes.aio.SearchIndexClient.create_or_update_index", new_callable=AsyncMock
    ) as create:
        create.side_effect = http_error("refused", status)
        with pytest.raises(expected):
            await substrate.ainit_schema()
    await substrate.aclose()


# -- retryability of a service failure -------------------------------------------


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504, None])
def test_a_retryable_http_failure_propagates_unconverted(substrate, status):
    """``MemoryOperationError`` is read as permanent by ``co_located._raise_write_failure``, so a
    throttle or a transient 5xx must not be dressed up as one -- the caller would abandon a write
    over a rate limit. ``HttpResponseError`` is azure-core's base class for every status alike, so
    this is decided on the status code, not the exception type."""
    from azure.core.exceptions import HttpResponseError

    error = http_error("throttled", status)
    assert _is_retryable(error) is True
    with patch("azure.search.documents.SearchClient.search") as se:
        se.side_effect = error
        with pytest.raises(HttpResponseError):
            substrate.search(ACME, MemoryQuery(text="hello", limit=5))


@pytest.mark.parametrize("status", [400, 403, 404, 409, 413])
def test_a_deterministic_http_failure_becomes_a_memory_operation_error(substrate, status):
    error = http_error("Invalid expression", status)
    assert _is_retryable(error) is False
    with patch("azure.search.documents.SearchClient.search") as se:
        se.side_effect = error
        with pytest.raises(MemoryOperationError, match="search"):
            substrate.search(ACME, MemoryQuery(text="hello", limit=5))


@pytest.mark.asyncio
@pytest.mark.parametrize("status, expected", [(429, "retryable"), (400, "permanent")])
async def test_the_async_path_splits_retryability_the_same_way(substrate, status, expected):
    from azure.core.exceptions import HttpResponseError

    with patch("azure.search.documents.aio.SearchClient.search", new_callable=AsyncMock) as se:
        se.side_effect = http_error("refused", status)
        with pytest.raises(HttpResponseError if expected == "retryable" else MemoryOperationError):
            await substrate.asearch(ACME, MemoryQuery(text="hello", limit=5))
    await substrate.aclose()


# -- document key -----------------------------------------------------------------


def test_document_key_is_deterministic():
    assert _document_key("acme", "e1") == _document_key("acme", "e1")


def test_document_key_differs_across_tenants_for_the_same_id():
    """The property that makes one shared index safe for per-tenant ``id`` reuse: two tenants
    minting the same record id must not collide onto the same Azure document."""
    assert _document_key("acme", "e1") != _document_key("globex", "e1")


#: Component values built to collide under a plain ``f"{tenant_id}|{id}"`` join: every one either
#: contains the separator, or contains the ``digits + ":"`` shape the length prefix itself uses, or
#: is a boundary case (empty, a lone separator) -- the inputs the three original key tests never
#: had. ``Scope.__post_init__`` rejects none of these in ``tenant_id``, and nothing anywhere
#: constrains ``MemoryRecord.id``.
_ADVERSARIAL_COMPONENTS = [
    "a",
    "ab",
    "a|b",
    "|",
    "||",
    "a|",
    "|a",
    "",
    "1:a",
    "3:abc",
    "0:",
    "2:ab|c",
    "acme",
    "acme|x",
]


def test_document_key_is_injective_over_adversarial_separator_inputs():
    """The property the whole shared-index design rests on: distinct ``(tenant_id, id)`` pairs get
    distinct Azure document keys, *including* when either component contains the separator.

    Not a spot check -- every pair drawn from a component set chosen to break the join is enumerated
    and the resulting keys counted. Before the length prefix this failed at the first colliding
    pair: ``("acme", "x|e1")`` and ``("acme|x", "e1")`` both joined to ``acme|x|e1`` and so shared
    one document key, and because ``upsert`` writes through ``merge_or_upload_documents`` -- a
    merge, not an insert-or-fail -- one tenant's write would have merged its fields into another
    tenant's document in place.
    """
    pairs = [(tenant_id, id_) for tenant_id in _ADVERSARIAL_COMPONENTS for id_ in _ADVERSARIAL_COMPONENTS]
    keys = {}
    for pair in pairs:
        key = _document_key(*pair)
        assert key not in keys, f"{pair} collides with {keys[key]} on document key {key}"
        keys[key] = pair
    assert len(keys) == len(pairs)


def test_the_specific_reported_cross_tenant_collision_is_closed():
    """The exact pair from the review, kept as its own named test so a future key-format change
    cannot quietly reopen it behind the enumeration above."""
    assert _document_key("acme", "x|e1") != _document_key("acme|x", "e1")


def test_document_key_is_azures_allowed_key_charset():
    import re

    for tenant_id in _ADVERSARIAL_COMPONENTS:
        key = _document_key(tenant_id, "some/id with spaces?and=punct'uation|and|separators")
        assert re.fullmatch(r"[A-Za-z0-9_=-]+", key)


# -- foreign-scope refusal --------------------------------------------------------


def test_a_record_under_a_foreign_scope_is_refused_by_slot_name_only(substrate):
    foreign = record(scope=Scope(tenant_id="acme", user_id="bob"))
    with pytest.raises(MemoryOperationError) as caught:
        substrate._checked(ACME, [foreign])
    assert "user_id" in str(caught.value)
    assert "bob" not in str(caught.value) and "ada" not in str(caught.value)


def test_the_whole_batch_is_refused_before_anything_is_embedded(substrate):
    calls = []
    substrate.embedder.get_embedding = lambda text: calls.append(text) or [0.0] * DIM
    with pytest.raises(MemoryOperationError):
        substrate.upsert(ACME, [record("e1"), record("e2", scope=GLOBEX)])
    assert calls == []


# -- upsert: document shape -------------------------------------------------------


def test_upsert_writes_the_documented_shape(substrate):
    rec = record(id_="e1", content="hello world", scope=ACME, topics=["a", "b"])
    with patch("azure.search.documents.SearchClient.merge_or_upload_documents") as up:
        up.return_value = MagicMock()
        substrate.upsert(ACME, [rec])
    up.assert_called_once()
    documents = up.call_args.kwargs["documents"]
    assert len(documents) == 1
    doc = documents[0]
    assert doc["doc_key"] == _document_key("acme", "e1")
    assert doc["id"] == "e1"
    assert doc["tenant_id"] == "acme"
    assert doc["user_id"] == "ada"
    assert doc["agent_id"] is None
    assert doc["content"] == "hello world"
    assert doc["tier"] == "episodic"
    assert doc["topics_json"] == '["a", "b"]'
    assert len(doc["embedding"]) == DIM


def test_upsert_refuses_a_partially_failed_batch_naming_the_record(substrate):
    """Azure's batch indexing API is non-atomic: a per-document rejection comes back inside an
    otherwise successful response as ``succeeded=False``, and only a whole-request failure raises.
    Discarding that return value reported a silently dropped record as a landed write."""
    rec_ok, rec_bad = record(id_="e1"), record(id_="e2")
    with patch("azure.search.documents.SearchClient.merge_or_upload_documents") as up:
        up.return_value = [
            _IndexingResult(_document_key("acme", "e1"), True),
            _IndexingResult(_document_key("acme", "e2"), False, error_message="field too large", status_code=413),
        ]
        with pytest.raises(MemoryOperationError) as raised:
            substrate.upsert(ACME, [rec_ok, rec_bad])
    message = str(raised.value)
    assert "'e2'" in message and "field too large" in message
    assert "'e1'" not in message  # the record that landed is not reported as lost
    assert raised.value.field_name == "upsert"


def test_upsert_accepts_a_fully_successful_batch(substrate):
    with patch("azure.search.documents.SearchClient.merge_or_upload_documents") as up:
        up.return_value = [_IndexingResult(_document_key("acme", "e1"), True)]
        substrate.upsert(ACME, [record(id_="e1")])


@pytest.mark.asyncio
async def test_aupsert_refuses_a_partially_failed_batch_too(substrate):
    """The async twin: the per-document check has to live on both write paths, or an async
    deployment keeps losing records silently."""
    with patch("azure.search.documents.aio.SearchClient.merge_or_upload_documents", new_callable=AsyncMock) as up:
        up.return_value = [
            _IndexingResult(_document_key("acme", "e2"), False, error_message="field too large", status_code=413)
        ]
        with pytest.raises(MemoryOperationError) as raised:
            await substrate.aupsert(ACME, [record(id_="e2")])
    assert "'e2'" in str(raised.value)
    await substrate.aclose()


def test_upsert_of_an_empty_batch_makes_no_request(substrate):
    with patch("azure.search.documents.SearchClient.merge_or_upload_documents") as up:
        substrate.upsert(ACME, [])
    up.assert_not_called()


@pytest.mark.asyncio
async def test_aupsert_agrees_with_upsert(substrate):
    rec = record(id_="e1", content="hello world", scope=ACME)
    with patch("azure.search.documents.aio.SearchClient.merge_or_upload_documents", new_callable=AsyncMock) as up:
        await substrate.aupsert(ACME, [rec])
    up.assert_called_once()
    doc = up.call_args.kwargs["documents"][0]
    assert doc["doc_key"] == _document_key("acme", "e1")
    assert doc["content"] == "hello world"


# -- search: request shape and tenant isolation -----------------------------------


def test_search_issues_a_vector_only_query_scoped_by_filter(substrate):
    query = MemoryQuery(text="hello", limit=5)
    with patch("azure.search.documents.SearchClient.search") as se:
        se.return_value = iter([hit()])
        records = substrate.search(ACME, query)
    assert len(records) == 1
    assert records[0].id == "e1"
    assert records[0].score == 0.9  # Azure's @search.score for cosine is already in (0, 1].
    kwargs = se.call_args.kwargs
    assert kwargs["search_text"] is None
    assert kwargs["vector_queries"] is not None
    assert kwargs["top"] == 5
    assert "tenant_id eq 'acme'" in kwargs["filter"]
    assert "user_id eq 'ada'" in kwargs["filter"]
    assert "expired_at eq null" in kwargs["filter"]


def test_search_filter_for_one_tenant_never_names_another(substrate):
    """The security-critical property: a request built for tenant A's scope must not carry any
    clause that could match tenant B's rows -- proven on the literal request the mocked SDK
    receives, not on the substrate's internal state."""
    query = MemoryQuery(text="hello", limit=5)
    with patch("azure.search.documents.SearchClient.search") as se:
        se.return_value = iter([])
        substrate.search(ACME, query)
    acme_filter = se.call_args.kwargs["filter"]
    assert "acme" in acme_filter
    assert "globex" not in acme_filter
    assert "wile" not in acme_filter

    with patch("azure.search.documents.SearchClient.search") as se:
        se.return_value = iter([])
        substrate.search(GLOBEX, query)
    globex_filter = se.call_args.kwargs["filter"]
    assert "globex" in globex_filter
    assert "acme" not in globex_filter
    assert "ada" not in globex_filter


def test_search_with_a_blank_query_text_makes_no_request(substrate):
    with patch("azure.search.documents.SearchClient.search") as se:
        assert substrate.search(ACME, MemoryQuery(text="   ", limit=5)) == []
    se.assert_not_called()


def test_search_with_limit_zero_makes_no_request(substrate):
    with patch("azure.search.documents.SearchClient.search") as se:
        assert substrate.search(ACME, MemoryQuery(text="hello", limit=0)) == []
    se.assert_not_called()


@pytest.mark.asyncio
async def test_asearch_agrees_with_search(substrate):
    query = MemoryQuery(text="hello", limit=5)

    async def _async_iter(items):
        for item in items:
            yield item

    with patch("azure.search.documents.aio.SearchClient.search", new_callable=AsyncMock) as se:
        se.return_value = _async_iter([hit()])
        records = await substrate.asearch(ACME, query)
    assert len(records) == 1
    assert records[0].id == "e1"
    kwargs = se.call_args.kwargs
    assert kwargs["search_text"] is None
    assert "tenant_id eq 'acme'" in kwargs["filter"]


# -- search_candidates: two unfused legs, no native RRF ---------------------------


def test_search_candidates_issues_two_independent_single_leg_queries(substrate):
    """Decision 2 (``AZ_B_dimension_and_scoring_decisions.md``): no native hybrid/RRF call. Proven
    directly on the two calls the mocked client receives -- one vector-only, one text-only, both
    carrying the identical scope filter."""
    query = MemoryQuery(text="hello world", limit=5)
    with patch("azure.search.documents.SearchClient.search") as se:
        se.side_effect = [iter([hit(score=0.9)]), iter([hit(score=12.5)])]
        candidates = substrate.search_candidates(ACME, query)
    assert se.call_count == 2
    vector_kwargs, lexical_kwargs = (call.kwargs for call in se.call_args_list)
    assert vector_kwargs["search_text"] is None
    assert vector_kwargs["vector_queries"] is not None
    assert lexical_kwargs["search_text"] == "hello world"
    assert lexical_kwargs["vector_queries"] is None
    # Both legs answer the same question about which rows exist.
    assert vector_kwargs["filter"] == lexical_kwargs["filter"]
    assert len(candidates.vector) == 1
    assert len(candidates.lexical) == 1
    # Vector leg's Azure @search.score passes through unchanged; lexical leg's unbounded BM25
    # score is squashed into [0.0, 1.0] via the saturating transform.
    assert candidates.vector[0].score == 0.9
    assert 0.0 < candidates.lexical[0].score < 1.0


def test_search_candidates_with_no_searchable_tiers_makes_no_request(substrate):
    with patch("azure.search.documents.SearchClient.search") as se:
        candidates = substrate.search_candidates(ACME, MemoryQuery(text="", limit=5))
    se.assert_not_called()
    assert candidates.vector == [] and candidates.lexical == []


@pytest.mark.asyncio
async def test_asearch_candidates_agrees_with_search_candidates(substrate):
    query = MemoryQuery(text="hello world", limit=5)

    async def _async_iter(items):
        for item in items:
            yield item

    with patch("azure.search.documents.aio.SearchClient.search", new_callable=AsyncMock) as se:
        se.side_effect = [_async_iter([hit(score=0.9)]), _async_iter([hit(score=12.5)])]
        candidates = await substrate.asearch_candidates(ACME, query)
    assert se.call_count == 2
    vector_kwargs, lexical_kwargs = (call.kwargs for call in se.call_args_list)
    assert vector_kwargs["search_text"] is None
    assert lexical_kwargs["search_text"] == "hello world"
    assert len(candidates.vector) == 1 and len(candidates.lexical) == 1


# -- scan ---------------------------------------------------------------------


def test_scan_orders_by_created_at_then_id_and_applies_no_current_belief_filter(substrate):
    """Matches ``PgVectorMemorySubstrate.scan``: enumeration, not a ranked read, so the
    current-belief predicate ``search`` applies does not appear here -- a caller walking a tier for
    consolidation must see superseded rows too."""
    with patch("azure.search.documents.SearchClient.search") as se:
        se.return_value = iter([hit()])
        records = substrate.scan(ACME, MemoryTier.EPISODIC, limit=10)
    assert len(records) == 1
    assert records[0].score is None
    kwargs = se.call_args.kwargs
    assert kwargs["order_by"] == ["created_at asc", "id asc"]
    assert "expired_at eq null" not in kwargs["filter"]
    assert "tenant_id eq 'acme'" in kwargs["filter"]


def test_scan_with_limit_zero_makes_no_request(substrate):
    with patch("azure.search.documents.SearchClient.search") as se:
        assert substrate.scan(ACME, MemoryTier.EPISODIC, limit=0) == []
    se.assert_not_called()


def test_scan_after_cursor_narrows_by_the_composite_order(substrate):
    with patch("azure.search.documents.SearchClient.search") as se:
        se.return_value = iter([])
        substrate.scan(ACME, MemoryTier.EPISODIC, limit=10, after=(100, "e1"))
    kwargs = se.call_args.kwargs
    assert "created_at gt 100" in kwargs["filter"]
    assert "id gt 'e1'" in kwargs["filter"]


def test_a_quote_in_a_scan_cursor_id_cannot_splice_a_second_clause(substrate):
    """``_scan_filter`` writes the cursor id into the filter itself rather than through
    ``odata.scope_filter``, so the escaping there is a separate call site with its own test --
    mirrors ``test_a_quote_in_a_scope_value_cannot_splice_a_second_clause`` in
    ``test_azure_ai_search_odata.py``."""
    with patch("azure.search.documents.SearchClient.search") as se:
        se.return_value = iter([])
        substrate.scan(ACME, MemoryTier.EPISODIC, limit=10, after=(100, "e1' or tier eq 'semantic"))
    # Asserted on the whole filter, not a substring: the hostile text appears in the output either
    # way, so the property is that it appears *inside* the literal, leaving the predicate structure
    # exactly as it would be for a benign cursor id.
    assert se.call_args.kwargs["filter"] == (
        "tenant_id eq 'acme' and user_id eq 'ada' and tier eq 'episodic' and "
        "(created_at gt 100 or (created_at eq 100 and id gt 'e1'' or tier eq ''semantic'))"
    )


@pytest.mark.parametrize("bad", ["100", 100.5, True, None])
def test_a_scan_cursor_with_a_non_integer_created_at_is_refused(substrate, bad):
    """``ScanCursor`` is an unenforced ``Tuple[int, str]`` alias and ``created_at`` reaches the
    filter as an unquoted numeric literal -- there is no escaping to lean on there, so the type is
    checked rather than trusted. Matches ``ValidWindow.__post_init__``'s precedent, ``bool``
    exclusion included."""
    with patch("azure.search.documents.SearchClient.search") as se:
        with pytest.raises(MemoryOperationError, match="cursor"):
            substrate.scan(ACME, MemoryTier.EPISODIC, limit=10, after=(bad, "e1"))
    se.assert_not_called()


@pytest.mark.asyncio
async def test_ascan_applies_the_same_cursor_escaping_and_guard(substrate):
    """Both scan twins build their filter through ``_scan_filter``; asserted on the async path too
    so the shared helper cannot be bypassed on one side later."""

    async def _async_iter(items):
        for item in items:
            yield item

    with patch("azure.search.documents.aio.SearchClient.search", new_callable=AsyncMock) as se:
        se.return_value = _async_iter([])
        await substrate.ascan(ACME, MemoryTier.EPISODIC, limit=10, after=(100, "e1' or tier eq 'semantic"))
        assert se.call_args.kwargs["filter"] == (
            "tenant_id eq 'acme' and user_id eq 'ada' and tier eq 'episodic' and "
            "(created_at gt 100 or (created_at eq 100 and id gt 'e1'' or tier eq ''semantic'))"
        )
        with pytest.raises(MemoryOperationError, match="cursor"):
            await substrate.ascan(ACME, MemoryTier.EPISODIC, limit=10, after=("100", "e1"))
    await substrate.aclose()


# -- get_record -----------------------------------------------------------------


def test_a_quote_in_a_get_record_id_cannot_splice_a_second_clause(substrate):
    """``get_record`` composes ``id eq '...'`` itself rather than through ``odata.scope_filter``, so
    its escaping is its own call site and gets its own hostile-input test."""
    with patch("azure.search.documents.SearchClient.search") as se:
        se.return_value = iter([])
        assert substrate.get_record(ACME, "e1' or tenant_id eq 'globex") is None
    # Three top-level clauses -- tenant, user, id -- with the whole hostile value contained in the
    # third one's literal. An unescaped id would have closed that literal and spliced a second
    # tenant predicate in via ` or tenant_id eq '...`.
    assert se.call_args.kwargs["filter"].split(" and ") == [
        "tenant_id eq 'acme'",
        "user_id eq 'ada'",
        "id eq 'e1'' or tenant_id eq ''globex'",
    ]


@pytest.mark.asyncio
async def test_aget_record_escapes_a_hostile_id_the_same_way(substrate):
    async def _async_iter(items):
        for item in items:
            yield item

    with patch("azure.search.documents.aio.SearchClient.search", new_callable=AsyncMock) as se:
        se.return_value = _async_iter([])
        await substrate.aget_record(ACME, "e1' or tenant_id eq 'globex")
    assert se.call_args.kwargs["filter"].split(" and ") == [
        "tenant_id eq 'acme'",
        "user_id eq 'ada'",
        "id eq 'e1'' or tenant_id eq ''globex'",
    ]
    await substrate.aclose()


def test_get_record_filters_by_scope_and_id_top_one(substrate):
    with patch("azure.search.documents.SearchClient.search") as se:
        se.return_value = iter([hit()])
        found = substrate.get_record(ACME, "e1")
    assert found is not None
    assert found.id == "e1"
    assert found.score is None
    kwargs = se.call_args.kwargs
    assert kwargs["top"] == 1
    assert "id eq 'e1'" in kwargs["filter"]
    assert "tenant_id eq 'acme'" in kwargs["filter"]


def test_get_record_returns_none_for_no_hits(substrate):
    with patch("azure.search.documents.SearchClient.search") as se:
        se.return_value = iter([])
        assert substrate.get_record(ACME, "missing") is None


@pytest.mark.asyncio
async def test_aget_record_agrees_with_get_record(substrate):
    async def _async_iter(items):
        for item in items:
            yield item

    with patch("azure.search.documents.aio.SearchClient.search", new_callable=AsyncMock) as se:
        se.return_value = _async_iter([hit()])
        found = await substrate.aget_record(ACME, "e1")
    assert found is not None and found.id == "e1"


# -- score normalization -----------------------------------------------------------


def test_lexical_similarity_is_monotonic_and_bounded():
    scores = [_lexical_similarity(r) for r in (0.0, 0.5, 1.0, 10.0, 1000.0)]
    assert scores == sorted(scores)
    assert all(0.0 <= s < 1.0 for s in scores)


@pytest.mark.parametrize("rank", [-0.05, -0.1, -1.0])
def test_a_negative_bm25_rank_is_refused_rather_than_silently_signed(rank):
    """``r / (r + k)`` is not the safety net it looks like: for ``-k < r < 0`` it returns a negative
    float, which would travel out as a ``MemoryRecord.score`` in violation of the ``[0.0, 1.0]``
    contract, and only ``r == -k`` raises at all (a ``ZeroDivisionError`` naming nothing)."""
    with pytest.raises(MemoryOperationError, match="negative"):
        _lexical_similarity(rank)


# -- embedding_version -----------------------------------------------------------


def test_an_absent_embedder_takes_the_mode_default(monkeypatch):
    from egpt.memory import embedding

    built = {}

    def _fake_construct(key, options):
        built["key"], built["options"] = key, options
        return StubEmbedder()

    monkeypatch.setattr(embedding, "_construct", _fake_construct)
    with patch("azure.search.documents.indexes.SearchIndexClient.create_or_update_index") as create:
        create.return_value = MagicMock()
        store = AzureAISearchMemorySubstrate(endpoint=ENDPOINT, api_key="k", embedder=None, dim=DIM)
    assert built["key"] == embedding.MODE_DEFAULTS[ProviderMode.LOCAL]["backend"]
    assert store.mode is ProviderMode.LOCAL
