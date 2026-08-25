"""A bounded thread pool with daemon workers (round-4 review, PR #148).

``concurrent.futures.ThreadPoolExecutor`` creates non-daemon worker threads with no public hook
to change that (its ``_adjust_thread_count`` hard-codes ``threading.Thread(...)``), and CPython
registers a process-wide ``atexit`` hook (``concurrent.futures.thread._python_exit``) that
untimed-``join()``s *every* worker thread of *every* ``ThreadPoolExecutor`` ever created --
in-flight work included, and regardless of whether that executor's own ``shutdown()`` was ever
called (the hook walks a module-level weak dict of threads, not per-executor state). For a pool
whose whole purpose is best-effort work a caller has already stopped waiting on -- a budgeted
stage that abandons an overrun and returns its fallback promptly -- that is the wrong tradeoff:
nothing depends on the abandoned call's result once the caller has moved on, so an abrupt kill at
process exit costs nothing observable, while blocking graceful shutdown for however long the
abandoned call takes (or forever, if it never returns) is a real operational cost.

``BoundedDaemonExecutor`` accepts that tradeoff explicitly. Its worker threads are daemon threads,
started once at construction and kept alive for the process's life -- daemon threads are never
joined at interpreter exit, so they never enter ``_python_exit``'s wait list at all. Its queue is
bounded: past ``max_queue`` items already waiting, ``submit()`` fails fast (via the returned
``Future``'s exception, not a raise at the call site) rather than growing without limit, so
sustained overrun traffic degrades into the caller's own fallback path instead of retaining
references to every abandoned call's arguments indefinitely.

Subclasses ``concurrent.futures.Executor`` -- so it type-checks anywhere a stdlib executor is
expected (``asyncio``'s ``loop.run_in_executor``, in particular) and inherits a working ``map`` --
but is not a general-purpose one: no dynamic scale-up, and ``shutdown()`` inherits the base
class's default no-op rather than doing anything, since these daemon workers have nothing that
needs releasing at the end of a process that is, by construction, about to exit anyway.
"""

import queue
import threading
from concurrent.futures import Executor, Future
from typing import Any, Callable, TypeVar

T = TypeVar("T")

__all__ = ["BoundedDaemonExecutor"]


class _WorkItem:
    __slots__ = ("future", "fn", "args", "kwargs")

    def __init__(self, future: "Future[Any]", fn: Callable[..., Any], args: Any, kwargs: Any) -> None:
        self.future = future
        self.fn = fn
        self.args = args
        self.kwargs = kwargs

    def run(self) -> None:
        if not self.future.set_running_or_notify_cancel():
            return
        try:
            result = self.fn(*self.args, **self.kwargs)
        except BaseException as exc:  # noqa: BLE001 - handed to the caller via the future, not raised here
            self.future.set_exception(exc)
        else:
            self.future.set_result(result)


class BoundedDaemonExecutor(Executor):
    """``max_workers`` daemon threads behind a queue bounded at ``max_queue``.

    Workers start immediately at construction, not lazily on first ``submit`` -- the pool is
    meant to be a module-level singleton, so "lazy" would only mean "on the first call, from
    whichever thread happens to make it," which buys nothing and adds a startup race to guard
    against for no benefit.
    """

    def __init__(self, max_workers: int, max_queue: int, thread_name_prefix: str = "pool") -> None:
        self._queue: "queue.Queue[Any]" = queue.Queue(maxsize=max_queue)
        self._threads = [
            threading.Thread(target=self._worker_loop, name=f"{thread_name_prefix}-{i}", daemon=True)
            for i in range(max_workers)
        ]
        for thread in self._threads:
            thread.start()

    def _worker_loop(self) -> None:
        while True:
            item = self._queue.get()
            try:
                item.run()
            except BaseException:  # noqa: BLE001 - a worker thread that dies here permanently
                # shrinks the pool (nothing replaces it); `_WorkItem.run` already reports the
                # callable's own exception through the future, so anything reaching this branch
                # is `run` itself misbehaving (round-5 review finding: not currently reachable --
                # nothing here cancels a future -- but cheap enough to guard regardless).
                pass

    def submit(self, fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> "Future[T]":
        """Queue ``fn(*args, **kwargs)``, or fail the returned future if the queue is full.

        Never raises: a full queue is reported through the future's exception (``queue.Full``,
        the same type a blocking caller would see), matching the shape every caller here already
        handles for "the reranker call itself failed" -- one failure path, not two.
        """
        future: "Future[T]" = Future()
        try:
            self._queue.put_nowait(_WorkItem(future, fn, args, kwargs))
        except queue.Full as exc:
            future.set_exception(exc)
        return future
