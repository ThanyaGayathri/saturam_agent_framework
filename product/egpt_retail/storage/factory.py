"""Building the configured object store.

Refuses an unset provider rather than defaulting to ``local``. A default would mean an
unconfigured deployment reads an empty directory, finds no files, loads nothing and reports a
clean run -- the same silent-success failure the memory startup check exists to prevent, and
the one the row-count gate would then have no baseline to catch.
"""

from __future__ import annotations

from egpt_retail.app.settings import RetailSettings
from egpt_retail.storage.base import ObjectStore
from egpt_retail.storage.local import LocalDirectoryStore
from egpt_retail.storage.s3 import S3ObjectStore

__all__ = ["build_object_store"]


def build_object_store(settings: RetailSettings) -> ObjectStore:
    """The object store this deployment reads the raw drop from.

    Pure construction for the local backend, and for S3 it builds a client that opens nothing
    until first use -- so there is no async twin (the standing exemption in ``CLAUDE.md``).
    The I/O, and both its variants, live on the store itself.
    """
    provider = settings.retail_storage_provider
    if provider is None:
        raise RuntimeError(
            "EGPT_RETAIL_STORAGE_PROVIDER is not set, so there is no raw drop to load from. "
            "Set 'local' with EGPT_RETAIL_STORAGE_LOCAL_ROOT for a directory, or 's3' with "
            "EGPT_RETAIL_STORAGE_ENDPOINT and EGPT_RETAIL_STORAGE_CONTAINER for MinIO."
        )
    if provider == "local":
        if not settings.retail_storage_local_root:
            raise RuntimeError(
                "EGPT_RETAIL_STORAGE_PROVIDER is 'local' but "
                "EGPT_RETAIL_STORAGE_LOCAL_ROOT names no directory."
            )
        return LocalDirectoryStore(settings.retail_storage_local_root)
    return S3ObjectStore(
        container=settings.retail_storage_container,
        endpoint_url=settings.retail_storage_endpoint,
        region=settings.retail_storage_region,
        access_key=settings.retail_storage_access_key,
        secret_key=settings.retail_storage_secret_key,
        prefix=settings.retail_storage_prefix,
    )
