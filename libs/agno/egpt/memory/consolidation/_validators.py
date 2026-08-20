"""Shared validation helper for the consolidation package (SM-L1 #122 / SM-L2 #123).

Extracted after `worker.py` and `triggers.py` each independently defined a `_positive` under the
same name but with different return types (`worker.py`'s returned the validated value; a
`triggers.py` revision returned `None`) -- a silent trap for a future call site in either module
that copies the other's usage pattern (`self.x = _positive(v, "x")`) and gets `None` assigned
instead of `v`. One implementation, imported by both, closes that class of drift for good.
"""

__all__ = ["_positive"]


def _positive(value: float, option: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"{option} must be a positive number, got {value!r}.")
    return value
