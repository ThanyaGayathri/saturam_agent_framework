# Database layer

## Table prefix

Persisted tables use a configurable prefix. The default is `egpt_` (see
`egpt.brand.TABLE_PREFIX`), so fresh provisioning creates names such as
`egpt_sessions`, `egpt_components`, and `egpt_traces`.

### Override options

1. **Global prefix** — pass `table_prefix` when constructing any `BaseDb`
   subclass:

   ```python
   from egpt.db.sqlite import SqliteDb

   db = SqliteDb(db_file="tmp/app.db", table_prefix="acme_")
   ```

2. **Per-table names** — pass explicit `session_table`, `memory_table`, etc.
   These take precedence over the prefixed default:

   ```python
   db = SqliteDb(db_file="tmp/app.db", session_table="custom_sessions")
   ```

Serialized db configs from `to_dict()` include `table_prefix` and per-table
keys for round-trip through the components API.

Existing deployments with `agno_*` tables are not migrated automatically;
point overrides at the legacy names or run a deployment-specific migration.
