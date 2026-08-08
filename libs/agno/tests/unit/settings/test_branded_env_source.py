"""Tests for the shared BrandedEnvSettingsSource / BrandedSettings base."""

from __future__ import annotations

import warnings
from typing import List, Optional

import pytest
from pydantic import Field

from egpt.settings.sources import BrandedSettings, bare_env
from egpt.utils import env as env_module


@pytest.fixture(autouse=True)
def _clear_env_warnings() -> None:
    env_module._reset_warning_state()
    yield
    env_module._reset_warning_state()


class _Probe(BrandedSettings):
    plain: Optional[str] = Field(default=None)
    bare_field: Optional[str] = Field(default=None, json_schema_extra=bare_env("BARE_FIELD"))
    origins: Optional[List[str]] = Field(default=None)
    flag: bool = False


def test_env_prefix_is_egpt() -> None:
    assert _Probe.model_config.get("env_prefix") == "EGPT_"


def test_branded_prefix_binds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EGPT_PLAIN", "value")
    assert _Probe().plain == "value"


def test_legacy_prefix_binds_with_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EGPT_PLAIN", raising=False)
    monkeypatch.setenv("AGNO_PLAIN", "value")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert _Probe().plain == "value"
    assert any("AGNO_PLAIN is deprecated" in str(item.message) for item in caught)


def test_unmarked_field_does_not_bind_bare_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bare-name binding is opt-in per field -- a field without bare_env() must never
    pick up a same-named unprefixed variable it happens to share a suffix with."""
    monkeypatch.setenv("PLAIN", "should-not-bind")
    assert _Probe().plain is None


def test_marked_field_binds_bare_name_with_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BARE_FIELD", "value")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert _Probe().bare_field == "value"
    assert any("BARE_FIELD is deprecated" in str(item.message) for item in caught)


def test_default_env_source_does_not_bind_unprefixed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default pydantic-settings env source is dropped entirely -- an unprefixed
    PLAIN must not bind even though env_prefix alone wouldn't otherwise forbid it."""
    monkeypatch.setenv("PLAIN", "unprefixed")
    assert _Probe().plain is None


def test_complex_field_still_json_decodes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression guard: a hand-rolled source that skips ``prepare_field_value`` would
    turn a JSON-encoded list into a ValidationError instead of decoding it."""
    monkeypatch.setenv("EGPT_ORIGINS", '["https://a.example", "https://b.example"]')
    assert _Probe().origins == ["https://a.example", "https://b.example"]


def test_case_exact_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deliberate narrowing vs. the default (case-insensitive) env source."""
    monkeypatch.setenv("egpt_plain", "lowercase")
    assert _Probe().plain is None
    monkeypatch.setenv("EGPT_PLAIN", "uppercase")
    assert _Probe().plain == "uppercase"


def test_bool_field_coerces(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EGPT_FLAG", "true")
    assert _Probe().flag is True
