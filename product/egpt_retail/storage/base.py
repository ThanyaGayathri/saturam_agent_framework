"""The object-store seam: read-only access to the raw drop (R4, D4).

One protocol, two backends. The framework has nothing reusable here --
``egpt.integrations.knowledge.remote_content.s3.S3Config`` lists objects but has no
``endpoint_url`` field, so it cannot address MinIO at all, and no get-object surface, because
bytes are fetched inside ``S3Reader`` and immediately chunked into RAG documents. Extending
it would be a framework change, which R4's scope excludes; the gap is worth naming rather
than quietly working around.

**Paths in and out are always landing-root-relative and start with ``raw/``.** That is the
only form :func:`~egpt_retail.contracts.landing.parse_landing_path` accepts -- it anchors on
``^raw/``, so a bucket-qualified key or an absolute filesystem path is rejected outright.
Every backend therefore strips its own container, prefix or root before returning a path, and
``product/tests/test_r4_storage.py`` asserts that everything returned actually parses. This
normalisation is the seam's entire reason for existing and the likeliest place for an
integration bug to hide.
"""

from __future__ import annotations

from typing import Protocol, Tuple, runtime_checkable

__all__ = ["ObjectStore", "NDJSON_SUFFIX", "RAW_PREFIX"]

#: Everything the landing contract governs sits under this prefix.
RAW_PREFIX = "raw/"

#: The contract's file format. Anything else under ``raw/`` is not ours to read.
NDJSON_SUFFIX = ".ndjson"


@runtime_checkable
class ObjectStore(Protocol):
    """Read-only access to a landing root.

    Read-only deliberately: R4 loads from the raw drop and never writes to it. Raw is
    immutable and append-only, and the only writers are Lane C's connectors. A store handle
    that could delete a landed file is a handle that eventually will.
    """

    def list_objects(self, prefix: str = RAW_PREFIX) -> Tuple[str, ...]:
        """Every NDJSON object under ``prefix``, landing-root-relative, sorted.

        Sorted so a caller folding files in the returned order gets the contract's fold
        order for free: ``raw/{source}/dt=.../{source}-{ingested_at}.ndjson`` sorts by source,
        then partition date, then ``ingested_at`` -- oldest pull first, which is what
        de-duplication's tie-break depends on.
        """
        ...

    async def alist_objects(self, prefix: str = RAW_PREFIX) -> Tuple[str, ...]:
        """:meth:`list_objects`, off the event loop."""
        ...

    def get_bytes(self, path: str) -> bytes:
        """One object's contents, by its landing-root-relative path."""
        ...

    async def aget_bytes(self, path: str) -> bytes:
        """:meth:`get_bytes`, off the event loop."""
        ...
