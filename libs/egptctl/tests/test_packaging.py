"""Distribution metadata checks for the branded CLI."""

from importlib.metadata import distribution


def test_egptctl_exposes_only_branded_command():
    scripts = {
        entry.name: entry.value for entry in distribution("egptctl").entry_points if entry.group == "console_scripts"
    }

    assert scripts == {"egpt": "egptctl.main:app"}
