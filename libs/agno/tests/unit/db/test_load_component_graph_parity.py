"""SQLite/Postgres parity for load_component_graph (EGPT-2.5).

Before this fix the SQLite implementation had no `label` parameter -- `Team.load(label=...)`
raised TypeError on SQLite (tools/studio.py documented a workaround) -- and no cycle or depth
guard, so a self-referencing component graph recursed forever. Postgres accepted `label` but
silently ignored it, resolving the current version instead of the labeled one.

Exercises a real SQLite database rather than mocks, matching test_component_tenant_scoping.py.
"""

import os
import tempfile

import pytest

from egpt.db.base import ComponentType

TENANT_A = "tenant-a"


@pytest.fixture
def sqlite_db():
    from egpt.integrations.db.sqlite.sqlite import SqliteDb

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = SqliteDb(db_file=path)
    yield db
    try:
        os.unlink(path)
    except OSError:
        pass


def _link(child_component_id, child_version=1, position=0):
    return {
        "link_kind": "member",
        "link_key": child_component_id,
        "child_component_id": child_component_id,
        "child_version": child_version,
        "position": position,
    }


def _create(db, component_id, *, stage="draft", links=None, label=None, config=None, component_type=None):
    return db.create_component_with_config(
        component_id=component_id,
        component_type=component_type or ComponentType.AGENT,
        name=component_id,
        config=config or {"model": "gpt-5.5"},
        stage=stage,
        links=links,
        label=label,
        tenant_id=TENANT_A,
    )


class TestLabelResolution:
    def test_label_kwarg_is_accepted(self, sqlite_db):
        """The signature gap: label used to raise TypeError on SQLite."""
        _create(sqlite_db, "agent-1", stage="published")

        graph = sqlite_db.load_component_graph("agent-1", label=None, tenant_id=TENANT_A)
        assert graph is not None

    def test_label_resolves_the_labeled_version_not_current(self, sqlite_db):
        _create(sqlite_db, "agent-1", label="stable", config={"model": "v1-model"})
        sqlite_db.upsert_config("agent-1", config={"model": "v2-model"}, stage="published", tenant_id=TENANT_A)

        labeled = sqlite_db.load_component_graph("agent-1", label="stable", tenant_id=TENANT_A)
        assert labeled is not None
        assert labeled["resolved_versions"]["agent-1"] == 1

        current = sqlite_db.load_component_graph("agent-1", tenant_id=TENANT_A)
        assert current is not None
        assert current["resolved_versions"]["agent-1"] == 2

    def test_unknown_label_returns_none(self, sqlite_db):
        _create(sqlite_db, "agent-1", stage="published")

        assert sqlite_db.load_component_graph("agent-1", label="no-such-label", tenant_id=TENANT_A) is None

    def test_explicit_version_wins_over_label(self, sqlite_db):
        _create(sqlite_db, "agent-1", label="stable")
        sqlite_db.upsert_config("agent-1", config={"model": "v2-model"}, stage="published", tenant_id=TENANT_A)

        graph = sqlite_db.load_component_graph("agent-1", version=2, label="stable", tenant_id=TENANT_A)
        assert graph is not None
        assert graph["resolved_versions"]["agent-1"] == 2


class TestCycleHandling:
    def test_two_level_graph_resolves_children(self, sqlite_db):
        _create(sqlite_db, "leaf", stage="published")
        _create(sqlite_db, "mid", stage="published", links=[_link("leaf")])
        _create(sqlite_db, "root", stage="published", links=[_link("mid")])

        graph = sqlite_db.load_component_graph("root", tenant_id=TENANT_A)
        assert graph is not None
        mid_graph = graph["children"][0]["graph"]
        assert mid_graph["component"]["component_id"] == "mid"
        assert mid_graph["children"][0]["graph"]["component"]["component_id"] == "leaf"
        assert graph["resolved_versions"] == {"root": 1, "mid": 1, "leaf": 1}

    def test_cycle_returns_stub_instead_of_recursing_forever(self, sqlite_db):
        _create(sqlite_db, "a")
        _create(sqlite_db, "b", stage="published", links=[_link("a")])
        # Close the loop: a (still a draft, so editable) gains a link back to b v1, then publish.
        sqlite_db.upsert_config("a", version=1, links=[_link("b")], stage="published", tenant_id=TENANT_A)

        graph = sqlite_db.load_component_graph("a", tenant_id=TENANT_A)
        assert graph is not None

        b_graph = graph["children"][0]["graph"]
        assert b_graph["component"]["component_id"] == "b"
        a_again = b_graph["children"][0]["graph"]
        assert a_again["cycle_detected"] is True
        assert a_again["children"] == []

    def test_a_diamond_is_not_a_cycle(self, sqlite_db):
        """PR #57 review: _visited must track the active recursion path, not every node ever
        seen -- a shared child reached through two siblings is a valid graph, and the second
        branch used to come back as a cycle stub."""
        _create(sqlite_db, "leaf", stage="published")
        _create(sqlite_db, "left", stage="published", links=[_link("leaf")])
        _create(sqlite_db, "right", stage="published", links=[_link("leaf")])
        _create(sqlite_db, "root", stage="published", links=[_link("left", position=0), _link("right", position=1)])

        graph = sqlite_db.load_component_graph("root", tenant_id=TENANT_A)
        assert graph is not None

        for branch in graph["children"]:
            leaf_graph = branch["graph"]["children"][0]["graph"]
            assert leaf_graph["component"]["component_id"] == "leaf"
            assert leaf_graph.get("cycle_detected") is not True
            assert leaf_graph["config"] is not None


class TestTeamLoadCycle:
    def test_team_load_survives_a_team_cycle(self, sqlite_db):
        """PR #57 review: Team.load hydrates members from the loaded graph. Before that fix it
        rebuilt them through from_dict's per-member get_team_by_id recursion, which never
        consults the graph's cycle stub -- an A -> B -> A team cycle recursed until failure."""
        from egpt.team.team import Team

        def _member_link(child_id):
            return {**_link(child_id), "meta": {"type": "team"}}

        _create(
            sqlite_db,
            "team-a",
            component_type=ComponentType.TEAM,
            config={"name": "Team A", "members": [{"type": "team", "team_id": "team-b"}]},
        )
        _create(
            sqlite_db,
            "team-b",
            component_type=ComponentType.TEAM,
            stage="published",
            links=[_member_link("team-a")],
            config={"name": "Team B", "members": [{"type": "team", "team_id": "team-a"}]},
        )
        # Close the loop and publish team-a.
        sqlite_db.upsert_config(
            "team-a", version=1, links=[_member_link("team-b")], stage="published", tenant_id=TENANT_A
        )

        team = Team.load("team-a", db=sqlite_db, tenant_id=TENANT_A)

        assert team is not None and team.id == "team-a"
        assert [m.id for m in team.members] == ["team-b"]
        nested = team.members[0]
        assert [m.id for m in nested.members] == ["team-a"]
        # The cycle stub terminates the recursion as a member-less shell.
        assert nested.members[0].members in (None, [])
