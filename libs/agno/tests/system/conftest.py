"""System test markers (EGPT-0.10)."""

from __future__ import annotations

import pytest


def pytest_collection_modifyitems(config, items):  # noqa: ARG001
    """Mark all system tests with the ``system`` pytest marker."""
    for item in items:
        item.add_marker(pytest.mark.system)
