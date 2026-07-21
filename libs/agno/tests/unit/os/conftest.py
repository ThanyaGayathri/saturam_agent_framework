"""Shared unit-test fixtures and quarantine rules for OS tests (EGPT-0.10)."""

from __future__ import annotations

import sys
from pathlib import Path

# These modules fail at import time on Python 3.10 (e.g. datetime.UTC) or are
# gated to 3.11+ for the PR matrix. Ignore collection instead of skip-after-import.
_PY310_QUARANTINED = {
    "test_traces_remote_ownership.py",
    "test_jwt_middleware_helpers.py",
    "test_ws_jwt_config.py",
}

QUARANTINE_REASON = "Quarantined (EGPT-0.10): OS JWT/trace tests require Python 3.11+"


def pytest_ignore_collect(collection_path: Path, config) -> bool | None:  # noqa: ARG001
    """Skip selected OS unit tests on Python 3.10 before import."""
    if sys.version_info >= (3, 11):
        return None
    if collection_path.name in _PY310_QUARANTINED:
        return True
    return None
