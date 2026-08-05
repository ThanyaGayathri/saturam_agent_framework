"""Guards for DB-backend resolution and the EGPT-1.6 compatibility shims.

EGPT-1.6 moved the 14 backend packages from ``egpt/db/`` to ``egpt/integrations/db/`` and
left a deprecation shim at every old path. Three things can silently rot after a move like
that, and prose in a docstring cannot catch any of them:

* a backend is added to the integrations tree without a ``_DB_BACKENDS`` row, so it is
  unreachable by ``db_from_dict``;
* a shim is omitted or stops mirroring its target, so the deprecated path degrades from a
  drop-in replacement to a partially-bound module (exactly what happened to the ``azure``
  and ``langdb`` model shims in EGPT-1.4);
* core reaches back into a concrete backend, undoing the extraction.

The layout checks are AST-based and never import, so they hold in an environment with none
of the optional DB drivers installed.
"""

import ast
import importlib
import warnings
from pathlib import Path

import pytest

from egpt.db.utils import (
    ASYNC_DB_BACKEND_CLASSES,
    DB_BACKEND_CLASSES,
    db_from_dict,
)

# Backend packages that moved in EGPT-1.6. `async_postgres` is an alias package with no
# `_DB_BACKENDS` row of its own; it still needs a shim, so it is listed here.
_MOVED_PACKAGES = frozenset(DB_BACKEND_CLASSES) | {"async_postgres"}


def _package_root():
    """The directory `egpt` itself lives in, so dotted module names start with `egpt.`."""
    root = Path(__file__).resolve().parents[3]
    assert (root / "egpt").is_dir(), f"package root not found: {root}"
    return root


def _backends_root():
    """Backend implementations live in the integrations layer as of EGPT-1.6."""
    root = _package_root() / "egpt" / "integrations" / "db"
    assert root.is_dir(), f"db integrations root not found: {root}"
    return root


def _deprecated_db_root():
    """The pre-EGPT-1.6 `egpt/db` tree, which now holds the interface plus shims only."""
    return _package_root() / "egpt" / "db"


def _module_name(path):
    name = ".".join(path.relative_to(_package_root()).with_suffix("").parts)
    return name[: -len(".__init__")] if name.endswith(".__init__") else name


def _discover_db_subclasses(root, packages=None):
    """Statically find every concrete BaseDb/AsyncBaseDb subclass under ``root``.

    Parses with ``ast`` so driver-gated backends (whose modules raise ImportError without
    their optional dependency) are still discovered. Returns {(defining_module, class)}.
    """
    class_bases: dict = {}
    for path in root.rglob("*.py"):
        if path.name == "__init__.py":
            continue  # __init__ only re-exports; concrete classes live in submodules
        if packages is not None and path.relative_to(root).parts[0] not in packages:
            continue
        module = _module_name(path)
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if isinstance(node, ast.ClassDef):
                class_bases[(module, node.name)] = [
                    b.id if isinstance(b, ast.Name) else b.attr
                    for b in node.bases
                    if isinstance(b, (ast.Name, ast.Attribute))
                ]

    # Transitively mark classes whose base chain reaches either ABC, matched by class name:
    # bases are frequently imported under aliases and cannot be resolved without importing.
    rooted = {"BaseDb", "AsyncBaseDb"}
    changed = True
    while changed:
        changed = False
        for (_module, name), bases in class_bases.items():
            if name not in rooted and any(b in rooted for b in bases):
                rooted.add(name)
                changed = True

    return {
        (module, name) for (module, name) in class_bases if name not in ("BaseDb", "AsyncBaseDb") and name in rooted
    }


def _registered_defining_classes(root):
    """Resolve every _DB_BACKENDS entry to the (defining_module, class) it points at.

    The table references the re-exporting package (``egpt.integrations.db.postgres``,
    ``PostgresDb``) while the class is defined in a submodule
    (``egpt.integrations.db.postgres.postgres``). Parse each package ``__init__`` to follow
    ``from <mod> import <name>`` re-exports back to the definition.
    """
    reexports: dict = {}
    for path in root.rglob("__init__.py"):
        package = _module_name(path)
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                for alias in node.names:
                    reexports[(package, alias.asname or alias.name)] = (node.module, alias.name)

    resolved = set()
    for module, class_name in list(DB_BACKEND_CLASSES.values()) + list(ASYNC_DB_BACKEND_CLASSES.values()):
        entry = (module, class_name)
        # async_postgres re-exports through the postgres package, so follow chains to a fixpoint.
        while entry in reexports and reexports[entry] != entry:
            entry = reexports[entry]
        resolved.add(entry)
    return resolved


def test_every_db_subclass_is_registered():
    """Every concrete backend on disk must appear in the _DB_BACKENDS table.

    CI then fails on the same PR that adds a backend without a row, so the table stays the
    single source of truth without per-backend runtime wiring.
    """
    root = _backends_root()
    discovered = _discover_db_subclasses(root)
    covered = _registered_defining_classes(root)

    # Non-vacuity floor. Without it, a stale scan root yields an empty `discovered`, the
    # assertion below can never fire, and the test reports PASS while checking nothing --
    # which is how the EGPT-1.4 models guard silently broke when providers moved.
    assert len(discovered) >= 17, (
        f"Only {len(discovered)} BaseDb/AsyncBaseDb subclasses discovered under {root}; expected "
        "at least the 13 sync + 4 async backends. The scan root is probably stale, which makes "
        "this guard vacuous."
    )

    missing = sorted(f"{name} ({module})" for (module, name) in discovered - covered)
    assert not missing, (
        "Backend classes missing from _DB_BACKENDS: "
        + ", ".join(missing)
        + ". Add each to the table in egpt/db/utils.py."
    )


def test_deprecated_db_tree_holds_only_shims():
    """No backend implementation may reappear under the deprecated `egpt/db` tree.

    That tree is the interface, the domain schemas, the migrations and compatibility shims.
    A backend defined there would be invisible to test_every_db_subclass_is_registered,
    which scans the integrations layer, so it could ship unregistered.
    """
    stragglers = _discover_db_subclasses(_deprecated_db_root(), packages=_MOVED_PACKAGES)
    assert not stragglers, (
        "Backend classes found under the deprecated egpt/db tree: "
        + ", ".join(sorted(f"{name} ({module})" for (module, name) in stragglers))
        + ". Backend implementations belong under egpt/integrations/db/."
    )


# Names a shim exports that its target deliberately does not. Empty on purpose: every db
# package declares its own __all__, so the azure/langdb failure mode from EGPT-1.4 -- where
# there was no __all__ to copy -- cannot recur here. An unexplained extra fails just like an
# omission; this is a record of deliberate asymmetry, not a soft escape hatch.
_SHIM_EXTRA_EXPORTS: dict = {}


def _public_export_names(path):
    """Statically compute the star-import name set of a package ``__init__.py``.

    AST-based so it holds for driver-gated backends. A module-level ``__all__`` is
    authoritative when present; otherwise every public name bound at import time is
    collected. Submodule names are intentionally excluded: ``from .postgres import
    PostgresDb`` also binds ``postgres`` on the package, but only as a side effect of that
    submodule importing successfully, which would make the set depend on which optional
    drivers happen to be installed.
    """
    tree = ast.parse(path.read_text(), filename=str(path))

    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    if isinstance(node.value, (ast.List, ast.Tuple)):
                        return {
                            elt.value
                            for elt in node.value.elts
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                        }

    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == "typing":
                continue  # TYPE_CHECKING and friends are not part of the export surface
            names.update(alias.asname or alias.name for alias in node.names if alias.name != "*")
        elif isinstance(node, ast.Import):
            names.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return {name for name in names if not name.startswith("_")}


def test_deprecated_shims_reexport_everything_their_target_exports():
    """Each `egpt/db/<backend>` shim must export what its integrations target exports."""
    targets_root = _backends_root()
    shims_root = _deprecated_db_root()

    checked = 0
    problems = []
    for target_dir in sorted(p for p in targets_root.iterdir() if p.is_dir()):
        if target_dir.name.startswith("_") or target_dir.name == "__pycache__":
            continue
        target_init = target_dir / "__init__.py"
        shim_init = shims_root / target_dir.name / "__init__.py"
        if not target_init.exists() or not shim_init.exists():
            continue  # backends added after EGPT-1.6 need no deprecated path

        checked += 1
        target_names = _public_export_names(target_init)
        shim_names = _public_export_names(shim_init)
        allowed_extra = _SHIM_EXTRA_EXPORTS.get(target_dir.name, set())

        missing = sorted(target_names - shim_names)
        extra = sorted(shim_names - target_names - allowed_extra)
        if missing:
            problems.append(
                f"{target_dir.name}: shim does not re-export {missing}. Import them explicitly and "
                "list them in __all__, as the other shims do."
            )
        if extra:
            problems.append(
                f"{target_dir.name}: shim exports {extra} which the target does not. Either drop "
                "them or record the reason in _SHIM_EXTRA_EXPORTS."
            )

    assert checked >= 14, (
        f"Only {checked} shim/target pairs compared; expected all 14 backend packages. One of the "
        "two roots is probably stale, which makes this guard vacuous."
    )
    assert not problems, "Compatibility shims out of sync with their targets:\n" + "\n".join(problems)


def test_every_target_module_has_a_shim():
    """Every module under a moved package needs a shim at the matching deprecated path.

    Stricter than the models equivalent, which skips absent pairs so that providers added
    after the move need no deprecated path. The db backend set is closed -- all 14 packages
    moved at once -- so a missing file here is a generator slip, not a new backend.
    """
    targets_root = _backends_root()
    shims_root = _deprecated_db_root()

    checked = 0
    missing = []
    for path in sorted(targets_root.rglob("*.py")):
        rel = path.relative_to(targets_root)
        if rel.parts[0] not in _MOVED_PACKAGES:
            continue
        checked += 1
        if not (shims_root / rel).exists():
            missing.append(str(rel))

    assert checked >= 56, (
        f"Only {checked} target modules scanned; expected the full backend set. The scan root is "
        "probably stale, which makes this guard vacuous."
    )
    assert not missing, (
        "Moved modules with no compatibility shim at their old path: "
        + ", ".join(missing)
        + ". Old import paths must keep resolving until the shims are retired."
    )


@pytest.mark.parametrize("key", sorted(DB_BACKEND_CLASSES))
def test_every_registered_backend_resolves(key):
    """Each table row points at a class that actually exists, and the shim binds the same object.

    Resolution only, never construction: a backend whose inherited ``BaseDb.from_dict``
    would reject forwarded table kwargs must not produce a false failure here.
    """
    module_name, class_name = DB_BACKEND_CLASSES[key]
    try:
        module = importlib.import_module(module_name)
    except ImportError as e:
        pytest.skip(f"optional driver for '{key}' not installed: {e}")

    target_class = getattr(module, class_name, None)
    assert target_class is not None, f"{module_name} does not export {class_name}"

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        shim = importlib.import_module(module_name.replace("egpt.integrations.db.", "egpt.db.", 1))
    assert getattr(shim, class_name) is target_class, (
        f"egpt.db.{key}.{class_name} is not the same object as {module_name}.{class_name}; "
        "the shim has drifted from its target."
    )


def test_db_from_dict_does_not_touch_the_deprecated_shims():
    """Resolution must go straight to the integrations layer.

    Turning DeprecationWarning into an error proves it mechanically: before EGPT-1.6 this
    path imported `egpt.db.sqlite`, which is now a shim and would raise here.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        db = db_from_dict({"type": "sqlite", "db_file": ":memory:", "id": "guard"})

    assert db is not None
    assert type(db).__module__.startswith("egpt.integrations.db.sqlite")


def test_db_from_dict_returns_none_for_unknown_type():
    assert db_from_dict({"type": "definitely-not-a-backend"}) is None
    assert db_from_dict({}) is None


def test_default_clone_with_table_overrides_returns_none():
    """A backend that does not override the hook inherits BaseDb's None.

    Asserted against a real instance rather than a Mock, so it stays honest if the mocks in
    test_resolve_db_from_config.py ever drift from the real default.
    """
    from egpt.integrations.db.in_memory import InMemoryDb

    assert InMemoryDb().clone_with_table_overrides(session_table="x") is None


def _iter_core_db_modules():
    """Core `egpt/db` modules: everything outside the shim packages."""
    root = _deprecated_db_root()
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if rel.parts[0] in _MOVED_PACKAGES:
            continue
        yield path


# Core modules still permitted to name a concrete backend, with the reason. These are
# cast()-only imports inside the v1->v2 migration: dispatch is already duck-typed on
# type(db).__name__ and the imports are typing sugar. Deleting them risks new mypy
# attr-defined errors, so they are recorded rather than removed.
_CORE_BACKEND_IMPORT_ALLOWLIST = {"egpt/db/migrations/v1_to_v2.py"}


def test_core_db_package_imports_no_backend():
    """Core must not import a concrete backend -- at module level or inside a function.

    Walks the full AST rather than checking the import block, because the coupling this
    replaced was function-local: `db_from_dict` imported PostgresDb inside an if/elif, and
    `_clone_db_with_table_overrides` imported it inside a try.
    """
    root = _package_root()
    offenders = []
    for path in _iter_core_db_modules():
        rel = str(path.relative_to(root))
        if rel in _CORE_BACKEND_IMPORT_ALLOWLIST:
            continue
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            modules = []
            if isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
            elif isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            for module in modules:
                if module.startswith("egpt.integrations"):
                    offenders.append(f"{rel}:{node.lineno} imports {module}")
                elif module.startswith("egpt.db."):
                    tail = module[len("egpt.db.") :].split(".")[0]
                    if tail in _MOVED_PACKAGES:
                        offenders.append(f"{rel}:{node.lineno} imports {module}")

    assert not offenders, (
        "Core egpt/db modules import a concrete backend, which re-couples core to the "
        "integrations layer:\n" + "\n".join(offenders)
    )
