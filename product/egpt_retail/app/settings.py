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

import re
from typing import Literal, Optional

from egpt.metrics import ProviderMode
from egpt.settings.sources import BrandedSettings
from pydantic import Field, field_validator

__all__ = ["RetailSettings", "Role", "StorageProvider", "DEFAULT_DB_URL"]

Role = Literal["api", "pipelines"]

#: Where the raw drop lives. ``s3`` covers MinIO as well as AWS -- MinIO speaks the S3 API,
#: which is why the Helm chart's ``storage.provider`` reads ``s3`` rather than ``minio``.
#: ``azure_blob`` is the Azure Container Apps target (AZ-A,
#: ``docs/plan/azure-migration-plan.md``), backed by ``egpt_retail.storage.AzureBlobObjectStore``.
StorageProvider = Literal["local", "s3", "azure_blob"]

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
    retail_neo4j_uri: str = Field(
        default="neo4j://localhost:7701",
        description="Bolt URI for the ontology graph (EGPT_RETAIL_NEO4J_URI). The shipped retail "
        "ontology is written here under its own OntologyConcept label, outside the memory substrate "
        "(D11). The local default is the dedicated dev container, not a shared one.",
    )
    retail_neo4j_user: str = Field(
        default="neo4j",
        description="Neo4j username (EGPT_RETAIL_NEO4J_USER).",
    )
    retail_neo4j_password: Optional[str] = Field(
        default=None,
        description="Neo4j password (EGPT_RETAIL_NEO4J_PASSWORD). Unset means the ontology loader "
        "has nowhere to write and says so rather than starting.",
    )
    retail_neo4j_database: str = Field(
        default="neo4j",
        description="Neo4j database for the ontology (EGPT_RETAIL_NEO4J_DATABASE). Community edition "
        "supports one; the two-database split D11 assumes needs Enterprise.",
    )
    retail_provider_mode: ProviderMode = Field(
        default=ProviderMode.FRONTIER,
        description="Which embedder family memory AND the knowledge layers use "
        "(EGPT_RETAIL_PROVIDER_MODE). One setting for both, so a deployment never holds two vector "
        "spaces. Default is 'frontier' -- Azure text-embedding-3-large at 1024 -- because that is what "
        "the deployment runs; a laptop with no Azure embedding deployment sets 'local' explicitly "
        "(bge-base-en-v1.5 at 768, offline-capable per ADR 0003 A.3c). The default is the production "
        "truth on purpose: running the wrong mode should fail loudly at the first embed, not work "
        "silently on a different model.",
    )
    retail_corpus_path: Optional[str] = Field(
        default=None,
        description="Directory of curated retail markdown to ingest (EGPT_RETAIL_CORPUS_PATH). "
        "Defaults to the corpus shipped inside the package.",
    )
    retail_memory_table: str = Field(
        default="memory_vectors",
        description="pgvector table for memory (EGPT_RETAIL_MEMORY_TABLE). Configurable because "
        "the table is created at the embedder's dimension and the framework refuses to reuse one "
        "of a different width -- correctly, since changing dimension is a re-embedding migration. "
        "On a dev box that means Mode A (768) and Mode B (1024) cannot share a table, so a "
        "cookbook demonstrating one must not write into the deployment's.",
    )

    @field_validator("retail_memory_table")
    @classmethod
    def _memory_table_is_an_identifier(cls, value: str) -> str:
        """The name is interpolated into DDL by the substrate. An environment variable is not
        user input, but a value with a dot or a quote in it would fail somewhere far from
        here, and refusing at settings time names the variable."""
        if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", value):
            raise ValueError(
                f"EGPT_RETAIL_MEMORY_TABLE must be a plain lowercase identifier, got {value!r}"
            )
        return value

    retail_allow_unsafe_gold_options: bool = Field(
        default=False,
        description=(
            "Permit a workflow's additional_data to disable gold's quality gate "
            "(allow_unverified_silver) or its statement timeout "
            "(EGPT_RETAIL_ALLOW_UNSAFE_GOLD_OPTIONS). Off by default: that bag is "
            "client-supplied, so without this whoever can start a workflow can write gold "
            "from unverified silver. On for a deliberate backfill, and only then."
        ),
    )
    retail_knowledge_table: str = Field(
        default="egpt_retail_domain_prose",
        description="pgvector table for the domain prose (EGPT_RETAIL_KNOWLEDGE_TABLE).",
    )
    retail_ontology_overlay: Optional[str] = Field(
        default=None,
        description="Path to this deployment's ontology overlay (EGPT_RETAIL_ONTOLOGY_OVERLAY). "
        "The overlay may add and annotate; it may never redefine a shipped concept (G10).",
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
        "Read by `warehouse-migrate` and by R4's landing-to-silver workflow. Unset is refused at the point "
        "of use, not at startup: the api and consolidator roles never touch the warehouse.",
    )
    retail_warehouse_readonly_db_url: Optional[str] = Field(
        default=None,
        description="The read-only warehouse connection (EGPT_RETAIL_WAREHOUSE_READONLY_DB_URL), authenticated "
        "as `egpt_reader` (warehouse/sql/ddl/065_readonly_role.sql). R6's SQL guard sits above this "
        "connection, not in place of it -- the role is the floor, the guard is the ceiling. Unset is "
        "refused at the point of use, same as retail_warehouse_db_url.",
    )
    retail_storage_provider: Optional[StorageProvider] = Field(
        default=None,
        description="Where the raw drop lives: 'local' a directory, 's3' any S3-compatible endpoint "
        "including MinIO, 'azure_blob' Azure Blob Storage or Azurite (EGPT_RETAIL_STORAGE_PROVIDER). "
        "Unset is refused when a load runs, rather than defaulting to 'local' and silently reading an "
        "empty directory.",
    )
    retail_storage_endpoint: Optional[str] = Field(
        default=None,
        description="S3 endpoint URL (EGPT_RETAIL_STORAGE_ENDPOINT). Set for MinIO "
        "(http://egpt-minio:9000); leave unset for real AWS, which resolves its own.",
    )
    retail_storage_container: str = Field(
        default="egpt-landing",
        description="Bucket or container holding the raw drop (EGPT_RETAIL_STORAGE_CONTAINER). The name is "
        "reserved by the local-infra chart and by C1's verification.",
    )
    retail_storage_prefix: str = Field(
        default="",
        description="Key prefix ahead of 'raw/' when the landing root is not the container root "
        "(EGPT_RETAIL_STORAGE_PREFIX). Stripped before a path reaches the landing contract's parser.",
    )
    retail_storage_region: Optional[str] = Field(
        default=None,
        description="S3 region (EGPT_RETAIL_STORAGE_REGION). Unused by MinIO.",
    )
    retail_storage_access_key: Optional[str] = Field(
        default=None,
        description="S3 access key (EGPT_RETAIL_STORAGE_ACCESS_KEY). Unset falls back to the SDK's own "
        "credential chain, which is how workload identity is meant to work on AKS.",
    )
    retail_storage_secret_key: Optional[str] = Field(
        default=None,
        description="S3 secret key (EGPT_RETAIL_STORAGE_SECRET_KEY).",
    )
    retail_storage_local_root: Optional[str] = Field(
        default=None,
        description="Directory holding the 'raw/' subtree when the provider is 'local' "
        "(EGPT_RETAIL_STORAGE_LOCAL_ROOT). Used by the R4 cookbook against C1's committed fixtures.",
    )
    retail_storage_account_url: Optional[str] = Field(
        default=None,
        description="Azure Blob account URL when the provider is 'azure_blob' "
        "(EGPT_RETAIL_STORAGE_ACCOUNT_URL), e.g. https://<account>.blob.core.windows.net or "
        "Azurite's http://127.0.0.1:10000/devstoreaccount1. Derived from "
        "retail_storage_account_name when unset and a name is given.",
    )
    retail_storage_account_name: Optional[str] = Field(
        default=None,
        description="Azure Blob storage account name (EGPT_RETAIL_STORAGE_ACCOUNT_NAME). Paired with "
        "retail_storage_account_key for shared-key auth; either both or neither is set, the same rule "
        "retail_storage_access_key/_secret_key enforce for S3.",
    )
    retail_storage_account_key: Optional[str] = Field(
        default=None,
        description="Azure Blob storage account key (EGPT_RETAIL_STORAGE_ACCOUNT_KEY). Unset, with "
        "retail_storage_account_name also unset, falls back to the SDK's ambient credential chain "
        "(DefaultAzureCredential), which is how managed identity is meant to work on Container Apps.",
    )
    retail_storage_connection_string: Optional[str] = Field(
        default=None,
        description="A full Azure Blob connection string (EGPT_RETAIL_STORAGE_CONNECTION_STRING), "
        "bundling account, endpoint and key together. Azurite's well-known development connection "
        "string is the common local value. Takes precedence over account_name/account_key when set, "
        "since it is unambiguous about which account it addresses. A separate field rather than "
        "overloading retail_storage_account_url: a connection string is not a URL and mixing the two "
        "shapes in one field would make the 'both or neither' credential-pair check unreadable.",
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
        description="Azure OpenAI chat deployment for the frontier tier (EGPT_RETAIL_AZURE_DEPLOYMENT). "
        "R8's routing table binds frontier-tier agents here and escalates small-tier agents here. "
        "Credentials come from AZURE_OPENAI_* at call time, never at construction.",
    )
    retail_azure_small_deployment: str = Field(
        default="gpt-5.4-mini",
        description="Azure OpenAI chat deployment for the small tier (EGPT_RETAIL_AZURE_SMALL_DEPLOYMENT). "
        "Both tiers are Azure OpenAI deployments on the same code path -- the build plan defers the "
        "in-VPC GPU pool -- so 'small' is a cheaper deployment, not a different provider. Same "
        "credential rule as the frontier deployment.",
    )
    retail_small_context_window: int = Field(
        default=128_000,
        gt=0,
        description="The small deployment's context window, in tokens (EGPT_RETAIL_SMALL_CONTEXT_WINDOW). "
        "Declared here because the Mode B provider cannot state one without asking a server, and the "
        "escalation rule `input_exceeds_context_window` never fires on an unknown window -- so without "
        "this, a request too large for the small tier would reach it and fail there instead of escalating "
        "before the call. Set it to the deployment's real limit; a smaller value escalates earlier, never "
        "wrongly.",
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
