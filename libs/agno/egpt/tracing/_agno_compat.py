"""Production-side ``agno`` → ``egpt`` module aliasing (aicoe-tracker#200).

The third-party instrumentor (``openinference-instrumentation-agno``)
hardcodes ``from agno.agent import _run`` and similar imports inside its
``_instrument()`` body.  This fork renames the seed's ``agno`` package to
``egpt``, so those imports fail with ``ModuleNotFoundError`` in any
environment that does not explicitly alias ``agno`` → ``egpt`` in
``sys.modules``.

Previously the alias only existed in the *test* conftest
(``tests/unit/observability/conftest.py``), which is why the observability
tests passed while production silently got zero spans.

This module registers the alias at framework level so any call to
``ensure_agno_aliases()`` — including from ``tracing/setup.py`` before
``instrumentor.instrument()`` — makes the instrumentor's imports resolve.

Thread-safety: ``ensure_agno_aliases`` is guarded by a module-level
``threading.Lock`` and is idempotent.
"""

from __future__ import annotations

import sys
import threading

from egpt.utils.log import log_debug, log_warning

_lock = threading.Lock()
_installed = False

# Only alias the module prefixes the instrumentor actually needs.
# Scoped to keep the aliasing surface predictable and matched to what
# AgnoInstrumentor._instrument() imports.
_INSTRUMENTOR_PREFIXES = (
    "egpt.agent",
    "egpt.models",
    "egpt.run",
    "egpt.team",
    "egpt.tools",
    "egpt.workflow",
)


def _safe_import(name: str) -> None:
    """Import *name*, swallowing ImportError for optional-dependency modules.

    ``egpt.workflow`` pulls in ``fastapi`` at module scope, which is only
    present in ``egpt[os]``/``egpt[dev]`` installs — not in the minimal
    ``egpt[langfuse]`` install that ``setup.py`` recommends for tracing-only
    deployments.  Graceful degradation means workflow-span aliasing is
    skipped in that case, not a hard crash.
    """
    try:
        __import__(name)
    except ImportError:
        log_debug(f"Skipping alias for {name} (optional dependency not installed)")


def ensure_agno_aliases() -> None:
    """Register ``sys.modules["agno.*"]`` pointing at ``egpt.*``.

    Idempotent: subsequent calls are no-ops.  Must be called *before* the
    instrumentor's ``_instrument()`` runs, since those imports happen eagerly.

    Only aliases prefixes in ``_INSTRUMENTOR_PREFIXES`` (plus the root
    ``agno`` → ``egpt`` mapping).  If a real ``agno`` package is already
    registered in ``sys.modules``, the entire aliasing pass is skipped
    (root + submodules) to avoid silently clobbering a foreign package.
    """
    global _installed
    if _installed:
        return

    with _lock:
        if _installed:
            return

        import egpt

        # Guard: if a real agno package exists, skip the entire alias pass.
        existing = sys.modules.get("agno")
        if existing is not None and existing is not egpt:
            log_warning(
                f"sys.modules['agno'] already contains {existing!r} (not egpt); "
                "skipping all agno→egpt aliasing to avoid clobbering a foreign package"
            )
            _installed = True
            return

        # Import the core modules the instrumentor needs.  Workflow uses
        # fastapi which is optional — guarded so tracing-only installs
        # degrade gracefully.
        import egpt.agent  # noqa: E402
        import egpt.agent._run  # noqa: E402
        import egpt.agent.agent  # noqa: E402
        import egpt.models  # noqa: E402
        import egpt.models.base  # noqa: E402
        import egpt.models.message  # noqa: E402
        import egpt.run  # noqa: E402
        import egpt.run.agent  # noqa: E402
        import egpt.run.messages  # noqa: E402
        import egpt.run.team  # noqa: E402
        import egpt.team  # noqa: E402
        import egpt.team._run  # noqa: E402
        import egpt.tools  # noqa: E402
        import egpt.tools.function  # noqa: E402

        _safe_import("egpt.workflow")
        _safe_import("egpt.workflow.step")
        _safe_import("egpt.workflow.workflow")

        count = 0
        sys.modules["agno"] = egpt
        count += 1

        # Alias only the instrumentor-relevant prefixes.
        for mod_name in list(sys.modules.keys()):
            if not mod_name.startswith("egpt."):
                continue
            if not any(mod_name == pfx or mod_name.startswith(pfx + ".") for pfx in _INSTRUMENTOR_PREFIXES):
                continue
            alias = "agno." + mod_name[5:]
            if alias not in sys.modules:
                sys.modules[alias] = sys.modules[mod_name]
                count += 1

        _installed = True
        log_debug(f"Registered {count} agno→egpt sys.modules aliases for instrumentor compatibility")


def _reset_for_testing() -> None:
    """Reset the installed flag and strip all ``agno.*`` entries from ``sys.modules``.

    Test-only — not part of the public API.
    """
    global _installed
    with _lock:
        _installed = False
        to_remove = [k for k in sys.modules if k == "agno" or k.startswith("agno.")]
        for k in to_remove:
            del sys.modules[k]
