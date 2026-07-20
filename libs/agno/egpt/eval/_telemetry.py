"""Shared telemetry helpers for eval classes."""

from __future__ import annotations

from typing import Any

from egpt.utils.env import get_branded_env, get_branded_env_bool


def apply_eval_telemetry_env(eval_obj: Any) -> None:
    """Override eval.telemetry from EGPT_TELEMETRY / AGNO_TELEMETRY when set."""
    if get_branded_env("TELEMETRY") is not None:
        eval_obj.telemetry = get_branded_env_bool("TELEMETRY", default=False)
