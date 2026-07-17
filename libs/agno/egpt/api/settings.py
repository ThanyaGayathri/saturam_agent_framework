from __future__ import annotations

from importlib import metadata
from typing import Any, Dict, Tuple, Type

from pydantic import field_validator
from pydantic_core.core_schema import ValidationInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from egpt.brand import PRODUCT_NAME
from egpt.utils.env import get_branded_env
from egpt.utils.log import log_error


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

    api_runtime: str = "prd"
    alpha_features: bool = False

    api_url: str = "https://os-api.agno.com"

    model_config = SettingsConfigDict(env_prefix="")

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
        # fallbacks emit FutureWarning and late env changes are still honored.
        return (
            init_settings,
            _BrandedEnvSettingsSource(settings_cls),
            dotenv_settings,
            file_secret_settings,
        )

    @field_validator("api_runtime", mode="before")
    def validate_runtime_env(cls, v):
        """Validate api_runtime."""

        valid_api_runtimes = ["dev", "stg", "prd"]
        if v.lower() not in valid_api_runtimes:
            raise ValueError(f"Invalid api_runtime: {v}")

        return v.lower()

    @field_validator("api_url", mode="before")
    def update_api_url(cls, v, info: ValidationInfo):
        api_runtime = info.data.get("api_runtime")
        if api_runtime == "dev":
            if get_branded_env("RUNTIME") == "docker":
                return "http://host.docker.internal:7070"
            return "http://localhost:7070"
        elif api_runtime == "stg":
            return "https://api-stg.agno.com"
        else:
            return "https://os-api.agno.com"

    def gate_alpha_feature(self):
        if not self.alpha_features:
            log_error(
                "This is an Alpha feature not for general use.\nPlease message the "
                + PRODUCT_NAME
                + " team for access."
            )
            exit(1)


agno_api_settings = AgnoAPISettings()
