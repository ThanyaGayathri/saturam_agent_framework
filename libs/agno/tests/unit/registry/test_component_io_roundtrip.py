"""Export -> import round trip for component definitions (EGPT-2.8).

The acceptance criterion this file exists for is "export -> import reproduces the definition",
and the interesting word is *reproduces*. Two things are deliberately **not** expected to come
back identical, and a test that demanded they did would be wrong rather than strict:

* **Version numbers.** A bundle carries one config per component; the target renumbers from 1.
  So the assertions compare a link's ``child_version`` against the version the *target* created,
  never against the source's.
* **The ``db`` block.** Export strips connection fields, and the two databases are different
  objects by construction. Comparisons pop ``db`` from both sides and say so.

Everything else -- config bytes, component metadata, link tuples, member order, and the fact
that the result hydrates -- is expected to match exactly.

Real temp-file SQLite databases, two of them, for the reason given in ``conftest.py``.
"""

import pytest

from egpt.agent import Agent
from egpt.db.base import ComponentType
from egpt.registry import Registry
from egpt.registry.io import (
    EXPORT_FORMAT,
    EXPORT_FORMAT_VERSION,
    ComponentIdConflictError,
    ComponentIOError,
    dump_document,
    export_component,
    import_document,
    parse_document,
)
from egpt.registry.schema import SCHEMA_VERSION
from egpt.team import Team, get_team_by_id

SOURCE_TENANT = "acme"
TARGET_TENANT = "globex"


# ============================================================================
# FIXTURES / HELPERS
# ============================================================================


def _model():
    from egpt.integrations.models.openai import OpenAIResponses

    return OpenAIResponses(id="gpt-5.5")


def _agent(component_id, name):
    return Agent(id=component_id, name=name, model=_model(), instructions=[f"You are {name}."])


def _validator(db, registry=None, tenant_id=None):
    """The real EGPT-2.2 validator, bound the way ``egpt.components.io`` binds it."""
    from egpt.components.io import _validator_for

    return _validator_for(db, registry=registry, policy=None, tenant_id=tenant_id)


@pytest.fixture
def registry(component_db):
    reg = Registry()
    reg.add_db(component_db)
    return reg


@pytest.fixture
def seeded_team(component_db):
    """A team with two member agents, published in the source tenant."""
    first = _agent("faq-agent", "FAQ Agent")
    second = _agent("research-agent", "Research Agent")
    team = Team(
        id="support-team",
        name="Support Team",
        model=_model(),
        description="Answers customer questions.",
        members=[first, second],
    )
    first.save(db=component_db, stage="published")
    second.save(db=component_db, stage="published")
    team.save(db=component_db, stage="published")
    _stamp_tenant(component_db, SOURCE_TENANT)
    return team


def _stamp_tenant(db, tenant_id):
    """Put the saved rows in a tenant.

    ``Agent.save`` / ``Team.save`` take no tenant (they predate EGPT-2.7's threading), so the
    rows land untenanted. Rather than skip tenancy in this suite, the rows are stamped directly
    -- the alternative, building every fixture through ``create_component_with_config``, would
    stop exercising the serialization these tests are actually about.
    """
    from sqlalchemy import text

    with db.Session() as sess, sess.begin():
        for table in ("egpt_components", "egpt_component_configs", "egpt_component_links"):
            sess.execute(text(f"UPDATE {table} SET tenant_id = :tenant"), {"tenant": tenant_id})


def _import(db, document, **kwargs):
    kwargs.setdefault("tenant_id", TARGET_TENANT)
    kwargs.setdefault("validate", _validator(db, tenant_id=kwargs["tenant_id"]))
    return import_document(db, document, **kwargs)


# ============================================================================
# EXPORT
# ============================================================================


class TestExportProducesAPortableDocument:
    def test_envelope_identifies_the_format_and_both_versions(self, component_db, seeded_team):
        document = export_component(component_db, "support-team", tenant_id=SOURCE_TENANT)

        assert document["format"] == EXPORT_FORMAT
        assert document["format_version"] == EXPORT_FORMAT_VERSION
        assert document["schema_version"] == SCHEMA_VERSION
        assert document["root"] == "support-team"
        assert document["source"]["tenant_id"] == SOURCE_TENANT

    def test_dependencies_travel_with_the_root_children_first(self, component_db, seeded_team):
        document = export_component(component_db, "support-team", tenant_id=SOURCE_TENANT)

        ids = [entry["component_id"] for entry in document["components"]]
        assert set(ids) == {"faq-agent", "research-agent", "support-team"}
        # Children first: the target has to create a member before a link can point at it.
        assert ids.index("support-team") == 2

    def test_links_carry_no_child_version(self, component_db, seeded_team):
        document = export_component(component_db, "support-team", tenant_id=SOURCE_TENANT)

        team = next(e for e in document["components"] if e["component_id"] == "support-team")
        assert len(team["links"]) == 2
        for link in team["links"]:
            assert "child_version" not in link
            assert link["link_kind"] == "member"

    def test_include_dependencies_false_exports_only_the_root(self, component_db, seeded_team):
        document = export_component(component_db, "support-team", tenant_id=SOURCE_TENANT, include_dependencies=False)

        assert [entry["component_id"] for entry in document["components"]] == ["support-team"]

    def test_a_draft_only_component_exports(self, component_db, registry):
        """`current_version` is NULL until something is published.

        The graph loader resolves it and would return None, so export resolves the version with
        `get_config` first and passes it in explicitly. Without that, a definition could be
        created and then be impossible to export until published.
        """
        component_db.create_component_with_config(
            component_id="draft-agent",
            component_type=ComponentType.AGENT,
            name="Draft Agent",
            config={"id": "draft-agent", "model": {"id": "gpt-5.5", "name": "OpenAIResponses"}},
            stage="draft",
        )

        document = export_component(component_db, "draft-agent")

        assert document["components"][0]["stage"] == "draft"

    def test_dump_is_byte_stable_across_calls(self, component_db, seeded_team):
        document = export_component(component_db, "support-team", tenant_id=SOURCE_TENANT)

        assert dump_document(document) == dump_document(document)
        assert parse_document(dump_document(document))["root"] == "support-team"

    def test_export_is_tenant_scoped(self, component_db, seeded_team):
        with pytest.raises(ComponentIOError, match="not found"):
            export_component(component_db, "support-team", tenant_id="someone-else")


# ============================================================================
# THE ROUND TRIP
# ============================================================================


class TestRoundTripReproducesTheDefinition:
    """Export from one database and tenant, import into another, compare."""

    @pytest.fixture
    def imported(self, component_db, seeded_team, target_db):
        document = export_component(component_db, "support-team", tenant_id=SOURCE_TENANT)
        results = _import(target_db, document)
        return document, results

    def test_every_component_is_created(self, imported):
        _document, results = imported

        assert [r.action for r in results] == ["created", "created", "created"]
        assert all(r.version == 1 for r in results)

    def test_stored_config_equals_the_exported_config(self, imported, target_db):
        document, _results = imported

        for entry in document["components"]:
            stored = target_db.get_config(entry["component_id"], tenant_id=TARGET_TENANT)
            assert stored["config"] == entry["config"]

    def test_component_row_fields_survive(self, imported, target_db):
        document, _results = imported

        for entry in document["components"]:
            row = target_db.get_component(entry["component_id"], tenant_id=TARGET_TENANT)
            assert row["component_type"] == entry["component_type"]
            assert row["name"] == entry["name"]
            assert row["description"] == entry["description"]
            assert row["metadata"] == entry["metadata"]

    def test_links_are_repointed_at_the_targets_own_versions(self, imported, target_db):
        document, _results = imported
        source_entry = next(e for e in document["components"] if e["component_id"] == "support-team")

        links = target_db.get_links("support-team", 1, tenant_id=TARGET_TENANT)

        assert [(link["link_kind"], link["link_key"], link["position"]) for link in links] == [
            (link["link_kind"], link["link_key"], link["position"]) for link in source_entry["links"]
        ]
        for link in links:
            child = target_db.get_config(link["child_component_id"], tenant_id=TARGET_TENANT)
            assert link["child_version"] == child["version"]

    def test_the_imported_definition_hydrates(self, imported, target_db, seeded_team):
        """The criterion that matters: what came back is a working Team, not just rows.

        `to_dict` is compared with `db` popped from both sides -- the source team was built
        against one database and the imported one resolves against another, so that key differs
        by construction and comparing it would be asserting the round trip failed.
        """
        registry = Registry()
        registry.add_db(target_db)

        team = get_team_by_id(target_db, "support-team", registry=registry, tenant_id=TARGET_TENANT)

        assert team is not None
        assert team.name == "Support Team"
        assert [member.id for member in team.members] == ["faq-agent", "research-agent"]

        expected = dict(seeded_team.to_dict())
        actual = dict(team.to_dict())
        expected.pop("db", None)
        actual.pop("db", None)
        expected.pop("schema_version", None)
        actual.pop("schema_version", None)
        assert actual == expected

    def test_rows_carry_the_target_tenant_not_the_source(self, imported, target_db):
        document, _results = imported

        for entry in document["components"]:
            assert target_db.get_component(entry["component_id"], tenant_id=SOURCE_TENANT) is None
            assert target_db.get_component(entry["component_id"], tenant_id=TARGET_TENANT) is not None


# ============================================================================
# ID REWRITING
# ============================================================================


class TestIdRewriting:
    def test_prefix_renames_ids_links_and_member_references(self, component_db, seeded_team, target_db):
        document = export_component(component_db, "support-team", tenant_id=SOURCE_TENANT)

        _import(target_db, document, id_prefix="copy-")

        team_config = target_db.get_config("copy-support-team", tenant_id=TARGET_TENANT)["config"]
        assert team_config["id"] == "copy-support-team"
        assert [m["agent_id"] for m in team_config["members"]] == ["copy-faq-agent", "copy-research-agent"]

        links = target_db.get_links("copy-support-team", 1, tenant_id=TARGET_TENANT)
        assert {link["child_component_id"] for link in links} == {"copy-faq-agent", "copy-research-agent"}

    def test_renamed_definition_still_hydrates(self, component_db, seeded_team, target_db):
        document = export_component(component_db, "support-team", tenant_id=SOURCE_TENANT)
        _import(target_db, document, id_prefix="copy-")

        registry = Registry()
        registry.add_db(target_db)
        team = get_team_by_id(target_db, "copy-support-team", registry=registry, tenant_id=TARGET_TENANT)

        assert [member.id for member in team.members] == ["copy-faq-agent", "copy-research-agent"]

    def test_new_component_id_renames_a_single_component_bundle(self, component_db, seeded_team, target_db):
        document = export_component(component_db, "faq-agent", tenant_id=SOURCE_TENANT, include_dependencies=False)

        _import(target_db, document, new_component_id="renamed-agent")

        assert target_db.get_component("renamed-agent", tenant_id=TARGET_TENANT) is not None
        assert target_db.get_config("renamed-agent", tenant_id=TARGET_TENANT)["config"]["id"] == "renamed-agent"

    def test_new_component_id_is_refused_for_a_multi_component_bundle(self, component_db, seeded_team, target_db):
        document = export_component(component_db, "support-team", tenant_id=SOURCE_TENANT)

        with pytest.raises(ComponentIOError, match="renames a single component"):
            _import(target_db, document, new_component_id="whatever")

    def test_ids_outside_the_bundle_are_left_alone(self, component_db, seeded_team, target_db):
        """A bundle may legitimately reference something the target already holds."""
        document = export_component(component_db, "support-team", tenant_id=SOURCE_TENANT, include_dependencies=False)
        # The members exist in the target under their original ids.
        members = export_component(component_db, "faq-agent", tenant_id=SOURCE_TENANT, include_dependencies=False)
        _import(target_db, members)
        others = export_component(component_db, "research-agent", tenant_id=SOURCE_TENANT, include_dependencies=False)
        _import(target_db, others)

        _import(target_db, document, id_prefix="copy-")

        team_config = target_db.get_config("copy-support-team", tenant_id=TARGET_TENANT)["config"]
        assert [m["agent_id"] for m in team_config["members"]] == ["faq-agent", "research-agent"]

    def test_workflow_step_references_are_rewritten_through_containers(self):
        """Nested containers, without a database: this is pure dict surgery.

        `Parallel`/`Loop`/`Steps`/`Condition` nest under `steps`, `Condition` also under
        `else_steps`, and `Router` under `choices`. Missing one produces a workflow that
        imports and then hydrates against the wrong agent.
        """
        from egpt.registry.io import rewrite_component_ids

        document = {
            "format": EXPORT_FORMAT,
            "format_version": 1,
            "components": [
                {
                    "component_id": "wf",
                    "component_type": "workflow",
                    "config": {
                        "id": "wf",
                        "steps": [
                            {"type": "Step", "agent_id": "a"},
                            {"type": "Parallel", "steps": [{"type": "Step", "team_id": "b"}]},
                            {
                                "type": "Condition",
                                "steps": [{"type": "Step", "agent_id": "a"}],
                                "else_steps": [{"type": "Step", "workflow_id": "c"}],
                            },
                            {"type": "Router", "choices": [{"type": "Step", "agent_id": "d"}]},
                        ],
                    },
                    "links": [],
                }
            ],
        }

        result = rewrite_component_ids(document, {"a": "x-a", "b": "x-b", "c": "x-c", "d": "x-d"})

        steps = result["components"][0]["config"]["steps"]
        assert steps[0]["agent_id"] == "x-a"
        assert steps[1]["steps"][0]["team_id"] == "x-b"
        assert steps[2]["steps"][0]["agent_id"] == "x-a"
        assert steps[2]["else_steps"][0]["workflow_id"] == "x-c"
        assert steps[3]["choices"][0]["agent_id"] == "x-d"


# ============================================================================
# BUNDLE INTEGRITY
# ============================================================================


class TestBundleIntegrity:
    def test_a_dangling_link_names_every_offender(self, component_db, seeded_team, target_db):
        document = export_component(component_db, "support-team", tenant_id=SOURCE_TENANT, include_dependencies=False)

        with pytest.raises(ComponentIOError) as excinfo:
            _import(target_db, document)

        message = str(excinfo.value)
        assert "faq-agent" in message and "research-agent" in message

    def test_a_bundle_that_is_not_a_bundle_is_refused(self, target_db):
        with pytest.raises(ComponentIOError, match="Not a component bundle"):
            parse_document({"components": []})

    def test_a_newer_format_version_is_refused(self, target_db):
        with pytest.raises(ComponentIOError, match="format_version"):
            parse_document(
                {
                    "format": EXPORT_FORMAT,
                    "format_version": EXPORT_FORMAT_VERSION + 1,
                    "components": [{"component_id": "a", "component_type": "agent", "config": {}}],
                }
            )

    def test_duplicate_ids_in_one_bundle_are_refused(self):
        entry = {"component_id": "a", "component_type": "agent", "config": {}}
        with pytest.raises(ComponentIOError, match="appears twice"):
            parse_document({"format": EXPORT_FORMAT, "format_version": 1, "components": [entry, dict(entry)]})


# ============================================================================
# CONFLICT POLICIES
# ============================================================================


class TestConflictPolicies:
    @pytest.fixture
    def already_imported(self, component_db, seeded_team, target_db):
        document = export_component(component_db, "support-team", tenant_id=SOURCE_TENANT)
        _import(target_db, document)
        return document

    def test_error_is_the_default(self, already_imported, target_db):
        with pytest.raises(ComponentIdConflictError):
            _import(target_db, already_imported)

    def test_the_message_is_identical_for_a_foreign_tenants_component(self, already_imported, target_db):
        """`component_id` is the global primary key, so the collision is observable either way.

        Labelling *whose* it is would not hide anything -- it would only hand a prober a
        cleaner ownership oracle. The two messages are asserted equal so a future change that
        adds "owned by another tenant" to one of them fails here.
        """
        with pytest.raises(ComponentIdConflictError) as mine:
            _import(target_db, already_imported, tenant_id=TARGET_TENANT)
        with pytest.raises(ComponentIdConflictError) as theirs:
            _import(target_db, already_imported, tenant_id="a-third-tenant")

        assert str(mine.value) == str(theirs.value)

    def test_skip_writes_nothing(self, already_imported, target_db):
        before = target_db.list_configs("support-team", tenant_id=TARGET_TENANT)

        results = _import(target_db, already_imported, on_conflict="skip")

        assert [r.action for r in results] == ["skipped", "skipped", "skipped"]
        assert target_db.list_configs("support-team", tenant_id=TARGET_TENANT) == before

    def test_new_version_appends_when_the_definition_changed(self, already_imported, target_db):
        changed = dict(already_imported)
        changed["components"] = [dict(entry) for entry in already_imported["components"]]
        team = next(e for e in changed["components"] if e["component_id"] == "support-team")
        team["config"] = {**team["config"], "description": "A different description."}

        results = _import(target_db, changed, on_conflict="new_version")

        actions = {r.component_id: r.action for r in results}
        assert actions["support-team"] == "new_version"
        assert actions["faq-agent"] == "unchanged"
        assert target_db.get_config("support-team", tenant_id=TARGET_TENANT)["version"] == 2

    def test_new_version_degrades_to_a_refusal_across_tenants(self, already_imported, target_db):
        """Never a write: a component does not move between tenants."""
        with pytest.raises(ComponentIdConflictError):
            _import(target_db, already_imported, tenant_id="a-third-tenant", on_conflict="new_version")

        assert target_db.get_component("support-team", tenant_id="a-third-tenant") is None

    def test_an_unknown_policy_is_refused_before_anything_runs(self, already_imported, target_db):
        with pytest.raises(ComponentIOError, match="on_conflict"):
            _import(target_db, already_imported, on_conflict="clobber")


# ============================================================================
# DRY RUN AND GUARDS
# ============================================================================


class TestDryRun:
    def test_dry_run_reports_actions_and_writes_nothing(self, component_db, seeded_team, target_db):
        document = export_component(component_db, "support-team", tenant_id=SOURCE_TENANT)

        results = _import(target_db, document, dry_run=True)

        assert [r.action for r in results] == ["created", "created", "created"]
        assert target_db.get_component("support-team", tenant_id=TARGET_TENANT) is None

    def test_dry_run_still_validates(self, target_db):
        document = {
            "format": EXPORT_FORMAT,
            "format_version": 1,
            "components": [
                {
                    "component_id": "broken",
                    "component_type": "agent",
                    "config": {"id": "broken", "model": {"id": "x", "provider": "no-such-provider"}},
                    "links": [],
                }
            ],
        }

        from egpt.registry.schema import ConfigValidationError

        with pytest.raises(ConfigValidationError):
            _import(target_db, document, dry_run=True)


class TestAsyncDbIsRefused:
    def test_import_refuses_an_async_database(self):
        """Component methods raise `NotImplementedError` on every async backend.

        Refusing up front gives the same message the components router already gives, instead
        of a `NotImplementedError` from three frames down.
        """
        from egpt.components.io import import_component

        class NotADb:
            pass

        with pytest.raises(ComponentIOError, match="Async databases"):
            import_component(NotADb(), {"format": EXPORT_FORMAT, "format_version": 1, "components": []})
