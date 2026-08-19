"""Tests for the shared AST helpers behind the layout guards (`tests/unit/_layer_guards.py`).

The helpers are themselves the enforcement machinery for the layering contracts, so their
blind spots become everyone's blind spots. The relative-import cases below pin the fix for
the one that mattered: `import_sites` used to skip `level > 0` imports entirely, so
rewriting `from egpt.memory.config import X` as `from .config import X` walked a real edge
past every guard built on it (and past import-linter's direct-edge contracts too).
"""

import textwrap

from tests.unit import _layer_guards
from tests.unit._layer_guards import import_sites


def _fake_tree(tmp_path, monkeypatch):
    """A minimal `egpt` tree under tmp_path, with `package_root()` pointed at it."""
    (tmp_path / "egpt" / "memory" / "graph").mkdir(parents=True)
    monkeypatch.setattr(_layer_guards, "package_root", lambda: tmp_path)
    return tmp_path


def test_import_sites_resolves_relative_imports_in_a_module(tmp_path, monkeypatch):
    root = _fake_tree(tmp_path, monkeypatch)
    module = root / "egpt" / "memory" / "embedding.py"
    module.write_text(
        textwrap.dedent(
            """
            import os
            from egpt.utils.log import log_error
            from .config import build_memory_provider
            from . import config
            from ..utils import log
            """
        ),
        encoding="utf-8",
    )

    # Both relative spellings of the config import resolve to the same absolute name.
    assert {m for _, m in import_sites(module)} == {
        "os",
        "egpt.utils.log",
        "egpt.memory.config",  # from .config import ... AND from . import config
        "egpt.utils",  # from ..utils import log
    }


def test_import_sites_resolves_relative_imports_in_a_package_init(tmp_path, monkeypatch):
    root = _fake_tree(tmp_path, monkeypatch)
    init = root / "egpt" / "memory" / "graph" / "__init__.py"
    init.write_text(
        textwrap.dedent(
            """
            from .episodic import EpisodicRule
            from ..types import MemoryScope
            """
        ),
        encoding="utf-8",
    )

    # For an __init__.py the module's dotted name IS the package: one dot stays inside
    # egpt.memory.graph, two dots reach egpt.memory.
    assert {m for _, m in import_sites(init)} == {
        "egpt.memory.graph.episodic",
        "egpt.memory.types",
    }
