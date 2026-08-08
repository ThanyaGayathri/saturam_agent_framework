"""Built-in DB backend declarations.

One row per backend package:

    key -> (module, sync_class_name, async_class_name or None, reconstructable)

``key`` is the string a backend serializes as ``to_dict()["type"]``, so ``db_from_dict``
can resolve a stored config without an if/elif chain and without core naming any backend
module. ``async_postgres`` has no row of its own -- it is an alias package re-exporting the
``AsyncPostgresDb`` this table already reaches through the ``postgres`` row.

``reconstructable`` says whether ``db_from_dict`` may build this backend from a stored
config. It is NOT the same question as "is this class importable", and not quite the same
as "does this class define from_dict" either -- it is a policy, and the two rows where
policy and capability diverge carry ``reconstruct_exempt_reason`` to say why:

A backend that does not override ``BaseDb.from_dict`` inherits an implementation that
forwards all 18 ``TABLE_SUFFIXES`` keyword arguments, which its ``__init__`` does not
accept -- so dispatching to it turns an accurate "Unknown database type" warning into an
obscure ``TypeError`` about a table-name kwarg, and the caller still ends up with no db.
Keeping the flag false preserves the pre-EGPT-1.6 behaviour exactly. ``in_memory`` is false
for a different reason: its ``from_dict`` does succeed, but rebuilding an ephemeral store
from a persisted config hands the caller a live empty db instead of the None that makes the
caller fall back to the AgentOS-level db -- silent data loss in place of a visible warning.

Adding serialization to a backend is a two-part change: give it a ``from_dict`` that
accepts its own constructor's arguments, then flip this flag.
"""

from egpt.registry.discovery import SOURCE_BUILTIN, register

# Rows whose `reconstructable` deliberately disagrees with whether the class defines
# from_dict. The guard in tests/unit/db/test_backend_resolution.py requires a reason here
# for any such row, so the asymmetry stays a decision rather than a typo.
_RECONSTRUCT_EXEMPT_REASONS = {
    "in_memory": (
        "from_dict succeeds, but rebuilding an ephemeral store returns a live empty db "
        "instead of the None that triggers AgentOS-db fallback -- silent data loss."
    ),
}

_ROWS = {
    "clickhouse": ("egpt.integrations.db.clickhouse", "ClickhouseDb", None, True),
    "dynamo": ("egpt.integrations.db.dynamo", "DynamoDb", None, False),
    "firestore": ("egpt.integrations.db.firestore", "FirestoreDb", None, False),
    "gcs_json": ("egpt.integrations.db.gcs_json", "GcsJsonDb", None, False),
    "in_memory": ("egpt.integrations.db.in_memory", "InMemoryDb", None, False),
    "json": ("egpt.integrations.db.json", "JsonDb", None, True),
    "mongo": ("egpt.integrations.db.mongo", "MongoDb", "AsyncMongoDb", False),
    "mysql": ("egpt.integrations.db.mysql", "MySQLDb", "AsyncMySQLDb", False),
    "postgres": ("egpt.integrations.db.postgres", "PostgresDb", "AsyncPostgresDb", True),
    "redis": ("egpt.integrations.db.redis", "RedisDb", None, False),
    "singlestore": ("egpt.integrations.db.singlestore", "SingleStoreDb", None, False),
    "sqlite": ("egpt.integrations.db.sqlite", "SqliteDb", "AsyncSqliteDb", True),
    "surrealdb": ("egpt.integrations.db.surrealdb", "SurrealDb", None, False),
}


def register_all() -> None:
    """Register every row. Called by the discovery seam, and safe to call again."""
    for key, (module, sync_class, async_class, reconstructable) in _ROWS.items():
        register(
            "db",
            key,
            module=module,
            attr=sync_class,
            source=SOURCE_BUILTIN,
            override=True,
            async_attr=async_class,
            reconstructable=reconstructable,
            reconstruct_exempt_reason=_RECONSTRUCT_EXEMPT_REASONS.get(key),
        )
