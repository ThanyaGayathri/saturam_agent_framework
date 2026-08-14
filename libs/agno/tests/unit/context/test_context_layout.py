"""Mechanical guards on the EGPT-1.7a core/integrations split for context connectors.

Most checks are AST-based and import nothing under test, matching the pattern EGPT-1.4
through 1.8 established (`tests/unit/_layer_guards.py`, reused verbatim here). The shim
identity tests below are the exception -- they import both sides of each shim/target pair to
catch a name bound to the *wrong* object, which a static `__all__` comparison cannot see.
Two boundaries are new with this ticket and have no precedent elsewhere in the codebase:

- No `egpt.context.*` module outside a shim package may name a concrete
  `egpt.integrations.context.*` module -- the mirror image of
  `tests/unit/tools/test_toolkit_layout.py`'s toolkit check, but for connectors instead of
  toolkits.
- No `egpt.integrations.context.*` module may name `egpt.agent`, `egpt.team` or
  `egpt.workflow` -- the edge the `RunnableLike` protocol + `construct_runnable` seam
  (`egpt/registry/protocols.py`, `egpt/run/utils.py`) exists to close.

`wiki/` is excluded from the whole-package shim sweep: EGPT-1.7a split `wiki/backend.py`
along its ABC boundary rather than moving the package wholesale (`GitBackend` and the two
Notion backends moved; `WikiBackend`, `FileSystemBackend` and the provider stayed), so it
does not fit the target-package-equals-shim-package shape `check_shim_parity` assumes. Its
shim is a `__getattr__` proxy for exactly those three names, covered separately below.
"""

import ast
import importlib
import warnings
from pathlib import Path

import pytest

from tests.unit._layer_guards import (
    check_shim_parity,
    check_submodule_shims,
    find_shim_path_strings,
    imported_modules,
    module_name,
    package_root,
)

# The eight connector packages EGPT-1.7a moved wholesale. Closed set -- every name here was
# `context/<name>/` before the move, so a missing shim is a slip, not a newly added connector.
_MOVED_CONNECTOR_PACKAGES = {
    "calendar",
    "database",
    "gdrive",
    "gmail",
    "google",
    "mcp",
    "slack",
    "web",
}
_SUBMODULE_SHIM_COUNT = 12  # one provider.py per package except google (utils.py) and web (5)

# egpt.context.wiki's __init__ lazily proxies GitBackend/NotionDatabaseBackend/NotionPageBackend
# to their new home (see its module docstring), and its git_ops/notion_ops are eager whole-module
# shims like the other connector packages' -- these are the legitimate orchestration ->
# integrations references outside the shim packages the _MOVED_CONNECTOR_PACKAGES skip covers.
_CONTEXT_INTEGRATION_IMPORT_ALLOWLIST = {
    "egpt.context.wiki",
    "egpt.context.wiki.git_ops",
    "egpt.context.wiki.notion_ops",
}


def _integrations_context_root():
    root = package_root() / "egpt" / "integrations" / "context"
    assert root.is_dir(), f"context integrations root not found: {root}"
    return root


def _context_root():
    root = package_root() / "egpt" / "context"
    assert root.is_dir(), f"context root not found: {root}"
    return root


def test_all_moved_connector_packages_exist_under_integrations():
    """Non-vacuity anchor: if the scan root goes stale, the guards below compare empty sets."""
    root = _integrations_context_root()
    found = {p.name for p in root.iterdir() if p.is_dir() and p.name != "__pycache__"}
    missing = sorted(_MOVED_CONNECTOR_PACKAGES - found)
    assert not missing, f"Expected connector packages not found under {root}: {missing}"


def test_every_moved_package_has_a_shim_package():
    """Each relocated package keeps a deprecated path at its EGPT-1.7a-era location."""
    shims_root = _context_root()
    missing = sorted(name for name in _MOVED_CONNECTOR_PACKAGES if not (shims_root / name / "__init__.py").exists())
    assert not missing, (
        f"No deprecation shim for {missing}. Every package moved by EGPT-1.7a needs one at "
        "egpt/context/<name>/ so existing imports keep resolving."
    )


def test_every_shim_mirrors_its_target():
    """A shim must declare everything its target declares, and bind what it declares.

    ``skip_prefix="wiki"`` excludes the one package that does not fit the whole-package
    shape this helper assumes -- see the module docstring.
    """
    checked, problems = check_shim_parity(
        _integrations_context_root(),
        _context_root(),
        skip_prefix="wiki",
    )

    assert not problems, "\n".join(problems)
    assert checked >= len(_MOVED_CONNECTOR_PACKAGES), (
        f"Only {checked} shim/target pairs compared; expected at least "
        f"{len(_MOVED_CONNECTOR_PACKAGES)}. Pairs are skipped when either side is missing, so "
        "a low count means this guard is checking almost nothing."
    )


def test_every_moved_module_has_a_submodule_shim():
    """The dotted submodule paths cookbooks and tests import must keep resolving."""
    checked, problems = check_submodule_shims(
        _integrations_context_root(),
        _context_root(),
        "egpt.integrations.context",
        "egpt.context",
        skip_prefix="wiki",
    )

    assert not problems, "\n".join(problems)
    assert checked >= _SUBMODULE_SHIM_COUNT, (
        f"Only {checked} submodule shims verified; expected at least {_SUBMODULE_SHIM_COUNT}."
    )


def test_wiki_shim_proxies_the_three_moved_backends():
    """`context/wiki/__init__.py`'s lazy shim must still resolve the moved classes."""
    import egpt.integrations.context.wiki as target

    wiki_init = _context_root() / "wiki" / "__init__.py"
    tree = ast.parse(wiki_init.read_text(encoding="utf-8"), filename=str(wiki_init))
    moved = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_MOVED" for t in node.targets):
            moved = {elt.value for elt in node.value.elts if isinstance(elt, ast.Constant)}
    assert moved, "egpt/context/wiki/__init__.py must define _MOVED with the shimmed class names."

    for name in sorted(moved):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            import egpt.context.wiki as shim

            resolved = getattr(shim, name)
        assert resolved is getattr(target, name), f"{name}: shim and target resolve to different objects."
        assert any(issubclass(w.category, DeprecationWarning) for w in caught), (
            f"accessing egpt.context.wiki.{name} did not warn."
        )


@pytest.mark.parametrize("submodule", ["git_ops", "notion_ops"])
def test_wiki_submodule_shims_exist_and_warn(submodule):
    """`git_ops.py`/`notion_ops.py` moved wholesale (unlike the three backend classes, which
    are proxied lazily via `__getattr__`) and need their own eager shim module, same as every
    other moved connector submodule. `skip_prefix="wiki"` excludes wiki from the automatic
    `check_submodule_shims` sweep (see this file's module docstring), so this is the guard
    that actually covers these two.
    """
    shim_path = _context_root() / "wiki" / f"{submodule}.py"
    assert shim_path.exists(), (
        f"No deprecation shim at {shim_path}. egpt.integrations.context.wiki.{submodule} moved "
        "wholesale in EGPT-1.7a and needs a shim at the old path so existing imports keep resolving."
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        shim_module = importlib.import_module(f"egpt.context.wiki.{submodule}")
        shim_module = importlib.reload(shim_module)

    assert any(issubclass(w.category, DeprecationWarning) for w in caught), (
        f"importing egpt.context.wiki.{submodule} did not warn."
    )


def test_no_concrete_connector_imported_outside_integrations():
    """Only the integrations layer and the shim packages may name a connector module."""
    root = package_root() / "egpt"
    offenders = []

    for path in root.rglob("*.py"):
        relative = path.relative_to(root).parts
        if relative[0] == "integrations":
            continue  # the integrations layer itself
        if relative[0] == "context" and len(relative) > 1 and relative[1] in _MOVED_CONNECTOR_PACKAGES:
            continue  # the deprecation shim packages, which exist to name the new location
        module = module_name(path)
        if module in _CONTEXT_INTEGRATION_IMPORT_ALLOWLIST:
            continue
        hits = sorted(m for m in imported_modules(path) if m.startswith("egpt.integrations.context"))
        if hits:
            offenders.append(f"{module}: {hits}")

    assert not offenders, (
        "Concrete context connectors imported outside the integrations layer:\n"
        + "\n".join(offenders)
        + "\nEither move the caller into integrations/, or route it through a core seam."
    )


def test_no_integrations_context_module_imports_orchestration():
    """`RunnableLike` + `construct_runnable` exist so this edge never needs to open."""
    root = _integrations_context_root()
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
        "egpt.integrations.context modules importing orchestration:\n"
        + "\n".join(offenders)
        + "\nBuild the sub-agent via egpt.run.utils.construct_runnable instead."
    )


def test_context_core_modules_do_not_import_orchestration():
    """The contract + local-backend modules stay core: no `egpt.agent` reference, not even
    under `TYPE_CHECKING` (the AST walk here does not distinguish it -- neither would a
    reader following the import)."""
    core_files = [
        _context_root() / "provider.py",
        _context_root() / "mode.py",
        _context_root() / "_utils.py",
        _context_root() / "backend.py",
        _context_root() / "wiki" / "backend.py",
    ]
    offenders = []
    for path in core_files:
        assert path.exists(), f"expected core context module not found: {path}"
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
        "Core context modules importing orchestration:\n"
        + "\n".join(offenders)
        + "\nUse egpt.registry.protocols.RunnableLike for typing and construct_runnable to build one."
    )


# calendar/gdrive/gmail import the Google API client at module level (via
# egpt.integrations.tools.google.*), so importing their shim without the driver installed
# raises ImportError rather than exercising the shim -- gate them like test_providers.py does.
_REQUIRES_GOOGLE_API = {"calendar", "gdrive", "gmail"}
_GOOGLE_API_REASON = "the Google providers import the Google API client at module level"


@pytest.mark.parametrize("package", sorted(_MOVED_CONNECTOR_PACKAGES))
def test_eager_shim_binds_correct_objects_and_warns(package):
    """Runtime counterpart to `test_every_shim_mirrors_its_target`'s static check.

    Static AST parity only confirms a shim *declares* and *binds* every name its target
    declares -- it cannot catch a copy-paste bug that binds the right name to the wrong
    object (e.g. one package's shim accidentally importing another package's provider class
    under its own `__all__` entry). `importlib.reload` forces the module-level
    `_warnings.warn()` call to re-run; without it, `sys.modules` caching means only the first
    import anywhere in the whole test session would ever observe the warning.
    """
    if package in _REQUIRES_GOOGLE_API:
        pytest.importorskip("googleapiclient", reason=_GOOGLE_API_REASON)

    shim_module = importlib.import_module(f"egpt.context.{package}")
    target_module = importlib.import_module(f"egpt.integrations.context.{package}")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        shim_module = importlib.reload(shim_module)

    assert any(issubclass(w.category, DeprecationWarning) for w in caught), (
        f"reloading egpt.context.{package} did not warn."
    )
    assert shim_module.__all__, f"egpt.context.{package}.__all__ is empty"
    for name in shim_module.__all__:
        shim_value = getattr(shim_module, name)
        target_value = getattr(target_module, name)
        assert shim_value is target_value, (
            f"egpt.context.{package}.{name} resolves to a different object than "
            f"egpt.integrations.context.{package}.{name}."
        )


def test_no_test_patches_a_deprecated_connector_shim_path():
    """No test may aim `mock.patch` at an `egpt.context.<moved>` path.

    A whole-package shim re-exports names eagerly, so patching a name on the shim rebinds the
    shim's own copy while the implementation keeps reading its module globals. The patch
    silently no-ops -- a patched network client would let the test make a real call instead of
    failing loudly about a bad target. `egpt/context/slack/__init__.py`'s docstring calls this
    footgun out explicitly; this guard is what actually catches it. Mirrors
    `test_knowledge_layout.py::test_no_test_patches_a_deprecated_shim_path`.
    """
    tests_root = package_root() / "tests"
    assert tests_root.is_dir(), f"test root not found: {tests_root}"
    # Non-vacuity floor: an empty or stale scan root would silently report PASS.
    assert len(list(tests_root.rglob("test_*.py"))) >= 100, (
        f"only {len(list(tests_root.rglob('test_*.py')))} test files found under {tests_root}; "
        "the scan root is stale, which makes this guard vacuous."
    )

    offenders = find_shim_path_strings(
        tests_root,
        "egpt.context",
        _MOVED_CONNECTOR_PACKAGES,
        exclude={Path(__file__).resolve()},  # this file names the paths on purpose
    )

    assert not offenders, (
        "Tests referencing a deprecated connector shim path as a string (mock.patch targets "
        "patch the shim's binding, not the implementation):\n"
        + "\n".join(offenders)
        + "\nPoint them at egpt.integrations.context.* instead."
    )
