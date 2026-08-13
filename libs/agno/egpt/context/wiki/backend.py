"""
WikiBackend — pluggable I/O layer behind WikiContextProvider.
=============================================================

The provider owns the agent-facing contract (two tools, two sub-agents).
The backend owns the on-disk wiki directory and any synchronisation
with an external store.

This module carries the contract and the one backend with no external
dependency:

- ``FileSystemBackend`` — the directory is the source of truth. ``sync``
  and ``commit_after_write`` are no-ops. Demoable with no auth, no
  network. The path that proves the design works.

The backends that talk to an external service (``GitBackend``,
``NotionDatabaseBackend``, ``NotionPageBackend``) live in
``egpt.integrations.context.wiki.backend`` (EGPT-1.7a): they import a
concrete SDK (``notion_client``) and shell out to ``git``, which makes
them integrations, not core. This module stays core -- ``WikiBackend``
is the ABC those integrations-layer classes subclass, and
``integrations -> core`` is the only edge that split allows.

All backends expose the same surface so the provider doesn't branch on
backend type; new backends (S3, GitHub App auth, etc.) can drop in
without touching ``WikiContextProvider``.
"""

from __future__ import annotations

import asyncio
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from egpt.context.provider import Status
from egpt.utils.log import log_debug, log_warning


class WikiBackendError(RuntimeError):
    """Raised when a backend cannot complete its setup or sync.

    Distinct from ``GitError`` because some failures (e.g. existing
    clone with a different remote) aren't subprocess errors — they're
    safety guards the provider's caller needs to react to (usually by
    setting ``force_clone=True`` after confirming nothing important is
    in the local clone).
    """


@dataclass
class CommitSummary:
    """What a write hook actually committed."""

    sha: str
    message: str
    files_changed: int


class WikiBackend(ABC):
    """Pluggable I/O layer backing a ``WikiContextProvider``.

    Subclasses own the on-disk path the sub-agents read and write
    against. ``setup`` runs once before the provider serves any
    request; ``sync`` runs before each read; ``commit_after_write``
    runs after each write.
    """

    def __init__(self, *, path: Path) -> None:
        self.path: Path = Path(path).expanduser().resolve()

    @abstractmethod
    async def setup(self) -> None:
        """Make ``self.path`` ready to serve. Idempotent.

        For a filesystem backend this is ``mkdir -p``. For a git backend
        it's clone-or-validate-existing-clone. Either way, after
        ``setup`` returns, the provider can list/read/write under
        ``self.path``.
        """

    @abstractmethod
    async def sync(self) -> None:
        """Bring local content up-to-date with the source of truth.

        FS: no-op. Git: ``pull --rebase`` so a stale local clone
        doesn't serve stale content to a read sub-agent.
        """

    @abstractmethod
    async def commit_after_write(self, *, model=None) -> CommitSummary | None:  # noqa: ANN001
        """Persist any changes the write sub-agent made. Return ``None`` if nothing changed.

        FS: no-op (returns ``None``). Git: ``add -A``, summarise the
        diff into a one-line message, commit, rebase onto remote,
        push. The summary is logged by the provider so the caller has
        an audit trail without parsing transcripts.

        ``model`` is forwarded by the provider so backends that need
        to summarise a diff can reuse the same model the sub-agents
        run on.
        """

    def status(self) -> Status:
        """Synchronous health check. Must not block on network."""
        return Status(ok=self.path.exists() and self.path.is_dir(), detail=str(self.path))

    async def astatus(self) -> Status:
        """Async health check. Default mirrors ``status``."""
        return await asyncio.to_thread(self.status)

    # -----------------------------------------------------------------
    # Helper for write sub-agents
    # -----------------------------------------------------------------

    async def summarize_diff(
        self,
        *,
        diff: str,
        model,  # noqa: ANN001
    ) -> str:
        """Summarise a staged diff into an imperative one-liner under 72 chars.

        Used by the ``GitBackend``/``NotionDatabaseBackend`` implementations of
        ``commit_after_write`` (and any future backend that needs a commit
        message). Lives on the ABC so subclasses can share the prompt without
        duplicating it. Falls back to a generic message if the model is
        unavailable or returns something unusable.

        Builds its sub-agent through ``construct_runnable`` rather than
        importing ``egpt.agent.Agent`` directly: this class is core, and
        subclasses that call this method live in ``integrations/`` -- neither
        layer may name ``egpt.agent`` statically.
        """
        from datetime import datetime, timezone

        fallback = f"Update wiki ({datetime.now(timezone.utc).isoformat(timespec='seconds')})"
        if model is None or not diff.strip():
            return fallback
        try:
            from egpt.run.utils import construct_runnable

            summarizer = construct_runnable(
                "agent",
                id="wiki-commit-summarizer",
                name="Wiki Commit Summarizer",
                model=model,
                instructions=_COMMIT_SUMMARY_INSTRUCTIONS,
                markdown=False,
            )
            output = await summarizer.arun(_truncate_diff(diff))
            text = (
                output.get_content_as_string()
                if hasattr(output, "get_content_as_string")
                else str(output.content) or ""
            ).strip()
        except Exception as exc:
            log_warning(f"wiki commit summarizer failed: {type(exc).__name__}: {exc}")
            return fallback
        # Strip surrounding quotes/backticks/leading list markers; clamp
        # length. The prompt asks for one line under 72 chars, but models
        # sometimes wrap.
        first_line = text.splitlines()[0] if text else ""
        first_line = first_line.strip().strip("`'\"")
        first_line = re.sub(r"^[-*+]\s+", "", first_line)
        if not first_line:
            return fallback
        if len(first_line) > 72:
            first_line = first_line[:71].rstrip() + "…"
        return first_line


class FileSystemBackend(WikiBackend):
    """Wiki backend that's just a local directory. No remote, no auth."""

    def __init__(self, path: Path | str) -> None:
        super().__init__(path=Path(path))

    async def setup(self) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        log_debug(f"FileSystemBackend ready at {self.path}")

    async def sync(self) -> None:
        return None

    async def commit_after_write(self, *, model=None) -> CommitSummary | None:  # noqa: ANN001
        return None

    def status(self) -> Status:
        if not self.path.exists():
            return Status(ok=False, detail=f"path does not exist: {self.path}")
        if not self.path.is_dir():
            return Status(ok=False, detail=f"path is not a directory: {self.path}")
        return Status(ok=True, detail=str(self.path))


# -----------------------------------------------------------------
# Module-level helpers
# -----------------------------------------------------------------


_COMMIT_SUMMARY_INSTRUCTIONS = """\
You write a single-line git commit message for a wiki update.

Constraints:
- Imperative mood (e.g. "Add deploy runbook", not "Added deploy runbook" or "Adds...").
- Under 72 characters.
- No trailing period.
- No leading verbs like "feat:" / "fix:" — this is a wiki, not a code change.
- Describe WHAT changed, not why or how. The diff is the source of truth.

Output ONLY the commit message line. No quotes, no markdown, no backticks.
"""


def _truncate_diff(diff: str, max_chars: int = 8000) -> str:
    if len(diff) <= max_chars:
        return diff
    head = diff[: max_chars // 2]
    tail = diff[-max_chars // 2 :]
    return f"{head}\n... [diff truncated] ...\n{tail}"
