from egpt.db.base import BaseDb, SessionType

__all__ = [
    "BaseDb",
    "SessionType",
]


def __getattr__(name: str):
    """Lazy import for database implementations to avoid forcing all dependencies."""
    if name == "DynamoDb":
        from egpt.db.dynamo import DynamoDb

        return DynamoDb
    elif name == "MongoDb":
        from egpt.db.mongo import MongoDb

        return MongoDb
    elif name == "PostgresDb":
        from egpt.db.postgres import PostgresDb

        return PostgresDb
    elif name == "ClickhouseDb":
        from egpt.db.clickhouse import ClickhouseDb

        return ClickhouseDb
    # Add other db implementations as needed
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
