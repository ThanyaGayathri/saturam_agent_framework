from egpt.integrations.tools.zep.zep import (
    DEFAULT_INSTRUCTIONS,
    ZepAsyncTools,
    ZepTools,
)

__all__ = [
    "DEFAULT_INSTRUCTIONS",
    "ZepAsyncTools",
    "ZepTools",
]


def __getattr__(name: str):
    # Before EGPT-1.7 this was a flat module, so ``from egpt.tools.zep import X`` reached
    # anything it bound at import time -- including names it had imported rather than
    # defined. ``__all__`` above is the curated surface for star-imports and type checking;
    # this fallback keeps the rest reachable so the move is not a silent compat break.
    import importlib

    return getattr(importlib.import_module("egpt.integrations.tools.zep.zep"), name)
