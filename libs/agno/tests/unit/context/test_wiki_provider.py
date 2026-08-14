"""Unit tests for WikiContextProvider and FileSystemBackend.

GitBackend/git_ops tests live in tests/unit/integrations/test_wiki_git_backend.py (EGPT-1.7a
moved GitBackend into the integrations layer, alongside git_ops.py). The provider round-trip
here stubs the sub-agents so it doesn't hit a model.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from egpt.context.mode import ContextMode
from egpt.context.wiki import FileSystemBackend, WikiContextProvider

# ---------------------------------------------------------------------------
# FileSystemBackend
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_filesystem_backend_setup_creates_directory(tmp_path: Path):
    target = tmp_path / "wiki"
    fs = FileSystemBackend(path=target)
    await fs.setup()
    assert target.is_dir()


@pytest.mark.asyncio
async def test_filesystem_backend_sync_and_commit_are_noops(tmp_path: Path):
    fs = FileSystemBackend(path=tmp_path)
    await fs.sync()  # must not raise
    assert await fs.commit_after_write() is None


def test_filesystem_backend_status_ok_for_directory(tmp_path: Path):
    fs = FileSystemBackend(path=tmp_path)
    status = fs.status()
    assert status.ok is True
    assert str(tmp_path) in status.detail


def test_filesystem_backend_status_reports_missing(tmp_path: Path):
    fs = FileSystemBackend(path=tmp_path / "missing")
    status = fs.status()
    assert status.ok is False
    assert "does not exist" in status.detail


def test_filesystem_backend_status_reports_non_directory(tmp_path: Path):
    f = tmp_path / "not-a-dir"
    f.write_text("hello")
    fs = FileSystemBackend(path=f)
    status = fs.status()
    assert status.ok is False
    assert "not a directory" in status.detail


# ---------------------------------------------------------------------------
# WikiContextProvider — surface and round-trip
# ---------------------------------------------------------------------------


def test_provider_default_surface_is_query_plus_update(tmp_path: Path):
    p = WikiContextProvider(backend=FileSystemBackend(path=tmp_path))
    tools = p.get_tools()
    assert [t.name for t in tools] == ["query_wiki", "update_wiki"]


def test_provider_custom_id_renames_tools(tmp_path: Path):
    p = WikiContextProvider(backend=FileSystemBackend(path=tmp_path), id="docs")
    tools = p.get_tools()
    assert [t.name for t in tools] == ["query_docs", "update_docs"]


def test_provider_write_false_drops_update_tool(tmp_path: Path):
    """Voice-folder pattern: read+write provider configured read-only."""
    p = WikiContextProvider(backend=FileSystemBackend(path=tmp_path), id="voice", write=False)
    tools = p.get_tools()
    assert [t.name for t in tools] == ["query_voice"]


def test_provider_read_false_drops_query_tool(tmp_path: Path):
    """Asymmetric write-only sink — rare but supported."""
    p = WikiContextProvider(backend=FileSystemBackend(path=tmp_path), id="sink", read=False)
    tools = p.get_tools()
    assert [t.name for t in tools] == ["update_sink"]


def test_provider_both_flags_false_raises(tmp_path: Path):
    with pytest.raises(ValueError, match="at least one of `read` or `write`"):
        WikiContextProvider(backend=FileSystemBackend(path=tmp_path), read=False, write=False)


def test_provider_instructions_omit_update_when_write_false(tmp_path: Path):
    p = WikiContextProvider(backend=FileSystemBackend(path=tmp_path), id="voice", write=False)
    text = p.instructions()
    assert "query_voice" in text
    assert "update_voice" not in text


def test_provider_tools_mode_is_read_only(tmp_path: Path):
    p = WikiContextProvider(backend=FileSystemBackend(path=tmp_path), mode=ContextMode.tools)
    workspace = p.get_tools()[0]
    # Only the read aliases should be registered — no write/edit/delete.
    assert sorted(workspace.functions.keys()) == ["list_files", "read_file", "search_content"]


def test_provider_tools_mode_instructions_call_out_read_only(tmp_path: Path):
    p = WikiContextProvider(backend=FileSystemBackend(path=tmp_path), mode=ContextMode.tools)
    text = p.instructions()
    assert "read-only" in text
    assert "mode=default" in text


def test_provider_status_forwards_from_backend(tmp_path: Path):
    p = WikiContextProvider(backend=FileSystemBackend(path=tmp_path / "nope"))
    status = p.status()
    assert status.ok is False
    assert "does not exist" in status.detail


@pytest.mark.asyncio
async def test_provider_round_trip_with_filesystem_backend(tmp_path: Path):
    """update -> commit hook -> query, with stubbed sub-agents.

    The point is the wiring: aupdate must hit the write agent, commit
    hook fires on the backend (no-op for FS), aquery must hit the
    read agent. We stub both agents so no model is hit.
    """
    p = WikiContextProvider(
        id="wiki",
        backend=FileSystemBackend(path=tmp_path),
    )

    write_calls: list[str] = []
    read_calls: list[str] = []
    commit_calls: list[bool] = []

    class _StubAgent:
        def __init__(self, sink: list[str], reply: str) -> None:
            self._sink = sink
            self._reply = reply

        async def arun(self, message: str, **kwargs):  # noqa: ANN001
            self._sink.append(message)

            class _Out:
                content = self._reply  # noqa: B023

                def get_content_as_string(self):
                    return self.content

            return _Out()

    p._write_agent = _StubAgent(write_calls, "wrote runbooks/deploys.md")
    p._read_agent = _StubAgent(read_calls, "deploys.md says: deploy on green")

    # Spy on the backend hook so we know it was awaited inside the lock.
    original_commit = p.backend.commit_after_write

    async def _spy_commit(*, model=None):  # noqa: ANN001
        commit_calls.append(True)
        return await original_commit(model=model)

    p.backend.commit_after_write = _spy_commit  # type: ignore[assignment]

    out_w = await p.aupdate("add a deploys runbook")
    out_r = await p.aquery("what does the deploys runbook say?")

    assert write_calls == ["add a deploys runbook"]
    assert read_calls == ["what does the deploys runbook say?"]
    assert commit_calls == [True]
    # FS backend returns None from commit hook -> no commit note appended.
    assert "wrote runbooks/deploys.md" in (out_w.text or "")
    assert "deploys.md says: deploy on green" in (out_r.text or "")


@pytest.mark.asyncio
async def test_provider_query_tool_does_not_acquire_lock(tmp_path: Path):
    """A scheduled sync() in flight must not block reads."""
    import asyncio

    p = WikiContextProvider(backend=FileSystemBackend(path=tmp_path))

    class _StubAgent:
        async def arun(self, message: str, **kwargs):  # noqa: ANN001
            class _Out:
                content = "ok"

                def get_content_as_string(self):
                    return self.content

            return _Out()

    p._read_agent = _StubAgent()

    async with p._git_lock:
        # If aquery tried to take the lock, this would deadlock.
        out = await asyncio.wait_for(p.aquery("anything"), timeout=2.0)
    assert out.text == "ok"


@pytest.mark.asyncio
async def test_provider_sync_acquires_lock_and_delegates(tmp_path: Path):
    p = WikiContextProvider(backend=FileSystemBackend(path=tmp_path))

    sync_calls = {"n": 0}

    async def _recording_sync():
        sync_calls["n"] += 1

    p.backend.sync = _recording_sync  # type: ignore[assignment]

    await p.sync()
    assert sync_calls["n"] == 1
    # Lock must be released when sync returns.
    assert not p._git_lock.locked()


# ---------------------------------------------------------------------------
# Sanity: provider tool wrappers serialise Answer correctly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_query_tool_serialises_answer(tmp_path: Path):
    from egpt.run.agent import RunOutput

    p = WikiContextProvider(backend=FileSystemBackend(path=tmp_path))

    class _StubAgent:
        async def arun(self, message: str, **kwargs):  # noqa: ANN001
            # When stream=True, arun is an async generator function
            yield RunOutput(content="hello")

    p._read_agent = _StubAgent()
    tool = next(t for t in p.get_tools() if t.name == "query_wiki")

    # Collect output from generator (tool wrapper returns coroutine)
    gen = await tool.entrypoint(question="anything")
    out = ""
    async for chunk in gen:
        if isinstance(chunk, str):
            out = chunk
    payload = json.loads(out)
    assert payload == {"text": "hello"}


@pytest.mark.asyncio
async def test_web_backend_tools_attached_to_write_sub_agent(tmp_path: Path):
    """When `web` is wired, the write sub-agent gets web tools too."""
    from egpt.context.backend import ContextBackend
    from egpt.context.provider import Status as _Status
    from egpt.tools import tool

    @tool(name="web_search")
    async def _web_search(query: str) -> str:
        return "{}"

    @tool(name="web_fetch")
    async def _web_fetch(url: str) -> str:
        return "{}"

    class _StubWeb(ContextBackend):
        def status(self) -> _Status:
            return _Status(ok=True, detail="stub")

        async def astatus(self) -> _Status:
            return self.status()

        def get_tools(self) -> list:
            return [_web_search, _web_fetch]

    p = WikiContextProvider(backend=FileSystemBackend(path=tmp_path), web=_StubWeb())
    write_agent = p._ensure_write_agent()
    tool_names = [getattr(t, "name", None) for t in write_agent.tools or []]
    assert "web_search" in tool_names
    assert "web_fetch" in tool_names
    # Workspace toolkit registers methods, not tool names — but it
    # must still be in the list (name attribute on Toolkit instances).
    assert any(getattr(t, "name", "") == "workspace" for t in write_agent.tools or [])


@pytest.mark.asyncio
async def test_web_backend_not_attached_to_read_sub_agent(tmp_path: Path):
    """The read sub-agent stays scoped to the wiki even with web wired."""
    from egpt.context.backend import ContextBackend
    from egpt.context.provider import Status as _Status
    from egpt.tools import tool

    @tool(name="web_search")
    async def _web_search(query: str) -> str:
        return "{}"

    class _StubWeb(ContextBackend):
        def status(self) -> _Status:
            return _Status(ok=True, detail="stub")

        async def astatus(self) -> _Status:
            return self.status()

        def get_tools(self) -> list:
            return [_web_search]

    p = WikiContextProvider(backend=FileSystemBackend(path=tmp_path), web=_StubWeb())
    read_agent = p._ensure_read_agent()
    tool_names = [getattr(t, "name", None) for t in read_agent.tools or []]
    assert "web_search" not in tool_names


@pytest.mark.asyncio
async def test_web_backend_asetup_aclose_forwarded(tmp_path: Path):
    """Lifecycle hooks must reach the web backend (matters for MCP sessions)."""
    from egpt.context.backend import ContextBackend
    from egpt.context.provider import Status as _Status

    calls = {"asetup": 0, "aclose": 0}

    class _LifecycleWeb(ContextBackend):
        def status(self) -> _Status:
            return _Status(ok=True, detail="lifecycle")

        async def astatus(self) -> _Status:
            return self.status()

        def get_tools(self) -> list:
            return []

        async def asetup(self) -> None:
            calls["asetup"] += 1

        async def aclose(self) -> None:
            calls["aclose"] += 1

    p = WikiContextProvider(backend=FileSystemBackend(path=tmp_path), web=_LifecycleWeb())
    await p.asetup()
    assert calls["asetup"] == 1
    # asetup is idempotent — second call is a no-op on the provider,
    # which means the web backend isn't double-set-up either.
    await p.asetup()
    assert calls["asetup"] == 1
    await p.aclose()
    assert calls["aclose"] == 1


def test_web_none_keeps_default_write_instructions(tmp_path: Path):
    """No web backend = no ingestion stanza in the write sub-agent's prompt."""
    from egpt.context.wiki.provider import WIKI_WEB_INGEST_INSTRUCTIONS

    p = WikiContextProvider(backend=FileSystemBackend(path=tmp_path))
    composed = p._compose_write_instructions()
    assert WIKI_WEB_INGEST_INSTRUCTIONS not in composed


def test_web_set_appends_ingestion_stanza(tmp_path: Path):
    from egpt.context.backend import ContextBackend
    from egpt.context.provider import Status as _Status
    from egpt.context.wiki.provider import WIKI_WEB_INGEST_INSTRUCTIONS

    class _StubWeb(ContextBackend):
        def status(self) -> _Status:
            return _Status(ok=True, detail="stub")

        async def astatus(self) -> _Status:
            return self.status()

        def get_tools(self) -> list:
            return []

    p = WikiContextProvider(backend=FileSystemBackend(path=tmp_path), web=_StubWeb())
    composed = p._compose_write_instructions()
    assert WIKI_WEB_INGEST_INSTRUCTIONS in composed


def test_instructions_default_mode_advertises_web_when_wired(tmp_path: Path):
    from egpt.context.backend import ContextBackend
    from egpt.context.provider import Status as _Status

    class _StubWeb(ContextBackend):
        def status(self) -> _Status:
            return _Status(ok=True, detail="stub")

        async def astatus(self) -> _Status:
            return self.status()

        def get_tools(self) -> list:
            return []

    p_no_web = WikiContextProvider(backend=FileSystemBackend(path=tmp_path))
    assert "fetch the web" not in p_no_web.instructions()

    p_web = WikiContextProvider(backend=FileSystemBackend(path=tmp_path), web=_StubWeb())
    assert "fetch the web" in p_web.instructions()


@pytest.mark.asyncio
async def test_provider_threads_run_context_into_sub_agents(tmp_path: Path):
    """user_id / session_id / metadata / dependencies must propagate."""
    from egpt.run import RunContext

    p = WikiContextProvider(backend=FileSystemBackend(path=tmp_path))
    p._read_agent = type("M", (), {"arun": AsyncMock()})()
    out = type("Out", (), {"content": "ok", "get_content_as_string": lambda self: "ok"})()
    p._read_agent.arun.return_value = out  # type: ignore[attr-defined]

    rc = RunContext(
        run_id="r-1",
        user_id="u-7",
        session_id="s-7",
        metadata={"action_token": "xoxa-x"},
        dependencies={"tenant": "acme"},
    )
    await p.aquery("hello", run_context=rc)

    p._read_agent.arun.assert_awaited_once()  # type: ignore[attr-defined]
    _, kwargs = p._read_agent.arun.call_args  # type: ignore[attr-defined]
    assert kwargs == {
        "user_id": "u-7",
        "session_id": "s-7",
        "metadata": {"action_token": "xoxa-x"},
        "dependencies": {"tenant": "acme"},
    }
