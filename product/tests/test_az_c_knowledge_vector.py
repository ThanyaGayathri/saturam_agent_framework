"""AZ-C: the domain prose's vector store is chosen by setting, not hardcoded.

Dependency-free. The two backend classes are imported lazily inside
``build_knowledge_vector_db`` -- ``PgVector`` from a module that exists, ``AzureAISearch``
from AZ-B's module that lands in parallel -- so both are stood in for here: the pgvector one by
monkeypatching the attribute on its real module, the Azure one by planting a fake module in
``sys.modules``. Each fake records the kwargs it was built with, which is the whole assertion:
the factory must hand each backend exactly what the call-sites handed ``PgVector`` before.
"""

from __future__ import annotations

import importlib.util

import sys
import types
from pathlib import Path
from typing import Any, Dict, List

import pytest

from egpt.vectordb.search import SearchType
from egpt_retail.app.settings import RetailSettings
from egpt_retail.knowledge.vector import build_knowledge_vector_db

_ENTRYPOINTS = Path(__file__).resolve().parents[1] / "egpt_retail" / "entrypoints"


class _Recording:
    """A stand-in backend that keeps its constructor kwargs."""

    instances: List["_Recording"] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        type(self).instances.append(self)


@pytest.fixture
def fake_pgvector(monkeypatch: pytest.MonkeyPatch) -> type:
    import egpt.integrations.vectordb.pgvector as pg

    class FakePgVector(_Recording):
        instances: List[_Recording] = []

    monkeypatch.setattr(pg, "PgVector", FakePgVector)
    return FakePgVector


@pytest.fixture
def fake_azure(monkeypatch: pytest.MonkeyPatch) -> type:
    class FakeAzureAISearch(_Recording):
        instances: List[_Recording] = []

    module = types.ModuleType("egpt.integrations.vectordb.azure_ai_search")
    module.AzureAISearch = FakeAzureAISearch  # type: ignore[attr-defined]
    monkeypatch.setitem(
        sys.modules, "egpt.integrations.vectordb.azure_ai_search", module
    )
    return FakeAzureAISearch


@pytest.fixture
def no_backend_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The defaults under test must not be masked by this machine's environment."""
    for name in (
        "EGPT_RETAIL_KNOWLEDGE_BACKEND",
        "EGPT_RETAIL_KNOWLEDGE_SEARCH_ENDPOINT",
        "EGPT_RETAIL_KNOWLEDGE_SEARCH_API_KEY",
        "EGPT_RETAIL_KNOWLEDGE_SEARCH_INDEX",
        "EGPT_RETAIL_KNOWLEDGE_TABLE",
        "EGPT_RETAIL_DB_URL",
    ):
        monkeypatch.delenv(name, raising=False)


# --- the setting ------------------------------------------------------------------------


def test_default_backend_is_the_deployment_s(no_backend_env: None) -> None:
    """Same posture as retail_provider_mode: the default is the production truth."""
    settings = RetailSettings()
    assert settings.retail_knowledge_backend == "azure_ai_search"
    assert settings.retail_knowledge_search_index == "egpt-retail-knowledge"
    assert settings.retail_knowledge_search_endpoint is None
    assert settings.retail_knowledge_search_api_key is None


def test_backend_and_search_fields_bind_from_the_environment(
    no_backend_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EGPT_RETAIL_KNOWLEDGE_BACKEND", "pgvector")
    monkeypatch.setenv(
        "EGPT_RETAIL_KNOWLEDGE_SEARCH_ENDPOINT", "https://x.search.windows.net"
    )
    monkeypatch.setenv("EGPT_RETAIL_KNOWLEDGE_SEARCH_API_KEY", "k")
    monkeypatch.setenv("EGPT_RETAIL_KNOWLEDGE_SEARCH_INDEX", "prose-768")
    settings = RetailSettings()
    assert settings.retail_knowledge_backend == "pgvector"
    assert settings.retail_knowledge_search_endpoint == "https://x.search.windows.net"
    assert settings.retail_knowledge_search_api_key == "k"
    assert settings.retail_knowledge_search_index == "prose-768"


def test_an_unknown_backend_is_refused_at_settings_time(
    no_backend_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EGPT_RETAIL_KNOWLEDGE_BACKEND", "qdrant")
    with pytest.raises(Exception):
        RetailSettings()


# --- the factory ------------------------------------------------------------------------


def test_pgvector_branch_passes_exactly_what_the_call_sites_did(
    no_backend_env: None, fake_pgvector: type, fake_azure: type
) -> None:
    embedder = object()
    settings = RetailSettings(
        retail_knowledge_backend="pgvector",
        retail_knowledge_table="egpt_retail_prose_cookbook",
        retail_db_url="postgresql+psycopg://ai:ai@localhost:5532/ai",
    )
    db = build_knowledge_vector_db(settings, embedder)
    assert isinstance(db, fake_pgvector)
    assert db.kwargs == {  # type: ignore[attr-defined]
        "table_name": "egpt_retail_prose_cookbook",
        "db_url": "postgresql+psycopg://ai:ai@localhost:5532/ai",
        "search_type": SearchType.hybrid,
        "embedder": embedder,
    }
    assert fake_azure.instances == []  # type: ignore[attr-defined]


def test_azure_branch_passes_index_endpoint_key_embedder_and_hybrid(
    no_backend_env: None, fake_pgvector: type, fake_azure: type
) -> None:
    embedder = object()
    settings = RetailSettings(
        retail_knowledge_backend="azure_ai_search",
        retail_knowledge_search_index="egpt-retail-knowledge",
        retail_knowledge_search_endpoint="https://svc.search.windows.net",
        retail_knowledge_search_api_key="secret",
    )
    db = build_knowledge_vector_db(settings, embedder)
    assert isinstance(db, fake_azure)
    assert db.kwargs == {  # type: ignore[attr-defined]
        "index_name": "egpt-retail-knowledge",
        "endpoint": "https://svc.search.windows.net",
        "api_key": "secret",
        "embedder": embedder,
        "search_type": SearchType.hybrid,
    }
    assert fake_pgvector.instances == []  # type: ignore[attr-defined]


def test_azure_key_may_be_unset_and_is_passed_through_as_none(
    no_backend_env: None, fake_azure: type
) -> None:
    """The class resolves EGPT_KNOWLEDGE_AZURE_SEARCH_API_KEY / ambient identity itself."""
    settings = RetailSettings(
        retail_knowledge_search_endpoint="https://svc.search.windows.net",
    )
    db = build_knowledge_vector_db(settings, object())
    assert db.kwargs["api_key"] is None  # type: ignore[attr-defined]


def test_the_default_backend_refuses_with_no_endpoint_naming_the_variable(
    no_backend_env: None, fake_pgvector: type, fake_azure: type
) -> None:
    """Point-of-use refusal, like build_warehouse_engine: nothing is built, nothing is
    imported, and the message names both the variable and the laptop escape hatch."""
    settings = RetailSettings()
    with pytest.raises(RuntimeError) as excinfo:
        build_knowledge_vector_db(settings, object())
    message = str(excinfo.value)
    assert "EGPT_RETAIL_KNOWLEDGE_SEARCH_ENDPOINT" in message
    assert "EGPT_RETAIL_KNOWLEDGE_BACKEND=pgvector" in message
    assert fake_azure.instances == []  # type: ignore[attr-defined]
    assert fake_pgvector.instances == []  # type: ignore[attr-defined]


def test_azure_branch_does_not_import_pgvector_and_vice_versa(
    no_backend_env: None, monkeypatch: pytest.MonkeyPatch, fake_azure: type
) -> None:
    """The lazy imports are the point: a deployment on one backend must not need the
    other's client library at construction."""
    import egpt.integrations.vectordb.pgvector as pg

    def _boom(**_: Any) -> None:
        raise AssertionError("PgVector was constructed on the Azure branch")

    monkeypatch.setattr(pg, "PgVector", _boom)
    settings = RetailSettings(
        retail_knowledge_search_endpoint="https://svc.search.windows.net",
    )
    assert isinstance(build_knowledge_vector_db(settings, object()), fake_azure)


# --- the entrypoints --------------------------------------------------------------------


@pytest.mark.skipif(
    importlib.util.find_spec("unstructured") is None,
    reason="load_knowledge's markdown reader needs the optional `unstructured` chunker",
)
def test_load_knowledge_builds_its_store_through_the_factory(
    no_backend_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from egpt_retail.entrypoints import load_knowledge as lk
    from egpt_retail.knowledge import vector as vector_module
    from egpt_retail.knowledge.prose import IngestionReport

    (tmp_path / "01-doc.md").write_text("# A heading\n\nA body.\n")
    calls: List[Dict[str, Any]] = []

    class _Store:
        """Enough of a VectorDb for Knowledge.__post_init__ to accept it unopened."""

        def exists(self) -> bool:
            return True

    sentinel = _Store()

    def _factory(settings: RetailSettings, embedder: Any) -> Any:
        calls.append({"settings": settings, "embedder": embedder})
        return sentinel

    built: List[Any] = []

    def _ingest(
        knowledge: Any, documents: Any, *, reader: Any = None
    ) -> IngestionReport:
        built.append(knowledge.vector_db)
        return IngestionReport(documents=len(documents), origin="shipped", names=())

    monkeypatch.setattr(vector_module, "build_knowledge_vector_db", _factory)
    monkeypatch.setattr(lk, "ingest_corpus", _ingest)

    class _Embedder:
        embedder = object()
        embedding_version = "m|d8|cos|v1"

    settings = RetailSettings(
        retail_corpus_path=str(tmp_path),
        retail_knowledge_backend="pgvector",
        retail_knowledge_table="t",
    )
    lines = lk._load_prose(settings, _Embedder())
    assert calls == [{"settings": settings, "embedder": _Embedder.embedder}]
    assert built == [sentinel]
    assert lines == ["prose: 1 shipped document(s) ingested into t"]


def test_load_knowledge_report_names_the_index_on_azure(no_backend_env: None) -> None:
    from egpt_retail.entrypoints.load_knowledge import _prose_destination

    settings = RetailSettings(
        retail_knowledge_backend="azure_ai_search",
        retail_knowledge_search_index="prose-1024",
    )
    assert _prose_destination(settings) == "azure_ai_search:prose-1024"
    assert _prose_destination(RetailSettings(retail_knowledge_backend="pgvector")) == (
        "egpt_retail_domain_prose"
    )


@pytest.mark.parametrize("name", ["load_knowledge.py", "suggest.py"])
def test_no_entrypoint_names_a_backend_or_the_deprecated_shim(name: str) -> None:
    """``suggest`` opens the control plane and Neo4j before it reaches the knowledge store,
    so its wiring is checked at the source rather than by driving it; the same check covers
    ``load_knowledge`` for symmetry with the behavioural test above."""
    source = (_ENTRYPOINTS / name).read_text()
    assert "build_knowledge_vector_db" in source
    assert "PgVector(" not in source
    assert "egpt.vectordb.pgvector" not in source, "the deprecated shim import"
