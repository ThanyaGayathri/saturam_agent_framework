"""Central typed settings for the EGPT namespace.

``EgptSettings`` is the source of truth for every environment setting the
framework reads *for itself* -- serving/auth config and inbound-webhook
verification secrets. Third-party provider credentials (``OPENAI_API_KEY``,
``AWS_*``, ``GOOGLE_*``, outbound bot/access tokens, ...) are deliberately
absent: their names are owned by the SDK/provider convention, not by us, and
renaming them would fight that convention rather than serve it. See the
allowlist in ``tests/unit/test_env_read_guard.py`` for the full accounting
of what is and is not in this namespace, and why.

There is no module-level singleton and no cache. ``get_settings()`` builds a
fresh snapshot on every call, and ``resolve()`` reads a single field live
without building the model at all. A cached settings object is the wrong
default here: a test that sets ``EGPT_JWT_VERIFICATION_KEY`` after an earlier
test warmed a cache would read a stale value, and stale reads in an auth path
are the failure mode this module exists to prevent.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import Field

from egpt.settings.sources import BrandedSettings, bare_env, bare_env_name
from egpt.utils.env import branded_env_key, get_branded_env_with_bare_fallback, legacy_env_key

__all__ = [
    "EgptSettings",
    "MissingSettingError",
    "get_settings",
    "jwt_is_configured",
    "require",
    "resolve",
    "resolve_bool",
]


class EgptSettings(BrandedSettings):
    """Every EGPT-namespace environment setting the framework reads for itself."""

    # -- serving --------------------------------------------------------
    agent_os_host: Optional[str] = Field(default=None, json_schema_extra=bare_env("AGENT_OS_HOST"))
    agent_os_port: Optional[int] = Field(default=None, json_schema_extra=bare_env("AGENT_OS_PORT"))

    # -- JWT verification -------------------------------------------------
    jwt_verification_key: Optional[str] = Field(default=None, json_schema_extra=bare_env("JWT_VERIFICATION_KEY"))
    jwt_jwks_file: Optional[str] = Field(default=None, json_schema_extra=bare_env("JWT_JWKS_FILE"))

    # -- inbound webhook verification (this framework's own HTTP surface) --
    slack_signing_secret: Optional[str] = Field(default=None, json_schema_extra=bare_env("SLACK_SIGNING_SECRET"))
    telegram_webhook_secret_token: Optional[str] = Field(
        default=None, json_schema_extra=bare_env("TELEGRAM_WEBHOOK_SECRET_TOKEN")
    )
    whatsapp_app_secret: Optional[str] = Field(default=None, json_schema_extra=bare_env("WHATSAPP_APP_SECRET"))
    whatsapp_verify_token: Optional[str] = Field(default=None, json_schema_extra=bare_env("WHATSAPP_VERIFY_TOKEN"))

    # -- signature-check bypasses (framework behaviour flags, never credentials) --
    whatsapp_skip_signature_validation: bool = Field(
        default=False, json_schema_extra=bare_env("WHATSAPP_SKIP_SIGNATURE_VALIDATION")
    )
    # New in EGPT-1.10; no bare form. The legacy spelling it replaces
    # (APP_ENV == "development") is handled explicitly in
    # egpt.os.interfaces.telegram.security so that path can carry its own
    # deprecation warning instead of silently aliasing onto this flag.
    telegram_skip_secret_validation: bool = False


def get_settings(**overrides: Any) -> EgptSettings:
    """Build a fresh snapshot of every EGPT-namespace setting from the current environment."""
    return EgptSettings(**overrides)


def resolve(field_name: str) -> Optional[str]:
    """Live three-way read of a single ``EgptSettings`` field, without building the model.

    For call sites on a request path, or otherwise outside any settings
    object's lifetime, where constructing (and re-validating) the whole model
    on every call would be wasteful. ``field_name`` must name a real field --
    a typo raises ``KeyError`` rather than silently resolving to ``None``.
    """
    field = EgptSettings.model_fields[field_name]
    return get_branded_env_with_bare_fallback(field_name.upper(), bare=bare_env_name(field))


def resolve_bool(field_name: str, default: bool = False) -> bool:
    """Live three-way read of a single boolean field.

    Strict grammar: only the literal ``"true"`` (case-insensitive) is truthy.
    These flags gate security behavior -- signature-check bypasses -- so a
    stray ``WHATSAPP_SKIP_SIGNATURE_VALIDATION=1`` must not enable one.
    """
    value = resolve(field_name)
    if value is None:
        return default
    return value.strip().lower() == "true"


def jwt_is_configured() -> bool:
    """Whether JWT verification is configured, live-checked against the environment."""
    return bool(resolve("jwt_verification_key") or resolve("jwt_jwks_file"))


class MissingSettingError(ValueError):
    """A setting a switched-on feature requires is not configured.

    Subclasses ``ValueError`` so existing ``pytest.raises(ValueError)``
    assertions around these settings keep passing unchanged.
    """

    def __init__(self, field_name: str, hint: str = "") -> None:
        field = EgptSettings.model_fields[field_name]
        env_name = field_name.upper()
        aliases = [legacy_env_key(env_name)]
        bare = bare_env_name(field)
        if bare:
            aliases.append(bare)
        message = (
            f"{field_name} is not configured. Set {branded_env_key(env_name)} "
            f"(deprecated aliases: {', '.join(aliases)})."
        )
        if hint:
            message += f" {hint}"
        super().__init__(message)
        self.field_name = field_name


def require(field_name: str, *, hint: str = "") -> str:
    """Live three-way read of one field, raising ``MissingSettingError`` if unset."""
    value = resolve(field_name)
    if not value:
        raise MissingSettingError(field_name, hint=hint)
    return value
