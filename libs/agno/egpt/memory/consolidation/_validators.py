"""Shared validation/assertion helpers for the consolidation package (SM-L1 #122 / SM-L2 #123).

Private module (`_` prefix) -- no `__all__`, since nothing outside this package should ever
`from ... import *` a module whose entire contents are private names by construction.

``_positive`` was extracted after `worker.py` and `triggers.py` each independently defined a
function under the same name but with different return types (`worker.py`'s returned the
validated value; a `triggers.py` revision returned `None`) -- a silent trap for a future call
site in either module that copies the other's usage pattern (`self.x = _positive(v, "x")`) and
gets `None` assigned instead of `v`. One implementation, imported by both, closes that class of
drift for good.

``_reject_coroutine`` is the same shape of fix for a second, independently-discovered instance of
the identical class of bug: `worker.py`'s call sites into a caller-supplied
``ConsolidationTrigger`` and `triggers.py`'s `AnyTrigger` fanning out to its own children are both
"call a method the type system says is synchronous, but nothing stops an `async def`
implementation from being handed over" -- and both need the identical runtime guard, not two
hand-maintained copies of it.
"""

import asyncio
from typing import Any


def _positive(value: float, option: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"{option} must be a positive number, got {value!r}.")
    return value


def _reject_coroutine(result: Any, owner: Any, method: str) -> Any:
    """Guards a call into a ``ConsolidationTrigger`` method against an async implementation.

    ``ConsolidationTrigger`` is a synchronous Protocol by design, but nothing at the type level
    stops a caller from handing over one whose methods are ``async def``. Called as a plain
    function -- exactly how every call site using this guard calls it -- Python evaluates the
    *coroutine object* itself, which is always truthy: an async ``should_fire`` that means "not
    due" would silently drain every scope on every tick instead of raising anywhere, and an async
    ``record_event``/``reset`` whose coroutine is simply discarded (never awaited, never run)
    would silently leave that trigger's state never updated at all. Failing loudly here, at the
    one place every trigger call funnels through, turns either into an immediate, actionable
    ``TypeError`` instead.

    Catches ``async def`` coroutine objects only, via ``asyncio.iscoroutine`` -- an
    implementation that returns some *other* awaitable (a bare ``asyncio.Future``/``Task``, or a
    custom object with ``__await__``) instead of using ``async def`` would pass this check
    undetected. Not handled: every trigger built in this package is plain sync, and a caller
    reaching for a raw awaitable instead of ``async def`` to implement ``ConsolidationTrigger`` is
    enough of a deliberate edge case that broadening this check has no real case driving it yet.
    """
    if asyncio.iscoroutine(result):
        result.close()  # avoid a dangling "coroutine was never awaited" warning
        raise TypeError(
            f"{type(owner).__name__}.{method}() returned a coroutine. ConsolidationTrigger is a "
            "synchronous Protocol -- calls are made as plain function calls, never awaited. Do "
            "any real I/O with a blocking client instead of `async def`."
        )
    return result
