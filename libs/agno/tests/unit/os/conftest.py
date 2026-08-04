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

# The MCP server test modules hang -- wedge, not fail -- intermittently on Python 3.10:
# fastmcp's server lifespan awaits a pydocket Worker that the py3.10 taskgroup backport
# fails to cancel, so the lifespan never exits. Under CI's --max-worker-restart=0 one
# wedge ends the whole unit session. It is one race in the lifespan, not a defect in
# particular tests, so every module that enters that lifespan -- via
# `Client(build_mcp_server(...))` or by driving the app's lifespan_context -- is
# quarantined as a unit. Full root-cause analysis (repro, task dumps, why it is not
# fixable in this repo): the comment at the top of test_mcp_server.py.
_PY310_MCP_QUARANTINED = {
    "test_mcp_server.py",
    "test_mcp_surface_v2.py",
    "test_mcp_run_results.py",
    "test_mcp_resolution.py",
}

MCP_QUARANTINE_REASON = (
    "Quarantined (EGPT-0.10 follow-up): MCP server tests hang intermittently on Python 3.10 "
    "(fastmcp lifespan / pydocket Worker / taskgroup backport). See test_mcp_server.py."
)


def _announce_once(config, flag: str, reason: str) -> None:
    if getattr(config, flag, False):
        return
    setattr(config, flag, True)
    tr = config.pluginmanager.get_plugin("terminalreporter")
    if tr is not None:
        tr.write_line(reason)


def pytest_ignore_collect(collection_path: Path, config) -> bool | None:  # noqa: ARG001
    """Skip selected OS unit tests on Python 3.10 before import."""
    if sys.version_info >= (3, 11):
        return None
    if collection_path.name in _PY310_QUARANTINED:
        _announce_once(config, "_egpt_os_py310_q", QUARANTINE_REASON)
        return True
    if collection_path.name in _PY310_MCP_QUARANTINED:
        _announce_once(config, "_egpt_os_py310_mcp_q", MCP_QUARANTINE_REASON)
        return True
    return None
