"""Mechanical guards on the EGPT-1.5 core/integrations split for vector db backends.

Every check is AST-based and imports nothing under test, so it holds with no vector SDK
installed at all -- which matters, because all 18 backends raise ImportError at import time
when their optional dependency is missing. The shared machinery lives in
`tests/unit/_layer_guards.py`; EGPT-1.4 uses the same helpers for the model providers.

EGPT-1.9 gave vector dbs the name -> class construction seam they had been missing, so
`test_every_vectordb_subclass_is_registered` below is now the direct counterpart to
EGPT-1.4's model guard. The explicit non-vacuity floors stay: they are what proves the scan
root is not stale, without which these guards would pass while checking nothing. That is
not hypothetical -- EGPT-1.4 moved the providers and its drift guard silently degraded to a
no-op.
"""

from egpt.registry.discovery import declarations
from tests.unit._layer_guards import (
    check_shim_parity,
    check_submodule_shims,
    discover_subclasses,
    package_root,
    registered_defining_classes,
)

# `declarations`, not `entries`: a third-party backend registered through an
# `egpt.vectordbs` entry point lives outside this repository, so it must not be measured
# against the integrations tree on disk.
VECTORDB_DECLARATIONS = declarations("vectordb")

# The 18 backends moved to the integrations layer by EGPT-1.5. A floor rather than an exact
# count, so adding a 19th backend does not fail these guards -- landing it in the wrong tree
# is what `test_deprecated_vectordb_tree_holds_only_shims` catches.
_BACKEND_COUNT = 18

# Every module reachable by dotted path before the move: the 18 implementation modules plus
# the index/mixin helpers. Each needs a submodule shim.
_SUBMODULE_SHIM_COUNT = 24

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


def _vectordb_root():
    """Backend implementations live in the integrations layer as of EGPT-1.5."""
    root = package_root() / "egpt" / "integrations" / "vectordb"
    assert root.is_dir(), f"vectordb integrations root not found: {root}"
    return root


def _deprecated_vectordb_root():
    """The pre-EGPT-1.5 `egpt/vectordb` tree, which now holds core modules and shims only."""
    root = package_root() / "egpt" / "vectordb"
    assert root.is_dir(), f"deprecated vectordb root not found: {root}"
    return root


def test_all_vectordb_backends_live_in_integrations():
    """Every concrete backend must be discoverable under the integrations layer.

    This is the non-vacuity anchor for the guards below: if the scan root ever stops matching
    where backends actually live, they would compare empty sets and report PASS.
    """
    root = _vectordb_root()
    discovered = discover_subclasses(root, "VectorDb")

    assert len(discovered) >= _BACKEND_COUNT, (
        f"Only {len(discovered)} VectorDb subclasses discovered under {root}; expected at "
        f"least {_BACKEND_COUNT}. The scan root is probably stale, which makes the "
        "companion guards vacuous."
    )


def test_every_vectordb_subclass_is_registered():
    """Every concrete backend on disk must have a declaration row.

    The guard this file's docstring said was missing until EGPT-1.9 gave vector dbs a
    construction seam. Without it, a backend added to the integrations tree is resolvable
    only by the user importing its class directly -- so DB-defined-agent hydration, which
    can only name a backend by string, silently cannot reach it.

    `__init__.py` is excluded from the scan: a package re-export is not a second definition,
    and counting it as one would demand a row per import path.
    """
    root = _vectordb_root()
    discovered = discover_subclasses(root, "VectorDb", include_init=False)
    covered = registered_defining_classes(root, ((reg.module, reg.attr) for reg in VECTORDB_DECLARATIONS.values()))

    assert len(discovered) >= _BACKEND_COUNT, (
        f"Only {len(discovered)} VectorDb subclasses discovered under {root}; expected at "
        f"least {_BACKEND_COUNT}. The scan root is probably stale, which makes this guard vacuous."
    )

    missing = sorted(f"{name} ({module})" for (module, name) in discovered - covered)
    assert not missing, (
        "VectorDb subclasses with no declaration row: "
        + ", ".join(missing)
        + ". Add each to egpt/registry/_declarations/vectordbs.py."
    )


def test_vectordb_declaration_rows_point_at_integrations():
    """Core must not name a module outside the integrations layer.

    Separate from the drift guard above, which compares sets and so cannot notice a row
    pointing somewhere wrong -- an extra or misdirected row only grows the `covered` side.
    Matches the equivalent guards for tools and embedders.
    """
    assert VECTORDB_DECLARATIONS, "the vectordb declarations are empty; this guard would check nothing."

    for name, registration in VECTORDB_DECLARATIONS.items():
        assert registration.module.startswith("egpt.integrations.vectordb."), (
            f"vectordb declaration '{name}' points at {registration.module}, which is not in the "
            "integrations layer. Core must not name a module outside it."
        )


def test_deprecated_vectordb_tree_holds_only_shims():
    """No backend implementation may be added back under the deprecated `egpt/vectordb` tree.

    That tree is core interface modules plus compatibility shims (EGPT-1.5). A VectorDb
    subclass defined there would be invisible to the relocated scan above and would put an
    external-system adapter back in core, which ADR 0001 forbids.

    `__init__.py` files are scanned too. They normally only re-export, but nothing stops a
    small adapter being defined directly in one, and skipping them would wave that through.
    """
    shims_root = _deprecated_vectordb_root()
    stragglers = discover_subclasses(shims_root, "VectorDb", skip_modules=_CORE_VECTORDB_MODULES)

    assert not stragglers, (
        "VectorDb subclasses found under the deprecated egpt/vectordb tree: "
        + ", ".join(sorted(f"{name} ({module})" for (module, name) in stragglers))
        + ". Backend implementations belong under egpt/integrations/vectordb/."
    )

    # Without this the guard is vacuous if the tree is ever renamed or emptied: rglob would
    # return nothing, stragglers would be empty, and the assertion above could never fire.
    shim_packages = [p for p in shims_root.iterdir() if p.is_dir() and (p / "__init__.py").exists()]
    assert len(shim_packages) >= _BACKEND_COUNT, (
        f"Only {len(shim_packages)} shim packages found under {shims_root}; expected at least "
        f"{_BACKEND_COUNT}. The deprecated tree is stale, which makes this guard vacuous."
    )


def test_deprecated_shims_reexport_everything_their_target_exports():
    """Each `egpt/vectordb/<backend>` shim must export what its integrations target exports.

    EGPT-1.4 shipped two provider shims (azure, langdb) that bound zero names while their
    docstrings claimed explicit re-exports -- prose is not checkable, so the claim and the
    code drifted apart and star-imports, `dir()` and static typing silently broke on a path
    advertised as a drop-in replacement.

    The check is two-sided on purpose: a shim must declare what its target declares *and*
    actually bind everything it declares. Comparing `__all__` to `__all__` alone would miss
    the azure case entirely, since a stale `__all__` matches its target perfectly while the
    module binds nothing.

    Two vectordb names make this more than a typo check: `mongodb.MongoVectorDb` and
    `redis.RedisVectorDb` are module-level aliases assigned in `__init__.py`, defined by no
    submodule, so any rewrite that follows import statements alone drops them.
    """
    checked, problems = check_shim_parity(
        _vectordb_root(), _deprecated_vectordb_root(), extra_exports=_SHIM_EXTRA_EXPORTS
    )

    assert checked >= _BACKEND_COUNT, (
        f"Only {checked} shim/target pairs compared; expected at least {_BACKEND_COUNT}. One "
        "of the two roots is probably stale, which makes this guard vacuous."
    )
    assert not problems, "Compatibility shims out of sync with their targets:\n" + "\n".join(problems)


def test_backend_class_paths_are_pinned_to_the_integrations_layer():
    """`vector_db_class` in the AgentOS /registry response is derived from `cls.__module__`.

    `os/routers/registry/registry.py` reports `f"{cls.__module__}.{cls.__name__}"` as
    `VectorDbMetadata.class_path` and `KnowledgeMetadata.vector_db_class`. EGPT-1.5 changed
    those values from `egpt.vectordb.<backend>...` to `egpt.integrations.vectordb.<backend>...`,
    which no import shim can mask because the shim re-exports the same class object.

    Pinning the prefix here means the next tree move surfaces as a failing test naming an
    API payload change, rather than as a silent break for consumers that match on the string.
    """
    discovered = discover_subclasses(_vectordb_root(), "VectorDb")
    assert len(discovered) >= _BACKEND_COUNT, "scan root stale; see the anchor test above"

    stray = sorted(module for (module, _name) in discovered if not module.startswith("egpt.integrations.vectordb."))
    assert not stray, (
        "These backends would serialize a class_path outside the integrations layer: "
        + ", ".join(stray)
        + ". The AgentOS /registry response embeds this string, so moving them is a "
        "breaking API change that needs to be called out in the PR and release notes."
    )


def test_every_backend_submodule_has_a_shim():
    """The 24 submodule shims need coverage too, not just the 18 package `__init__` files.

    Dotted paths like `egpt.vectordb.pgvector.index` and `egpt.vectordb.weaviate.index` are
    what benchmarks and cookbooks actually import; package-level parity says nothing about
    them. A shim that loses its `from <target> import *` still resolves attribute access via
    `__getattr__`, but a star import of it binds zero names.
    """
    checked, problems = check_submodule_shims(
        _vectordb_root(),
        _deprecated_vectordb_root(),
        target_prefix="egpt.integrations.vectordb",
        shim_prefix="egpt.vectordb",
    )

    assert checked >= _SUBMODULE_SHIM_COUNT, (
        f"Only {checked} submodule shims compared; expected at least {_SUBMODULE_SHIM_COUNT}. "
        "One of the two roots is probably stale, which makes this guard vacuous."
    )
    assert not problems, "Backend submodule shims out of sync with their targets:\n" + "\n".join(problems)


def test_every_backend_seeds_its_own_id():
    """Every concrete VectorDb subclass must pass ``id=`` to ``super().__init__``.

    Seven backends omitted it and fell through to the base-class fallback --
    ``generate_id(<class name>)`` -- so every instance of each backend shared one
    deterministic id and the registry, which de-dupes by id and keeps the first
    registration, silently shadowed all but one (aicoe-tracker#155). The bug was reached
    by omission, so the guard is structural: a newly added backend that forgets the seed
    fails here, without any SDK installed.

    AST-based like everything above; matched on the keyword being *passed*, not on how
    the seed is computed -- connection-target seeds and the wrapper backends' fresh-id
    policy both satisfy it, and an explicit ``id=`` argument always must.
    """
    import ast

    root = _vectordb_root()
    offenders = []
    checked = 0

    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            base_names = {b.id for b in node.bases if isinstance(b, ast.Name)} | {
                b.attr for b in node.bases if isinstance(b, ast.Attribute)
            }
            if "VectorDb" not in base_names:
                continue
            init = next(
                (m for m in node.body if isinstance(m, ast.FunctionDef) and m.name == "__init__"),
                None,
            )
            if init is None:
                offenders.append(f"{path.name}:{node.name} (no __init__ at all)")
                continue
            checked += 1
            passes_id = any(
                isinstance(c, ast.Call)
                and isinstance(c.func, ast.Attribute)
                and c.func.attr == "__init__"
                and isinstance(c.func.value, ast.Call)
                and isinstance(c.func.value.func, ast.Name)
                and c.func.value.func.id == "super"
                and any(kw.arg == "id" for kw in c.keywords)
                for c in ast.walk(init)
            )
            if not passes_id:
                offenders.append(f"{path.name}:{node.name}")

    assert checked >= _BACKEND_COUNT, (
        f"Only {checked} VectorDb __init__ bodies checked; expected at least {_BACKEND_COUNT}. "
        "The scan root is probably stale, which would make this guard vacuous."
    )
    assert not offenders, (
        "VectorDb subclasses that never pass id= to super().__init__, so all their "
        f"instances share generate_id(<class name>): {sorted(offenders)}"
    )
