"""AZ-B: K1 memory selects its vector substrate by config (``docs/plan/azure-migration-plan.md``).

No Azure AI Search service, no Postgres. What is proven here is the config seam: which dict
each backend produces, that the Azure one carries exactly the kwargs the registered substrate
names, that a missing endpoint is refused naming the ``EGPT_RETAIL_*`` variable, and that the
refusal surfaces where K1's other startup faults do. The one construction test mocks the single
transport call the substrate makes at construction (``create_or_update_index``) and records --
rather than hides -- that construction is *not* lazy.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest
from egpt.memory.errors import MemoryProviderConfigError
from egpt.providers.model_provider import ProviderMode

from egpt_retail.app.components import build_memory_provider
from egpt_retail.app.factory import build_agent_os, memory_lifespan_for
from egpt_retail.app.settings import DEFAULT_DB_URL, RetailSettings
from egpt_retail.memory.config import (
    EF_SEARCH,
    build_memory_config,
    build_memory_resolver,
    build_memory_vector_settings,
)

ENDPOINT = "https://egpt-test.search.windows.net"


@pytest.fixture(autouse=True)
def _no_ambient_search_env(monkeypatch):
    """The substrate's own fallbacks must never rescue a test that means to prove a refusal."""
    for name in (
        "EGPT_RETAIL_MEMORY_BACKEND",
        "EGPT_RETAIL_MEMORY_SEARCH_ENDPOINT",
        "EGPT_RETAIL_MEMORY_SEARCH_API_KEY",
        "EGPT_RETAIL_MEMORY_SEARCH_INDEX",
        "EGPT_MEMORY_AZURE_SEARCH_ENDPOINT",
        "EGPT_MEMORY_AZURE_SEARCH_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


def test_the_default_backend_is_azure_ai_search():
    """The default is the production truth (same posture as retail_provider_mode); a laptop
    sets pgvector explicitly, as compose.yaml and values.local.yaml do."""
    settings = RetailSettings()
    assert settings.retail_memory_backend == "azure_ai_search"
    assert settings.retail_memory_search_index == "egpt-retail-memory"
    assert settings.retail_memory_search_endpoint is None
    assert settings.retail_memory_search_api_key is None


def test_the_env_names_bind(monkeypatch):
    monkeypatch.setenv("EGPT_RETAIL_MEMORY_BACKEND", "pgvector")
    monkeypatch.setenv("EGPT_RETAIL_MEMORY_SEARCH_ENDPOINT", ENDPOINT)
    monkeypatch.setenv("EGPT_RETAIL_MEMORY_SEARCH_API_KEY", "k")
    monkeypatch.setenv("EGPT_RETAIL_MEMORY_SEARCH_INDEX", "other-index")
    settings = RetailSettings()
    assert settings.retail_memory_backend == "pgvector"
    assert settings.retail_memory_search_endpoint == ENDPOINT
    assert settings.retail_memory_search_api_key == "k"
    assert settings.retail_memory_search_index == "other-index"


def test_an_unknown_backend_is_refused_at_settings_time():
    with pytest.raises(ValueError):
        RetailSettings(retail_memory_backend="qdrant")  # type: ignore[arg-type]


def test_the_pgvector_dict_is_unchanged_by_the_flip():
    """Byte-identical to what build_memory_config produced before AZ-B, so the laptop and
    kind paths keep working without a re-provision."""
    settings = RetailSettings(retail_memory_backend="pgvector")
    assert build_memory_vector_settings(settings) == {
        "backend": "pgvector",
        "db_url": DEFAULT_DB_URL,
        "table": "memory_vectors",
        "ef_search": 256,
    }
    config = build_memory_config(settings)
    assert config.provider == "co_located"
    assert config.mode is ProviderMode.FRONTIER
    assert config.graph == {}


def test_the_azure_dict_carries_exactly_the_substrates_kwargs():
    pytest.importorskip(
        "azure.search.documents", reason="AZ-B's substrate needs azure-search-documents"
    )
    from egpt.integrations.memory.vector.azure_ai_search import (
        AzureAISearchMemorySubstrate,
    )

    settings = RetailSettings(
        retail_memory_search_endpoint=ENDPOINT,
        retail_memory_search_api_key="secret",
        retail_memory_search_index="egpt-retail-memory-test",
    )
    vector = build_memory_vector_settings(settings)
    assert vector == {
        "backend": "azure_ai_search",
        "endpoint": ENDPOINT,
        "api_key": "secret",
        "index_name": "egpt-retail-memory-test",
        "ef_search": EF_SEARCH,
    }
    # Every key except `backend` is passed as a keyword to the constructor, and `mode` is
    # added by resolve_substrate because the constructor names it. Pin both, so a renamed
    # constructor parameter fails here rather than as a TypeError inside discovery.construct.
    params = inspect.signature(AzureAISearchMemorySubstrate.__init__).parameters
    assert set(vector) - {"backend"} <= set(params)
    assert "mode" in params and "mode" not in vector
    assert build_memory_config(settings).vector == vector


def test_a_missing_endpoint_is_refused_naming_the_retail_variable():
    """Refused here, not in the substrate: the substrate names
    EGPT_MEMORY_AZURE_SEARCH_ENDPOINT, a variable this deployment never sets."""
    with pytest.raises(MemoryProviderConfigError) as excinfo:
        build_memory_vector_settings(RetailSettings())
    message = str(excinfo.value)
    assert "EGPT_RETAIL_MEMORY_SEARCH_ENDPOINT" in message
    assert "EGPT_RETAIL_MEMORY_BACKEND=pgvector" in message, (
        "the refusal must name the way out on a machine without the service"
    )
    assert "EGPT_MEMORY_AZURE_SEARCH_ENDPOINT" not in message


def test_a_blank_endpoint_is_a_missing_endpoint():
    with pytest.raises(MemoryProviderConfigError):
        build_memory_resolver(RetailSettings(retail_memory_search_endpoint="   "))


def test_the_refusal_surfaces_through_build_memory_provider():
    """The same fault, one layer up: build_memory_provider is what the lifespan and the
    consolidator call. Nothing was constructed before the refusal (no client, no network)."""
    with pytest.raises(MemoryProviderConfigError) as excinfo:
        build_memory_provider(RetailSettings(retail_customer_slug="acme"))
    assert "EGPT_RETAIL_MEMORY_SEARCH_ENDPOINT" in str(excinfo.value)


def test_no_slug_still_short_circuits_before_the_backend_is_consulted():
    """G1's ordering is unchanged: an unset slug returns None so assert_memory_wired can
    report it with company; the backend is not even looked at."""
    assert build_memory_provider(RetailSettings(retail_customer_slug=None)) is None


def test_import_stays_pure_with_the_azure_default_and_no_endpoint(monkeypatch):
    """app/main.py calls build_agent_os at import. The default backend needing an endpoint
    must not turn import into a refusal; the refusal belongs to the lifespan."""
    monkeypatch.delenv("EGPT_RETAIL_CUSTOMER_SLUG", raising=False)
    monkeypatch.setenv(
        "EGPT_RETAIL_DB_URL", "postgresql+psycopg://nobody:nope@127.0.0.1:1/none"
    )
    settings = RetailSettings()
    assert settings.retail_memory_backend == "azure_ai_search"
    assert build_agent_os(settings) is not None


def test_the_lifespan_reports_the_missing_endpoint_as_one_named_fault():
    """Startup, not import: the lifespan builds the provider and the fault it raises is the
    config error naming the variable -- not a traceback from inside the SDK."""

    class _Agent:
        id = "synonym_proposer"
        memory_provider = None

    lifespan = memory_lifespan_for(
        RetailSettings(retail_customer_slug="acme"), [_Agent()]
    )

    async def _boot():
        async with lifespan(app=None, agent_os=None):
            pass

    with pytest.raises(MemoryProviderConfigError) as excinfo:
        asyncio.run(_boot())
    assert "EGPT_RETAIL_MEMORY_SEARCH_ENDPOINT" in str(excinfo.value)


def test_a_provider_builds_from_the_azure_config_with_one_schema_round_trip():
    """Through the real seam -- resolver, registry, substrate -- against a fake endpoint.

    Construction is **not** lazy, and this test records that rather than forcing it:
    ``AzureAISearchMemorySubstrate.__init__`` calls ``init_schema`` (``auto_init_schema=True``,
    the same self-provisioning posture as pgvector), which is one ``create_or_update_index``
    HTTPS call. That single call is mocked here; nothing else is. The embedder is Mode B's
    default and is consulted only on write and search, so no AZURE_EMBEDDER key is needed.
    """
    pytest.importorskip(
        "azure.search.documents", reason="AZ-B's substrate needs azure-search-documents"
    )
    from unittest.mock import patch

    from egpt.integrations.memory.vector.azure_ai_search import (
        AzureAISearchMemorySubstrate,
    )

    settings = RetailSettings(
        retail_customer_slug="acme",
        retail_memory_search_endpoint=ENDPOINT,
        retail_memory_search_api_key="secret",
        retail_memory_search_index="egpt-retail-memory-test",
    )
    target = "azure.search.documents.indexes.SearchIndexClient.create_or_update_index"
    with (
        patch(target) as create,
        patch("azure.search.documents.SearchClient.search") as search,
    ):
        provider = build_memory_provider(settings)
        try:
            assert provider is not None
            substrate = provider.vector
            assert isinstance(substrate, AzureAISearchMemorySubstrate)
            assert substrate.endpoint == ENDPOINT
            assert substrate.index_name == "egpt-retail-memory-test"
            assert substrate.ef_search == EF_SEARCH
            assert substrate.mode is ProviderMode.FRONTIER, (
                "resolve_substrate forwards config.mode to a constructor that names it"
            )
            assert substrate.dim == 1024, (
                "Mode B's truncation, kept per AZ-B's decision"
            )
            # The whole construction cost: one index definition, no document traffic.
            assert create.call_count == 1
            index = create.call_args.args[0]
            assert index.name == "egpt-retail-memory-test"
            search.assert_not_called()
        finally:
            provider.close()
