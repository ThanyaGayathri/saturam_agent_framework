"""Mechanical guards on the one property the EGPT-1.9 design is built around: laziness.

The base install ships no SDKs. Every extraction from EGPT-1.4 through 1.8 preserved that by
naming integrations with strings and importing them only on demand, and the discovery seam
has to keep the promise for all five kinds at once: you can list all 58 model providers and
all 18 vector db backends without importing a single one of them.

This is exactly what a decorator-based registry cannot do -- decorating a class means
importing its module, so learning 58 names would mean importing 58 SDKs. The declaration
rows exist to avoid that, and these tests are what stop someone "simplifying" them back into
decorators without noticing what it costs.

Three layers, cheapest first:

* an AST check that runs everywhere and needs no subprocess;
* a clean-interpreter check that enumerating every kind imports nothing;
* a clean-interpreter check that resolving one row imports only that row.

The last two must be subprocesses. Under pytest, ``sys.modules`` has already been filled by
conftest and by every sibling test that ran first, so an in-process assertion about it would
be meaningless -- it would pass or fail on test ordering rather than on the property.
"""

import ast
import json
import subprocess
import sys
import textwrap

import pytest

from egpt.registry import discovery
from tests.unit._layer_guards import imported_modules, package_root

# Vendor SDKs a provider module would pull in. Not exhaustive -- the `egpt.integrations.`
# check below is the general net -- but named explicitly so a failure says *which* SDK the
# registry started dragging in, rather than only which framework module.
_VENDOR_SDKS = [
    "anthropic",
    "boto3",
    "chromadb",
    "cohere",
    "google",
    "groq",
    "lancedb",
    "mistralai",
    "ollama",
    "openai",
    "pinecone",
    "pymongo",
    "qdrant_client",
    "redis",
    "sqlalchemy",
    "weaviate",
]


def _run_in_clean_interpreter(body):
    """Run `body` in a fresh interpreter and return the JSON it prints on the last line.

    A fresh process is the point: this suite's own imports would otherwise answer the
    question for us.
    """
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(body)],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, f"subprocess failed:\n{result.stdout}\n{result.stderr}"
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_declaration_modules_import_nothing_but_the_seam():
    """A declaration module is data. It may name integrations, never import them.

    The always-on layer: no subprocess, so it runs even where spawning one is awkward, and
    it points at the offending file directly instead of at a leaked module name.
    """
    root = package_root() / "egpt" / "registry" / "_declarations"
    assert root.is_dir(), f"declarations package not found: {root}"

    modules = sorted(root.glob("*.py"))
    # Non-vacuity: if the package moves, an empty glob would pass this while checking
    # nothing -- the exact way EGPT-1.4's drift guard silently became a no-op.
    assert len(modules) >= 6, (
        f"Only {len(modules)} declaration modules found under {root}; expected one per kind "
        "plus __init__. The path is probably stale, which makes this guard vacuous."
    )

    offenders = {}
    for path in modules:
        imported = {name for name in imported_modules(path) if name.startswith("egpt.")}
        bad = sorted(name for name in imported if not name.startswith("egpt.registry.discovery"))
        if bad:
            offenders[path.name] = bad

    assert not offenders, (
        f"Declaration modules importing framework code other than the seam: {offenders}. "
        "Rows must stay (module, attr) strings -- importing the target here would make "
        "enumerating a kind import every integration in it."
    )


def test_seam_itself_imports_no_integration():
    """`discovery` may import stdlib and the logger, nothing else.

    It sits below every kind, so anything it imports is imported by everyone who resolves
    anything.
    """
    path = package_root() / "egpt" / "registry" / "discovery.py"
    egpt_imports = sorted(name for name in imported_modules(path) if name.startswith("egpt."))
    assert egpt_imports == ["egpt.utils.log"], (
        f"egpt/registry/discovery.py imports {egpt_imports}; expected only egpt.utils.log. "
        "The seam must stay the cheapest module in core."
    )


def test_enumerating_every_kind_imports_no_integration():
    """The central claim: listing every registered name imports none of the targets.

    If this fails, `import egpt` has begun to cost the optional dependency set -- which is
    what the declaration rows exist to prevent.
    """
    result = _run_in_clean_interpreter(
        f"""
        import json, sys
        import egpt.registry.discovery as d

        for kind in d.KINDS:
            d.names(kind)
            d.entries(kind)
            d.declarations(kind)

        print(json.dumps({{
            "kinds": list(d.KINDS),
            "counts": {{k: len(d.names(k)) for k in d.KINDS}},
            "egpt": sorted(m for m in sys.modules if m.startswith("egpt.integrations.")),
            "sdk": sorted(m for m in {_VENDOR_SDKS!r} if m in sys.modules),
        }}))
        """
    )

    # Non-vacuity: enumerating an empty registry would trivially import nothing.
    assert result["counts"]["model"] >= 50, (
        f"Only {result['counts']['model']} model providers enumerated; the declarations did not "
        "load, so this guard would pass while checking nothing."
    )
    assert result["counts"]["vectordb"] >= 18, result["counts"]

    assert result["egpt"] == [], (
        f"Enumerating the registry imported integration modules: {result['egpt']}. "
        "Declaration rows must not import what they name."
    )
    assert result["sdk"] == [], f"Enumerating the registry imported vendor SDKs: {result['sdk']}."


def test_resolving_one_row_imports_only_that_row():
    """Resolution is narrow: asking for one backend must not drag in its siblings.

    `in_memory` is chosen because its module imports only stdlib and `egpt.db.*`, so a clean
    result here is about the registry rather than about which SDKs happen to be installed.
    """
    result = _run_in_clean_interpreter(
        """
        import json, sys
        import egpt.registry.discovery as d

        cls = d.resolve("db", "in_memory")
        db_modules = sorted(
            m for m in sys.modules
            if m.startswith("egpt.integrations.db.") and not m.endswith(".utils")
        )
        print(json.dumps({
            "resolved": cls.__name__,
            "db_modules": db_modules,
            "other_kinds": sorted(
                m for m in sys.modules
                if m.startswith("egpt.integrations.")
                and not m.startswith("egpt.integrations.db")
            ),
        }))
        """
    )

    assert result["resolved"] == "InMemoryDb"
    unrelated = [m for m in result["db_modules"] if "in_memory" not in m]
    assert not unrelated, (
        f"Resolving 'in_memory' also imported {unrelated}. Resolution must touch only the row "
        "asked for; importing siblings would make one lookup cost every driver."
    )
    assert result["other_kinds"] == [], (
        f"Resolving a db also imported {result['other_kinds']} from other integration kinds."
    )


def test_the_framework_registers_nothing_by_decorator():
    """The decorator exists for user code. In-tree use would defeat the whole design.

    ``@register`` is part of the public API (it is how runtime and out-of-tree code registers
    something it has already imported), but a decorator in this repository would mean an
    integration module has to be imported for its name to be known.
    """
    root = package_root() / "egpt"
    offenders = []
    for path in root.rglob("*.py"):
        if path.parts[-2:] == ("registry", "discovery.py"):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
            if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                call = decorator.func if isinstance(decorator, ast.Call) else decorator
                name = getattr(call, "attr", None) or getattr(call, "id", None)
                if name == "register":
                    offenders.append(f"{path.relative_to(root)}:{node.lineno} ({node.name})")

    assert not offenders, (
        "In-tree code registers by decorator: "
        + ", ".join(offenders)
        + ". Decorating a class imports its module, so its SDK would load just to learn its "
        "name. Add a row to egpt/registry/_declarations/ instead."
    )


@pytest.mark.parametrize("kind", discovery.KINDS)
def test_every_declaration_row_is_lazy(kind):
    """No built-in row may carry a preloaded object -- that would mean it was imported."""
    eager = sorted(name for name, reg in discovery.declarations(kind).items() if reg.obj is not None)
    assert not eager, (
        f"Built-in {kind} rows carrying an already-imported object: {eager}. "
        "Built-ins must be declared with module/attr strings."
    )
