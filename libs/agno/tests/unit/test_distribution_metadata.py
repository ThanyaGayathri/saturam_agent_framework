"""Distribution metadata checks for the core library."""

from importlib.metadata import distribution


def test_egpt_core_exposes_no_console_commands():
    scripts = {
        entry.name: entry.value for entry in distribution("egpt").entry_points if entry.group == "console_scripts"
    }

    assert scripts == {}


def test_egpt_core_does_not_depend_on_cli_distribution():
    requirements = distribution("egpt").requires or []

    assert all(not requirement.lower().startswith("egptctl") for requirement in requirements)
