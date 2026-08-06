"""Quarantine mistral integration tests (EGPT-0.10).

The mistralai optional extra is temporarily disabled in egpt[tests] pending
PyPI quarantine. Collection must be skipped to avoid ImportError on
`from egpt.integrations.models.mistral import MistralChat`.
"""

from __future__ import annotations

from pathlib import Path

QUARANTINE_REASON = (
    "Quarantined (EGPT-0.10): mistral extra disabled pending PyPI quarantine; run manually with egpt[mistral]"
)


def pytest_ignore_collect(collection_path: Path, config) -> bool | None:  # noqa: ARG001
    """Do not collect mistral integration tests (avoids import-time failures)."""
    if not getattr(config, "_egpt_mistral_int_q", False):
        config._egpt_mistral_int_q = True
        tr = config.pluginmanager.get_plugin("terminalreporter")
        if tr is not None:
            tr.write_line(QUARANTINE_REASON)
    return True
