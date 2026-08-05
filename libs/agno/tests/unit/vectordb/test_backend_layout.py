"""Mechanical guards on the EGPT-1.5 core/integrations split for vector db backends.

Every check is AST-based and imports nothing under test, so it holds with no vector SDK
installed at all -- which matters, because all 18 backends raise ImportError at import time
when their optional dependency is missing.

Vector dbs have no VECTORDB_CLASSES table (the name -> class construction seam is EGPT-1.9),
so there is no counterpart to EGPT-1.4's `test_every_model_subclass_is_registered`. Its real
job -- proving the scan root is not stale -- is done here by explicit non-vacuity floors,
without which the guards below would pass while checking nothing. That is not hypothetical:
EGPT-1.4 moved the providers and its drift guard silently degraded to a no-op.
"""

import ast
from pathlib import Path

# The 18 backends moved to the integrations layer by EGPT-1.5. A floor rather than an exact
# count, so adding a 19th backend does not fail these guards -- landing it in the wrong tree
# is what `test_deprecated_vectordb_tree_holds_only_shims` catches.
_BACKEND_COUNT = 18

# Core vectordb modules that stay behind by design (ADR 0001: the interface plus the shared
# distance/score/search value types remain in core). Excluded from the "shims only" scan so
# that `VectorDb` itself -- and any intermediate ABC added beside it later -- is not read as a
# backend implementation left in the deprecated tree.
_CORE_VECTORDB_MODULES = {
    "egpt.vectordb.base",
    "egpt.vectordb.distance",
    "egpt.vectordb.score",
    "egpt.vectordb.search",
}

# Names a shim exports that its target deliberately does not. Each entry needs a reason: the
# default expectation is that a shim mirrors its target exactly. Empty for vectordb -- unlike
# EGPT-1.4's openai/OpenAILike cycle, no backend subclasses or imports another, so no target
# has to withhold a name the shim must still expose.
_SHIM_EXTRA_EXPORTS: dict = {}


def _package_root():
    """The directory `egpt` itself lives in, so dotted module names start with `egpt.`."""
    root = Path(__file__).resolve().parents[3]
    assert (root / "egpt").is_dir(), f"package root not found: {root}"
    return root


def _vectordb_root():
    """Backend implementations live in the integrations layer as of EGPT-1.5.

    Anchored on `_package_root()` rather than a `parents[N]` walk, for the reason EGPT-1.4
    learned the hard way: deriving the dotted prefix from the scan root's own depth makes a
    relocated tree yield an empty scan and a vacuously passing guard.
    """
    root = _package_root() / "egpt" / "integrations" / "vectordb"
    assert root.is_dir(), f"vectordb integrations root not found: {root}"
    return root


def _deprecated_vectordb_root():
    """The pre-EGPT-1.5 `egpt/vectordb` tree, which now holds core modules and shims only."""
    return _package_root() / "egpt" / "vectordb"


def _module_name(path):
    name = ".".join(path.relative_to(_package_root()).with_suffix("").parts)
    return name[: -len(".__init__")] if name.endswith(".__init__") else name


def _discover_vectordb_subclasses(root, skip_modules=frozenset()):
    """Statically find every concrete VectorDb subclass under `root`, without importing.

    Parses the source with ``ast`` so SDK-gated backends are still discovered. Returns a set
    of (defining_module, class_name).
    """
    # (defining_module, class_name) -> [base names]
    class_bases: dict = {}
    for path in root.rglob("*.py"):
        if path.name == "__init__.py":
            continue  # __init__ only re-exports; concrete classes live in submodules
        module = _module_name(path)
        if module in skip_modules:
            continue
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if isinstance(node, ast.ClassDef):
                bases = [
                    b.id if isinstance(b, ast.Name) else b.attr
                    for b in node.bases
                    if isinstance(b, (ast.Name, ast.Attribute))
                ]
                class_bases[(module, node.name)] = bases

    # Transitively mark every class whose base chain reaches VectorDb, matched by class name:
    # bases are often imported under aliases and cannot be resolved without importing.
    rooted = {"VectorDb"}
    changed = True
    while changed:
        changed = False
        for (_module, name), bases in class_bases.items():
            if name not in rooted and any(b in rooted for b in bases):
                rooted.add(name)
                changed = True

    return {(module, name) for (module, name), _bases in class_bases.items() if name != "VectorDb" and name in rooted}


def _public_export_names(path):
    """Statically compute the star-import name set of a package ``__init__.py``.

    A module-level ``__all__`` is authoritative when present; otherwise every public name
    bound at import time is collected.

    Submodule names are intentionally not included. ``from .index import HNSW`` also binds
    ``index`` on the package, but only as a side effect of that submodule importing
    successfully, which makes the set depend on which optional SDKs happen to be installed.
    Submodules stay reachable by their dotted path either way.
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


def test_all_vectordb_backends_live_in_integrations():
    """Every concrete backend must be discoverable under the integrations layer.

    This is the non-vacuity anchor for the two guards below: if the scan root ever stops
    matching where backends actually live, both would compare empty sets and report PASS.
    """
    root = _vectordb_root()
    discovered = _discover_vectordb_subclasses(root)

    assert len(discovered) >= _BACKEND_COUNT, (
        f"Only {len(discovered)} VectorDb subclasses discovered under {root}; expected at "
        f"least {_BACKEND_COUNT}. The scan root is probably stale, which makes the "
        "companion guards vacuous."
    )


def test_deprecated_vectordb_tree_holds_only_shims():
    """No backend implementation may be added back under the deprecated `egpt/vectordb` tree.

    That tree is core interface modules plus compatibility shims (EGPT-1.5). A VectorDb
    subclass defined there would be invisible to the relocated scan above and would put an
    external-system adapter back in core, which ADR 0001 forbids.
    """
    stragglers = _discover_vectordb_subclasses(_deprecated_vectordb_root(), skip_modules=_CORE_VECTORDB_MODULES)

    assert not stragglers, (
        "VectorDb subclasses found under the deprecated egpt/vectordb tree: "
        + ", ".join(sorted(f"{name} ({module})" for (module, name) in stragglers))
        + ". Backend implementations belong under egpt/integrations/vectordb/."
    )


def test_deprecated_shims_reexport_everything_their_target_exports():
    """Each `egpt/vectordb/<backend>` shim must export what its integrations target exports.

    EGPT-1.4 shipped two provider shims (azure, langdb) that bound zero names while their
    docstrings claimed explicit re-exports -- prose is not checkable, so the claim and the
    code drifted apart and star-imports, `dir()` and static typing silently broke on a path
    advertised as a drop-in replacement.

    Two vectordb names make this more than a typo check: `mongodb.MongoVectorDb` and
    `redis.RedisVectorDb` are module-level aliases assigned in `__init__.py`, defined by no
    submodule at all, so any rewrite that follows import statements alone drops them.
    """
    targets_root = _vectordb_root()
    shims_root = _deprecated_vectordb_root()

    checked = 0
    problems = []
    for target_dir in sorted(p for p in targets_root.iterdir() if p.is_dir()):
        if target_dir.name.startswith("_"):
            continue  # shared infra, never had a public egpt.vectordb path
        target_init = target_dir / "__init__.py"
        shim_init = shims_root / target_dir.name / "__init__.py"
        if not target_init.exists() or not shim_init.exists():
            continue  # backends added after EGPT-1.5 need no deprecated path

        checked += 1
        target_names = _public_export_names(target_init)
        shim_names = _public_export_names(shim_init)
        allowed_extra = _SHIM_EXTRA_EXPORTS.get(target_dir.name, set())

        missing = sorted(target_names - shim_names)
        extra = sorted(shim_names - target_names - allowed_extra)
        if missing:
            problems.append(
                f"{target_dir.name}: shim does not re-export {missing}. Import them explicitly "
                "and list them in __all__, as the other shims do."
            )
        if extra:
            problems.append(
                f"{target_dir.name}: shim exports {extra} which the target does not. Either "
                "drop them or record the reason in _SHIM_EXTRA_EXPORTS."
            )

    assert checked >= _BACKEND_COUNT, (
        f"Only {checked} shim/target pairs compared; expected at least {_BACKEND_COUNT}. One "
        "of the two roots is probably stale, which makes this guard vacuous."
    )
    assert not problems, "Compatibility shims out of sync with their targets:\n" + "\n".join(problems)
