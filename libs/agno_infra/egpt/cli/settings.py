from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from egpt.utilities.branding import ENV_PREFIX

BRANDED_CLI_CONFIG_DIR: Path = Path.home().resolve().joinpath(".config").joinpath("egpt")
LEGACY_CLI_CONFIG_DIR: Path = Path.home().resolve().joinpath(".config").joinpath("ag")
# Deprecated path constant for the legacy on-disk location.
AGNO_CLI_CONFIG_DIR: Path = LEGACY_CLI_CONFIG_DIR


def resolve_cli_config_dir() -> Path:
    """Return the active CLI config directory, preferring egpt with legacy ag fallback."""
    branded_config = BRANDED_CLI_CONFIG_DIR.joinpath("config.json")
    legacy_config = LEGACY_CLI_CONFIG_DIR.joinpath("config.json")
    if branded_config.is_file():
        return BRANDED_CLI_CONFIG_DIR
    if legacy_config.is_file():
        return LEGACY_CLI_CONFIG_DIR
    return BRANDED_CLI_CONFIG_DIR


def migrate_legacy_cli_config() -> Path:
    """Copy ~/.config/ag/config.json to ~/.config/egpt once if needed.

    Returns the active config directory after migration.
    """
    import shutil

    branded_config = BRANDED_CLI_CONFIG_DIR.joinpath("config.json")
    legacy_config = LEGACY_CLI_CONFIG_DIR.joinpath("config.json")
    if not branded_config.is_file() and legacy_config.is_file():
        BRANDED_CLI_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(legacy_config, branded_config)
    return resolve_cli_config_dir()


EGPT_CLI_CONFIG_DIR: Path = resolve_cli_config_dir()


class AgnoCliSettings(BaseSettings):
    config_file_path: Path = Field(default_factory=lambda: resolve_cli_config_dir().joinpath("config.json"))

    model_config = SettingsConfigDict(env_prefix=f"{ENV_PREFIX}_")


agno_cli_settings = AgnoCliSettings()
