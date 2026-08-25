"""``BoundedDaemonExecutor`` (round-4 review, PR #148 -- the reranker executor findings).

Two properties a plain ``concurrent.futures.ThreadPoolExecutor`` does not have, both load-bearing
for ``egpt.memory.retrieval.rerank`` and ``egpt.integrations.knowledge.reranker.base``, whose own
tests cover the *wiring* (this pool is used, is shared, is reused); this file is only about what
the pool itself guarantees.
"""

import os
import queue
import subprocess
import sys
import threading
import time
from concurrent.futures import Executor

import pytest

from egpt.utils.concurrency import BoundedDaemonExecutor


def test_worker_threads_are_daemons():
    """The whole reason this class exists over `ThreadPoolExecutor`: daemon threads are never
    joined by CPython's `atexit` hook, so an abandoned/overrunning call can't block process exit.
    See `test_a_process_with_an_abandoned_slow_call_exits_promptly` for the end-to-end proof."""
    executor = BoundedDaemonExecutor(max_workers=3, max_queue=4, thread_name_prefix="t")

    assert len(executor._threads) == 3
    assert all(t.daemon for t in executor._threads)


def test_is_a_real_executor_subclass_so_asyncio_run_in_executor_accepts_it():
    assert isinstance(BoundedDaemonExecutor(max_workers=1, max_queue=1), Executor)


def test_submit_runs_the_callable_and_returns_its_result():
    executor = BoundedDaemonExecutor(max_workers=2, max_queue=4, thread_name_prefix="t")

    future = executor.submit(lambda x, y: x + y, 2, 3)

    assert future.result(timeout=1.0) == 5


def test_submit_offloads_to_a_worker_thread_not_the_calling_thread():
    executor = BoundedDaemonExecutor(max_workers=2, max_queue=4, thread_name_prefix="t")
    calling_thread = threading.current_thread()

    future = executor.submit(threading.current_thread)

    assert future.result(timeout=1.0) is not calling_thread


def test_submit_reports_the_callables_exception_through_the_future():
    executor = BoundedDaemonExecutor(max_workers=1, max_queue=4, thread_name_prefix="t")

    def boom():
        raise RuntimeError("boom")

    future = executor.submit(boom)

    with pytest.raises(RuntimeError, match="boom"):
        future.result(timeout=1.0)


def test_a_full_queue_fails_the_future_rather_than_blocking_or_raising_at_submit():
    """The other half of the round-4 finding: an unbounded queue behind a bounded pool lets
    sustained overrun traffic retain unbounded abandoned-call state. `max_queue` caps it -- past
    that, `submit` itself never blocks or raises; the rejection surfaces through the future,
    exactly like any other failed call, so a caller with an existing `future.result()` try/except
    needs no second code path for it."""
    started, release = threading.Event(), threading.Event()

    def block():
        started.set()
        release.wait()

    executor = BoundedDaemonExecutor(max_workers=1, max_queue=1, thread_name_prefix="t")

    # Occupy the single worker so the next submission has to sit in the queue -- waited out
    # deterministically (`started`), not raced against submitting the next two items.
    blocking_future = executor.submit(block)
    started.wait(timeout=1.0)
    # Fills the one queue slot the (busy) worker isn't holding.
    queued_future = executor.submit(lambda: None)

    # The pool is now full (1 running + 1 queued); this one must be rejected outright.
    rejected_future = executor.submit(lambda: None)

    with pytest.raises(queue.Full):
        rejected_future.result(timeout=0)

    release.set()
    assert blocking_future.result(timeout=1.0) is None
    assert queued_future.result(timeout=1.0) is None


def test_submit_never_raises_synchronously_even_when_the_queue_is_full():
    """A caller's own `try/except` wraps `future.result()`, not `submit()` itself -- `submit`
    raising would be a second failure shape callers of this pool do not check for."""
    started, release = threading.Event(), threading.Event()

    def block():
        started.set()
        release.wait()

    executor = BoundedDaemonExecutor(max_workers=1, max_queue=1, thread_name_prefix="t")
    executor.submit(block)
    started.wait(timeout=1.0)
    executor.submit(lambda: None)

    future = executor.submit(lambda: None)  # must not raise here

    release.set()
    assert future is not None


def test_a_process_with_an_abandoned_slow_call_exits_promptly():
    """End-to-end proof of the daemon-thread property (round-4 Critical finding, reproduced live
    by the reviewer): a plain `ThreadPoolExecutor`'s non-daemon workers make CPython's `atexit`
    hook (`concurrent.futures.thread._python_exit`) block process exit until an abandoned,
    still-running call finishes -- here, several seconds. A `BoundedDaemonExecutor` must not."""
    script = (
        "import time\n"
        "from egpt.utils.concurrency import BoundedDaemonExecutor\n"
        "executor = BoundedDaemonExecutor(max_workers=1, max_queue=1, thread_name_prefix='t')\n"
        "executor.submit(time.sleep, 5.0)\n"
        "time.sleep(0.05)\n"  # let the worker actually start the sleep before the process exits
    )
    import egpt

    agno_root = str(egpt.__file__).rsplit("/egpt/", 1)[0]

    start = time.monotonic()
    result = subprocess.run(
        [sys.executable, "-c", script],
        timeout=4.0,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": agno_root},
    )
    elapsed_s = time.monotonic() - start

    assert result.returncode == 0, result.stderr
    # The abandoned call sleeps 5s; a non-daemon-thread pool would make the process wait for it.
    # Generous relative to the 0.05s startup delay above -- this asserts "did not wait out the
    # full abandoned call," not a tight timing bound.
    assert elapsed_s < 3.0
