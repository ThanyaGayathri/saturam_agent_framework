"""Mechanical guards on the EGPT-1.7 core/integrations split for toolkits and MCP.

Every layout check is AST-based and imports nothing under test, which matters because most
of the 130-odd toolkits raise ImportError at import time when their optional SDK is missing.
The shared machinery lives in `tests/unit/_layer_guards.py`; EGPT-1.4 (models), 1.5 (vector
dbs) and 1.6 (db backends) use the same helpers.

The non-vacuity floors below are load-bearing. Without them a stale scan root would make
these guards compare empty sets and report PASS -- which is not hypothetical: EGPT-1.4 moved
the providers and its drift guard silently degraded to a no-op.
"""

import ast
import warnings

from tests.unit._layer_guards import (
    check_shim_parity,
    check_submodule_shims,
    discover_subclasses,
    module_name,
    package_root,
)

# Floors, not exact counts, so adding a 134th toolkit does not fail these guards -- landing
# one in the wrong tree is what `test_deprecated_tools_tree_holds_only_shims` catches.
_TOOLKIT_COUNT = 130  # Toolkit subclasses under integrations/tools at the time of the move
_TARGET_PACKAGE_COUNT = 119  # 116 flat toolkits + the google, models and mcp subpackages
_SUBMODULE_SHIM_COUNT = 132  # every module reachable by dotted path before the move

# Toolkits that wrap a framework capability rather than an external system (inventory A3).
# Moving them would force integrations to depend on services, inverting the ADR's arrow, so
# they stay in core and are excluded from the "shims only" scan.
_CORE_TOOLKIT_MODULES = {
    "egpt.tools.knowledge",
    "egpt.tools.memory",
    "egpt.tools.reasoning",
    "egpt.tools.scheduler",
    "egpt.tools.studio",
    "egpt.tools.user_control_flow",
    "egpt.tools.user_feedback",
    "egpt.tools.workflow",
    "egpt.tools.workspace",
}

# `integrations/tools/google` resolves every export through __getattr__ so that touching the
# package does not force each optional Google SDK to import. Its shim mirrors that, which
# means the names in __all__ are legitimately unbound there.
_SHIM_LAZY_EXPORTS = {
    "google": {
        "AuthConfig",
        "GmailTools",
        "GoogleAuth",
        "GoogleBigQueryTools",
        "GoogleCalendarTools",
        "GoogleDriveTools",
        "GoogleMapTools",
        "GoogleSheetsTools",
        "GoogleSlidesTools",
    }
}

# The FastMCP server registrar was created in the integrations layer by EGPT-1.7 (inventory
# A12); it was never importable under egpt.tools, so it has no legacy path to shim.
_MODULES_WITHOUT_LEGACY_PATH = {("mcp", "server.py")}

# Modules outside the integrations layer that still name a concrete toolkit. Each entry is a
# deliberate, ticketed exception -- an unlisted one fails, which is the point. Everything
# else resolves through the tool interface or egpt.tools.utils.construct_toolkit.
_INTEGRATION_IMPORT_ALLOWLIST = {
    # A12: os/mcp.py owns the built-in MCP tool surface and wires the extracted registrar.
    # This is the composition root ADR 0001 rule 4 carves out.
    "egpt.os.mcp",
    # A13 closed under EGPT-1.7b: the Slack channel adapter moved to
    # integrations/channels/slack/*, where its SlackTools import is a same-layer reference.
    # See tests/unit/os/interfaces/test_channels_layout.py for the guard covering the new
    # boundary.
    # A8 closed under EGPT-1.7a: the eight context connectors moved to
    # integrations/context/*, and egpt.context.fs.provider now resolves FileTools through
    # construct_toolkit like the Slack context provider already did. See
    # tests/unit/context/test_context_layout.py for the guard covering the new boundary.
}


def _integrations_tools_root():
    root = package_root() / "egpt" / "integrations" / "tools"
    assert root.is_dir(), f"tools integrations root not found: {root}"
    return root


def _deprecated_tools_root():
    root = package_root() / "egpt" / "tools"
    assert root.is_dir(), f"deprecated tools root not found: {root}"
    return root


def _imported_modules(path):
    """Every dotted module name `path` imports, including function-local imports.

    Walks the whole AST rather than the import block: the coupling EGPT-1.7 removed had
    function-local imports elsewhere in the tree, and a guard that only reads the header
    would wave the next one through.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def _moved_package_names():
    return {p.name for p in _integrations_tools_root().iterdir() if p.is_dir() and p.name != "__pycache__"}


def test_all_toolkits_live_in_integrations():
    """Every concrete Toolkit must be discoverable under the integrations layer.

    This is the non-vacuity anchor for the guards below: if the scan root stops matching
    where toolkits actually live, they would compare empty sets and report PASS.
    """
    root = _integrations_tools_root()
    discovered = discover_subclasses(root, "Toolkit")

    assert len(discovered) >= _TOOLKIT_COUNT, (
        f"Only {len(discovered)} Toolkit subclasses discovered under {root}; expected at "
        f"least {_TOOLKIT_COUNT}. The scan root is probably stale, which makes the "
        "companion guards vacuous."
    )


def test_deprecated_tools_tree_holds_only_shims():
    """No toolkit implementation may reappear under `egpt/tools`.

    The core tree keeps the tool contract (function/toolkit/decorator/tool_registry/utils)
    and the nine framework-internal toolkits of inventory A3. Anything else defining a
    Toolkit subclass there is a new adapter that landed in the wrong layer.
    """
    discovered = discover_subclasses(_deprecated_tools_root(), "Toolkit")
    stragglers = sorted(f"{module}.{name}" for module, name in discovered if module not in _CORE_TOOLKIT_MODULES)

    assert not stragglers, (
        f"Toolkit implementations found under the deprecated egpt/tools tree: {stragglers}. "
        "Concrete toolkits belong in egpt/integrations/tools/<name>/; egpt/tools holds the "
        "interface plus the framework-internal toolkits only."
    )


def test_every_target_package_has_a_shim_package():
    """Each relocated package keeps a deprecated path.

    The set is closed -- every package under integrations/tools got there by being moved --
    so a missing shim directory is a slip rather than a newly added toolkit.
    """
    shims_root = _deprecated_tools_root()
    missing = sorted(name for name in _moved_package_names() if not (shims_root / name).is_dir())

    assert not missing, (
        f"No deprecation shim for {missing}. Every package moved by EGPT-1.7 needs one at "
        "egpt/tools/<name>/ so existing imports keep resolving."
    )


def test_every_shim_mirrors_its_target():
    """A shim must declare everything its target declares, and bind what it declares."""
    checked, problems = check_shim_parity(
        _integrations_tools_root(),
        _deprecated_tools_root(),
        lazy_exports=_SHIM_LAZY_EXPORTS,
    )

    assert not problems, "\n".join(problems)
    assert checked >= _TARGET_PACKAGE_COUNT, (
        f"Only {checked} shim/target pairs compared; expected at least "
        f"{_TARGET_PACKAGE_COUNT}. Pairs are skipped when either side is missing, so a low "
        "count means this guard is checking almost nothing."
    )


def test_every_moved_module_has_a_submodule_shim():
    """The dotted submodule paths cookbooks and tests import must keep resolving."""
    checked, problems = check_submodule_shims(
        _integrations_tools_root(),
        _deprecated_tools_root(),
        "egpt.integrations.tools",
        "egpt.tools",
        skip_modules=_MODULES_WITHOUT_LEGACY_PATH,
    )

    assert not problems, "\n".join(problems)
    assert checked >= _SUBMODULE_SHIM_COUNT, (
        f"Only {checked} submodule shims verified; expected at least {_SUBMODULE_SHIM_COUNT}."
    )


def test_nested_google_auth_modules_have_shims():
    """`check_submodule_shims` only globs one level deep.

    `tools/google/auth/` is the one package nested two levels under the tools root, and it
    is the one every Google toolkit imports -- so it is asserted explicitly rather than
    assumed covered.
    """
    target = _integrations_tools_root() / "google" / "auth"
    shim = _deprecated_tools_root() / "google" / "auth"
    expected = {p.name for p in target.glob("*.py")}
    assert expected, f"no modules found under {target}; the path is stale"

    missing = sorted(name for name in expected if not (shim / name).exists())
    assert not missing, f"No shim for egpt/tools/google/auth/{missing}."


def test_no_concrete_toolkit_imported_outside_integrations():
    """Only the integrations layer, the shim tree and the composition root may name a toolkit."""
    root = package_root() / "egpt"
    offenders = []

    for path in root.rglob("*.py"):
        relative = path.relative_to(root).parts
        if relative[0] in ("integrations", "tools"):
            continue  # the integrations layer itself, and the deprecation shims
        module = module_name(path)
        if module in _INTEGRATION_IMPORT_ALLOWLIST:
            continue
        hits = sorted(m for m in _imported_modules(path) if m.startswith("egpt.integrations.tools"))
        if hits:
            offenders.append(f"{module}: {hits}")

    assert not offenders, (
        "Concrete toolkits imported outside the integrations layer:\n"
        + "\n".join(offenders)
        + "\nResolve the toolkit through egpt.tools.utils.construct_toolkit, or record the "
        "module in the composition-root allowlist with the ticket that clears it."
    )


def test_nothing_in_the_package_imports_through_a_shim():
    """No first-party module may reach a toolkit by its deprecated path.

    Shims exist for downstream users. A shim import inside the package would emit a
    DeprecationWarning on every run, which is how the warnings stop meaning anything.
    """
    root = package_root() / "egpt"
    moved = _moved_package_names()
    offenders = []

    for path in root.rglob("*.py"):
        if path.relative_to(root).parts[0] == "tools":
            continue  # the shims necessarily mention their own deprecated path
        hits = sorted(m for m in _imported_modules(path) if m.startswith("egpt.tools.") and m.split(".")[2] in moved)
        if hits:
            offenders.append(f"{module_name(path)}: {hits}")

    assert not offenders, "Deprecated toolkit paths imported inside the package:\n" + "\n".join(offenders)


def test_toolkit_table_rows_point_at_integrations():
    """Core must not name a module outside the integrations layer.

    Split from the resolution guard below because it needs no import: it therefore holds on
    an environment with none of the optional SDKs, where the resolution half can only skip.
    """
    from egpt.registry.discovery import declarations

    # `declarations`, not `entries`: an out-of-tree toolkit registered through an
    # `egpt.tools` entry point lives in someone else's package and is under no obligation
    # to sit in this repository's integrations layer. Reading `entries` here would fail this
    # guard on any machine with a third-party plugin installed.
    rows = declarations("tool")
    assert rows, "the tool declarations are empty; this guard would check nothing."

    for name, registration in rows.items():
        assert registration.module.startswith("egpt.integrations."), (
            f"tool declaration '{name}' points at {registration.module}, which is not in the "
            "integrations layer. Core must not name a module outside it."
        )


def test_toolkit_table_rows_resolve_without_a_shim():
    """Every row of the core construction seam reaches a real Toolkit, directly.

    Rows whose optional SDK is absent are skipped rather than failed, matching
    `tests/unit/db/test_backend_resolution.py`. The caught type is ``ImportError``, not
    ``ModuleNotFoundError``: a toolkit typically catches the SDK's ModuleNotFoundError and
    re-raises a plain ImportError naming the package to install (see
    `integrations/tools/slack/slack.py`), so the narrower type would let that through as an
    error on any environment without the SDK.

    If no row resolves, the test skips instead of passing: a guard that silently checks
    nothing is worse than one that says so.
    """
    import pytest

    from egpt.registry.discovery import declarations
    from egpt.tools.toolkit import Toolkit
    from egpt.tools.utils import get_toolkit_class

    resolved_count = 0
    for name, class_name in ((key, reg.attr) for key, reg in declarations("tool").items()):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", DeprecationWarning)
                resolved = get_toolkit_class(name)
        except ImportError as e:
            continue_reason = f"optional SDK for toolkit '{name}' not installed: {e}"
            print(continue_reason)
            continue
        resolved_count += 1
        assert resolved.__name__ == class_name
        assert issubclass(resolved, Toolkit), f"{class_name} is not a Toolkit."

    if resolved_count == 0:
        pytest.skip("no toolkit row could be resolved; every optional SDK is absent")


def test_unknown_toolkit_name_is_rejected():
    """An unregistered name fails loudly rather than resolving to None."""
    import pytest

    from egpt.tools.utils import construct_toolkit

    with pytest.raises(ValueError, match="Unknown toolkit"):
        construct_toolkit("definitely-not-a-toolkit")


def test_mcp_server_registrar_lives_in_integrations():
    """The fastmcp registration primitives must not move back into the api layer."""
    server = _integrations_tools_root() / "mcp" / "server.py"
    assert server.exists(), "egpt/integrations/tools/mcp/server.py is missing (inventory A12)."

    tree = ast.parse(server.read_text(encoding="utf-8"), filename=str(server))
    defined = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert {"enabled_builtin_tags", "builtin_tool_registrar", "register_custom_tools"} <= defined

    # Integrations depend on core only: the registrar must not reach back into the OS.
    reaches_back = sorted(m for m in _imported_modules(server) if m.startswith("egpt.os"))
    assert not reaches_back, (
        f"egpt/integrations/tools/mcp/server.py imports {reaches_back}. The tag set and the "
        "user-id injector are passed in by the control plane precisely so it does not."
    )

    os_mcp = package_root() / "egpt" / "os" / "mcp.py"
    fastmcp_defs = {"_enabled_builtin_tags", "_builtin_tool_registrar", "_register_custom_tool"}
    os_tree = ast.parse(os_mcp.read_text(encoding="utf-8"), filename=str(os_mcp))
    redefined = {n.name for n in os_tree.body if isinstance(n, ast.FunctionDef)} & fastmcp_defs
    assert not redefined, f"os/mcp.py re-defines {sorted(redefined)}; import them from integrations instead."
