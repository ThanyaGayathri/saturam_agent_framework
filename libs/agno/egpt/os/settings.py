from __future__ import annotations

import warnings
from typing import List, Optional

from pydantic import Field, field_validator

from egpt.settings.sources import BrandedSettings, bare_env


class EgptServingSettings(BrandedSettings):
    """Serving/auth settings for the EgptOS app, settable via environment variables.

    Reference: https://pydantic-docs.helpmanual.io/usage/settings/
    """

    env: str = Field(default="dev", json_schema_extra=bare_env("ENV"))

    # Set to False to disable docs server at /docs and /redoc
    docs_enabled: bool = Field(default=True, json_schema_extra=bare_env("DOCS_ENABLED"))

    # Authentication settings
    os_security_key: Optional[str] = Field(
        default=None, description="Bearer token for API authentication", json_schema_extra=bare_env("OS_SECURITY_KEY")
    )

    # Authorization flag - when True, JWT middleware handles auth and security key validation is skipped
    authorization_enabled: bool = Field(
        default=False,
        description="Whether JWT authorization is enabled",
        json_schema_extra=bare_env("AUTHORIZATION_ENABLED"),
    )

    # Seconds to cache a successful service-account token verification in-process, to
    # avoid a database lookup on every request. Revocation takes effect within this
    # window across worker processes (immediately on the worker that processes the
    # revoke). Set to 0 for strict instant revocation (verify against the DB every time).
    service_account_cache_ttl_seconds: int = Field(
        default=30,
        description="TTL (seconds) for the in-process service-account verification cache; 0 disables it",
        json_schema_extra=bare_env("SERVICE_ACCOUNT_CACHE_TTL_SECONDS"),
    )

    # Seconds to cache a component's config row in-process during hydration (EGPT-2.5),
    # to avoid a database read per hydration (one per team member / workflow step).
    # A component mutation through the components API takes effect immediately on the
    # worker that processes it and within this window on other workers -- and on writers
    # that bypass the API (Agent.save, the studio toolkit). Set to 0 to disable the
    # cache (read the DB on every hydration).
    component_config_cache_ttl_seconds: int = Field(
        default=30,
        description="TTL (seconds) for the in-process component config hydration cache; 0 disables it",
        json_schema_extra=bare_env("COMPONENT_CONFIG_CACHE_TTL_SECONDS"),
    )

    # When True, hydration refuses any stored component config whose stage is not
    # "published" (including explicit-version reads): production should not run drafts.
    # Default False so authoring and dev flows can run drafts freely.
    hydrate_published_only: bool = Field(
        default=False,
        description="Refuse to hydrate component configs that are not published",
        json_schema_extra=bare_env("HYDRATE_PUBLISHED_ONLY"),
    )

    # When True, GET /components/*/audit requires a dedicated components:audit scope instead
    # of reusing components:read. Default False: existing components:read-only tokens keep
    # working until a deployment opts in to the split (aicoe-tracker#170). Set via
    # EGPT_COMPONENTS_AUDIT_SCOPE_ENABLED (the bare COMPONENTS_AUDIT_SCOPE_ENABLED spelling
    # also works, via bare_env's legacy fallback, but fires a FutureWarning on every read --
    # since this setting has no legacy deployments to support, the branded spelling is the
    # one to use).
    components_audit_scope_enabled: bool = Field(
        default=False,
        description="Require components:audit (instead of components:read) for the component audit "
        "endpoint. Set via EGPT_COMPONENTS_AUDIT_SCOPE_ENABLED.",
        json_schema_extra=bare_env("COMPONENTS_AUDIT_SCOPE_ENABLED"),
    )

    # Cors origin list to allow requests from.
    # This list is set using the set_cors_origin_list validator
    cors_origin_list: Optional[List[str]] = Field(
        default=None, validate_default=True, json_schema_extra=bare_env("CORS_ORIGIN_LIST")
    )

    @field_validator("cors_origin_list", mode="before")
    def set_cors_origin_list(cls, cors_origin_list):
        valid_cors = cors_origin_list or []

        # Legacy upstream (agno.com) origins, deliberately retained: removing them would
        # cut off consoles already pointed at this API. Out of scope for the EGPT-0.12
        # rebrand (aicoe-tracker#149) and tracked as its own decision — not an oversight.
        # Kept in sync with the identical default list in egpt/os/utils.py::resolve_origins.
        valid_cors.extend(
            [
                "http://localhost:3000",
                "https://agno.com",
                "https://www.agno.com",
                "https://app.agno.com",
                "https://os-stg.agno.com",
                "https://os.agno.com",
            ]
        )

        return valid_cors


_warned_agno_api_settings = False


def __getattr__(name: str):
    global _warned_agno_api_settings
    if name == "AgnoAPISettings":
        if not _warned_agno_api_settings:
            _warned_agno_api_settings = True
            warnings.warn(
                "AgnoAPISettings is deprecated; use EgptServingSettings instead.",
                FutureWarning,
                stacklevel=2,
            )
        return EgptServingSettings
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(list(globals().keys()) + ["AgnoAPISettings"])
