"""Deterministic-id seeding for the seven backends that never passed one (aicoe-tracker#155).

Before the fix, none of these passed ``id`` to ``super().__init__``, so every instance of
a backend fell through to ``generate_id(<class name>)`` -- one shared id per backend,
regardless of server, bucket, keyspace, collection or table. The registry de-dupes by id
and keeps the first registration, so the second instance was silently shadowed.

Layout follows the conventions this bug family established:

* SDK-gated backends (cassandra, clickhouse, couchbase, llamaindex) import inside a
  fixture behind ``pytest.importorskip``, so this file stays collectable -- and the
  SDK-free tests keep running -- on a driverless environment.
* No network is touched: engines are injected or stubbed, and the seed is computed
  before any client construction.
* The two wrapper backends (langchaindb, llamaindex) have no connection target: the
  pinned contract there is *distinctness* (fresh id per instance) plus explicit
  ``name=``/``id=`` restoring determinism.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# --------------------------------------------------------------------------- #
# SingleStore (vectordb) -- importable without its driver
# --------------------------------------------------------------------------- #


class TestSingleStoreVectorIdSeed:
    def _cls(self):
        from egpt.integrations.vectordb.singlestore.singlestore import SingleStore

        return SingleStore

    def test_different_urls_get_different_ids(self, monkeypatch):
        SingleStore = self._cls()
        import egpt.integrations.vectordb.singlestore.singlestore as mod

        monkeypatch.setattr(mod, "create_engine", lambda url, *a, **k: SimpleNamespace(url=url, connect=MagicMock()))
        a = SingleStore(embedder=MagicMock(), collection="docs", db_url="mysql+pymysql://u:p@host-a/db")
        b = SingleStore(embedder=MagicMock(), collection="docs", db_url="mysql+pymysql://u:p@host-b/db")
        assert a.id != b.id

    def test_collection_and_schema_contribute(self, monkeypatch):
        SingleStore = self._cls()
        import egpt.integrations.vectordb.singlestore.singlestore as mod

        monkeypatch.setattr(mod, "create_engine", lambda url, *a, **k: SimpleNamespace(url=url, connect=MagicMock()))
        base = SingleStore(embedder=MagicMock(), collection="docs", db_url="mysql+pymysql://u:p@host-a/db")
        other_coll = SingleStore(embedder=MagicMock(), collection="other", db_url="mysql+pymysql://u:p@host-a/db")
        other_schema = SingleStore(
            embedder=MagicMock(), collection="docs", schema="s2", db_url="mysql+pymysql://u:p@host-a/db"
        )
        assert len({base.id, other_coll.id, other_schema.id}) == 3

    def test_stable_and_explicit_id_wins(self, monkeypatch):
        SingleStore = self._cls()
        import egpt.integrations.vectordb.singlestore.singlestore as mod

        monkeypatch.setattr(mod, "create_engine", lambda url, *a, **k: SimpleNamespace(url=url, connect=MagicMock()))
        a = SingleStore(embedder=MagicMock(), collection="docs", db_url="mysql+pymysql://u:p@host-a/db")
        b = SingleStore(embedder=MagicMock(), collection="docs", db_url="mysql+pymysql://u:p@host-a/db")
        assert a.id == b.id
        assert (
            SingleStore(embedder=MagicMock(), collection="docs", db_url="mysql+pymysql://u:p@host-a/db", id="pinned").id
            == "pinned"
        )

    def test_injected_engine_seeds_from_engine_url(self):
        SingleStore = self._cls()
        a = SingleStore(
            embedder=MagicMock(), collection="docs", db_engine=SimpleNamespace(url="mysql+pymysql://u:p@host-a/db")
        )
        b = SingleStore(
            embedder=MagicMock(), collection="docs", db_engine=SimpleNamespace(url="mysql+pymysql://u:p@host-b/db")
        )
        assert a.id != b.id


# --------------------------------------------------------------------------- #
# LightRag -- importable without extras
# --------------------------------------------------------------------------- #


class TestLightRagIdSeed:
    def test_servers_discriminate_and_same_server_is_stable(self):
        from egpt.integrations.vectordb.lightrag.lightrag import LightRag

        a = LightRag(server_url="http://host-a:9621")
        b = LightRag(server_url="http://host-b:9621")
        assert a.id != b.id
        assert a.id == LightRag(server_url="http://host-a:9621").id

    def test_api_key_stays_out_of_the_seed(self):
        from egpt.integrations.vectordb.lightrag.lightrag import LightRag

        with_key = LightRag(server_url="http://host-a:9621", api_key="secret")
        without = LightRag(server_url="http://host-a:9621")
        assert with_key.id == without.id


# --------------------------------------------------------------------------- #
# Wrapper backends -- no connection target; the contract is distinctness
# --------------------------------------------------------------------------- #


class TestLangChainWrapperIds:
    def test_two_wrappers_get_distinct_ids(self):
        from egpt.integrations.vectordb.langchaindb.langchaindb import LangChainVectorDb

        a = LangChainVectorDb(vectorstore=MagicMock())
        b = LangChainVectorDb(vectorstore=MagicMock())
        assert a.id != b.id, "wrapper instances shared one id, so the registry kept only the first"

    def test_explicit_name_restores_determinism(self):
        from egpt.integrations.vectordb.langchaindb.langchaindb import LangChainVectorDb

        a = LangChainVectorDb(vectorstore=MagicMock(), name="kb-main")
        b = LangChainVectorDb(vectorstore=MagicMock(), name="kb-main")
        assert a.id == b.id
        assert LangChainVectorDb(vectorstore=MagicMock(), id="pinned").id == "pinned"


class TestLlamaIndexWrapperIds:
    @pytest.fixture
    def cls(self):
        pytest.importorskip("llama_index.core", reason="LlamaIndexVectorDb imports llama-index-core at module level")
        from egpt.integrations.vectordb.llamaindex.llamaindexdb import LlamaIndexVectorDb

        return LlamaIndexVectorDb

    def test_two_wrappers_get_distinct_ids(self, cls):
        assert cls(knowledge_retriever=MagicMock()).id != cls(knowledge_retriever=MagicMock()).id

    def test_explicit_name_restores_determinism(self, cls):
        assert cls(knowledge_retriever=MagicMock(), name="kb").id == cls(knowledge_retriever=MagicMock(), name="kb").id


# --------------------------------------------------------------------------- #
# SDK-gated backends -- import inside the fixture so this file always collects
# --------------------------------------------------------------------------- #


class TestCassandraIdSeed:
    @pytest.fixture
    def cls(self):
        pytest.importorskip("cassio", reason="Cassandra imports cassio at module level")
        from egpt.integrations.vectordb.cassandra.cassandra import Cassandra

        return Cassandra

    def test_targets_discriminate_and_are_stable(self, cls, monkeypatch):
        # initialize_table builds a real cassio table object; the seed is computed
        # before it runs and is all this test is about.
        monkeypatch.setattr(cls, "initialize_table", lambda self: None)
        session = MagicMock()
        a = cls(table_name="docs", keyspace="ks_a", session=session)
        b = cls(table_name="docs", keyspace="ks_b", session=session)
        c = cls(table_name="other", keyspace="ks_a", session=session)
        assert len({a.id, b.id, c.id}) == 3
        assert a.id == cls(table_name="docs", keyspace="ks_a", session=session).id
        assert cls(table_name="docs", keyspace="ks_a", session=session, id="pinned").id == "pinned"


class TestClickhouseIdSeed:
    @pytest.fixture
    def cls(self):
        pytest.importorskip("clickhouse_connect", reason="Clickhouse imports clickhouse_connect at module level")
        from egpt.integrations.vectordb.clickhouse.clickhousedb import Clickhouse

        return Clickhouse

    def test_targets_discriminate_and_password_stays_out(self, cls):
        client = MagicMock()
        a = cls(table_name="docs", host="host-a", client=client, asyncclient=client)
        b = cls(table_name="docs", host="host-b", client=client, asyncclient=client)
        assert a.id != b.id
        assert a.id == cls(table_name="docs", host="host-a", client=client, asyncclient=client).id
        with_pw = cls(table_name="docs", host="host-a", password="s3cret", client=client, asyncclient=client)
        assert with_pw.id == a.id, "the password must not contribute to the seed"


class TestCouchbaseIdSeed:
    @pytest.fixture
    def cls(self):
        pytest.importorskip("couchbase", reason="CouchbaseSearch imports the couchbase SDK at module level")
        from egpt.integrations.vectordb.couchbase.couchbase import CouchbaseSearch

        return CouchbaseSearch

    def test_targets_discriminate(self, cls):
        kw = dict(cluster_options=MagicMock(), search_index="idx", embedder=MagicMock())
        a = cls("bucket", "scope", "docs", "couchbase://host-a", **kw)
        b = cls("bucket", "scope", "docs", "couchbase://host-b", **kw)
        c = cls("bucket", "scope", "other", "couchbase://host-a", **kw)
        assert len({a.id, b.id, c.id}) == 3
        assert a.id == cls("bucket", "scope", "docs", "couchbase://host-a", **kw).id
