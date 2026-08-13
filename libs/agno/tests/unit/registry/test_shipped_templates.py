"""The starter templates stay loadable as the schema moves (EGPT-2.8).

These four files are the first thing a new deployment runs, so a template that stops validating
is a broken onboarding path rather than a broken example. Everything here is parametrized over
``glob("*.json")`` so a template added later is covered the moment it lands, without anyone
remembering to extend a list.

The validation case runs the **full** EGPT-2.2 gate, dry-run hydration included, and it does so
offline: ``_dry_run`` strips the ``db`` block before building anything, and providers resolve
through the discovery table rather than by importing an SDK.
"""

from pathlib import Path

import pytest

from egpt.components.io import default_templates_dir
from egpt.components.spec import validate_component_config
from egpt.registry.io import EXPORT_FORMAT, EXPORT_FORMAT_VERSION, parse_document, redact_config
from egpt.registry.schema import COMPONENT_TYPES, SCHEMA_VERSION

pytest.importorskip("openai", reason="the shipped templates name an OpenAI model")

TEMPLATE_DIR = default_templates_dir()
TEMPLATE_PATHS = sorted(TEMPLATE_DIR.glob("*.json"))

# Keys `to_dict` can emit that describe one run rather than one definition. An ignore list in
# the comparison logic would hide them; a guard here makes shipping one loud.
_PER_RUN_KEYS = {"session_id", "user_id", "session_state"}


def _ids(path: Path) -> str:
    """pytest calls an `ids` callable once per parameter value, not once with the list."""
    return path.name


def test_templates_are_actually_shipped():
    """Guards the pyproject `package-data` entry as much as the files themselves.

    Without `egpt = [..., "registry/templates/*.json"]` the directory is silently absent from a
    built wheel, and `pip install egpt` gives an operator a seed command with nothing to seed.
    """
    assert TEMPLATE_DIR.is_dir(), f"templates directory missing: {TEMPLATE_DIR}"
    assert TEMPLATE_PATHS, "no starter templates found"


@pytest.mark.parametrize("path", TEMPLATE_PATHS, ids=_ids)
class TestEveryTemplate:
    def test_parses_as_a_bundle(self, path: Path):
        document = parse_document(path.read_text(encoding="utf-8"))

        assert document["format"] == EXPORT_FORMAT
        assert document["format_version"] == EXPORT_FORMAT_VERSION
        assert document["schema_version"] == SCHEMA_VERSION

    def test_every_config_passes_the_full_validator(self, path: Path):
        document = parse_document(path.read_text(encoding="utf-8"))

        for entry in document["components"]:
            assert entry["component_type"] in COMPONENT_TYPES
            validated = validate_component_config(entry["config"], entry["component_type"])
            assert validated["schema_version"] == SCHEMA_VERSION

    def test_carries_no_secrets(self, path: Path):
        """A shipped template must already be clean, not merely cleanable."""
        document = parse_document(path.read_text(encoding="utf-8"))

        for entry in document["components"]:
            _config, redacted = redact_config(entry["config"])
            assert redacted == [], f"{path.name}: {entry['component_id']} carries {redacted}"

    def test_carries_no_per_run_state(self, path: Path):
        document = parse_document(path.read_text(encoding="utf-8"))

        for entry in document["components"]:
            leaked = _PER_RUN_KEYS & set(entry["config"])
            assert not leaked, f"{path.name}: {entry['component_id']} carries per-run state {leaked}"

    def test_is_dependency_closed(self, path: Path):
        """Every link points inside the bundle, so seeding an empty database works."""
        document = parse_document(path.read_text(encoding="utf-8"))
        present = {entry["component_id"] for entry in document["components"]}

        for entry in document["components"]:
            for link in entry.get("links") or []:
                assert link["child_component_id"] in present, (
                    f"{path.name}: {entry['component_id']} links to "
                    f"{link['child_component_id']}, which the bundle does not carry"
                )

    def test_declares_no_database_it_cannot_name(self, path: Path):
        """A template names a db by id or not at all -- never by connection string."""
        document = parse_document(path.read_text(encoding="utf-8"))

        for entry in document["components"]:
            db_block = entry["config"].get("db")
            if db_block is not None:
                assert db_block.get("id"), f"{path.name}: {entry['component_id']} has a db block with no id"


def test_components_shared_between_templates_agree():
    """Two bundles may re-declare the same component; they must not disagree about it.

    `team.support.json` and `workflow.triage.json` both carry the agents they need, which is
    what makes each independently seedable. The cost is two copies, and this closes it: seeding
    both files must converge on "unchanged", which it only does while the configs are identical.
    """
    by_id: dict = {}
    for path in TEMPLATE_PATHS:
        for entry in parse_document(path.read_text(encoding="utf-8"))["components"]:
            by_id.setdefault(entry["component_id"], []).append((path.name, entry["config"]))

    for component_id, occurrences in by_id.items():
        first_name, first_config = occurrences[0]
        for other_name, other_config in occurrences[1:]:
            assert other_config == first_config, (
                f"'{component_id}' differs between {first_name} and {other_name}; "
                "seeding both would append a version on every run"
            )


def test_a_template_naming_an_unknown_database_is_rejected(component_db):
    """The templates name a database by id, and that id has to exist in the target.

    `agent.researcher.json` says `db: {"id": "os-db"}` -- the id `scripts/seed_agents.py`
    registers by default. Seeding into a deployment whose database is called something else is
    an error, not a silent drop, and `--db-id` is the answer. This is the same field-level
    rejection an API write gets, which is the point of binding the real validator.
    """
    from egpt.components.io import seed_from_directory
    from egpt.registry import Registry
    from egpt.registry.schema import ConfigValidationError

    registry = Registry()
    registry.add_db(component_db)  # registered as "source-db", not "os-db"

    with pytest.raises(ConfigValidationError) as excinfo:
        seed_from_directory(component_db, tenant_id="acme", registry=registry)

    assert any(error.path == "db.id" for error in excinfo.value.errors)


def test_the_whole_set_seeds_into_an_empty_database(component_db):
    """The onboarding path itself, end to end."""
    from egpt.components.io import seed_from_directory
    from egpt.registry import Registry

    registry = Registry()
    registry.add_db(component_db)

    results = seed_from_directory(component_db, tenant_id="acme", registry=registry, db_id=component_db.id)

    assert {r.action for r in results} <= {"created", "unchanged"}
    created = {r.component_id for r in results if r.action == "created"}
    assert created == {"support-agent", "research-agent", "support-team", "triage-workflow"}


def test_a_seeded_team_and_workflow_hydrate(component_db):
    from egpt.components.io import seed_from_directory
    from egpt.registry import Registry
    from egpt.team import get_team_by_id
    from egpt.workflow.workflow import get_workflow_by_id

    registry = Registry()
    registry.add_db(component_db)
    seed_from_directory(component_db, tenant_id="acme", registry=registry, db_id=component_db.id)

    team = get_team_by_id(component_db, "support-team", registry=registry, tenant_id="acme")
    workflow = get_workflow_by_id(component_db, "triage-workflow", registry=registry, tenant_id="acme")

    assert [member.id for member in team.members] == ["support-agent", "research-agent"]
    assert [step.agent.id for step in workflow.steps] == ["research-agent", "support-agent"]
