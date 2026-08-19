"""Mechanical guards on the memory seam's integrations boundary (EGPT-1.7c follow-up, #180).

``egpt/memory/`` is a services-layer package (ADR 0001), but its substrate backends live in
``egpt.integrations.memory`` and are addressed through ``egpt.memory``'s ports -- so, unlike
any other services package, its *envelope types* must be importable from the integrations
layer. The import-linter contract cannot express "these five modules yes, the rest no"
(aicoe-tracker#180 records why an `ignore_imports` carve-out was rejected), so `egpt.memory`
left that contract's `forbidden_modules` and this guard enforces the boundary with
module-level precision instead -- the same hand-off `egpt.db`/`egpt.vectordb`/`egpt.knowledge`
made to their layout guards, and `test_backend_layout.py`'s `_CORE_VECTORDB_MODULES` is the
direct precedent for the island below. The inward direction (``egpt.memory`` must not import
``egpt.integrations``) needs no test here: `egpt.memory` stays in the first import-linter
contract's `source_modules`.

Every check is AST-based (`tests/unit/_layer_guards.py`) and imports nothing under test, so
it holds with no backend SDK installed.
"""

from tests.unit._layer_guards import import_sites, package_root

# The type island: the exact `egpt.memory` modules an integrations-layer substrate backend may
# import. Everything a backend needs to *implement* the ports lives here -- envelope types and
# scope rules (`types`), the tier policy table (`tiers`), the seam's error taxonomy (`errors`,
# including `MemoryProviderConfigError`, moved there from `config` for exactly this reason),
# and the embedder-resolution seam (`embedding`). `config` is deliberately NOT in the island:
# it is the composition root (it imports `MemoryProvider` and the registry), and a backend
# importing it for anything would drag that whole surface across the boundary.
_ISLAND_MODULES = {
    "egpt.memory.embedding",
    "egpt.memory.errors",
    "egpt.memory.graph.episodic",
    "egpt.memory.graph.semantic",
    "egpt.memory.tiers",
    "egpt.memory.types",
}
# `egpt.memory.graph.episodic` and `egpt.memory.graph.semantic` are listed exactly, not as a
# `graph.*` prefix: their sibling `graph/entities.py` (and the subpackage `__init__`, which
# re-exports it) imports `egpt.memory.substrates` and `egpt.learn.*` -- services surface no
# backend may pull in. `semantic.py` earns its row here (SM-G7 #105) the same way `episodic.py`
# did: `cypher.py`'s `recall_facts_statement` needs `require_fact_filters`, the fact-edge recall
# guard every backend must apply identically, and `semantic.py` itself imports only
# `egpt.memory.types` -- already in the island -- so it passes
# `test_island_modules_import_only_core_or_island` below cleanly. A new cross-backend graph rule
# module earns its own island row only once it passes that same test.

# What an island module may not import: the integrations tree (that direction inverts the
# seam), and the orchestration/services/api packages the import-linter contract forbids for
# integrations -- because integrations import island modules, anything the island imports is
# one hop from every backend, and import-linter checks only direct edges
# (`allow_indirect_imports = true`). Core (egpt.utils, egpt.metrics, egpt.knowledge's embedder
# interface, ...) stays importable. The list is read from the contract itself (via
# import-linter's own config reader, the parser `lint-imports` trusts) rather than hand-copied:
# a new services package added to the contract must not leave the island guard behind silently.
_INTEGRATIONS_CONTRACT_NAME = "integrations must not import orchestration, services, or api"


def _forbidden_for_island():
    from importlinter import configuration
    from importlinter.application import use_cases

    configuration.configure()
    user_options = use_cases.read_user_options(config_filename=str(package_root() / "pyproject.toml"))
    contract = next(
        (c for c in user_options.contracts_options if c.get("name") == _INTEGRATIONS_CONTRACT_NAME),
        None,
    )
    assert contract is not None, (
        f"import-linter contract {_INTEGRATIONS_CONTRACT_NAME!r} not found in pyproject.toml -- "
        "if it was renamed, update _INTEGRATIONS_CONTRACT_NAME so this guard keeps tracking it"
    )
    forbidden = set(contract["forbidden_modules"])
    assert forbidden, "the contract's forbidden_modules emptied out; the island guard would check nothing"
    return tuple(sorted(forbidden | {"egpt.integrations"}))


_FORBIDDEN_FOR_ISLAND = _forbidden_for_island()

# Non-vacuity floor: the backends that exist today and provably import from the island. If the
# scan roots go stale, the guards below would compare empty sets and pass while checking
# nothing -- this anchor is what fails instead.
_KNOWN_BACKEND_MODULES = {
    "egpt/integrations/memory/graph/neo4j/cypher.py",
    "egpt/integrations/memory/graph/neo4j/neo4j.py",
    "egpt/integrations/memory/vector/pgvector/pgvector.py",
    "egpt/integrations/memory/vector/pgvector/sql.py",
}


def _is_island(module: str) -> bool:
    return module in _ISLAND_MODULES


def _island_paths():
    root = package_root()
    paths = [root.joinpath(*module.split(".")).with_suffix(".py") for module in sorted(_ISLAND_MODULES)]
    for path in paths:
        assert path.exists(), f"island module not found: {path}"
    return paths


def test_integrations_imports_of_memory_stay_inside_the_island():
    """No `egpt.integrations` module may import `egpt.memory` outside the type island.

    This is the check that replaced `egpt.memory` in the import-linter contract's
    `forbidden_modules`; a bare `import egpt.memory` also fails, because the package
    `__init__` re-exports the whole services surface.
    """
    root = package_root() / "egpt" / "integrations"
    assert root.is_dir(), f"integrations root not found: {root}"

    offenders = []
    for path in sorted(root.rglob("*.py")):
        for lineno, module in import_sites(path):
            if (module == "egpt.memory" or module.startswith("egpt.memory.")) and not _is_island(module):
                offenders.append(f"  {path.relative_to(package_root())}:{lineno}: {module}")

    assert not offenders, (
        "integrations module(s) import egpt.memory outside the type island:\n"
        + "\n".join(offenders)
        + "\nA substrate backend may import only "
        + ", ".join(sorted(_ISLAND_MODULES))
        + " -- anything else it needs should be handed to it through the port's method "
        "arguments, or belongs in the island by the rules in this file's docstring (and then "
        "in this list)."
    )


def test_the_island_guard_is_not_vacuous():
    """The known backends still exist and still import from the island."""
    root = package_root()
    importing_backends = set()
    for rel in sorted(_KNOWN_BACKEND_MODULES):
        path = root / rel
        assert path.exists(), f"known backend module disappeared (update _KNOWN_BACKEND_MODULES): {path}"
        if any(_is_island(module) for _, module in import_sites(path)):
            importing_backends.add(rel)

    assert importing_backends == _KNOWN_BACKEND_MODULES, (
        "backend module(s) no longer import from the island -- if the seam changed, update "
        f"this guard's floors: {sorted(_KNOWN_BACKEND_MODULES - importing_backends)}"
    )


def test_island_modules_import_only_core_or_island():
    """An island module must not reach into integrations, orchestration, services, or api.

    Integrations import the island directly, and import-linter checks only direct edges -- so
    a services import here would put every backend one silent hop from the layer it is
    forbidden to touch.
    """
    offenders = []
    for path in _island_paths():
        for lineno, module in import_sites(path):
            in_memory = module == "egpt.memory" or module.startswith("egpt.memory.")
            if in_memory and not _is_island(module):
                offenders.append(f"  {path.relative_to(package_root())}:{lineno}: {module} (non-island egpt.memory)")
            elif any(module == p or module.startswith(p + ".") for p in _FORBIDDEN_FOR_ISLAND):
                offenders.append(f"  {path.relative_to(package_root())}:{lineno}: {module}")

    assert not offenders, (
        "island module(s) import outside core/island, which would hand every substrate "
        "backend a transitive dependency on a forbidden layer:\n" + "\n".join(offenders)
    )
