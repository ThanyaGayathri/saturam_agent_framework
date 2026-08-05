#!/usr/bin/env python3
"""Validate that every `egpt` import in a cookbook resolves: both the module and the names.

Cookbooks are never executed in CI and `ruff check` cannot catch a wrong *imported* name --
that is a runtime error, not a lint -- so a typo like `from egpt.models.cohere import
CohereChat` (the class is `Cohere`) ships silently and only fails when a user runs the example.
The same is true of a wrong module path: `from egpt.learn.mode import LearningMode` names a
module that does not exist (`LearningMode` lives in `egpt.learn`).

The check is deliberately **static**. It parses the target module with `ast` instead of
importing it, because the base CI environment installs only the `openai` SDK: an import-based
check would skip nearly every provider as "optional dependency absent" and catch nothing.

Resolution handles both layouts, so this keeps working across the EGPT-1.4 provider move:
`egpt.models.<provider>` resolves to `egpt/integrations/models/<provider>` when that exists,
otherwise to `egpt/models/<provider>`.
"""

from __future__ import annotations

import argparse
import ast
import json
from dataclasses import asdict, dataclass
from pathlib import Path

SKIP_DIR_NAMES = {"__pycache__", ".git", ".context", ".venv", ".venvs"}
ROOT_PACKAGE = "egpt"


@dataclass
class Violation:
    path: str
    line: int
    code: str
    message: str


def iter_python_files(base_dir: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(base_dir.rglob("*.py")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIR_NAMES for part in path.parts):
            continue
        files.append(path)
    return files


def _module_candidates(dotted: str) -> list[list[str]]:
    parts = dotted.split(".")
    candidates = []
    if parts[:2] == [ROOT_PACKAGE, "models"] and len(parts) > 2:
        candidates.append(["egpt", "integrations", "models"] + parts[2:])
    candidates.append(parts)
    return candidates


def module_path(dotted: str, lib_root: Path) -> Path | None:
    """Map a dotted module name to its file, preferring the integrations layer.

    After EGPT-1.4 the modules under `egpt/models/<provider>` are compatibility shims that
    re-export via `from ... import *`, which binds no statically visible names. Resolving to
    the real implementation keeps the check meaningful on both sides of that move.
    """
    for cand in _module_candidates(dotted):
        base = lib_root.joinpath(*cand)
        for candidate in (base / "__init__.py", base.with_suffix(".py")):
            if candidate.is_file():
                return candidate
    return None


def is_namespace_package(dotted: str, lib_root: Path) -> bool:
    """True if the module is a directory with no `__init__.py` (e.g. `egpt/cloud`).

    Such a package is importable but exports nothing statically visible, so it must be
    treated as resolved-but-unverifiable rather than missing.
    """
    return any(lib_root.joinpath(*cand).is_dir() for cand in _module_candidates(dotted))


def _bound_names(node: ast.AST, names: set[str]) -> None:
    """Collect names a single top-level statement binds."""
    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        names.add(node.name)
    elif isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    elif isinstance(node, ast.AnnAssign):
        # e.g. `logger: AgnoLogger = agent_logger` -- an AnnAssign, not an Assign.
        if isinstance(node.target, ast.Name):
            names.add(node.target.id)
    elif isinstance(node, (ast.Import, ast.ImportFrom)):
        for alias in node.names:
            if alias.name == "*":
                continue
            names.add(alias.asname or alias.name.split(".")[0])


def _is_type_checking(test: ast.expr) -> bool:
    """True for `TYPE_CHECKING` and `typing.TYPE_CHECKING`."""
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    return False


def _runtime_branches(node: ast.If) -> list[list[ast.stmt]]:
    """The branches of an `if` that can actually execute at runtime.

    `typing.TYPE_CHECKING` is false at runtime, so a name bound only under `if TYPE_CHECKING:`
    is *not* importable -- claiming otherwise manufactures phantom exports and suppresses the
    real broken imports this check exists to catch.
    """
    if _is_type_checking(node.test):
        return [node.orelse]
    if (
        isinstance(node.test, ast.UnaryOp)
        and isinstance(node.test.op, ast.Not)
        and _is_type_checking(node.test.operand)
    ):
        return [node.body]
    return [node.body, node.orelse]


def _collect_scope(body: list[ast.stmt], names: set[str]) -> None:
    """Collect names bound by a statement list, without leaving module scope.

    Descends into `try`/`if`/`with` -- optional-dependency fallbacks (aws's `Claude` stub) bind
    real module-level names there -- but never into a function or class body, whose locals are
    not module attributes.
    """
    for node in body:
        _bound_names(node, names)

        if isinstance(node, ast.Try):
            branches = [node.body, node.orelse, node.finalbody]
            branches += [handler.body for handler in node.handlers]
        elif isinstance(node, ast.If):
            branches = _runtime_branches(node)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            branches = [node.body]
        else:
            continue

        for branch in branches:
            _collect_scope(branch, names)


def exported_names(path: Path) -> set[str] | None:
    """Names a module provides, or None if it cannot be determined statically.

    Returns None when the module defines a module-level `__getattr__`, which can synthesise
    any attribute on demand (e.g. `egpt/db/__init__.py` resolves `DynamoDb` lazily). Such a
    module is unverifiable here and must not be reported.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return None

    names: set[str] = set()
    for node in tree.body:
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "__getattr__"
        ):
            return None

        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    try:
                        names |= {str(n) for n in ast.literal_eval(node.value)}
                    except (ValueError, SyntaxError):
                        pass

    _collect_scope(tree.body, names)
    return names


def has_submodule(dotted: str, name: str, lib_root: Path) -> bool:
    """True if `name` is a submodule of `dotted` -- `from egpt.utils import pprint` is a
    module import, not an attribute lookup.

    Compares directory entries explicitly rather than probing with `Path.is_file()`: macOS is
    case-insensitive, so `SambaNova.py` would match `sambanova.py` and mask exactly the
    wrong-capitalization typos this check exists to catch. That would make the check pass
    locally and fail on Linux CI (or vice versa). A child directory is importable even without
    an `__init__.py`, so namespace subpackages count as modules too.
    """
    for candidate in _module_candidates(dotted):
        base = lib_root.joinpath(*candidate)
        if not base.is_dir():
            continue
        entries = {entry.name for entry in base.iterdir()}
        if f"{name}.py" in entries or (name in entries and (base / name).is_dir()):
            return True
    return False


def check_file(path: Path, lib_root: Path, repo_root: Path) -> list[Violation]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return []

    rel = (
        path.relative_to(repo_root).as_posix()
        if path.is_relative_to(repo_root)
        else path.as_posix()
    )
    violations: list[Violation] = []

    def missing_module(dotted: str, lineno: int) -> Violation:
        return Violation(
            path=rel,
            line=lineno,
            code="unknown_import_module",
            message=f"'{dotted}' does not resolve to a module in the '{ROOT_PACKAGE}' package.",
        )

    for node in ast.walk(tree):
        # `import egpt.models.cohere` fails at runtime just as `from ... import` does.
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] != ROOT_PACKAGE:
                    continue
                if module_path(
                    alias.name, lib_root
                ) is None and not is_namespace_package(alias.name, lib_root):
                    violations.append(missing_module(alias.name, node.lineno))
            continue

        if not isinstance(node, ast.ImportFrom) or node.level != 0:
            continue
        if not node.module or node.module.split(".")[0] != ROOT_PACKAGE:
            continue

        target = module_path(node.module, lib_root)
        if target is None:
            # A namespace package (a directory with no `__init__.py`, e.g. `egpt/cloud`) is
            # importable but has no statically bound attributes. Treat it as an empty export
            # surface so the checks below still accept real child modules and reject arbitrary
            # names.
            if not is_namespace_package(node.module, lib_root):
                violations.append(missing_module(node.module, node.lineno))
                continue
            names: set[str] = set()
        else:
            names = exported_names(target)
            if names is None:
                continue  # dynamic module -- unverifiable statically

        resolved_to = (
            target.relative_to(lib_root).as_posix()
            if target is not None
            else node.module.replace(".", "/")
        )
        for alias in node.names:
            if alias.name == "*":
                continue
            if alias.name in names:
                continue
            if has_submodule(node.module, alias.name, lib_root):
                continue
            violations.append(
                Violation(
                    path=rel,
                    line=node.lineno,
                    code="unknown_import_name",
                    message=(
                        f"'{alias.name}' is not provided by '{node.module}' "
                        f"(resolved to {resolved_to})."
                    ),
                )
            )
    return violations


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-dir",
        default="cookbook",
        help="Directory of cookbook python examples to check.",
    )
    parser.add_argument(
        "--lib-root",
        default="libs/agno",
        help="Directory containing the `egpt` package.",
    )
    parser.add_argument(
        "--output-format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base_dir = Path(args.base_dir).resolve()
    lib_root = Path(args.lib_root).resolve()
    repo_root = Path.cwd().resolve()

    if not (lib_root / ROOT_PACKAGE).is_dir():
        print(f"error: no '{ROOT_PACKAGE}' package under {lib_root}")
        return 2

    files = iter_python_files(base_dir)
    violations: list[Violation] = []
    for path in files:
        violations.extend(check_file(path, lib_root, repo_root))

    payload = {
        "base_dir": base_dir.as_posix(),
        "checked_files": len(files),
        "violation_count": len(violations),
        "violations": [asdict(v) for v in violations],
    }

    if args.output_format == "json":
        print(json.dumps(payload, indent=2))
    else:
        print(
            f"Checked {payload['checked_files']} file(s) in {payload['base_dir']}. Violations: {payload['violation_count']}"
        )
        for v in violations:
            print(f"{v.path}:{v.line} [{v.code}] {v.message}")

    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
