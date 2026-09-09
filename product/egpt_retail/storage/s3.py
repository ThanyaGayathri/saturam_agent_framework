"""A landing root in an S3-compatible bucket: MinIO locally, AWS S3 in principle.

``boto3`` rather than the framework's ``S3Config``, which cannot address MinIO (no
``endpoint_url``) and cannot fetch an object's bytes (only ``S3Reader`` does, straight into
RAG documents). ``boto3`` is a product runtime dependency for that reason -- ``egpt[os]``
does not carry it; only the ``aws-bedrock`` extra does.

Synchronous, because ``boto3`` is. The async twins move the call to a thread rather than
pretending to await it, which is the idiom ``DatabaseContextProvider.astatus`` already uses.
``aioboto3`` would be a second AWS SDK in the image to save a thread hop on a job that is
waiting on a network anyway.

**Azure ADLS Gen2 is not implemented**, and that is a stated deferral rather than an
oversight: nothing on AKS has run a load yet, MinIO is the only store any verification in
this repository can reach, and a backend nobody exercises is precisely the "NEVER EXECUTED"
category the Helm values already flag. It is one more class behind
:class:`~egpt_retail.storage.base.ObjectStore`; ``deploy/AKS_CUTOVER.md`` carries it as a
cutover blocker.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple

from egpt_retail.storage.base import NDJSON_SUFFIX, RAW_PREFIX

__all__ = ["S3ObjectStore"]


class S3ObjectStore:
    """Reads ``{container}/{prefix}raw/...`` and returns paths relative to the landing root.

    ``prefix`` is the part of the key ahead of ``raw/`` when the landing root is not the
    bucket root. It is stripped on the way out and re-attached on the way in, so nothing
    outside this class ever sees a key the landing contract's parser would reject.
    """

    def __init__(
        self,
        *,
        container: str,
        endpoint_url: Optional[str] = None,
        region: Optional[str] = None,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        prefix: str = "",
    ) -> None:
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover -- boto3 is a runtime dependency
            raise RuntimeError(
                "boto3 is required to read the raw drop from S3 or MinIO. It is a runtime "
                "dependency of egpt-retail; reinstall the product package."
            ) from exc

        self._container = container
        # Normalised to '' or 'something/', so joining is concatenation and never grows a
        # double slash -- which S3 would treat as a real, different key.
        self._prefix = f"{prefix.strip('/')}/" if prefix.strip("/") else ""
        client_kwargs: Dict[str, Any] = {}
        if endpoint_url:
            client_kwargs["endpoint_url"] = endpoint_url
        if region:
            client_kwargs["region_name"] = region
        # Both or neither. Neither is the intended AKS path: the SDK's own credential chain,
        # which is workload identity. Exactly one is always a mistake -- a Helm secret with
        # one key mistyped -- and letting it fall through to ambient credentials would answer
        # a typo by silently using a different identity than the one configured. Refuse.
        if bool(access_key) != bool(secret_key):
            missing = (
                "EGPT_RETAIL_STORAGE_SECRET_KEY"
                if access_key
                else "EGPT_RETAIL_STORAGE_ACCESS_KEY"
            )
            raise RuntimeError(
                f"{missing} is not set while its partner is. Set both to use explicit "
                "credentials, or neither to use the ambient credential chain (workload "
                "identity on AKS). One of the two is a typo, not a configuration."
            )
        if access_key and secret_key:
            client_kwargs["aws_access_key_id"] = access_key
            client_kwargs["aws_secret_access_key"] = secret_key
        self._client = boto3.client("s3", **client_kwargs)

    def __repr__(self) -> str:
        return f"S3ObjectStore(container={self._container!r}, prefix={self._prefix!r})"

    def _to_key(self, path: str) -> str:
        return f"{self._prefix}{path}"

    def _to_path(self, key: str) -> str:
        return key[len(self._prefix) :] if self._prefix else key

    def list_objects(self, prefix: str = RAW_PREFIX) -> Tuple[str, ...]:
        paginator = self._client.get_paginator("list_objects_v2")
        paths: List[str] = []
        for page in paginator.paginate(
            Bucket=self._container, Prefix=self._to_key(prefix)
        ):
            for item in page.get("Contents", ()):
                key = item["Key"]
                if not key.endswith(NDJSON_SUFFIX):
                    continue
                paths.append(self._to_path(key))
        # S3 returns keys in lexicographic order per page, but sorting the whole result is
        # what actually guarantees the contract's fold order across page boundaries.
        return tuple(sorted(paths))

    async def alist_objects(self, prefix: str = RAW_PREFIX) -> Tuple[str, ...]:
        return await asyncio.to_thread(self.list_objects, prefix)

    def get_bytes(self, path: str) -> bytes:
        response = self._client.get_object(
            Bucket=self._container, Key=self._to_key(path)
        )
        body = response["Body"]
        try:
            return bytes(body.read())
        finally:
            body.close()

    async def aget_bytes(self, path: str) -> bytes:
        return await asyncio.to_thread(self.get_bytes, path)
