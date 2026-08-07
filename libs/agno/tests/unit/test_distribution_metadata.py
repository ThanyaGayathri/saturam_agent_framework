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


def test_egpt_declares_no_integration_entry_points_of_its_own():
    """In-tree integrations are declarations, not entry points.

    The two mechanisms look interchangeable but are not. Entry points require an installed
    ``.dist-info``, so declaring the 58 providers that way would break ``python -c "import
    egpt"`` from an uninstalled source checkout, and would put the rows somewhere the
    AST-based layer-guards cannot read them.

    Discovery still scans these groups -- that is how a third party adds a provider (AC4).
    This asserts only that *this* distribution does not use them for its own integrations.
    """
    from egpt.registry.discovery import KINDS, entry_point_group

    groups = {entry_point_group(kind) for kind in KINDS}
    declared = sorted(
        f"{entry.group}:{entry.name}" for entry in distribution("egpt").entry_points if entry.group in groups
    )

    assert declared == [], (
        f"egpt declares its own integration entry points: {declared}. Built-in integrations "
        "belong in egpt/registry/_declarations/, which needs no installed metadata."
    )
