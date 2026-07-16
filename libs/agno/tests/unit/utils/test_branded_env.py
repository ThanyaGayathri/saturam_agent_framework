"""Tests for branded environment variable helpers."""

from __future__ import annotations

import warnings

import pytest

from egpt.utils import env as env_module
from egpt.utils.env import (
    apply_legacy_env_fallbacks,
    get_branded_env,
    get_branded_env_bool,
)


@pytest.fixture(autouse=True)
def _clear_env_warnings() -> None:
    env_module._warned_legacy_env_vars.clear()
    yield
    env_module._warned_legacy_env_vars.clear()


def test_get_branded_env_prefers_egpt_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EGPT_DEBUG", "true")
    monkeypatch.setenv("AGNO_DEBUG", "false")
    assert get_branded_env("DEBUG") == "true"


def test_get_branded_env_falls_back_to_agno_with_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EGPT_DEBUG", raising=False)
    monkeypatch.setenv("AGNO_DEBUG", "true")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert get_branded_env("DEBUG") == "true"

    assert len(caught) == 1
    assert "AGNO_DEBUG is deprecated" in str(caught[0].message)


def test_get_branded_env_returns_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EGPT_DEBUG", raising=False)
    monkeypatch.delenv("AGNO_DEBUG", raising=False)
    assert get_branded_env("DEBUG", "false") == "false"


def test_get_branded_env_bool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EGPT_TELEMETRY", "yes")
    assert get_branded_env_bool("TELEMETRY") is True


def test_apply_legacy_env_fallbacks_copies_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EGPT_KEY", raising=False)
    monkeypatch.setenv("AGNO_KEY", "legacy-value")
    apply_legacy_env_fallbacks()
    assert get_branded_env("KEY") == "legacy-value"
