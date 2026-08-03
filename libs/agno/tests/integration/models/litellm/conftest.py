"""Quarantine litellm integration tests (EGPT-0.10).

The litellm optional extra is temporarily disabled in egpt[tests] pending PyPI
security review. Collection must be skipped to avoid ImportError on
`from egpt.models.litellm import LiteLLM`.
"""

from __future__ import annotations

from pathlib import Path

QUARANTINE_REASON = (
    "Quarantined (EGPT-0.10): litellm extra disabled pending PyPI security review; run manually with egpt[litellm]"
)


def pytest_ignore_collect(collection_path: Path, config) -> bool | None:  # noqa: ARG001
    """Do not collect litellm integration tests (avoids import-time failures)."""
    if not getattr(config, "_egpt_litellm_int_q", False):
        config._egpt_litellm_int_q = True
        tr = config.pluginmanager.get_plugin("terminalreporter")
        if tr is not None:
            tr.write_line(QUARANTINE_REASON)
    return True
