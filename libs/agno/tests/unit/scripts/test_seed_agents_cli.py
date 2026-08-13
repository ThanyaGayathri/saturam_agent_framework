"""Tests for scripts/seed_agents.py (the EGPT-2.8 seeding CLI, aicoe-tracker#60).

The script is the onboarding entry point, so the things pinned here are the ones an operator
would hit on a bad day rather than the happy path the library tests already cover: that
``--tenant-id`` cannot be forgotten, that ``--dry-run`` really writes nothing, that a refusal
exits non-zero instead of looking like success, and that the exit codes distinguish "your
input was wrong" from "the work failed".

The `sys.path` dance matches `test_brand_audit.py`: `scripts/` is not a package.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT / "scripts"))

import seed_agents  # noqa: E402

AGENT_TEMPLATE = {
    "format": "egpt.component-bundle",
    "format_version": 1,
    "schema_version": 1,
    "components": [
        {
            "component_id": "faq-agent",
            "component_type": "agent",
            "name": "FAQ Agent",
            "stage": "published",
            "config": {
                "id": "faq-agent",
                "name": "FAQ Agent",
                "model": {"id": "gpt-5.5", "name": "OpenAIResponses", "provider": "OpenAI"},
            },
            "links": [],
        }
    ],
}


@pytest.fixture
def templates(tmp_path):
    directory = tmp_path / "templates"
    directory.mkdir()
    (directory / "agent.faq.json").write_text(json.dumps(AGENT_TEMPLATE), encoding="utf-8")
    return directory


@pytest.fixture
def db_file(tmp_path):
    return str(tmp_path / "seed.db")


def _run(db_file, templates, *extra):
    return seed_agents.main(["--db-file", db_file, "--tenant-id", "acme", "--templates-dir", str(templates), *extra])


def _open_db(db_file):
    from egpt.integrations.db.sqlite.sqlite import SqliteDb

    return SqliteDb(db_file=db_file, id="os-db")


class TestArgumentSurface:
    def test_a_database_is_required(self):
        with pytest.raises(SystemExit) as excinfo:
            seed_agents.main(["--tenant-id", "acme"])

        assert excinfo.value.code == 2

    def test_the_two_database_flags_are_mutually_exclusive(self):
        with pytest.raises(SystemExit) as excinfo:
            seed_agents.main(["--db-file", "a.db", "--db-url", "sqlite:///b.db", "--tenant-id", "acme"])

        assert excinfo.value.code == 2

    def test_the_tenant_is_required(self, db_file):
        """Not defaulted on purpose.

        `tenant_id=None` means "every tenant" everywhere in the db layer, so a default would
        quietly write untenanted rows that only an untenanted reader can ever see.
        """
        with pytest.raises(SystemExit) as excinfo:
            seed_agents.main(["--db-file", db_file])

        assert excinfo.value.code == 2

    def test_an_unknown_conflict_policy_is_rejected_by_argparse(self, db_file):
        with pytest.raises(SystemExit) as excinfo:
            seed_agents.main(["--db-file", db_file, "--tenant-id", "acme", "--on-conflict", "clobber"])

        assert excinfo.value.code == 2


class TestBackendSelection:
    def test_a_db_file_builds_sqlite(self, db_file):
        args = seed_agents.build_parser().parse_args(["--db-file", db_file, "--tenant-id", "acme"])

        assert type(seed_agents.build_db(args)).__name__ == "SqliteDb"

    def test_a_sqlite_url_builds_sqlite(self, tmp_path):
        args = seed_agents.build_parser().parse_args(["--db-url", f"sqlite:///{tmp_path}/x.db", "--tenant-id", "acme"])

        assert type(seed_agents.build_db(args)).__name__ == "SqliteDb"

    def test_the_db_id_is_configurable_because_templates_name_one(self, db_file):
        args = seed_agents.build_parser().parse_args(
            ["--db-file", db_file, "--tenant-id", "acme", "--db-id", "primary"]
        )

        assert seed_agents.build_db(args).id == "primary"


class TestSeeding:
    def test_a_successful_run_exits_zero_and_creates_the_component(self, db_file, templates, capsys):
        code = _run(db_file, templates)

        assert code == 0
        assert "created" in capsys.readouterr().out
        assert _open_db(db_file).get_component("faq-agent", tenant_id="acme") is not None

    def test_a_second_run_reports_unchanged(self, db_file, templates, capsys):
        _run(db_file, templates)
        capsys.readouterr()

        code = _run(db_file, templates)

        assert code == 0
        assert "unchanged" in capsys.readouterr().out

    def test_dry_run_writes_nothing(self, db_file, templates, capsys):
        code = _run(db_file, templates, "--dry-run")

        assert code == 0
        assert "dry run" in capsys.readouterr().out
        assert _open_db(db_file).get_component("faq-agent", tenant_id="acme") is None

    def test_json_output_carries_the_result_fields(self, db_file, templates, capsys):
        _run(db_file, templates, "--json")

        payload = json.loads(capsys.readouterr().out)
        assert payload == [
            {
                "component_id": "faq-agent",
                "component_type": "agent",
                "action": "created",
                "version": 1,
                "reason": None,
            }
        ]

    def test_json_stdout_is_pipeable_in_a_real_process(self, db_file, templates):
        """A subprocess, because in-process this cannot fail.

        The framework logs per-file progress to stdout at INFO and configures that logger when
        it is imported. Quieting it before the import looks correct and does nothing -- but a
        test whose own process already imported `egpt` passes anyway. Only a fresh interpreter
        reproduces what an operator running `... --json | jq` sees.
        """
        import subprocess

        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "seed_agents.py"),
                "--db-file",
                db_file,
                "--tenant-id",
                "acme",
                "--templates-dir",
                str(templates),
                "--json",
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert [item["action"] for item in payload] == ["created"]

    def test_an_id_prefix_reaches_the_database(self, db_file, templates):
        _run(db_file, templates, "--id-prefix", "globex-")

        assert _open_db(db_file).get_component("globex-faq-agent", tenant_id="acme") is not None


class TestFailuresAreNotSilent:
    def test_a_cross_tenant_collision_exits_one(self, db_file, templates, capsys):
        _run(db_file, templates)
        capsys.readouterr()

        code = seed_agents.main(["--db-file", db_file, "--tenant-id", "globex", "--templates-dir", str(templates)])

        assert code == 1
        assert "already exists" in capsys.readouterr().err

    def test_an_invalid_definition_exits_one_and_names_the_field(self, db_file, tmp_path, capsys):
        directory = tmp_path / "broken"
        directory.mkdir()
        broken = json.loads(json.dumps(AGENT_TEMPLATE))
        broken["components"][0]["config"]["model"] = {"id": "x", "provider": "no-such-provider"}
        (directory / "broken.json").write_text(json.dumps(broken), encoding="utf-8")

        code = _run(db_file, directory)

        assert code == 1
        assert "model.provider" in capsys.readouterr().err

    def test_an_empty_templates_directory_exits_one(self, db_file, tmp_path, capsys):
        empty = tmp_path / "empty"
        empty.mkdir()

        code = _run(db_file, empty)

        assert code == 1
        assert "No template bundles" in capsys.readouterr().err


class TestReportFormatting:
    def test_a_skip_reason_is_shown_next_to_the_action(self):
        from egpt.registry.io import ImportResult

        lines = seed_agents.render([ImportResult("a", "agent", "skipped", None, "component_id already exists")])

        assert lines[0].startswith("skipped")
        assert lines[0].endswith("component_id already exists")

    def test_the_summary_counts_every_action(self):
        from egpt.registry.io import ImportResult

        summary = seed_agents.summarize(
            [
                ImportResult("a", "agent", "created", 1),
                ImportResult("b", "agent", "unchanged", 1),
                ImportResult("c", "team", "new_version", 2),
            ]
        )

        assert "1 created" in summary
        assert "1 new version" in summary
        assert "1 unchanged" in summary
