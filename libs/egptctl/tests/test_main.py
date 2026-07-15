"""Root `egpt` app: branded home screen, version flag, and command routing."""

from rich.text import Text
from typer.testing import CliRunner

from egptctl import CLI_NAME, __version__
from egptctl.main import app

runner = CliRunner()


def plain_output(output: str) -> str:
    """Remove terminal styling before asserting rendered CLI text."""
    return Text.from_ansi(output).plain


def test_bare_invocation_shows_home_screen():
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    output = plain_output(result.output)
    # The banner and every command group heading are present.
    assert "The CLI for AgentOS" in output
    for heading in ("Get started", "Operate", "Tokens"):
        assert heading in output
    assert __version__ in output


def test_version_flag():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    output = plain_output(result.output)
    assert CLI_NAME + " " + __version__ in output


def test_commands_are_registered():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    output = plain_output(result.output)
    assert "Usage: " + CLI_NAME in output
    for command in ("connect", "create", "status", "tokens", "up", "down", "restart"):
        assert command in output
