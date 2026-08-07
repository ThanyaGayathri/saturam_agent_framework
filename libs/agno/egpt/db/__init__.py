from egpt.db.base import BaseDb, SessionType

__all__ = [
    "BaseDb",
    "SessionType",
]


# Concrete backends moved to egpt.integrations.db.<backend> in EGPT-1.6. These four names
# were historically re-exported here, so they keep resolving -- now through the discovery
# seam, which means this module names no backend module of its own.
# Deliberately not widened to the other backends: this is a surface to retire, not grow.
_LEGACY_BACKEND_ATTRS = {
    "ClickhouseDb": "clickhouse",
    "DynamoDb": "dynamo",
    "MongoDb": "mongo",
    "PostgresDb": "postgres",
}


def __getattr__(name: str):
    """Deprecated lazy access to the four backends this package used to re-export."""
    key = _LEGACY_BACKEND_ATTRS.get(name)
    if key is None:
        raise AttributeError(f"module '{__name__}' has no attribute '{name}'")

    import warnings

    from egpt.registry import discovery

    registration = discovery.get("db", key)
    if registration is None:
        # A key dropped out of the db declarations without this map being updated. Raising
        # AttributeError rather than letting the seam's ValueError escape keeps the module
        # contract intact, so hasattr() on the deprecated path still answers False instead
        # of blowing up. The guard test asserts this state is unreachable.
        raise AttributeError(f"module '{__name__}' has no attribute '{name}'")

    warnings.warn(
        f"egpt.db.{name} is deprecated; import it from {registration.module} instead. "
        "This re-export will be removed in a future release.",
        DeprecationWarning,
        stacklevel=2,
    )
    return registration.load()
