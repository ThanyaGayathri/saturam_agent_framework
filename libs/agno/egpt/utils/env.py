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


def _warn_legacy_env_var(name: str) -> None:
    if name in _warned_legacy_env_vars:
        return
    _warned_legacy_env_vars.add(name)
    warnings.warn(
        f"{legacy_env_key(name)} is deprecated; use {branded_env_key(name)} instead.",
        DeprecationWarning,
        stacklevel=3,
    )


def get_branded_env(name: str, default: Optional[str] = None) -> Optional[str]:
    """Read a setting from EGPT_* first, then AGNO_* with a deprecation warning."""
    branded_value = getenv(branded_env_key(name))
    if branded_value is not None:
        return branded_value

    legacy_value = getenv(legacy_env_key(name))
    if legacy_value is not None:
        _warn_legacy_env_var(name)
        return legacy_value

    return default


def get_branded_env_bool(name: str, default: bool = False) -> bool:
    """Read a boolean setting from EGPT_* first, then AGNO_* with a deprecation warning."""
    value = get_branded_env(name)
    if value is None:
        return default
    return value.lower() in ("true", "1", "yes")


def apply_legacy_env_fallbacks() -> None:
    """Copy legacy AGNO_* values into EGPT_* when only the legacy key is set."""
    import os

    for key, value in os.environ.items():
        if not key.startswith(f"{LEGACY_ENV_PREFIX}_"):
            continue
        suffix = key[len(f"{LEGACY_ENV_PREFIX}_") :]
        branded_key = branded_env_key(suffix)
        if branded_key not in os.environ:
            os.environ[branded_key] = value
