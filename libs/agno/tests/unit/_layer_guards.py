"""Shared AST helpers for the core/integrations extraction guards.

EGPT-1.4 moved the model providers into the integrations layer and EGPT-1.5 the vector db
backends; 1.6 through 1.8 repeat the shape for db backends, tools and knowledge readers.
Each extraction leaves deprecation shims behind and needs the same two checks -- "no
implementation left in the deprecated tree" and "every shim re-exports what its target
exports" -- so the machinery lives here once instead of being copy-pasted per layer.

EGPT-1.8 folded the last private copy (db's) back in, which is why several helpers here
carry options only one caller uses today: `discover_subclasses` takes multiple base classes
and a `packages` filter, and `registered_defining_classes` chases re-export chains to a
fixpoint. Those were the real divergences in the copy, not incidental ones -- absorbing them
is what let the copy go, and the db guard gained the `unbound` half of `check_shim_parity`
it had been missing in the process.

Everything is AST-based and imports nothing under test, which matters because most of these
modules raise ImportError at import time when their optional SDK is absent.
"""

import ast
import re
from pathlib import Path


def package_root():
    """The directory `egpt` itself lives in, so dotted module names start with `egpt.`."""
    root = Path(__file__).resolve().parents[2]
    assert (root / "egpt").is_dir(), f"package root not found: {root}"
    return root


def module_name(path):
    name = ".".join(path.relative_to(package_root()).with_suffix("").parts)
    return name[: -len(".__init__")] if name.endswith(".__init__") else name


def _parse(path):
    # encoding is explicit: several backends carry non-ASCII (em dashes) in comments, and
    # read_text() without it decodes with the platform's preferred encoding -- which makes
    # these guards raise UnicodeDecodeError on any non-UTF-8 CI runner rather than pass.
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def discover_subclasses(root, base_class, skip_modules=frozenset(), include_init=True, packages=None):
    """Statically find every concrete subclass of `base_class` under `root`.

    Returns a set of (defining_module, class_name). Bases are matched by name, since they
    are often imported under aliases that cannot be resolved without importing.

    `base_class` is one class name or an iterable of them -- db roots its scan at both
    ``BaseDb`` and ``AsyncBaseDb``, which are siblings rather than one deriving from the
    other, so a single root would miss half the backends.

    `include_init` covers `__init__.py` too: a package `__init__` normally only re-exports,
    but nothing stops someone defining a small adapter there, and a scan that skips them
    would wave it through.

    `packages` restricts the scan to those top-level directory names under `root`, for
    callers that need to ask about one slice of a tree rather than all of it.
    """
    base_classes = {base_class} if isinstance(base_class, str) else set(base_class)

    class_bases: dict = {}
    for path in root.rglob("*.py"):
        if not include_init and path.name == "__init__.py":
            continue
        if packages is not None and path.relative_to(root).parts[0] not in packages:
            continue
        module = module_name(path)
        if module in skip_modules:
            continue
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.ClassDef):
                bases = [
                    b.id if isinstance(b, ast.Name) else b.attr
                    for b in node.bases
                    if isinstance(b, (ast.Name, ast.Attribute))
                ]
                class_bases[(module, node.name)] = bases

    rooted = set(base_classes)
    changed = True
    while changed:
        changed = False
        for (_module, name), bases in class_bases.items():
            if name not in rooted and any(b in rooted for b in bases):
                rooted.add(name)
                changed = True

    return {
        (module, name) for (module, name), _bases in class_bases.items() if name not in base_classes and name in rooted
    }


def declared_exports(path):
    """The star-import name set of a module: ``__all__`` when present, else public bindings.

    Submodule names are intentionally excluded. ``from .index import HNSW`` also binds
    ``index`` on the package, but only as a side effect of that submodule importing
    successfully, which makes the set depend on which optional SDKs happen to be installed.
    """
    tree = _parse(path)

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

    return bound_names(path)


def bound_names(path):
    """Every public name a module actually binds at import time, ignoring ``__all__``.

    ``declared_exports`` trusts ``__all__``, which is the right answer for "what does a star
    import yield" but useless for catching a shim that lists names it never imports -- the
    exact EGPT-1.4 azure/langdb regression. Comparing against this instead makes the claim
    checkable.
    """
    names = set()
    for node in ast.walk(_parse(path)):
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


def star_import_targets(path):
    """Modules this file star-imports from, e.g. ``from egpt.integrations.x.y import *``."""
    return {
        node.module
        for node in ast.walk(_parse(path))
        if isinstance(node, ast.ImportFrom) and node.module and any(a.name == "*" for a in node.names)
    }


def import_sites(path):
    """Every dotted module `path` imports, as (lineno, module) pairs.

    Walks the whole AST rather than the import block: the coupling these extractions removed
    was routinely function-local -- ``db_from_dict`` imported ``PostgresDb`` inside an
    if/elif -- and a guard that only reads the header waves the next one through.

    Relative imports are skipped. ``from . import x`` names no absolute module, and a guard
    asking "does this reach into another layer" can only answer that about absolute paths.
    """
    sites = []
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            sites.append((node.lineno, node.module))
        elif isinstance(node, ast.Import):
            sites.extend((node.lineno, alias.name) for alias in node.names)
    return sites


def imported_modules(path):
    """The set of dotted modules `path` imports, without line numbers."""
    return {module for _lineno, module in import_sites(path)}


def registered_defining_classes(root, entries):
    """Resolve registry rows to the (defining_module, class_name) each one points at.

    A registry table names the re-exporting package (``egpt.integrations.db.postgres``,
    ``PostgresDb``) while the class is defined in a submodule
    (``egpt.integrations.db.postgres.postgres``). Parsing each package ``__init__`` for
    ``from <mod> import <name>`` re-exports maps the former back to the latter, so a table
    row and an on-disk scan can be compared as the same kind of thing.

    Chains are followed to a fixpoint rather than one hop: ``async_postgres`` re-exports
    through the ``postgres`` package, so a single hop lands on another re-export and the row
    would not match the definition. `entries` is an iterable of (module, class_name) pairs.
    """
    reexports: dict = {}
    for path in root.rglob("__init__.py"):
        package = module_name(path)
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                for alias in node.names:
                    reexports[(package, alias.asname or alias.name)] = (node.module, alias.name)

    resolved = set()
    for entry in entries:
        entry = tuple(entry)
        # A mutual re-export between two packages would otherwise spin here forever, turning
        # a layout mistake into a hung test run rather than a failure anyone can read.
        seen = {entry}
        while entry in reexports and reexports[entry] != entry:
            entry = reexports[entry]
            if entry in seen:
                break
            seen.add(entry)
        resolved.add(entry)
    return resolved


def find_shim_path_strings(tests_root, prefix, names, exclude=frozenset()):
    """Test lines that name a ``<prefix>.<name>`` module path inside a string literal.

    Aimed at ``mock.patch`` targets. A submodule shim is ``from <target> import *``, so
    patching a name on the shim rebinds the shim's own copy while the implementation keeps
    reading its module globals: the patch silently no-ops, and a patched network client lets
    the test make a real call instead of failing loudly about a bad target. The shim
    docstrings record this, but prose does not stop anyone re-introducing it.

    Returns a sorted list of ``relative/path.py:lineno`` strings. `exclude` holds resolved
    paths to skip -- a guard file naming these paths on purpose is not an offender.
    """
    pattern = re.compile(r"""["']%s\.(?:%s)\b""" % (re.escape(prefix), "|".join(sorted(re.escape(n) for n in names))))

    offenders = []
    for path in sorted(tests_root.rglob("test_*.py")):
        if path.resolve() in exclude:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(tests_root)}:{lineno}")
    return offenders


def check_shim_parity(targets_root, shims_root, extra_exports=None, lazy_exports=None, skip_prefix="_"):
    """Compare every shim package against its target package.

    Returns (checked, problems). A shim must (a) declare everything its target declares and
    (b) actually bind every name it declares -- (b) is what catches a shim whose ``__all__``
    survives an edit that drops the import block, which a plain ``__all__``-to-``__all__``
    comparison cannot see.

    `lazy_exports` records names a shim declares but deliberately resolves through
    ``__getattr__`` instead of binding eagerly -- because the target provides them lazily or
    behind an optional-dependency guard that must not be forced open. Each entry is a
    deliberate exception, so an unlisted unbound name still fails.
    """
    extra_exports = extra_exports or {}
    lazy_exports = lazy_exports or {}
    checked = 0
    problems = []

    for target_dir in sorted(p for p in targets_root.iterdir() if p.is_dir()):
        if target_dir.name.startswith(skip_prefix):
            continue  # shared infra, never had a public deprecated path
        target_init = target_dir / "__init__.py"
        shim_init = shims_root / target_dir.name / "__init__.py"
        if not target_init.exists() or not shim_init.exists():
            continue  # added after the extraction; needs no deprecated path

        checked += 1
        target_names = declared_exports(target_init)
        shim_names = declared_exports(shim_init)
        allowed_extra = set(extra_exports.get(target_dir.name, set()))

        missing = sorted(target_names - shim_names)
        extra = sorted(shim_names - target_names - allowed_extra)
        allowed_lazy = set(lazy_exports.get(target_dir.name, set()))
        unbound = sorted(shim_names - bound_names(shim_init) - allowed_lazy)
        if missing:
            problems.append(
                f"{target_dir.name}: shim does not re-export {missing}. Import them explicitly "
                "and list them in __all__, as the other shims do."
            )
        if extra:
            problems.append(
                f"{target_dir.name}: shim exports {extra} which the target does not. Either "
                "drop them or record the reason in the extra-exports allowlist."
            )
        if unbound:
            problems.append(
                f"{target_dir.name}: shim lists {unbound} in __all__ but never binds them. "
                "Star-imports and static type checking break on this path even though "
                "__getattr__ keeps attribute access working at runtime. If the name must "
                "stay lazy, record it in the lazy-exports allowlist with a reason."
            )

    return checked, problems


def check_submodule_shims(
    targets_root, shims_root, target_prefix, shim_prefix, skip_prefix="_", skip_modules=frozenset()
):
    """Every non-``__init__`` module under a target package needs a shim that star-imports it.

    Package-level parity says nothing about the submodule shims, yet those are the paths
    cookbooks and benchmarks import by dotted name. Returns (checked, problems).

    A package added after the extraction is skipped wholesale (it never had a deprecated
    path). `skip_modules` is the per-module counterpart -- ``(package_name, filename)``
    pairs for modules created inside an already-extracted package, which likewise have no
    legacy path to shim. Each entry is a deliberate exception, so an unlisted missing shim
    still fails.
    """
    checked = 0
    problems = []

    for target_dir in sorted(p for p in targets_root.iterdir() if p.is_dir()):
        if target_dir.name.startswith(skip_prefix):
            continue
        if not (shims_root / target_dir.name).is_dir():
            continue  # added after the extraction; needs no deprecated path

        for target_mod in sorted(target_dir.glob("*.py")):
            if target_mod.name == "__init__.py":
                continue
            if (target_dir.name, target_mod.name) in skip_modules:
                continue
            shim_mod = shims_root / target_dir.name / target_mod.name
            if not shim_mod.exists():
                problems.append(
                    f"{target_dir.name}/{target_mod.name}: no shim at {shim_mod.relative_to(shims_root.parent)}. "
                    "Every module reachable by dotted path before the move needs one."
                )
                continue

            checked += 1
            expected = module_name(target_mod)
            if expected not in star_import_targets(shim_mod):
                problems.append(
                    f"{target_dir.name}/{target_mod.name}: shim does not `from {expected} import *`. "
                    "Without it a star import of the deprecated path binds nothing."
                )

    return checked, problems
