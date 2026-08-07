from egpt.integrations.tools.searxng.searxng import (
    Searxng,
    SearxngTools,
)

__all__ = [
    "Searxng",
    "SearxngTools",
]


def __getattr__(name: str):
    # Before EGPT-1.7 this was a flat module, so ``from egpt.tools.searxng import X`` reached
    # anything it bound at import time -- including names it had imported rather than
    # defined. ``__all__`` above is the curated surface for star-imports and type checking;
    # this fallback keeps the rest reachable so the move is not a silent compat break.
    import importlib

    return getattr(importlib.import_module("egpt.integrations.tools.searxng.searxng"), name)
