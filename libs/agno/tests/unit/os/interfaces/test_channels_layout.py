"""Mechanical guards on the EGPT-1.7b core/integrations split for channel adapters
(aicoe-tracker#158).

Same AST-based approach as `tests/unit/context/test_context_layout.py` (EGPT-1.7a), reusing
`tests/unit/_layer_guards.py` verbatim. One structural difference from that file: EGPT-1.7a
moved whole packages, including their `__init__.py`, so the old `egpt/context/<name>/` package
became a pure shim comparable name-for-name against its target's `__init__.py`
(`check_shim_parity`). Here, `egpt/os/interfaces/<name>/__init__.py` and `<name>.py` (the
`BaseInterface` subclass) do NOT move -- only the router/handler submodules do -- so this file
only exercises `check_submodule_shims`, never a package-level parity check.

`_MOVED_CHANNEL_PACKAGES` grows across the two EGPT-1.7b PRs: WhatsApp lands first (this file's
initial state), Slack second. Every check here discovers packages/modules from what actually
exists on disk under `egpt/integrations/channels/`, so Slack's rows populate automatically once
its PR lands -- nothing here needs editing for that, only `_MOVED_CHANNEL_PACKAGES` itself.
"""

import ast
import importlib
import warnings
from pathlib import Path

import pytest

from tests.unit._layer_guards import (
    check_submodule_shims,
    find_shim_path_strings,
    imported_modules,
    module_name,
    package_root,
)

# Packages moved into egpt.integrations.channels.* under EGPT-1.7b. Closed set -- every name
# here was egpt/os/interfaces/<name>/ before its move, so a missing shim is a slip, not a
# newly added channel.
_MOVED_CHANNEL_PACKAGES = {"whatsapp"}  # "slack" joins once its EGPT-1.7b PR lands


def _integrations_channels_root():
    root = package_root() / "egpt" / "integrations" / "channels"
    assert root.is_dir(), f"channels integrations root not found: {root}"
    return root


def _os_interfaces_root():
    root = package_root() / "egpt" / "os" / "interfaces"
    assert root.is_dir(), f"os interfaces root not found: {root}"
    return root


def _moved_submodule_pairs():
    """(package, filename) for every .py module moved under egpt/integrations/channels/*."""
    root = _integrations_channels_root()
    pairs = []
    for package in sorted(_MOVED_CHANNEL_PACKAGES):
        package_dir = root / package
        if not package_dir.is_dir():
            continue
        for path in sorted(package_dir.glob("*.py")):
            if path.name == "__init__.py":
                continue
            pairs.append((package, path.name))
    return pairs


def _module_level_names(path):
    """Public names a module binds at its own top level -- unlike `_layer_guards.bound_names`,
    this deliberately ignores names imported inside a function body (e.g. `helpers.py`'s
    `download_event_media_async` does a local `from egpt.media import Audio, ...`), since
    those are never attributes of the module itself and a shim cannot be expected to re-export
    them.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            names.update(alias.asname or alias.name for alias in node.names if alias.name != "*")
        elif isinstance(node, ast.Import):
            names.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return {name for name in names if not name.startswith("_")}


def test_all_moved_channel_packages_exist_under_integrations():
    """Non-vacuity anchor: if the scan root goes stale, the guards below compare empty sets."""
    root = _integrations_channels_root()
    found = {p.name for p in root.iterdir() if p.is_dir() and p.name != "__pycache__"}
    missing = sorted(_MOVED_CHANNEL_PACKAGES - found)
    assert not missing, f"Expected channel packages not found under {root}: {missing}"


def test_every_moved_module_has_a_submodule_shim():
    """The dotted submodule paths cookbooks and tests import must keep resolving."""
    checked, problems = check_submodule_shims(
        _integrations_channels_root(),
        _os_interfaces_root(),
        "egpt.integrations.channels",
        "egpt.os.interfaces",
    )

    assert not problems, "\n".join(problems)
    expected = len(_moved_submodule_pairs())
    assert checked >= expected, f"Only {checked} submodule shims verified; expected at least {expected}."


@pytest.mark.parametrize("package,filename", _moved_submodule_pairs())
def test_eager_shim_binds_correct_objects_and_warns(package, filename):
    """Runtime counterpart to `test_every_moved_module_has_a_submodule_shim`'s static check.

    Static AST parity only confirms a shim star-imports the right target module -- it cannot
    catch a copy-paste bug that binds the right name to the wrong object. `importlib.reload`
    forces the module-level `_warnings.warn()` call to re-run; without it, `sys.modules`
    caching means only the first import anywhere in the whole test session would ever observe
    the warning.
    """
    submodule = filename[: -len(".py")]
    shim_path = f"egpt.os.interfaces.{package}.{submodule}"
    target_path = f"egpt.integrations.channels.{package}.{submodule}"

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        shim_module = importlib.import_module(shim_path)
        shim_module = importlib.reload(shim_module)

    assert any(issubclass(w.category, DeprecationWarning) for w in caught), f"reloading {shim_path} did not warn."

    target_module = importlib.import_module(target_path)
    target_file = _integrations_channels_root() / package / filename
    expected_names = _module_level_names(target_file)
    assert expected_names, f"{target_path} binds no public names -- nothing for the shim to re-export."
    for name in sorted(expected_names):
        shim_value = getattr(shim_module, name, None)
        target_value = getattr(target_module, name)
        assert shim_value is target_value, (
            f"{shim_path}.{name} resolves to a different object than {target_path}.{name}."
        )


def test_no_concrete_channel_adapter_imported_outside_integrations():
    """Only the integrations layer and the shim modules may name a moved channel-adapter module."""
    root = package_root() / "egpt"
    offenders = []

    for path in root.rglob("*.py"):
        relative = path.relative_to(root).parts
        if relative[0] == "integrations":
            continue  # the integrations layer itself
        if (
            relative[0] == "os"
            and len(relative) > 2
            and relative[1] == "interfaces"
            and relative[2] in _MOVED_CHANNEL_PACKAGES
        ):
            continue  # the deprecation shim modules, which exist to name the new location
        module = module_name(path)
        hits = sorted(m for m in imported_modules(path) if m.startswith("egpt.integrations.channels"))
        if hits:
            offenders.append(f"{module}: {hits}")

    assert not offenders, (
        "Concrete channel adapters imported outside the integrations layer:\n"
        + "\n".join(offenders)
        + "\nEither move the caller into integrations/, or route it through a core seam."
    )


def test_no_integrations_channels_module_imports_orchestration():
    """`AsyncRunnableLike` exists so this edge never needs to open.

    AST-based, so it catches module-level, function-local, bare, and aliased import forms --
    but not a dynamic `importlib.import_module("egpt.team.team")`. That's inherent to scanning
    source rather than executing it; a string-literal scan to catch it would false-positive on
    the docstrings and error messages these modules already contain.
    """
    root = _integrations_channels_root()
    offenders = []

    for path in root.rglob("*.py"):
        module = module_name(path)
        hits = sorted(
            m
            for m in imported_modules(path)
            if m == "egpt.agent"
            or m.startswith("egpt.agent.")
            or m == "egpt.team"
            or m.startswith("egpt.team.")
            or m == "egpt.workflow"
            or m.startswith("egpt.workflow.")
        )
        if hits:
            offenders.append(f"{module}: {hits}")

    assert not offenders, (
        "egpt.integrations.channels modules importing orchestration:\n"
        + "\n".join(offenders)
        + "\nType the entity as egpt.registry.protocols.AsyncRunnableLike instead."
    )


def test_no_test_patches_a_deprecated_channel_shim_path():
    """No test may aim `mock.patch` at a shimmed `egpt.os.interfaces.<package>.<module>` path.

    A submodule shim re-exports names eagerly via `import *`, so patching a name on the shim
    rebinds the shim's own copy while the implementation keeps reading its module globals. The
    patch silently no-ops -- a patched network client would let the test make a real call
    instead of failing loudly about a bad target. Scoped to the specific moved submodules
    (`whatsapp.router`, not bare `whatsapp` or `whatsapp.whatsapp`), since the package
    `__init__.py` and the `BaseInterface` subclass module are NOT shims here -- they're the
    still-legitimate api-layer wrapper. Mirrors
    `test_context_layout.py::test_no_test_patches_a_deprecated_connector_shim_path`.
    """
    tests_root = package_root() / "tests"
    assert tests_root.is_dir(), f"test root not found: {tests_root}"

    shimmed_submodules = {f"{package}.{filename[: -len('.py')]}" for package, filename in _moved_submodule_pairs()}
    scanned, offenders = find_shim_path_strings(
        tests_root,
        "egpt.os.interfaces",
        shimmed_submodules,
        exclude={Path(__file__).resolve()},  # this file names the paths on purpose
    )

    # Non-vacuity floor: an empty or stale scan root would silently report PASS.
    assert scanned >= 100, f"only {scanned} test files scanned under {tests_root}; the scan root is stale."

    assert not offenders, (
        "Tests referencing a deprecated channel-adapter shim path as a string (mock.patch "
        "targets patch the shim's binding, not the implementation):\n"
        + "\n".join(offenders)
        + "\nPoint them at egpt.integrations.channels.* instead."
    )
