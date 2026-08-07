"""Shared branded-env pydantic-settings source.

Every EGPT settings model reads through :class:`BrandedEnvSettingsSource`
instead of the default env source, so ``EGPT_*`` / ``AGNO_*`` / (opt-in) bare
names resolve the same way everywhere. This module must stay a leaf: it may
only import ``pydantic``, ``pydantic_settings``, and ``egpt.utils.env`` --
nothing under ``egpt.os`` or ``egpt.api``, which import *this* module.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple, Type

from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, EnvSettingsSource, PydanticBaseSettingsSource, SettingsConfigDict

from egpt.utils.env import get_branded_env_with_bare_fallback

_BARE_ENV_KEY = "egpt_bare_env"


def bare_env(name: str) -> Dict[str, Any]:
    """Mark a field as additionally readable from the unprefixed ``name``.

    Opt-in per field, never blanket: bare names live in the shared OS
    namespace, and binding something like ``ENV`` or ``PORT`` by accident is
    how a settings object starts reading someone else's variables.
    """
    return {_BARE_ENV_KEY: name}


def bare_env_name(field: FieldInfo) -> Optional[str]:
    """Return the bare env name a field opted into via :func:`bare_env`, if any."""
    extra = field.json_schema_extra
    if isinstance(extra, dict):
        value = extra.get(_BARE_ENV_KEY)
        if isinstance(value, str):
            return value
    return None


class BrandedEnvSettingsSource(EnvSettingsSource):
    """Read ``EGPT_<FIELD>``, then ``AGNO_<FIELD>``, then an opt-in bare name.

    Subclasses :class:`EnvSettingsSource` (not ``PydanticBaseSettingsSource``)
    so the inherited ``prepare_field_value`` keeps JSON-decoding complex
    fields such as ``List[str]``. A hand-rolled source that builds its result
    dict directly would turn e.g. ``CORS_ORIGIN_LIST='["https://x"]'`` into a
    ``ValidationError``.

    Lookup goes through ``egpt.utils.env`` rather than ``self.env_vars``
    (snapshotted at source construction) so reads stay live, matching the
    rest of the branded-env machinery. One deliberate narrowing: this lookup
    is case-exact, where the default env source is case-insensitive, so a
    lowercase ``os_security_key=x`` no longer binds.
    """

    def get_field_value(self, field: FieldInfo, field_name: str) -> Tuple[Any, str, bool]:
        value = get_branded_env_with_bare_fallback(field_name.upper(), bare=bare_env_name(field))
        return value, field_name, self.field_is_complex(field)


class BrandedSettings(BaseSettings):
    """Base for every EGPT settings model: branded source only, no bare env_settings."""

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
        # env_settings is dropped, not reordered: leaving it in would let an
        # unprefixed name bind without going through the deprecation warning.
        return (init_settings, BrandedEnvSettingsSource(settings_cls), dotenv_settings, file_secret_settings)
