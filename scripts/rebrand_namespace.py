#!/usr/bin/env python3
# /// script
# dependencies = ["libcst"]
# ///
"""Rewrite Python imports from the agno namespace to egpt."""

from __future__ import annotations

import argparse
from pathlib import Path
import re

import libcst as cst


SOURCE_NAMESPACE = "agno"
TARGET_NAMESPACE = "egpt"
TELEMETRY_KEYS = ("agent", "run", "session", "team", "user", "workflow")

METADATA_REPLACEMENTS = {
    Path("libs/agno/pyproject.toml"): (
        ('name = "agno"', 'name = "egpt"'),
        ('keywords = ["agno",', 'keywords = ["egpt",'),
        ('"agno[', '"egpt['),
        ("agno-infra", "egpt-infra"),
        ('include = ["agno*"]', 'include = ["egpt*"]'),
        ('agno = ["py.typed"]', 'egpt = ["py.typed"]'),
    ),
    Path("libs/agno_infra/pyproject.toml"): (
        ('name = "agno-infra"', 'name = "egpt-infra"'),
        ('"agno.cli.entrypoint:agno_cli"', '"egpt.cli.entrypoint:agno_cli"'),
        ('include = ["agno*"]', 'include = ["egpt*"]'),
        ('agno = ["py.typed"]', 'egpt = ["py.typed"]'),
        ('"agno.*"', '"egpt.*"'),
    ),
    Path("libs/agno/requirements.txt"): (
        ("agno (libs/agno/pyproject.toml)", "egpt (libs/agno/pyproject.toml)"),
    ),
    Path("libs/agno_infra/requirements.txt"): (
        (
            "agno-infra (libs/agno_infra/pyproject.toml)",
            "egpt-infra (libs/agno_infra/pyproject.toml)",
        ),
    ),
}


def replace_root(module: cst.BaseExpression) -> cst.BaseExpression:
    if isinstance(module, cst.Name):
        if module.value == SOURCE_NAMESPACE:
            return module.with_changes(value=TARGET_NAMESPACE)
        return module

    if isinstance(module, cst.Attribute):
        return module.with_changes(value=replace_root(module.value))

    return module


class NamespaceImportTransformer(cst.CSTTransformer):
    def leave_ImportAlias(
        self, original_node: cst.ImportAlias, updated_node: cst.ImportAlias
    ) -> cst.ImportAlias:
        return updated_node.with_changes(name=replace_root(updated_node.name))

    def leave_ImportFrom(
        self, original_node: cst.ImportFrom, updated_node: cst.ImportFrom
    ) -> cst.ImportFrom:
        if updated_node.relative or updated_node.module is None:
            return updated_node
        return updated_node.with_changes(module=replace_root(updated_node.module))


def rewrite_file(path: Path) -> bool:
    source = path.read_text()
    updated = cst.parse_module(source).visit(NamespaceImportTransformer()).code
    if updated == source:
        return False
    path.write_text(updated)
    return True


def rewrite_metadata() -> int:
    changed = 0
    for path, replacements in METADATA_REPLACEMENTS.items():
        source = path.read_text()
        updated = source
        for old, new in replacements:
            updated = updated.replace(old, new)
        if updated != source:
            path.write_text(updated)
            changed += 1
    return changed


def rewrite_module_references(roots: list[Path]) -> int:
    package_roots = set()
    for package_dir in (Path("libs/agno/egpt"), Path("libs/agno_infra/egpt")):
        for path in package_dir.iterdir():
            if path.name.startswith("__"):
                continue
            package_roots.add(path.stem if path.is_file() else path.name)
    module_pattern = re.compile(r"\bagno\.(" + "|".join(sorted(package_roots)) + r")\b")
    changed = 0

    for root in roots:
        for path in root.rglob("*.py"):
            if path.resolve() == Path(__file__).resolve():
                continue
            source = path.read_text()
            updated = module_pattern.sub(r"egpt.\1", source)
            for key in TELEMETRY_KEYS:
                updated = updated.replace(f"egpt.{key}.id", f"agno.{key}.id")
            updated = updated.replace(
                'metadata.version("agno")', 'metadata.version("egpt")'
            )
            updated = updated.replace('version("agno")', 'version("egpt")')
            updated = updated.replace('pkg_version("agno")', 'pkg_version("egpt")')
            updated = updated.replace(' / "agno" / ', ' / "egpt" / ')
            updated = updated.replace('LOGGER_NAME = "agno"', 'LOGGER_NAME = "egpt"')
            updated = updated.replace('getLogger("agno")', 'getLogger("egpt")')
            if updated != source:
                path.write_text(updated)
                changed += 1
    return changed


def rewrite_namespace_paths(roots: list[Path]) -> int:
    changed = 0
    for root in roots:
        paths = [root] if root.is_file() else root.rglob("*")
        for path in paths:
            if not path.is_file() or path.name == "TEST_LOG.md" or path.suffix not in {".md", ".py"}:
                continue
            source = path.read_text()
            updated = source.replace("libs/agno/agno", "libs/agno/egpt")
            updated = updated.replace("libs/agno_infra/agno", "libs/agno_infra/egpt")
            updated = updated.replace(
                "github.com/agno-agi/agno/blob/main/libs/agno/egpt",
                "github.com/agno-agi/agno/blob/main/libs/agno/agno",
            )
            updated = updated.replace(
                "raw.githubusercontent.com/agno-agi/agno/main/libs/agno/egpt",
                "raw.githubusercontent.com/agno-agi/agno/main/libs/agno/agno",
            )
            if updated != source:
                path.write_text(updated)
                changed += 1
    return changed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("roots", nargs="+", type=Path)
    args = parser.parse_args()

    changed = 0
    for root in args.roots:
        for path in root.rglob("*.py"):
            changed += rewrite_file(path)
    references_changed = rewrite_module_references(args.roots)
    paths_changed = rewrite_namespace_paths(
        [Path("AGENTS.md"), Path("CLAUDE.md"), Path("CONTRIBUTING.md"), Path("README.md"), Path("cookbook")]
    )
    metadata_changed = rewrite_metadata()
    print(
        f"Updated imports in {changed} files, module references in {references_changed} files, "
        f"namespace paths in {paths_changed} files, and {metadata_changed} metadata files."
    )


if __name__ == "__main__":
    main()
