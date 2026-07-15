"""Root `egpt` app: branded home screen, version flag, and command routing."""

from typer.testing import CliRunner

from egptctl import __version__
from egptctl.main import app

runner = CliRunner()


def test_bare_invocation_shows_home_screen():
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    # The banner and every command group heading are present.
    assert "The CLI for AgentOS" in result.output
    for heading in ("Get started", "Operate", "Tokens"):
        assert heading in result.output
    assert __version__ in result.output


def test_version_flag():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_commands_are_registered():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Usage: egpt" in result.output
    for command in ("connect", "create", "status", "tokens", "up", "down", "restart"):
        assert command in result.output
