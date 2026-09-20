"""The shape of a user-registered data source (#302, #304).

Today's harvest reads exactly one operator-configured warehouse
(``RetailSettings.retail_warehouse_db_url``, schema ``silver``) -- see
``semantic/refresh.py``. This contract is the first piece of the self-serve path: a
customer registering their own database connection through Configure's "Internal Sources"
form, which #306 will generalise harvest to read and #308 will wire the frontend to.

**Deliberately the ``InternalSource`` resource only** (``api/openapi/paths/configure.yaml``'s
``/configure/sources``), not ``ExternalIntegration`` (``/configure/integrations`` --
``ConfigureExternalForm.jsx``'s OAuth API connectors: analytics, CRM, session replay).
``InternalSource`` already existed in the API contract before #304 -- a stub-only resource
with real request/response schemas (``InternalSource``/``InternalSourceWrite``/
``InternalSourcePatch`` in ``api/openapi/schemas/configure.yaml``) but no backing store or
router, the same "contract first, wiring later" shape ``semantic.py``'s router docstring
describes for ``/semantic/*``. This module's enums and fields mirror that schema field for
field (:class:`DataSourceType` is the wire ``SourceType``, :class:`PermissionLevel` the
wire ``PermissionLevel``, and so on) so #305's router is a thin, close-to-mechanical mapping
rather than a second, drifting vocabulary. ``ExternalIntegration`` is real work #305 does not
attempt -- its OAuth connectors are a different integration shape (Lane C's connectors, not
a SQL warehouse a harvest can introspect) and belong in their own follow-on.

**Credentials never appear in a persisted or returned shape.** :class:`Credential` is
construction-only input to ``configure/secrets.py``'s ``SecretStore`` -- a
:class:`DataSource` (what gets stored, returned from the API, and logged) carries only a
``secret_ref``, the store's own handle to the material, never the material itself. This is
the contract-level enforcement of that rule: there is no field on :class:`DataSource` a
caller could accidentally serialise a password into.

Everything here is pure. There is no I/O, so the repository's sync/async rule does not
apply (see the construction exemption in ``CLAUDE.md``); ``configure/store.py`` and
``configure/secrets.py`` are where I/O against these shapes happens.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Mapping, Optional

__all__ = [
    "CONTRACT_VERSION",
    "AuthMethod",
    "ConnectionTestResult",
    "Credential",
    "DataSource",
    "DataSourceCreate",
    "DataSourceStatus",
    "DataSourceType",
    "DataSourceUpdate",
    "DataSourceValidationError",
    "HARVESTABLE_SOURCE_TYPES",
    "HarvestState",
    "PermissionLevel",
    "SSL_MODES",
    "validate_create",
    "validate_credential",
    "validate_update",
]

#: Bumped on any breaking change, per the change protocol. Additive changes do not bump it.
CONTRACT_VERSION = "1.0.0"


class DataSourceType(str, Enum):
    """The wire contract's ``SourceType`` (``api/openapi/schemas/configure.yaml``), exactly --
    this enum is checked against it by ``test_p0_data_sources.py``. Registering one is always
    possible; only :data:`HARVESTABLE_SOURCE_TYPES` can actually be harvested today."""

    POSTGRES = "postgres"
    SNOWFLAKE = "snowflake"
    SAP_S4HANA = "sap_s4hana"
    SALESFORCE = "salesforce"
    MYSQL = "mysql"
    BIGQUERY = "bigquery"
    OTHER = "other"


class AuthMethod(str, Enum):
    """The wire contract's ``AuthMethod``, exactly. ``InternalSourceWrite`` (the wire request
    body) has no ``oauth_client_id``/``oauth_client_secret`` fields -- only ``username``,
    ``password`` and ``api_key`` -- so an ``OAUTH`` credential on this resource is satisfied by
    ``api_key`` alone (a bearer/access token), never by the full client-credentials pair
    :class:`Credential` also supports for a future ``ExternalIntegration`` (client_id +
    client_secret, which that resource's wire schema does carry). See
    :func:`validate_credential`."""

    USERNAME_PASSWORD = "username_password"
    API_KEY = "api_key"
    OAUTH = "oauth"


class PermissionLevel(str, Enum):
    """The wire contract's ``PermissionLevel``, exactly. ``READ_ONLY`` is the floor R6's SQL
    guard sits on; a ``READ_WRITE`` source is never exposed to a model (the contract's own
    words) -- #306's harvest workflow and #307's SQL-guard scoping must both check this
    before ever building a connection from a stored source."""

    READ_ONLY = "read_only"
    READ_WRITE = "read_write"


class DataSourceStatus(str, Enum):
    """Connection-level status -- whether the credential reaches the source at all. Separate
    from :class:`HarvestState`, which is about the semantic layer: a source can be
    ``CONNECTED`` and still ``NOT_STARTED`` on harvest (registered, reachable, not yet
    modelled), which is the honest middle state #302 found the mock UI had no way to show."""

    REGISTERED = "registered"
    TESTING = "testing"
    CONNECTED = "connected"
    ERROR = "error"


class HarvestState(str, Enum):
    """Mirrors the frontend's ``MODEL_STATES`` (``source-model-state.js``) exactly, so #308 can
    stop hand-maintaining a parallel vocabulary once it reads this field instead of the
    hardcoded ``BY_ID`` lookup."""

    NOT_STARTED = "not_started"
    BUILDING = "building"
    MODELLED = "modelled"
    NEEDS_REVIEW = "needs_review"
    ERROR = "error"


#: Source types #306's harvest workflow can actually read today. Postgres and Snowflake are
#: SQL warehouses a ``sqlglot``-parsed guard already has a dialect for (R6); SAP and
#: Salesforce are OAuth API connectors, a different integration shape entirely, and
#: registering one ahead of that connector existing is deliberate -- see
#: :class:`DataSourceCreate`.
HARVESTABLE_SOURCE_TYPES = frozenset(
    {DataSourceType.POSTGRES, DataSourceType.SNOWFLAKE}
)

#: The ``sslmode`` values libpq accepts, spelled the way libpq spells them (hyphenated
#: ``verify-ca``/``verify-full``). ``ssl_mode`` is handed to the driver as ``sslmode=`` when a
#: source is tested or harvested, so anything outside this set would register cleanly and only
#: fail much later inside a connection attempt, with nothing pointing at the typo.
SSL_MODES = frozenset(
    {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}
)

#: Auth methods each source type's form actually collects. Checked by :func:`validate_create`
#: so a Postgres row can never end up with ``AuthMethod.OAUTH`` -- ``ConfigureInternalSourceForm.jsx``
#: never offers it, and a row shaped like that would have no code path that could ever use it.
_ALLOWED_AUTH_METHODS: Mapping[DataSourceType, frozenset] = {
    DataSourceType.POSTGRES: frozenset(
        {AuthMethod.USERNAME_PASSWORD, AuthMethod.API_KEY}
    ),
    DataSourceType.SNOWFLAKE: frozenset(
        {AuthMethod.USERNAME_PASSWORD, AuthMethod.API_KEY}
    ),
    DataSourceType.MYSQL: frozenset({AuthMethod.USERNAME_PASSWORD, AuthMethod.API_KEY}),
    DataSourceType.BIGQUERY: frozenset({AuthMethod.API_KEY, AuthMethod.OAUTH}),
    DataSourceType.SAP_S4HANA: frozenset(
        {AuthMethod.USERNAME_PASSWORD, AuthMethod.OAUTH}
    ),
    DataSourceType.SALESFORCE: frozenset({AuthMethod.OAUTH}),
    # Unknown to this deployment's closed vocabulary by definition -- every method stays
    # possible rather than guessing which one a system nobody has named yet would use.
    DataSourceType.OTHER: frozenset(AuthMethod),
}


class DataSourceValidationError(ValueError):
    """Raised by :func:`validate_create`/:func:`validate_update`/:func:`validate_credential`. Carries a human-readable
    ``field`` so a caller (the API router, in #305) can turn it into a 422 without re-deriving
    which field was wrong from the message text."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(f"{field}: {message}")
        self.field = field


@dataclass(frozen=True)
class Credential:
    """Construction-only input. Never stored, returned, or logged as a whole -- ``__repr__``
    is overridden below so an accidental ``log.info("%r", credential)`` or an uncaught
    exception's traceback cannot print a password or API key. Only ``secrets.py`` ever reads
    the real fields."""

    auth_method: AuthMethod
    username: Optional[str] = None
    password: Optional[str] = field(default=None, repr=False)
    api_key: Optional[str] = field(default=None, repr=False)
    oauth_client_id: Optional[str] = None
    oauth_client_secret: Optional[str] = field(default=None, repr=False)

    def __repr__(
        self,
    ) -> str:  # pragma: no cover -- exercised by test_no_secret_in_repr
        return f"Credential(auth_method={self.auth_method!r}, <redacted>)"


def validate_credential(source_type: DataSourceType, credential: Credential) -> None:
    """Raises :class:`DataSourceValidationError` on the first problem found. Checked at
    registration (#305), not only at use -- a source with an incomplete credential should
    never reach ``REGISTERED`` status with nothing saying why a later harvest will fail."""
    allowed = _ALLOWED_AUTH_METHODS[source_type]
    if credential.auth_method not in allowed:
        raise DataSourceValidationError(
            "credential.auth_method",
            f"{credential.auth_method.value} is not offered for {source_type.value}",
        )
    if credential.auth_method is AuthMethod.USERNAME_PASSWORD:
        if not credential.username or not credential.password:
            raise DataSourceValidationError(
                "credential", "username_password requires both username and password"
            )
    elif credential.auth_method is AuthMethod.API_KEY:
        if not credential.api_key:
            raise DataSourceValidationError("credential.api_key", "api_key is required")
    elif credential.auth_method is AuthMethod.OAUTH:
        has_token = bool(credential.api_key)
        has_client_credentials = bool(
            credential.oauth_client_id and credential.oauth_client_secret
        )
        if not has_token and not has_client_credentials:
            raise DataSourceValidationError(
                "credential",
                "oauth requires either api_key (a bearer/access token -- the only oauth "
                "shape InternalSourceWrite's wire schema carries) or both oauth_client_id "
                "and oauth_client_secret (the full client-credentials flow)",
            )


@dataclass(frozen=True)
class DataSourceCreate:
    """What ``POST /sources`` (#305) accepts. ``credential`` is consumed once, at creation --
    written to the ``SecretStore`` and discarded, never held on the resulting object."""

    name: str
    source_type: DataSourceType
    host: str
    database_name: str
    credential: Credential
    port: Optional[int] = None
    schema_name: str = ""
    ssl_mode: str = "require"
    permission_level: PermissionLevel = PermissionLevel.READ_ONLY
    created_by: str = ""


@dataclass(frozen=True)
class DataSourceUpdate:
    """What ``PATCH /sources/{id}`` (#305) accepts. Every field is optional and means "leave
    unchanged" when absent -- ``credential`` present means rotate it (write a new secret,
    replace ``secret_ref``); ``credential`` absent leaves the existing one alone, which is
    the common case (renaming a source, fixing a typo'd host) and must not force a
    re-entered password to do it."""

    name: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    database_name: Optional[str] = None
    schema_name: Optional[str] = None
    ssl_mode: Optional[str] = None
    permission_level: Optional[PermissionLevel] = None
    credential: Optional[Credential] = None


def _check_connection_fields(
    *,
    name: Optional[str],
    host: Optional[str],
    database_name: Optional[str],
    port: Optional[int],
    ssl_mode: Optional[str],
) -> None:
    """The field invariants create and update share, so the two cannot drift apart: a row
    ``validate_create`` would refuse must not be reachable by patching a good one. A ``None``
    argument means "not being set" and is skipped -- create always passes real values for
    ``name``/``host``/``database_name``, and a patch passes only the fields it carries."""
    if name is not None and not name.strip():
        raise DataSourceValidationError("name", "must not be empty")
    if host is not None and not host.strip():
        raise DataSourceValidationError("host", "must not be empty")
    if database_name is not None and not database_name.strip():
        raise DataSourceValidationError("database_name", "must not be empty")
    if port is not None and not (0 < port <= 65535):
        raise DataSourceValidationError("port", "must be between 1 and 65535")
    if ssl_mode is not None and ssl_mode not in SSL_MODES:
        raise DataSourceValidationError(
            "ssl_mode", f"must be one of {', '.join(sorted(SSL_MODES))}"
        )


def validate_create(create: DataSourceCreate) -> None:
    """Raises :class:`DataSourceValidationError` on the first problem found."""
    _check_connection_fields(
        name=create.name,
        host=create.host,
        database_name=create.database_name,
        port=create.port,
        ssl_mode=create.ssl_mode,
    )
    validate_credential(create.source_type, create.credential)


@dataclass(frozen=True)
class DataSource:
    """A registered source, as stored and returned. No credential field exists here by
    construction -- see the module docstring."""

    id: str
    name: str
    source_type: DataSourceType
    host: str
    database_name: str
    auth_method: AuthMethod
    secret_ref: str
    status: DataSourceStatus
    harvest_state: HarvestState
    created_by: str
    created_at: datetime
    updated_at: datetime
    port: Optional[int] = None
    schema_name: str = ""
    ssl_mode: str = "require"
    permission_level: PermissionLevel = PermissionLevel.READ_ONLY
    #: Non-secret -- the wire contract's own ``InternalSource.username`` is returned in the
    #: clear (only ``password``/``api_key`` are ``writeOnly``), so this is stored as a plain
    #: column rather than requiring every list/get to decrypt a credential just to show it.
    #: Kept in sync with ``Credential.username`` by ``DataSourceStore.create``/``update``
    #: whenever a credential is written or rotated; empty for an ``api_key``-only credential.
    username: str = ""
    last_tested_at: Optional[datetime] = None
    last_test_ok: Optional[bool] = None
    last_test_message: str = ""
    last_harvest_run_id: Optional[str] = None
    last_harvest_at: Optional[datetime] = None

    @property
    def harvestable(self) -> bool:
        """Whether #306's harvest workflow can be run against this source at all -- SAP and
        Salesforce register today (the form exists) but have no connector yet, and the API
        should refuse a harvest request rather than let it fail deep inside a workflow run."""
        return self.source_type in HARVESTABLE_SOURCE_TYPES


def validate_update(existing: DataSource, patch: DataSourceUpdate) -> None:
    """Raises :class:`DataSourceValidationError` if applying ``patch`` to ``existing`` would
    leave a row :func:`validate_create` would have refused. Only the fields the patch carries
    are checked; a rotated ``credential`` is checked against ``existing.source_type``, the
    source type a patch can never change. Called before anything is written, so a bad patch
    never leaves a stray secret behind."""
    _check_connection_fields(
        name=patch.name,
        host=patch.host,
        database_name=patch.database_name,
        port=patch.port,
        ssl_mode=patch.ssl_mode,
    )
    if patch.credential is not None:
        validate_credential(existing.source_type, patch.credential)


@dataclass(frozen=True)
class ConnectionTestResult:
    """What ``POST /sources/{id}/test-connection`` (#305) returns. ``message`` is a small,
    fixed vocabulary (``"unreachable"``, ``"authentication failed"``, ``"timed out"``, ...),
    never a raw driver exception -- #305's own scope note is explicit that a stack trace or
    an internal hostname must never reach the caller."""

    ok: bool
    message: str
    tested_at: datetime
