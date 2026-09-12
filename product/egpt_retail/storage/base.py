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

import string
from typing import Protocol, Tuple, runtime_checkable

__all__ = ["ObjectStore", "NDJSON_SUFFIX", "RAW_PREFIX", "validate_object_path"]

#: Everything the landing contract governs sits under this prefix.
RAW_PREFIX = "raw/"

#: The contract's file format. Anything else under ``raw/`` is not ours to read.
NDJSON_SUFFIX = ".ndjson"

#: Every character a landing-root-relative path may contain. An allowlist, not a denylist:
#: ``raw/{source}/dt={YYYY-MM-DD}/{source}-{ingested_at}.ndjson`` -- the only shape
#: :func:`~egpt_retail.contracts.landing.parse_landing_path` accepts -- needs exactly ASCII
#: letters, digits, ``-``, ``_``, ``.``, ``=`` and ``/``. Enumerating what is allowed is what
#: makes a backslash, a drive letter, a NUL byte, whitespace or a control character a refusal
#: without anyone having had to think of that shape in advance; a ``..``-only denylist refuses
#: one traversal spelling and admits every other.
_ALLOWED_PATH_CHARS = frozenset(string.ascii_letters + string.digits + "-_.=/")


def validate_object_path(path: str, landing_root: str) -> str:
    """Return ``path`` unchanged, or raise ``ValueError`` if it is not a relative object path.

    A landed path comes from object storage, which is to say from outside this process, so
    every backend validates before it builds a key or a filesystem path. Shared rather than
    reimplemented per backend because the seam's stated contract is that the backends are
    *indistinguishable to their caller* (see this module's docstring): if ``/etc/passwd`` is a
    ``ValueError`` on the local backend and a silently different blob key on Azure Blob, that
    contract is already broken -- and a flat blob namespace makes that unexploitable, not
    correct.

    Refused: any character outside :data:`_ALLOWED_PATH_CHARS` (a backslash, ``C:``,
    whitespace, a control character), an absolute path, a ``.`` or ``..`` segment, and an
    empty segment (``//``). A single trailing ``/`` is allowed, because a prefix such as
    :data:`RAW_PREFIX` legitimately carries one, and so is the empty string, which names the
    landing root itself.

    ``landing_root`` only names the root in the message -- each backend spells its own
    differently (a directory, ``{container}/{prefix}``).
    """
    unexpected = sorted({char for char in path if char not in _ALLOWED_PATH_CHARS})
    if unexpected:
        raise ValueError(
            f"{path!r} is not a valid landing-root-relative path for {landing_root}: "
            f"the character(s) {unexpected!r} are not allowed. A landed path may contain "
            "only ASCII letters, digits, '-', '_', '.', '=' and '/'."
        )
    if path.startswith("/"):
        raise ValueError(
            f"{path!r} is absolute, not relative to the landing root {landing_root}. Every "
            "path crossing this seam is landing-root-relative and starts with 'raw/'."
        )
    segments = path.split("/")
    last = len(segments) - 1
    for index, segment in enumerate(segments):
        # The ".." wording is deliberately the one LocalDirectoryStore._resolve has always
        # raised -- the shape the AZ-A cookbook's evidence and the seam's tests both read.
        if segment == "..":
            raise ValueError(
                f"{path!r} resolves outside the landing root {landing_root}"
            )
        # An empty last segment is a trailing slash on a prefix; an empty one anywhere else is
        # '//', which Azure Blob and S3 treat as a real, different key while a filesystem
        # collapses it -- the backends would stop agreeing on what was asked for.
        if segment == "." or (segment == "" and index != last):
            raise ValueError(
                f"{path!r} is not a normalised landing-root-relative path for "
                f"{landing_root}: the segment {segment!r} has no meaning in a flat object "
                "namespace and would change meaning on a filesystem."
            )
    return path


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
