"""Per-tenant token budget and request-rate limiting (EGPT-3.6).

Cost control has to be enforced by the platform. Provider-side per-key limits are per *org*, not
per tenant, so a single noisy tenant on a shared key exhausts the quota for everyone else and
nothing in this tree notices.

This module is the mechanism only -- counters, windows and a verdict. It deliberately holds no
knowledge of models, providers or requests, so it is testable without either, and the seam
(``providers/budget_provider.py``) and the chokepoint (``models/base.py``) both consume it rather
than reimplementing counting.

Three properties are load-bearing:

* **Fail open on a store outage, closed on a real over-limit.** A Redis blip must not take the
  deployment offline; an actual over-budget reading must deny. Those are different conditions and
  are handled separately -- see ``BudgetEnforcer.check``.

* **The clock is injected.** Window arithmetic is the whole behaviour here, and testing it against
  ``time.time()`` means either real sleeps or a global patch. No fake clock existed in this tree
  (the one time-sensitive test sleeps for real), so this establishes the convention: a keyword-only
  ``clock: Callable[[], float]`` constructor argument, never a module-level patch.

* **Reserve then reconcile.** A token count is not knowable before the call, so the enforcer
  charges an estimate up front and settles against real usage afterwards. That bounds overshoot to
  one request's estimation error, which is stated rather than hidden -- ``utils/tokens.py`` falls
  back to ``len/4`` when no tokenizer matches, so exactness would need the provider round-trip this
  design explicitly avoids.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Protocol, Tuple, runtime_checkable

from egpt.utils.log import log_debug, log_warning

__all__ = [
    "BudgetDecision",
    "BudgetEnforcer",
    "BudgetLimits",
    "CounterStore",
    "InMemoryCounterStore",
    "RedisCounterStore",
    "WINDOW_SECONDS",
]

#: Window names and their length in seconds. A fixed set rather than a free-form duration string:
#: the counter key embeds the window, so an operator inventing "90m" would silently start a new
#: counter series rather than being told the value is unsupported.
WINDOW_SECONDS: Dict[str, int] = {
    "minute": 60,
    "hour": 3_600,
    "day": 86_400,
}


@dataclass(frozen=True)
class BudgetLimits:
    """What a tenant may spend, per window. ``None`` on a field means "no limit on that axis".

    Frozen because a limit set is configuration, and a mutable one shared across concurrent
    requests is the same class of bug as a shared mutable ``Model`` -- one request's adjustment
    becoming another's ceiling.

    ``max_tokens`` counts input + output together. Splitting them was considered and rejected for
    v1: the two are priced differently per provider, so a split limit only becomes meaningful
    alongside real cost computation, which is EGPT-1.x/#163's job rather than this ticket's.
    """

    max_tokens: Optional[int] = None
    max_requests: Optional[int] = None
    window: str = "hour"

    def __post_init__(self) -> None:
        if self.window not in WINDOW_SECONDS:
            raise ValueError(f"Unknown budget window {self.window!r}. Supported: {', '.join(sorted(WINDOW_SECONDS))}.")
        for name, value in (("max_tokens", self.max_tokens), ("max_requests", self.max_requests)):
            if value is not None and value <= 0:
                # Zero would mean "deny everything", which is expressible but is almost always a
                # config mistake rather than an intent -- and an operator who means it can remove
                # the tenant's policy instead. Negative is meaningless.
                raise ValueError(f"{name} must be a positive integer or None, got {value!r}.")

    @property
    def enabled(self) -> bool:
        """Whether this limit set constrains anything at all.

        The hot path checks this before touching a store: a policy carrying a ``BudgetLimits`` with
        both axes ``None`` must cost nothing beyond the attribute read.
        """
        return self.max_tokens is not None or self.max_requests is not None

    @property
    def window_seconds(self) -> int:
        return WINDOW_SECONDS[self.window]


@dataclass(frozen=True)
class BudgetDecision:
    """The verdict for one check, and why.

    Carries ``reason`` in the same shape as EGPT-3.5's ``EscalationRule`` return value -- a string
    naming the rule that fired, or ``None`` for "nothing fired" -- so the two rule sets read alike
    and a log line reporting either is written by whichever rule caused it.
    """

    allowed: bool
    reason: Optional[str] = None
    limit: Optional[int] = None
    used: Optional[int] = None
    window: Optional[str] = None
    retry_after: Optional[int] = None

    @property
    def denied(self) -> bool:
        return not self.allowed


@runtime_checkable
class CounterStore(Protocol):
    """Where per-tenant counters live.

    A ``Protocol`` rather than an ABC, matching ``ModelProvider``: a deployment substituting its own
    store (Memcached, a DB table, a hosted quota service) should need no inheritance, and this
    module must not import any backend to type-check the call.

    ``increment`` returns the value *after* the increment and must be atomic with respect to
    concurrent callers -- that is the whole contract. A read-then-write implementation is wrong
    under any concurrency, which is why there is no separate ``get``/``set`` pair to misuse.
    """

    def increment(self, key: str, amount: int, ttl_seconds: int) -> int:
        """Add ``amount`` to ``key``, returning the new total. Sets ``ttl_seconds`` on first write."""
        ...

    def get(self, key: str) -> int:
        """Current value, or 0 when absent or expired."""
        ...

    def decrement(self, key: str, amount: int) -> int:
        """Subtract ``amount``, returning the new total. Used by reconciliation to settle an
        over-reservation; must not create the key if it is absent, and must not go below zero."""
        ...


class InMemoryCounterStore:
    """Process-local counters. The default, and honest about what that means.

    **The effective limit is per worker.** With N uvicorn workers a tenant configured for 1000
    tokens/hour gets N×1000, because each worker counts independently. That is a real limitation
    rather than an approximation, which is why ``BudgetEnforcer`` warns at construction when it is
    used, and why ``RedisCounterStore`` exists.

    Chosen as the default anyway: a budget that works out of the box with no infrastructure is
    worth more than one that is exact but off by default, and the single-worker deployment -- which
    is every local run and many small ones -- is exact here.

    Windowing is by key expiry rather than a sliding log: a fixed window is one integer per tenant
    per window, where a sliding window is a timestamp list whose memory grows with traffic. The
    tradeoff is boundary burstiness -- a tenant can spend its whole allowance at the end of one
    window and again at the start of the next -- which is bounded and acceptable for spend control,
    where the goal is a monthly bill rather than smooth pacing.
    """

    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        # key -> (value, expires_at). A plain dict under a lock rather than per-key locks: the
        # critical section is a few arithmetic ops, so lock contention is cheaper than the
        # bookkeeping of a lock-per-key map that also has to be pruned.
        self._counters: Dict[str, Tuple[int, float]] = {}
        self._lock = threading.Lock()

    def increment(self, key: str, amount: int, ttl_seconds: int) -> int:
        now = self._clock()
        with self._lock:
            value, expires_at = self._counters.get(key, (0, 0.0))
            if expires_at <= now:
                # Expired or absent: start a fresh window. The TTL is set from *now* rather than
                # extended, so a window is a fixed span from its first write.
                value, expires_at = 0, now + ttl_seconds
            value += amount
            self._counters[key] = (value, expires_at)
            self._maybe_prune(now)
            return value

    def get(self, key: str) -> int:
        now = self._clock()
        with self._lock:
            value, expires_at = self._counters.get(key, (0, 0.0))
            return value if expires_at > now else 0

    def decrement(self, key: str, amount: int) -> int:
        now = self._clock()
        with self._lock:
            existing = self._counters.get(key)
            if existing is None:
                # Never create on decrement: reconciliation settling a reservation whose window has
                # already rolled over must not resurrect the old window with a negative-ish value.
                return 0
            value, expires_at = existing
            if expires_at <= now:
                return 0
            # Clamped at zero: a reconciliation that over-refunds (an estimate larger than actual
            # usage, which is the common direction) must not leave a negative counter that would
            # then grant free headroom in the remainder of the window.
            value = max(0, value - amount)
            self._counters[key] = (value, expires_at)
            return value

    def _maybe_prune(self, now: float) -> None:
        """Drop expired keys occasionally.

        Called under the lock from ``increment``. Without this the dict grows one entry per tenant
        per window forever -- a slow leak proportional to tenant count, invisible until it is not.
        Pruning on a size threshold rather than every call keeps the hot path O(1) amortized.
        """
        if len(self._counters) < 1_024:
            return
        expired = [k for k, (_, exp) in self._counters.items() if exp <= now]
        for k in expired:
            del self._counters[k]


class BudgetEnforcer:
    """Decides whether a tenant may spend, and records what it spent.

    Holds no policy of its own: limits are passed per call, because they come from the tenant's
    ``ModelPolicy`` and a long-lived enforcer must not cache a value an operator has since changed.

    Not a provider and not a model. This is the shared mechanism both the seam and the chokepoint
    call, so that "am I over budget" has exactly one implementation and the two enforcement points
    cannot disagree.
    """

    #: Counter key prefix, versioned so a future change to the key shape starts a new series
    #: rather than silently reinterpreting counters written by an older build.
    KEY_PREFIX = "egpt:budget:v1"

    def __init__(
        self,
        store: Optional[CounterStore] = None,
        *,
        clock: Callable[[], float] = time.time,
        warn_on_in_memory: bool = True,
    ) -> None:
        self._clock = clock
        if store is None:
            store = InMemoryCounterStore(clock=clock)
            if warn_on_in_memory:
                # Warned at construction, not per request: an operator who configured a budget and
                # is running multiple workers is getting N× their intended limit, and the only
                # honest place to say so is once, loudly, at startup.
                log_warning(
                    "Per-tenant budget is using the in-memory counter store, so limits are "
                    "enforced per worker process: with N workers the effective limit is N times "
                    "the configured value. Configure a shared store (RedisCounterStore) for "
                    "accurate multi-worker enforcement."
                )
        self._store = store

    # -- keys ---------------------------------------------------------------

    def _key(self, tenant_id: str, axis: str, window: str, agent_id: Optional[str] = None) -> str:
        """One counter per (tenant, agent, axis, window).

        The window is part of the key so switching a tenant from hourly to daily starts a fresh
        counter instead of reading an hourly total against a daily limit.

        ``agent_id`` is part of it because AC3 asks for attribution "per tenant **and per agent**":
        without it a tenant's total is knowable but the breakdown is not, so an operator cannot
        tell which agent consumed the budget. ``None`` keeps the tenant-wide key shape, so a
        deployment that does not attribute per agent reads exactly the counter it always did.
        """
        if agent_id is None:
            return f"{self.KEY_PREFIX}:{tenant_id}:{axis}:{window}"
        return f"{self.KEY_PREFIX}:{tenant_id}:agent:{agent_id}:{axis}:{window}"

    # -- decisions ----------------------------------------------------------

    def check(self, tenant_id: Optional[str], limits: BudgetLimits, *, estimated_tokens: int = 0) -> BudgetDecision:
        """Whether this request may proceed, without charging for it.

        Returns allowed for an untenanted request (``tenant_id`` is None) and for a limit set that
        constrains nothing -- both before any store access, which is what keeps the no-budget path
        free.

        **Fails open on a store error.** A counter outage is not evidence of over-spend, and
        denying every request because Redis is unreachable converts a metrics problem into an
        outage. The failure is logged; a genuine over-limit reading still denies.
        """
        if tenant_id is None or not limits.enabled:
            return BudgetDecision(allowed=True)

        try:
            if limits.max_requests is not None:
                used = self._store.get(self._key(tenant_id, "requests", limits.window))
                if used >= limits.max_requests:
                    return BudgetDecision(
                        allowed=False,
                        reason="request_rate_exceeded",
                        limit=limits.max_requests,
                        used=used,
                        window=limits.window,
                        retry_after=limits.window_seconds,
                    )

            if limits.max_tokens is not None:
                used = self._store.get(self._key(tenant_id, "tokens", limits.window))
                # `>=` on the used total, then the estimate on top: a tenant already at its ceiling
                # is denied even for a zero-token request, and one whose estimate would cross the
                # ceiling is denied before spending rather than after.
                if used >= limits.max_tokens or used + max(0, estimated_tokens) > limits.max_tokens:
                    return BudgetDecision(
                        allowed=False,
                        reason="token_budget_exceeded",
                        limit=limits.max_tokens,
                        used=used,
                        window=limits.window,
                        retry_after=limits.window_seconds,
                    )
        except Exception as e:
            log_warning(f"Budget check failed against the counter store; allowing the request: {e}")
            return BudgetDecision(allowed=True, reason="store_unavailable")

        return BudgetDecision(allowed=True)

    def reserve(
        self,
        tenant_id: Optional[str],
        limits: BudgetLimits,
        *,
        estimated_tokens: int,
        agent_id: Optional[str] = None,
    ) -> None:
        """Charge the estimate up front.

        Paired with :meth:`reconcile`. Charging before the call rather than after is what makes
        concurrent requests from one tenant sum correctly -- post-only accounting lets N in-flight
        requests each see the same pre-call total and collectively overshoot by N-1 requests.
        """
        if tenant_id is None or not limits.enabled:
            return
        try:
            if limits.max_requests is not None:
                self._store.increment(self._key(tenant_id, "requests", limits.window), 1, limits.window_seconds)
            if limits.max_tokens is not None and estimated_tokens > 0:
                self._store.increment(
                    self._key(tenant_id, "tokens", limits.window), estimated_tokens, limits.window_seconds
                )
            # Per-agent counters are *attribution*, not a second ceiling: the limit is the
            # tenant's, and an agent-scoped counter answers "which agent spent it". Charged
            # alongside rather than instead, so the enforcement axis is unchanged.
            if agent_id is not None:
                if limits.max_requests is not None:
                    self._store.increment(
                        self._key(tenant_id, "requests", limits.window, agent_id), 1, limits.window_seconds
                    )
                if limits.max_tokens is not None and estimated_tokens > 0:
                    self._store.increment(
                        self._key(tenant_id, "tokens", limits.window, agent_id),
                        estimated_tokens,
                        limits.window_seconds,
                    )
        except Exception as e:
            # Fail open, same rationale as `check`: a store that cannot record is a reporting
            # problem, and refusing traffic over it is worse than under-counting.
            log_warning(f"Budget reservation failed against the counter store; not charging: {e}")

    def reconcile(
        self,
        tenant_id: Optional[str],
        limits: BudgetLimits,
        *,
        estimated_tokens: int,
        actual_tokens: int,
        agent_id: Optional[str] = None,
    ) -> None:
        """Settle a reservation against real usage.

        Adjusts by the difference rather than rewriting the total, so a concurrent request's charge
        landing between reserve and reconcile is not clobbered. An over-estimate refunds, an
        under-estimate charges the remainder -- the latter can push a tenant past its ceiling by
        one request, which is the bounded overshoot this design accepts and documents.

        Settles **both axes ``reserve`` charged.** ``reserve`` writes a per-agent counter alongside
        the tenant one and this method used to settle only the tenant, so the per-agent figure was
        frozen at whatever the estimate happened to be -- forever, since nothing else ever wrote it.
        A three-turn run showed 2100 tokens against the tenant and 100 against the agent that spent
        them. That defeats AC3, whose whole purpose is answering "which agent spent it", and it is
        exactly the axis an operator would consult to *detect* a mis-count on the other one.
        """
        if tenant_id is None or not limits.enabled or limits.max_tokens is None:
            return
        delta = actual_tokens - estimated_tokens
        if delta == 0:
            return
        keys = [self._key(tenant_id, "tokens", limits.window)]
        if agent_id is not None:
            keys.append(self._key(tenant_id, "tokens", limits.window, agent_id))
        try:
            for key in keys:
                if delta > 0:
                    self._store.increment(key, delta, limits.window_seconds)
                else:
                    self._store.decrement(key, -delta)
            log_debug(f"Budget reconciled for tenant {tenant_id}: estimate {estimated_tokens}, actual {actual_tokens}")
        except Exception as e:
            log_warning(f"Budget reconciliation failed against the counter store: {e}")

    # -- introspection ------------------------------------------------------

    def usage(self, tenant_id: str, limits: BudgetLimits, *, agent_id: Optional[str] = None) -> Dict[str, int]:
        """Current counters for a tenant, or for one agent within it (AC3).

        For diagnostics and reporting, not the hot path.
        """
        return {
            "tokens": self._store.get(self._key(tenant_id, "tokens", limits.window, agent_id)),
            "requests": self._store.get(self._key(tenant_id, "requests", limits.window, agent_id)),
        }


class RedisCounterStore:
    """Counters in Redis, shared across workers (EGPT-3.6 unit 7 -- AC6).

    The reason this exists: ``InMemoryCounterStore`` counts per *process*, so with N uvicorn
    workers a tenant configured for 1000 tokens/hour actually gets N×1000. That is not an
    approximation to live with -- it is the difference between a budget and a suggestion -- so
    accurate multi-worker enforcement needs one counter every worker can see.

    ``INCRBY`` is atomic server-side, which is the whole reason Redis is the right shape here: the
    increment and the read are one round-trip and one operation, so two workers incrementing
    concurrently cannot lose an update. The in-memory store achieves the same property with a lock;
    neither can be implemented as read-then-write.

    The TTL is set only when the key is created (``INCRBY`` then ``EXPIRE`` under ``NX``-like
    semantics via a pipeline check): extending it on every write would turn a fixed window into a
    sliding one that never expires under sustained traffic, so a tenant that keeps spending would
    never get its allowance back.

    ``redis`` is an optional dependency. It is imported inside ``__init__`` so that importing this
    module -- which every budget-configured deployment does -- costs nothing for the majority that
    use the in-memory default.
    """

    def __init__(self, url: Optional[str] = None, *, client: Optional[Any] = None) -> None:
        if client is not None:
            self._redis = client
        else:
            try:
                import redis  # noqa: PLC0415
            except ImportError as e:
                raise ImportError(
                    "RedisCounterStore needs the `redis` package. Install it with "
                    "`pip install redis`, or leave EGPT_BUDGET_REDIS_URL unset to use the "
                    "in-memory store (which enforces per worker process)."
                ) from e
            self._redis = redis.Redis.from_url(url or "redis://localhost:6379/0")

    def increment(self, key: str, amount: int, ttl_seconds: int) -> int:
        new_value = int(self._redis.incrby(key, amount))
        # Only on creation: `incrby` returns exactly `amount` when the key did not exist. Setting
        # the TTL on every write would keep pushing expiry forward, so a continuously-spending
        # tenant's window would never roll over and its allowance would never come back.
        if new_value == amount:
            self._redis.expire(key, ttl_seconds)
        return new_value

    def get(self, key: str) -> int:
        value = self._redis.get(key)
        return int(value) if value is not None else 0

    def decrement(self, key: str, amount: int) -> int:
        """Refund an over-reservation, never creating the key and never going below zero.

        Guarded by an existence check rather than a bare ``DECRBY``: ``DECRBY`` on a missing key
        creates it at a negative value, which would grant free headroom for the rest of the window
        -- and reconciling after a window rolls over is exactly when the key is missing.
        """
        # Best-effort atomicity without EVAL. The read-modify-write version
        # (`exists` -> `decrby` -> `set`) left two windows for a concurrent refund to interleave and
        # could expose a negative intermediate to a concurrent reader.
        #
        # `EVAL` would make this genuinely atomic and is the right answer against a real Redis, but
        # it is deliberately not used: `fakeredis` only implements it with the optional `lua` extra,
        # so the store would be untestable in CI on the dependency the tests already declare. A
        # correctness property that cannot be tested is worth less than a slightly weaker one that
        # can.
        #
        # So: a single pipeline, which Redis executes without interleaving other clients' commands,
        # and a clamp that is idempotent -- two concurrent refunds may both clamp to 0, which is the
        # same result either order. The residual race is an *over*-refund (a counter floored at 0
        # rather than a partial value), which grants at most one window's headroom and never
        # over-charges a tenant.
        if not self._redis.exists(key):
            return 0
        pipe = self._redis.pipeline()
        pipe.decrby(key, amount)
        pipe.get(key)
        results = pipe.execute()
        new_value = int(results[0])
        if new_value < 0:
            self._redis.set(key, 0, keepttl=True)
            return 0
        return new_value
