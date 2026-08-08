"""EGPT-1.10: interface security reads routed through egpt.settings.

Covers the new EGPT_TELEGRAM_SKIP_SECRET_VALIDATION flag and cross-checks that the
inbound-webhook-verification settings (Slack/WhatsApp/Telegram) resolve through the
same three-way precedence as everything else in EgptSettings. The individual
interfaces' request-handling tests (test_slack_security.py, test_whatsapp_security.py,
test_telegram_interface.py) already cover their signature-check mechanics; this file
covers the settings-layer wiring specifically.
"""

from __future__ import annotations

import sys
import types
import warnings

import pytest


def _install_fake_telebot() -> None:
    """The telegram package requires the optional pyTelegramBotAPI dependency just to
    import; this module only needs security.py, so stub the SDK like
    test_telegram_interface.py does rather than adding a real dependency."""
    if "telebot" in sys.modules:
        return
    telebot = types.ModuleType("telebot")
    telebot_async = types.ModuleType("telebot.async_telebot")
    telebot_apihelper = types.ModuleType("telebot.apihelper")

    class AsyncTeleBot:
        def __init__(self, token=None):
            self.token = token

    class TeleBot:
        def __init__(self, token=None):
            self.token = token

    class ApiTelegramException(Exception):
        pass

    telebot.TeleBot = TeleBot
    telebot_async.AsyncTeleBot = AsyncTeleBot
    telebot_apihelper.ApiTelegramException = ApiTelegramException
    sys.modules.setdefault("telebot", telebot)
    sys.modules.setdefault("telebot.async_telebot", telebot_async)
    sys.modules.setdefault("telebot.apihelper", telebot_apihelper)


_install_fake_telebot()

from egpt.os.interfaces.telegram.security import _is_dev_mode as is_development_mode  # noqa: E402
from egpt.utils import env as env_module  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_env_warnings() -> None:
    env_module._reset_warning_state()
    yield
    env_module._reset_warning_state()


def _clear_telegram_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("EGPT_TELEGRAM_SKIP_SECRET_VALIDATION", raising=False)
    monkeypatch.delenv("AGNO_TELEGRAM_SKIP_SECRET_VALIDATION", raising=False)


class TestTelegramSkipSecretValidationFlag:
    def test_new_flag_bypasses_without_app_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_telegram_env(monkeypatch)
        monkeypatch.setenv("EGPT_TELEGRAM_SKIP_SECRET_VALIDATION", "true")
        assert is_development_mode() is True

    def test_new_flag_strict_grammar_rejects_truthy_non_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_telegram_env(monkeypatch)
        monkeypatch.setenv("EGPT_TELEGRAM_SKIP_SECRET_VALIDATION", "1")
        assert is_development_mode() is False

    def test_legacy_app_env_still_bypasses_with_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_telegram_env(monkeypatch)
        monkeypatch.setenv("APP_ENV", "development")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            assert is_development_mode() is True
        assert any("APP_ENV=development is deprecated" in str(item.message) for item in caught)

    def test_neither_set_means_not_dev(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_telegram_env(monkeypatch)
        assert is_development_mode() is False

    def test_new_flag_does_not_unify_with_serving_env_field(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """EgptServingSettings.env defaults to "dev" -- if the bypass were ever wired to
        read from that field instead of its own dedicated flag, every default-configured
        deployment would silently disable Telegram webhook secret validation. Pin that
        the two stay independent: EGPT_ENV must have zero effect on the bypass."""
        _clear_telegram_env(monkeypatch)
        monkeypatch.setenv("EGPT_ENV", "dev")
        assert is_development_mode() is False
