"""Quarantine litellm_openai integration tests (EGPT-0.10).

Importing LiteLLMOpenAI still loads egpt.models.litellm.chat, which requires
the disabled litellm extra.
"""

from __future__ import annotations

from pathlib import Path

QUARANTINE_REASON = (
    "Quarantined (EGPT-0.10): litellm extra disabled pending PyPI security review; "
    "run manually with egpt[litellm]"
)


def pytest_ignore_collect(collection_path: Path, config) -> bool | None:  # noqa: ARG001
    """Do not collect litellm_openai integration tests (avoids import-time failures)."""
    if not getattr(config, "_egpt_litellm_openai_int_q", False):
        config._egpt_litellm_openai_int_q = True
        tr = config.pluginmanager.get_plugin("terminalreporter")
        if tr is not None:
            tr.write_line(QUARANTINE_REASON)
    return True
