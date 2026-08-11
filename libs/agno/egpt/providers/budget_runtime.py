"""Carrying a budget reservation from the seam to the chokepoint (EGPT-3.6 unit 6).

The seam reserves an estimate before the call; the chokepoint settles it against real usage after.
Those are different layers, so something has to carry the reservation between them -- and the two
obvious mechanisms are both wrong here:

* **Not a ContextVar.** ``factory/utils.py`` rejects exactly that on exactly this boundary, and
  gives the reason: Agent dispatches tool calls onto a ``ThreadPoolExecutor``, and an ambient value
  on a tenancy boundary that survives an executor handoff is a cross-tenant leak.

* **Not an attribute on the Model.** ``agent/_utils.py`` shares ``model`` by reference in
  ``deep_copy``, so concurrent requests hold the same object and would overwrite each other's
  reservation -- the same class of bug the escalation work found in ``agent.model``.

What is left is a keyed side-table on the **resolved model instance**.

That needs justifying, because "do not stamp the Model" is one of the constraints above. The
distinction is *which* model: ``agent.model`` is assigned once and shared by reference through
``deep_copy`` (``agent/_utils.py`` lists it under "share heavy resources"), so stamping *that* would
leak across concurrent requests. The model a provider **returns from a resolve** is different -- every
in-tree provider builds it fresh via ``_construct`` per call, verified distinct per resolve -- so it
is already per-request state and is exactly the object the chokepoint has in hand.

An earlier version keyed on the message-metrics object instead, and it never fired: ``Model`` has no
``metrics`` attribute at all, so the lookup returned ``None`` and ``record_reservation`` was never
called. Nothing reconciled on any path, and a 1300-token call left the counter at the 120-token
estimate -- a ~10x under-count that grows with every request. The unit tests passed because they
called ``record_reservation`` directly, which proved the mechanism and assumed the wiring.

**The settlement contract is per-turn, not cumulative**, and getting that wrong produced the same
under-count twice more (see ``reconcile_reservation``). The authority is ``models/base.py``: its tool
loop is a ``while True:`` that constructs a fresh ``Message`` -- and so a fresh ``MessageMetrics`` --
on every iteration, and hands *that* to the chokepoint. So each settle reports one call's usage,
never a running total. Anything in this module that assumes otherwise silently charges one turn per
run. The cumulative figures live on ``run_response.metrics`` via ``accumulate_model_metrics`` and
never reach here.
"""

from __future__ import annotations

from typing import Any, Dict, List, NamedTuple, Optional
from weakref import finalize

from egpt.utils.log import log_debug

__all__ = ["BudgetReservation", "reconcile_reservation", "record_reservation"]


class BudgetReservation(NamedTuple):
    """What was charged up front, and everything needed to settle it."""

    tenant_id: str
    estimated_tokens: int
    enforcer: Any  # BudgetEnforcer; typed loosely so this module imports no provider
    limits: Any  # BudgetLimits
    agent_id: Optional[str] = None
    #: Running log of what this reservation's turns have actually charged. Purely informational --
    #: it is NOT the base for the next turn's delta. See ``reconcile_reservation`` for why: turns
    #: report independent per-call totals, not a cumulative running total, so a delta scheme
    #: charges nothing after the first turn.
    settled_tokens: int = 0
    #: The ``MessageMetrics`` objects this reservation has already settled. Each turn of a
    #: tool-calling loop builds a *fresh* metrics object, so object identity distinguishes "a second
    #: turn" (must charge) from "the same turn settled twice" (must not) -- something comparing token
    #: totals cannot do, since two turns may legitimately report the same numbers.
    #:
    #: Holds **strong references**, not ``id()`` values. ``id()`` was tried and is unsound here for
    #: the same reason the module warns about below: a turn's metrics object can be collected once
    #: the next turn replaces its ``Message``, and CPython promptly reuses the address. A three-turn
    #: run then charged only twice, because turn 3 landed on turn 2's recycled id and was dropped as
    #: a replay. The references are released when the reservation is (the model's finalizer drops
    #: the whole entry), so the retention is bounded by one run.
    #:
    #: Mutated in place rather than rebound, so the NamedTuple stays immutable in the way that
    #: matters (its identity fields). ``None`` means "not yet settling"; ``record_reservation``
    #: installs a fresh list, so no default is ever shared between reservations.
    settled_metrics: Optional[List[Any]] = None


#: id(resolved model) -> reservation, with a weakref finalizer per entry so an abandoned
#: reservation (a call that raised before usage landed) is dropped when the model is collected.
#:
#: Keyed by ``id()`` rather than by the object, because the natural alternative -- a
#: ``WeakKeyDictionary`` -- requires a hashable key, and the object this was first keyed on
#: (``MessageMetrics``, a ``dataclass`` with ``eq=True``) is **unhashable**, raising ``TypeError``.
#: That failure was silent in the first cut of this module: the reservation was never stored, so
#: reconciliation never ran and every call was charged its estimate forever.
#:
#: ``id()`` alone would be unsound (ids are reused after collection), which is exactly what the
#: finalizer prevents: the entry is removed the moment the metrics object dies, so a reused id
#: cannot collide with a stale reservation.
_RESERVATIONS: Dict[int, BudgetReservation] = {}


def record_reservation(owner: Any, reservation: BudgetReservation) -> None:
    """Associate a reservation with the object that owns it for the life of the run.

    ``owner`` is the **resolved model** as the seam calls it -- the parameter was named ``metrics``
    when the note was keyed on a message-metrics object, and kept that name after the key moved,
    which made the signature describe something it no longer took. Named for the role rather than
    one caller's type: what matters is that the object is per-request and outlives the call.
    """
    key = id(owner)
    try:
        finalize(owner, _RESERVATIONS.pop, key, None)
    except TypeError:
        # Not weak-referenceable at all. Losing the settlement is a bounded accounting error;
        # raising here would fail a call over bookkeeping.
        log_debug("Metrics object is not weak-referenceable; budget reservation will not reconcile")
        return
    _RESERVATIONS[key] = reservation._replace(settled_metrics=[])


def reconcile_reservation(metrics: Any, *, model: Any) -> Optional[BudgetReservation]:
    """Settle the reservation for this call against its final token counts, if there is one.

    ``model`` is where the reservation actually lives -- the seam records it against the model it
    resolved, which is the only object shared between the two layers. ``metrics`` supplies the final
    token counts.

    Returns the reservation that was settled, for callers that want to log or assert on it.
    """
    # Looked up, NOT popped. The seam resolves once per *run* but the model is invoked once per
    # *turn* -- a tool-calling loop calls it repeatedly against one resolution. Popping settled the
    # first turn and silently dropped every later one, so a three-turn run of 700 real tokens each
    # counted 700 against 2100 actual: a tool-using agent, which is the common case for this
    # framework, had its usage attributed at roughly 1/N. That undercuts AC1 (the tenant gets ~N
    # times its budget) and AC3 (attribution wrong by the same factor).
    # Model-keyed only. There was a metrics-keyed fallback, and its sole caller was the test
    # suite -- so production carried a branch nothing in `egpt/` reached, while the tests that used
    # it read as coverage of a path that never ran. That is exactly the blind spot which produced
    # the original "settlement never fires" bug, reconstructed one layer up, so the branch is gone
    # and the tests drive the real API.
    if model is None:
        return None
    key = id(model)
    reservation = _RESERVATIONS.get(key)
    if reservation is None:
        return None

    # Each turn charges its OWN actual, in full; the up-front estimate is netted out exactly once,
    # on the first settle.
    #
    # An earlier version treated `actual` as a cumulative running total and charged the delta
    # against what had been settled so far. That is the wrong contract: `models/base.py` runs the
    # tool loop as `while True:` and builds a *fresh* `Message` -- hence a fresh `MessageMetrics` --
    # inside the loop on every iteration, so each turn reports only its own call. The cumulative
    # totals do exist, but `accumulate_model_metrics` puts them on `run_response.metrics`, which
    # this function never sees. So turn 2 arrived with actual == turn 1's actual, the delta was
    # zero, and nothing was charged: three 700-token turns still counted 700 against 2100. Same
    # ~1/N under-count as the popping bug it replaced, differently shaped.
    settled = reservation.settled_metrics
    if settled is None:
        settled = []
        reservation = reservation._replace(settled_metrics=settled)
        _RESERVATIONS[key] = reservation

    # Idempotency is keyed on the identity of the metrics object, not on its totals. Two turns may
    # legitimately report identical numbers, so "same totals" cannot distinguish a second turn from
    # a double-settle of one turn; object identity can, because every turn gets a fresh object.
    #
    # `is` rather than `in`: `MessageMetrics` is a dataclass with `eq=True`, so `in` compares by
    # value and would treat a second turn reporting the same tokens as an already-settled one --
    # reintroducing the very under-count this replaced.
    if any(seen is metrics for seen in settled):
        return reservation

    actual = (getattr(metrics, "input_tokens", 0) or 0) + (getattr(metrics, "output_tokens", 0) or 0)
    try:
        # The estimate is netted out on the first settle only; later turns net out nothing, so each
        # adds its full actual and N turns sum to N turns' usage.
        first_settle = not settled
        reservation.enforcer.reconcile(
            reservation.tenant_id,
            reservation.limits,
            estimated_tokens=reservation.estimated_tokens if first_settle else 0,
            actual_tokens=actual,
            # Settles the per-agent counter too. `reserve` charged both axes, so omitting this left
            # the agent figure stuck at the estimate while the tenant figure was correct.
            agent_id=reservation.agent_id,
        )
        settled.append(metrics)
        # A running log of what has actually been charged, for `usage_by_tenant` and for debugging.
        # Not the base for the next turn's charge -- see above.
        reservation = reservation._replace(settled_tokens=reservation.settled_tokens + actual)
        _RESERVATIONS[key] = reservation
    except Exception as e:
        # Reconciliation is accounting, not serving: a store failure here must not fail a call
        # that already succeeded. The reservation stands, which over-counts rather than under --
        # the safe direction for a spend ceiling.
        log_debug(f"Budget reconciliation failed; the reservation stands: {e}")
    return reservation


def usage_by_tenant(metrics: Any, *, model: Any = None) -> Dict[str, Any]:
    """Per-tenant attribution for one call (AC3), or an empty mapping when untenanted.

    Reports tokens *and* cost: ``MessageMetrics.cost`` is populated by aicoe-tracker#163, so the
    cost half of "usage and cost are attributed per tenant" has a real source rather than a None.
    """
    reservation = _RESERVATIONS.get(id(model)) if model is not None else None
    if reservation is None:
        return {}
    return {
        "tenant_id": reservation.tenant_id,
        "agent_id": reservation.agent_id,
        "input_tokens": getattr(metrics, "input_tokens", 0) or 0,
        "output_tokens": getattr(metrics, "output_tokens", 0) or 0,
        "cost": getattr(metrics, "cost", None),
    }
