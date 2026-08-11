"""Append-only lifecycle audit trail for component definitions (EGPT-2.6).

Exercises a real temp-file SQLite database rather than mocks, following the sibling
``test_component_tenant_scoping.py``: the assertions cover the actual DDL, the actual ordering
key and the actual transaction boundaries, none of which a mock can show.

The three properties that carry the ticket, and the tests that pin each:

1. **No mutation goes unaudited, and no refusal is audited.** The audit insert runs on the
   mutation's own transaction, so the two commit or roll back together. ``TestNothingIsAudited
   ThatDidNotHappen`` drives every refusal path and asserts the row count never moves.
2. **The history has a total order.** ``created_at`` has second resolution, so an update and
   the publish that follows it share a timestamp. ``audit_id`` is the ordering key, and
   ``test_a_tight_lifecycle_walk_comes_back_in_order`` is the test that fails if anyone
   reorders on ``created_at`` or swaps the column to ``BigInteger`` (which stops autoincrement
   on SQLite entirely).
3. **The trail outlives what it describes.** No foreign key to ``components``, so
   ``test_the_trail_survives_a_hard_delete`` still finds the rows afterwards. It reads them
   with raw SQL on purpose -- the HTTP route's existence gate 404s for a hard-deleted
   component, so going through it would prove nothing about the rows.
"""

import os
import tempfile
import threading

import pytest
from sqlalchemy import text

from egpt.db.base import ComponentType
from egpt.db.component_audit import COMPONENT_AUDIT_ACTIONS, ComponentAuditAction

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"

AUDIT_TABLE = "egpt_component_audit"

EXPECTED_COLUMNS = {
    "audit_id",
    "component_id",
    "tenant_id",
    "version",
    "action",
    "actor",
    "note",
    "details",
    "created_at",
}

EXPECTED_INDEXES = {
    "idx_egpt_component_audit_tenant_id_component_id",
    "idx_egpt_component_audit_component_id_audit_id",
}


# ============================================================================
# FIXTURES / HELPERS
# ============================================================================


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


def _create_component(db, component_id, tenant_id, *, stage="draft", actor=None, notes=None):
    return db.create_component_with_config(
        component_id=component_id,
        component_type=ComponentType.AGENT,
        name=component_id,
        config={"model": "gpt-5.5"},
        stage=stage,
        tenant_id=tenant_id,
        actor=actor,
        notes=notes,
    )


def _actions(db, component_id, tenant_id=None, **kwargs):
    """The component's history oldest-first, as a plain list of action strings."""
    rows, _ = db.list_component_audit(component_id, tenant_id=tenant_id, limit=100, **kwargs)
    return [row["action"] for row in reversed(rows)]


def _raw_count(db, component_id):
    """Row count straight from the table, bypassing every read path."""
    with db.Session() as sess:
        return sess.execute(
            text(f"SELECT COUNT(*) FROM {AUDIT_TABLE} WHERE component_id = :cid"),
            {"cid": component_id},
        ).scalar()


# ============================================================================
# SCHEMA
# ============================================================================


def test_the_audit_table_has_the_expected_columns(sqlite_db):
    _create_component(sqlite_db, "agent-1", TENANT_A)

    with sqlite_db.Session() as sess:
        columns = {row[1] for row in sess.execute(text(f"PRAGMA table_info({AUDIT_TABLE})")).fetchall()}
    assert columns == EXPECTED_COLUMNS


def test_the_audit_table_has_both_composite_indexes(sqlite_db):
    _create_component(sqlite_db, "agent-1", TENANT_A)

    with sqlite_db.Session() as sess:
        indexes = {row[1] for row in sess.execute(text(f"PRAGMA index_list({AUDIT_TABLE})")).fetchall()}
    assert EXPECTED_INDEXES.issubset(indexes)


def test_audit_id_autoincrements(sqlite_db):
    """The regression guard for the column type.

    SQLite only aliases its implicit rowid -- and so only autoincrements -- for a column
    declared exactly ``INTEGER``. Switching this column to ``BigInteger`` for consistency with
    the rest of the SQLite schema file raises ``IntegrityError: NOT NULL constraint failed`` on
    the very first insert, and this is the test that says so.
    """
    _create_component(sqlite_db, "agent-1", TENANT_A)

    with sqlite_db.Session() as sess:
        ids = [row[0] for row in sess.execute(text(f"SELECT audit_id FROM {AUDIT_TABLE} ORDER BY audit_id")).fetchall()]
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)
    assert all(isinstance(i, int) for i in ids)


def test_the_audit_table_has_no_foreign_key_to_components(sqlite_db):
    """The absence is deliberate: see test_the_trail_survives_a_hard_delete."""
    _create_component(sqlite_db, "agent-1", TENANT_A)

    with sqlite_db.Session() as sess:
        assert sess.execute(text(f"PRAGMA foreign_key_list({AUDIT_TABLE})")).fetchall() == []


# ============================================================================
# ONE ROW PER FACT
# ============================================================================


def test_creating_a_draft_component_records_create_and_config_create(sqlite_db):
    _create_component(sqlite_db, "agent-1", TENANT_A)

    assert _actions(sqlite_db, "agent-1", TENANT_A) == [
        ComponentAuditAction.COMPONENT_CREATE.value,
        ComponentAuditAction.CONFIG_CREATE.value,
    ]


def test_creating_a_published_component_also_records_the_publish(sqlite_db):
    _create_component(sqlite_db, "agent-1", TENANT_A, stage="published")

    assert _actions(sqlite_db, "agent-1", TENANT_A) == [
        ComponentAuditAction.COMPONENT_CREATE.value,
        ComponentAuditAction.CONFIG_CREATE.value,
        ComponentAuditAction.CONFIG_PUBLISH.value,
    ]


def test_editing_a_draft_records_an_update(sqlite_db):
    _create_component(sqlite_db, "agent-1", TENANT_A)
    sqlite_db.upsert_config("agent-1", version=1, config={"model": "other"}, tenant_id=TENANT_A)

    assert _actions(sqlite_db, "agent-1", TENANT_A)[-1] == ComponentAuditAction.CONFIG_UPDATE.value


def test_publishing_a_draft_records_a_publish_and_not_an_update(sqlite_db):
    """One call did one thing.

    Publishing goes through ``upsert_config``, the same method that edits drafts, so the action
    is derived from the stage transition rather than the method name. Recording both would
    invent an intermediate state that never existed.
    """
    _create_component(sqlite_db, "agent-1", TENANT_A)
    sqlite_db.upsert_config("agent-1", version=1, stage="published", tenant_id=TENANT_A)

    trail = _actions(sqlite_db, "agent-1", TENANT_A)
    assert trail[-1] == ComponentAuditAction.CONFIG_PUBLISH.value
    assert trail.count(ComponentAuditAction.CONFIG_UPDATE.value) == 0


def test_a_publish_records_the_version_it_replaced(sqlite_db):
    _create_component(sqlite_db, "agent-1", TENANT_A, stage="published")
    sqlite_db.upsert_config("agent-1", config={"model": "v2"}, tenant_id=TENANT_A)
    sqlite_db.upsert_config("agent-1", version=2, stage="published", tenant_id=TENANT_A)

    rows, _ = sqlite_db.list_component_audit(
        "agent-1", tenant_id=TENANT_A, action=ComponentAuditAction.CONFIG_PUBLISH.value
    )
    assert rows[0]["version"] == 2
    assert rows[0]["details"]["previous_current_version"] == 1
    assert rows[0]["details"]["from_stage"] == "draft"


def test_rollback_records_both_ends_of_the_move(sqlite_db):
    _create_component(sqlite_db, "agent-1", TENANT_A, stage="published")
    sqlite_db.upsert_config("agent-1", config={"model": "v2"}, tenant_id=TENANT_A)
    sqlite_db.upsert_config("agent-1", version=2, stage="published", tenant_id=TENANT_A)

    sqlite_db.set_current_version("agent-1", version=1, tenant_id=TENANT_A, actor="carol", audit_note="regression")

    rows, _ = sqlite_db.list_component_audit(
        "agent-1", tenant_id=TENANT_A, action=ComponentAuditAction.CONFIG_ROLLBACK.value
    )
    assert rows[0]["details"] == {"from_version": 2, "to_version": 1}
    assert rows[0]["actor"] == "carol"
    assert rows[0]["note"] == "regression"


def test_deleting_a_draft_records_a_config_delete(sqlite_db):
    _create_component(sqlite_db, "agent-1", TENANT_A, stage="published")
    sqlite_db.upsert_config("agent-1", config={"model": "v2"}, tenant_id=TENANT_A)
    sqlite_db.delete_config("agent-1", version=2, tenant_id=TENANT_A)

    assert _actions(sqlite_db, "agent-1", TENANT_A)[-1] == ComponentAuditAction.CONFIG_DELETE.value


def test_component_level_mutations_are_recorded_without_a_version(sqlite_db):
    _create_component(sqlite_db, "agent-1", TENANT_A)
    sqlite_db.upsert_component("agent-1", name="Renamed", tenant_id=TENANT_A)
    sqlite_db.delete_component("agent-1", tenant_id=TENANT_A)

    rows, _ = sqlite_db.list_component_audit("agent-1", tenant_id=TENANT_A)
    component_rows = [r for r in rows if r["action"].startswith("component.")]
    assert [r["action"] for r in reversed(component_rows)] == [
        ComponentAuditAction.COMPONENT_CREATE.value,
        ComponentAuditAction.COMPONENT_UPDATE.value,
        ComponentAuditAction.COMPONENT_DELETE.value,
    ]
    assert all(r["version"] is None for r in component_rows)


def test_reactivating_a_soft_deleted_component_is_its_own_action(sqlite_db):
    _create_component(sqlite_db, "agent-1", TENANT_A)
    sqlite_db.delete_component("agent-1", tenant_id=TENANT_A)
    sqlite_db.upsert_component("agent-1", component_type=ComponentType.AGENT, tenant_id=TENANT_A)

    assert _actions(sqlite_db, "agent-1", TENANT_A)[-1] == ComponentAuditAction.COMPONENT_REACTIVATE.value


def test_a_pointer_move_via_patch_is_an_update_not_a_rollback(sqlite_db):
    """``rollback`` stays reserved for ``set_current_version``.

    ``upsert_component(current_version=N)`` moves the same pointer but never checks that the
    target is published, so labelling it a rollback would have the trail claim a validation
    that did not run. The two versions go in ``details`` instead.
    """
    _create_component(sqlite_db, "agent-1", TENANT_A, stage="published")
    sqlite_db.upsert_config("agent-1", config={"model": "v2"}, tenant_id=TENANT_A)
    sqlite_db.upsert_component("agent-1", current_version=2, tenant_id=TENANT_A)

    rows, _ = sqlite_db.list_component_audit("agent-1", tenant_id=TENANT_A)
    assert rows[0]["action"] == ComponentAuditAction.COMPONENT_UPDATE.value
    assert rows[0]["details"]["current_version"] == 2
    assert rows[0]["details"]["previous_current_version"] == 1


# ============================================================================
# ORDERING
# ============================================================================


def test_a_tight_lifecycle_walk_comes_back_in_order(sqlite_db):
    """The reason ``audit_id`` exists rather than ordering on ``created_at``.

    Every one of these calls lands in the same second, so a ``created_at`` sort would return
    them in an arbitrary order and this assertion would be flaky rather than wrong.
    """
    _create_component(sqlite_db, "agent-1", TENANT_A)
    sqlite_db.upsert_config("agent-1", version=1, config={"model": "b"}, tenant_id=TENANT_A)
    sqlite_db.upsert_config("agent-1", version=1, stage="published", tenant_id=TENANT_A)
    sqlite_db.upsert_config("agent-1", config={"model": "c"}, tenant_id=TENANT_A)
    sqlite_db.upsert_config("agent-1", version=2, stage="published", tenant_id=TENANT_A)
    sqlite_db.set_current_version("agent-1", version=1, tenant_id=TENANT_A)

    assert _actions(sqlite_db, "agent-1", TENANT_A) == [
        ComponentAuditAction.COMPONENT_CREATE.value,
        ComponentAuditAction.CONFIG_CREATE.value,
        ComponentAuditAction.CONFIG_UPDATE.value,
        ComponentAuditAction.CONFIG_PUBLISH.value,
        ComponentAuditAction.CONFIG_CREATE.value,
        ComponentAuditAction.CONFIG_PUBLISH.value,
        ComponentAuditAction.CONFIG_ROLLBACK.value,
    ]


def test_the_listing_is_newest_first(sqlite_db):
    _create_component(sqlite_db, "agent-1", TENANT_A)

    rows, _ = sqlite_db.list_component_audit("agent-1", tenant_id=TENANT_A)
    assert rows[0]["action"] == ComponentAuditAction.CONFIG_CREATE.value
    assert rows[0]["audit_id"] > rows[-1]["audit_id"]


# ============================================================================
# NOTHING IS AUDITED THAT DID NOT HAPPEN
# ============================================================================


class TestNothingIsAuditedThatDidNotHappen:
    """A refused mutation must leave the trail exactly as it found it.

    This is the other half of "no mutation goes unaudited": both hold because the audit insert
    shares the mutation's transaction. If anyone moves the write outside the ``with`` block, or
    wraps it in a try/except, these are the tests that catch it.
    """

    def test_a_refused_published_update_writes_nothing(self, sqlite_db):
        _create_component(sqlite_db, "agent-1", TENANT_A, stage="published")
        before = _raw_count(sqlite_db, "agent-1")

        with pytest.raises(ValueError, match="Cannot update published config"):
            sqlite_db.upsert_config("agent-1", version=1, config={"model": "x"}, tenant_id=TENANT_A)

        assert _raw_count(sqlite_db, "agent-1") == before

    def test_a_refused_current_delete_writes_nothing(self, sqlite_db):
        _create_component(sqlite_db, "agent-1", TENANT_A, stage="published")
        before = _raw_count(sqlite_db, "agent-1")

        with pytest.raises(ValueError, match="Cannot delete current config"):
            sqlite_db.delete_config("agent-1", version=1, tenant_id=TENANT_A)

        assert _raw_count(sqlite_db, "agent-1") == before

    def test_a_refused_published_delete_writes_nothing(self, sqlite_db):
        _create_component(sqlite_db, "agent-1", TENANT_A, stage="published")
        sqlite_db.upsert_config("agent-1", config={"model": "v2"}, tenant_id=TENANT_A)
        sqlite_db.upsert_config("agent-1", version=2, stage="published", tenant_id=TENANT_A)
        sqlite_db.set_current_version("agent-1", version=1, tenant_id=TENANT_A)
        before = _raw_count(sqlite_db, "agent-1")

        with pytest.raises(ValueError, match="Cannot delete published config"):
            sqlite_db.delete_config("agent-1", version=2, tenant_id=TENANT_A)

        assert _raw_count(sqlite_db, "agent-1") == before

    def test_a_cross_tenant_delete_writes_nothing(self, sqlite_db):
        """The SQLite ``delete_component`` guard specifically.

        That backend has no tenant-scoped existence precheck -- it reports success from the
        statement's own rowcount -- so an unguarded audit write would record a delete that
        matched no rows.
        """
        _create_component(sqlite_db, "agent-1", TENANT_A)
        before = _raw_count(sqlite_db, "agent-1")

        assert sqlite_db.delete_component("agent-1", tenant_id=TENANT_B) is False

        assert _raw_count(sqlite_db, "agent-1") == before

    def test_a_cross_tenant_rollback_writes_nothing(self, sqlite_db):
        _create_component(sqlite_db, "agent-1", TENANT_A, stage="published")
        before = _raw_count(sqlite_db, "agent-1")

        assert sqlite_db.set_current_version("agent-1", version=1, tenant_id=TENANT_B) is False

        assert _raw_count(sqlite_db, "agent-1") == before

    def test_a_refused_draft_rollback_writes_nothing(self, sqlite_db):
        _create_component(sqlite_db, "agent-1", TENANT_A)
        before = _raw_count(sqlite_db, "agent-1")

        with pytest.raises(ValueError, match="Cannot set draft config"):
            sqlite_db.set_current_version("agent-1", version=1, tenant_id=TENANT_A)

        assert _raw_count(sqlite_db, "agent-1") == before


# ============================================================================
# SURVIVAL
# ============================================================================


def test_the_trail_survives_a_hard_delete(sqlite_db):
    """The whole reason the table carries no foreign key to ``components``.

    Read with raw SQL rather than through ``list_component_audit``: the point is that the rows
    are still there, and any read path that filters on a component row would prove the opposite
    of what it looks like.
    """
    _create_component(sqlite_db, "agent-1", TENANT_A)
    before = _raw_count(sqlite_db, "agent-1")
    assert before > 0

    assert sqlite_db.delete_component("agent-1", hard_delete=True, tenant_id=TENANT_A) is True

    with sqlite_db.Session() as sess:
        assert sess.execute(text("SELECT COUNT(*) FROM egpt_components")).scalar() == 0
    assert _raw_count(sqlite_db, "agent-1") == before + 1  # the delete itself is recorded too


# ============================================================================
# TENANCY AND ATTRIBUTION
# ============================================================================


def test_the_history_is_tenant_scoped(sqlite_db):
    _create_component(sqlite_db, "agent-1", TENANT_A)

    assert sqlite_db.list_component_audit("agent-1", tenant_id=TENANT_B) == ([], 0)
    assert sqlite_db.list_component_audit("agent-1", tenant_id=TENANT_A)[1] > 0


def test_an_unscoped_read_sees_every_tenant(sqlite_db):
    """``tenant_id=None`` means no predicate, matching the rest of the component API."""
    _create_component(sqlite_db, "agent-1", TENANT_A)

    assert sqlite_db.list_component_audit("agent-1")[1] > 0


def test_the_actor_is_stored_verbatim(sqlite_db):
    _create_component(sqlite_db, "agent-1", TENANT_A, actor="sa:ci-bot")

    rows, _ = sqlite_db.list_component_audit("agent-1", tenant_id=TENANT_A)
    assert {r["actor"] for r in rows} == {"sa:ci-bot"}


def test_a_programmatic_write_records_a_null_actor(sqlite_db):
    """Not a gap in the trail: the change is recorded, it simply had no principal."""
    _create_component(sqlite_db, "agent-1", TENANT_A)

    rows, _ = sqlite_db.list_component_audit("agent-1", tenant_id=TENANT_A)
    assert all(r["actor"] is None for r in rows)


def test_the_note_falls_back_to_the_config_notes(sqlite_db):
    _create_component(sqlite_db, "agent-1", TENANT_A, notes="initial import")

    rows, _ = sqlite_db.list_component_audit("agent-1", tenant_id=TENANT_A)
    assert {r["note"] for r in rows} == {"initial import"}


def test_an_explicit_audit_note_wins_over_notes(sqlite_db):
    _create_component(sqlite_db, "agent-1", TENANT_A)
    sqlite_db.upsert_config(
        "agent-1", version=1, config={"model": "x"}, notes="config notes", audit_note="why", tenant_id=TENANT_A
    )

    rows, _ = sqlite_db.list_component_audit("agent-1", tenant_id=TENANT_A)
    assert rows[0]["note"] == "why"


# ============================================================================
# READ FILTERS AND PAGINATION
# ============================================================================


def test_the_history_can_be_filtered_by_action(sqlite_db):
    _create_component(sqlite_db, "agent-1", TENANT_A, stage="published")

    rows, total = sqlite_db.list_component_audit(
        "agent-1", tenant_id=TENANT_A, action=ComponentAuditAction.CONFIG_PUBLISH.value
    )
    assert total == 1
    assert rows[0]["action"] == ComponentAuditAction.CONFIG_PUBLISH.value


def test_the_history_can_be_filtered_by_version(sqlite_db):
    _create_component(sqlite_db, "agent-1", TENANT_A)
    sqlite_db.upsert_config("agent-1", config={"model": "v2"}, tenant_id=TENANT_A)

    rows, total = sqlite_db.list_component_audit("agent-1", tenant_id=TENANT_A, version=2)
    assert total == 1
    assert rows[0]["version"] == 2


def test_pagination_reports_the_unpaged_total(sqlite_db):
    _create_component(sqlite_db, "agent-1", TENANT_A, stage="published")

    rows, total = sqlite_db.list_component_audit("agent-1", tenant_id=TENANT_A, limit=1)
    assert len(rows) == 1
    assert total == 3


def test_a_component_with_no_history_reads_empty(sqlite_db):
    _create_component(sqlite_db, "agent-1", TENANT_A)

    assert sqlite_db.list_component_audit("nonexistent", tenant_id=TENANT_A) == ([], 0)


def test_the_action_vocabulary_matches_the_enum(sqlite_db):  # noqa: ARG001
    """The router validates a caller's filter against this set, so it must not drift."""
    assert COMPONENT_AUDIT_ACTIONS == {a.value for a in ComponentAuditAction}


# ============================================================================
# TENANT ATTRIBUTION FOLLOWS THE COMPONENT, NOT THE CALLER
# ============================================================================


class TestTenantAttributionFollowsTheComponent:
    """An unattributed write (``tenant_id=None``, e.g. ``Agent.save()``) to a tenanted
    component must record that component's real tenant, not ``NULL``.

    Before this fix, the audit row was stamped with the caller's filter argument rather than
    the tenant already stored on the row being mutated. A ``NULL``-tenant row is invisible to
    the owning tenant's own scoped reads -- and ``total_count`` agrees with the truncated page,
    so nothing signals the gap (EGPT-2.6 review). Each test here reads back scoped to the real
    tenant: under the bug, that read would have missed the row just written.
    """

    def test_reactivating_with_no_tenant_filter_keeps_the_components_tenant(self, sqlite_db):
        _create_component(sqlite_db, "agent-1", TENANT_A)
        sqlite_db.delete_component("agent-1", tenant_id=TENANT_A)

        sqlite_db.upsert_component("agent-1", component_type=ComponentType.AGENT)

        rows, _ = sqlite_db.list_component_audit("agent-1", tenant_id=TENANT_A)
        assert rows[0]["action"] == ComponentAuditAction.COMPONENT_REACTIVATE.value

    def test_updating_with_no_tenant_filter_keeps_the_components_tenant(self, sqlite_db):
        _create_component(sqlite_db, "agent-1", TENANT_A)

        sqlite_db.upsert_component("agent-1", name="Renamed")

        rows, _ = sqlite_db.list_component_audit("agent-1", tenant_id=TENANT_A)
        assert rows[0]["action"] == ComponentAuditAction.COMPONENT_UPDATE.value

    def test_a_new_config_version_with_no_tenant_filter_keeps_the_components_tenant(self, sqlite_db):
        _create_component(sqlite_db, "agent-1", TENANT_A)

        sqlite_db.upsert_config("agent-1", config={"model": "v2"})

        rows, _ = sqlite_db.list_component_audit("agent-1", tenant_id=TENANT_A)
        assert rows[0]["action"] == ComponentAuditAction.CONFIG_CREATE.value
        assert rows[0]["version"] == 2

    def test_deleting_a_config_with_no_tenant_filter_keeps_the_components_tenant(self, sqlite_db):
        _create_component(sqlite_db, "agent-1", TENANT_A, stage="published")
        sqlite_db.upsert_config("agent-1", config={"model": "v2"}, tenant_id=TENANT_A)

        sqlite_db.delete_config("agent-1", version=2)

        rows, _ = sqlite_db.list_component_audit("agent-1", tenant_id=TENANT_A)
        assert rows[0]["action"] == ComponentAuditAction.CONFIG_DELETE.value

    def test_soft_deleting_with_no_tenant_filter_keeps_the_components_tenant(self, sqlite_db):
        _create_component(sqlite_db, "agent-1", TENANT_A)

        assert sqlite_db.delete_component("agent-1") is True

        rows, _ = sqlite_db.list_component_audit("agent-1", tenant_id=TENANT_A)
        assert rows[0]["action"] == ComponentAuditAction.COMPONENT_DELETE.value

    def test_rolling_back_with_no_tenant_filter_keeps_the_components_tenant(self, sqlite_db):
        _create_component(sqlite_db, "agent-1", TENANT_A, stage="published")
        sqlite_db.upsert_config("agent-1", config={"model": "v2"}, tenant_id=TENANT_A)
        sqlite_db.upsert_config("agent-1", version=2, stage="published", tenant_id=TENANT_A)

        assert sqlite_db.set_current_version("agent-1", version=1) is True

        rows, _ = sqlite_db.list_component_audit("agent-1", tenant_id=TENANT_A)
        assert rows[0]["action"] == ComponentAuditAction.CONFIG_ROLLBACK.value


# ============================================================================
# A REPEATED SOFT DELETE IS A NO-OP, NOT A SECOND DELETE
# ============================================================================


class TestRepeatedSoftDeleteIsANoOp:
    """Without a ``deleted_at IS NULL`` guard on the soft-delete UPDATE, a repeat call matched
    the already-deleted row again: it returned ``True`` a second time, overwrote the original
    ``deleted_at`` (state loss -- *when* the component was deleted becomes unrecoverable), and
    wrote a second, phantom ``component.delete`` audit row for a delete that never happened
    (EGPT-2.6 review).
    """

    def test_the_second_call_reports_no_match(self, sqlite_db):
        _create_component(sqlite_db, "agent-1", TENANT_A)

        assert sqlite_db.delete_component("agent-1", tenant_id=TENANT_A) is True
        assert sqlite_db.delete_component("agent-1", tenant_id=TENANT_A) is False

    def test_the_second_call_writes_no_audit_row(self, sqlite_db):
        _create_component(sqlite_db, "agent-1", TENANT_A)
        sqlite_db.delete_component("agent-1", tenant_id=TENANT_A)
        before = _raw_count(sqlite_db, "agent-1")

        sqlite_db.delete_component("agent-1", tenant_id=TENANT_A)

        assert _raw_count(sqlite_db, "agent-1") == before

    def test_the_second_call_does_not_move_deleted_at(self, sqlite_db):
        _create_component(sqlite_db, "agent-1", TENANT_A)

        sqlite_db.delete_component("agent-1", tenant_id=TENANT_A)
        with sqlite_db.Session() as sess:
            first_deleted_at = sess.execute(
                text("SELECT deleted_at FROM egpt_components WHERE component_id = :cid"), {"cid": "agent-1"}
            ).scalar()

        sqlite_db.delete_component("agent-1", tenant_id=TENANT_A)
        with sqlite_db.Session() as sess:
            second_deleted_at = sess.execute(
                text("SELECT deleted_at FROM egpt_components WHERE component_id = :cid"), {"cid": "agent-1"}
            ).scalar()

        assert second_deleted_at == first_deleted_at


# ============================================================================
# CONCURRENT ROLLBACK DOES NOT FABRICATE TRANSITIONS
# ============================================================================


def test_concurrent_rollbacks_never_record_a_transition_that_did_not_happen(sqlite_db):
    """The critical finding from the EGPT-2.6 review: an unlocked read of ``current_version``,
    used later to fill in ``details.from_version``, let a concurrent writer's commit land in
    between -- producing entries like ``(3, 3)``, a self-rollback that cannot really happen, and
    a chain that did not reconnect. Reproduced with 8 threads against a real (temp-file) SQLite
    database, not a mock, since the race is between real connections.

    The fix is a compare-and-swap on the pointer update: a losing attempt re-reads and retries
    rather than writing the stale value. This test asserts the resulting chain of
    ``(from_version, to_version)`` pairs reconnects with no gaps -- the property that was false
    before the fix.
    """
    _create_component(sqlite_db, "agent-1", TENANT_A, stage="published")
    for v in range(2, 6):
        sqlite_db.upsert_config("agent-1", config={"model": f"v{v}"}, stage="published", tenant_id=TENANT_A)
    # Every one of the four publishes above also moved current_version, so it is 5, not 1,
    # by the time the threads below start.

    errors = []

    def worker(target_version):
        try:
            sqlite_db.set_current_version("agent-1", version=target_version, tenant_id=TENANT_A)
        except Exception as e:  # pragma: no cover - asserted via `errors`, not raised in-thread
            errors.append(e)

    threads = [threading.Thread(target=worker, args=((i % 5) + 1,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []

    rows, _ = sqlite_db.list_component_audit(
        "agent-1", tenant_id=TENANT_A, action=ComponentAuditAction.CONFIG_ROLLBACK.value, limit=100
    )
    rows = list(reversed(rows))  # oldest first
    assert len(rows) == 8
    assert rows[0]["details"]["from_version"] == 5

    for earlier, later in zip(rows, rows[1:]):
        assert earlier["details"]["to_version"] == later["details"]["from_version"], (
            f"broken link: {earlier['details']} -> {later['details']}"
        )

    final = sqlite_db.get_component("agent-1", tenant_id=TENANT_A)
    assert final["current_version"] == rows[-1]["details"]["to_version"]
