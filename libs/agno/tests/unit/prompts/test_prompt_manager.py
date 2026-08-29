"""Unit tests for PromptManager (EGPT-4.4).

Tests cover:
- Langfuse fetch success path (mocked)
- Langfuse unavailable → local fallback
- Langfuse not configured → fallback
- langfuse package not installed → fallback
- No fallback + Langfuse unavailable → RuntimeError
- TTL cache hit / expiry / disable (ttl=0)
- Cache key includes variables and fallback hash (no cross-contamination)
- LRU eviction at max entries
- Variable substitution: Langfuse prompt + fallback (string.Template)
- Fallback with literal braces (JSON) doesn't crash
- Safe partial substitution for missing variables
- Empty Langfuse prompt triggers fallback
- Span attribute recording
- Async variants
- Langfuse client singleton + negative caching (permanent + transient)
- Default manager singleton
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from egpt.exceptions import PromptResolutionError
from egpt.prompts.managed import ManagedPrompt
from egpt.prompts.manager import (
    PromptManager,
    _MAX_CACHE_ENTRIES,
    _reset_default_manager,
    _reset_langfuse_client,
    _set_prompt_on_span,
    clear_cache,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class FakeLangfusePrompt:
    """Mimics the object returned by ``langfuse.get_prompt()``."""

    version: int
    _compiled: str

    def compile(self, **kwargs: Any) -> str:
        text = self._compiled
        for k, v in kwargs.items():
            text = text.replace(f"{{{k}}}", v)
        return text


def _make_fake_langfuse_client(
    prompt_text: str = "Hello from Langfuse",
    version: int = 3,
) -> MagicMock:
    """Return a mock that behaves like ``langfuse.Langfuse``."""
    client = MagicMock()
    fake_prompt = FakeLangfusePrompt(version=version, _compiled=prompt_text)
    client.get_prompt.return_value = fake_prompt
    return client


@pytest.fixture(autouse=True)
def _clear_state():
    """Ensure each test starts with clean singletons and cache."""
    clear_cache()
    _reset_langfuse_client()
    _reset_default_manager()
    yield
    clear_cache()
    _reset_langfuse_client()
    _reset_default_manager()


# ---------------------------------------------------------------------------
# Langfuse fetch success
# ---------------------------------------------------------------------------


class TestLangfuseFetchSuccess:
    """When Langfuse is configured and reachable, the prompt is fetched."""

    def test_resolves_from_langfuse(self):
        ref = ManagedPrompt(name="greet", label="prod", fallback="fallback text")
        fake_client = _make_fake_langfuse_client("Managed greeting", version=5)

        with patch("egpt.prompts.manager._get_langfuse_client", return_value=fake_client):
            manager = PromptManager(cache_ttl=300)
            result = manager.resolve(ref)

        assert result.text == "Managed greeting"
        assert result.version == "5"
        assert result.source == "langfuse"
        assert result.name == "greet"

    def test_variables_are_substituted(self):
        ref = ManagedPrompt(
            name="greet",
            variables={"user": "Alice"},
            fallback="Hi $user",
        )
        fake_client = _make_fake_langfuse_client("Hello {user}", version=2)

        with patch("egpt.prompts.manager._get_langfuse_client", return_value=fake_client):
            manager = PromptManager(cache_ttl=300)
            result = manager.resolve(ref)

        assert result.text == "Hello Alice"

    def test_empty_langfuse_prompt_triggers_fallback(self):
        """Prompt authored in Langfuse with empty content should fall back."""
        ref = ManagedPrompt(name="empty", fallback="Safe fallback")
        fake_client = _make_fake_langfuse_client("", version=1)

        with patch("egpt.prompts.manager._get_langfuse_client", return_value=fake_client):
            manager = PromptManager(cache_ttl=300)
            result = manager.resolve(ref)

        assert result.source == "fallback"
        assert result.text == "Safe fallback"


# ---------------------------------------------------------------------------
# Fallback
# ---------------------------------------------------------------------------


class TestFallback:
    """When Langfuse is unavailable, the local fallback is used."""

    def test_fallback_when_not_configured(self):
        ref = ManagedPrompt(name="greet", fallback="Local greeting")

        with patch("egpt.prompts.manager._get_langfuse_client", return_value=None):
            manager = PromptManager(cache_ttl=300)
            result = manager.resolve(ref)

        assert result.text == "Local greeting"
        assert result.version == "fallback"
        assert result.source == "fallback"

    def test_fallback_when_sdk_not_installed(self):
        ref = ManagedPrompt(name="greet", fallback="Local greeting")

        with patch("egpt.prompts.manager._get_langfuse_client", return_value=None):
            manager = PromptManager(cache_ttl=300)
            result = manager.resolve(ref)

        assert result.source == "fallback"

    def test_fallback_with_variables(self):
        """Fallback uses string.Template $variable syntax."""
        ref = ManagedPrompt(
            name="greet",
            variables={"company": "Acme"},
            fallback="Support agent for $company.",
        )

        with patch("egpt.prompts.manager._get_langfuse_client", return_value=None):
            manager = PromptManager(cache_ttl=300)
            result = manager.resolve(ref)

        assert result.text == "Support agent for Acme."

    def test_fallback_missing_variable_left_as_is(self):
        """Missing variables are left as-is by safe_substitute."""
        ref = ManagedPrompt(
            name="greet",
            variables={"company": "Acme"},
            fallback="Agent for $company, role: $role.",
        )

        with patch("egpt.prompts.manager._get_langfuse_client", return_value=None):
            manager = PromptManager(cache_ttl=300)
            result = manager.resolve(ref)

        assert result.text == "Agent for Acme, role: $role."

    def test_fallback_with_literal_braces(self):
        """Fallback containing JSON-like braces must not crash."""
        ref = ManagedPrompt(
            name="json-prompt",
            variables={"model": "gpt-4"},
            fallback='Use model $model. Output format: {"key": "value"}',
        )

        with patch("egpt.prompts.manager._get_langfuse_client", return_value=None):
            manager = PromptManager(cache_ttl=300)
            result = manager.resolve(ref)

        assert result.text == 'Use model gpt-4. Output format: {"key": "value"}'

    def test_no_fallback_raises(self):
        ref = ManagedPrompt(name="missing-prompt")

        with patch("egpt.prompts.manager._get_langfuse_client", return_value=None):
            manager = PromptManager(cache_ttl=300)
            with pytest.raises(PromptResolutionError, match="no local fallback"):
                manager.resolve(ref)

    def test_fallback_on_fetch_exception(self):
        """Network error during Langfuse fetch should fall back gracefully."""
        ref = ManagedPrompt(name="greet", fallback="Safe fallback")

        client = MagicMock()
        client.get_prompt.side_effect = ConnectionError("timeout")

        with patch("egpt.prompts.manager._get_langfuse_client", return_value=client):
            manager = PromptManager(cache_ttl=300)
            result = manager.resolve(ref)

        assert result.source == "fallback"
        assert result.text == "Safe fallback"


# ---------------------------------------------------------------------------
# TTL cache
# ---------------------------------------------------------------------------


class TestCache:
    """Verify caching behaviour."""

    def test_cache_hit(self):
        ref = ManagedPrompt(name="cached", fallback="text")

        with patch("egpt.prompts.manager._get_langfuse_client", return_value=None):
            manager = PromptManager(cache_ttl=300)
            r1 = manager.resolve(ref)
            r2 = manager.resolve(ref)

        # Same object from cache
        assert r1 is r2

    def test_cache_expiry(self):
        """Langfuse-sourced results expire at the configured cache_ttl."""
        ref = ManagedPrompt(name="expiring", fallback="text")
        fake_client = _make_fake_langfuse_client("Langfuse text", version=1)

        with (
            patch("egpt.prompts.manager._get_langfuse_client", return_value=fake_client),
            patch("egpt.prompts.manager.time") as mock_time,
        ):
            # First call at t=0
            mock_time.monotonic.return_value = 0.0
            manager = PromptManager(cache_ttl=10)
            r1 = manager.resolve(ref)

            # Second call at t=11 (past TTL)
            mock_time.monotonic.return_value = 11.0
            r2 = manager.resolve(ref)

        # Different objects — cache expired
        assert r1 is not r2
        assert r1.text == r2.text

    def test_cache_disabled_when_ttl_zero(self):
        """ttl=0 disables caching — every call re-resolves."""
        ref = ManagedPrompt(name="no-cache", fallback="text")

        with patch("egpt.prompts.manager._get_langfuse_client", return_value=None):
            manager = PromptManager(cache_ttl=0)
            r1 = manager.resolve(ref)
            r2 = manager.resolve(ref)

        # Different objects — cache disabled
        assert r1 is not r2
        assert r1.text == r2.text

    def test_cache_key_includes_variables(self):
        """Same prompt name with different variables must not cross-contaminate."""
        ref_alice = ManagedPrompt(name="greet", variables={"user": "Alice"}, fallback="Hi $user")
        ref_bob = ManagedPrompt(name="greet", variables={"user": "Bob"}, fallback="Hi $user")

        with patch("egpt.prompts.manager._get_langfuse_client", return_value=None):
            manager = PromptManager(cache_ttl=300)
            r_alice = manager.resolve(ref_alice)
            r_bob = manager.resolve(ref_bob)

        assert r_alice.text == "Hi Alice"
        assert r_bob.text == "Hi Bob"
        assert r_alice is not r_bob

    def test_cache_key_includes_fallback(self):
        """Same name/label/variables but different fallback must not collide."""
        ref_a = ManagedPrompt(name="x", fallback="Fallback A")
        ref_b = ManagedPrompt(name="x", fallback="Fallback B")

        with patch("egpt.prompts.manager._get_langfuse_client", return_value=None):
            manager = PromptManager(cache_ttl=300)
            r_a = manager.resolve(ref_a)
            r_b = manager.resolve(ref_b)

        assert r_a.text == "Fallback A"
        assert r_b.text == "Fallback B"

    def test_lru_eviction(self):
        """Cache evicts oldest entries when exceeding _MAX_CACHE_ENTRIES."""
        with patch("egpt.prompts.manager._get_langfuse_client", return_value=None):
            manager = PromptManager(cache_ttl=300)
            # Fill cache past limit
            for i in range(_MAX_CACHE_ENTRIES + 10):
                ref = ManagedPrompt(name=f"prompt-{i}", fallback=f"text-{i}")
                manager.resolve(ref)

        # Verify cache size is bounded
        from egpt.prompts.manager import _cache

        assert len(_cache) <= _MAX_CACHE_ENTRIES

    def test_fallback_cached_with_short_ttl(self):
        """Fallback results are cached with _TRANSIENT_RETRY_SECONDS, not full cache_ttl."""
        from egpt.prompts.manager import _TRANSIENT_RETRY_SECONDS

        ref = ManagedPrompt(name="short-ttl", fallback="fallback text")

        with (
            patch("egpt.prompts.manager._get_langfuse_client", return_value=None),
            patch("egpt.prompts.manager.time") as mock_time,
        ):
            # First call at t=0 — caches fallback
            mock_time.monotonic.return_value = 0.0
            manager = PromptManager(cache_ttl=600)
            r1 = manager.resolve(ref)
            assert r1.source == "fallback"

            # At t=30 — still within transient retry window, should be cached
            mock_time.monotonic.return_value = 30.0
            r2 = manager.resolve(ref)
            assert r2 is r1

            # At t=_TRANSIENT_RETRY_SECONDS+1 — past short TTL, should re-resolve
            mock_time.monotonic.return_value = float(_TRANSIENT_RETRY_SECONDS + 1)
            r3 = manager.resolve(ref)
            assert r3 is not r1

    def test_per_entry_ttl_respected(self):
        """A short-TTL manager cannot evict a long-TTL manager's still-valid entry."""
        ref = ManagedPrompt(name="shared", fallback="text")
        fake_client = _make_fake_langfuse_client("Langfuse text", version=1)

        with (
            patch("egpt.prompts.manager._get_langfuse_client", return_value=fake_client),
            patch("egpt.prompts.manager.time") as mock_time,
        ):
            # Long-TTL manager writes at t=0
            mock_time.monotonic.return_value = 0.0
            long_mgr = PromptManager(cache_ttl=600)
            r1 = long_mgr.resolve(ref)
            assert r1.source == "langfuse"

            # Short-TTL manager reads at t=10 — entry was written with ttl=600,
            # so it should still be valid (stored TTL is 600, not 5)
            mock_time.monotonic.return_value = 10.0
            short_mgr = PromptManager(cache_ttl=5)
            r2 = short_mgr.resolve(ref)
            assert r2 is r1  # cache hit, not evicted


# ---------------------------------------------------------------------------
# Langfuse client singleton
# ---------------------------------------------------------------------------


class TestLangfuseClientSingleton:
    """Verify that the Langfuse client is created once and reused."""

    def test_client_created_once(self):
        """Multiple _get_langfuse_client calls return the same instance."""
        from egpt.prompts.manager import _get_langfuse_client

        fake_client = MagicMock()
        mock_langfuse_mod = MagicMock()
        mock_langfuse_mod.Langfuse.return_value = fake_client

        with (
            patch("egpt.settings.tracing.get_tracing_settings") as mock_settings_fn,
            patch.dict("sys.modules", {"langfuse": mock_langfuse_mod}),
        ):
            settings = MagicMock()
            settings.langfuse_is_configured = True
            settings.langfuse_public_key = "pk"
            settings.langfuse_secret_key = MagicMock()
            settings.langfuse_secret_key.get_secret_value.return_value = "sk"
            settings.langfuse_host = None
            mock_settings_fn.return_value = settings

            c1 = _get_langfuse_client()
            c2 = _get_langfuse_client()

        assert c1 is c2
        # Constructor called only once
        assert mock_langfuse_mod.Langfuse.call_count == 1

    def test_not_configured_cached_as_sentinel(self):
        """When Langfuse is not configured, the negative result is cached permanently."""
        from egpt.prompts.manager import _get_langfuse_client

        with patch("egpt.settings.tracing.get_tracing_settings") as mock_settings_fn:
            settings = MagicMock()
            settings.langfuse_is_configured = False
            mock_settings_fn.return_value = settings

            c1 = _get_langfuse_client()
            c2 = _get_langfuse_client()

        assert c1 is None
        assert c2 is None
        # Settings read only once — sentinel cached the negative result
        assert mock_settings_fn.call_count == 1

    def test_transient_failure_retried_after_ttl(self):
        """Transient client creation failure is retried after the retry window."""
        from egpt.prompts.manager import _get_langfuse_client

        with (
            patch("egpt.settings.tracing.get_tracing_settings") as mock_settings_fn,
            patch("egpt.prompts.manager.time") as mock_time,
        ):
            settings = MagicMock()
            settings.langfuse_is_configured = True
            settings.langfuse_public_key = "pk"
            settings.langfuse_secret_key = MagicMock()
            settings.langfuse_secret_key.get_secret_value.return_value = "sk"
            settings.langfuse_host = None
            mock_settings_fn.return_value = settings

            # First call at t=0 — simulate transient error
            mock_time.monotonic.return_value = 0.0
            with patch.dict("sys.modules", {"langfuse": MagicMock(Langfuse=MagicMock(side_effect=OSError("DNS")))}):
                c1 = _get_langfuse_client()
            assert c1 is None

            # Second call at t=30 — still within retry window
            mock_time.monotonic.return_value = 30.0
            c2 = _get_langfuse_client()
            assert c2 is None

            # Third call at t=70 — past retry window, should retry
            mock_time.monotonic.return_value = 70.0
            fake_client = MagicMock()
            with patch.dict("sys.modules", {"langfuse": MagicMock(Langfuse=MagicMock(return_value=fake_client))}):
                c3 = _get_langfuse_client()
            assert c3 is fake_client


# ---------------------------------------------------------------------------
# Default manager singleton
# ---------------------------------------------------------------------------


class TestDefaultManagerSingleton:
    """Verify the module-level manager singleton."""

    def test_singleton_reused(self):
        from egpt.prompts.manager import _get_default_manager

        with patch("egpt.settings.tracing.get_tracing_settings") as mock_settings_fn:
            settings = MagicMock()
            settings.prompt_cache_ttl = 300
            mock_settings_fn.return_value = settings

            m1 = _get_default_manager()
            m2 = _get_default_manager()

        assert m1 is m2
        # Settings read only once
        assert mock_settings_fn.call_count == 1

    def test_settings_error_falls_back_to_default_ttl(self):
        """Malformed env var for prompt_cache_ttl must not crash — uses default TTL."""
        from egpt.prompts.manager import _DEFAULT_TTL_SECONDS, _get_default_manager

        with patch("egpt.settings.tracing.get_tracing_settings", side_effect=Exception("bad env")):
            manager = _get_default_manager()

        assert manager.cache_ttl == _DEFAULT_TTL_SECONDS


# ---------------------------------------------------------------------------
# Span attributes
# ---------------------------------------------------------------------------


class TestSpanAttributes:
    """Verify that prompt metadata is recorded on the active OTel span."""

    def test_set_prompt_on_span(self):
        mock_span = MagicMock()
        mock_span.is_recording.return_value = True

        with patch("opentelemetry.trace.get_current_span", return_value=mock_span):
            _set_prompt_on_span("my-prompt", "7")

        mock_span.set_attribute.assert_any_call("egpt.prompt.name", "my-prompt")
        mock_span.set_attribute.assert_any_call("egpt.prompt.version", "7")

    def test_no_error_when_span_not_recording(self):
        mock_span = MagicMock()
        mock_span.is_recording.return_value = False

        with patch("opentelemetry.trace.get_current_span", return_value=mock_span):
            _set_prompt_on_span("my-prompt", "1")

        mock_span.set_attribute.assert_not_called()


# ---------------------------------------------------------------------------
# Async variants
# ---------------------------------------------------------------------------


class TestAsyncResolve:
    """Verify async resolution paths."""

    def test_async_fallback(self):
        ref = ManagedPrompt(name="async-test", fallback="async fallback")

        with patch("egpt.prompts.manager._get_langfuse_client", return_value=None):
            manager = PromptManager(cache_ttl=300)
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(manager.aresolve(ref))
            finally:
                loop.close()

        assert result.text == "async fallback"
        assert result.source == "fallback"

    def test_async_langfuse_fetch(self):
        ref = ManagedPrompt(name="async-lf", fallback="fb")
        fake_client = _make_fake_langfuse_client("Async Langfuse text", version=9)

        with patch("egpt.prompts.manager._get_langfuse_client", return_value=fake_client):
            manager = PromptManager(cache_ttl=300)
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(manager.aresolve(ref))
            finally:
                loop.close()

        assert result.text == "Async Langfuse text"
        assert result.source == "langfuse"


# ---------------------------------------------------------------------------
# Inline system_message unchanged
# ---------------------------------------------------------------------------


class TestInlineUnchanged:
    """Ensure ManagedPrompt does not interfere with plain string system_message."""

    def test_managed_prompt_is_not_str(self):
        ref = ManagedPrompt(name="x")
        assert not isinstance(ref, str)

    def test_managed_prompt_is_not_callable(self):
        ref = ManagedPrompt(name="x")
        assert not callable(ref)
