"""Distribution metadata checks for the infrastructure library."""

from importlib.metadata import distribution


def test_egpt_infra_exposes_no_console_commands():
    scripts = {
        entry.name: entry.value for entry in distribution("egpt-infra").entry_points if entry.group == "console_scripts"
    }

    assert scripts == {}
