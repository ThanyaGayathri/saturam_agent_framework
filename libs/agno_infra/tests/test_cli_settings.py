"""Tests for CLI config directory resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from egpt.cli.settings import migrate_legacy_cli_config, resolve_cli_config_dir


def test_resolve_cli_config_dir_prefers_branded_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    branded = tmp_path / "egpt"
    legacy = tmp_path / "ag"
    branded.mkdir()
    legacy.mkdir()
    (branded / "config.json").write_text("{}", encoding="utf-8")
    (legacy / "config.json").write_text('{"legacy": true}', encoding="utf-8")

    monkeypatch.setattr("egpt.cli.settings.BRANDED_CLI_CONFIG_DIR", branded)
    monkeypatch.setattr("egpt.cli.settings.LEGACY_CLI_CONFIG_DIR", legacy)

    assert resolve_cli_config_dir() == branded


def test_resolve_cli_config_dir_falls_back_to_legacy_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    branded = tmp_path / "egpt"
    legacy = tmp_path / "ag"
    legacy.mkdir()
    (legacy / "config.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr("egpt.cli.settings.BRANDED_CLI_CONFIG_DIR", branded)
    monkeypatch.setattr("egpt.cli.settings.LEGACY_CLI_CONFIG_DIR", legacy)

    assert resolve_cli_config_dir() == legacy


def test_resolve_cli_config_dir_defaults_to_branded_for_new_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    branded = tmp_path / "egpt"
    legacy = tmp_path / "ag"

    monkeypatch.setattr("egpt.cli.settings.BRANDED_CLI_CONFIG_DIR", branded)
    monkeypatch.setattr("egpt.cli.settings.LEGACY_CLI_CONFIG_DIR", legacy)

    assert resolve_cli_config_dir() == branded


def test_migrate_legacy_cli_config_copies_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    branded = tmp_path / "egpt"
    legacy = tmp_path / "ag"
    legacy.mkdir()
    (legacy / "config.json").write_text('{"token": "legacy"}', encoding="utf-8")

    monkeypatch.setattr("egpt.cli.settings.BRANDED_CLI_CONFIG_DIR", branded)
    monkeypatch.setattr("egpt.cli.settings.LEGACY_CLI_CONFIG_DIR", legacy)

    assert migrate_legacy_cli_config() == branded
    assert (branded / "config.json").read_text(encoding="utf-8") == '{"token": "legacy"}'
    # Legacy file remains intact.
    assert (legacy / "config.json").is_file()
