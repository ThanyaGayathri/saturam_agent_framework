"""Component mutations evict the hydration config cache (EGPT-2.5).

The components router is the invalidation seam until EGPT-2.6 lifecycle events land: the
worker that processes a mutation drops the component's cached rows immediately; other
workers (and writers that bypass this router) converge within the cache TTL. Every mutating
route is pinned here, and reads are pinned NOT to evict.
"""

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from egpt.db.base import BaseDb
from egpt.os.routers.components import get_components_router
from egpt.os.settings import EgptServingSettings
from egpt.registry import Registry
from egpt.registry.hydrate import ComponentConfigCache

VALID_AGENT_CONFIG = {"model": "openai:gpt-5.5"}

CONFIG_ROW = {
    "component_id": "agent-1",
    "version": 1,
    "config": dict(VALID_AGENT_CONFIG),
    "stage": "draft",
    "created_at": 1234567890,
}


def _mock_db():
    abstract = {
        name: MagicMock() for name in dir(BaseDb) if getattr(getattr(BaseDb, name, None), "__isabstractmethod__", False)
    }
    db = type("MockDb", (BaseDb,), abstract)()
    db.id = "test-db"
    db.get_component = MagicMock(
        return_value={
            "component_id": "agent-1",
            "component_type": "agent",
            "name": "Agent",
            "created_at": 1234567890,
        }
    )
    db.upsert_component = MagicMock(
        return_value={
            "component_id": "agent-1",
            "component_type": "agent",
            "name": "Agent",
            "created_at": 1234567890,
        }
    )
    db.delete_component = MagicMock(return_value=True)
    db.get_config = MagicMock(return_value=dict(CONFIG_ROW))
    db.upsert_config = MagicMock(return_value=dict(CONFIG_ROW))
    db.delete_config = MagicMock(return_value=True)
    db.set_current_version = MagicMock(return_value=True)
    db.list_configs = MagicMock(return_value=[])
    db.list_components = MagicMock(return_value=([], 0))
    db.to_dict = MagicMock(return_value={"type": "postgres", "id": "test-db"})
    return db


@pytest.fixture
def cache():
    cache = ComponentConfigCache(ttl_seconds=30)
    cache.invalidate = MagicMock(wraps=cache.invalidate)
    return cache


@pytest.fixture
def client(cache):
    app = FastAPI()
    registry = Registry(component_config_cache=cache)
    router = get_components_router(os_db=_mock_db(), settings=EgptServingSettings(), registry=registry)
    app.include_router(router)
    return TestClient(app)


class TestMutationsEvict:
    def test_update_component_evicts(self, client, cache):
        assert client.patch("/components/agent-1", json={"name": "Renamed"}).status_code == 200
        cache.invalidate.assert_called_once_with("agent-1")

    def test_delete_component_evicts(self, client, cache):
        assert client.delete("/components/agent-1").status_code == 204
        cache.invalidate.assert_called_once_with("agent-1")

    def test_create_config_evicts(self, client, cache):
        assert client.post("/components/agent-1/configs", json={"config": VALID_AGENT_CONFIG}).status_code in (200, 201)
        cache.invalidate.assert_called_once_with("agent-1")

    def test_update_config_evicts(self, client, cache):
        assert client.patch("/components/agent-1/configs/1", json={"config": VALID_AGENT_CONFIG}).status_code == 200
        cache.invalidate.assert_called_once_with("agent-1")

    def test_delete_config_version_evicts(self, client, cache):
        assert client.delete("/components/agent-1/configs/1").status_code == 204
        cache.invalidate.assert_called_once_with("agent-1")

    def test_set_current_config_evicts(self, client, cache):
        assert client.post("/components/agent-1/configs/1/set-current").status_code == 200
        cache.invalidate.assert_called_once_with("agent-1")


class TestFailedMutationsAndReadsDoNotEvict:
    def test_a_404_mutation_does_not_evict(self, cache):
        # A client whose db misses everything.
        db = _mock_db()
        db.get_component = MagicMock(return_value=None)
        db.delete_component = MagicMock(return_value=False)
        app = FastAPI()
        router = get_components_router(
            os_db=db, settings=EgptServingSettings(), registry=Registry(component_config_cache=cache)
        )
        app.include_router(router)
        missing_client = TestClient(app)

        assert missing_client.delete("/components/ghost").status_code == 404
        cache.invalidate.assert_not_called()

    def test_reads_do_not_evict(self, client, cache):
        assert client.get("/components/agent-1").status_code == 200
        assert client.get("/components/agent-1/configs/current").status_code == 200
        cache.invalidate.assert_not_called()

    def test_a_router_without_a_cache_still_mutates_fine(self):
        """No registry (or no cache parked) must not break the routes."""
        app = FastAPI()
        router = get_components_router(os_db=_mock_db(), settings=EgptServingSettings(), registry=None)
        app.include_router(router)
        client = TestClient(app)
        assert client.patch("/components/agent-1", json={"name": "Renamed"}).status_code == 200
