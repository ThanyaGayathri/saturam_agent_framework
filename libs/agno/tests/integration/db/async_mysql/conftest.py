from unittest.mock import Mock

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from egpt.integrations.db.mysql import AsyncMySQLDb


@pytest.fixture
def mock_async_engine():
    """Create a mock async SQLAlchemy engine"""
    engine = Mock(spec=AsyncEngine)
    return engine


@pytest.fixture
def async_mysql_db(mock_async_engine) -> AsyncMySQLDb:
    """Create an AsyncMySQLDb instance with mock engine"""
    return AsyncMySQLDb(
        db_engine=mock_async_engine,
        db_schema="test_schema",
        session_table="test_sessions",
        memory_table="test_memories",
        metrics_table="test_metrics",
        eval_table="test_evals",
        knowledge_table="test_knowledge",
    )


# In MySQL, db_schema IS a database (CREATE DATABASE IF NOT EXISTS `{db_schema}` in
# mysql/utils.py), unlike Postgres where a schema lives inside a database. The stock
# mysql:8 entrypoint grants the `ai` user rights on the `ai` database only, so a
# `test_schema` schema copied from the async_postgres conftest could never be created
# (Access denied, swallowed into a log_warning) and its teardown DROP DATABASE errored
# once per test. Operate inside `ai` and drop tables, not the database.
_TEST_TABLES = (
    "test_sessions",
    "test_memories",
    "test_metrics",
    "test_evals",
    "test_knowledge",
    "schema_versions",
)


@pytest_asyncio.fixture
async def async_mysql_engine():
    """Create an async MySQL engine for testing using the actual database setup"""
    # Use the asyncmy driver for async MySQL connections
    db_url = "mysql+asyncmy://ai:ai@localhost:3306/ai"
    engine = create_async_engine(db_url)

    # Test connection
    async with engine.begin() as conn:
        await conn.execute(text("SELECT 1"))

    yield engine

    # Cleanup: drop the test tables inside the granted database
    async with engine.begin() as conn:
        for table in _TEST_TABLES:
            await conn.execute(text(f"DROP TABLE IF EXISTS `ai`.`{table}`"))

    await engine.dispose()


@pytest_asyncio.fixture
async def async_mysql_db_real(async_mysql_engine) -> AsyncMySQLDb:
    """Create AsyncMySQLDb with real async MySQL engine"""
    return AsyncMySQLDb(
        db_engine=async_mysql_engine,
        db_schema="ai",
        session_table="test_sessions",
        memory_table="test_memories",
        metrics_table="test_metrics",
        eval_table="test_evals",
        knowledge_table="test_knowledge",
    )
