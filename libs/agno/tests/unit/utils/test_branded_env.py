"""Tests for branded environment variable helpers."""

from __future__ import annotations

import warnings

import pytest

from egpt.utils import env as env_module
from egpt.utils.env import get_branded_env, get_branded_env_bool, get_from_env


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
    assert issubclass(caught[0].category, FutureWarning)


def test_get_branded_env_returns_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EGPT_DEBUG", raising=False)
    monkeypatch.delenv("AGNO_DEBUG", raising=False)
    assert get_branded_env("DEBUG", "false") == "false"


def test_get_branded_env_empty_branded_falls_through_to_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EGPT_DEBUG", "")
    monkeypatch.setenv("AGNO_DEBUG", "true")
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        assert get_branded_env("DEBUG") == "true"


def test_get_branded_env_bool_strict_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EGPT_TELEMETRY", "yes")
    assert get_branded_env_bool("TELEMETRY") is False
    monkeypatch.setenv("EGPT_TELEMETRY", "true")
    assert get_branded_env_bool("TELEMETRY") is True


def test_get_branded_env_bool_wide_grammar(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EGPT_LOG_TRACEBACKS", "1")
    assert get_branded_env_bool("LOG_TRACEBACKS", strict=False) is True


def test_get_from_env_deprecated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOME_KEY", "value")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert get_from_env("SOME_KEY") == "value"
    assert any("get_from_env is deprecated" in str(item.message) for item in caught)
