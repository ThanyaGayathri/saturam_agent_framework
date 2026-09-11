"""EXECUTOR_REGISTRY.md's own convention, checked in the direction it did not yet check.

``test_r4_executors.py``/``test_r7_executors.py`` already assert that every executor a lane
*registers* is also documented -- code missing from the doc. They do not assert the reverse:
that every executor the doc *documents* is actually reachable through ``build_registry()``.

That reverse direction is exactly how ``check_golden_gate`` (R15) shipped: fully implemented,
documented as a row in this table with its own "Refuses" column, and never added to the tuple
sum ``build_registry()`` passes to ``Registry()`` -- so a stored workflow referencing it as an
``executor_ref`` would fail at resolution time, and the publish gate this package builds was
reachable only from tests and cookbooks, never through real workflow composition.

This module walks the document's own "The list" table and resolves each documented name
against the real registry, so this direction of drift is caught for every package from here
on, not only R15's.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Tuple

from egpt_retail.app.components import build_registry

_REGISTRY_DOC = (
    Path(__file__).resolve().parents[1]
    / "egpt_retail"
    / "contracts"
    / "EXECUTOR_REGISTRY.md"
)

#: Matches a table row's first cell once split on ``|``, e.g. ``" `check_golden_gate` "``.
_NAME_CELL = re.compile(r"^\s*`([a-zA-Z_][a-zA-Z0-9_]*)`\s*$")


def _documented_executor_names() -> Tuple[str, ...]:
    """Every ``executor_ref`` named as a row in the document's "## The list" table.

    Parses the markdown table directly rather than importing anything, so this test does not
    accidentally validate the document against itself -- it reads the same words a human
    reviewer would.
    """
    body = _REGISTRY_DOC.read_text(encoding="utf-8")
    lines = body.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "## The list")

    names = []
    for line in lines[start:]:
        if line.startswith("##") and lines[start] != line:
            break  # the next section: the table is over
        if not line.startswith("|"):
            continue
        cells = line.split("|")
        if len(cells) < 2:
            continue
        match = _NAME_CELL.match(cells[1])
        if match:
            names.append(match.group(1))
    return tuple(names)


def test_the_list_table_is_parseable() -> None:
    """A sanity check on the parser itself: if this drops to zero, the table's shape changed
    and the parser needs updating, not the executors."""
    assert len(_documented_executor_names()) >= 15


def test_every_documented_executor_resolves_via_build_registry() -> None:
    """The direction ``test_r4_executors.py``/``test_r7_executors.py`` do not check: documented
    but unregistered. A name here that ``get_function`` cannot find is a ``Step`` that would
    fail at resolution time in real workflow composition, however well it is covered by
    cookbooks and unit tests that call the function directly."""
    registry = build_registry()
    documented = _documented_executor_names()
    unresolved = [name for name in documented if registry.get_function(name) is None]

    assert unresolved == [], (
        f"documented in EXECUTOR_REGISTRY.md but not resolvable via build_registry(): "
        f"{unresolved}. Add the missing executor(s) to the appropriate *_EXECUTORS tuple in "
        "egpt_retail/app/components.py and include that tuple in build_registry()'s sum."
    )
