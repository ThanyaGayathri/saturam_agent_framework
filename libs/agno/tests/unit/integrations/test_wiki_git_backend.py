"""Unit tests for GitBackend and git_ops (EGPT-1.7a: moved into integrations/context/wiki).

These don't hit GitHub. The git backend tests stub `git_ops.run` so we can assert on the args +
scrubber wiring without a real subprocess.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from egpt.context.wiki import WikiBackendError
from egpt.integrations.context.wiki import GitBackend
from egpt.integrations.context.wiki.backend import _count_changed_files, _normalise_remote, _remotes_equivalent
from egpt.integrations.context.wiki.git_ops import (
    GitError,
    GitResult,
    Scrubber,
    build_authenticated_url,
)

# ---------------------------------------------------------------------------
# Scrubber
# ---------------------------------------------------------------------------


def test_scrubber_replaces_registered_secret():
    s = Scrubber()
    s.add("ghp_supersecret")
    out = s.scrub("error: token=ghp_supersecret was rejected")
    assert "ghp_supersecret" not in out
    assert "***" in out


def test_scrubber_handles_empty_input():
    s = Scrubber()
    s.add("ghp_supersecret")
    assert s.scrub("") == ""
    assert s.scrub(None) is None  # type: ignore[arg-type]


def test_scrubber_ignores_empty_secret():
    s = Scrubber()
    s.add("")
    s.add(None)
    # Nothing to scrub but also no crash.
    assert s.scrub("hello world") == "hello world"


def test_scrubber_catches_unregistered_xaccess_url():
    s = Scrubber()
    # Even without registering this exact URL, the regex catch-all
    # blocks any `x-access-token:<token>@` form from leaking.
    leaked = "fatal: unable to access 'https://x-access-token:ghp_other@github.com/o/r.git/'"
    out = s.scrub(leaked)
    assert "ghp_other" not in out
    assert "x-access-token:***@" in out


def test_scrubber_handles_multiple_registered_secrets():
    s = Scrubber()
    s.add("token-a")
    s.add("token-b")
    out = s.scrub("a=token-a b=token-b c=plain")
    assert "token-a" not in out
    assert "token-b" not in out
    assert "plain" in out


# ---------------------------------------------------------------------------
# build_authenticated_url
# ---------------------------------------------------------------------------


def test_build_authenticated_url_embeds_token():
    url = build_authenticated_url("https://github.com/owner/repo.git", "ghp_x")
    assert url == "https://x-access-token:ghp_x@github.com/owner/repo.git"


def test_build_authenticated_url_strips_existing_credentials():
    # If a caller passes back the previously authenticated URL, we
    # shouldn't double-stack credentials.
    pre = "https://x-access-token:OLD@github.com/owner/repo.git"
    url = build_authenticated_url(pre, "NEW")
    assert url == "https://x-access-token:NEW@github.com/owner/repo.git"


def test_build_authenticated_url_rejects_ssh():
    with pytest.raises(ValueError, match="HTTPS"):
        build_authenticated_url("git@github.com:owner/repo.git", "ghp_x")


def test_build_authenticated_url_requires_token():
    with pytest.raises(ValueError, match="PAT is required"):
        build_authenticated_url("https://github.com/owner/repo.git", "")


def test_build_authenticated_url_rejects_non_https():
    with pytest.raises(ValueError, match="https"):
        build_authenticated_url("http://github.com/owner/repo.git", "ghp_x")


# ---------------------------------------------------------------------------
# Remote normalisation
# ---------------------------------------------------------------------------


def test_normalise_remote_strips_dot_git_and_credentials():
    a = "https://x-access-token:abc@github.com/owner/repo.git"
    b = "https://github.com/owner/repo"
    assert _normalise_remote(a) == _normalise_remote(b)
    assert _remotes_equivalent(a, b) is True


def test_normalise_remote_distinct_repos_not_equivalent():
    assert (
        _remotes_equivalent(
            "https://github.com/owner/repo-a.git",
            "https://github.com/owner/repo-b.git",
        )
        is False
    )


# ---------------------------------------------------------------------------
# _count_changed_files
# ---------------------------------------------------------------------------


def test_count_changed_files_uses_summary_line():
    stat = "docs/a.md | 2 +-\n 1 file changed, 1 insertion(+), 1 deletion(-)"
    assert _count_changed_files(stat) == 1
    multi = "docs/a.md | 2 +-\ndocs/b.md | 4 ++--\n 2 files changed, 3 insertions(+), 3 deletions(-)"
    assert _count_changed_files(multi) == 2


def test_count_changed_files_falls_back_to_body_count():
    # No summary line — just count rows that look like file entries.
    stat = "docs/a.md | 2 +-\ndocs/b.md | 1 +\n"
    assert _count_changed_files(stat) == 2


# ---------------------------------------------------------------------------
# GitBackend — construction safety + token scrubbing
# ---------------------------------------------------------------------------


def test_git_backend_requires_token():
    with pytest.raises(ValueError, match="github_token is required"):
        GitBackend(repo_url="https://github.com/o/r.git", github_token="")


def test_git_backend_requires_repo_url():
    with pytest.raises(ValueError, match="repo_url is required"):
        GitBackend(repo_url="", github_token="ghp_x")


def test_git_backend_authenticated_url_is_built_once_and_registered():
    b = GitBackend(repo_url="https://github.com/owner/repo.git", github_token="ghp_x")
    assert b._authenticated_url == "https://x-access-token:ghp_x@github.com/owner/repo.git"
    # Both the bare token and the full URL should be in the scrubber so a
    # leak in either form gets stripped.
    assert "ghp_x" in b._scrubber.secrets
    assert b._authenticated_url in b._scrubber.secrets


def test_git_backend_default_local_path_under_repos():
    b = GitBackend(repo_url="https://github.com/owner/My.Repo.git", github_token="ghp_x")
    # `/repos/<sanitized>` per the spec.
    assert b.path == Path("/repos/my-repo")


def test_git_backend_custom_local_path_is_resolved(tmp_path: Path):
    b = GitBackend(
        repo_url="https://github.com/owner/repo.git",
        github_token="ghp_x",
        local_path=tmp_path / "wiki",
    )
    assert b.path == (tmp_path / "wiki").resolve()


@pytest.mark.asyncio
async def test_git_backend_scrubs_token_from_subprocess_errors(monkeypatch, tmp_path: Path):
    """Force a GitError and assert the scrubber stripped the token from stderr."""
    import egpt.integrations.context.wiki.backend as backend_module

    b = GitBackend(
        repo_url="https://github.com/owner/repo.git",
        github_token="ghp_supersecret",
        local_path=tmp_path / "clone",
    )

    async def _fake_run(args, *, cwd, scrubber=None, **kwargs):  # noqa: ANN001
        # Mirrors what `git_ops.run` does on a non-zero exit: scrub
        # both args and stderr before constructing the GitError. Args
        # need scrubbing because `git clone <authenticated-url> ...`
        # carries the PAT.
        leaked = f"fatal: unable to access '{b._authenticated_url}/': the requested token ghp_supersecret was rejected"
        if scrubber is not None:
            safe_args = [scrubber.scrub(a) for a in args]
            stderr = scrubber.scrub(leaked)
        else:
            safe_args = list(args)
            stderr = leaked
        raise GitError(safe_args, 128, stderr)

    monkeypatch.setattr(backend_module, "git_run", _fake_run)

    with pytest.raises(GitError) as excinfo:
        await b.setup()

    msg = str(excinfo.value)
    assert "ghp_supersecret" not in msg
    assert "ghp_supersecret" not in excinfo.value.stderr
    assert "***" in msg


@pytest.mark.asyncio
async def test_git_backend_existing_clone_wrong_remote_raises_by_default(monkeypatch, tmp_path: Path):
    import egpt.integrations.context.wiki.backend as backend_module

    b = GitBackend(
        repo_url="https://github.com/owner/repo.git",
        github_token="ghp_x",
        local_path=tmp_path / "clone",
    )
    # Set up the local "clone" with a stub `.git` so setup() takes the
    # existing-clone branch.
    b.path.mkdir(parents=True)
    (b.path / ".git").mkdir()

    async def _fake_run(args, *, cwd, scrubber=None, check=True, **kwargs):  # noqa: ANN001
        if args[:3] == ["remote", "get-url", "origin"]:
            return GitResult(returncode=0, stdout="https://github.com/other/repo.git\n", stderr="")
        return GitResult(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(backend_module, "git_run", _fake_run)

    with pytest.raises(WikiBackendError, match="force_clone=True"):
        await b.setup()


@pytest.mark.asyncio
async def test_git_backend_existing_clone_force_clone_wipes_and_reclones(monkeypatch, tmp_path: Path):
    import egpt.integrations.context.wiki.backend as backend_module

    b = GitBackend(
        repo_url="https://github.com/owner/repo.git",
        github_token="ghp_x",
        local_path=tmp_path / "clone",
        force_clone=True,
    )
    # Existing clone with mismatched remote.
    b.path.mkdir(parents=True)
    (b.path / ".git").mkdir()
    (b.path / "stale-file.md").write_text("stale")

    invocations: list[list[str]] = []

    async def _fake_run(args, *, cwd, scrubber=None, check=True, **kwargs):  # noqa: ANN001
        invocations.append(list(args))
        if args[:3] == ["remote", "get-url", "origin"]:
            return GitResult(returncode=0, stdout="https://github.com/other/repo.git\n", stderr="")
        return GitResult(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(backend_module, "git_run", _fake_run)

    await b.setup()

    # The stale clone was wiped and a fresh `git clone` was issued.
    assert any(args[0] == "clone" for args in invocations), (
        f"expected git clone to be invoked after wipe; saw {invocations}"
    )
    # Identity gets configured after the clone.
    assert any(args[0] == "config" and args[1] == "user.name" for args in invocations)


@pytest.mark.asyncio
async def test_git_backend_commit_after_write_returns_none_when_nothing_staged(monkeypatch, tmp_path: Path):
    import egpt.integrations.context.wiki.backend as backend_module

    b = GitBackend(
        repo_url="https://github.com/owner/repo.git",
        github_token="ghp_x",
        local_path=tmp_path / "clone",
    )
    b.path.mkdir(parents=True)

    seen: list[list[str]] = []

    async def _fake_run(args, *, cwd, scrubber=None, check=True, **kwargs):  # noqa: ANN001
        seen.append(list(args))
        if args[:3] == ["diff", "--cached", "--quiet"]:
            # Exit 0 == no staged changes.
            return GitResult(returncode=0, stdout="", stderr="")
        return GitResult(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(backend_module, "git_run", _fake_run)

    summary = await b.commit_after_write()
    assert summary is None
    # The no-op path still attempts a push so any commits left over from a
    # previous failed push get flushed once auth recovers. `git push` with
    # nothing to send is a cheap no-op.
    assert ["push", "origin", "main"] in seen


@pytest.mark.asyncio
async def test_git_backend_idle_push_failure_is_swallowed(monkeypatch, tmp_path: Path):
    """When nothing is staged, a failing idle push must not propagate."""
    import egpt.integrations.context.wiki.backend as backend_module

    b = GitBackend(
        repo_url="https://github.com/owner/repo.git",
        github_token="ghp_x",
        local_path=tmp_path / "clone",
    )
    b.path.mkdir(parents=True)

    async def _fake_run(args, *, cwd, scrubber=None, check=True, **kwargs):  # noqa: ANN001
        if args[:3] == ["diff", "--cached", "--quiet"]:
            return GitResult(returncode=0, stdout="", stderr="")
        if args[:1] == ["push"]:
            from egpt.integrations.context.wiki.git_ops import GitError

            raise GitError(args=args, returncode=128, stderr="403")
        return GitResult(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(backend_module, "git_run", _fake_run)

    summary = await b.commit_after_write()
    assert summary is None  # idle path, push failure swallowed


@pytest.mark.asyncio
async def test_git_backend_commit_after_write_uses_fallback_message_without_model(monkeypatch, tmp_path: Path):
    import egpt.integrations.context.wiki.backend as backend_module

    b = GitBackend(
        repo_url="https://github.com/owner/repo.git",
        github_token="ghp_x",
        local_path=tmp_path / "clone",
    )
    b.path.mkdir(parents=True)

    seen: dict[str, list[str]] = {"commit_msg": []}

    async def _fake_run(args, *, cwd, scrubber=None, check=True, **kwargs):  # noqa: ANN001
        if args[:3] == ["diff", "--cached", "--quiet"]:
            # Exit 1 == staged changes pending.
            return GitResult(returncode=1, stdout="", stderr="")
        if args[:3] == ["diff", "--cached", "--stat"]:
            return GitResult(
                returncode=0, stdout="docs/a.md | 2 +-\n 1 file changed, 1 insertion(+), 1 deletion(-)", stderr=""
            )
        if args[:3] == ["diff", "--cached"]:
            return GitResult(returncode=0, stdout="diff --git a/docs/a.md b/docs/a.md\n+hi\n", stderr="")
        if args[:1] == ["commit"]:
            # Capture the message the backend used.
            assert args[1] == "-m"
            seen["commit_msg"].append(args[2])
            return GitResult(returncode=0, stdout="", stderr="")
        if args[:1] == ["rev-parse"]:
            return GitResult(returncode=0, stdout="deadbeefcafe1234\n", stderr="")
        return GitResult(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(backend_module, "git_run", _fake_run)

    summary = await b.commit_after_write(model=None)
    assert summary is not None
    assert summary.sha.startswith("deadbeef")
    assert summary.files_changed == 1
    # No model -> deterministic fallback prefix.
    assert seen["commit_msg"][0].startswith("Update wiki (")


# ---------------------------------------------------------------------------
# git_ops.run — happy path + GIT_TERMINAL_PROMPT enforcement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_git_run_sets_terminal_prompt_zero(monkeypatch, tmp_path: Path):
    """Ensure the wrapper always sets GIT_TERMINAL_PROMPT=0 so a busted
    PAT fails fast instead of hanging on stdin."""
    import egpt.integrations.context.wiki.git_ops as git_ops

    captured_env: dict[str, str] = {}

    class _FakeProc:
        def __init__(self) -> None:
            self.returncode = 0

        async def communicate(self):
            return (b"", b"")

        def kill(self):  # pragma: no cover - timeout path not exercised here
            pass

        async def wait(self):  # pragma: no cover
            pass

    async def _fake_exec(*args, env=None, **kwargs):  # noqa: ANN001
        captured_env.update(env or {})
        return _FakeProc()

    monkeypatch.setattr(git_ops.asyncio, "create_subprocess_exec", _fake_exec)

    await git_ops.run(["status"], cwd=tmp_path)
    assert captured_env.get("GIT_TERMINAL_PROMPT") == "0"
