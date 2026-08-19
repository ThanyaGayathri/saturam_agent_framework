"""Errors the memory seam raises (SM-C1).

Their own module because ``tiers.py`` needs to raise ``MemoryOperationError`` and ``types.py``
imports ``tiers``, so the error cannot live in ``types``. Both are re-exported from
:mod:`egpt.memory.types`, which stays the import site every other module uses.

``MemoryProviderConfigError`` moved here from ``config.py`` for the same layering reason
(EGPT-1.7c follow-up, aicoe-tracker#180): substrate backends in ``egpt.integrations.memory``
raise it, and ``config.py`` is the composition root -- it imports ``MemoryProvider`` and the
registry -- so a backend importing ``config`` for one error class dragged the whole
composition surface across the integrations boundary. ``config.py`` re-exports it, which
stays the import site for services-layer callers.
"""

from typing import Optional

__all__ = ["MemoryOperationError", "MemoryProviderConfigError", "MemoryScopeRequiredError"]


class MemoryProviderConfigError(ValueError):
    """A memory provider's configuration is invalid or incomplete.

    ``ValueError`` for the same reason ``ModelProviderConfigError`` is one: a deployment mistake
    caught at configuration time should land on the type callers already assert against.

    Deliberately distinct from ``ModelProviderConfigError`` -- that one is about resolving a
    *model*, and collapsing the two would make a bad memory DSN look like a bad model policy in
    every log and every ``except`` clause.
    """

    def __init__(self, message: str, *, field_name: Optional[str] = None) -> None:
        super().__init__(message)
        self.field_name = field_name


class MemoryOperationError(ValueError):
    """A memory call cannot be served as asked.

    A **per-request** refusal by a correctly configured provider, distinct from the construction-
    time ``MemoryProviderConfigError``: conflating them makes a routine bad request look like a
    deployment fault in every log and every ``except`` clause. ``ValueError`` because callers
    already assert against it.
    """

    def __init__(self, message: str, *, field_name: Optional[str] = None) -> None:
        super().__init__(message)
        self.field_name = field_name


class MemoryScopeRequiredError(MemoryOperationError):
    """A memory data-access call arrived without a usable tenant scope (SM-C4 #92).

    Its own type so an operator can alert on unscoped access without matching on message text,
    and so a caller can tell it from the ordinary bad request it would otherwise be indexed under.

    A subclass rather than a sibling of ``Exception``, which is the shape ``db/component_scope.py``
    chose for the same refusal one layer down. The reason they differ: that one is raised beneath
    an HTTP router that maps ``ValueError`` to a 400, and an unscoped call is a server fault rather
    than something the client sent wrong. Nothing routes this seam yet, and every other refusal it
    raises is a ``MemoryOperationError`` -- the swap conformance matrix asserts that type across
    both adapters -- so a refusal outside that taxonomy would be the anomaly. Revisit when memory
    gets an HTTP surface.

    ``verb`` carries the method that refused, structured rather than only interpolated into the
    message. The type alone answers "was anything called unscoped"; paging specifically on an
    unscoped ``consolidate`` needed a regex over the message otherwise, and ``require_scope``
    already has the name in hand at every raise.
    """

    def __init__(self, message: str, *, field_name: Optional[str] = None, verb: str = "") -> None:
        super().__init__(message, field_name=field_name)
        self.verb = verb
