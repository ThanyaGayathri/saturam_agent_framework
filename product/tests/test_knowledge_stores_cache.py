"""``build_knowledge_stores`` caches by what decides the store, and that includes the backend.

The cache exists because building the prose store per question leaked a connection pool per
question. A cache keyed on too little is worse than none: two settings that name different
backends would share one entry, and a deployment would be handed a store built for the other
backend -- prose written to one service and read from another, with no error.
"""

from __future__ import annotations

import sys
import types
from typing import Any, List

import pytest

from egpt_retail.app import stores
from egpt_retail.app.settings import RetailSettings


@pytest.fixture
def isolated_cache(monkeypatch: pytest.MonkeyPatch) -> List[RetailSettings]:
    """An empty cache, a construction that records what it was asked to build, and a stand-in
    ontology store so a cache hit needs no Neo4j. Returns the list of build requests."""
    for name in (
        "EGPT_RETAIL_KNOWLEDGE_BACKEND",
        "EGPT_RETAIL_KNOWLEDGE_SEARCH_ENDPOINT",
        "EGPT_RETAIL_KNOWLEDGE_SEARCH_API_KEY",
        "EGPT_RETAIL_KNOWLEDGE_SEARCH_INDEX",
        "EGPT_RETAIL_KNOWLEDGE_TABLE",
        "EGPT_RETAIL_DB_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(stores, "_STORES", {})

    built: List[RetailSettings] = []

    def fake_build(settings: RetailSettings, driver: Any) -> stores.KnowledgeStores:
        built.append(settings)
        return stores.KnowledgeStores(
            knowledge=f"knowledge-{len(built)}",
            ontology=None,
            embedder="embedder",
            embedder_model_id="m",
            dimensions=3,
        )

    monkeypatch.setattr(stores, "_build_knowledge_stores", fake_build)

    fake_module = types.ModuleType("egpt_retail.ontology.store")
    fake_module.OntologyStore = lambda driver, embedder: ("ontology", driver)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "egpt_retail.ontology.store", fake_module)
    return built


def _azure(
    index: str = "prose-a", endpoint: str = "https://a.search.windows.net"
) -> RetailSettings:
    return RetailSettings(
        retail_knowledge_backend="azure_ai_search",
        retail_knowledge_search_endpoint=endpoint,
        retail_knowledge_search_index=index,
    )


def test_the_same_settings_build_once(isolated_cache: List[RetailSettings]) -> None:
    settings = RetailSettings(retail_knowledge_backend="pgvector")
    first = stores.build_knowledge_stores(settings, driver="d1")
    second = stores.build_knowledge_stores(settings, driver="d2")
    assert len(isolated_cache) == 1
    assert first.knowledge == second.knowledge


def test_a_different_backend_is_not_a_cache_hit(
    isolated_cache: List[RetailSettings],
) -> None:
    """Everything else equal, only the backend differs -- the case a key of just
    (mode, table, db url) cannot tell apart."""
    stores.build_knowledge_stores(
        RetailSettings(retail_knowledge_backend="pgvector"), driver="d"
    )
    stores.build_knowledge_stores(_azure(), driver="d")
    assert len(isolated_cache) == 2


def test_a_different_azure_index_or_endpoint_is_not_a_cache_hit(
    isolated_cache: List[RetailSettings],
) -> None:
    stores.build_knowledge_stores(_azure(index="prose-768"), driver="d")
    stores.build_knowledge_stores(_azure(index="prose-1024"), driver="d")
    stores.build_knowledge_stores(
        _azure(index="prose-1024", endpoint="https://b.search.windows.net"), driver="d"
    )
    assert len(isolated_cache) == 3


def test_the_api_key_does_not_split_the_cache(
    isolated_cache: List[RetailSettings],
) -> None:
    """A credential does not decide which store this is; rotating one must not rebuild it,
    and it must not sit in a dict key."""
    base = dict(
        retail_knowledge_backend="azure_ai_search",
        retail_knowledge_search_endpoint="https://a.search.windows.net",
    )
    stores.build_knowledge_stores(
        RetailSettings(**base, retail_knowledge_search_api_key="k1"), driver="d"
    )
    stores.build_knowledge_stores(
        RetailSettings(**base, retail_knowledge_search_api_key="k2"), driver="d"
    )
    assert len(isolated_cache) == 1
