"""Tenant scoping of team hydration, including the member recursion (EGPT-2.5).

The boundary that matters here is one level down: scoping only the root team read would
still let a tenant-scoped team pull cross-tenant members, because Team.from_dict issues its
own get_agent_by_id / get_team_by_id round trip per member. These tests pin that the tenant
travels with the recursion, and that the config cache collapses the member N+1.
"""

import copy
from unittest.mock import MagicMock

from egpt.db.base import BaseDb
from egpt.registry import Registry
from egpt.registry.hydrate import ComponentConfigCache
from egpt.team.team import Team, get_team_by_id

TEAM_ROW = {
    "component_id": "team-1",
    "config": {
        "name": "Stored Team",
        "members": [
            {"type": "agent", "agent_id": "member-a"},
            {"type": "agent", "agent_id": "member-b"},
        ],
    },
    "tenant_id": "acme",
    "version": 1,
    "stage": "published",
}

MEMBER_ROWS = {
    "member-a": {
        "component_id": "member-a",
        "config": {"name": "Member A"},
        "tenant_id": "acme",
        "version": 1,
        "stage": "published",
    },
    "member-b": {
        "component_id": "member-b",
        "config": {"name": "Member B"},
        "tenant_id": "acme",
        "version": 1,
        "stage": "published",
    },
}


def _mock_db(rows=None):
    rows = rows if rows is not None else {"team-1": TEAM_ROW, **MEMBER_ROWS}
    abstract = {
        name: MagicMock() for name in dir(BaseDb) if getattr(getattr(BaseDb, name, None), "__isabstractmethod__", False)
    }
    db = type("MockDb", (BaseDb,), abstract)()
    db.id = "test-db"

    def get_config(component_id=None, version=None, label=None, tenant_id=None, **_):
        row = rows.get(component_id)
        if row is None:
            return None
        if tenant_id is not None and tenant_id != row.get("tenant_id"):
            return None
        return copy.deepcopy(row)

    db.get_config = MagicMock(side_effect=get_config)
    return db


class TestMemberRecursionCarriesTheTenant:
    def test_every_member_read_is_scoped_to_the_callers_tenant(self):
        db = _mock_db()
        team = get_team_by_id(db=db, id="team-1", tenant_id="acme")
        assert team is not None
        assert [m.name for m in team.members] == ["Member A", "Member B"]
        # The root and both members were all read as acme.
        tenants = [call.kwargs["tenant_id"] for call in db.get_config.call_args_list]
        assert tenants == ["acme", "acme", "acme"]

    def test_a_cross_tenant_member_is_dropped_like_a_missing_one(self):
        """A tenant-scoped root must not pull a foreign member one level down."""
        rows = {"team-1": TEAM_ROW, **MEMBER_ROWS}
        rows["member-b"] = {**MEMBER_ROWS["member-b"], "tenant_id": "globex"}
        db = _mock_db(rows=rows)

        team = get_team_by_id(db=db, id="team-1", tenant_id="acme")
        assert team is not None
        assert [m.name for m in team.members] == ["Member A"]

    def test_a_cross_tenant_team_does_not_resolve_at_all(self):
        db = _mock_db()
        assert get_team_by_id(db=db, id="team-1", tenant_id="globex") is None


class TestMemberFanOutUsesTheCache:
    def test_one_db_read_per_config_across_repeat_hydrations(self):
        """The member N+1 is the cache's payoff: 3 reads for the first hydration, 0 after."""
        db = _mock_db()
        registry = Registry(component_config_cache=ComponentConfigCache(ttl_seconds=30))

        get_team_by_id(db=db, id="team-1", tenant_id="acme", registry=registry)
        assert db.get_config.call_count == 3

        get_team_by_id(db=db, id="team-1", tenant_id="acme", registry=registry)
        assert db.get_config.call_count == 3


class TestTeamLoad:
    def test_load_scopes_the_graph_read(self):
        db = _mock_db()
        db.load_component_graph = MagicMock(return_value=None)
        Team.load("team-1", db=db, tenant_id="acme")
        db.load_component_graph.assert_called_once_with("team-1", version=None, label=None, tenant_id="acme")
