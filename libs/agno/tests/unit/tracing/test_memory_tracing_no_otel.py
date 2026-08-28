"""Regression: `memory_span`'s "opentelemetry not installed" fallback branch.

`memory_span` (`egpt/tracing/memory.py`) imports `opentelemetry.trace` lazily, inside its own
guarding `try/except Exception`, precisely so a host with no `opentelemetry` package installed
still gets working memory reads/writes/consolidates -- just without spans. `test_memory_tracing.py`
covers the "tracing not configured" case (package installed, no `TracerProvider` registered), but
every test in that file `pytest.importorskip("opentelemetry.sdk", ...)`, so none of them ever runs
with the import itself failing.

This file forces that import to fail via `sys.modules`, deliberately without importing
`opentelemetry.sdk` anywhere -- so it collects and runs identically whether or not the SDK is
actually installed, including under the `unit-drivers-absent` CI job.
"""

from __future__ import annotations

import sys

import pytest

from egpt.memory.adapters.co_located import CoLocatedMemoryProvider
from egpt.memory.config import MemoryProviderConfig
from egpt.memory.types import MemoryEvent, MemoryQuery, Scope
from tests.unit.memory._fakes import FakeGraph, FakeVector

ACME = Scope(tenant_id="acme", user_id="u1")


@pytest.fixture(autouse=True)
def _opentelemetry_not_installed(monkeypatch):
    """Makes `from opentelemetry import trace as trace_api` raise ImportError, regardless of
    whether the package is actually present in this environment -- the standard way to simulate a
    missing import via `sys.modules` (a `None` entry short-circuits the import machinery)."""
    monkeypatch.setitem(sys.modules, "opentelemetry", None)
    monkeypatch.setitem(sys.modules, "opentelemetry.trace", None)


def build_provider() -> CoLocatedMemoryProvider:
    vector, graph = FakeVector(), FakeGraph()
    config = MemoryProviderConfig(provider="co_located")
    return CoLocatedMemoryProvider(vector=vector, graph=graph, config=config)


def test_write_still_works_when_opentelemetry_is_not_installed():
    provider = build_provider()
    events = [MemoryEvent(content="the customer prefers email over phone", scope=ACME)]

    receipt = provider.write(ACME, events)

    assert receipt.accepted == 1


def test_read_still_works_when_opentelemetry_is_not_installed():
    provider = build_provider()
    provider.write(ACME, [MemoryEvent(content="the customer prefers email over phone", scope=ACME)])

    slice_ = provider.read(ACME, MemoryQuery(text="customer email", limit=5))

    assert slice_.scope == ACME
    assert len(slice_.records) >= 1


def test_consolidate_still_works_when_opentelemetry_is_not_installed():
    provider = build_provider()
    provider.write(ACME, [MemoryEvent(content="the customer prefers email over phone", scope=ACME)])

    report = provider.consolidate(ACME)

    assert report.scope == ACME
