"""Per-domain settings for the model provider seam (Epic 3).

Follows the per-domain-file convention: the central ``EgptSettings`` in
``egpt/settings/__init__.py`` owns the framework's own serving/auth surface, and each lane adds
its own module here rather than appending to that shared class body.

Every environment read for Epic 3 goes through this module. ``tests/unit/test_env_read_guard.py``
is a ratchet checked in both directions -- a raw ``getenv`` under ``egpt/providers/`` fails it,
and so does an allowlist entry with no matching access -- so providers must read settings from
here rather than reaching for the environment themselves.

Field blocks below are partitioned by ticket. EGPT-3.2, 3.3 and 3.4 are built in parallel
against this file; each adds fields only inside its own block so the three diffs stay
line-disjoint. A block that needs ``pydantic.Field`` or ``bare_env`` adds that import itself --
they are omitted here rather than pre-imported, because an unused import fails ruff and a
placeholder reference to silence it is worse than three branches touching one import line.
"""

from __future__ import annotations

from typing import Any, Optional

from egpt.settings.sources import BrandedSettings


class ProviderSettings(BrandedSettings):
    """Environment configuration for dual-mode model resolution.

    Read via ``EGPT_<FIELD>``, falling back to ``AGNO_<FIELD>`` and then to an opt-in bare name
    where a field declares one -- the same three-way chain every other EGPT settings model uses.
    """

    # -- shared (EGPT-3.1) -------------------------------------------------
    # Which mode to use when nothing more specific applies. None means "no mode configured",
    # which is the seam's no-op path: agents resolve their model exactly as before Epic 3.
    default_mode: Optional[str] = None

    # -- config-driven resolution (EGPT-3.2) -------------------------------
    # Owned by #64. Path to the per-tenant policy document, and the fallback identity claim.
    # (Add fields here.)

    # -- Mode A: self-hosted SLM (EGPT-3.3) --------------------------------
    # Owned by #65. The in-VPC endpoint and adapter configuration.
    #
    # Note for the implementer: `mode_a_base_url` must have NO default that points anywhere
    # public. `egpt/integrations/models/ollama/chat.py:61` falls back to `https://ollama.com`
    # and `vllm.py:57` to `http://localhost:8000/v1/` when unset, so a Mode A binding that
    # lets either default through has silently left the VPC. Raise
    # ModelProviderConfigError instead.
    # (Add fields here.)

    # -- Mode B: frontier provider (EGPT-3.4) ------------------------------
    # Owned by #66. Frontier selection plus optional gateway routing.
    #
    # Note for the implementer: credentials for the frontier SDKs (OPENAI_API_KEY, etc.) are
    # deliberately NOT fields here. Their names are owned by the provider SDK convention, and
    # `egpt/settings/__init__.py` documents why the framework does not re-namespace them. Only
    # gateway endpoints and EGPT-owned routing config belong in this block.
    #
    # Which frontier backend to build when the caller pins nothing. These are registry keys
    # ("anthropic", "openai", "google", "aws-bedrock", "azure-openai"), not import paths:
    # naming one imports nothing, and `discovery.construct` turns the string into a class only
    # when a resolution actually happens.
    mode_b_provider: Optional[str] = None
    mode_b_model: Optional[str] = None

    # Optional gateway in front of the frontier. None means "talk to the frontier directly".
    # "portkey" is the tested path; "litellm" is code-only and raises at resolve time while the
    # package stays blocked (libs/agno/pyproject.toml ~line 249).
    mode_b_gateway: Optional[str] = None
    # EGPT-owned routing config for that gateway.
    #
    # Deliberately NOT marked `bare_env("PORTKEY_API_KEY")`. In this codebase `bare_env` does
    # not mean "also readable" -- `get_branded_env_with_bare_fallback` routes the bare name
    # through `_warn_bare_env_var`, which emits `FutureWarning: PORTKEY_API_KEY is deprecated`.
    # Every other `bare_env` in the tree names a legacy EGPT/agno-owned spelling, but
    # `PORTKEY_*` is the vendor's own convention: `integrations/models/portkey/portkey.py`
    # reads `getenv("PORTKEY_VIRTUAL_KEY")` directly and names these variables in its own
    # errors. Marking them deprecated told a correctly-configured deployment its Portkey setup
    # was obsolete, and broke anything running under `-W error::FutureWarning`.
    #
    # The vendor names still work -- the Portkey class reads them itself, which is exactly the
    # arrangement the frontier-credential comment above describes.
    mode_b_gateway_api_key: Optional[str] = None
    mode_b_gateway_base_url: Optional[str] = None
    mode_b_gateway_virtual_key: Optional[str] = None


def get_provider_settings(**overrides: Any) -> ProviderSettings:
    """Build a fresh snapshot of the provider settings from the current environment.

    No module-level singleton and no cache, matching ``egpt.settings.get_settings``: a test
    that sets an endpoint after an earlier test warmed a cache must not read a stale value.
    """
    return ProviderSettings(**overrides)


__all__ = ["ProviderSettings", "get_provider_settings"]
