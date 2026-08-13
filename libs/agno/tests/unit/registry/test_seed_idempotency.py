"""Seeding template definitions is safe to re-run (EGPT-2.8).

"Idempotent" is asserted here in the strongest form the schema allows: a second run writes **no
audit rows**. EGPT-2.6 guarantees that a successful mutation cannot commit without one, so an
unchanged audit count is a much stronger statement than an unchanged version count -- the latter
would still pass if seeding quietly rewrote a config in place.

The regression that motivates the whole design is ``test_a_db_reference_does_not_look_changed``:
a template says ``db: {"id": "os-db"}`` while the stored row holds the resolved block with the
connection details merged in. Comparing the *template* against the row reports a difference every
single run and appends a version forever. So ``configs_equal`` compares what would actually be
written -- post db-resolution, post-validation -- against what is there.
"""

import json

import pytest

from egpt.components.io import seed_from_directory
from egpt.registry import Registry
from egpt.registry.io import ComponentIOError, ComponentSoftDeletedError

TENANT = "acme"

AGENT_TEMPLATE = {
    "format": "egpt.component-bundle",
    "format_version": 1,
    "schema_version": 1,
    "components": [
        {
            "component_id": "faq-agent",
            "component_type": "agent",
            "name": "FAQ Agent",
            "description": "Answers frequently asked questions.",
            "stage": "published",
            "config": {
                "id": "faq-agent",
                "name": "FAQ Agent",
                "model": {"id": "gpt-5.5", "name": "OpenAIResponses", "provider": "OpenAI"},
                "instructions": ["Answer from the FAQ only."],
            },
            "links": [],
        }
    ],
}

SECOND_TEMPLATE = {
    "format": "egpt.component-bundle",
    "format_version": 1,
    "schema_version": 1,
    "components": [
        {
            "component_id": "triage-agent",
            "component_type": "agent",
            "name": "Triage Agent",
            "stage": "published",
            "config": {
                "id": "triage-agent",
                "name": "Triage Agent",
                "model": {"id": "gpt-5.5", "name": "OpenAIResponses", "provider": "OpenAI"},
            },
            "links": [],
        }
    ],
}


@pytest.fixture
def templates(tmp_path):
    (tmp_path / "agent.faq.json").write_text(json.dumps(AGENT_TEMPLATE), encoding="utf-8")
    (tmp_path / "agent.triage.json").write_text(json.dumps(SECOND_TEMPLATE), encoding="utf-8")
    return tmp_path


@pytest.fixture
def registry(component_db):
    reg = Registry()
    reg.add_db(component_db)
    return reg


def _seed(db, templates_dir, registry, **kwargs):
    return seed_from_directory(db, templates_dir, tenant_id=TENANT, registry=registry, **kwargs)


def _audit_count(db, component_id):
    _records, total = db.list_component_audit(component_id, tenant_id=TENANT, limit=1)
    return total


class TestFirstRun:
    def test_every_component_is_created_at_version_one(self, component_db, templates, registry):
        results = _seed(component_db, templates, registry)

        assert sorted((r.component_id, r.action, r.version) for r in results) == [
            ("faq-agent", "created", 1),
            ("triage-agent", "created", 1),
        ]

    def test_the_definitions_are_published_and_current(self, component_db, templates, registry):
        _seed(component_db, templates, registry)

        row = component_db.get_component("faq-agent", tenant_id=TENANT)
        assert row["current_version"] == 1
        assert component_db.get_config("faq-agent", tenant_id=TENANT)["stage"] == "published"

    def test_seeding_a_draft_leaves_no_current_version(self, component_db, templates, registry):
        _seed(component_db, templates, registry, stage="draft")

        assert component_db.get_component("faq-agent", tenant_id=TENANT)["current_version"] is None


class TestSecondRunChangesNothing:
    def test_every_component_reports_unchanged(self, component_db, templates, registry):
        _seed(component_db, templates, registry)

        results = _seed(component_db, templates, registry)

        assert {r.action for r in results} == {"unchanged"}

    def test_no_new_config_versions(self, component_db, templates, registry):
        _seed(component_db, templates, registry)
        before = len(component_db.list_configs("faq-agent", tenant_id=TENANT))

        _seed(component_db, templates, registry)

        assert len(component_db.list_configs("faq-agent", tenant_id=TENANT)) == before == 1

    def test_no_new_audit_rows(self, component_db, templates, registry):
        """The sharpest available assertion, and the reason it is worth making.

        A mutation cannot commit without an audit row (EGPT-2.6), so an unchanged count proves
        nothing was written at all -- not merely that no *new version* appeared, which would
        still hold if a config had been rewritten in place.
        """
        _seed(component_db, templates, registry)
        before = _audit_count(component_db, "faq-agent")

        _seed(component_db, templates, registry)

        assert _audit_count(component_db, "faq-agent") == before

    def test_a_db_reference_does_not_look_changed(self, component_db, tmp_path, registry):
        """The regression the whole comparison design turns on.

        The template names a database by id; what gets stored is that block merged with the
        registered db's connection details. Comparing the template against the row would differ
        on every run and append a version forever.
        """
        template = json.loads(json.dumps(AGENT_TEMPLATE))
        template["components"][0]["config"]["db"] = {"id": "source-db"}
        (tmp_path / "agent.faq.json").write_text(json.dumps(template), encoding="utf-8")

        _seed(component_db, tmp_path, registry)
        stored = component_db.get_config("faq-agent", tenant_id=TENANT)["config"]
        # The stored block really did gain fields -- otherwise this test proves nothing.
        assert set(stored["db"]) > {"id"}

        results = _seed(component_db, tmp_path, registry)

        assert [r.action for r in results] == ["unchanged"]


class TestAChangedTemplate:
    def test_appends_exactly_one_version_to_the_component_that_changed(self, component_db, templates, registry):
        _seed(component_db, templates, registry)
        changed = json.loads(json.dumps(AGENT_TEMPLATE))
        changed["components"][0]["config"]["instructions"].append("Escalate anything about billing.")
        (templates / "agent.faq.json").write_text(json.dumps(changed), encoding="utf-8")

        results = _seed(component_db, templates, registry)

        actions = {r.component_id: r.action for r in results}
        assert actions == {"faq-agent": "new_version", "triage-agent": "unchanged"}
        assert len(component_db.list_configs("faq-agent", tenant_id=TENANT)) == 2
        assert len(component_db.list_configs("triage-agent", tenant_id=TENANT)) == 1

    def test_the_published_version_is_appended_never_mutated(self, component_db, templates, registry):
        _seed(component_db, templates, registry)
        original = component_db.get_config("faq-agent", version=1, tenant_id=TENANT)["config"]
        changed = json.loads(json.dumps(AGENT_TEMPLATE))
        changed["components"][0]["config"]["instructions"] = ["Something else entirely."]
        (templates / "agent.faq.json").write_text(json.dumps(changed), encoding="utf-8")

        _seed(component_db, templates, registry)

        assert component_db.get_config("faq-agent", version=1, tenant_id=TENANT)["config"] == original
        assert component_db.get_config("faq-agent", tenant_id=TENANT)["version"] == 2

    def test_a_changed_display_name_updates_metadata_without_a_new_version(self, component_db, templates, registry):
        _seed(component_db, templates, registry)
        renamed = json.loads(json.dumps(AGENT_TEMPLATE))
        renamed["components"][0]["name"] = "Frequently Asked Questions"
        (templates / "agent.faq.json").write_text(json.dumps(renamed), encoding="utf-8")

        results = _seed(component_db, templates, registry)

        actions = {r.component_id: r.action for r in results}
        assert actions["faq-agent"] == "metadata_updated"
        assert component_db.get_component("faq-agent", tenant_id=TENANT)["name"] == "Frequently Asked Questions"
        assert len(component_db.list_configs("faq-agent", tenant_id=TENANT)) == 1

    def test_re_running_after_the_change_is_quiet_again(self, component_db, templates, registry):
        _seed(component_db, templates, registry)
        changed = json.loads(json.dumps(AGENT_TEMPLATE))
        changed["components"][0]["config"]["instructions"].append("Escalate anything about billing.")
        (templates / "agent.faq.json").write_text(json.dumps(changed), encoding="utf-8")
        _seed(component_db, templates, registry)

        results = _seed(component_db, templates, registry)

        assert {r.action for r in results} == {"unchanged"}


class TestTwoTemplatesDeclaringTheSameComponent:
    def test_the_second_one_converges_instead_of_colliding(self, component_db, tmp_path, registry):
        """What lets a team bundle re-declare the agents it needs.

        Both files carry `faq-agent`; the second must report it unchanged rather than raise an
        id collision or write a duplicate.
        """
        (tmp_path / "a.json").write_text(json.dumps(AGENT_TEMPLATE), encoding="utf-8")
        (tmp_path / "b.json").write_text(json.dumps(AGENT_TEMPLATE), encoding="utf-8")

        results = _seed(component_db, tmp_path, registry)

        assert [r.action for r in results] == ["created", "unchanged"]
        assert len(component_db.list_configs("faq-agent", tenant_id=TENANT)) == 1


class TestASoftDeletedComponent:
    """The id is held but invisible: `get_component` and `list_configs` both filter
    `deleted_at IS NULL`, while the row still owns the primary key. There is no undelete
    anywhere in `BaseDb`, so the only honest outcomes are a clear refusal or a skip."""

    def test_seeding_over_it_refuses_with_an_actionable_message(self, component_db, templates, registry):
        _seed(component_db, templates, registry)
        component_db.delete_component("faq-agent", tenant_id=TENANT)

        with pytest.raises(ComponentSoftDeletedError, match="hard-delete|hard_delete"):
            _seed(component_db, templates, registry)

    def test_on_deleted_skip_leaves_it_alone(self, component_db, templates, registry):
        _seed(component_db, templates, registry)
        component_db.delete_component("faq-agent", tenant_id=TENANT)

        results = _seed(component_db, templates, registry, on_deleted="skip")

        actions = {r.component_id: r.action for r in results}
        assert actions["faq-agent"] == "skipped"
        assert actions["triage-agent"] == "unchanged"

    def test_a_hard_delete_makes_the_id_reusable(self, component_db, templates, registry):
        _seed(component_db, templates, registry)
        component_db.delete_component("faq-agent", hard_delete=True, tenant_id=TENANT)

        results = _seed(component_db, templates, registry)

        assert {r.component_id: r.action for r in results}["faq-agent"] == "created"


class TestDirectoryHandling:
    def test_an_empty_directory_is_an_error_not_a_silent_success(self, component_db, tmp_path, registry):
        with pytest.raises(ComponentIOError, match="No template bundles"):
            _seed(component_db, tmp_path, registry)

    def test_a_missing_directory_says_so(self, component_db, tmp_path, registry):
        with pytest.raises(ComponentIOError, match="not found"):
            _seed(component_db, tmp_path / "nope", registry)

    def test_files_are_applied_in_a_stable_order(self, component_db, templates, registry):
        """Sorted, so a seed run is reproducible and its output diffable."""
        results = _seed(component_db, templates, registry)

        assert [r.component_id for r in results] == ["faq-agent", "triage-agent"]

    def test_dry_run_writes_nothing(self, component_db, templates, registry):
        results = _seed(component_db, templates, registry, dry_run=True)

        assert {r.action for r in results} == {"created"}
        assert component_db.get_component("faq-agent", tenant_id=TENANT) is None
