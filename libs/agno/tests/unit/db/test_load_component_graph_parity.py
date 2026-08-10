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


def _create(db, component_id, *, stage="draft", links=None, label=None, config=None):
    return db.create_component_with_config(
        component_id=component_id,
        component_type=ComponentType.AGENT,
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
        sqlite_db.upsert_config(
            "agent-1", config={"model": "v2-model"}, stage="published", tenant_id=TENANT_A
        )

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
