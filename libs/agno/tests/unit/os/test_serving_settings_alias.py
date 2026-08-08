"""Tests for deprecated AgnoAPISettings alias."""

from __future__ import annotations

import warnings

import pytest


def test_agnoapisettings_alias_emits_deprecation_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    import egpt.os.settings as settings_module

    # Other test modules import AgnoAPISettings at collection time, which consumes the
    # one-shot dedup flag before this test body runs -- reset it so the warning fires here.
    monkeypatch.setattr(settings_module, "_warned_agno_api_settings", False)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        alias = settings_module.AgnoAPISettings
        assert alias.__name__ == "EgptServingSettings"

    assert any("AgnoAPISettings is deprecated" in str(item.message) for item in caught)


def test_egptservingsettings_is_primary_export() -> None:
    from egpt.os.settings import EgptServingSettings

    assert EgptServingSettings.__name__ == "EgptServingSettings"
