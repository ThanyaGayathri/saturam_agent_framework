"""Draft/published lifecycle guarantees for component configs (EGPT-2.6).

The versioning, stages, publish-sets-current and rollback behaviour all predate this ticket.
EGPT-2.6's acceptance criteria ask that they be *verified* rather than rebuilt, and verifying
them turned one of them up as untrue:

``delete_config`` selected the config's ``stage`` and then never looked at it. Only the
*current* version was protected, so a published-but-not-current version was deletable --
contradicting the method's own docstring ("Only draft configs can be deleted. Published configs
are immutable"), the route description ("Cannot delete published or current configs") and the
ticket. Nothing pinned it. ``test_a_published_non_current_version_cannot_be_deleted`` is that
missing test, and the rest of this file pins the guarantees that were already holding so the
next change to this area cannot quietly drop one.

Ordering note: the published check runs *after* the current-version check, so a version that is
both keeps answering "Cannot delete current config" exactly as before. The fix adds a refusal
where there was none rather than relabelling one that already worked.
"""

import os
import tempfile

import pytest

from egpt.db.base import ComponentType

TENANT = "tenant-a"


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


def _create(db, component_id="agent-1", *, stage="draft"):
    return db.create_component_with_config(
        component_id=component_id,
        component_type=ComponentType.AGENT,
        name=component_id,
        config={"model": "gpt-5.5"},
        stage=stage,
        tenant_id=TENANT,
    )


def _publish_second_version(db):
    """Leaves v1 published+current and v2 published-but-not-current after the rollback."""
    _create(db, stage="published")
    db.upsert_config("agent-1", config={"model": "v2"}, tenant_id=TENANT)
    db.upsert_config("agent-1", version=2, stage="published", tenant_id=TENANT)
    db.set_current_version("agent-1", version=1, tenant_id=TENANT)


# ============================================================================
# PUBLISHED CONFIGS ARE IMMUTABLE
# ============================================================================


def test_a_published_version_cannot_be_edited(sqlite_db):
    _create(sqlite_db, stage="published")

    with pytest.raises(ValueError, match="Cannot update published config"):
        sqlite_db.upsert_config("agent-1", version=1, config={"model": "other"}, tenant_id=TENANT)


def test_a_published_version_cannot_be_relabelled(sqlite_db):
    """Immutability covers the metadata, not just the config blob."""
    _create(sqlite_db, stage="published")

    with pytest.raises(ValueError, match="Cannot update published config"):
        sqlite_db.upsert_config("agent-1", version=1, label="stable", tenant_id=TENANT)


def test_a_published_non_current_version_cannot_be_deleted(sqlite_db):
    """The gap this ticket closed.

    v2 is published but no longer current, so before the fix it fell past the only guard and
    was deleted -- destroying a version something may still have been pinned to by label.
    """
    _publish_second_version(sqlite_db)

    with pytest.raises(ValueError, match="Cannot delete published config"):
        sqlite_db.delete_config("agent-1", version=2, tenant_id=TENANT)

    assert sqlite_db.get_config("agent-1", version=2, tenant_id=TENANT) is not None


def test_a_published_current_version_still_reports_the_current_refusal(sqlite_db):
    """The pre-existing message is unchanged for the overlapping case."""
    _create(sqlite_db, stage="published")

    with pytest.raises(ValueError, match="Cannot delete current config"):
        sqlite_db.delete_config("agent-1", version=1, tenant_id=TENANT)


# ============================================================================
# DRAFTS STAY EDITABLE AND DELETABLE
# ============================================================================


def test_a_draft_can_be_edited(sqlite_db):
    _create(sqlite_db)

    config = sqlite_db.upsert_config("agent-1", version=1, config={"model": "edited"}, tenant_id=TENANT)
    assert config["config"] == {"model": "edited"}


def test_a_non_current_draft_can_be_deleted(sqlite_db):
    _create(sqlite_db, stage="published")
    sqlite_db.upsert_config("agent-1", config={"model": "draft"}, tenant_id=TENANT)

    assert sqlite_db.delete_config("agent-1", version=2, tenant_id=TENANT) is True
    assert sqlite_db.get_config("agent-1", version=2, tenant_id=TENANT) is None


# ============================================================================
# PUBLISH AND ROLLBACK
# ============================================================================


def test_publishing_moves_current_version(sqlite_db):
    _create(sqlite_db)
    assert sqlite_db.get_component("agent-1", tenant_id=TENANT)["current_version"] is None

    sqlite_db.upsert_config("agent-1", version=1, stage="published", tenant_id=TENANT)

    assert sqlite_db.get_component("agent-1", tenant_id=TENANT)["current_version"] == 1


def test_rollback_repoints_current_version_without_deleting_anything(sqlite_db):
    _publish_second_version(sqlite_db)

    assert sqlite_db.get_component("agent-1", tenant_id=TENANT)["current_version"] == 1
    assert sqlite_db.get_config("agent-1", version=2, tenant_id=TENANT)["stage"] == "published"


def test_a_draft_cannot_be_made_current(sqlite_db):
    """Rollback is a published-only operation, which is what distinguishes it from a
    ``current_version`` write through ``upsert_component``."""
    _create(sqlite_db)

    with pytest.raises(ValueError, match="Cannot set draft config"):
        sqlite_db.set_current_version("agent-1", version=1, tenant_id=TENANT)


def test_the_current_version_is_what_a_versionless_read_resolves(sqlite_db):
    """Production hydration reads the current version -- the last acceptance criterion."""
    _publish_second_version(sqlite_db)

    assert sqlite_db.get_config("agent-1", tenant_id=TENANT)["version"] == 1
