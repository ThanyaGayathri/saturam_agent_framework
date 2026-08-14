"""Mechanical guards on the EGPT-1.8 core/integrations split for knowledge.

EGPT-1.8 moved the concrete readers, embedders, rerankers, loaders and remote-content
sources out of ``egpt/knowledge/`` into ``egpt/integrations/knowledge/``, leaving a
deprecation shim at every old path. What stays in core is the pipeline, the domain types,
the ``Reader``/``Embedder`` interfaces, ``ReaderFactory`` (the construction seam) and the
chunkers.

The layout checks are AST-based and import nothing under test, which matters because nearly
every module they scan raises ImportError without its optional SDK. The shared machinery
lives in ``tests/unit/_layer_guards.py``; EGPT-1.4 through 1.7 use the same helpers.

The non-vacuity floors below are load-bearing. Without them a stale scan root would make
these guards compare empty sets and report PASS -- not hypothetical: EGPT-1.4 moved the
providers and its drift guard silently degraded to a no-op.
"""

import ast
import importlib.util  # `.util` is not bound by a bare `import importlib`; a guard below needs find_spec
import warnings
from pathlib import Path

import pytest

# Safe to import at module scope: the reader package binds only the interface and the
# factory, both of which are dependency-free. Nothing else here imports a scanned module.
from egpt.knowledge.reader import ReaderFactory

# Same rationale for the discovery seam: it imports only stdlib and the logger, and the
# declaration modules it loads are pure data -- `(module, attr)` strings that import nothing.
# Module scope rather than function scope because the embedder keys parametrize a test below,
# so they have to exist at collection time. Mirrors tests/unit/vectordb/test_backend_layout.py.
from egpt.registry.discovery import declarations
from tests.unit._layer_guards import (
    check_shim_parity,
    check_submodule_shims,
    discover_subclasses,
    find_shim_path_strings,
    import_sites,
    imported_modules,
    module_name,
    package_root,
)

# Floors, not exact counts, so adding a 22nd reader does not fail these guards -- landing one
# in the wrong tree is what `test_deprecated_knowledge_tree_holds_only_shims` catches.
_READER_COUNT = 21  # 19 modules; field_labeled_csv and llms_txt each define more than one
_EMBEDDER_COUNT = 18
_RERANKER_COUNT = 6  # aws_bedrock alone defines three
_TARGET_PACKAGE_COUNT = 5  # reader, embedder, reranker, loaders, remote_content
_SUBMODULE_SHIM_COUNT = 55

# What the core `knowledge/reader/__init__.py` and `knowledge/embedder/__init__.py` export
# beyond their (deliberately bare) integrations counterparts. These two are not shims: they
# are the interface packages, and the target packages export nothing precisely so that
# importing them does not drag in every optional SDK at once.
_SHIM_EXTRA_EXPORTS = {
    "reader": {"Reader", "ReaderFactory"},
    "embedder": {"Embedder"},
}

# Modules allowed to name a concrete knowledge integration, with the reason and the ticket
# that clears it. Neither is a slip; both are recorded so the exception stays visible.
_INTEGRATION_IMPORT_ALLOWLIST = {
    # ADR 0001 extraction prerequisite #1: the pipeline statically names the remote-content
    # types and RemoteKnowledge, and the ADR carves it out of the enforceable core set until
    # those dependencies are inverted. Readers already go through ReaderFactory.
    "egpt.knowledge.knowledge",
    # ADR 0001 rule 4 composition-root carve-out: the AgentOS router builds remote-content
    # configs straight from request payloads and type-dispatches on them.
    "egpt.os.routers.knowledge.knowledge",
    # Annotation only -- the import sits under `if TYPE_CHECKING` and the field is quoted,
    # so nothing is imported at runtime. Recorded rather than removed because deleting it
    # would type `Content.remote_content` as Any and lose the check at every call site; the
    # same call the db guard makes for its cast()-only migration import.
    "egpt.knowledge.content",
}

_INTEGRATIONS_PREFIX = "egpt.integrations.knowledge"

# `declarations`, not `entries`: an out-of-tree embedder registered through an
# `egpt.embedders` entry point lives in someone else's package, so it must not be measured
# against the integrations tree on disk.
EMBEDDER_DECLARATIONS = declarations("embedder")


def _integrations_knowledge_root():
    root = package_root() / "egpt" / "integrations" / "knowledge"
    assert root.is_dir(), f"knowledge integrations root not found: {root}"
    return root


def _deprecated_knowledge_root():
    root = package_root() / "egpt" / "knowledge"
    assert root.is_dir(), f"deprecated knowledge root not found: {root}"
    return root


def _shim_modules():
    """Dotted names of the modules under `egpt/knowledge` that only forward to a target.

    Derived from disk rather than frozen in a list: a module counts as a shim when it
    imports the integrations module occupying its own former path. That deliberately
    excludes `knowledge/reader/__init__.py` and `knowledge/embedder/__init__.py`, which
    share a name with a target package but export the core interfaces and forward nothing.
    """
    root = _deprecated_knowledge_root()
    shims = set()
    for path in root.rglob("*.py"):
        module = module_name(path)
        target = _INTEGRATIONS_PREFIX + module[len("egpt.knowledge") :]
        if any(m == target or m.startswith(target + ".") for m in imported_modules(path)):
            shims.add(module)
    return shims


def _core_knowledge_modules():
    """Modules under `egpt/knowledge` that are core, i.e. everything that is not a shim."""
    shims = _shim_modules()
    for path in sorted(_deprecated_knowledge_root().rglob("*.py")):
        if module_name(path) not in shims:
            yield path


def test_all_readers_live_in_integrations():
    """Every concrete Reader must be discoverable under the integrations layer.

    This is the non-vacuity anchor for the guards below: if the scan root stops matching
    where readers actually live, they would compare empty sets and report PASS.
    """
    root = _integrations_knowledge_root()
    discovered = discover_subclasses(root, "Reader")

    assert len(discovered) >= _READER_COUNT, (
        f"Only {len(discovered)} Reader subclasses discovered under {root}; expected at "
        f"least {_READER_COUNT}. The scan root is probably stale, which makes the companion "
        "guards vacuous."
    )


def test_all_embedders_and_rerankers_live_in_integrations():
    """Same anchor for the other two implementation families EGPT-1.8 moved."""
    root = _integrations_knowledge_root()
    embedders = discover_subclasses(root, "Embedder")
    rerankers = discover_subclasses(root, "Reranker")

    assert len(embedders) >= _EMBEDDER_COUNT, (
        f"Only {len(embedders)} Embedder subclasses discovered under {root}; expected at least {_EMBEDDER_COUNT}."
    )
    assert len(rerankers) >= _RERANKER_COUNT, (
        f"Only {len(rerankers)} Reranker subclasses discovered under {root}; expected at least {_RERANKER_COUNT}."
    )


def test_deprecated_knowledge_tree_holds_only_shims():
    """No reader, embedder or reranker implementation may reappear under `egpt/knowledge`.

    That tree keeps the pipeline, the domain types, the two interfaces, ReaderFactory and
    the chunkers. A concrete implementation defined there would be invisible to the scans
    above, so it could ship having quietly re-coupled core to an external SDK.

    Scans the whole tree, not just the moved package names: a reader added in a *new*
    directory under egpt/knowledge/ is the likeliest way this rule actually gets broken, and
    precisely the case it exists for.
    """
    root = _deprecated_knowledge_root()
    stragglers = sorted(
        f"{module}.{name}"
        for base in ("Reader", "Embedder", "Reranker")
        for module, name in discover_subclasses(root, base)
    )

    assert not stragglers, (
        f"Implementations found under the deprecated egpt/knowledge tree: {stragglers}. "
        "Concrete readers, embedders and rerankers belong under egpt/integrations/knowledge/; "
        "egpt/knowledge holds the pipeline, interfaces, domain types and chunkers only."
    )


def test_chunkers_stay_in_core():
    """The chunkers are core, and this ticket deliberately did not move them.

    EGPT-1.8's ticket justified that with "chunkers have zero external imports", which is
    false -- code/markdown/semantic carry chonkie, unstructured and numpy, and agentic
    reaches into egpt.models. They stay in core because ADR 0001 places them there, not
    because they are pure. This guard pins the decision so a later reading of the wrong
    rationale does not quietly relocate them.
    """
    chunking = _deprecated_knowledge_root() / "chunking"
    assert chunking.is_dir(), "egpt/knowledge/chunking is gone; it is core per ADR 0001"
    assert (chunking / "strategy.py").exists(), "the chunking strategy seam left core"

    moved = _integrations_knowledge_root() / "chunking"
    assert not moved.exists(), (
        "chunkers appeared under egpt/integrations/knowledge/chunking. ADR 0001 places them "
        "in core; moving them needs an ADR amendment, not just a ticket."
    )


def test_every_shim_mirrors_its_target():
    """A shim must declare everything its target declares, and bind what it declares."""
    checked, problems = check_shim_parity(
        _integrations_knowledge_root(),
        _deprecated_knowledge_root(),
        extra_exports=_SHIM_EXTRA_EXPORTS,
    )

    assert not problems, "\n".join(problems)
    assert checked >= _TARGET_PACKAGE_COUNT, (
        f"Only {checked} shim/target package pairs compared; expected at least "
        f"{_TARGET_PACKAGE_COUNT}. Pairs are skipped when either side is missing, so a low "
        "count means this guard is checking almost nothing."
    )


def test_every_moved_module_has_a_submodule_shim():
    """The dotted submodule paths cookbooks and tests import must keep resolving."""
    checked, problems = check_submodule_shims(
        _integrations_knowledge_root(),
        _deprecated_knowledge_root(),
        _INTEGRATIONS_PREFIX,
        "egpt.knowledge",
    )

    assert not problems, "\n".join(problems)
    assert checked >= _SUBMODULE_SHIM_COUNT, (
        f"Only {checked} submodule shims verified; expected at least {_SUBMODULE_SHIM_COUNT}."
    )


def test_nested_reader_utils_modules_have_shims():
    """`check_submodule_shims` only globs one level deep.

    `reader/utils/` is the one package nested two levels under the knowledge root, and the
    llms_txt toolkit imports from it -- so it is asserted explicitly rather than assumed
    covered.
    """
    target = _integrations_knowledge_root() / "reader" / "utils"
    shim = _deprecated_knowledge_root() / "reader" / "utils"
    expected = {p.name for p in target.glob("*.py")}
    assert expected, f"no modules found under {target}; the path is stale"

    missing = sorted(name for name in expected if not (shim / name).exists())
    assert not missing, f"No shim for egpt/knowledge/reader/utils/{missing}."


def test_top_level_remote_knowledge_has_a_shim():
    """`remote_knowledge.py` sits directly under the knowledge root, so no package scan sees it."""
    shim = _deprecated_knowledge_root() / "remote_knowledge.py"
    assert shim.exists(), "egpt/knowledge/remote_knowledge.py lost its shim."
    assert f"{_INTEGRATIONS_PREFIX}.remote_knowledge" in imported_modules(shim), (
        "egpt/knowledge/remote_knowledge.py does not forward to its target."
    )


def test_no_concrete_knowledge_integration_imported_outside_integrations():
    """Only the integrations layer, the shim tree and the allowlist may name an implementation."""
    root = package_root() / "egpt"
    shims = _shim_modules()
    offenders = []

    for path in root.rglob("*.py"):
        if path.relative_to(root).parts[0] == "integrations":
            continue  # the integrations layer itself
        module = module_name(path)
        if module in shims or module in _INTEGRATION_IMPORT_ALLOWLIST:
            continue
        hits = sorted(m for m in imported_modules(path) if m.startswith(_INTEGRATIONS_PREFIX))
        if hits:
            offenders.append(f"{module}: {hits}")

    assert not offenders, (
        "Concrete knowledge integrations imported outside the integrations layer:\n"
        + "\n".join(offenders)
        + "\nResolve readers through egpt.knowledge.reader.ReaderFactory and the default "
        "embedder through egpt.knowledge.utils.get_default_embedder, or record the module "
        "in the allowlist with the ticket that clears it."
    )


def test_nothing_in_the_package_imports_through_a_shim():
    """No first-party module may reach an implementation by its deprecated path.

    Shims exist for downstream users. A shim import inside the package would emit a
    DeprecationWarning on every run, which is how the warnings stop meaning anything.
    """
    root = package_root() / "egpt"
    shims = _shim_modules()
    assert len(shims) >= _SUBMODULE_SHIM_COUNT, (
        f"Only {len(shims)} shim modules detected; expected at least {_SUBMODULE_SHIM_COUNT}. "
        "The detection is probably stale, which makes this guard vacuous."
    )

    offenders = []
    for path in root.rglob("*.py"):
        module = module_name(path)
        if module in shims:
            continue  # a shim necessarily names its own deprecated path
        hits = sorted(m for m in imported_modules(path) if m in shims)
        if hits:
            offenders.append(f"{module}: {hits}")

    assert not offenders, "Deprecated knowledge paths imported inside the package:\n" + "\n".join(offenders)


def test_core_knowledge_modules_import_no_implementation():
    """The core half of `egpt/knowledge` must not reach into the integrations layer.

    Walks the full AST rather than the import block, because the coupling this replaced was
    function-local: `SemanticChunking.__init__` imported OpenAIEmbedder inside an `if`.
    """
    root = package_root()
    offenders = []
    for path in _core_knowledge_modules():
        module = module_name(path)
        if module in _INTEGRATION_IMPORT_ALLOWLIST:
            continue
        for lineno, imported in import_sites(path):
            if imported.startswith("egpt.integrations"):
                offenders.append(f"{path.relative_to(root)}:{lineno} imports {imported}")

    assert not offenders, (
        "Core egpt/knowledge modules import the integrations layer, which re-couples core to "
        "it:\n" + "\n".join(offenders)
    )


def _reader_factory_path():
    return package_root() / "egpt" / "knowledge" / "reader" / "reader_factory.py"


def _module_path_strings(path):
    """Every `egpt.knowledge.*`/`egpt.integrations.*` string literal in a file.

    ReaderFactory addresses readers by dotted string, so these rows are invisible to the
    import scans above and fail only when a reader is actually requested.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith(("egpt.knowledge.", "egpt.integrations."))
    }


def test_reader_factory_rows_point_at_integrations():
    """Core must not address a reader module outside the integrations layer.

    `ReaderFactory.get_reader_class` resolves a hardcoded `(module_path, class_name)` table
    with importlib. A stale row raises only when that reader key is requested, so nothing
    else in the suite would notice.
    """
    rows = sorted(_module_path_strings(_reader_factory_path()))

    assert len(rows) >= 18, (
        f"Only {len(rows)} module-path strings found in reader_factory.py; expected at least "
        "the 18 reader_class_map rows. The extraction is probably stale, which makes this "
        "guard vacuous."
    )
    stale = [row for row in rows if not row.startswith(f"{_INTEGRATIONS_PREFIX}.reader.")]
    assert not stale, (
        f"ReaderFactory addresses modules outside the integrations layer: {stale}. "
        "These resolve through importlib, so a stale row fails only when that reader is "
        "requested."
    )


def test_embedder_declarations_point_at_integrations():
    """The core embedder seam is addressed by name for the same reason, so pin it."""
    from egpt.knowledge.utils import DEFAULT_EMBEDDER_NAME

    rows = EMBEDDER_DECLARATIONS
    # A floor, not merely `assert rows`. This guard shipped against a one-row table and so
    # said nothing about the other 17 embedders -- it passed while the kind was 1/18. The
    # floor is what makes "every embedder is declared" a claim the suite actually checks.
    assert len(rows) >= _EMBEDDER_COUNT, (
        f"Only {len(rows)} embedder declaration rows; expected at least {_EMBEDDER_COUNT}, "
        "one per Embedder subclass in the integrations tree."
    )
    assert DEFAULT_EMBEDDER_NAME in rows, (
        f"get_default_embedder() asks for '{DEFAULT_EMBEDDER_NAME}', which has no declaration row, "
        "so every core caller that was not handed an embedder would fail at construction."
    )

    for name, registration in rows.items():
        assert registration.module.startswith(f"{_INTEGRATIONS_PREFIX}.embedder."), (
            f"embedder declaration '{name}' points at {registration.module}, outside the integrations layer."
        )

    default = rows[DEFAULT_EMBEDDER_NAME]
    try:
        module = importlib.import_module(default.module)
    except ImportError as e:
        pytest.skip(f"optional dependency for the default embedder not installed: {e}")
    assert getattr(module, default.attr, None) is not None, f"{default.module} does not export {default.attr}"


def test_every_embedder_subclass_is_registered():
    """Every concrete embedder on disk must have a declaration row.

    The counterpart of the model and vector db drift guards. Embedders had no such guard
    because a table holding only the framework default could not support one; ADR 0001's
    extraction prerequisite 6 wants every embedder reachable by name, so the table is now
    complete and this is what keeps it complete.

    `__init__.py` is excluded from the scan: the target package is deliberately bare, and a
    re-export would not be a second definition anyway.
    """
    root = _integrations_knowledge_root() / "embedder"
    assert root.is_dir(), f"embedder integrations root not found: {root}"

    discovered = discover_subclasses(root, "Embedder", include_init=False)
    assert len(discovered) >= _EMBEDDER_COUNT, (
        f"Only {len(discovered)} Embedder subclasses discovered under {root}; expected at "
        f"least {_EMBEDDER_COUNT}. The scan root is probably stale, which makes this guard vacuous."
    )

    # Rows name the implementation module directly, so no re-export chasing is needed here.
    covered = {(reg.module, reg.attr) for reg in EMBEDDER_DECLARATIONS.values()}
    missing = sorted(f"{name} ({module})" for (module, name) in discovered - covered)
    assert not missing, (
        "Embedder subclasses with no declaration row: "
        + ", ".join(missing)
        + ". Add each to egpt/registry/_declarations/embedders.py."
    )


def test_embedder_keys_match_their_module():
    """The key of an embedder row is its module's basename, verbatim.

    The naming rule the table is built on -- `sentence_transformer`, not
    `sentence-transformer`, following the db and vectordb kinds rather than the hyphenated
    slugs users type in `get_model("provider:id")`. Pinned mechanically so renaming a module
    cannot silently strand the key that names it, which is the failure a convention held only
    in a docstring would not catch.
    """
    assert len(EMBEDDER_DECLARATIONS) >= _EMBEDDER_COUNT, "table is short; this guard would check little."

    mismatched = sorted(
        f"{key} -> {registration.module}"
        for key, registration in EMBEDDER_DECLARATIONS.items()
        if registration.module.rsplit(".", 1)[-1] != key
    )
    assert not mismatched, (
        "Embedder rows whose key is not their module basename: "
        + ", ".join(mismatched)
        + ". Keys follow the module name; see egpt/registry/_declarations/embedders.py."
    )


@pytest.mark.parametrize("embedder_key", sorted(EMBEDDER_DECLARATIONS))
def test_every_embedder_key_resolves(embedder_key):
    """Each declared embedder key resolves to a class in the integrations layer.

    Resolution imports the target, so a stale row fails only when that embedder is actually
    asked for -- nothing else in the suite would notice. Skip-gated per key on the missing
    SDK, the same shape as the reader guard above.

    The `find_spec` check is not redundant with the skip. `ModuleNotFoundError` subclasses
    `ImportError`, so a row pointing at a module that does not exist would otherwise be
    swallowed as "optional vendor package absent" -- the guard would skip on precisely the
    defect it exists to catch. `find_spec` locates the module without executing it, so it
    answers "is this row stale?" separately from "is the SDK installed?".
    """
    from egpt.registry.discovery import resolve

    registration = EMBEDDER_DECLARATIONS[embedder_key]
    try:
        spec = importlib.util.find_spec(registration.module)
    except ModuleNotFoundError:
        spec = None
    assert spec is not None, (
        f"embedder '{embedder_key}' declares module {registration.module}, which does not exist. "
        "Rows resolve through importlib, so this fails only when the embedder is requested."
    )

    try:
        embedder_class = resolve("embedder", embedder_key)
    except ImportError as e:
        pytest.skip(f"optional dependency for embedder '{embedder_key}' not installed: {e}")

    assert embedder_class.__module__.startswith(f"{_INTEGRATIONS_PREFIX}.embedder."), (
        f"embedder '{embedder_key}' resolved to {embedder_class.__module__}, outside the integrations layer."
    )


@pytest.mark.parametrize("reader_key", sorted(ReaderFactory.READER_METADATA))
def test_every_reader_key_resolves_without_touching_a_shim(reader_key):
    """Each advertised reader key resolves to a class in the integrations layer.

    `simplefilter("error")` proves resolution does not route through a deprecated path: the
    factory must import the target directly, or the shims would warn on ordinary use.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        try:
            reader_class = ReaderFactory.get_reader_class(reader_key)
        except ImportError as e:
            pytest.skip(f"optional dependency for reader '{reader_key}' not installed: {e}")

    assert reader_class.__module__.startswith(f"{_INTEGRATIONS_PREFIX}.reader."), (
        f"reader '{reader_key}' resolved to {reader_class.__module__}, outside the integrations layer."
    )


def test_no_test_patches_a_deprecated_shim_path():
    """No test may aim mock.patch at an `egpt.knowledge.<moved>` path.

    A submodule shim is `from <target> import *`, so patching a name on the shim rebinds the
    shim's own copy while the implementation keeps reading its module globals. The patch
    silently no-ops -- a patched HTTP client would let the test make a real request instead
    of failing loudly about a bad target.
    """
    tests_root = package_root() / "tests"
    assert tests_root.is_dir(), f"test root not found: {tests_root}"

    moved = sorted({module[len("egpt.knowledge.") :].split(".")[0] for module in _shim_modules()})
    assert moved, "no shim modules detected, so this guard would match nothing"

    scanned, offenders = find_shim_path_strings(
        tests_root,
        "egpt.knowledge",
        moved,
        exclude={Path(__file__).resolve()},  # this file names the paths on purpose
    )
    # Non-vacuity floor, taken from the helper's own return value: an empty or stale scan root
    # would otherwise silently report PASS.
    assert scanned >= 100, f"only {scanned} test files scanned under {tests_root}; the scan root is stale."

    assert not offenders, (
        "Tests referencing a deprecated shim path as a string (mock.patch targets patch the "
        "shim's binding, not the implementation):\n"
        + "\n".join(offenders)
        + f"\nPoint them at {_INTEGRATIONS_PREFIX}.* instead."
    )
