from __future__ import annotations

from importlib import metadata

from pydantic import field_validator
from pydantic_core.core_schema import ValidationInfo
from pydantic_settings import BaseSettings, SettingsConfigDict

from egpt.brand import ENV_PREFIX, PRODUCT_NAME
from egpt.utils.env import apply_legacy_env_fallbacks, get_branded_env
from egpt.utils.log import log_error

apply_legacy_env_fallbacks()


class AgnoAPISettings(BaseSettings):
    app_name: str = "egpt"
    app_version: str = metadata.version("egpt")

    api_runtime: str = "prd"
    alpha_features: bool = False

    api_url: str = "https://os-api.agno.com"

    model_config = SettingsConfigDict(env_prefix=f"{ENV_PREFIX}_")

    @field_validator("api_runtime", mode="before")
    def validate_runtime_env(cls, v):
        """Validate api_runtime."""

        valid_api_runtimes = ["dev", "stg", "prd"]
        if v.lower() not in valid_api_runtimes:
            raise ValueError(f"Invalid api_runtime: {v}")

        return v.lower()

    @field_validator("api_url", mode="before")
    def update_api_url(cls, v, info: ValidationInfo):
        api_runtime = info.data["api_runtime"]
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
