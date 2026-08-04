"""`egpt status` against the fake EgptOS and tmp client configs.

Focused on the EGPT-0.12 legacy-entry advisory: `egpt connect` deliberately leaves an
MCP entry written under the old "agno" key alone, so status is what tells the operator
it is still there.
"""

import json
from pathlib import Path

import pytest
from rich.text import Text
from typer.testing import CliRunner

import egptctl.commands.status as status_module
from egptctl.clients.claude_code import ClaudeCodeAdapter
from egptctl.clients.codex import CodexAdapter
from egptctl.clients.cursor import CursorAdapter
from egptctl.main import app

runner = CliRunner()

URL_ARGS = ["--url", "http://localhost:7777"]
MCP_URL = "http://localhost:7777/mcp"


def plain_output(output: str) -> str:
    """Remove terminal styling before asserting rendered CLI text."""
    return Text.from_ansi(output).plain


@pytest.fixture
def fake_clients(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """All three clients 'installed' under a tmp home, wired into the status command."""
    (tmp_path / ".claude.json").write_text("{}")
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".cursor").mkdir()

    def build(home=None, cwd=None, project=False):
        return {
            "claude-code": ClaudeCodeAdapter(home=tmp_path, cwd=tmp_path, which=lambda name: None),
            "codex": CodexAdapter(home=tmp_path),
            "cursor": CursorAdapter(home=tmp_path, cwd=tmp_path, project=project),
        }

    monkeypatch.setattr(status_module, "build_adapters", build)
    return tmp_path


def _status(args=()):
    return runner.invoke(app, ["status"] + URL_ARGS + list(args))


def _write_cursor_entry(home: Path, server_name: str, url: str = MCP_URL) -> None:
    (home / ".cursor" / "mcp.json").write_text(json.dumps({"mcpServers": {server_name: {"url": url}}}))


def _clients_by_key(payload):
    return {c["client"]: c for c in payload["clients"]}


def test_status_reports_no_legacy_entry_when_clean(fake_os, fake_clients):
    _write_cursor_entry(fake_clients, "egpt")

    result = _status(["--json"])
    assert result.exit_code == 0, result.output
    clients = _clients_by_key(json.loads(result.output))

    assert clients["cursor"]["configured"] is True
    assert all(c["legacy_entry"] is None for c in clients.values())


def test_status_flags_stale_agno_entry_in_json(fake_os, fake_clients):
    """A leftover "agno" entry is reported alongside the current one, not instead of it."""
    (fake_clients / ".cursor" / "mcp.json").write_text(
        json.dumps({"mcpServers": {"egpt": {"url": MCP_URL}, "agno": {"url": "http://old-os:7777/mcp"}}})
    )

    result = _status(["--json"])
    assert result.exit_code == 0, result.output
    cursor = _clients_by_key(json.loads(result.output))["cursor"]

    assert cursor["configured"] is True
    assert cursor["location"] is not None
    assert cursor["legacy_entry"] is not None


def test_status_advises_removal_in_human_output(fake_os, fake_clients):
    _write_cursor_entry(fake_clients, "agno", url="http://old-os:7777/mcp")

    result = _status()
    assert result.exit_code == 0, result.output
    output = plain_output(result.output)

    assert "before the rename" in output
    # The advisory has to carry the actual removal command, or it is just a complaint.
    assert "claude mcp remove --scope user agno" in output


def test_status_stays_quiet_without_a_legacy_entry(fake_os, fake_clients):
    _write_cursor_entry(fake_clients, "egpt")

    result = _status()
    assert result.exit_code == 0, result.output
    output = plain_output(result.output)

    assert "before the rename" not in output
    assert "mcp remove" not in output


def test_status_server_name_agno_is_not_reported_against_itself(fake_os, fake_clients):
    """Asking for the old name means it is the wanted entry, so there is nothing stale."""
    _write_cursor_entry(fake_clients, "agno")

    result = _status(["--json", "--server-name", "agno"])
    assert result.exit_code == 0, result.output
    cursor = _clients_by_key(json.loads(result.output))["cursor"]

    assert cursor["configured"] is True
    assert cursor["legacy_entry"] is None


def test_status_undetected_client_reports_no_legacy_entry(fake_os, tmp_path, monkeypatch):
    """An unconfigured box must not be probed for legacy entries it cannot have."""

    def build(home=None, cwd=None, project=False):
        return {"codex": CodexAdapter(home=tmp_path)}

    monkeypatch.setattr(status_module, "build_adapters", build)

    result = _status(["--json"])
    assert result.exit_code == 0, result.output
    codex = _clients_by_key(json.loads(result.output))["codex"]

    assert codex["detected"] is False
    assert codex["legacy_entry"] is None
