import warnings as _warnings

from egpt.context.wiki.backend import (
    CommitSummary,
    FileSystemBackend,
    WikiBackend,
    WikiBackendError,
)
from egpt.context.wiki.provider import (
    DEFAULT_WIKI_READ_INSTRUCTIONS,
    DEFAULT_WIKI_WRITE_INSTRUCTIONS,
    WIKI_WEB_INGEST_INSTRUCTIONS,
    WikiContextProvider,
)

__all__ = [
    "DEFAULT_WIKI_READ_INSTRUCTIONS",
    "DEFAULT_WIKI_WRITE_INSTRUCTIONS",
    "WIKI_WEB_INGEST_INSTRUCTIONS",
    "CommitSummary",
    "FileSystemBackend",
    "GitBackend",
    "NotionDatabaseBackend",
    "NotionPageBackend",
    "WikiBackend",
    "WikiBackendError",
    "WikiContextProvider",
]

# GitBackend/NotionDatabaseBackend/NotionPageBackend moved to
# egpt.integrations.context.wiki (EGPT-1.7a) -- they each depend on an external
# service (git, notion_client), which makes them integrations, not orchestration.
# Resolved lazily, not with a top-level import: this package also holds real
# orchestration code (WikiContextProvider) that must not statically import
# integrations, and eager resolution would warn on every unrelated import too.
_MOVED = {"GitBackend", "NotionDatabaseBackend", "NotionPageBackend"}

# CPython's IMPORT_FROM opcode probes a module with hasattr() before getattr(), so
# `from egpt.context.wiki import GitBackend` would otherwise hit __getattr__ -- and warn --
# twice per import. Track names already warned so each fires once per process, matching the
# eager shim packages (their warnings.warn() runs at module-exec time, which sys.modules
# caching already limits to once).
_warned: set = set()


def __dir__():
    return sorted(set(globals()) | set(__all__))


def __getattr__(name: str):
    if name in _MOVED:
        if name not in _warned:
            _warned.add(name)
            _warnings.warn(
                f"egpt.context.wiki.{name} is deprecated; import from egpt.integrations.context.wiki instead. "
                "This shim will be removed in a future release.",
                DeprecationWarning,
                stacklevel=2,
            )
        import egpt.integrations.context.wiki as _target

        return getattr(_target, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
