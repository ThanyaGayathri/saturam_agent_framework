"""Tracing/observability settings (Epic 4).

Follows the per-domain-file convention established in ``settings/providers.py``.
Every environment read for Langfuse/tracing goes through this module.

Environment variables (three-way resolution: EGPT_ > AGNO_ > vendor bare):
- EGPT_LANGFUSE_PUBLIC_KEY / LANGFUSE_PUBLIC_KEY (vendor convention)
- EGPT_LANGFUSE_SECRET_KEY / LANGFUSE_SECRET_KEY (vendor convention)
- EGPT_LANGFUSE_HOST / LANGFUSE_HOST (vendor convention)
- EGPT_LANGFUSE_ENABLED (EGPT-owned flag, no bare form)
- EGPT_TRACING_DB_ENABLED (EGPT-owned flag, no bare form)
"""

from __future__ import annotations

from typing import Optional

from pydantic import Field, SecretStr

from egpt.settings.sources import BrandedSettings, vendor_env


class TracingSettings(BrandedSettings):
    """Environment configuration for tracing/observability backends.

    When ``langfuse_public_key`` and ``langfuse_secret_key`` are set, Langfuse
    export is enabled by default.  Set ``langfuse_enabled = false`` to explicitly
    disable even when keys are present.
    """

    # -- Langfuse (EGPT-4.1) -----------------------------------------------
    # LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_HOST are the vendor's
    # own convention (the langfuse SDK reads them), so we use vendor_env (no
    # deprecation warning on the bare name) rather than bare_env.

    langfuse_public_key: Optional[str] = Field(
        default=None,
        description="Langfuse public key for trace export",
        json_schema_extra=vendor_env("LANGFUSE_PUBLIC_KEY"),
    )

    langfuse_secret_key: Optional[SecretStr] = Field(
        default=None,
        description="Langfuse secret key for trace export",
        json_schema_extra=vendor_env("LANGFUSE_SECRET_KEY"),
    )

    langfuse_host: Optional[str] = Field(
        default=None,
        description="Langfuse host URL (e.g. https://cloud.langfuse.com). Defaults to Langfuse cloud if unset.",
        json_schema_extra=vendor_env("LANGFUSE_HOST"),
    )

    # LANGFUSE_ENABLED is an EGPT-invented flag (not a Langfuse SDK convention),
    # so it is EGPT_-prefixed only — no bare form, no vendor fallback.
    langfuse_enabled: Optional[bool] = Field(
        default=None,
        description=(
            "Explicit toggle for Langfuse export. "
            "When None (default), export is enabled if keys are present. "
            "Set to False to disable even when keys are configured."
        ),
    )

    # -- DB exporter (existing) ---------------------------------------------
    # EGPT-owned flag, no bare form.

    tracing_db_enabled: bool = Field(
        default=True,
        description="Whether to export spans to the database (existing DatabaseSpanExporter)",
    )

    @property
    def langfuse_is_configured(self) -> bool:
        """True if Langfuse keys are set and export is not explicitly disabled."""
        if self.langfuse_enabled is False:
            return False
        return bool(self.langfuse_public_key and self.langfuse_secret_key)


def get_tracing_settings() -> TracingSettings:
    """Build a fresh snapshot of tracing settings from the current environment."""
    return TracingSettings()
