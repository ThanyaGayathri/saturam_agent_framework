"""ComponentConfigCache and fetch_config_row (EGPT-2.5).

The cache stores raw config rows from db.get_config. Two invariants are load-bearing and
pinned hard here:

* every row that crosses fetch_config_row is a private copy -- a shared row would let one
  request's hydration mutate another tenant's cached config (from_dict shallow-copies and
  hands nested dicts to constructors);
* the key includes the db identity and the caller's tenant, so the same component id never
  crosses tenants or db handles through the cache.
"""

import copy
import time
from unittest.mock import MagicMock

import pytest

from egpt.registry import Registry
from egpt.registry.hydrate import ComponentConfigCache, NoPublishedVersionError, fetch_config_row

ROW = {
    "component_id": "agent-1",
    "config": {"model": "openai:gpt-5.5", "instructions": ["be helpful"]},
    "tenant_id": "acme",
    "version": 3,
    "stage": "published",
}


def _db(row=ROW, db_id="db-1"):
    db = MagicMock()
    db.id = db_id
    # A fresh deep copy per call, like a real db materializing a row per query -- a shared
    # return_value would alias ROW's nested dicts across tests.
    db.get_config = MagicMock(side_effect=lambda **kw: copy.deepcopy(row) if row else None)
    return db


def _registry(cache):
    return Registry(component_config_cache=cache)


class TestCacheMechanics:
    def test_get_returns_what_was_put(self):
        cache = ComponentConfigCache(ttl_seconds=30)
        key = cache.make_key(_db(), "agent-1", version=3, tenant_id="acme")
        cache.put(key, dict(ROW))
        assert cache.get(key)["version"] == 3

    def test_ttl_zero_disables_the_cache(self):
        cache = ComponentConfigCache(ttl_seconds=0)
        key = cache.make_key(_db(), "agent-1")
        cache.put(key, dict(ROW))
        assert not cache.enabled
        assert cache.get(key) is None

    def test_entries_expire_after_the_ttl(self, monkeypatch):
        cache = ComponentConfigCache(ttl_seconds=10)
        key = cache.make_key(_db(), "agent-1")
        now = time.monotonic()
        monkeypatch.setattr(time, "monotonic", lambda: now)
        cache.put(key, dict(ROW))
        monkeypatch.setattr(time, "monotonic", lambda: now + 11)
        assert cache.get(key) is None

    def test_lru_bound_evicts_the_oldest(self):
        cache = ComponentConfigCache(ttl_seconds=30, max_entries=2)
        db = _db()
        keys = [cache.make_key(db, f"agent-{i}") for i in range(3)]
        for key in keys:
            cache.put(key, dict(ROW))
        assert cache.get(keys[0]) is None
        assert cache.get(keys[1]) is not None
        assert cache.get(keys[2]) is not None

    def test_tenant_is_part_of_the_key(self):
        cache = ComponentConfigCache(ttl_seconds=30)
        db = _db()
        cache.put(cache.make_key(db, "agent-1", tenant_id="acme"), dict(ROW))
        assert cache.get(cache.make_key(db, "agent-1", tenant_id="globex")) is None
        assert cache.get(cache.make_key(db, "agent-1", tenant_id=None)) is None

    def test_db_identity_is_part_of_the_key(self):
        cache = ComponentConfigCache(ttl_seconds=30)
        cache.put(cache.make_key(_db(db_id="db-1"), "agent-1"), dict(ROW))
        assert cache.get(cache.make_key(_db(db_id="db-2"), "agent-1")) is None

    def test_invalidate_drops_every_entry_for_the_component(self):
        cache = ComponentConfigCache(ttl_seconds=30)
        db = _db()
        for tenant, version in [("acme", 1), ("acme", None), ("globex", 2)]:
            cache.put(cache.make_key(db, "agent-1", version=version, tenant_id=tenant), dict(ROW))
        cache.put(cache.make_key(db, "agent-2"), dict(ROW))

        assert cache.invalidate("agent-1") == 3
        assert cache.get(cache.make_key(db, "agent-2")) is not None

    def test_invalidate_can_narrow_by_tenant_and_version(self):
        cache = ComponentConfigCache(ttl_seconds=30)
        db = _db()
        cache.put(cache.make_key(db, "agent-1", version=1, tenant_id="acme"), dict(ROW))
        cache.put(cache.make_key(db, "agent-1", version=2, tenant_id="acme"), dict(ROW))

        assert cache.invalidate("agent-1", tenant_id="acme", version=2) == 1
        assert cache.get(cache.make_key(db, "agent-1", version=1, tenant_id="acme")) is not None


class TestFetchConfigRow:
    def test_miss_reads_the_db_and_populates_the_cache(self):
        cache = ComponentConfigCache(ttl_seconds=30)
        db = _db()
        row = fetch_config_row(db, "agent-1", "agent", version=3, tenant_id="acme", registry=_registry(cache))
        assert row["version"] == 3
        assert db.get_config.call_count == 1

        fetch_config_row(db, "agent-1", "agent", version=3, tenant_id="acme", registry=_registry(cache))
        assert db.get_config.call_count == 1  # served from cache

    def test_tenant_id_reaches_the_db_read(self):
        db = _db()
        fetch_config_row(db, "agent-1", "agent", tenant_id="acme", registry=None)
        db.get_config.assert_called_once_with(component_id="agent-1", version=None, label=None, tenant_id="acme")

    def test_a_hit_is_a_private_copy(self):
        """Mutating a returned row must not poison the cache (or another tenant's read)."""
        cache = ComponentConfigCache(ttl_seconds=30)
        db = _db()
        registry = _registry(cache)
        first = fetch_config_row(db, "agent-1", "agent", registry=registry)
        first["config"]["instructions"].append("MUTATED")
        first["config"]["model"] = "MUTATED"

        second = fetch_config_row(db, "agent-1", "agent", registry=registry)
        assert second["config"]["model"] == "openai:gpt-5.5"
        assert second["config"]["instructions"] == ["be helpful"]

    def test_the_row_stored_on_a_miss_is_also_a_private_copy(self):
        """The object returned to the missing caller must not alias the cached one."""
        cache = ComponentConfigCache(ttl_seconds=30)
        db = _db()
        registry = _registry(cache)
        miss = fetch_config_row(db, "agent-1", "agent", registry=registry)
        miss["config"]["model"] = "MUTATED"

        hit = fetch_config_row(db, "agent-1", "agent", registry=registry)
        assert hit["config"]["model"] == "openai:gpt-5.5"

    def test_labeled_lookups_bypass_the_cache(self):
        cache = ComponentConfigCache(ttl_seconds=30)
        db = _db()
        registry = _registry(cache)
        fetch_config_row(db, "agent-1", "agent", label="stable", registry=registry)
        fetch_config_row(db, "agent-1", "agent", label="stable", registry=registry)
        assert db.get_config.call_count == 2

    def test_not_found_is_not_cached(self):
        cache = ComponentConfigCache(ttl_seconds=30)
        db = _db(row=None)
        db.get_config = MagicMock(return_value=None)
        registry = _registry(cache)
        assert fetch_config_row(db, "missing", "agent", registry=registry) is None
        assert fetch_config_row(db, "missing", "agent", registry=registry) is None
        assert db.get_config.call_count == 2

    def test_no_registry_means_no_cache_and_a_plain_read(self):
        db = _db()
        assert fetch_config_row(db, "agent-1", "agent", registry=None)["version"] == 3
        assert db.get_config.call_count == 1


class TestPublishedOnly:
    def test_a_draft_row_is_refused_when_published_only(self):
        db = _db(row={**ROW, "stage": "draft"})
        registry = Registry(hydrate_published_only=True)
        with pytest.raises(NoPublishedVersionError, match="agent-1"):
            fetch_config_row(db, "agent-1", "agent", registry=registry)

    def test_an_explicit_draft_version_is_also_refused(self):
        """Production should not run drafts even when asked for one by number."""
        db = _db(row={**ROW, "stage": "draft"})
        registry = Registry(hydrate_published_only=True)
        with pytest.raises(NoPublishedVersionError):
            fetch_config_row(db, "agent-1", "agent", version=3, registry=registry)

    def test_a_published_row_passes(self):
        db = _db()
        registry = Registry(hydrate_published_only=True)
        assert fetch_config_row(db, "agent-1", "agent", registry=registry) is not None

    def test_default_off_serves_drafts(self):
        db = _db(row={**ROW, "stage": "draft"})
        assert fetch_config_row(db, "agent-1", "agent", registry=Registry()) is not None
