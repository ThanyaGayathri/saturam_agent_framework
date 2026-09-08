"""Retail deployment settings, from the environment.

Every setting is an ``EGPT_RETAIL_*`` environment variable. **The prefix lives in the field
name, not in ``env_prefix``.** ``BrandedEnvSettingsSource.get_field_value`` builds the key from
``field_name.upper()`` through ``branded_env_key`` and never consults ``model_config["env_prefix"]``
(``egpt/settings/sources.py``, ``egpt/utils/env.py``) -- so a subclass that sets
``env_prefix="EGPT_RETAIL_"`` binds nothing and looks correct. The ``retail_`` prefix on each
field below is what makes ``EGPT_RETAIL_CUSTOMER_SLUG`` resolve. Resolution order is the
framework's: ``EGPT_<NAME>``, then ``AGNO_<NAME>`` with a warning, then the default. No bare
spelling is opted into: ``CUSTOMER_SLUG`` unprefixed is exactly the kind of name that binds by
accident.

Two of these are contracts with other lanes rather than private knobs:

* ``retail_customer_slug`` is the tenant id stamped onto every run context. Lane K's
  ``MEMORY_SCOPE.md`` decides the slug comes from configuration; K1 (P1) is the middleware that
  stamps it. This is the name K1 inherits. In phase zero only the consolidator reads it.
* ``retail_internal_service_token`` is what the scheduler's callback authenticates with. It must
  be injected: with the scheduler on and no token given, ``EgptOS`` mints one per process
  (``os/app.py:422-426``), so two pipelines pods behind one Service reject each other's callbacks.
"""

from __future__ import annotations

from typing import Literal, Optional

from egpt.settings.sources import BrandedSettings
from pydantic import Field

__all__ = ["RetailSettings", "Role", "DEFAULT_DB_URL"]

Role = Literal["api", "pipelines"]

#: The cookbook and CI Postgres: ``agnohq/pgvector:18`` on host port 5532, database ``ai``.
DEFAULT_DB_URL = "postgresql+psycopg://ai:ai@localhost:5532/ai"


class RetailSettings(BrandedSettings):
    """Everything the retail deployment reads from its environment."""

    retail_customer_slug: Optional[str] = Field(
        default=None,
        description="The customer's tenant id, stamped on every run context (EGPT_RETAIL_CUSTOMER_SLUG). "
        "Unset means no memory scope is configured, which the consolidator reports and exits on.",
    )
    retail_role: Role = Field(
        default="api",
        description="Which deployment this process is: 'api' serves requests; 'pipelines' also runs the "
        "scheduler poller (EGPT_RETAIL_ROLE). Both import the same app.",
    )
    retail_db_url: str = Field(
        default=DEFAULT_DB_URL,
        description="Control-plane Postgres, SQLAlchemy form with the psycopg driver (EGPT_RETAIL_DB_URL). "
        "The memory vector store uses the same database in phase zero.",
    )
    retail_db_direct_url: Optional[str] = Field(
        default=None,
        description="A connection that bypasses any pooler, for the consolidator's session-scoped advisory "
        "lock (EGPT_RETAIL_DB_DIRECT_URL). Through a transaction-mode pooler the session ends with the "
        "first transaction and the lock evaporates. Defaults to retail_db_url.",
    )
    retail_auth_jwks_file: Optional[str] = Field(
        default=None,
        description="Path to a JWKS file of RS256 public keys (EGPT_RETAIL_AUTH_JWKS_FILE). Setting it turns "
        "on JWT authorization: locally an offline key pair, on AKS the identity provider's keys.",
    )
    retail_auth_audience: Optional[str] = Field(
        default=None,
        description="Expected `aud` claim when authorization is on (EGPT_RETAIL_AUTH_AUDIENCE). Unset skips "
        "audience verification -- refused by build_agent_os whenever a JWKS file is configured, unless "
        "retail_auth_skip_audience_check is set.",
    )
    retail_auth_skip_audience_check: bool = Field(
        default=False,
        description="Escape hatch for local use only: lets authorization run with no EGPT_RETAIL_AUTH_AUDIENCE, "
        "accepting any `aud` claim (EGPT_RETAIL_AUTH_SKIP_AUDIENCE_CHECK). Never set this against a shared "
        "identity provider -- with Entra, every RS256 token the JWKS can verify would be accepted regardless of "
        "which application it was issued for.",
    )
    retail_warehouse_db_url: Optional[str] = Field(
        default=None,
        description="The separate warehouse database for landing, silver and gold (EGPT_RETAIL_WAREHOUSE_DB_URL). "
        "Declared here because the compose stack creates it; nothing reads it until R4.",
    )
    retail_internal_service_token: Optional[str] = Field(
        default=None,
        description="Shared token for scheduler-to-OS callbacks (EGPT_RETAIL_INTERNAL_SERVICE_TOKEN). "
        "Required when the role is 'pipelines'.",
    )
    retail_scheduler_base_url: Optional[str] = Field(
        default=None,
        description="Where the scheduler posts its callbacks (EGPT_RETAIL_SCHEDULER_BASE_URL). "
        "Defaults to the framework's own default, which is this process.",
    )
    retail_azure_deployment: str = Field(
        default="gpt-5.4",
        description="Azure OpenAI chat deployment for the registered agents (EGPT_RETAIL_AZURE_DEPLOYMENT). "
        "Credentials come from AZURE_OPENAI_* at call time, never at construction.",
    )
    retail_consolidation_poll_interval: float = Field(
        default=300.0,
        description="Seconds between consolidator ticks in loop mode (EGPT_RETAIL_CONSOLIDATION_POLL_INTERVAL).",
    )
    retail_consolidation_max_passes: int = Field(
        default=20,
        description="Bounded passes per scope per tick (EGPT_RETAIL_CONSOLIDATION_MAX_PASSES).",
    )

    @property
    def scheduler_enabled(self) -> bool:
        return self.retail_role == "pipelines"

    @property
    def authorization_enabled(self) -> bool:
        return bool(self.retail_auth_jwks_file)

    @property
    def db_direct_url(self) -> str:
        return self.retail_db_direct_url or self.retail_db_url
