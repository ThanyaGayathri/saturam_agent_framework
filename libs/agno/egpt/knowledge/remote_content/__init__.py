"""Deprecated compatibility shim (EGPT-1.8).

Import from ``egpt.integrations.knowledge.remote_content`` instead; this module will be
removed.

Names are re-exported explicitly (not proxied through ``__getattr__`` alone) so that
star-imports, ``dir()`` and static type checking keep working on this path.

Known limitations:

* ``mock.patch`` against this module patches the shim's binding, not the implementation in
  the target module. Patch the target path instead.
* Submodule identity is order-dependent. ``from egpt.knowledge.remote_content import <sub>``
  resolves through the ``__getattr__`` below to the *target* submodule and registers no
  ``egpt.knowledge.remote_content.<sub>`` entry in ``sys.modules``, whereas
  ``import egpt.knowledge.remote_content.<sub>`` loads the shim submodule. The same dotted
  path can therefore name two different module objects depending on which route ran first.
  Calls are unaffected -- both routes reach the same objects -- but it compounds the
  ``mock.patch`` limitation above.
"""

import warnings as _warnings

import egpt.integrations.knowledge.remote_content as _target
from egpt.integrations.knowledge.remote_content import (  # noqa: F401
    AzureBlobConfig,
    AzureBlobContent,
    BaseStorageConfig,
    GCSContent,
    GcsConfig,
    GitHubConfig,
    GitHubContent,
    ListFilesResult,
    RemoteContent,
    S3Config,
    S3Content,
    SharePointConfig,
    SharePointContent,
)

_warnings.warn(
    "egpt.knowledge.remote_content is deprecated; import from "
    "egpt.integrations.knowledge.remote_content instead. This shim will be removed in a "
    "future release.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    # Base classes
    "BaseStorageConfig",
    "ListFilesResult",
    # Config classes
    "S3Config",
    "GcsConfig",
    "SharePointConfig",
    "GitHubConfig",
    "AzureBlobConfig",
    # Content classes
    "RemoteContent",
    "S3Content",
    "GCSContent",
    "SharePointContent",
    "GitHubContent",
    "AzureBlobContent",
]


def __dir__():
    # __all__ rather than globals() alone: names the target provides lazily are resolved
    # by __getattr__ below and never land in this module's globals, so the default module
    # dir() would omit them despite them being importable and listed in __all__.
    return sorted(set(globals()) | set(__all__))


def __getattr__(name: str):
    # Fallback for names the target provides lazily or via an optional-dependency
    # fallback, which must not be imported eagerly.
    return getattr(_target, name)
