"""Unit tests for the Azure AI Search ``VectorDb`` backend (AZ-C, framework half).

No Azure AI Search service is reachable from this environment (the same boundary AZ-B recorded),
so the SDK's four clients are replaced by fakes that record every call and hand back scripted
responses. What these tests prove is the shape of what the backend *sends* -- index schema,
document keys, ``$filter`` strings, query parameters, the Python-side hybrid blend -- not what a
live service would do with it.
"""

import asyncio
import base64
import json
import math
import re
from hashlib import blake2b, md5
from typing import Any, Dict, List, Optional

import pytest

pytest.importorskip("azure.search.documents", reason="azure-search-documents not installed")

from azure.core.exceptions import HttpResponseError, ResourceNotFoundError  # noqa: E402

from egpt.filters import AND, EQ, GT, IN, NEQ, NOT, OR  # noqa: E402
from egpt.integrations.vectordb.azure_ai_search import azure_ai_search as module  # noqa: E402
from egpt.integrations.vectordb.azure_ai_search import AzureAISearch, AzureAISearchConfigError  # noqa: E402
from egpt.integrations.vectordb.azure_ai_search.azure_ai_search import (  # noqa: E402
    _document_key,
    _is_retryable,
    _lexical_similarity,
    render_filter,
)
from egpt.knowledge.document import Document  # noqa: E402
from egpt.registry.discovery import declarations  # noqa: E402
from egpt.vectordb.base import VectorDb  # noqa: E402
from egpt.vectordb.search import SearchType  # noqa: E402

DIM = 8
ENDPOINT = "https://example.search.windows.net"
KEY_CHARSET = re.compile(r"^[A-Za-z0-9_\-=]+$")


# -- fakes -------------------------------------------------------------------------


class StubEmbedder:
    dimensions = DIM
    enable_batch = False

    def get_embedding(self, text):
        vector = [0.0] * DIM
        for token in text.lower().split():
            vector[int.from_bytes(blake2b(token.encode(), digest_size=4).digest(), "big") % DIM] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    def get_embedding_and_usage(self, text):
        return self.get_embedding(text), {"tokens": len(text.split())}

    async def async_get_embedding(self, text):
        await asyncio.sleep(0)
        return self.get_embedding(text)

    async def async_get_embedding_and_usage(self, text):
        await asyncio.sleep(0)
        return self.get_embedding_and_usage(text)


class _Response:
    def __init__(self, status_code):
        self.status_code = status_code
        self.reason = "stub"

    def text(self, encoding=None):
        return ""


def http_error(message, status_code):
    if status_code is None:
        return HttpResponseError(message)
    return HttpResponseError(message, response=_Response(status_code))


class _IndexingResult:
    def __init__(self, key, succeeded=True, error_message=None, status_code=200):
        self.key = key
        self.succeeded = succeeded
        self.error_message = error_message
        self.status_code = status_code


class Recorder:
    """Shared between the sync and async fakes so a test can read every call in order."""

    def __init__(self):
        self.calls: List[Dict[str, Any]] = []
        self.indexes: Dict[str, Any] = {}
        self.search_responses: List[List[Dict[str, Any]]] = []
        self.search_error: Optional[Exception] = None
        self.write_results: Optional[List[_IndexingResult]] = None

    def record(self, method, **kwargs):
        self.calls.append({"method": method, **kwargs})

    def of(self, method):
        return [c for c in self.calls if c["method"] == method]

    def next_search(self):
        if self.search_error is not None:
            raise self.search_error
        return self.search_responses.pop(0) if self.search_responses else []

    def results_for(self, documents):
        if self.write_results is not None:
            return self.write_results
        return [_IndexingResult(d["doc_key"]) for d in documents]


class FakeSearchClient:
    def __init__(self, recorder, endpoint, index_name, credential):
        self.recorder = recorder
        self.recorder.record("SearchClient", endpoint=endpoint, index_name=index_name, credential=credential)

    def search(self, **kwargs):
        self.recorder.record("search", **kwargs)
        return iter(self.recorder.next_search())

    def _write(self, name, documents):
        self.recorder.record(name, documents=documents)
        return self.recorder.results_for(documents)

    def upload_documents(self, documents):
        return self._write("upload_documents", documents)

    def merge_or_upload_documents(self, documents):
        return self._write("merge_or_upload_documents", documents)

    def merge_documents(self, documents):
        return self._write("merge_documents", documents)

    def delete_documents(self, documents):
        return self._write("delete_documents", documents)

    def close(self):
        self.recorder.record("close")


class _AsyncIter:
    def __init__(self, items):
        self._items = list(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop(0)


class FakeAsyncSearchClient(FakeSearchClient):
    def __init__(self, recorder, endpoint, index_name, credential):
        self.recorder = recorder
        self.recorder.record("AsyncSearchClient", endpoint=endpoint, index_name=index_name, credential=credential)

    async def search(self, **kwargs):
        self.recorder.record("async_search", **kwargs)
        return _AsyncIter(self.recorder.next_search())

    async def upload_documents(self, documents):
        return self._write("async_upload_documents", documents)

    async def merge_or_upload_documents(self, documents):
        return self._write("async_merge_or_upload_documents", documents)

    async def merge_documents(self, documents):
        return self._write("async_merge_documents", documents)

    async def delete_documents(self, documents):
        return self._write("async_delete_documents", documents)

    async def close(self):
        self.recorder.record("aclose")


class FakeIndexClient:
    def __init__(self, recorder, endpoint, credential):
        self.recorder = recorder
        self.recorder.record("SearchIndexClient", endpoint=endpoint, credential=credential)

    def get_index(self, name):
        self.recorder.record("get_index", name=name)
        if name not in self.recorder.indexes:
            raise ResourceNotFoundError(f"no index {name}")
        return self.recorder.indexes[name]

    def create_index(self, index):
        self.recorder.record("create_index", index=index)
        self.recorder.indexes[index.name] = index
        return index

    def delete_index(self, name):
        self.recorder.record("delete_index", name=name)
        if name not in self.recorder.indexes:
            raise ResourceNotFoundError(f"no index {name}")
        del self.recorder.indexes[name]

    def close(self):
        pass


class FakeAsyncIndexClient(FakeIndexClient):
    def __init__(self, recorder, endpoint, credential):
        self.recorder = recorder
        self.recorder.record("AsyncSearchIndexClient", endpoint=endpoint, credential=credential)

    async def get_index(self, name):
        return super().get_index(name)

    async def create_index(self, index):
        return super().create_index(index)

    async def delete_index(self, name):
        return super().delete_index(name)

    async def close(self):
        pass


@pytest.fixture
def recorder(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(module, "SearchClient", lambda **kw: FakeSearchClient(rec, **kw))
    monkeypatch.setattr(module, "AsyncSearchClient", lambda **kw: FakeAsyncSearchClient(rec, **kw))
    monkeypatch.setattr(module, "SearchIndexClient", lambda **kw: FakeIndexClient(rec, **kw))
    monkeypatch.setattr(module, "AsyncSearchIndexClient", lambda **kw: FakeAsyncIndexClient(rec, **kw))
    return rec


def make(**kwargs) -> AzureAISearch:
    defaults = dict(index_name="egpt-knowledge-test", endpoint=ENDPOINT, api_key="fake-key", embedder=StubEmbedder())
    defaults.update(kwargs)
    return AzureAISearch(**defaults)


@pytest.fixture
def db(recorder):
    return make()


def hit(id_="r1", content="hello world", score=0.9, meta=None, **extra):
    base = {
        "@search.score": score,
        "doc_key": _document_key(id_),
        "id": id_,
        "name": "doc",
        "content_hash": "h1",
        "content_id": "c1",
        "content": content,
        "meta_data": json.dumps(meta or {}),
        "usage": None,
        "filters": [],
        "embedding": [0.0] * DIM,
    }
    base.update(extra)
    return base


# -- construction ---------------------------------------------------------------------


def test_is_a_vectordb_and_registered_lazily():
    assert issubclass(AzureAISearch, VectorDb)
    row = declarations("vectordb")["azure_ai_search"]
    assert (row.module, row.attr) == ("egpt.integrations.vectordb.azure_ai_search", "AzureAISearch")
    assert row.obj is None


def test_missing_endpoint_is_refused_naming_the_env_var(monkeypatch):
    for var in module.ENDPOINT_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(AzureAISearchConfigError, match="EGPT_KNOWLEDGE_AZURE_SEARCH_ENDPOINT") as raised:
        AzureAISearch("idx", api_key="k", embedder=StubEmbedder())
    assert raised.value.field_name == "endpoint"


def test_missing_credential_is_refused_naming_the_env_var(monkeypatch):
    for var in module.API_KEY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(AzureAISearchConfigError, match="EGPT_KNOWLEDGE_AZURE_SEARCH_API_KEY") as raised:
        AzureAISearch("idx", endpoint=ENDPOINT, embedder=StubEmbedder())
    assert raised.value.field_name == "credential"


def test_knowledge_env_vars_win_over_memory_env_vars(monkeypatch):
    monkeypatch.setenv("EGPT_MEMORY_AZURE_SEARCH_ENDPOINT", "https://memory.search.windows.net")
    monkeypatch.setenv("EGPT_KNOWLEDGE_AZURE_SEARCH_ENDPOINT", "https://knowledge.search.windows.net")
    monkeypatch.setenv("EGPT_MEMORY_AZURE_SEARCH_API_KEY", "memory-key")
    monkeypatch.setenv("EGPT_KNOWLEDGE_AZURE_SEARCH_API_KEY", "knowledge-key")
    db = AzureAISearch("idx", embedder=StubEmbedder())
    assert db.endpoint == "https://knowledge.search.windows.net"
    assert db.credential.key == "knowledge-key"


def test_memory_env_vars_are_the_fallback(monkeypatch):
    monkeypatch.delenv("EGPT_KNOWLEDGE_AZURE_SEARCH_ENDPOINT", raising=False)
    monkeypatch.delenv("EGPT_KNOWLEDGE_AZURE_SEARCH_API_KEY", raising=False)
    monkeypatch.setenv("EGPT_MEMORY_AZURE_SEARCH_ENDPOINT", "https://memory.search.windows.net")
    monkeypatch.setenv("EGPT_MEMORY_AZURE_SEARCH_API_KEY", "memory-key")
    db = AzureAISearch("idx", embedder=StubEmbedder())
    assert db.endpoint == "https://memory.search.windows.net"
    assert db.credential.key == "memory-key"


def test_an_explicit_credential_wins_over_api_key():
    sentinel = object()
    db = make(credential=sentinel, api_key="ignored")
    assert db.credential is sentinel


@pytest.mark.parametrize("name", ["Bad_Name", "-dash", "a", "UPPER", ""])
def test_a_bad_index_name_is_refused(name):
    with pytest.raises(AzureAISearchConfigError) as raised:
        make(index_name=name)
    assert raised.value.field_name == "index_name"


def test_dimensions_default_to_the_embedder_and_can_be_overridden():
    assert make().dimensions == DIM
    assert make(vector_dimensions=1024).dimensions == 1024


def test_dimensions_above_azures_ceiling_are_refused():
    with pytest.raises(AzureAISearchConfigError, match="3072") as raised:
        make(vector_dimensions=module.MAX_VECTOR_DIM + 1)
    assert raised.value.field_name == "vector_dimensions"


def test_an_embedder_without_dimensions_needs_an_override():
    class NoDim(StubEmbedder):
        dimensions = None

    with pytest.raises(AzureAISearchConfigError) as raised:
        make(embedder=NoDim())
    assert raised.value.field_name == "vector_dimensions"
    assert make(embedder=NoDim(), vector_dimensions=DIM).dimensions == DIM


@pytest.mark.parametrize("weight", [-0.1, 1.5])
def test_vector_score_weight_outside_unit_interval_is_refused(weight):
    with pytest.raises(AzureAISearchConfigError) as raised:
        make(vector_score_weight=weight)
    assert raised.value.field_name == "vector_score_weight"


def test_id_is_seeded_from_endpoint_and_index_name():
    assert make().id == make().id
    assert make().id != make(index_name="other-index").id
    assert make(id="custom").id == "custom"


def test_supported_search_types():
    assert make().get_supported_search_types() == [SearchType.vector, SearchType.keyword, SearchType.hybrid]
    assert make().upsert_available() is True


# -- schema -----------------------------------------------------------------------------


def _field(index, name):
    return next(f for f in index.fields if f.name == name)


def test_create_builds_the_documented_schema(db, recorder):
    db.create()
    assert [c["method"] for c in recorder.of("get_index")] == ["get_index"]
    (created,) = recorder.of("create_index")
    index = created["index"]
    assert index.name == "egpt-knowledge-test"

    assert _field(index, "doc_key").key is True
    assert _field(index, "content").searchable is True
    assert _field(index, "id").filterable is True
    assert _field(index, "content_hash").filterable is True
    assert _field(index, "content_id").filterable is True
    assert _field(index, "name").filterable is True
    assert _field(index, "filters").filterable is True
    assert _field(index, "filters").type == "Collection(Edm.String)"

    embedding = _field(index, "embedding")
    assert embedding.type == "Collection(Edm.Single)"
    assert embedding.vector_search_dimensions == DIM
    assert embedding.vector_search_profile_name == "egpt-knowledge-vector-profile"

    (algorithm,) = index.vector_search.algorithms
    assert algorithm.kind == "hnsw"
    assert algorithm.parameters.metric == "cosine"
    assert (algorithm.parameters.m, algorithm.parameters.ef_construction, algorithm.parameters.ef_search) == (
        4,
        400,
        500,
    )
    (profile,) = index.vector_search.profiles
    assert profile.algorithm_configuration_name == algorithm.name


def test_create_is_idempotent_when_the_width_agrees(db, recorder):
    db.create()
    db.create()
    assert len(recorder.of("create_index")) == 1


def test_create_refuses_an_existing_index_of_a_different_width(recorder):
    make(vector_dimensions=1024).create()
    with pytest.raises(AzureAISearchConfigError, match=r"1024.*8|8.*1024") as raised:
        make(vector_dimensions=8).create()
    assert raised.value.field_name == "vector_dimensions"
    assert "1024" in str(raised.value) and "8-dimension" in str(raised.value)


def test_create_refuses_a_foreign_index_without_a_vector_field(recorder):
    class Foreign:
        name = "egpt-knowledge-test"
        fields = []

    recorder.indexes["egpt-knowledge-test"] = Foreign()
    with pytest.raises(AzureAISearchConfigError, match="not created by this backend"):
        make().create()


def test_exists_and_drop(db, recorder):
    assert db.exists() is False
    db.create()
    assert db.exists() is True
    db.drop()
    assert db.exists() is False
    db.drop()  # a second drop is a no-op, not an error


# -- keys and documents -------------------------------------------------------------------


@pytest.mark.parametrize("record_id", ["abc123", "with space/and:punct'", "unicode-éè", ""])
def test_document_key_is_charset_safe_and_injective(record_id):
    key = _document_key(record_id)
    assert KEY_CHARSET.match(key) or key == ""
    assert base64.urlsafe_b64decode(key.encode()).decode() == record_id


def test_insert_sends_pgvectors_record_shape(db, recorder):
    doc = Document(content="chunk text", name="doc.md", meta_data={"section": "intro", "page": 3}, content_id="c1")
    db.insert("hash-1", [doc], filters={"tenant": "acme"})

    (call,) = recorder.of("upload_documents")
    (record,) = call["documents"]
    expected_id = md5(f"{md5(b'chunk text').hexdigest()}_hash-1".encode()).hexdigest()
    assert record["id"] == expected_id
    assert record["doc_key"] == _document_key(expected_id)
    assert KEY_CHARSET.match(record["doc_key"])
    assert record["content"] == "chunk text"
    assert record["content_hash"] == "hash-1"
    assert record["content_id"] == "c1"
    assert record["name"] == "doc.md"
    assert json.loads(record["meta_data"]) == {"section": "intro", "page": 3, "tenant": "acme"}
    assert sorted(record["filters"]) == ["page=3", "section=intro", "tenant=acme"]
    assert len(record["embedding"]) == DIM
    assert json.loads(record["usage"]) == {"tokens": 2}


def test_insert_uses_the_documents_own_id_as_the_base(db, recorder):
    db.insert("h", [Document(id="my-id", content="x")])
    (record,) = recorder.of("upload_documents")[0]["documents"]
    assert record["id"] == md5(b"my-id_h").hexdigest()


def test_insert_nulls_are_cleaned_and_batches_dedupe_by_key(db, recorder):
    db.insert("h", [Document(id="same", content="a\x00b"), Document(id="same", content="a\x00b")])
    (record,) = recorder.of("upload_documents")[0]["documents"]
    assert record["content"] == "a�b"


def test_insert_raises_on_a_per_document_rejection(db, recorder):
    recorder.write_results = [_IndexingResult("k", succeeded=False, error_message="too large")]
    with pytest.raises(RuntimeError, match="rejected 1 of 1"):
        db.insert("h", [Document(content="x")])


def test_insert_refuses_an_embedding_of_the_wrong_width(recorder):
    db = make(vector_dimensions=DIM + 1)
    db.insert("h", [Document(content="x")])
    # The mis-sized document is logged and skipped, as PgVector skips a document it cannot process.
    assert recorder.of("upload_documents") == []


def test_upsert_deletes_the_old_hash_then_merges(db, recorder):
    recorder.search_responses = [[hit("old")], [hit("old")]]
    db.upsert("h1", [Document(content="new")])
    methods = [c["method"] for c in recorder.calls if c["method"] not in ("SearchClient",)]
    assert methods == ["search", "search", "delete_documents", "merge_or_upload_documents"]
    assert recorder.of("search")[0]["filter"] == "content_hash eq 'h1'"
    assert recorder.of("delete_documents")[0]["documents"] == [{"doc_key": _document_key("old")}]


def test_upsert_skips_the_delete_when_the_hash_is_new(db, recorder):
    db.upsert("h1", [Document(content="new")])
    assert recorder.of("delete_documents") == []
    assert len(recorder.of("merge_or_upload_documents")) == 1


def test_existence_checks_render_escaped_filters(db, recorder):
    recorder.search_responses = [[hit()], [], []]
    assert db.name_exists("o'brien") is True
    assert db.id_exists("r1") is False
    assert db.content_hash_exists("h") is False
    filters = [c["filter"] for c in recorder.of("search")]
    assert filters == ["name eq 'o''brien'", "id eq 'r1'", "content_hash eq 'h'"]
    assert all(c["top"] == 1 and c["select"] == ["doc_key"] for c in recorder.of("search"))


# -- filters ---------------------------------------------------------------------------


def test_render_filter_none_and_empty():
    assert render_filter(None) is None
    assert render_filter({}) is None
    assert render_filter([]) is None


def test_dict_filter_is_a_conjunction_of_token_matches():
    assert render_filter({"tenant": "acme", "page": 3}) == (
        "filters/any(f: f eq 'tenant=acme') and filters/any(f: f eq 'page=3')"
    )


def test_filter_literals_are_escaped():
    assert render_filter({"who": "o'brien"}) == "filters/any(f: f eq 'who=o''brien')"


def test_dsl_filters_render():
    assert render_filter([EQ("a", "1")]) == "filters/any(f: f eq 'a=1')"
    assert render_filter([NEQ("a", "1")]) == "not filters/any(f: f eq 'a=1')"
    assert render_filter([IN("a", ["x", 2])]) == "(filters/any(f: f eq 'a=x') or filters/any(f: f eq 'a=2'))"
    assert render_filter([IN("a", [])]) == "false"
    assert render_filter([NOT(EQ("a", "1"))]) == "not (filters/any(f: f eq 'a=1'))"
    assert render_filter([AND(EQ("a", "1"), OR(EQ("b", "2"), EQ("c", True)))]) == (
        "(filters/any(f: f eq 'a=1') and (filters/any(f: f eq 'b=2') or filters/any(f: f eq 'c=true')))"
    )
    assert render_filter([EQ("a", "1"), EQ("b", "2")]) == "filters/any(f: f eq 'a=1') and filters/any(f: f eq 'b=2')"


def test_dsl_filters_accept_dict_form():
    assert render_filter([{"op": "EQ", "key": "a", "value": "1"}]) == "filters/any(f: f eq 'a=1')"


def test_unexpressible_operators_are_refused_by_name():
    with pytest.raises(ValueError, match="GT is not expressible"):
        render_filter([GT("views", 10)])
    with pytest.raises(ValueError, match="Unknown filter operator: BOGUS"):
        render_filter([{"op": "BOGUS"}])


def test_stored_tokens_and_query_tokens_agree_for_non_string_values(db, recorder):
    db.insert("h", [Document(content="x", meta_data={"flag": True, "nested": {"b": 1, "a": 2}})])
    (record,) = recorder.of("upload_documents")[0]["documents"]
    assert sorted(record["filters"]) == ["flag=true", 'nested={"a": 2, "b": 1}']
    assert render_filter({"nested": {"a": 2, "b": 1}}) == 'filters/any(f: f eq \'nested={"a": 2, "b": 1}\')'


# -- search ----------------------------------------------------------------------------


def test_vector_search_sends_a_vector_only_query(db, recorder):
    recorder.search_responses = [[hit("r1", score=0.9, meta={"k": "v"}), hit("r2", score=0.7)]]
    results = db.search("hello world", limit=2, filters={"tenant": "acme"})

    (call,) = recorder.of("search")
    assert call["search_text"] is None
    assert call["top"] == 2
    assert call["filter"] == "filters/any(f: f eq 'tenant=acme')"
    (vq,) = call["vector_queries"]
    assert vq.fields == "embedding"
    assert vq.k_nearest_neighbors == 2
    assert vq.vector == pytest.approx(StubEmbedder().get_embedding("hello world"))

    assert [d.id for d in results] == ["r1", "r2"]
    assert results[0].meta_data == {"k": "v", "similarity_score": 0.9}
    assert results[0].content_id == "c1"
    assert results[0].embedder is db.embedder


def test_vector_search_applies_the_similarity_threshold(recorder):
    db = make(similarity_threshold=0.8)
    recorder.search_responses = [[hit("r1", score=0.9), hit("r2", score=0.7)]]
    assert [d.id for d in db.search("q")] == ["r1"]


def test_keyword_search_sends_a_text_only_query(recorder):
    db = make(search_type=SearchType.keyword)
    recorder.search_responses = [[hit("r1", score=3.2)]]
    results = db.search("hello", limit=3)
    (call,) = recorder.of("search")
    assert call["search_text"] == "hello"
    assert call["search_fields"] == ["content"]
    assert "vector_queries" not in call
    assert call["top"] == 3
    assert results[0].id == "r1"
    assert "similarity_score" not in results[0].meta_data


def test_hybrid_search_blends_two_single_leg_queries(recorder):
    db = make(search_type=SearchType.hybrid, vector_score_weight=0.7)
    recorder.search_responses = [
        [hit("both", score=0.9), hit("vec-only", score=0.8)],  # vector leg
        [hit("both", score=0.4), hit("lex-only", score=1.0)],  # BM25 leg
    ]
    results = db.search("hello", limit=3, filters={"t": "a"})

    vector_call, lexical_call = recorder.of("search")
    assert vector_call["search_text"] is None and len(vector_call["vector_queries"]) == 1
    assert lexical_call["search_text"] == "hello" and "vector_queries" not in lexical_call
    assert vector_call["filter"] == lexical_call["filter"] == "filters/any(f: f eq 't=a')"
    assert vector_call["top"] == lexical_call["top"] == 3

    scores = {d.id: d.meta_data["similarity_score"] for d in results}
    assert scores["both"] == pytest.approx(0.7 * 0.9 + 0.3 * _lexical_similarity(0.4))
    assert scores["vec-only"] == pytest.approx(0.7 * 0.8)
    assert scores["lex-only"] == pytest.approx(0.3 * _lexical_similarity(1.0))
    assert [d.id for d in results] == ["both", "vec-only", "lex-only"]


def test_hybrid_search_honours_limit_and_threshold(recorder):
    db = make(search_type=SearchType.hybrid, similarity_threshold=0.5)
    recorder.search_responses = [[hit("a", score=1.0), hit("b", score=0.9)], [hit("c", score=0.1)]]
    results = db.search("q", limit=1)
    assert [d.id for d in results] == ["a"]


def test_lexical_similarity_is_pgvectors_saturation():
    assert _lexical_similarity(0.0) == 0.0
    assert _lexical_similarity(0.1) == pytest.approx(0.5)
    assert _lexical_similarity(0.4) == pytest.approx(0.8)
    with pytest.raises(ValueError):
        _lexical_similarity(-1.0)


def test_reranker_is_applied_to_vector_and_hybrid_results(recorder):
    class Reverse:
        def rerank(self, query, documents):
            return list(reversed(documents))

    db = make(reranker=Reverse())
    recorder.search_responses = [[hit("r1"), hit("r2")]]
    assert [d.id for d in db.search("q")] == ["r2", "r1"]


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504, None])
def test_a_retryable_search_failure_propagates(db, recorder, status):
    recorder.search_error = http_error("throttled", status)
    assert _is_retryable(recorder.search_error) is True
    with pytest.raises(HttpResponseError):
        db.search("q")


@pytest.mark.parametrize("status", [400, 403, 404])
def test_a_deterministic_search_failure_returns_nothing(db, recorder, status):
    recorder.search_error = http_error("bad filter", status)
    assert _is_retryable(recorder.search_error) is False
    assert db.search("q") == []


# -- deletes and metadata --------------------------------------------------------------------


def test_delete_by_paths_page_keys_then_delete(db, recorder):
    recorder.search_responses = [[hit("a"), hit("b")], [hit("c")], [hit("d")], [hit("e")], [hit("f")]]
    assert db.delete_by_id("a") is True
    assert db.delete_by_name("n") is True
    assert db.delete_by_content_id("c1") is True
    assert db.delete_by_metadata({"k": "v"}) is True
    assert db.delete() is True

    searches = recorder.of("search")
    assert [c["filter"] for c in searches] == [
        "id eq 'a'",
        "name eq 'n'",
        "content_id eq 'c1'",
        "filters/any(f: f eq 'k=v')",
        None,
    ]
    assert all(c["search_text"] == "*" and c["select"] == ["doc_key"] for c in searches)
    deletes = recorder.of("delete_documents")
    assert deletes[0]["documents"] == [{"doc_key": _document_key("a")}, {"doc_key": _document_key("b")}]
    assert len(deletes) == 5


def test_delete_by_empty_metadata_deletes_nothing(db, recorder):
    assert db.delete_by_metadata({}) is False
    assert recorder.of("delete_documents") == []


def test_delete_failure_returns_false(db, recorder):
    recorder.search_error = http_error("bad", 400)
    assert db.delete_by_id("x") is False


def test_update_metadata_merges_and_reflattens(db, recorder):
    recorder.search_responses = [[hit("r1", meta={"a": 1, "b": 2})]]
    db.update_metadata("c1", {"b": 3, "c": "z"})
    assert recorder.of("search")[0]["filter"] == "content_id eq 'c1'"
    (call,) = recorder.of("merge_documents")
    (update,) = call["documents"]
    assert update["doc_key"] == _document_key("r1")
    assert json.loads(update["meta_data"]) == {"a": 1, "b": 3, "c": "z"}
    assert sorted(update["filters"]) == ["a=1", "b=3", "c=z"]


# -- async twins --------------------------------------------------------------------------


ASYNC_TWINS = [
    ("create", "async_create"),
    ("name_exists", "async_name_exists"),
    ("insert", "async_insert"),
    ("upsert", "async_upsert"),
    ("search", "async_search"),
    ("drop", "async_drop"),
    ("exists", "async_exists"),
    ("vector_search", "async_vector_search"),
    ("keyword_search", "async_keyword_search"),
    ("hybrid_search", "async_hybrid_search"),
]


@pytest.mark.parametrize("sync_name, async_name", ASYNC_TWINS)
def test_every_async_twin_exists_and_is_a_coroutine(sync_name, async_name):
    assert callable(getattr(AzureAISearch, sync_name))
    assert asyncio.iscoroutinefunction(getattr(AzureAISearch, async_name))


def test_every_abstract_method_is_implemented():
    assert not getattr(AzureAISearch, "__abstractmethods__", set())


@pytest.mark.asyncio
async def test_async_create_exists_drop_use_the_async_index_client(db, recorder):
    assert await db.async_exists() is False
    await db.async_create()
    assert await db.async_exists() is True
    await db.async_drop()
    assert await db.async_exists() is False
    assert len(recorder.of("AsyncSearchIndexClient")) == 1
    assert len(recorder.of("create_index")) == 1
    assert recorder.of("SearchIndexClient") == []  # only the async client was ever built
    await db.aclose()


@pytest.mark.asyncio
async def test_async_insert_and_upsert_use_the_async_search_client(db, recorder):
    await db.async_insert("h", [Document(content="x")])
    recorder.search_responses = [[hit("old")], [hit("old")]]
    await db.async_upsert("h", [Document(content="y")])
    methods = [c["method"] for c in recorder.calls if c["method"] != "AsyncSearchClient"]
    assert methods == [
        "async_upload_documents",
        "async_search",
        "async_search",
        "async_delete_documents",
        "async_merge_or_upload_documents",
    ]
    assert recorder.of("SearchClient") == []
    await db.aclose()


@pytest.mark.asyncio
async def test_async_search_dispatches_on_search_type(recorder):
    db = make(search_type=SearchType.hybrid, vector_score_weight=0.5)
    recorder.search_responses = [[hit("v", score=0.8)], [hit("l", score=0.4)]]
    results = await db.async_search("hello", limit=2)
    calls = recorder.of("async_search")
    assert calls[0]["search_text"] is None and calls[1]["search_text"] == "hello"
    assert {d.id: d.meta_data["similarity_score"] for d in results} == {
        "v": pytest.approx(0.4),
        "l": pytest.approx(0.5 * _lexical_similarity(0.4)),
    }
    assert await db.async_name_exists("n") is False
    assert recorder.of("SearchClient") == []
    await db.aclose()


@pytest.mark.asyncio
async def test_async_search_splits_retryability_like_sync(db, recorder):
    recorder.search_error = http_error("throttled", 429)
    with pytest.raises(HttpResponseError):
        await db.async_search("q")
    recorder.search_error = http_error("bad", 400)
    assert await db.async_search("q") == []
    await db.aclose()


def test_close_refuses_while_an_async_client_is_open(db):
    db._async_search_client()
    with pytest.raises(RuntimeError, match="aclose"):
        db.close()


def test_deepcopy_shares_clients_and_embedder(db):
    from copy import deepcopy

    db._search_client()
    copied = deepcopy(db)
    assert copied._client is db._client
    assert copied.embedder is db.embedder
    assert copied.index_name == db.index_name
