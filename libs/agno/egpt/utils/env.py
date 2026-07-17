"""Environment variable helpers with branded prefix and legacy fallback."""

from __future__ import annotations

import warnings
from os import getenv
from typing import Optional, Set

from egpt.brand import ENV_PREFIX, LEGACY_ENV_PREFIX

_warned_legacy_env_vars: Set[str] = set()


def branded_env_key(name: str) -> str:
    """Return the branded environment variable name for a setting suffix."""
    return f"{ENV_PREFIX}_{name}"


def legacy_env_key(name: str) -> str:
    """Return the legacy environment variable name for a setting suffix."""
    return f"{LEGACY_ENV_PREFIX}_{name}"


def _warn_legacy_env_var(name: str, *, stacklevel: int = 3) -> None:
    if name in _warned_legacy_env_vars:
        return
    _warned_legacy_env_vars.add(name)
    warnings.warn(
        f"{legacy_env_key(name)} is deprecated; use {branded_env_key(name)} instead.",
        FutureWarning,
        stacklevel=stacklevel,
    )


def get_branded_env(name: str, default: Optional[str] = None) -> Optional[str]:
    """Read a setting from EGPT_* first, then AGNO_* with a deprecation warning.

    Empty-but-set branded values fall through to the legacy key / default so
    docker-compose passthrough of unset host vars does not silently disable
    settings.
    """
    branded_value = getenv(branded_env_key(name))
    if branded_value is not None and branded_value != "":
        return branded_value

    legacy_value = getenv(legacy_env_key(name))
    if legacy_value is not None and legacy_value != "":
        _warn_legacy_env_var(name, stacklevel=3)
        return legacy_value

    if branded_value is not None:
        return branded_value
    if legacy_value is not None:
        _warn_legacy_env_var(name, stacklevel=3)
        return legacy_value

    return default


def get_branded_env_bool(name: str, default: bool = False, *, strict: bool = True) -> bool:
    """Read a boolean setting from EGPT_* first, then AGNO_* with a deprecation warning.

    By default only the literal ``\"true\"`` (case-insensitive) is truthy, matching
    the pre-EGPT-0.6 grammar for most settings. Pass ``strict=False`` for the
    historically wide set used by ``LOG_TRACEBACKS`` (``true`` / ``1`` / ``yes``).
    """
    value = get_branded_env(name)
    if value is None:
        return default
    lowered = value.lower()
    if strict:
        return lowered == "true"
    return lowered in ("true", "1", "yes")


def get_from_env(key: str, default: Optional[str] = None, required: bool = False) -> Optional[str]:
    """Deprecated: get a value for an environment variable.

    Prefer :func:`get_branded_env` for branded settings.
    """
    warnings.warn(
        "get_from_env is deprecated; use get_branded_env for branded settings.",
        FutureWarning,
        stacklevel=2,
    )
    value = getenv(key, default)
    if required and value is None:
        raise ValueError(f"Environment variable {key} is required but not set")
    return value
