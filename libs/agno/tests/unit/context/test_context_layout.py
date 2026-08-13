"""Mechanical guards on the EGPT-1.7a core/integrations split for context connectors.

Every check is AST-based and imports nothing under test, matching the pattern EGPT-1.4
through 1.8 established (`tests/unit/_layer_guards.py`, reused verbatim here). Two boundaries
are new with this ticket and have no precedent elsewhere in the codebase:

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

from tests.unit._layer_guards import (
    check_shim_parity,
    check_submodule_shims,
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
# to their new home (see its module docstring) -- the one legitimate orchestration -> integrations
# reference outside the shim packages themselves.
_CONTEXT_INTEGRATION_IMPORT_ALLOWLIST = {
    "egpt.context.wiki",
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
    import warnings

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
            if m == "egpt.agent" or m.startswith("egpt.agent.") or m == "egpt.team" or m == "egpt.workflow"
        )
        if hits:
            offenders.append(f"{module}: {hits}")

    assert not offenders, (
        "Core context modules importing orchestration:\n"
        + "\n".join(offenders)
        + "\nUse egpt.registry.protocols.RunnableLike for typing and construct_runnable to build one."
    )
