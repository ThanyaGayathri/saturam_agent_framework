"""PromptManager — Langfuse fetch + local fallback + TTL cache (EGPT-4.4).

The manager resolves a :class:`ManagedPrompt` to rendered text and records
the prompt name + version on the active OpenTelemetry span.

Thread-safety: the module-level ``_cache`` uses an ``OrderedDict`` guarded
by a :class:`threading.Lock` with LRU eviction at ``_MAX_CACHE_ENTRIES``.
The TTL is intentionally short (default 300 s) so stale entries are evicted
quickly without needing background timers.

Note on cache stampede: concurrent callers missing the cache for the same
key may each fire a separate Langfuse fetch.  This is a known trade-off for
simplicity — the TTL cache amortises the cost adequately for prompt
resolution, and adding per-key in-flight deduplication would significantly
complicate the locking model.  Documented here for future reference.

Note on TTL+LRU cache: this module uses a hand-rolled OrderedDict cache.
``ComponentConfigCache`` (``registry/hydrate.py``) and
``_VerificationCache`` (``os/service_accounts.py``) implement the same
pattern.  A follow-up should factor a generic ``TTLLRUCache[K, V]`` utility
under ``egpt/utils/`` and have all three call sites use it.

Tenant scoping: the prompt cache and Langfuse client singleton are
process-global, not tenant-scoped.  Langfuse prompts are currently shared
infrastructure (single project per process), matching the rest of tracing
config.  If per-tenant Langfuse configuration is added in the future, both
the cache and client must be re-keyed per ``tenant_id`` to avoid
cross-tenant prompt/version leakage.  Until then, tenant-specific prompt
content can be achieved by passing ``tenant_id`` as a variable in
``ManagedPrompt.variables``, which is already part of the cache key.
"""

from __future__ import annotations

import atexit
import hashlib
import string
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from egpt.exceptions import PromptResolutionError
from egpt.prompts.managed import ManagedPrompt
from egpt.utils.log import log_debug, log_warning

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_TTL_SECONDS: int = 300
_MAX_CACHE_ENTRIES: int = 2048
_LANGFUSE_CLIENT_TIMEOUT: int = 10  # seconds
_TRANSIENT_RETRY_SECONDS: int = 60  # short TTL for fallback / transient failures

# ---------------------------------------------------------------------------
# Resolved result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedPrompt:
    """The text and metadata returned after resolving a :class:`ManagedPrompt`."""

    text: str
    name: str
    version: str
    source: str  # "langfuse" | "fallback"


# ---------------------------------------------------------------------------
# In-process TTL + LRU cache
# ---------------------------------------------------------------------------

# Key: (name, label, sorted-variables-tuple, fallback_hash)
_CacheKey = Tuple[str, str, Tuple[Tuple[str, str], ...], str]
# Value: (resolved, cached_at, ttl) — TTL stored per entry so readers
# evaluate expiry against the TTL that *wrote* the entry, not their own.
_cache: "OrderedDict[_CacheKey, Tuple[ResolvedPrompt, float, int]]" = OrderedDict()
_cache_lock = threading.Lock()


def _cache_key(ref: ManagedPrompt) -> _CacheKey:
    # Include a hash of fallback so two ManagedPrompts with different
    # fallback text but the same name/label/variables don't collide.
    fb_hash = hashlib.md5((ref.fallback or "").encode()).hexdigest()[:8]
    return (ref.name, ref.label or "", tuple(sorted(ref.variables.items())), fb_hash)


def _get_cached(ref: ManagedPrompt) -> Optional[ResolvedPrompt]:
    """Return a cached result if present and not expired.  Returns None on miss."""
    key = _cache_key(ref)
    with _cache_lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        resolved, cached_at, ttl = entry
        if ttl <= 0 or time.monotonic() - cached_at > ttl:
            _cache.pop(key, None)
            return None
        # Move to end for LRU ordering
        _cache.move_to_end(key)
        return resolved


def _put_cache(ref: ManagedPrompt, resolved: ResolvedPrompt, ttl: int) -> None:
    """Store a resolved prompt in the cache with LRU eviction.

    The *ttl* is stored alongside the entry so expiry is evaluated against
    the TTL that wrote it, not whatever TTL a reader happens to have.
    """
    if ttl <= 0:
        return
    key = _cache_key(ref)
    with _cache_lock:
        _cache[key] = (resolved, time.monotonic(), ttl)
        _cache.move_to_end(key)
        while len(_cache) > _MAX_CACHE_ENTRIES:
            _cache.popitem(last=False)


def clear_cache() -> None:
    """Drop all cached prompts. Useful in tests."""
    with _cache_lock:
        _cache.clear()


# ---------------------------------------------------------------------------
# Span enrichment (EGPT-4.2 pattern)
# ---------------------------------------------------------------------------


def _set_prompt_on_span(name: str, version: str) -> None:
    """Record prompt metadata on the active OTel span."""
    try:
        from opentelemetry import trace as _trace_api

        span = _trace_api.get_current_span()
        if span.is_recording():
            span.set_attribute("egpt.prompt.name", name)
            span.set_attribute("egpt.prompt.version", version)
    except Exception:
        log_debug("Failed to set prompt attributes on span (tracing may not be configured)")


# ---------------------------------------------------------------------------
# Langfuse client singleton
# ---------------------------------------------------------------------------

_langfuse_client: Optional[Any] = None
_langfuse_client_lock = threading.Lock()
_NOT_CONFIGURED = object()  # sentinel: keys absent / SDK missing (permanent)
_TRANSIENT_FAILURE = object()  # sentinel: transient error (TTL-based retry)
_transient_failure_expiry: float = 0.0  # monotonic expiry for transient sentinel


def _get_langfuse_client() -> Optional[Any]:
    """Return a cached Langfuse client, creating one on first call.

    The Langfuse SDK constructor initialises HTTP sessions and background
    flush threads.  Caching the client avoids thread/resource leaks on
    repeated cache misses.  An ``atexit`` handler calls ``shutdown()`` for
    clean teardown.

    Permanent failures (not configured, SDK not installed) cache the
    ``_NOT_CONFIGURED`` sentinel.  Transient failures (e.g. DNS blip during
    client construction) cache ``_TRANSIENT_FAILURE`` with a short TTL so
    the client is retried periodically.
    """
    global _langfuse_client, _transient_failure_expiry

    if _langfuse_client is not None:
        if _langfuse_client is _NOT_CONFIGURED:
            return None
        if _langfuse_client is _TRANSIENT_FAILURE:
            if time.monotonic() < _transient_failure_expiry:
                return None
            # TTL expired — fall through to retry
        else:
            return _langfuse_client

    with _langfuse_client_lock:
        # Re-check after acquiring lock
        if _langfuse_client is not None:
            if _langfuse_client is _NOT_CONFIGURED:
                return None
            if _langfuse_client is _TRANSIENT_FAILURE:
                if time.monotonic() < _transient_failure_expiry:
                    return None
                # Fall through to retry
            else:
                return _langfuse_client

        try:
            from egpt.settings.tracing import get_tracing_settings

            settings = get_tracing_settings()
            if not settings.langfuse_is_configured:
                log_debug("Langfuse not configured — skipping managed-prompt fetch")
                _langfuse_client = _NOT_CONFIGURED
                return None

            from langfuse import Langfuse  # type: ignore[import-not-found]

            client = Langfuse(
                public_key=settings.langfuse_public_key,
                secret_key=(settings.langfuse_secret_key.get_secret_value() if settings.langfuse_secret_key else None),
                host=settings.langfuse_host or "https://cloud.langfuse.com",
                timeout=_LANGFUSE_CLIENT_TIMEOUT,
            )

            def _shutdown_langfuse() -> None:
                try:
                    client.shutdown()
                except Exception:
                    pass

            atexit.register(_shutdown_langfuse)
            _langfuse_client = client
            return client
        except ImportError:
            log_warning(
                "langfuse package not installed — cannot fetch managed prompts. "
                "Install with: pip install egpt[langfuse]"
            )
            _langfuse_client = _NOT_CONFIGURED
            return None
        except Exception as exc:
            log_warning(f"Failed to create Langfuse client: {exc}")
            _langfuse_client = _TRANSIENT_FAILURE
            _transient_failure_expiry = time.monotonic() + _TRANSIENT_RETRY_SECONDS
            return None


def _reset_langfuse_client() -> None:
    """Reset the cached Langfuse client. Useful in tests."""
    global _langfuse_client, _transient_failure_expiry
    with _langfuse_client_lock:
        _langfuse_client = None
        _transient_failure_expiry = 0.0


# ---------------------------------------------------------------------------
# Langfuse fetch helpers
# ---------------------------------------------------------------------------


def _fetch_from_langfuse(ref: ManagedPrompt) -> Optional[ResolvedPrompt]:
    """Attempt to fetch a prompt from the Langfuse SDK.

    Returns *None* when Langfuse is not configured, the SDK is not installed,
    the fetch fails, or the compiled prompt is empty.
    """
    try:
        client = _get_langfuse_client()
        if client is None:
            return None

        kwargs: Dict[str, str] = {"name": ref.name}
        if ref.label:
            kwargs["label"] = ref.label

        prompt_obj = client.get_prompt(**kwargs)
        compiled = prompt_obj.compile(**ref.variables) if ref.variables else prompt_obj.compile()

        # Treat empty compiled text as a fetch failure so fallback is used
        if not compiled:
            log_warning(f"Prompt '{ref.name}' compiled to empty text from Langfuse — falling back")
            return None

        version_str = str(getattr(prompt_obj, "version", "unknown"))

        return ResolvedPrompt(
            text=compiled,
            name=ref.name,
            version=version_str,
            source="langfuse",
        )
    except Exception as exc:
        log_warning(f"Failed to fetch prompt '{ref.name}' from Langfuse: {exc}")
        return None


async def _afetch_from_langfuse(ref: ManagedPrompt) -> Optional[ResolvedPrompt]:
    """Async variant — delegates to sync fetch in a thread to avoid blocking.

    The Langfuse Python SDK is synchronous; wrapping in a thread keeps the
    event loop responsive.
    """
    import asyncio

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch_from_langfuse, ref)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class PromptManager:
    """Resolve a :class:`ManagedPrompt` from Langfuse with a local fallback.

    Parameters
    ----------
    cache_ttl:
        How long (in seconds) to keep a resolved prompt in memory before
        re-fetching.  Defaults to 300 s (5 min).  Set to 0 to disable
        caching entirely.
    """

    def __init__(self, cache_ttl: int = _DEFAULT_TTL_SECONDS) -> None:
        self.cache_ttl = cache_ttl

    # -- sync ---------------------------------------------------------------

    def resolve(self, ref: ManagedPrompt) -> ResolvedPrompt:
        """Resolve *ref* and record the version on the active span."""
        cached = _get_cached(ref)
        if cached is not None:
            _set_prompt_on_span(cached.name, cached.version)
            return cached

        resolved = _fetch_from_langfuse(ref)
        if resolved is None:
            resolved = self._fallback(ref)

        # Cache fallback results with a short TTL so Langfuse recovery is
        # picked up quickly, rather than serving stale fallback for the full
        # cache_ttl window.  When cache_ttl=0, skip caching entirely.
        if self.cache_ttl > 0:
            effective_ttl = (
                min(_TRANSIENT_RETRY_SECONDS, self.cache_ttl) if resolved.source == "fallback" else self.cache_ttl
            )
            _put_cache(ref, resolved, effective_ttl)
        _set_prompt_on_span(resolved.name, resolved.version)
        return resolved

    # -- async --------------------------------------------------------------

    async def aresolve(self, ref: ManagedPrompt) -> ResolvedPrompt:
        """Async variant of :meth:`resolve`."""
        cached = _get_cached(ref)
        if cached is not None:
            _set_prompt_on_span(cached.name, cached.version)
            return cached

        resolved = await _afetch_from_langfuse(ref)
        if resolved is None:
            resolved = self._fallback(ref)

        if self.cache_ttl > 0:
            effective_ttl = (
                min(_TRANSIENT_RETRY_SECONDS, self.cache_ttl) if resolved.source == "fallback" else self.cache_ttl
            )
            _put_cache(ref, resolved, effective_ttl)
        _set_prompt_on_span(resolved.name, resolved.version)
        return resolved

    # -- fallback -----------------------------------------------------------

    @staticmethod
    def _fallback(ref: ManagedPrompt) -> ResolvedPrompt:
        if ref.fallback is None:
            raise PromptResolutionError(
                f"Managed prompt '{ref.name}' could not be fetched from Langfuse and no local fallback was provided."
            )
        text = ref.fallback
        if ref.variables:
            # Use string.Template.safe_substitute for safe variable substitution.
            # This handles literal braces in prompt text (e.g. JSON examples)
            # without crashing, matching the pattern in format_message_with_state_variables.
            try:
                tmpl = string.Template(text)
                text = tmpl.safe_substitute(ref.variables)
            except Exception:
                log_warning(f"Variable substitution failed in fallback for prompt '{ref.name}'")
        return ResolvedPrompt(
            text=text,
            name=ref.name,
            version="fallback",
            source="fallback",
        )


# ---------------------------------------------------------------------------
# Module-level convenience (used by agent/_messages.py)
# ---------------------------------------------------------------------------

_default_manager: Optional[PromptManager] = None
_default_manager_lock = threading.Lock()


def _get_default_manager() -> PromptManager:
    """Return a lazily-created singleton PromptManager.

    The TTL is read from ``TracingSettings`` once on first access.
    If settings fail to load (e.g. malformed env var), falls back to
    ``_DEFAULT_TTL_SECONDS`` and logs a warning rather than crashing.
    """
    global _default_manager
    if _default_manager is not None:
        return _default_manager

    with _default_manager_lock:
        if _default_manager is not None:
            return _default_manager

        ttl = _DEFAULT_TTL_SECONDS
        try:
            from egpt.settings.tracing import get_tracing_settings

            settings = get_tracing_settings()
            ttl = int(settings.prompt_cache_ttl)
        except Exception as exc:
            log_warning(f"Failed to read prompt settings, using default TTL={_DEFAULT_TTL_SECONDS}s: {exc}")

        _default_manager = PromptManager(cache_ttl=ttl)
        return _default_manager


def _reset_default_manager() -> None:
    """Reset the singleton manager. Useful in tests."""
    global _default_manager
    with _default_manager_lock:
        _default_manager = None


def resolve_managed_prompt(ref: ManagedPrompt) -> ResolvedPrompt:
    """Resolve a managed prompt (sync)."""
    return _get_default_manager().resolve(ref)


async def aresolve_managed_prompt(ref: ManagedPrompt) -> ResolvedPrompt:
    """Resolve a managed prompt (async)."""
    return await _get_default_manager().aresolve(ref)
