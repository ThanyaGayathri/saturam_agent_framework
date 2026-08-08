"""Registry package: the runtime instance catalog and the construction/discovery seam.

``Registry`` is exported lazily (PEP 562). Python runs a package ``__init__`` before any
submodule, so importing it eagerly here would make ``import egpt.registry.discovery`` --
the cheapest module in core, which resolves integrations by name -- drag in ``BaseDb``,
``Model``, ``VectorDb``, ``Toolkit`` and pydantic, and would close an import cycle
(``egpt.db.utils`` -> ``discovery`` -> ``registry`` -> ``egpt.db.base``).

The ``TYPE_CHECKING`` import is not redundant with ``__getattr__``: without it mypy sees a
module-level ``__getattr__`` returning ``Any`` and silently degrades ``Registry`` to
``Any`` at every ``from egpt.registry import Registry`` site.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from egpt.registry.registry import Registry

__all__ = ["Registry"]


def __getattr__(name: str) -> Any:
    if name == "Registry":
        from egpt.registry.registry import Registry

        return Registry
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
