from __future__ import annotations

from importlib import metadata
from typing import Any, Dict, Tuple, Type
from urllib.parse import urlparse

from pydantic import field_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from egpt.brand import PRODUCT_NAME
from egpt.utils.env import get_branded_env
from egpt.utils.log import log_error

# Known upstream phone-home hosts — never allow as a telemetry endpoint.
_BLOCKED_TELEMETRY_HOSTS = frozenset(
    {
        "os-api.agno.com",
        "api-stg.agno.com",
        "agno.com",
        "www.agno.com",
    }
)


def _normalize_api_url(value: Any) -> str:
    if value is None:
        return ""
    url = str(value).strip()
    if not url or url.lower() in ("false", "true", "0", "1", "none", "null"):
        return ""
    return url


def _is_blocked_upstream(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return host in _BLOCKED_TELEMETRY_HOSTS


def resolve_telemetry_api_url() -> str:
    """Resolve telemetry API URL, preferring live EGPT_API_URL / AGNO_API_URL.

    Env is re-read on every call so runtime ``setenv`` is honored (the module
    singleton alone freezes values at import time).
    """
    env_url = _normalize_api_url(get_branded_env("API_URL"))
    if env_url:
        if _is_blocked_upstream(env_url):
            return ""
        return env_url

    # Fall back to the constructed singleton value (explicit init / earlier env).
    settings = globals().get("agno_api_settings")
    if settings is None:
        return ""
    configured = _normalize_api_url(getattr(settings, "api_url", ""))
    if configured and not _is_blocked_upstream(configured):
        return configured
    return ""


class _BrandedEnvSettingsSource(PydanticBaseSettingsSource):
    """Read EGPT_* then AGNO_* via get_branded_env (emits deprecation warnings)."""

    def get_field_value(self, field: Any, field_name: str) -> Tuple[Any, str, bool]:
        env_name = field_name.upper()
        value = get_branded_env(env_name)
        return value, field_name, value is not None

    def __call__(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {}
        for field_name in self.settings_cls.model_fields:
            value, _key, is_set = self.get_field_value(None, field_name)
            if is_set and value is not None:
                data[field_name] = value
        return data


class AgnoAPISettings(BaseSettings):
    app_name: str = "egpt"
    app_version: str = metadata.version("egpt")

    # Retained for compatibility with EGPT_API_RUNTIME / AGNO_API_RUNTIME.
    # No longer selects a telemetry URL (callers must set EGPT_API_URL explicitly).
    # Kept so existing deployments that set the env var do not raise ValidationError.
    api_runtime: str = "prd"
    alpha_features: bool = False

    # Empty by default — no upstream phone-home. Set EGPT_API_URL to opt in.
    api_url: str = ""

    # Belt-and-suspenders: if the default env source is ever re-added, unprefixed
    # API_URL / APP_NAME cannot bind. Branded dual-read still uses get_branded_env.
    model_config = SettingsConfigDict(env_prefix="EGPT_")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: Type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> Tuple[PydanticBaseSettingsSource, ...]:
        # Prefer branded dual-read source over the default env source so AGNO_*
        # fallbacks emit FutureWarning. Live EGPT_API_URL is also re-read via
        # resolve_telemetry_api_url() at gate/client time.
        return (
            init_settings,
            _BrandedEnvSettingsSource(settings_cls),
            dotenv_settings,
            file_secret_settings,
        )

    @field_validator("api_runtime", mode="before")
    def validate_runtime_env(cls, v):
        """Validate api_runtime.

        Legacy compatibility only — does not select telemetry endpoints.
        """

        valid_api_runtimes = ["dev", "stg", "prd"]
        if v.lower() not in valid_api_runtimes:
            raise ValueError(f"Invalid api_runtime: {v}")

        return v.lower()

    @field_validator("api_url", mode="before")
    def update_api_url(cls, v):
        # Require an explicit endpoint (EGPT_API_URL / AGNO_API_URL / init kwarg).
        url = _normalize_api_url(v)
        if not url:
            return ""
        if _is_blocked_upstream(url):
            return ""
        return url

    def has_telemetry_endpoint(self) -> bool:
        """Return True when a telemetry API endpoint is configured (live env aware)."""
        env_url = _normalize_api_url(get_branded_env("API_URL"))
        if env_url:
            return not _is_blocked_upstream(env_url)
        configured = _normalize_api_url(self.api_url)
        return bool(configured) and not _is_blocked_upstream(configured)

    def gate_alpha_feature(self):
        if not self.alpha_features:
            log_error(
                "This is an Alpha feature not for general use.\nPlease message the "
                + PRODUCT_NAME
                + " team for access."
            )
            exit(1)


agno_api_settings = AgnoAPISettings()
