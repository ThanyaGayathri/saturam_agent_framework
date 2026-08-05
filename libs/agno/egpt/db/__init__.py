from egpt.db.base import BaseDb, SessionType

__all__ = [
    "BaseDb",
    "SessionType",
]


# Concrete backends moved to egpt.integrations.db.<backend> in EGPT-1.6. These four names
# were historically re-exported here, so they keep resolving -- now through the
# DB_BACKEND_CLASSES table, which means this module names no backend module of its own.
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

    import importlib
    import warnings

    # Imported inside the function on purpose: at module level this would add an
    # egpt.db -> egpt.db.utils -> egpt.metrics/egpt.models.message edge at package-init.
    from egpt.db.utils import DB_BACKEND_CLASSES

    module_name, class_name = DB_BACKEND_CLASSES[key]
    warnings.warn(
        f"egpt.db.{name} is deprecated; import it from {module_name} instead. "
        "This re-export will be removed in a future release.",
        DeprecationWarning,
        stacklevel=2,
    )
    return getattr(importlib.import_module(module_name), class_name)
