"""Every file the product opens by path is shipped by the package.

An editable install (`uv pip install -e product`) reads straight from the working tree, so a
data file missing from `[tool.setuptools.package-data]` is invisible in development and
missing only in the image -- where it surfaces as a `FileNotFoundError` in a background run
that nobody can debug from the UI. That is exactly how the KPI suggestion run failed on the
demo VM: `kpi/retail_metrics.json` was read at the start of every run and packaged by no
glob, and the prose corpus next to it loaded as empty for the same reason.

These tests are the guard. They resolve the paths from the constants the code itself uses,
so moving a file moves the assertion with it. No database, no network.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import List

import pytest

from egpt_retail.entrypoints.load_knowledge import PACKAGE_CORPUS
from egpt_retail.suggestions.agents import CATALOGUE_PATH

_PRODUCT_ROOT = Path(__file__).resolve().parents[1]
_PACKAGE_ROOT = _PRODUCT_ROOT / "egpt_retail"


def _package_data_globs() -> List[str]:
    """The globs in ``[tool.setuptools.package-data]``, read as text.

    Not parsed with ``tomllib``: that is stdlib only from 3.11 and this package supports 3.10
    (``requires-python = ">=3.10"``), so a parsed read passes locally and fails the 3.10 CI
    job. ``test_r4_sql_assets.py`` reads the same section the same way and says the same
    thing. The section is sliced out first, so a glob mentioned in a comment elsewhere in the
    file cannot satisfy an assertion here.
    """
    body = (_PRODUCT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    start = body.index("[tool.setuptools.package-data]")
    end = body.index("\n[", start + 1)
    block = body[start:end]
    # Only the quoted entries of the list, never a word out of the comments around them.
    return re.findall(r'"([^"]+)"', block.split("egpt_retail = [", 1)[1])


def _is_packaged(path: Path) -> bool:
    relative = path.resolve().relative_to(_PACKAGE_ROOT).as_posix()
    return any(fnmatch.fnmatch(relative, glob) for glob in _package_data_globs())


def test_the_kpi_metric_catalogue_is_packaged() -> None:
    """Read by `_catalogue()` before the suggestion run reports its first phase."""
    assert CATALOGUE_PATH.is_file()
    assert _is_packaged(CATALOGUE_PATH), (
        f"{CATALOGUE_PATH.name} is read at runtime but matches no package-data glob, so "
        "an installed copy cannot suggest KPIs at all"
    )


def test_the_shipped_prose_corpus_is_packaged() -> None:
    """`load-knowledge` falls back to this directory when no corpus path is configured."""
    documents = sorted(PACKAGE_CORPUS.glob("*.md"))
    assert documents, "the shipped corpus is empty"
    unpackaged = [d.name for d in documents if not _is_packaged(d)]
    assert not unpackaged, (
        f"{len(unpackaged)} corpus documents match no package-data glob, so an installed "
        f"copy loads no domain prose: {unpackaged[:3]}"
    )


@pytest.mark.parametrize(
    "directory",
    ["ontology", "skills", "kpi", "knowledge/corpus", "api/openapi"],
)
def test_runtime_data_directories_ship_their_files(directory: str) -> None:
    """A whole directory of runtime data, not one file: a second `.json` dropped next to
    `retail_metrics.json` should be shipped by the same glob rather than need a new one."""
    root = _PACKAGE_ROOT / directory
    carried = [
        p
        for p in sorted(root.glob("*"))
        if p.is_file() and p.suffix in (".json", ".md", ".yaml")
    ]
    unpackaged = [p.name for p in carried if not _is_packaged(p)]
    # Documentation about the data is not the data; it is allowed to stay behind.
    unpackaged = [
        name for name in unpackaged if name not in ("README.md", "CHANGELOG.md")
    ]
    assert not unpackaged, f"{directory} ships files no glob covers: {unpackaged}"
