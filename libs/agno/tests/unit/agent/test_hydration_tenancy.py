"""Tenant scoping of agent hydration (EGPT-2.5).

Before this ticket the hydration path read stored configs with no tenant predicate, so
`POST /agents/{id}/runs` could hydrate another tenant's component even though
`GET /components/{id}` 404'd (docs/tenancy.md section 5). These tests pin the closure: the
caller's tenant reaches the DB read, a cross-tenant read is indistinguishable from a missing
component, and the policy re-check runs as the caller rather than as whatever tenant the row
claims to belong to.
"""

import copy
from unittest.mock import MagicMock

import pytest

from egpt.agent.agent import Agent, get_agent_by_id, get_agents
from egpt.db.base import BaseDb
from egpt.registry import Registry
from egpt.registry.hydrate import ComponentConfigCache, HydrationError, NoPublishedVersionError
from egpt.registry.policy import CapabilityPolicy, PolicyViolationError

ROW = {
    "component_id": "agent-1",
    "config": {"model": "openai:gpt-5.5", "name": "Stored Agent"},
    "tenant_id": "acme",
    "version": 1,
    "stage": "published",
}


def _mock_db(row=ROW):
    """A BaseDb whose reads honor the tenant predicate the way the real backends do."""
    abstract = {
        name: MagicMock() for name in dir(BaseDb) if getattr(getattr(BaseDb, name, None), "__isabstractmethod__", False)
    }
    db = type("MockDb", (BaseDb,), abstract)()
    db.id = "test-db"

    def get_config(component_id=None, version=None, label=None, tenant_id=None, **_):
        if row is None:
            return None
        if tenant_id is not None and tenant_id != row.get("tenant_id"):
            return None
        return copy.deepcopy(row)

    db.get_config = MagicMock(side_effect=get_config)
    db.list_components = MagicMock(return_value=([{"component_id": "agent-1", "current_version": 1}], 1))
    return db


class TestTenantScopedRead:
    def test_the_callers_tenant_reaches_the_db_read(self):
        db = _mock_db()
        get_agent_by_id(db=db, id="agent-1", tenant_id="acme")
        assert db.get_config.call_args.kwargs["tenant_id"] == "acme"

    def test_a_cross_tenant_component_is_indistinguishable_from_a_missing_one(self):
        db = _mock_db()
        assert get_agent_by_id(db=db, id="agent-1", tenant_id="globex") is None

    def test_an_unscoped_call_still_resolves(self):
        """tenant_id=None keeps the pre-2.5 behavior for programmatic callers."""
        db = _mock_db()
        agent = get_agent_by_id(db=db, id="agent-1")
        assert agent is not None and agent.id == "agent-1"

    def test_the_listing_is_scoped_too(self):
        db = _mock_db()
        get_agents(db=db, tenant_id="acme")
        assert db.list_components.call_args.kwargs["tenant_id"] == "acme"
        assert db.get_config.call_args.kwargs["tenant_id"] == "acme"


class TestCallerTenantSelectsThePolicy:
    """The policy must be selected by who is asking, not by what the row claims (EGPT-2.5).

    The row's tenant is self-attested; before this ticket a caller hydrating another tenant's
    row was policy-checked against the *owner's* allow-list.
    """

    def test_the_callers_tenant_wins_over_the_rows(self):
        # acme (the row's tenant) may use openai; globex may not. The row would pass its own
        # tenant's policy, so a refusal proves the caller's tenant was the one checked.
        policy = CapabilityPolicy.from_dict({"tenants": {"acme": {"models": ["openai"]}, "globex": {"models": []}}})
        row = {**ROW, "tenant_id": "globex"}
        db = _mock_db(row=row)

        with pytest.raises(PolicyViolationError) as excinfo:
            get_agent_by_id(db=db, id="agent-1", tenant_id="globex", registry=Registry(capability_policy=policy))
        assert excinfo.value.tenant_id == "globex"

    def test_unscoped_calls_fall_back_to_the_rows_tenant(self):
        """Identical to pre-2.5 behavior when no caller tenant exists."""
        policy = CapabilityPolicy.from_dict({"tenants": {"acme": {"models": []}}})
        db = _mock_db()
        with pytest.raises(PolicyViolationError) as excinfo:
            get_agent_by_id(db=db, id="agent-1", registry=Registry(capability_policy=policy))
        assert excinfo.value.tenant_id == "acme"


class TestErrorSurfacing:
    def test_an_existing_but_broken_row_raises_hydration_error(self):
        """None is reserved for not-found; broken configs must not render as 404."""
        db = _mock_db(row={**ROW, "config": None})
        with pytest.raises(HydrationError, match="agent-1"):
            get_agent_by_id(db=db, id="agent-1", tenant_id="acme")

    def test_published_only_refuses_a_draft(self):
        db = _mock_db(row={**ROW, "stage": "draft"})
        with pytest.raises(NoPublishedVersionError):
            get_agent_by_id(db=db, id="agent-1", registry=Registry(hydrate_published_only=True))


class TestCacheReadThrough:
    def test_a_second_hydration_serves_from_the_cache(self):
        db = _mock_db()
        registry = Registry(component_config_cache=ComponentConfigCache(ttl_seconds=30))
        get_agent_by_id(db=db, id="agent-1", tenant_id="acme", registry=registry)
        get_agent_by_id(db=db, id="agent-1", tenant_id="acme", registry=registry)
        assert db.get_config.call_count == 1

    def test_the_cache_does_not_leak_across_tenants(self):
        """A hit for acme must not satisfy globex; the miss goes to the (scoped) db."""
        db = _mock_db()
        registry = Registry(component_config_cache=ComponentConfigCache(ttl_seconds=30))
        assert get_agent_by_id(db=db, id="agent-1", tenant_id="acme", registry=registry) is not None
        assert get_agent_by_id(db=db, id="agent-1", tenant_id="globex", registry=registry) is None
        assert db.get_config.call_count == 2

    def test_hydration_does_not_poison_the_cached_row(self):
        """from_dict mutates its input; the second hydration must see the original config."""
        db = _mock_db()
        registry = Registry(component_config_cache=ComponentConfigCache(ttl_seconds=30))
        first = get_agent_by_id(db=db, id="agent-1", tenant_id="acme", registry=registry)
        second = get_agent_by_id(db=db, id="agent-1", tenant_id="acme", registry=registry)
        assert isinstance(first, Agent) and isinstance(second, Agent)
        assert second.name == "Stored Agent"
        assert db.get_config.call_count == 1

    def test_invalidation_forces_a_fresh_read(self):
        db = _mock_db()
        cache = ComponentConfigCache(ttl_seconds=30)
        registry = Registry(component_config_cache=cache)
        get_agent_by_id(db=db, id="agent-1", tenant_id="acme", registry=registry)
        cache.invalidate("agent-1")
        get_agent_by_id(db=db, id="agent-1", tenant_id="acme", registry=registry)
        assert db.get_config.call_count == 2


class TestAgentLoad:
    """Agent.load is a separate read path and needs its own scoping."""

    def test_load_is_tenant_scoped(self):
        db = _mock_db()
        assert Agent.load("agent-1", db=db, tenant_id="globex") is None
        loaded = Agent.load("agent-1", db=db, tenant_id="acme")
        assert loaded is not None and loaded.id == "agent-1"
