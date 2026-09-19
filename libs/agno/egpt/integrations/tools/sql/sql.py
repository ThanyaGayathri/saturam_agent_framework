from __future__ import annotations

import asyncio
import json
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence

from egpt.exceptions import SQLGuardRefusal
from egpt.tools import Toolkit
from egpt.utils.log import log_debug, logger

if TYPE_CHECKING:
    # egpt.registry.protocols, not egpt.guardrails.sql_guard.SQLGuard directly: `egpt.guardrails`
    # is exactly as forbidden to `egpt.integrations` as `egpt.agent` is (see that contract in
    # libs/agno/pyproject.toml, and SQLGuardLike's own docstring). SQLGuard satisfies this
    # protocol structurally, with no changes on its side -- and duck typing at runtime
    # (self.guard.check(...) / self.guard.effective_limit(...)) means SQLTools needs no import
    # of `egpt.guardrails` at all, let alone `sqlglot`, unless a caller passes a guard.
    from egpt.registry.protocols import SQLGuardLike

try:
    from sqlalchemy import Engine, create_engine
    from sqlalchemy.inspection import inspect
    from sqlalchemy.orm import Session, sessionmaker
    from sqlalchemy.sql.expression import text
except ImportError:
    raise ImportError("`sqlalchemy` not installed")


class SQLTools(Toolkit):
    def __init__(
        self,
        db_url: Optional[str] = None,
        db_engine: Optional[Engine] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        host: Optional[str] = None,
        port: Optional[int] = None,
        schema: Optional[str] = None,
        dialect: Optional[str] = None,
        tables: Optional[Dict[str, Any]] = None,
        enable_list_tables: bool = True,
        enable_describe_table: bool = True,
        enable_run_sql_query: bool = True,
        all: bool = False,
        guard: Optional[SQLGuardLike] = None,
        **kwargs,
    ):
        # Get the database engine
        _engine: Optional[Engine] = db_engine
        if _engine is None and db_url is not None:
            _engine = create_engine(db_url)
        elif user and password and host and port and dialect:
            if schema is not None:
                _engine = create_engine(f"{dialect}://{user}:{password}@{host}:{port}/{schema}")
            else:
                _engine = create_engine(f"{dialect}://{user}:{password}@{host}:{port}")

        if _engine is None:
            raise ValueError("Could not build the database connection")

        # Database connection
        self.db_engine: Engine = _engine
        self.Session: sessionmaker[Session] = sessionmaker(bind=self.db_engine)

        self.schema = schema

        # Tables this toolkit can access
        self.tables: Optional[Dict[str, Any]] = tables

        # R6's SQL guard (G3). None by default: this same class also backs a write path
        # (DatabaseContextProvider's write sub-agent), and forcing read-only classification
        # there would refuse every legitimate write. Only a caller that knows this instance is
        # read-only should pass a guard.
        self.guard: Optional[SQLGuardLike] = guard

        tools: List[Any] = []
        async_tools: List[Any] = []
        if enable_list_tables or all:
            tools.append(self.list_tables)
        if enable_describe_table or all:
            tools.append(self.describe_table)
        if enable_run_sql_query or all:
            tools.append(self.run_sql_query)
            # Registered under the *same* tool name as the sync variant (the
            # `async_tools=[(callable, name)]` convention every async-capable toolkit in this
            # repo follows, e.g. PerplexityTools) so Toolkit.get_async_functions() picks the
            # async version in an agent's async run path. Without this, `arun_sql_query`
            # exists as a plain method but is never reachable through tool-calling -- an
            # agent's `arun()` would still dispatch to the synchronous `run_sql_query` and
            # block the event loop on the guard's DB I/O.
            async_tools.append((self.arun_sql_query, "run_sql_query"))

        super().__init__(name="sql_tools", tools=tools, async_tools=async_tools, **kwargs)

    def list_tables(self) -> str:
        """Use this function to get a list of table names in the database.

        Returns:
            str: list of tables in the database.
        """
        if self.tables is not None:
            return json.dumps(self.tables)

        try:
            log_debug("listing tables in the database")
            inspector = inspect(self.db_engine)
            if self.schema:
                table_names = inspector.get_table_names(schema=self.schema)
            else:
                table_names = inspector.get_table_names()
            log_debug(f"table_names: {table_names}")
            return json.dumps(table_names)
        except Exception as e:
            logger.exception("Error getting tables")
            return f"Error getting tables: {e}"

    def describe_table(self, table_name: str) -> str:
        """Use this function to describe a table.

        Args:
            table_name (str): The name of the table to get the schema for.

        Returns:
            str: schema of a table
        """

        try:
            log_debug(f"Describing table: {table_name}")
            inspector = inspect(self.db_engine)
            # A caller whose instructions name tables qualified (`silver.crm_deal`) hands that
            # name straight back here, and passing it on *with* `schema=self.schema` asks for
            # `silver.silver.crm_deal` -- a NoSuchTableError on every table, which this tool
            # then returns to the model as a string rather than raising. The model is left with
            # no column knowledge and guesses, so the failure surfaces as a confidently wrong
            # answer rather than an error. A name that carries its own qualifier is therefore
            # honoured as given; a bare one still falls back to the configured schema.
            qualifier, _, bare_name = table_name.rpartition(".")
            table_schema = inspector.get_columns(bare_name, schema=qualifier or self.schema)
            return json.dumps(
                [
                    {
                        "name": column["name"],
                        "type": str(column["type"]),
                        "nullable": column["nullable"],
                        "default": column.get("default"),
                    }
                    for column in table_schema
                ]
            )
        except Exception as e:
            logger.exception("Error getting table schema")
            return f"Error getting table schema: {e}"

    def run_sql_query(self, query: str, limit: Optional[int] = 10) -> str:
        """Use this function to run a SQL query and return the result.

        Args:
            query (str): The query to run.
            limit (int, optional): The number of rows to return. Defaults to 10. Use `None` to show all results.
        Returns:
            str: Result of the SQL query.
        Notes:
            - The result may be empty if the query does not return any data.
            - If a guard is attached (R6), the effective limit is always capped at the
              guard's configured row cap, regardless of what is requested here -- including
              `None`, which does not mean unlimited when a guard is present.
        """

        try:
            return json.dumps(self.run_sql(sql=query, limit=limit), default=str)
        except SQLGuardRefusal as e:
            # Distinct from the generic error path below: a refusal is a deliberate policy
            # decision, not a fault, and reads that way to the model rather than as an
            # ordinary SQL error.
            return f"Refused: {e.message}"
        except Exception as e:
            logger.exception("Error running query")
            return f"Error running query: {e}"

    async def arun_sql_query(self, query: str, limit: Optional[int] = 10) -> str:
        """Async twin of :meth:`run_sql_query`. SQLAlchemy's sync ``Session`` isn't
        awaitable, so this hands the sync path to a thread rather than pretending otherwise --
        the same idiom ``DatabaseContextProvider.astatus`` already uses.
        """
        return await asyncio.to_thread(self.run_sql_query, query, limit)

    def run_sql(self, sql: str, limit: Optional[int] = None) -> List[dict]:
        """Internal function to run a sql query.

        Args:
            sql (str): The sql query to run.
            limit (int, optional): The number of rows to return. Defaults to None.

        Returns:
            List[dict]: The result of the query.

        Raises:
            SQLGuardRefusal: if a guard is set and refuses the statement. Raised before
                anything is sent to the database.
        """
        log_debug(f"Running sql |\n{sql}")

        if self.guard is not None:
            self.guard.check(sql)  # raises SQLGuardRefusal; nothing executes on a refusal
            limit = self.guard.effective_limit(limit, sql=sql)

        with self.Session() as sess, sess.begin():
            # Postgres-only, and deliberately not nested under the guard check below: a schema
            # needs resolving whether or not a guard happens to be attached, and folding the
            # two together would make an unguarded toolkit silently keep the broken behaviour.
            is_postgres = sess.bind is not None and sess.bind.dialect.name == "postgresql"

            if is_postgres and self.schema:
                # A bare `FROM crm_deal` resolves only if the connection's search_path names
                # this toolkit's schema, and a role need not have one -- egpt_reader in the
                # retail warehouse has none, which is why that deployment's materialiser
                # (product/egpt_retail/warehouse/gold.py) issues exactly this statement before
                # its own work. Without it the tool is caught between two conventions: the
                # caller's instructions may name tables qualified while a model writing
                # ordinary SQL names them bare, and only one of the two can ever execute.
                #
                # SET LOCAL, so it is scoped to this transaction and never leaks into a pooled
                # connection's default for the next caller. Interpolated for the same reason
                # as the timeout below -- SET takes no bind parameter -- and therefore checked
                # here rather than trusted: `schema` is a constructor argument today, not model
                # output, but an unchecked f-string at a SET site is how that stops being true.
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.schema):
                    raise ValueError(f"schema must be a plain SQL identifier, got {self.schema!r}")
                sess.execute(text(f"SET LOCAL search_path = {self.schema}"))

            if is_postgres and self.guard is not None:
                # SET LOCAL scopes the timeout to this transaction only -- it resets on
                # commit/rollback and never leaks into a pooled connection's default for the
                # next caller. Postgres-only: SQLite (the dev-only backend, per CLAUDE.md) has
                # no equivalent, so this is skipped rather than erroring there.
                #
                # Interpolated, not bound: Postgres's SET command does not accept a bind
                # parameter for its value (`SET LOCAL statement_timeout = $1` is a syntax
                # error against a real server -- caught by running this against real Postgres,
                # not by any mock). Safe only because of the isinstance check on the next
                # line: `self.guard` is typed against SQLGuardLike, a structural protocol, so
                # nothing here guarantees a *different* implementation validated this value
                # the way SQLGuard.__init__ does -- an f-string interpolation with no runtime
                # check at the point of use would silently reopen a SQL-injection path the
                # moment this value could flow from anywhere less trusted.
                timeout_ms = self.guard.statement_timeout_ms
                if not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool):
                    raise TypeError(f"guard.statement_timeout_ms must be an int, got {type(timeout_ms).__name__}")
                sess.execute(text(f"SET LOCAL statement_timeout = {timeout_ms}"))
            result = sess.execute(text(sql))

            # DML (INSERT/UPDATE/DELETE) and DDL don't return rows — don't
            # try to fetch. The `sess.begin()` context still commits on
            # clean exit.
            if not result.returns_rows:  # type: ignore[attr-defined]
                return []

            try:
                # `limit == 0` is handled explicitly rather than passed to fetchmany(0):
                # DB-API drivers are inconsistent about what size=0 means, and Python's
                # own sqlite3 driver (verified directly, not assumed) returns *every* row
                # for fetchmany(0) -- the opposite of "zero rows". Checking `is not None`
                # rather than truthiness is also necessary on its own: with a guard
                # attached, `limit` here is `effective_limit(...)`'s return value, which
                # promises "always <= row_cap", and a plain `if limit:` would treat an
                # explicit, guard-computed `0` as falsy and fall through to fetchall().
                rows: Sequence[Any]
                if limit == 0:
                    rows = []
                elif limit is not None:
                    rows = result.fetchmany(limit)
                else:
                    rows = result.fetchall()
                return [row._asdict() for row in rows]
            except Exception:
                logger.exception("Error while executing SQL")
                return []

    async def arun_sql(self, sql: str, limit: Optional[int] = None) -> List[dict]:
        """Async twin of :meth:`run_sql`."""
        return await asyncio.to_thread(self.run_sql, sql, limit)
