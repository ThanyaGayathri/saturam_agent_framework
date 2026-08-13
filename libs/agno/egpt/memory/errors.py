"""Errors the memory seam raises (SM-C1).

Their own module because ``tiers.py`` needs to raise ``MemoryOperationError`` and ``types.py``
imports ``tiers``, so the error cannot live in ``types``. Both are re-exported from
:mod:`egpt.memory.types`, which stays the import site every other module uses.
"""

from typing import Optional

__all__ = ["MemoryOperationError"]


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
