"""A lost write race must not be misreported as a soft-deleted holder (PR #138 QC review).

``_plan_entry`` probes for an id collision before the write. Between that probe and
``_apply_plan``'s call into ``create_component_with_config``, a competing writer can create a
*live* row under the same id -- the pre-pass is not inside the write's transaction, so it cannot
close this window. Before this fix, the db raised the same ``ValueError`` message for a live
race-loser as it did for a genuinely soft-deleted holder, and ``_apply_plan`` collapsed both into
``ComponentSoftDeletedError`` ("Hard-delete it") -- an actively wrong instruction for a row that
is live and, if foreign, unreachable by the caller's own tenant-scoped ``delete_component``.

The fix reads ``deleted_at`` inside the same in-transaction check the write fails against (a
post-hoc re-probe cannot close this race: the winner's row could itself be soft-deleted between
the failed insert and the re-probe), and raises two distinguishable messages. This suite pins the
io layer's handling of both, simulated by making the pre-pass probe lie (stale/raced) about a row
that is really there by the time the write runs -- the same effect a genuine race produces,
without needing real concurrency.
"""

import pytest

from egpt.db.base import ComponentType
from egpt.registry.io import (
    EXPORT_FORMAT,
    ComponentIdConflictError,
    ComponentSoftDeletedError,
    import_document,
)

MODEL = {"id": "gpt-5.5", "name": "OpenAIResponses", "provider": "OpenAI"}
TENANT_A = "acme"
TENANT_B = "globex"


def _bundle(component_id):
    return {
        "format": EXPORT_FORMAT,
        "format_version": 1,
        "components": [
            {
                "component_id": component_id,
                "component_type": "agent",
                "name": component_id.title(),
                "stage": "published",
                "config": {"id": component_id, "model": MODEL},
                "links": [],
            }
        ],
    }


def _seed_live_holder(db, component_id, tenant_id):
    db.create_component_with_config(
        component_id=component_id,
        component_type=ComponentType.AGENT,
        name=component_id,
        config={"id": component_id, "model": MODEL},
        stage="published",
        tenant_id=tenant_id,
    )


class TestALostRaceIsAConflictNotASoftDelete:
    def test_a_live_race_loser_raises_conflict_not_soft_deleted(self, target_db, monkeypatch):
        """The pre-pass says free (raced); the write finds a live row. That must surface as
        the same conflict the planner would have raised had it seen the row in time -- never
        the soft-deleted diagnostic, which tells the caller to hard-delete a row that was never
        deleted."""
        _seed_live_holder(target_db, "race-agent", tenant_id=TENANT_B)
        monkeypatch.setattr(target_db, "component_id_is_taken", lambda component_id: False)

        with pytest.raises(ComponentIdConflictError):
            import_document(target_db, _bundle("race-agent"), tenant_id=TENANT_A)

    def test_on_deleted_skip_does_not_swallow_a_live_race_loser(self, target_db, monkeypatch):
        """``on_deleted="skip"`` exists for genuinely soft-deleted holders. A live collision
        must still surface -- silently skipping it would hide a real conflict from the caller,
        and previously did so under the reason "soft_deleted"."""
        _seed_live_holder(target_db, "race-agent", tenant_id=TENANT_B)
        monkeypatch.setattr(target_db, "component_id_is_taken", lambda component_id: False)

        with pytest.raises(ComponentIdConflictError):
            import_document(target_db, _bundle("race-agent"), tenant_id=TENANT_A, on_deleted="skip")

    def test_a_genuinely_soft_deleted_holder_is_unaffected(self, target_db, monkeypatch):
        """The other branch of the same db-layer discrimination: a real soft-deleted holder
        still gets ``ComponentSoftDeletedError``, unchanged by this fix."""
        _seed_live_holder(target_db, "deleted-agent", tenant_id=TENANT_B)
        target_db.delete_component("deleted-agent", tenant_id=TENANT_B)
        monkeypatch.setattr(target_db, "component_id_is_taken", lambda component_id: False)

        with pytest.raises(ComponentSoftDeletedError):
            import_document(target_db, _bundle("deleted-agent"), tenant_id=TENANT_A)

    def test_a_genuinely_soft_deleted_holder_still_honours_on_deleted_skip(self, target_db, monkeypatch):
        _seed_live_holder(target_db, "deleted-agent", tenant_id=TENANT_B)
        target_db.delete_component("deleted-agent", tenant_id=TENANT_B)
        monkeypatch.setattr(target_db, "component_id_is_taken", lambda component_id: False)

        results = import_document(target_db, _bundle("deleted-agent"), tenant_id=TENANT_A, on_deleted="skip")

        assert [r.action for r in results] == ["skipped"]
        assert [r.reason for r in results] == ["soft_deleted"]
