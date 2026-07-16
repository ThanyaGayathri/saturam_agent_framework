from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from egpt.brand import ENV_PREFIX
from egpt.utils.env import apply_legacy_env_fallbacks

apply_legacy_env_fallbacks()

EGPT_CLI_CONFIG_DIR: Path = Path.home().resolve().joinpath(".config").joinpath("egpt")
# Deprecated alias kept for one release.
AGNO_CLI_CONFIG_DIR: Path = EGPT_CLI_CONFIG_DIR


class AgnoCliSettings(BaseSettings):
    config_file_path: Path = EGPT_CLI_CONFIG_DIR.joinpath("config.json")

    model_config = SettingsConfigDict(env_prefix=f"{ENV_PREFIX}_")


agno_cli_settings = AgnoCliSettings()
