"""Tests for egpt.settings.EgptSettings and its accessors."""

from __future__ import annotations

import warnings

import pytest

from egpt.settings import (
    EgptSettings,
    MissingSettingError,
    get_settings,
    jwt_is_configured,
    require,
    resolve,
    resolve_bool,
)
from egpt.settings.sources import bare_env_name
from egpt.utils import env as env_module


@pytest.fixture(autouse=True)
def _clear_env_warnings() -> None:
    env_module._reset_warning_state()
    yield
    env_module._reset_warning_state()


def _clear_all(monkeypatch: pytest.MonkeyPatch, field_name: str) -> None:
    name = field_name.upper()
    monkeypatch.delenv(f"EGPT_{name}", raising=False)
    monkeypatch.delenv(f"AGNO_{name}", raising=False)
    bare = bare_env_name(EgptSettings.model_fields[field_name])
    if bare:
        monkeypatch.delenv(bare, raising=False)


def test_constructs_with_empty_environment() -> None:
    settings = EgptSettings()
    assert settings.jwt_verification_key is None
    assert settings.whatsapp_skip_signature_validation is False


def test_field_to_bare_name_mapping_is_mechanical() -> None:
    """Every bare_env() marker must equal FIELD_NAME.upper() -- resolve()/require()
    derive the branded/legacy names purely from the field name, so a marker that
    diverges from that pattern would silently stop being reachable via those helpers."""
    for name, field in EgptSettings.model_fields.items():
        bare = bare_env_name(field)
        if bare is not None:
            assert bare == name.upper(), (name, bare)


def test_get_settings_is_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_all(monkeypatch, "jwt_verification_key")
    assert get_settings().jwt_verification_key is None
    monkeypatch.setenv("EGPT_JWT_VERIFICATION_KEY", "abc")
    assert get_settings().jwt_verification_key == "abc"


def test_get_settings_accepts_overrides() -> None:
    assert get_settings(jwt_verification_key="explicit").jwt_verification_key == "explicit"


def test_resolve_typo_raises_key_error(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(KeyError):
        resolve("jwt_verifcation_key")


def test_resolve_reads_live(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_all(monkeypatch, "slack_signing_secret")
    assert resolve("slack_signing_secret") is None
    monkeypatch.setenv("EGPT_SLACK_SIGNING_SECRET", "s")
    assert resolve("slack_signing_secret") == "s"


def test_resolve_bool_strict_grammar(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_all(monkeypatch, "whatsapp_skip_signature_validation")
    monkeypatch.setenv("EGPT_WHATSAPP_SKIP_SIGNATURE_VALIDATION", "1")
    assert resolve_bool("whatsapp_skip_signature_validation") is False
    monkeypatch.setenv("EGPT_WHATSAPP_SKIP_SIGNATURE_VALIDATION", "true")
    assert resolve_bool("whatsapp_skip_signature_validation") is True


def test_resolve_bool_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_all(monkeypatch, "whatsapp_skip_signature_validation")
    assert resolve_bool("whatsapp_skip_signature_validation", default=False) is False
    assert resolve_bool("whatsapp_skip_signature_validation", default=True) is True


class TestJwtIsConfigured:
    def test_false_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_all(monkeypatch, "jwt_verification_key")
        _clear_all(monkeypatch, "jwt_jwks_file")
        assert jwt_is_configured() is False

    def test_true_with_verification_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_all(monkeypatch, "jwt_verification_key")
        _clear_all(monkeypatch, "jwt_jwks_file")
        monkeypatch.setenv("EGPT_JWT_VERIFICATION_KEY", "k")
        assert jwt_is_configured() is True

    def test_true_with_jwks_file(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_all(monkeypatch, "jwt_verification_key")
        _clear_all(monkeypatch, "jwt_jwks_file")
        monkeypatch.setenv("EGPT_JWT_JWKS_FILE", "/tmp/jwks.json")
        assert jwt_is_configured() is True


class TestMissingSettingError:
    def test_is_a_value_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_all(monkeypatch, "slack_signing_secret")
        with pytest.raises(ValueError):
            require("slack_signing_secret")

    def test_message_names_every_accepted_spelling(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_all(monkeypatch, "slack_signing_secret")
        with pytest.raises(MissingSettingError) as exc_info:
            require("slack_signing_secret")
        message = str(exc_info.value)
        assert "EGPT_SLACK_SIGNING_SECRET" in message
        assert "AGNO_SLACK_SIGNING_SECRET" in message
        assert "SLACK_SIGNING_SECRET" in message
        assert exc_info.value.field_name == "slack_signing_secret"

    def test_message_omits_bare_alias_when_field_has_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_all(monkeypatch, "telegram_skip_secret_validation")
        with pytest.raises(MissingSettingError):
            require("telegram_skip_secret_validation")

    def test_hint_is_appended(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_all(monkeypatch, "telegram_webhook_secret_token")
        with pytest.raises(MissingSettingError, match=r"required outside development mode"):
            require("telegram_webhook_secret_token", hint="(required outside development mode)")

    def test_require_returns_value_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_all(monkeypatch, "slack_signing_secret")
        monkeypatch.setenv("EGPT_SLACK_SIGNING_SECRET", "s")
        assert require("slack_signing_secret") == "s"


def test_agent_os_port_coerces_to_int(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_all(monkeypatch, "agent_os_port")
    monkeypatch.setenv("EGPT_AGENT_OS_PORT", "9999")
    settings = get_settings()
    assert settings.agent_os_port == 9999
    assert isinstance(settings.agent_os_port, int)


def test_no_third_party_provider_fields_in_namespace() -> None:
    """EGPT-1.10 decision 1: third-party provider credentials never join this model."""
    provider_like = {"OPENAI_API_KEY", "AWS_SECRET_ACCESS_KEY", "TELEGRAM_TOKEN", "WHATSAPP_ACCESS_TOKEN"}
    field_bare_names = {bare_env_name(f) for f in EgptSettings.model_fields.values()}
    assert not (field_bare_names & provider_like)


def test_bare_field_warning_does_not_fail_suite(monkeypatch: pytest.MonkeyPatch) -> None:
    """No filterwarnings config turns FutureWarning into a test failure -- this pins that
    a deployment on the documented bare OS_SECURITY_KEY-style spelling keeps working."""
    _clear_all(monkeypatch, "slack_signing_secret")
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "bare-value")
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        assert resolve("slack_signing_secret") == "bare-value"
