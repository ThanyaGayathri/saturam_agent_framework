"""``SecretStore`` -- where a data source's credential material actually lives (#304).

``configure.data_source`` (``store.py``) has no column for a password or API key, by
construction: a :class:`~egpt_retail.contracts.data_source.DataSource` row keeps only a
``secret_ref``, the store's own opaque handle to whatever this module wrote. That is the
enforcement, not a convention someone could forget -- there is no column a bug could
accidentally select, log or serialise credential bytes from.

Two implementations, chosen by :func:`build_secret_store`:

* :class:`AzureKeyVaultSecretStore` -- the deployed path. Authenticates through the ambient
  credential chain (``azure.identity.DefaultAzureCredential``), the same managed-identity
  pattern ``storage/azure_blob.py``'s ``AzureBlobObjectStore`` already uses for the raw
  drop. No shared-key option, unlike blob storage's account-key pair: Key Vault's RBAC model
  is the entire reason to use it, and an application-held master key would defeat that.
* :class:`LocalEncryptedSecretStore` -- the ``.venv``/``compose.yaml`` dev fallback.
  Fernet-encrypts the credential and stores the ciphertext in its own control-plane table,
  keyed by ``EGPT_RETAIL_SECRET_ENCRYPTION_KEY``. Never selected in a deployment that has set
  ``EGPT_RETAIL_SECRET_STORE_PROVIDER=azure_key_vault`` -- see :func:`build_secret_store`.

Both are I/O (a network call, or a database round trip), so both get the sync/async pair
every public method needs per ``CLAUDE.md``: a synchronous implementation and an
``asyncio.to_thread`` twin, the same shape every store in this package already uses.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from egpt_retail.contracts.data_source import AuthMethod, Credential
from egpt_retail.db.schema_utils import aensure_schema as _aensure_schema
from egpt_retail.db.schema_utils import ensure_schema as _ensure_schema

if TYPE_CHECKING:
    from egpt_retail.app.settings import RetailSettings

__all__ = [
    "SECRET_SCHEMA",
    "AzureKeyVaultSecretStore",
    "LocalEncryptedSecretStore",
    "SecretNotFoundError",
    "SecretStore",
    "build_secret_store",
]

SECRET_SCHEMA = "configure"


class SecretNotFoundError(KeyError):
    """Raised by ``read``/``aread`` when ``secret_ref`` names nothing the store holds --
    either it was never written here, or it was already deleted."""


@runtime_checkable
class SecretStore(Protocol):
    """What ``configure/store.py``'s ``DataSourceStore`` needs from a credential backend.
    Both implementations below satisfy it; a test fake can too, without importing either."""

    def write(self, name_hint: str, credential: Credential) -> str:
        """Store ``credential`` and return a ``secret_ref`` that later resolves it. ``name_hint``
        (the data source's own name) is never sensitive and may appear in the backend's own
        naming -- it exists purely so a human looking at Key Vault sees which source a secret
        belongs to, not to identify or look up the secret programmatically."""
        ...

    async def awrite(self, name_hint: str, credential: Credential) -> str: ...

    def read(self, secret_ref: str) -> Credential:
        """Raises :class:`SecretNotFoundError` if ``secret_ref`` names nothing stored."""
        ...

    async def aread(self, secret_ref: str) -> Credential: ...

    def delete(self, secret_ref: str) -> None:
        """A no-op, not an error, if ``secret_ref`` is already gone -- callers use this for
        best-effort cleanup after rotating or deleting a data source, and a second delete
        racing the first must not raise.

        What "delete" guarantees is backend-specific: the secret stops resolving through
        :meth:`read` on every backend, but whether the material is *erased* is not part of
        this contract -- see :class:`AzureKeyVaultSecretStore.delete`."""
        ...

    async def adelete(self, secret_ref: str) -> None: ...


def _credential_to_dict(credential: Credential) -> dict:
    return {
        "auth_method": credential.auth_method.value,
        "username": credential.username,
        "password": credential.password,
        "api_key": credential.api_key,
        "oauth_client_id": credential.oauth_client_id,
        "oauth_client_secret": credential.oauth_client_secret,
    }


def _credential_from_dict(data: dict) -> Credential:
    return Credential(
        auth_method=AuthMethod(data["auth_method"]),
        username=data.get("username"),
        password=data.get("password"),
        api_key=data.get("api_key"),
        oauth_client_id=data.get("oauth_client_id"),
        oauth_client_secret=data.get("oauth_client_secret"),
    )


# --- Azure Key Vault --------------------------------------------------------------------

_KEY_VAULT_REF_PREFIX = "azure-key-vault:"
_KEY_VAULT_NAME_DISALLOWED = re.compile(r"[^a-zA-Z0-9-]")


def _key_vault_secret_name(name_hint: str) -> str:
    """A Key Vault secret name is alphanumeric-and-dash only, <=127 chars. The uuid suffix is
    what actually identifies the secret; the slugified ``name_hint`` prefix is there only so a
    person scanning the vault can tell sources apart without opening each one."""
    slug = _KEY_VAULT_NAME_DISALLOWED.sub("-", name_hint.strip().lower()).strip("-")
    slug = slug[:40] or "source"
    return f"egpt-retail-datasource-{slug}-{uuid.uuid4().hex[:12]}"


class AzureKeyVaultSecretStore:
    """Production credential storage. See the module docstring."""

    def __init__(self, *, vault_url: str) -> None:
        try:
            from azure.identity import DefaultAzureCredential
            from azure.keyvault.secrets import SecretClient
        except (
            ImportError
        ) as exc:  # pragma: no cover -- azure-keyvault-secrets is a runtime dep
            raise RuntimeError(
                "azure-keyvault-secrets and azure-identity are required for "
                "AzureKeyVaultSecretStore. They are runtime dependencies of egpt-retail; "
                "reinstall the product package."
            ) from exc
        self._client = SecretClient(
            vault_url=vault_url, credential=DefaultAzureCredential()
        )

    def write(self, name_hint: str, credential: Credential) -> str:
        name = _key_vault_secret_name(name_hint)
        self._client.set_secret(name, json.dumps(_credential_to_dict(credential)))
        return f"{_KEY_VAULT_REF_PREFIX}{name}"

    async def awrite(self, name_hint: str, credential: Credential) -> str:
        return await asyncio.to_thread(self.write, name_hint, credential)

    def read(self, secret_ref: str) -> Credential:
        from azure.core.exceptions import ResourceNotFoundError

        if not secret_ref.startswith(_KEY_VAULT_REF_PREFIX):
            raise SecretNotFoundError(secret_ref)
        name = secret_ref[len(_KEY_VAULT_REF_PREFIX) :]
        try:
            secret = self._client.get_secret(name)
        except ResourceNotFoundError as exc:
            raise SecretNotFoundError(secret_ref) from exc
        if secret.value is None:
            # A secret can exist with a null value if something else in the vault set one
            # that way -- not a shape this store ever writes itself, but not impossible.
            raise SecretNotFoundError(secret_ref)
        return _credential_from_dict(json.loads(secret.value))

    async def aread(self, secret_ref: str) -> Credential:
        return await asyncio.to_thread(self.read, secret_ref)

    def delete(self, secret_ref: str) -> None:
        """Soft-deletes the secret: :meth:`read` raises :class:`SecretNotFoundError` from here
        on, but the credential is **not erased**. ``begin_delete_secret`` moves it to Key
        Vault's recoverable "deleted" state, where anyone holding the vault's recover
        permission can restore it until the vault's soft-delete retention period lapses (90
        days by default; the vault's own setting, not this code's). That is weaker than
        :class:`LocalEncryptedSecretStore`, whose ``DELETE`` is immediate and final -- "deleting
        a data source deletes its secret" means the two things below, not one.

        Purging (``purge_deleted_secret``) is deliberately not done here: it needs a purge
        permission this identity may not have, fails outright on a vault with purge protection
        on (which a production vault should have), and this path has only been reviewed
        against the SDK's shape, not a real vault. A deployment that needs the material gone
        sooner than the retention period should shorten the vault's retention, or purge
        out of band, until a follow-up makes it an explicit, tested step.
        """
        from azure.core.exceptions import ResourceNotFoundError

        if not secret_ref.startswith(_KEY_VAULT_REF_PREFIX):
            return
        name = secret_ref[len(_KEY_VAULT_REF_PREFIX) :]
        try:
            self._client.begin_delete_secret(name).wait()
        except ResourceNotFoundError:
            pass

    async def adelete(self, secret_ref: str) -> None:
        await asyncio.to_thread(self.delete, secret_ref)


# --- local, dev-only ---------------------------------------------------------------------

_LOCAL_REF_PREFIX = "local:"


def _local_ddl(schema: str) -> tuple:
    return (
        f"CREATE SCHEMA IF NOT EXISTS {schema}",
        f"""CREATE TABLE IF NOT EXISTS {schema}.local_secret (
            ref        text PRIMARY KEY,
            ciphertext bytea       NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        )""",
    )


class LocalEncryptedSecretStore:
    """Dev-only fallback -- see the module docstring. Not a substitute for Key Vault in any
    environment that looks deployed; :func:`build_secret_store` is the one call site that
    decides which store a process gets, and it is the thing to change, not this class."""

    def __init__(
        self, connection: Any, encryption_key: str, schema: str = SECRET_SCHEMA
    ) -> None:
        try:
            from cryptography.fernet import Fernet, InvalidToken
        except ImportError as exc:  # pragma: no cover -- cryptography is a runtime dep
            raise RuntimeError(
                "cryptography is required for LocalEncryptedSecretStore. It is a runtime "
                "dependency of egpt-retail; reinstall the product package."
            ) from exc
        self._connection = connection
        self._schema = schema
        self._invalid_token = InvalidToken
        try:
            self._fernet = Fernet(encryption_key.encode("utf-8"))
        except (ValueError, TypeError) as exc:
            raise RuntimeError(
                "EGPT_RETAIL_SECRET_ENCRYPTION_KEY is not a valid Fernet key. Generate one "
                'with `python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"`.'
            ) from exc

    def ensure_schema(self) -> None:
        _ensure_schema(
            self._connection,
            _local_ddl(self._schema),
            "egpt_retail.configure.secrets.ddl",
        )

    async def aensure_schema(self) -> None:
        await _aensure_schema(
            self._connection,
            _local_ddl(self._schema),
            "egpt_retail.configure.secrets.ddl",
        )

    def write(self, name_hint: str, credential: Credential) -> str:
        ref = f"{_LOCAL_REF_PREFIX}{uuid.uuid4().hex}"
        ciphertext = self._fernet.encrypt(
            json.dumps(_credential_to_dict(credential)).encode("utf-8")
        )
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO {self._schema}.local_secret (ref, ciphertext) VALUES (%s, %s)",
                (ref, ciphertext),
            )
        self._connection.commit()
        return ref

    async def awrite(self, name_hint: str, credential: Credential) -> str:
        return await asyncio.to_thread(self.write, name_hint, credential)

    def read(self, secret_ref: str) -> Credential:
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"SELECT ciphertext FROM {self._schema}.local_secret WHERE ref = %s",
                (secret_ref,),
            )
            row = cursor.fetchone()
        if row is None:
            raise SecretNotFoundError(secret_ref)
        try:
            payload = self._fernet.decrypt(row[0])
        except self._invalid_token as exc:
            # Wrong key, or tampered ciphertext -- either way not a "does not exist", but the
            # caller-facing failure mode is the same: this ref cannot be resolved right now.
            raise SecretNotFoundError(secret_ref) from exc
        return _credential_from_dict(json.loads(payload))

    async def aread(self, secret_ref: str) -> Credential:
        return await asyncio.to_thread(self.read, secret_ref)

    def delete(self, secret_ref: str) -> None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"DELETE FROM {self._schema}.local_secret WHERE ref = %s", (secret_ref,)
            )
        self._connection.commit()

    async def adelete(self, secret_ref: str) -> None:
        await asyncio.to_thread(self.delete, secret_ref)


def build_secret_store(settings: "RetailSettings", connection: Any) -> "SecretStore":
    """The one call site that decides which backend a process gets, from
    ``EGPT_RETAIL_SECRET_STORE_PROVIDER``. Defaults to the local store, deliberately not to
    Key Vault: an unset setting must fail obviously in a real deployment (no vault URL
    configured) rather than silently write credentials somewhere unexpected, and the local
    store already refuses to run without an explicit encryption key, so neither default is
    a silent one.
    """
    provider = settings.retail_secret_store_provider
    if provider == "azure_key_vault":
        if not settings.retail_key_vault_url:
            raise RuntimeError(
                "EGPT_RETAIL_KEY_VAULT_URL is required when "
                "EGPT_RETAIL_SECRET_STORE_PROVIDER=azure_key_vault."
            )
        return AzureKeyVaultSecretStore(vault_url=settings.retail_key_vault_url)
    if not settings.retail_secret_encryption_key:
        raise RuntimeError(
            "EGPT_RETAIL_SECRET_ENCRYPTION_KEY is required when "
            "EGPT_RETAIL_SECRET_STORE_PROVIDER=local_encrypted (the default). Generate one "
            'with `python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"`. Refused at the point of use, not at '
            "startup -- same as retail_warehouse_db_url -- a deployment that never registers "
            "a data source never needs this."
        )
    return LocalEncryptedSecretStore(connection, settings.retail_secret_encryption_key)
