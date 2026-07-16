"""Tests for branded API user-agent settings."""

from __future__ import annotations

import importlib

import pytest


def test_api_settings_default_user_agent_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EGPT_APP_NAME", raising=False)
    monkeypatch.delenv("AGNO_APP_NAME", raising=False)

    from egpt.api import settings as settings_module

    importlib.reload(settings_module)
    try:
        assert settings_module.agno_api_settings.app_name == "egpt"
    finally:
        importlib.reload(settings_module)
