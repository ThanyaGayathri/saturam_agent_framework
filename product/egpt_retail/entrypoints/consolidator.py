"""``egpt-retail consolidator`` -- drain the configured customer's memory scopes.

R1's evidence line is *"the consolidator logs a clean no-work exit rather than an error"*, and
no existing code can produce it: with an empty scope list ``ConsolidationWorker.run_once`` returns
``[]`` and logs nothing at any level (``memory/consolidation/worker.py:191-213``), and ``start``/
``stop`` log two lines regardless of work done. The summary lines here are new, and they are the
caller's to write -- the worker does not know what "no work" means for this deployment.

Two shapes, both exit 0, both greppable, deliberately distinguishable:

    consolidator: no scopes configured -- EGPT_RETAIL_CUSTOMER_SLUG is unset. Nothing to consolidate.
    consolidator: tick complete -- 1 scope(s) checked, 0 with work, 1 pass(es), 0 error(s) in 0.04s

The provider is built **lazily, and not at all when there is nothing to do**:
``build_memory_provider_for`` opens the pgvector engine and provisions its schema, and the slug-
unset case must be provable against nothing but a log line. In loop mode a ``SIGTERM`` handler
awaits ``worker.stop()``, which joins the in-flight tick within ``stop_timeout`` (30s) -- the pod's
``terminationGracePeriodSeconds`` must exceed that or Kubernetes kills a pass mid-way.

**Single instance, enforced.** The worker is idempotent by upsert-on-content-id, so two replicas
never corrupt state, but they redo the same window instead of splitting it. A Postgres advisory
lock on a **dedicated session held for the process lifetime** turns "pin to one replica" from a
chart comment into a refusal: the second instance logs who holds the lock and exits 3. The session
is opened directly to Postgres, bypassing any pool -- through a transaction-mode pooler the session
would end with the first transaction and the lock would silently evaporate.

**Loop mode reports tick errors only in the log, never in the exit code.** ``--once`` maps an
exhausted-retries outcome to ``EXIT_TICK_ERRORS``, but in loop mode ``ConsolidationWorker._loop``
logs and continues and the process exits 0 on SIGTERM regardless of how many ticks failed, so a
health signal for the long-running deployment has to come from the log, not from the exit code.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import time
from typing import Any, List, Optional, Sequence

from egpt.memory.config import build_memory_provider_for
from egpt.memory.consolidation.worker import ConsolidationOutcome, ConsolidationWorker

from egpt_retail.app.components import MEMORY_WRITING_AGENT_IDS
from egpt_retail.app.db import psycopg_dsn
from egpt_retail.app.settings import RetailSettings
from egpt_retail.memory.config import build_memory_resolver
from egpt_retail.memory.scopes import (
    build_consolidation_scopes,
    consolidation_scope_source,
)

__all__ = ["main", "amain", "summarise", "EXIT_LOCK_HELD"]

log = logging.getLogger("egpt_retail.consolidator")

#: Exit status when another consolidator already holds this customer's lock.
EXIT_LOCK_HELD = 3
#: Exit status when a tick finished but at least one scope exhausted its retries.
EXIT_TICK_ERRORS = 1

_LOCK_NAMESPACE = "egpt-retail:consolidator"


def summarise(outcomes: Sequence[ConsolidationOutcome], elapsed: float) -> str:
    """The one line the package asks for, computed from what the worker returns."""
    checked = len(outcomes)
    with_work = sum(
        1 for o in outcomes if any(getattr(r, "episodic_scanned", 0) for r in o.reports)
    )
    passes = sum(len(o.reports) for o in outcomes)
    errors = sum(1 for o in outcomes if o.error is not None)
    return (
        f"consolidator: tick complete -- {checked} scope(s) checked, {with_work} with work, "
        f"{passes} pass(es), {errors} error(s) in {elapsed:.2f}s"
    )


class _AdvisoryLock:
    """``pg_try_advisory_lock`` on a dedicated psycopg connection, held until ``release``."""

    def __init__(self, dsn: str, key: str) -> None:
        self._dsn = dsn
        self._key = key
        self._conn: Optional[Any] = None
        self._held = False

    def acquire(self) -> bool:
        import psycopg

        self._conn = psycopg.connect(self._dsn, autocommit=True)
        with self._conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (self._key,))
            row = cur.fetchone()
        self._held = bool(row and row[0])
        return self._held

    def release(self) -> None:
        if self._conn is not None:
            try:
                if self._held:
                    with self._conn.cursor() as cur:
                        cur.execute(
                            "SELECT pg_advisory_unlock(hashtext(%s))", (self._key,)
                        )
                    self._held = False
            finally:
                self._conn.close()
                self._conn = None


async def _run_loop(worker: ConsolidationWorker) -> None:
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    worker.start()
    log.info("consolidator: running; SIGTERM drains the in-flight tick and exits")
    await stop.wait()
    log.info("consolidator: stopping")
    await worker.stop()


async def amain(once: bool = False, poll_interval: Optional[float] = None) -> int:
    settings = RetailSettings()
    interval = (
        poll_interval
        if poll_interval is not None
        else settings.retail_consolidation_poll_interval
    )
    slug = (settings.retail_customer_slug or "").strip() or None
    log.info(
        "consolidator: starting (slug=%s, mode=%s, poll_interval=%.1fs, agents=%s)",
        slug or "<unset>",
        "once" if once else "loop",
        interval,
        ",".join(MEMORY_WRITING_AGENT_IDS),
    )

    scopes = build_consolidation_scopes(settings, MEMORY_WRITING_AGENT_IDS)
    if not scopes:
        log.info(
            "consolidator: no scopes configured -- EGPT_RETAIL_CUSTOMER_SLUG is unset. Nothing to consolidate. Exiting 0."
        )
        return 0
    assert slug is not None
    log.info(
        "consolidator: %d scope(s) configured: %s",
        len(scopes),
        ", ".join(s.key() for s in scopes),
    )

    lock = _AdvisoryLock(
        psycopg_dsn(settings.db_direct_url), f"{_LOCK_NAMESPACE}:{slug}"
    )
    if not lock.acquire():
        log.error(
            "consolidator: another consolidator holds the lock for customer %r; this instance exits %d. "
            "One replica per customer -- scale the API, not this.",
            slug,
            EXIT_LOCK_HELD,
        )
        lock.release()
        return EXIT_LOCK_HELD

    try:
        # Built once, here, and only now that there is work to do and this is the instance doing it.
        provider = build_memory_provider_for(
            build_memory_resolver(settings), tenant_id=slug
        )
        try:
            worker = ConsolidationWorker(
                provider,
                consolidation_scope_source(settings, MEMORY_WRITING_AGENT_IDS),
                poll_interval=interval,
                max_passes_per_scope=settings.retail_consolidation_max_passes,
            )
            if once:
                started = time.monotonic()
                outcomes: List[ConsolidationOutcome] = list(await worker.run_once())
                log.info(summarise(outcomes, time.monotonic() - started))
                for outcome in outcomes:
                    if outcome.error is not None:
                        log.error(
                            "consolidator: scope %s failed after retries: %s",
                            outcome.scope.key(),
                            outcome.error,
                        )
                log.info(
                    "consolidator: exiting %d",
                    EXIT_TICK_ERRORS if any(o.error for o in outcomes) else 0,
                )
                return EXIT_TICK_ERRORS if any(o.error for o in outcomes) else 0
            await _run_loop(worker)
            log.info("consolidator: exiting 0")
            return 0
        finally:
            await provider.aclose()
    finally:
        lock.release()


def main(once: bool = False, poll_interval: Optional[float] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    return asyncio.run(amain(once=once, poll_interval=poll_interval))
