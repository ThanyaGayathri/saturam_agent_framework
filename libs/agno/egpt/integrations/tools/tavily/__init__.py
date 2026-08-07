from egpt.integrations.tools.tavily.tavily import (
    TavilyTools,
)

__all__ = [
    "TavilyTools",
]


def __getattr__(name: str):
    # Before EGPT-1.7 this was a flat module, so ``from egpt.tools.tavily import X`` reached
    # anything it bound at import time -- including names it had imported rather than
    # defined. ``__all__`` above is the curated surface for star-imports and type checking;
    # this fallback keeps the rest reachable so the move is not a silent compat break.
    import importlib

    return getattr(importlib.import_module("egpt.integrations.tools.tavily.tavily"), name)
