import importlib
from typing import Optional, Union

from packaging import version as packaging_version
from packaging.version import Version

from egpt.db.base import AsyncBaseDb, BaseDb
from egpt.db.table_names import MEMORY_TIER_TABLE_TYPES, memory_tier_table_attr
from egpt.utils.log import log_debug, log_error, log_info, log_warning


class MigrationManager:
    """Manager class to handle database migrations"""

    available_versions: list[tuple[str, Version]] = [
        ("v2_0_0", packaging_version.parse("2.0.0")),
        ("v2_3_0", packaging_version.parse("2.3.0")),
        ("v2_5_0", packaging_version.parse("2.5.0")),
        ("v2_5_6", packaging_version.parse("2.5.6")),
        ("v2_6_0", packaging_version.parse("2.6.0")),
        ("v2_7_0", packaging_version.parse("2.7.0")),
        ("v2_8_0", packaging_version.parse("2.8.0")),
        ("v2_9_0", packaging_version.parse("2.9.0")),
        ("v2_10_0", packaging_version.parse("2.10.0")),
    ]

    # Mapping from table_type to the BaseDb attribute holding that table's resolved name.
    # Shared by up() and down() so the two can never drift.
    _TABLE_TYPE_TO_ATTR = {
        "memories": "memory_table_name",
        "sessions": "session_table_name",
        "metrics": "metrics_table_name",
        "evals": "eval_table_name",
        "knowledge": "knowledge_table_name",
        "culture": "culture_table_name",
        "approvals": "approvals_table_name",
        "components": "components_table_name",
        "component_configs": "component_configs_table_name",
        "component_links": "component_links_table_name",
        "component_audit": "component_audit_table_name",
        # Added by EGPT-2.4 for v2_7_0. Without an entry here the table is simply unreachable
        # by the runner, so a migration targeting it would never run -- which is exactly the
        # state the component tables were in before EGPT-2.1 added theirs.
        "service_accounts": "service_accounts_table_name",
        # Added by EGPT-4.3a for v2_10_0. Traces were unreachable by the migration
        # runner — a migration targeting them would never run. This is what allowed EGPT-4.2's
        # tenant_id column addition to ship without a Postgres migration.
        "traces": "trace_table_name",
        **{table_type: memory_tier_table_attr(table_type) for table_type in MEMORY_TIER_TABLE_TYPES},
    }

    def __init__(self, db: Union[AsyncBaseDb, BaseDb]):
        self.db = db

    @property
    def latest_schema_version(self) -> Version:
        return self.available_versions[-1][1]

    async def _current_version(self, table_name: str) -> Optional[Version]:
        """Read and parse a table's stored schema version.

        Returns None when the backend reports no version, so callers can skip the table
        instead of letting ``packaging.version.parse(None)`` raise and abort the whole run.
        The guard has to live here, before the parse -- a check on the parsed value can never
        fire, because ``parse`` raises on None rather than returning it.

        Note the deliberate asymmetry between the two ways a table can be "unversioned":

        - ``"2.0.0"`` means *not yet stamped*. Every backend returns this floor for a table it
          has no record for, so the table gets migrated up from the earliest schema.
        - ``None`` means *the backend is broken*. Now that all backends return the floor, an
          override that still yields None is a bug in that override, and there is no version
          to reason from -- so the table is skipped with a log rather than migrated blindly.

        Skipping is the conservative read of "we do not know what state this table is in": a
        crash takes unrelated tables down with it, and migrating from an assumed floor would
        re-run migrations against a table that may already be current.
        """
        if isinstance(self.db, AsyncBaseDb):
            raw_version = await self.db.get_latest_schema_version(table_name)
        else:
            raw_version = self.db.get_latest_schema_version(table_name)

        if raw_version is None:
            return None
        return packaging_version.parse(raw_version)

    def _supports(self, table_type: str, db_type: str) -> bool:
        """Whether any shipped migration declares it can handle (table_type, db_type).

        Declarative, not derived from executing a migration: each ``versions/vX_Y_Z`` module
        exposes its own ``supports()`` predicate (the same one that gates its ``up``/``down``
        dispatch), checked here across the *whole* catalogue -- not just the versions between
        the table's current version and the target. Support, once shipped, does not need
        reasserting by every later version to stay true: a table with nothing left to migrate
        must still be stamped all the way to the latest version, not just the last version that
        actually touched it -- that is what makes "stamp only when a migration executed" wrong:
        it would stamp a SQLite table with nothing left to migrate at the last version that
        touched it (e.g. 2.3.0) instead of the target, so every later ``up()`` would re-walk the
        tail of the migration chain forever.

        A backend this returns ``False`` for has no code path, in any shipped migration, that
        would ever touch this table -- so ``up()`` must not stamp it: the stamp would claim a
        schema state that was never verified, let alone reached.

        A table type can also be genuinely supported with nothing to migrate: ``component_audit``
        was born with ``tenant_id`` already in place, so ``v2_6_0.supports()`` returns ``True``
        for it while its ``up``/``down`` are a declared no-op (see that module's docstring). That
        is different from a table type no migration recognizes at all, which stays unstamped.
        """
        for version, _ in self.available_versions:
            if version == "v2_0_0":
                continue  # the 2.0.0 floor ships no migration module; nothing to import
            module = importlib.import_module(f"egpt.db.migrations.versions.{version}")
            if module.supports(table_type, db_type):
                return True
        return False

    def _resolve_tables(self, table_type: Optional[str]) -> Optional[list[tuple[str, str]]]:
        """Resolve the (table_type, table_name) pairs to migrate.

        Not every db models every table type: ``AsyncBaseDb`` has no component tables at all,
        so the attributes behind ``components`` / ``component_configs`` / ``component_links``
        are absent there. Such table types are skipped rather than raising ``AttributeError``,
        which would take the whole migration run down with them.

        Returns None when the caller named a table type this db cannot migrate.
        """
        if table_type:
            if table_type not in self._TABLE_TYPE_TO_ATTR:
                log_warning(
                    f"Invalid table type: {table_type}. Use one of: {', '.join(self._TABLE_TYPE_TO_ATTR.keys())}"
                )
                return None
            table_name = getattr(self.db, self._TABLE_TYPE_TO_ATTR[table_type], None)
            if table_name is None:
                log_warning(f"{type(self.db).__name__} has no {table_type} table; nothing to migrate.")
                return None
            return [(table_type, table_name)]

        tables = []
        for tt, attr in self._TABLE_TYPE_TO_ATTR.items():
            table_name = getattr(self.db, attr, None)
            if table_name is None:
                log_debug(f"Skipping {tt}: {type(self.db).__name__} does not define {attr}.")
                continue
            tables.append((tt, table_name))
        return tables

    async def up(self, target_version: Optional[str] = None, table_type: Optional[str] = None, force: bool = False):
        """Handle executing an up migration.

        Args:
            target_version: The version to migrate to, e.g. "v3.0.0". If not provided, the latest available version will be used.
            table_type: The type of table to migrate. If not provided, all table types will be considered.
        """

        # If not target version is provided, use the latest available version
        if not target_version:
            _target_version = self.latest_schema_version
            log_info(
                f"No target version provided. Will migrate to the latest available version: {str(_target_version)}"
            )
        else:
            _target_version = packaging_version.parse(target_version)

        tables = self._resolve_tables(table_type)
        if tables is None:
            return

        db_type = type(self.db).__name__

        # Handle migrations for each table separately (extend in future if needed). The loop
        # variable is deliberately not named `table_type`: this method also takes a `table_type`
        # parameter, and `self._supports(...)` below must read the per-table value, not whichever
        # of the two a reader assumes.
        for tt, table_name in tables:
            current_version = await self._current_version(table_name)
            if current_version is None:
                log_info(f"Skipping migration: No version found for table {table_name}.")
                continue

            # If the target version is less or equal to the current version, no migrations needed
            if _target_version <= current_version and not force:
                log_info(
                    f"Skipping migration: the version of table '{table_name}' ({current_version}) is less or equal to the target version ({_target_version})."
                )
                continue

            if not self._supports(tt, db_type):
                # No shipped migration has ever touched (table_type, db_type): the table may not
                # even exist on this backend. Stamping it would claim a schema state that was
                # never verified, so it is left exactly as it was found.
                log_info(
                    f"{db_type} has no migration support for table type '{tt}'; leaving table {table_name} unstamped."
                )
                continue

            log_info(
                f"Starting database migration for table {table_name}. Current version: {current_version}. Target version: {_target_version}."
            )

            # Find files after the current version
            latest_version = None
            for version, normalised_version in self.available_versions:
                if normalised_version > current_version:
                    if target_version and normalised_version > _target_version:
                        break

                    log_info(f"Applying migration {normalised_version} on {table_name}")
                    migration_executed = await self._up_migration(version, tt, table_name)
                    latest_version = normalised_version.public
                    if migration_executed:
                        log_info(f"Successfully applied migration {normalised_version} on table {table_name}")
                    else:
                        log_info(f"No changes needed for migration {normalised_version} on table {table_name}")

            if latest_version:
                log_info(f"Storing version {latest_version} in database for table {table_name}")
                if isinstance(self.db, AsyncBaseDb):
                    await self.db.upsert_schema_version(table_name, latest_version)
                else:
                    self.db.upsert_schema_version(table_name, latest_version)
                log_info(f"Successfully stored version {latest_version} in database for table {table_name}")
            log_info("----------------------------------------------------------")

    async def _up_migration(self, version: str, table_type: str, table_name: str) -> bool:
        """Run the database-specific logic to handle an up migration.

        Args:
            version: The version to migrate to, e.g. "v3.0.0"
        """
        migration_module = importlib.import_module(f"egpt.db.migrations.versions.{version}")

        try:
            if isinstance(self.db, AsyncBaseDb):
                return await migration_module.async_up(self.db, table_type, table_name)
            else:
                return migration_module.up(self.db, table_type, table_name)
        except Exception as e:
            log_error(f"Error running migration to version {version}: {str(e)}")
            raise

    async def down(self, target_version: str, table_type: Optional[str] = None, force: bool = False):
        """Handle executing a down migration.

        Unlike ``up()``, this has no manager-level ``self._supports()`` gate: it relies entirely
        on each version module's own ``supports()``/dispatch to decide whether it did real work.
        That is intentional -- reverting is table-type-scoped per module already, and a second
        gate here would not add anything ``supports()`` doesn't already check -- but it does mean
        ``down()`` has no defense-in-depth against a module's dispatch being wrong.

        Args:
            target_version: The version to migrate to. e.g. "v2.3.0"
            table_type: The type of table to migrate. If not provided, all table types will be considered.
        """
        _target_version = packaging_version.parse(target_version)

        tables = self._resolve_tables(table_type)
        if tables is None:
            return

        for table_type, table_name in tables:
            current_version = await self._current_version(table_name)
            if current_version is None:
                log_info(f"Skipping down migration: No version found for table {table_name}.")
                continue

            if _target_version >= current_version and not force:
                log_warning(
                    f"Skipping down migration: the version of table '{table_name}' ({current_version}) is less or equal to the target version ({_target_version})."
                )
                continue

            any_migration_executed = False
            # Run down migration for all versions between target and current (include down of current version)
            # Apply down migrations in reverse order to ensure dependencies are met
            for version, normalised_version in reversed(self.available_versions):
                if normalised_version > _target_version:
                    log_info(f"Reverting migration {normalised_version} on table {table_name}")
                    migration_executed = await self._down_migration(version, table_type, table_name)
                    if migration_executed:
                        any_migration_executed = True
                        log_info(f"Successfully reverted migration {normalised_version} on table {table_name}")
                    else:
                        log_info(f"Skipping revert of migration {normalised_version} on table {table_name}")

            if any_migration_executed:
                log_info(f"Storing version {_target_version} in database for table {table_name}")
                if isinstance(self.db, AsyncBaseDb):
                    await self.db.upsert_schema_version(table_name, _target_version.public)
                else:
                    self.db.upsert_schema_version(table_name, _target_version.public)
                log_info(f"Successfully stored version {_target_version} in database for table {table_name}")

    async def _down_migration(self, version: str, table_type: str, table_name: str) -> bool:
        """Run the database-specific logic to handle a down migration.

        Args:
            version: The version to migrate to, e.g. "v3.0.0"
        """
        migration_module = importlib.import_module(f"egpt.db.migrations.versions.{version}")
        try:
            if isinstance(self.db, AsyncBaseDb):
                return await migration_module.async_down(self.db, table_type, table_name)
            else:
                return migration_module.down(self.db, table_type, table_name)
        except Exception as e:
            log_error(f"Error running migration to version {version}: {str(e)}")
            raise
