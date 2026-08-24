"""StudioTools tenant scoping (EGPT-2.5).

Studio bypasses both the HTTP routes and the component getters, so it needs its own tenant:
the operator supplies one at construction and every component read and write is scoped to it.
The default None keeps the pre-2.5 unscoped behavior for single-tenant deployments.
"""

import copy
from unittest.mock import MagicMock

from egpt.db.base import BaseDb
from egpt.registry import Registry
from egpt.tools.studio import StudioTools

AGENT_ROW = {
    "component_id": "agent-1",
    "config": {"name": "Stored Agent", "model": "openai:gpt-5.5"},
    "tenant_id": "acme",
    "version": 1,
    "stage": "published",
}


def _mock_db(row=AGENT_ROW):
    abstract = {
        name: MagicMock() for name in dir(BaseDb) if getattr(getattr(BaseDb, name, None), "__isabstractmethod__", False)
    }
    db = type("MockDb", (BaseDb,), abstract)()
    db.id = "test-db"

    def get_config(component_id=None, version=None, label=None, *, tenant_id, **_):
        if row is None:
            return None
        if tenant_id is not None and tenant_id != row.get("tenant_id"):
            return None
        return copy.deepcopy(row)

    def get_component(component_id=None, component_type=None, *, tenant_id, **_):
        if row is None:
            return None
        if tenant_id is not None and tenant_id != row.get("tenant_id"):
            return None
        return {"component_id": component_id, "component_type": "agent", "current_version": 1}

    db.get_config = MagicMock(side_effect=get_config)
    db.get_component = MagicMock(side_effect=get_component)
    db.list_components = MagicMock(return_value=([], 0))
    db.list_configs = MagicMock(return_value=[])
    db.upsert_component = MagicMock(return_value={})
    db.upsert_config = MagicMock(return_value={"version": 1})
    db.delete_component = MagicMock(return_value=True)
    return db


def _studio(db, tenant_id=None):
    return StudioTools(registry=Registry(), db=db, tenant_id=tenant_id)


class TestReadsAreScoped:
    def test_a_scoped_studio_reads_as_its_tenant(self):
        db = _mock_db()
        studio = _studio(db, tenant_id="acme")
        assert studio._load_agent_from_db("agent-1") is not None
        assert db.get_config.call_args.kwargs["tenant_id"] == "acme"

    def test_a_foreign_tenants_component_is_invisible(self):
        db = _mock_db()
        studio = _studio(db, tenant_id="globex")
        assert studio._load_agent_from_db("agent-1") is None

    def test_the_default_is_unscoped_like_before(self):
        db = _mock_db()
        studio = _studio(db)
        assert studio._load_agent_from_db("agent-1") is not None
        assert db.get_config.call_args.kwargs["tenant_id"] is None


class TestIdProbeSeesEveryTenant:
    """The id-uniqueness probe is the deliberately unscoped one (aicoe-tracker#176).

    A probe scoped to the studio's own tenant misses an id held by a foreign tenant, and the id
    this toolkit then vouches for fails the insert on the global primary key. So
    ``_component_id_exists`` asks ``component_id_is_taken`` -- one bit, no tenant parameter --
    and ``_unique_component_id`` walks past a foreign holder instead.
    """

    def test_the_db_probe_is_the_unscoped_one(self):
        db = _mock_db(row=None)
        db.component_id_is_taken = MagicMock(side_effect=lambda cid: cid == "held-by-globex")
        studio = _studio(db, tenant_id="acme")

        assert studio._component_id_exists("held-by-globex", db) is True
        assert studio._component_id_exists("fresh-id", db) is False
        db.component_id_is_taken.assert_called_with("fresh-id")

    def test_a_foreign_holder_is_stepped_over_not_collided_with(self):
        db = _mock_db(row=None)
        db.component_id_is_taken = MagicMock(side_effect=lambda cid: cid == "math-tutor")
        studio = _studio(db, tenant_id="acme")

        assert studio._unique_component_id("Math Tutor", db) == "math-tutor-2"


class TestWritesCarryTheTenant:
    def test_delete_is_scoped(self):
        db = _mock_db()
        studio = _studio(db, tenant_id="acme")
        studio.delete_agent("agent-1")
        assert db.delete_component.call_args.kwargs["tenant_id"] == "acme"

    def test_draft_persistence_stamps_the_tenant(self):
        db = _mock_db(row=None)  # component does not exist yet
        studio = _studio(db, tenant_id="acme")
        from egpt.agent.agent import Agent

        studio._upsert_draft(Agent(id="new-agent", name="New Agent"))
        assert db.upsert_component.call_args.kwargs["tenant_id"] == "acme"
        assert db.upsert_config.call_args.kwargs["tenant_id"] == "acme"
