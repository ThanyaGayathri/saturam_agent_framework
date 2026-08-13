"""Tenant scoping of team hydration, including the member recursion (EGPT-2.5).

The boundary that matters here is one level down: scoping only the root team read would
still let a tenant-scoped team pull cross-tenant members, because Team.from_dict issues its
own get_agent_by_id / get_team_by_id round trip per member. These tests pin that the tenant
travels with the recursion, that the config cache collapses the member N+1, and that a
member which cannot be resolved refuses the whole team instead of silently running it
half-built (PR #57 review).
"""

import copy
from unittest.mock import MagicMock

import pytest

from egpt.db.base import BaseDb
from egpt.registry import Registry
from egpt.registry.hydrate import ComponentConfigCache, HydrationError
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

    def get_config(component_id=None, version=None, label=None, *, tenant_id, **_):
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

    def test_a_cross_tenant_member_refuses_the_whole_team(self):
        """A tenant-scoped root must not pull a foreign member one level down -- and must not
        silently run without it either: a partial team changes execution semantics with no
        error, so the hydration is refused (PR #57 review)."""
        rows = {"team-1": TEAM_ROW, **MEMBER_ROWS}
        rows["member-b"] = {**MEMBER_ROWS["member-b"], "tenant_id": "globex"}
        db = _mock_db(rows=rows)

        with pytest.raises(HydrationError, match="member-b"):
            get_team_by_id(db=db, id="team-1", tenant_id="acme")

    def test_a_missing_member_refuses_the_whole_team(self):
        rows = {"team-1": TEAM_ROW, "member-a": MEMBER_ROWS["member-a"]}
        db = _mock_db(rows=rows)

        with pytest.raises(HydrationError, match="member-b"):
            get_team_by_id(db=db, id="team-1", tenant_id="acme")

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

    def test_load_hydrates_members_from_the_graph_not_from_dict(self):
        """PR #57 review: members must come from graph children, not from from_dict's own
        per-member DB round trips -- on an A -> B -> A cycle those never consult the graph's
        cycle stub and recurse forever. The mock db has no get_config wired for the members,
        so any refetch fails the test."""
        stub_a = {
            "component": {"component_id": "team-a"},
            "config": {
                "config": {"name": "Team A", "members": [{"type": "team", "team_id": "team-b"}]},
                "tenant_id": "acme",
                "stage": "published",
            },
            "children": [],
            "cycle_detected": True,
        }
        graph_b = {
            "component": {"component_id": "team-b"},
            "config": {
                "config": {"name": "Team B", "members": [{"type": "team", "team_id": "team-a"}]},
                "tenant_id": "acme",
                "stage": "published",
            },
            "children": [{"link": {"child_component_id": "team-a", "meta": {"type": "team"}}, "graph": stub_a}],
        }
        graph_a = {
            "component": {"component_id": "team-a"},
            "config": {
                "config": {"name": "Team A", "members": [{"type": "team", "team_id": "team-b"}]},
                "tenant_id": "acme",
                "stage": "published",
            },
            "children": [{"link": {"child_component_id": "team-b", "meta": {"type": "team"}}, "graph": graph_b}],
        }
        db = _mock_db(rows={})
        db.load_component_graph = MagicMock(return_value=graph_a)

        team = Team.load("team-a", db=db, tenant_id="acme")

        assert team is not None and team.id == "team-a"
        assert [m.id for m in team.members] == ["team-b"]
        assert [m.id for m in team.members[0].members] == ["team-a"]
        # The cycle stub terminates the recursion as a member-less shell.
        assert team.members[0].members[0].members in (None, [])
        # One graph read, zero per-member config reads.
        db.get_config.assert_not_called()

    def test_load_refuses_an_unresolved_graph_child(self):
        """A stored member link whose child the graph could not resolve (deleted, foreign
        tenant, unresolvable version) must not degrade into a runnable partial team."""
        graph = {
            "component": {"component_id": "team-1"},
            "config": {
                "config": {"name": "Team", "members": [{"type": "agent", "agent_id": "ghost"}]},
                "tenant_id": "acme",
                "stage": "published",
            },
            "children": [
                {
                    "link": {"child_component_id": "ghost", "meta": {"type": "agent"}},
                    "graph": None,
                    "error": "child_version_unresolvable",
                }
            ],
        }
        db = _mock_db(rows={})
        db.load_component_graph = MagicMock(return_value=graph)

        with pytest.raises(HydrationError) as excinfo:
            Team.load("team-1", db=db, tenant_id="acme")
        assert "team-1" in str(excinfo.value)
        assert "ghost" in str(excinfo.value)


class TestGraphMemberHydrationCarriesTheRegistryAndTheEndpointPolicy:
    """`Team.load` -> `_hydrate_from_graph` -> `Agent.from_dict` for each member.

    Untested before the round-2 review, which is how the missing `registry=` argument reached
    main in the first place: the member branch rebuilt every model from its serialized identity
    triple, discarding whatever the registry held. A later refactor dropping it again would have
    been just as silent.
    """

    def _member_graph(self, model_config):
        return {
            "component": {"component_id": "team-a"},
            "config": {
                "config": {"name": "Team A", "members": [{"type": "agent", "agent_id": "agent-1"}]},
                "tenant_id": "acme",
                "stage": "published",
            },
            "children": [
                {
                    "link": {"child_component_id": "agent-1", "meta": {"type": "agent"}},
                    "graph": {
                        "component": {"component_id": "agent-1"},
                        "config": {
                            "config": {"id": "agent-1", "name": "Member", "model": model_config},
                            "tenant_id": "acme",
                            "stage": "published",
                        },
                        "children": [],
                    },
                }
            ],
        }

    def test_a_member_on_an_env_resolved_proxy_loads(self, monkeypatch):
        """The regression the round-2 review caught: one member on `AzureOpenAI` must not make the
        whole team unloadable. `_hydrate_from_graph` converts any member failure into a team-wide
        `HydrationError`, so an over-broad model guard took out every stored team with an Azure or
        vLLM member -- deployments with no public egress at all."""
        from egpt.integrations.models.azure.openai_chat import AzureOpenAI

        monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://priv.openai.azure.com")
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "k")
        db = _mock_db(rows={})
        db.load_component_graph = MagicMock(
            return_value=self._member_graph({"id": "gpt-4.1-mini", "name": "AzureOpenAI", "provider": "Azure"})
        )

        team = Team.load("team-a", db=db, tenant_id="acme")

        assert team is not None
        assert [m.id for m in team.members] == ["agent-1"]
        assert isinstance(team.members[0].model, AzureOpenAI)

    def test_require_endpoint_reaches_every_member(self):
        """`require_endpoint` is threaded to members, not just to the team's own model.

        Without the forwarding a caller who opted out still could not load a team whose *member*
        carried a bare proxy -- and the member is where an endpointless model is most likely to
        be, since members are the things hydrated from stored graphs.
        """
        db = _mock_db(rows={})
        db.load_component_graph = MagicMock(return_value=self._member_graph("openai-like:qwen"))

        # The member's refusal propagates as a raw `ValueError` rather than being wrapped -- worth
        # asserting the type that actually escapes, since a caller catching only `HydrationError`
        # (which the surrounding graph errors use) would not catch this one.
        with pytest.raises(ValueError, match="cannot be restored"):
            Team.load("team-a", db=db, tenant_id="acme")

        db.load_component_graph = MagicMock(return_value=self._member_graph("openai-like:qwen"))
        team = Team.load("team-a", db=db, tenant_id="acme", require_endpoint=False)

        assert team is not None and [m.id for m in team.members] == ["agent-1"]

    def test_require_endpoint_reaches_members_on_the_from_dict_path_too(self):
        """The `from_dict` member path, which had no coverage and diverged from `Team.load`.

        Round-2 review caught this: `Team.load` threads the flag through `_hydrate_from_graph`,
        but `Team.from_dict` hydrates members through `get_agent_by_id` / `get_team_by_id`, and
        neither accepted the parameter -- so the opt-out was inert on exactly the path
        `get_team_by_id` (the AgentOS read path) takes. Both flag values failed identically.

        Two entry points reaching the same members by different routes need the same test, or the
        one without coverage is free to drift.
        """
        cfg = {"id": "t1", "name": "T", "members": [{"type": "agent", "agent_id": "m1"}]}

        def _db_with_a_bare_proxy_member():
            db = MagicMock()
            db.get_component = MagicMock(return_value={"component_id": "m1", "component_type": "agent"})
            db.get_config = MagicMock(
                return_value={
                    "config": {"id": "m1", "name": "M", "model": "openai-like:qwen"},
                    "tenant_id": None,
                    "stage": "published",
                }
            )
            return db

        with pytest.raises(HydrationError):
            Team.from_dict(cfg, db=_db_with_a_bare_proxy_member())

        team = Team.from_dict(cfg, db=_db_with_a_bare_proxy_member(), require_endpoint=False)

        assert team is not None and [m.id for m in team.members] == ["m1"]

    def test_get_team_by_id_forwards_the_opt_out(self):
        """The entry point finding [3] was actually about.

        `get_team_by_id` routes through `Team.from_dict`, not `Team.load`, so the remedy the
        refusal message names has to be reachable from here or it is not reachable from AgentOS.
        """
        from egpt.team.team import get_team_by_id

        db = MagicMock()
        db.get_component = MagicMock(return_value={"component_id": "m1", "component_type": "agent"})
        db.get_config = MagicMock(
            return_value={
                "config": {"id": "m1", "name": "M", "model": "openai-like:qwen"},
                "tenant_id": None,
                "stage": "published",
            }
        )

        import inspect

        assert "require_endpoint" in inspect.signature(get_team_by_id).parameters, (
            "the AgentOS read path must expose the opt-out its own error message recommends"
        )
