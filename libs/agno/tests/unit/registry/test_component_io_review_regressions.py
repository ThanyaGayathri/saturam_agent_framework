"""Regressions from the EGPT-2.8 QC review of PR #66.

Each class here pins one finding, with the reproduction that found it. They live together
rather than being scattered into the suites they belong to because what they have in common is
more useful than what separates them: every one of them is a place where the original tests
asserted the *presence* of a safety property without ever exercising the path that would break
it. The redaction suite checked configs and never the envelope; the round-trip suite checked
link versions where every version was 1; the CLI suite checked `--json` in a process that could
not reproduce the pollution.
"""

import json
import os
import tempfile

import pytest

from egpt.db.base import ComponentType
from egpt.registry import Registry
from egpt.registry.io import (
    EXPORT_FORMAT,
    REDACTED,
    ComponentIOError,
    export_component,
    import_document,
    redact_config,
    strip_redacted,
)

MODEL = {"id": "gpt-5.5", "name": "OpenAIResponses", "provider": "OpenAI"}
DSN = "postgresql+psycopg://ai:hunter2@db.internal:5432/ai"


def _bundle(*components):
    return {"format": EXPORT_FORMAT, "format_version": 1, "components": list(components)}


def _agent_entry(component_id, **overrides):
    entry = {
        "component_id": component_id,
        "component_type": "agent",
        "name": component_id.title(),
        "stage": "published",
        "config": {"id": component_id, "model": MODEL},
        "links": [],
    }
    entry.update(overrides)
    return entry


# ============================================================================
# Finding 1 (critical) -- envelope fields bypassed redaction
# ============================================================================


class TestTheWholePayloadIsRedactedNotJustTheConfig:
    """`to_payload` emits nine fields; only `config` used to be scanned.

    The severity came from the manifest rather than the leak: `redacted` reported `[]` while
    five fields carried whatever the operator had put in them, so a CI gate asserting "this
    bundle declares no secrets" passed a file containing a production password.
    """

    @pytest.fixture
    def exported(self, component_db):
        component_db.create_component_with_config(
            component_id="leaky",
            component_type=ComponentType.AGENT,
            name=f"Leaky {DSN}",
            description=f"Talks to {DSN} while debugging",
            metadata={"api_key": "sk-live-metadata"},
            notes=f"connect with {DSN}",
            label="stable",
            config={"id": "leaky", "model": MODEL},
            stage="published",
        )
        return export_component(component_db, "leaky")

    @pytest.mark.parametrize("field", ["name", "description", "notes"])
    def test_free_text_fields_no_longer_carry_a_dsn(self, exported, field):
        assert exported["components"][0][field] == REDACTED

    def test_metadata_is_scanned_like_any_other_config_tree(self, exported):
        assert exported["components"][0]["metadata"] == {"api_key": REDACTED}

    def test_the_manifest_names_every_field_it_removed(self, exported):
        assert sorted(exported["redacted"]) == [
            "components[0].description",
            "components[0].metadata.api_key",
            "components[0].name",
            "components[0].notes",
        ]

    def test_a_clean_component_still_reports_an_empty_manifest(self, component_db):
        """The manifest has to stay a signal, not a field that is always populated."""
        component_db.create_component_with_config(
            component_id="clean",
            component_type=ComponentType.AGENT,
            name="Clean Agent",
            description="Nothing to see here.",
            config={"id": "clean", "model": MODEL},
            stage="published",
        )

        assert export_component(component_db, "clean")["redacted"] == []

    def test_the_db_block_is_still_dropped_not_sentinelled(self, component_db):
        """The obvious fix for this finding regresses the other half of the design.

        Running `redact_config` over the assembled payload would scan `db_url` as an unknown
        key and leave `__redacted__` behind -- which a driver would then try to connect with.
        The two halves stay separate for that reason.
        """
        component_db.create_component_with_config(
            component_id="with-db",
            component_type=ComponentType.AGENT,
            name="With Db",
            config={"id": "with-db", "model": MODEL, "db": {"id": "os-db", "type": "postgres", "db_url": DSN}},
            stage="published",
        )

        document = export_component(component_db, "with-db")

        db_block = document["components"][0]["config"]["db"]
        assert "db_url" not in db_block
        assert REDACTED not in db_block.values()


# ============================================================================
# Finding 2 (major) -- plural credential keys
# ============================================================================


class TestPluralCredentialKeys:
    """`api_keys` is the natural shape for a dict of per-provider credentials.

    Requiring a separator after the singular term let every plural through, and the inner keys
    of such a dict are provider names that no pattern would catch -- so the outer key was the
    only chance.
    """

    @pytest.mark.parametrize(
        "key", ["api_keys", "secrets", "passwords", "credentials", "private_keys", "access_keys", "auth_tokens"]
    )
    def test_plurals_are_caught(self, key):
        config = {key: {"openai": "sk-REALSECRET"}}

        result, redacted = redact_config(config)

        assert result[key] == REDACTED
        assert redacted == [key]

    @pytest.mark.parametrize("key", ["max_tokens", "num_history_runs", "tool_call_limit"])
    def test_numeric_settings_survive_whatever_they_are_called(self, key):
        """What keeps `max_tokens` safe is the value type, not the key pattern.

        Expressing "not max_tokens" in the regex is what excluded the plurals in the first
        place. A credential is never an int, so gating on the value is both simpler and
        strictly more accurate.
        """
        config = {key: 4096}

        result, redacted = redact_config(config)

        assert result[key] == 4096
        assert redacted == []

    def test_a_string_valued_credential_dict_is_redacted_whole(self):
        config = {"model": {"id": "gpt-5.5", "api_keys": {"openai": "sk-1", "anthropic": "sk-2"}}}

        result, redacted = redact_config(config)

        assert result["model"]["api_keys"] == REDACTED
        assert redacted == ["model.api_keys"]

    def test_an_embedded_dsn_is_caught_mid_string(self):
        """Anchoring the value pattern to the start missed anything pasted into a sentence."""
        config = {"metadata": {"note": f"failing over to {DSN} until Tuesday"}}

        result, redacted = redact_config(config)

        assert result["metadata"]["note"] == REDACTED
        assert redacted == ["metadata.note"]


# ============================================================================
# Finding 6 (medium) -- the sentinel used to import as a live credential
# ============================================================================


class TestRedactedSentinelsDoNotImport:
    def test_strip_redacted_removes_the_key_and_reports_the_path(self):
        cleaned, stripped = strip_redacted({"model": {"id": "gpt-5.5", "api_key": REDACTED}})

        assert cleaned == {"model": {"id": "gpt-5.5"}}
        assert stripped == ["model.api_key"]

    def test_a_redacted_bundle_imports_without_the_sentinel(self, target_db):
        """Absent beats `__redacted__`: the SDK falls back to its environment lookup.

        A component whose `api_key` is the literal string `__redacted__` looks configured and
        fails on first call, which is the worst of both.
        """
        document = _bundle(_agent_entry("keyed", config={"id": "keyed", "model": {**MODEL, "api_key": REDACTED}}))

        import_document(target_db, document, tenant_id="acme")

        stored = target_db.get_config("keyed", tenant_id="acme")["config"]
        assert "api_key" not in stored["model"]

    def test_the_sentinel_does_not_compound_across_hops(self, component_db, target_db):
        """Re-exporting an imported bundle used to re-detect the sentinel as a fresh secret."""
        component_db.create_component_with_config(
            component_id="keyed",
            component_type=ComponentType.AGENT,
            name="Keyed",
            config={"id": "keyed", "model": {**MODEL, "api_key": "sk-live"}},
            stage="published",
        )
        first = export_component(component_db, "keyed")
        assert first["redacted"] == ["components[0].config.model.api_key"]

        import_document(target_db, first, tenant_id="acme")
        second = export_component(target_db, "keyed", tenant_id="acme")

        assert second["redacted"] == []


# ============================================================================
# Finding 3 (major) -- phase-two partial writes
# ============================================================================


class TestWritesAreOrderedChildrenFirst:
    """A parent-first bundle passed every phase-one check and then died mid-write.

    `_check_links_resolvable` only asks whether the child is *in the bundle*; the apply loop
    then wrote in document order, so a link could point at a component this run had not created
    yet. The original repro left `['a-first']` behind.
    """

    @pytest.fixture
    def parent_first(self):
        return _bundle(
            _agent_entry("a-first"),
            {
                "component_id": "b-parent",
                "component_type": "team",
                "name": "B Parent",
                "stage": "published",
                "config": {"id": "b-parent", "model": MODEL, "members": [{"type": "agent", "agent_id": "c-last"}]},
                "links": [
                    {
                        "link_kind": "member",
                        "link_key": "member_0",
                        "child_component_id": "c-last",
                        "position": 0,
                        "meta": {"type": "agent"},
                    }
                ],
            },
            _agent_entry("c-last"),
        )

    def test_the_whole_bundle_lands(self, target_db, parent_first):
        results = import_document(target_db, parent_first, tenant_id="acme")

        assert {r.component_id: r.action for r in results} == {
            "a-first": "created",
            "b-parent": "created",
            "c-last": "created",
        }

    def test_children_are_written_before_their_parents(self, target_db, parent_first):
        results = import_document(target_db, parent_first, tenant_id="acme")

        order = [r.component_id for r in results]
        assert order.index("c-last") < order.index("b-parent")

    def test_the_link_pins_the_version_the_child_actually_got(self, target_db, parent_first):
        import_document(target_db, parent_first, tenant_id="acme")

        links = target_db.get_links("b-parent", 1, tenant_id="acme")
        child = target_db.get_config("c-last", tenant_id="acme")
        assert links[0]["child_version"] == child["version"]

    def test_an_already_ordered_bundle_is_not_reshuffled(self, target_db):
        """Export emits children first; that order must survive unchanged."""
        document = _bundle(_agent_entry("first"), _agent_entry("second"), _agent_entry("third"))

        results = import_document(target_db, document, tenant_id="acme")

        assert [r.component_id for r in results] == ["first", "second", "third"]

    def test_a_cycle_is_refused_with_the_path_named(self, target_db):
        def team(component_id, child):
            return {
                "component_id": component_id,
                "component_type": "team",
                "name": component_id,
                "stage": "published",
                "config": {"id": component_id, "model": MODEL, "members": [{"type": "team", "team_id": child}]},
                "links": [{"link_kind": "member", "link_key": "member_0", "child_component_id": child, "position": 0}],
            }

        with pytest.raises(ComponentIOError, match="cycle"):
            import_document(target_db, _bundle(team("x", "y"), team("y", "x")), tenant_id="acme")

        assert target_db.list_components(tenant_id="acme") == ([], 0)


class TestAPartialWriteIsReported:
    def test_a_soft_deleted_collision_carries_what_was_already_written(self, target_db):
        """The one failure phase one cannot see, so the one that can still write partially.

        No read API can see a soft-deleted row, so this is only discoverable when the create
        raises. There is no cross-component transaction to roll the earlier writes back, so the
        contract is that they are reported rather than silently left.
        """
        target_db.create_component_with_config(
            component_id="ghost",
            component_type=ComponentType.AGENT,
            name="Ghost",
            config={"id": "ghost", "model": MODEL},
            stage="published",
            tenant_id="acme",
        )
        target_db.delete_component("ghost", tenant_id="acme")

        document = _bundle(_agent_entry("survivor"), _agent_entry("ghost"))

        with pytest.raises(ComponentIOError) as excinfo:
            import_document(target_db, document, tenant_id="acme")

        assert [r.component_id for r in excinfo.value.results] == ["survivor"]
        assert target_db.get_component("survivor", tenant_id="acme") is not None


# ============================================================================
# Finding 7 (minor) -- missing name, and stage never compared
# ============================================================================


class TestEntryShapeCoversName:
    def test_a_missing_name_is_a_bundle_error_not_a_database_error(self, target_db):
        """SQLite's `name` column is NOT NULL, so this used to surface as an IntegrityError.

        That is not a `ValueError`, so it escaped the router's 400 arm and became a 500 for
        what is simply a malformed caller document.
        """
        document = _bundle({"component_id": "nameless", "component_type": "agent", "config": {}, "links": []})

        with pytest.raises(ComponentIOError, match="name must be a non-empty string"):
            import_document(target_db, document, tenant_id="acme")

    @pytest.mark.parametrize("name", ["", None, 42])
    def test_an_unusable_name_is_refused(self, target_db, name):
        document = _bundle(_agent_entry("a", name=name))

        with pytest.raises(ComponentIOError, match="name"):
            import_document(target_db, document, tenant_id="acme")


class TestStageIsPartOfTheComparison:
    """Re-seeding with `--stage published` after a draft seed used to report "unchanged".

    The config genuinely had not changed, so the comparison said nothing to do -- leaving
    `current_version` NULL, the component unserveable, and the operator holding a zero exit
    code.
    """

    @pytest.fixture
    def registry(self, component_db):
        registry = Registry()
        registry.add_db(component_db)
        return registry

    @pytest.fixture
    def draft_seeded(self, component_db, tmp_path):
        (tmp_path / "a.json").write_text(json.dumps(_bundle(_agent_entry("a", stage="draft"))), encoding="utf-8")
        return tmp_path

    def test_publishing_an_unchanged_draft_publishes_it(self, component_db, draft_seeded, registry):
        from egpt.components.io import seed_from_directory

        seed_from_directory(component_db, draft_seeded, tenant_id="acme", registry=registry, stage="draft")
        assert component_db.get_component("a", tenant_id="acme")["current_version"] is None

        results = seed_from_directory(
            component_db, draft_seeded, tenant_id="acme", registry=registry, stage="published"
        )

        assert [r.action for r in results] == ["published"]
        assert component_db.get_component("a", tenant_id="acme")["current_version"] == 1

    def test_it_publishes_in_place_rather_than_writing_a_duplicate(self, component_db, draft_seeded, registry):
        from egpt.components.io import seed_from_directory

        seed_from_directory(component_db, draft_seeded, tenant_id="acme", registry=registry, stage="draft")
        seed_from_directory(component_db, draft_seeded, tenant_id="acme", registry=registry, stage="published")

        assert len(component_db.list_configs("a", tenant_id="acme")) == 1

    def test_a_third_run_is_quiet_again(self, component_db, draft_seeded, registry):
        from egpt.components.io import seed_from_directory

        seed_from_directory(component_db, draft_seeded, tenant_id="acme", registry=registry, stage="draft")
        seed_from_directory(component_db, draft_seeded, tenant_id="acme", registry=registry, stage="published")

        results = seed_from_directory(
            component_db, draft_seeded, tenant_id="acme", registry=registry, stage="published"
        )

        assert [r.action for r in results] == ["unchanged"]


# ============================================================================
# Finding 4 (major) -- --json pollution from sibling loggers
# ============================================================================


class TestJsonOutputSurvivesTeamHydration:
    def test_stdout_is_parseable_after_the_team_logger_has_been_used(self, tmp_path):
        """A subprocess that first switches the sibling loggers on.

        Quieting only `logging.getLogger("egpt")` left `egpt-team` and `egpt-workflow`
        interleaving INFO lines into the payload. Neither the in-process test (its interpreter
        had `egpt` imported already) nor the plain subprocess test (a fresh interpreter has not
        touched the siblings) could reproduce it -- it took running the CLI's own suite next to
        the `os` suite, which is why CI's sharding hid it.
        """
        import subprocess
        import sys
        from pathlib import Path

        root = Path(__file__).resolve().parents[5]
        (tmp_path / "a.json").write_text(json.dumps(_bundle(_agent_entry("a"))), encoding="utf-8")
        db_file = str(tmp_path / "seed.db")

        script = (
            "import sys;"
            "from egpt.utils.log import use_team_logger, log_info;"
            "use_team_logger();"
            f"sys.argv = ['seed_agents.py', '--db-file', {db_file!r}, '--tenant-id', 'acme',"
            f" '--templates-dir', {str(tmp_path)!r}, '--json'];"
            f"sys.path.insert(0, {str(root / 'scripts')!r});"
            "import seed_agents;"
            "raise SystemExit(seed_agents.main())"
        )
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)

        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert [item["action"] for item in payload] == ["created"]


# ============================================================================
# Finding 5 (medium) -- child_version re-resolution was unguarded
# ============================================================================


class TestChildVersionIsActuallyResolved:
    """Hardcoding `child_version = 1` passed 1084 tests.

    Every assertion on `child_version` happened to run where the child was at v1, so
    `link["child_version"] == child["version"]` compared 1 to 1. The module docstring names
    this re-resolution as the reason bundles carry no `child_version` at all, so it is exactly
    what a reader would trust the tests to pin.
    """

    def test_a_parent_links_to_the_childs_current_version_not_version_one(self, target_db):
        import_document(target_db, _bundle(_agent_entry("child")), tenant_id="acme")
        # Push the child to v3 before the parent arrives.
        for instructions in (["v2"], ["v3"]):
            target_db.upsert_config(
                "child",
                config={"id": "child", "model": MODEL, "instructions": instructions, "schema_version": 1},
                stage="published",
                tenant_id="acme",
            )
        assert target_db.get_config("child", tenant_id="acme")["version"] == 3

        import_document(
            target_db,
            _bundle(
                {
                    "component_id": "parent",
                    "component_type": "team",
                    "name": "Parent",
                    "stage": "published",
                    "config": {"id": "parent", "model": MODEL, "members": [{"type": "agent", "agent_id": "child"}]},
                    "links": [
                        {
                            "link_kind": "member",
                            "link_key": "member_0",
                            "child_component_id": "child",
                            "position": 0,
                            "meta": {"type": "agent"},
                        }
                    ],
                }
            ),
            tenant_id="acme",
        )

        links = target_db.get_links("parent", 1, tenant_id="acme")
        assert links[0]["child_version"] == 3

    def test_an_intra_bundle_child_pins_the_version_this_run_created(self, target_db):
        """The other branch: the child does not exist yet, so its version comes from this run.

        Seeded to v2 first so a hardcoded 1 would fail here too.
        """
        import_document(target_db, _bundle(_agent_entry("solo")), tenant_id="acme")
        target_db.upsert_config(
            "solo",
            config={"id": "solo", "model": MODEL, "instructions": ["v2"], "schema_version": 1},
            stage="published",
            tenant_id="acme",
        )

        import_document(
            target_db,
            _bundle(
                {
                    "component_id": "holder",
                    "component_type": "team",
                    "name": "Holder",
                    "stage": "published",
                    "config": {"id": "holder", "model": MODEL, "members": [{"type": "agent", "agent_id": "solo"}]},
                    "links": [
                        {"link_kind": "member", "link_key": "member_0", "child_component_id": "solo", "position": 0}
                    ],
                }
            ),
            tenant_id="acme",
        )

        assert target_db.get_links("holder", 1, tenant_id="acme")[0]["child_version"] == 2


def test_the_temp_file_fixtures_are_independent(component_db, target_db):
    """Guards the fixtures the round-trip suite depends on being two different databases."""
    assert component_db.db_file != target_db.db_file
    assert os.path.exists(tempfile.gettempdir())
