"""Tests for branded environment variable helpers."""

from __future__ import annotations

import warnings

import pytest

from egpt.utils import env as env_module
from egpt.utils.env import get_branded_env, get_branded_env_bool, get_branded_env_with_bare_fallback, get_from_env


@pytest.fixture(autouse=True)
def _clear_env_warnings() -> None:
    env_module._reset_warning_state()
    yield
    env_module._reset_warning_state()


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


def _clear_three_way(monkeypatch: pytest.MonkeyPatch, name: str, bare: str) -> None:
    monkeypatch.delenv(f"EGPT_{name}", raising=False)
    monkeypatch.delenv(f"AGNO_{name}", raising=False)
    monkeypatch.delenv(bare, raising=False)


def test_bare_fallback_prefers_branded(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_three_way(monkeypatch, "OS_SECURITY_KEY", "OS_SECURITY_KEY")
    monkeypatch.setenv("EGPT_OS_SECURITY_KEY", "branded")
    monkeypatch.setenv("AGNO_OS_SECURITY_KEY", "legacy")
    monkeypatch.setenv("OS_SECURITY_KEY", "bare")
    assert get_branded_env_with_bare_fallback("OS_SECURITY_KEY", bare="OS_SECURITY_KEY") == "branded"


def test_bare_fallback_prefers_legacy_over_bare(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_three_way(monkeypatch, "OS_SECURITY_KEY", "OS_SECURITY_KEY")
    monkeypatch.setenv("AGNO_OS_SECURITY_KEY", "legacy")
    monkeypatch.setenv("OS_SECURITY_KEY", "bare")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert get_branded_env_with_bare_fallback("OS_SECURITY_KEY", bare="OS_SECURITY_KEY") == "legacy"
    assert any("AGNO_OS_SECURITY_KEY is deprecated" in str(item.message) for item in caught)


def test_bare_fallback_warns_once_per_name(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_three_way(monkeypatch, "OS_SECURITY_KEY", "OS_SECURITY_KEY")
    monkeypatch.setenv("OS_SECURITY_KEY", "bare")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert get_branded_env_with_bare_fallback("OS_SECURITY_KEY", bare="OS_SECURITY_KEY") == "bare"
        assert get_branded_env_with_bare_fallback("OS_SECURITY_KEY", bare="OS_SECURITY_KEY") == "bare"
    assert sum("OS_SECURITY_KEY is deprecated" in str(item.message) for item in caught) == 1
    assert all(issubclass(item.category, FutureWarning) for item in caught)


def test_bare_fallback_and_legacy_warnings_are_independent(monkeypatch: pytest.MonkeyPatch) -> None:
    """AGNO_X and bare X are different deprecated spellings and must not share a dedupe key."""
    _clear_three_way(monkeypatch, "OS_SECURITY_KEY", "OS_SECURITY_KEY")
    monkeypatch.setenv("AGNO_OS_SECURITY_KEY", "legacy")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert get_branded_env_with_bare_fallback("OS_SECURITY_KEY", bare="OS_SECURITY_KEY") == "legacy"
    assert any("AGNO_OS_SECURITY_KEY is deprecated" in str(item.message) for item in caught)

    monkeypatch.delenv("AGNO_OS_SECURITY_KEY", raising=False)
    monkeypatch.setenv("OS_SECURITY_KEY", "bare")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert get_branded_env_with_bare_fallback("OS_SECURITY_KEY", bare="OS_SECURITY_KEY") == "bare"
    assert any("OS_SECURITY_KEY is deprecated" in str(item.message) for item in caught)


def test_bare_fallback_no_bare_name_skips_third_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_three_way(monkeypatch, "SLACK_SIGNING_SECRET", "SLACK_SIGNING_SECRET")
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "bare")
    assert get_branded_env_with_bare_fallback("SLACK_SIGNING_SECRET", bare=None) is None


def test_bare_fallback_empty_branded_falls_through(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_three_way(monkeypatch, "OS_SECURITY_KEY", "OS_SECURITY_KEY")
    monkeypatch.setenv("EGPT_OS_SECURITY_KEY", "")
    monkeypatch.setenv("OS_SECURITY_KEY", "bare")
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        assert get_branded_env_with_bare_fallback("OS_SECURITY_KEY", bare="OS_SECURITY_KEY") == "bare"


def test_bare_fallback_returns_default_when_all_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_three_way(monkeypatch, "OS_SECURITY_KEY", "OS_SECURITY_KEY")
    assert get_branded_env_with_bare_fallback("OS_SECURITY_KEY", bare="OS_SECURITY_KEY", default="d") == "d"
