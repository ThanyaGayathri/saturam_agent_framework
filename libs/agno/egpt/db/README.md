# Database layer

## Table prefix

Persisted tables use a configurable prefix. The default is `egpt_` (canonical
constant: `egpt.db.table_names.DEFAULT_TABLE_PREFIX`), so fresh provisioning
creates names such as `egpt_sessions`, `egpt_components`, and `egpt_traces`.

The prefix must be a safe SQL identifier fragment (`^[a-z][a-z0-9_]*$`, at
most 32 characters) or the empty string `""` for unprefixed table names.

### Override options

1. **Global prefix** — pass `table_prefix` when constructing any `BaseDb` /
   `AsyncBaseDb` subclass:

   ```python
   from egpt.db.sqlite import SqliteDb

   db = SqliteDb(db_file="tmp/app.db", table_prefix="acme_")
   ```

2. **Per-table names** — pass explicit `session_table`, `memory_table`, etc.
   These take precedence over the prefixed default:

   ```python
   db = SqliteDb(db_file="tmp/app.db", session_table="custom_sessions")
   ```

Serialized db configs from `to_dict()` include `table_prefix` plus only the
table names that differ from their prefixed default, so editing the prefix in
a stored config re-derives the default names on reload.

## Upgrading from pre-rename (agno_*) deployments

Existing deployments with `agno_*` tables are **not migrated automatically**.
To keep using the existing tables, pass `table_prefix="agno_"`:

```python
db = PostgresDb(db_url=..., table_prefix="agno_")
```

Safeguards for the upgrade path:

- When a default-prefixed table is about to be created and its `agno_*`
  sibling already exists in the same database, a warning is logged naming
  `table_prefix="agno_"` as the escape hatch.
- Stored component configs that were serialized before the rename contain the
  old `agno_*` default names for every table. These are treated as equal to
  the current defaults when resolving a config against a registered db
  (one-release compatibility shim in `egpt.db.utils`), so they are not
  misread as explicit per-table overrides.

## Out of scope / follow-up

Container-level namespaces intentionally still default to the legacy `agno`
brand and are **not** renamed by the table-prefix change (renaming them would
silently orphan existing data one level up): Mongo `db_name="agno"`, Redis
`db_prefix="agno"`, ClickHouse `database="agno"`, GCS path prefix `"agno/"`,
and the `db_schema="agno"` defaults in the SQL schema helpers. A future
change may make these configurable with a branded default plus an upgrade
path.
