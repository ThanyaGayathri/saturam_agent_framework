from egpt.integrations.tools.apify.apify import (
    APIFY_API_ENDPOINT_GET_DEFAULT_BUILD,
    MAX_DESCRIPTION_LEN,
    REQUESTS_TIMEOUT_SECS,
    ApifyTools,
    actor_id_to_tool_name,
    create_apify_client,
    get_actor_latest_build,
    props_to_json_schema,
    prune_actor_input_schema,
)

__all__ = [
    "APIFY_API_ENDPOINT_GET_DEFAULT_BUILD",
    "ApifyTools",
    "MAX_DESCRIPTION_LEN",
    "REQUESTS_TIMEOUT_SECS",
    "actor_id_to_tool_name",
    "create_apify_client",
    "get_actor_latest_build",
    "props_to_json_schema",
    "prune_actor_input_schema",
]


def __getattr__(name: str):
    # Before EGPT-1.7 this was a flat module, so ``from egpt.tools.apify import X`` reached
    # anything it bound at import time -- including names it had imported rather than
    # defined. ``__all__`` above is the curated surface for star-imports and type checking;
    # this fallback keeps the rest reachable so the move is not a silent compat break.
    import importlib

    return getattr(importlib.import_module("egpt.integrations.tools.apify.apify"), name)
