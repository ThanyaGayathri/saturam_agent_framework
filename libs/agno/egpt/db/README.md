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
   from egpt.integrations.db.sqlite import SqliteDb

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

## Tenant scoping on the component tables (v2.6.0)

The `components`, `component_configs` and `component_links` tables carry a nullable
`tenant_id` column plus tenant-leading composite indexes. Every `BaseDb` component
method accepts a `tenant_id`:

- On writes it is stored on the rows the call creates. An update never changes it —
  a component does not move between tenants via an upsert.
- On reads (and on the lookups behind a mutation) `tenant_id=None` applies **no**
  tenant predicate, so existing single-tenant callers keep working unchanged. Passing
  a value restricts the statement to that tenant.
- Rows written before this change keep `tenant_id = NULL`, meaning "unscoped".

`component_id` remains the primary key and is therefore globally unique: two tenants
cannot both own the same `component_id`.

**Pre-existing rows disappear from scoped calls until they are backfilled.** The predicate
is a strict `tenant_id = ?`; there is deliberately no `OR tenant_id IS NULL`, because
treating NULL as "belongs to everyone" would be a cross-tenant leak. The consequence is a
behavioural cliff at the moment a deployment starts passing `tenant_id`: components created
before the migration carry NULL and are invisible to every scoped read, while remaining
visible to unscoped ones. Backfill them to the owning tenant as part of adopting
multi-tenancy:

```sql
UPDATE egpt_components        SET tenant_id = :tenant WHERE tenant_id IS NULL;
UPDATE egpt_component_configs SET tenant_id = :tenant WHERE tenant_id IS NULL;
UPDATE egpt_component_links   SET tenant_id = :tenant WHERE tenant_id IS NULL;
```

**What actually starts passing `tenant_id`.** As of EGPT-2.3 the components router does, taken
from the JWT claim named by `AuthorizationConfig(tenant_id_claim=...)`. That claim is unset by
default, so `tenant_id` stays `None` and no predicate is applied until an operator configures it —
which is the point at which the backfill above stops being advice and becomes a prerequisite. See
[docs/capability-policy.md](../../../../docs/capability-policy.md).

As of EGPT-2.4 the components router additionally **refuses** a caller who resolves to no tenant
once that claim is configured, rather than serving them every tenant's rows, and service accounts
carry a tenant of their own. See [docs/tenancy.md](../../../../docs/tenancy.md).

## Service account tenancy (EGPT-2.4)

`egpt_service_accounts` gained a nullable `tenant_id` column and an index on it, via migration
`v2_7_0`. The value is stamped at mint time and is **immutable** — it is not in
`SERVICE_ACCOUNT_MUTABLE_COLUMNS`, so re-tenanting requires revoking and re-minting.
`get_service_account` and `get_service_accounts` take a trailing `tenant_id` with the same
semantics as the component methods: `None` applies no predicate, a value restricts the rows
(and, for the listing, the returned count).

> **⚠️ Deploy order matters more here than it did for `v2_6_0`.** `_get_table` raises
> `Table ... has an invalid schema` for a live table missing a declared column, and
> `ServiceAccountVerifier` turns that into `UNAVAILABLE` — so a deployment that ships this code
> before running the migration fails **every** service-account token with a 503 until it does.
> Run `asyncio.run(MigrationManager(db).up())` first, or in the same maintenance step.

Tokens minted before this change carry `tenant_id = NULL` (unscoped) and are refused on the
component routes of a tenancy-enabled deployment; they must be re-minted.

**Upgrading an existing deployment.** Migrations are not run automatically — table
creation only stamps the schema version. A deployment that already has component
tables must run the migration before those tables will load again, otherwise
`_get_table` raises `Table ... has an invalid schema` for the missing column:

```python
import asyncio
from egpt.db.migrations.manager import MigrationManager

asyncio.run(MigrationManager(db).up())
```

The migration is reversible; `MigrationManager(db).down(target_version="2.5.6")`
drops the indexes and the column and leaves all existing version / stage / link data
intact. Only Postgres and SQLite define these tables. On SQLite, the down-migration
needs SQLite 3.35+ for `DROP COLUMN`; on older builds the indexes are dropped and the
column is left in place with a log message.

## Out of scope / follow-up

Container-level namespaces intentionally still default to the legacy `agno`
brand and are **not** renamed by the table-prefix change (renaming them would
silently orphan existing data one level up): Mongo `db_name="agno"`, Redis
`db_prefix="agno"`, ClickHouse `database="agno"`, GCS path prefix `"agno/"`,
and the `db_schema="agno"` defaults in the SQL schema helpers. A future
change may make these configurable with a branded default plus an upgrade
path.
