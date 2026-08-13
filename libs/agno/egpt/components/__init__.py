"""Orchestration-side helpers for DB-defined components.

Two things live here, and both exist because they need the component classes:

* ``spec`` -- the ``ComponentSpec`` factory and the ``validate_component_config`` entry point
  that close the EGPT-2.2 validator's dry-run check.
* ``io`` -- import/seed of portable definition bundles (EGPT-2.8), which binds that validator
  onto the core machinery in ``egpt.registry.io``.

Both were reachable only through ``egpt.os`` before EGPT-2.8. Importing that package runs
``from egpt.os.app import EgptOS``, so anything that wanted to validate or import a definition
had to install FastAPI first -- a real obstacle for a seeding script whose only dependency
should be a database driver.

Nothing is imported eagerly: ``egpt.components.spec`` pulls in ``Agent``, ``Team`` and
``Workflow``, which is a cost a caller that only wants ``egpt.components.io``'s type hints
should not pay at import time.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from egpt.components.io import export_component, import_component, seed_from_directory
    from egpt.components.spec import component_spec_for, validate_component_config

__all__ = [
    "component_spec_for",
    "export_component",
    "import_component",
    "seed_from_directory",
    "validate_component_config",
]

_LAZY = {
    "component_spec_for": "egpt.components.spec",
    "validate_component_config": "egpt.components.spec",
    "export_component": "egpt.components.io",
    "import_component": "egpt.components.io",
    "seed_from_directory": "egpt.components.io",
}


def __getattr__(name: str) -> Any:
    module_path = _LAZY.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from importlib import import_module

    return getattr(import_module(module_path), name)
