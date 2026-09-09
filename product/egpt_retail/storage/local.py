"""A landing root on the local filesystem.

Not a test double. It is how the R4 cookbook runs against C1's committed fixtures with no
services at all, and it is what makes ``product/tests/test_r4_storage.py`` able to exercise
the path normalisation that the S3 backend also depends on -- without Docker, credentials or
a network.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Tuple

from egpt_retail.storage.base import NDJSON_SUFFIX, RAW_PREFIX

__all__ = ["LocalDirectoryStore"]


class LocalDirectoryStore:
    """Reads a ``raw/`` subtree under ``root``.

    ``root`` is the landing root, so ``root/raw/crm/dt=.../crm-....ndjson`` is object
    ``raw/crm/dt=.../crm-....ndjson``.
    """

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root).resolve()

    def __repr__(self) -> str:
        return f"LocalDirectoryStore(root={str(self._root)!r})"

    @property
    def root(self) -> Path:
        return self._root

    def _resolve(self, path: str) -> Path:
        """The absolute path of an object, refusing anything that escapes the root.

        A landed path comes from object storage, which is to say from outside this process.
        ``raw/../../etc/passwd`` parses as a path and would resolve outside the root, so the
        containment check is here rather than trusted to the caller.
        """
        candidate = (self._root / path).resolve()
        if candidate != self._root and self._root not in candidate.parents:
            raise ValueError(f"{path!r} resolves outside the landing root {self._root}")
        return candidate

    def list_objects(self, prefix: str = RAW_PREFIX) -> Tuple[str, ...]:
        base = self._resolve(prefix)
        if not base.is_dir():
            return ()
        return tuple(
            sorted(
                # `.as_posix()`, not `str()`: parse_landing_path's regex is anchored on
                # forward slashes, so an OS-separated path would not parse. No risk on this
                # Docker/Linux stack, but the S3 backend returns POSIX keys and the two
                # backends must be indistinguishable to their caller -- that is the point of
                # the seam.
                path.relative_to(self._root).as_posix()
                for path in base.rglob(f"*{NDJSON_SUFFIX}")
                if path.is_file()
            )
        )

    async def alist_objects(self, prefix: str = RAW_PREFIX) -> Tuple[str, ...]:
        return await asyncio.to_thread(self.list_objects, prefix)

    def get_bytes(self, path: str) -> bytes:
        return self._resolve(path).read_bytes()

    async def aget_bytes(self, path: str) -> bytes:
        return await asyncio.to_thread(self.get_bytes, path)
