"""Lightweight env helpers for egptctl without importing the egpt framework."""

from __future__ import annotations

import os
import warnings
from typing import Optional, Set

ENV_PREFIX = "EGPT"
LEGACY_ENV_PREFIX = "AGNO"
PRODUCT_OS_NAME = "EgptOS"

_warned_legacy_env_vars: Set[str] = set()


def branded_env_key(name: str) -> str:
    return f"{ENV_PREFIX}_{name}"


def legacy_env_key(name: str) -> str:
    return f"{LEGACY_ENV_PREFIX}_{name}"


def _warn_legacy_env_var(name: str) -> None:
    if name in _warned_legacy_env_vars:
        return
    _warned_legacy_env_vars.add(name)
    warnings.warn(
        f"{legacy_env_key(name)} is deprecated; use {branded_env_key(name)} instead.",
        FutureWarning,
        stacklevel=3,
    )


def get_branded_env(name: str, default: Optional[str] = None) -> Optional[str]:
    branded_value = os.environ.get(branded_env_key(name))
    if branded_value is not None:
        return branded_value

    legacy_value = os.environ.get(legacy_env_key(name))
    if legacy_value is not None:
        _warn_legacy_env_var(name)
        return legacy_value

    return default
