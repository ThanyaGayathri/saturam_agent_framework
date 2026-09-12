"""A landing root in Azure Blob Storage: Azurite locally, real Azure in production.

``azure-storage-blob`` rather than the framework's own knowledge-side S3 connectors -- there
is nothing Azure-Blob-shaped in the framework to reuse, the same gap ``s3.py`` documents for
the S3 case. This is a product runtime dependency for that reason: ``egpt[os]`` carries no
Azure SDK at all.

Synchronous, because ``azure-storage-blob``'s default client is. The async twins move the
call to a thread rather than pretending to await it, matching ``S3ObjectStore``'s idiom and
``DatabaseContextProvider.astatus`` before it.

This is the Azure ADLS Gen2 deferral ``s3.py`` names as a stated gap -- it is now implemented,
closing gap #5 of the Azure migration plan (``docs/plan/azure-migration-plan.md``, package
AZ-A).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, List, Optional, Tuple

from egpt_retail.storage.base import (
    NDJSON_SUFFIX,
    RAW_PREFIX,
    validate_object_path,
)

if TYPE_CHECKING:
    from azure.storage.blob import BlobServiceClient

__all__ = ["AzureBlobObjectStore"]


class AzureBlobObjectStore:
    """Reads ``{container}/{prefix}raw/...`` and returns paths relative to the landing root.

    ``prefix`` is the part of the blob name ahead of ``raw/`` when the landing root is not the
    container root. It is stripped on the way out and re-attached on the way in, so nothing
    outside this class ever sees a blob name the landing contract's parser would reject.

    Three ways to authenticate, resolved in this order:

    1. ``connection_string`` -- a full connection string (account, endpoint and key bundled
       together). Takes precedence because it is unambiguous: if it's set, that is the account.
    2. ``account_name``/``account_key`` -- a shared-key pair, the Azure analogue of S3's
       access/secret pair. Both or neither, enforced below for the same reason
       ``S3ObjectStore`` enforces it for its own pair.
    3. Neither -- the ambient credential chain (``azure.identity.DefaultAzureCredential``),
       which resolves to managed identity on Container Apps. ``account_url`` (or
       ``account_name``, from which the default ``*.blob.core.windows.net`` URL is derived)
       must still be set, because *something* has to address the account even when nothing
       authenticates explicitly.
    """

    def __init__(
        self,
        *,
        container: str,
        account_url: Optional[str] = None,
        account_name: Optional[str] = None,
        account_key: Optional[str] = None,
        connection_string: Optional[str] = None,
        prefix: str = "",
    ) -> None:
        try:
            import azure.storage.blob  # noqa: F401
        except (
            ImportError
        ) as exc:  # pragma: no cover -- azure-storage-blob is a runtime dep
            raise RuntimeError(
                "azure-storage-blob is required to read the raw drop from Azure Blob "
                "Storage. It is a runtime dependency of egpt-retail; reinstall the product "
                "package."
            ) from exc

        self._container = container
        # Normalised to '' or 'something/', so joining is concatenation and never grows a
        # double slash -- which Azure Blob would treat as a real, different blob name.
        self._prefix = f"{prefix.strip('/')}/" if prefix.strip("/") else ""
        self._connection_string = connection_string

        # Both or neither. Neither is the intended Container Apps path: DefaultAzureCredential's
        # own chain, which is managed identity there. Exactly one is always a mistake -- a
        # Key Vault secret with one value mistyped -- and letting it fall through to the
        # ambient chain would answer a typo by silently using a different identity than the
        # one configured. Refuse, the same way S3ObjectStore refuses a half-set access-key
        # pair. A connection string already bundles its own key, so this pair is only
        # meaningful when no connection string is given.
        if not connection_string and bool(account_name) != bool(account_key):
            missing = (
                "EGPT_RETAIL_STORAGE_ACCOUNT_KEY"
                if account_name
                else "EGPT_RETAIL_STORAGE_ACCOUNT_NAME"
            )
            raise RuntimeError(
                f"{missing} is not set while its partner is. Set both to use an explicit "
                "shared key, or neither to use the ambient credential chain (managed "
                "identity on Container Apps). One of the two is a typo, not a configuration."
            )

        self._account_url = account_url or (
            f"https://{account_name}.blob.core.windows.net" if account_name else None
        )
        if not connection_string and not self._account_url:
            raise RuntimeError(
                "None of EGPT_RETAIL_STORAGE_ACCOUNT_URL, EGPT_RETAIL_STORAGE_ACCOUNT_NAME or "
                "a connection string is set, so there is no account to address. Set the "
                "account URL (or name) explicitly, or a full connection string."
            )
        self._account_key = account_key
        # Lazy: constructing DefaultAzureCredential can probe the environment (IMDS, env-var
        # lookups) and this class must stay constructible -- and its credential validation
        # above testable -- with no network reachable at all. Only the first real call pays
        # for building the actual client, the same "opens nothing until first use" contract
        # build_object_store's docstring already promises for S3.
        self._client: Optional["BlobServiceClient"] = None

    def __repr__(self) -> str:
        return f"AzureBlobObjectStore(container={self._container!r}, prefix={self._prefix!r})"

    @property
    def _blob_service_client(self) -> "BlobServiceClient":
        if self._client is None:
            from azure.storage.blob import BlobServiceClient

            if self._connection_string:
                self._client = BlobServiceClient.from_connection_string(
                    self._connection_string
                )
            else:
                # __init__ already refused to reach here without an account URL unless a
                # connection string was given, and that branch returns above -- so this is
                # always set. Asserted rather than silently passed as Optional[str], since
                # BlobServiceClient's own signature requires a real str.
                assert self._account_url is not None
                if self._account_key:
                    self._client = BlobServiceClient(
                        account_url=self._account_url, credential=self._account_key
                    )
                else:
                    from azure.identity import DefaultAzureCredential

                    self._client = BlobServiceClient(
                        account_url=self._account_url,
                        credential=DefaultAzureCredential(),
                    )
        return self._client

    def _to_key(self, path: str) -> str:
        # Blob names are flat strings, not filesystem paths, so "raw/../../etc/passwd" does
        # not resolve outside anything the way it would for LocalDirectoryStore -- there is no
        # directory to escape. But a landed path comes from object storage, which is to say
        # from outside this process, and anything that is not a plain relative path reaching
        # this far is evidence of something wrong upstream. validate_object_path is the shared
        # check every backend runs, so the three read identically to a caller -- an earlier
        # ".."-only check here refused one traversal spelling and silently forwarded the rest
        # ("/etc/passwd", "raw\\..\\..") as different, unvalidated blob keys.
        validate_object_path(path, f"{self._container}/{self._prefix}")
        return f"{self._prefix}{path}"

    def _to_path(self, key: str) -> str:
        return key[len(self._prefix) :] if self._prefix else key

    def list_objects(self, prefix: str = RAW_PREFIX) -> Tuple[str, ...]:
        container_client = self._blob_service_client.get_container_client(
            self._container
        )
        paths: List[str] = []
        for blob in container_client.list_blobs(name_starts_with=self._to_key(prefix)):
            # `.name` is already a plain forward-slash-delimited string -- Azure Blob has no
            # native concept of an OS path separator, so there is no `.as_posix()`-equivalent
            # conversion to do here. It is still worth stating: this is what makes the value
            # returned parse_landing_path's forward-slash-anchored regex without care, the
            # same guarantee LocalDirectoryStore earns with `.as_posix()`.
            key = blob.name
            if not key.endswith(NDJSON_SUFFIX):
                continue
            paths.append(self._to_path(key))
        # Azure Blob's listing is already lexicographically ordered, but sorting the whole
        # result is what actually guarantees the contract's fold order across page boundaries.
        return tuple(sorted(paths))

    async def alist_objects(self, prefix: str = RAW_PREFIX) -> Tuple[str, ...]:
        return await asyncio.to_thread(self.list_objects, prefix)

    def get_bytes(self, path: str) -> bytes:
        container_client = self._blob_service_client.get_container_client(
            self._container
        )
        blob_client = container_client.get_blob_client(self._to_key(path))
        return blob_client.download_blob().readall()

    async def aget_bytes(self, path: str) -> bytes:
        return await asyncio.to_thread(self.get_bytes, path)
