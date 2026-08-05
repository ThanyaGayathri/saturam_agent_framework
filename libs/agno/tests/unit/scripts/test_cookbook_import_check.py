"""Tests for cookbook/scripts/check_cookbook_imports.py.

The checker exists because cookbooks are never executed in CI and `ruff` cannot flag a wrong
*imported* name -- five such typos shipped undetected (`CohereChat` for `Cohere`, `vLLM` for
`VLLM`, ...). These tests pin the two ways a guard like this dies quietly:

1. It stops detecting anything and passes vacuously (the failure mode EGPT-1.4's provider drift
   guard hit when its scan root went stale).
2. It gains false positives, gets seen as noise, and is disabled.

The case-sensitivity test is not hypothetical: the first draft used `Path.is_file()` to detect
submodules, and on macOS -- a case-insensitive filesystem -- `SambaNova.py` matched
`sambanova.py`, so four of the five real typos were silently skipped locally while CI on Linux
would have caught them.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT / "cookbook" / "scripts"))

import check_cookbook_imports as checker  # noqa: E402

LIB_ROOT = ROOT / "libs" / "agno"


def _check(source: str, tmp_path: Path):
    example = tmp_path / "example.py"
    example.write_text(source, encoding="utf-8")
    return checker.check_file(example, LIB_ROOT, tmp_path)


def test_real_cookbook_tree_is_clean():
    """The shipped tree passes. Also the zero-false-positive guard: loosening name extraction
    until it flags legitimate imports fails here first."""
    files = checker.iter_python_files(ROOT / "cookbook")
    violations = [v for f in files for v in checker.check_file(f, LIB_ROOT, ROOT)]
    assert not violations, "unexpected violations: " + "; ".join(f"{v.path}:{v.line} {v.message}" for v in violations)


def test_scan_is_not_vacuous():
    """A checker that silently scans nothing would pass every test above it."""
    files = checker.iter_python_files(ROOT / "cookbook")
    assert len(files) > 500, f"only {len(files)} cookbook files discovered; scan root is likely wrong"


def test_detects_a_nonexistent_name(tmp_path):
    violations = _check("from egpt.models.cohere import CohereChat\n", tmp_path)
    assert len(violations) == 1
    assert violations[0].code == "unknown_import_name"
    assert "CohereChat" in violations[0].message


@pytest.mark.parametrize(
    "wrong, module",
    [
        ("vLLM", "egpt.models.vllm"),
        ("SambaNova", "egpt.models.sambanova"),
        ("SiliconFlow", "egpt.models.siliconflow"),
    ],
)
def test_wrong_capitalization_is_caught_on_case_insensitive_filesystems(wrong, module, tmp_path):
    """`vLLM` must be flagged even though `vllm.py` exists and macOS would match it.

    Regression test for the submodule probe: `Path.is_file()` on a case-insensitive
    filesystem reported `vLLM.py` as present, so the typo was skipped as a module import.
    """
    violations = _check(f"from {module} import {wrong}\n", tmp_path)
    assert len(violations) == 1, f"{module}.{wrong} should be flagged"
    assert wrong in violations[0].message


def test_correct_names_are_accepted(tmp_path):
    source = (
        "from egpt.models.cohere import Cohere\n"
        "from egpt.models.vllm import VLLM\n"
        "from egpt.models.sambanova import Sambanova\n"
        "from egpt.models.vertexai.claude import Claude\n"
        "from egpt.knowledge.knowledge import Knowledge\n"
    )
    assert _check(source, tmp_path) == []


def test_submodule_import_is_not_a_violation(tmp_path):
    """`from egpt.utils import pprint` imports the module egpt/utils/pprint.py."""
    assert _check("from egpt.utils import pprint\n", tmp_path) == []


def test_annotated_assignment_is_a_binding(tmp_path):
    """`logger: AgnoLogger = agent_logger` is an AnnAssign, not an Assign."""
    assert _check("from egpt.utils.log import logger\n", tmp_path) == []


def test_module_with_dynamic_getattr_is_skipped(tmp_path):
    """egpt/db/__init__.py resolves names lazily via __getattr__ and cannot be checked."""
    assert _check("from egpt.db import DynamoDb\n", tmp_path) == []


def test_optional_dependency_fallback_names_are_accepted(tmp_path):
    """aws's `Claude` is bound inside a try/except ImportError fallback, still importable."""
    assert _check("from egpt.models.aws import AwsBedrock, Claude\n", tmp_path) == []


def test_detects_a_nonexistent_module(tmp_path):
    """A wrong *module path* fails at runtime exactly like a wrong name.

    Two of the seven bugs this check was written for were wrong module paths
    (`egpt.models.vertexai` for `egpt.models.vertexai.claude`); those were only caught because
    the wrong package happened to exist and be empty. A misspelled one must be reported too.
    """
    violations = _check("from egpt.this_module_does_not_exist import Missing\n", tmp_path)
    assert len(violations) == 1
    assert violations[0].code == "unknown_import_module"
    assert "egpt.this_module_does_not_exist" in violations[0].message


def test_detects_a_nonexistent_module_in_a_plain_import(tmp_path):
    violations = _check("import egpt.this_module_does_not_exist\n", tmp_path)
    assert len(violations) == 1
    assert violations[0].code == "unknown_import_module"


def test_plain_import_of_a_real_module_is_accepted(tmp_path):
    assert _check("import egpt.models.cohere\nimport egpt.agent as agent_pkg\n", tmp_path) == []


def test_namespace_package_is_not_reported_as_missing(tmp_path):
    """`egpt/cloud` has no `__init__.py`: importable, but exporting nothing statically visible."""
    assert _check("from egpt.cloud.aws import anything_at_all\n", tmp_path) == []


def test_type_checking_only_import_is_not_a_runtime_export(tmp_path):
    """`egpt/utils/agent.py` binds `Agent` under `if TYPE_CHECKING:`, which is false at runtime.

    Counting it as an export manufactured a phantom name and would suppress a genuinely broken
    import -- the same failure class this check exists to catch, in the opposite direction.
    """
    violations = _check("from egpt.utils.agent import Agent\n", tmp_path)
    assert len(violations) == 1
    assert violations[0].code == "unknown_import_name"


def test_type_checking_branch_is_excluded_but_its_else_is_not(tmp_path):
    module = tmp_path / "mod.py"
    module.write_text(
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from x import OnlyForTypes\n"
        "else:\n"
        "    from y import RealAtRuntime\n"
        "if not TYPE_CHECKING:\n"
        "    from z import AlsoRealAtRuntime\n",
        encoding="utf-8",
    )
    names = checker.exported_names(module)
    assert "OnlyForTypes" not in names
    assert {"RealAtRuntime", "AlsoRealAtRuntime"} <= names


def test_nested_scopes_do_not_manufacture_exports(tmp_path):
    """A name bound inside a function or class body is not a module attribute."""
    module = tmp_path / "mod.py"
    module.write_text(
        "if True:\n"
        "    def outer():\n"
        "        from egpt.agent import NotAnExport\n"
        "        local_name = 1\n"
        "\n"
        "    class Holder:\n"
        "        attribute = 2\n",
        encoding="utf-8",
    )
    assert checker.exported_names(module) == {"outer", "Holder"}


def test_non_egpt_imports_are_ignored(tmp_path):
    assert _check("from os.path import definitely_not_real\n", tmp_path) == []
    assert _check("import definitely.not.real\n", tmp_path) == []


def test_star_import_is_ignored(tmp_path):
    assert _check("from egpt.models.cohere import *\n", tmp_path) == []
