"""Isolation for provider-seam tests.

Two process-global surfaces need restoring around every test in this package:

* The discovery table -- a provider test that registers a fake model backend would otherwise
  leak it into every later test. This fixture is the one from ``tests/unit/registry/conftest.py``,
  including the ``_VERSION`` bump on teardown, which is load-bearing: ``egpt/models/utils.py``
  keys its alias indices on ``discovery.version(kind)``, so restoring the table without
  bumping would keep serving indices built from a test's fakes.

* The default model provider -- ``egpt.providers.set_default_model_provider`` is a module
  global, so a test that installs one must not change how any later test's agent resolves its
  model.
"""

import pytest

from egpt.providers import get_default_model_provider, set_default_model_provider
from egpt.registry import discovery


@pytest.fixture(autouse=True)
def restore_discovery_table():
    """Snapshot and restore the discovery table around every test in this package."""
    saved_table = {kind: dict(table) for kind, table in discovery._TABLE.items()}
    saved_ready = set(discovery._READY)
    saved_entry_points = discovery._ENTRY_POINTS
    try:
        yield
    finally:
        for kind, table in saved_table.items():
            discovery._TABLE[kind] = table
            # Bump rather than restore: a consumer caching on `version` (egpt/models/utils.py
            # keys its alias indices on it) must see the rollback as a change, or it would
            # keep serving indices built from a test's fake providers.
            discovery._VERSION[kind] += 1
        discovery._READY.clear()
        discovery._READY.update(saved_ready)
        discovery._BOOTSTRAPPING.clear()
        discovery._ENTRY_POINTS = saved_entry_points


@pytest.fixture(autouse=True)
def restore_default_model_provider():
    """Restore the process-wide default provider around every test in this package."""
    saved = get_default_model_provider()
    try:
        yield
    finally:
        set_default_model_provider(saved)


@pytest.fixture(autouse=True)
def reset_resolution_counter():
    """The resolved-without-provider counter is process-global and never otherwise reset.

    Without this, a test installing a provider after any earlier test resolved an agent emits
    the ordering warning -- noise that reads like a real misconfiguration.

    Resets the counter only, not the provider global: ``restore_default_model_provider`` above
    owns that one, and ``reset_provider_state_for_tests`` would clear the value it is trying to
    put back. Two autouse fixtures writing the same global is how a restore silently stops
    restoring.
    """
    import egpt.providers as providers

    try:
        yield
    finally:
        providers._RESOLVED_WITHOUT_PROVIDER = 0
