"""Read-only access to the raw drop (R4, D4).

``ObjectStore`` is the seam; ``LocalDirectoryStore``, ``S3ObjectStore`` and
``AzureBlobObjectStore`` are the backends this product ships. See ``base.py`` for the
path-normalisation rule every backend owes, ``s3.py`` for why the framework's own S3 code
could not be reused, and ``azure_blob.py`` for the Azure Blob Storage backend (AZ-A).
"""

from __future__ import annotations

from egpt_retail.storage.azure_blob import AzureBlobObjectStore
from egpt_retail.storage.base import NDJSON_SUFFIX, RAW_PREFIX, ObjectStore
from egpt_retail.storage.factory import build_object_store
from egpt_retail.storage.local import LocalDirectoryStore
from egpt_retail.storage.s3 import S3ObjectStore

__all__ = [
    "ObjectStore",
    "LocalDirectoryStore",
    "S3ObjectStore",
    "AzureBlobObjectStore",
    "build_object_store",
    "RAW_PREFIX",
    "NDJSON_SUFFIX",
]
