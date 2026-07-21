"""Shared unit-test fixtures and quarantine rules for OS tests."""

import sys

import pytest

_PY310_QUARANTINED = {
    "test_traces_remote_ownership.py",
    "test_jwt_middleware_helpers.py",
    "test_ws_jwt_config.py",
}


def pytest_collection_modifyitems(config, items):
    """Skip selected OS unit tests on Python 3.10 (EGPT-0.10 PR gate)."""
    if sys.version_info >= (3, 11):
        return

    skip = pytest.mark.skip(
        reason="Quarantined (EGPT-0.10): OS JWT/trace tests require Python 3.11+",
    )
    for item in items:
        if item.path.name in _PY310_QUARANTINED:
            item.add_marker(skip)
