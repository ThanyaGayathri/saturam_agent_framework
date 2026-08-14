import os
import tempfile
import time
import uuid
from pathlib import Path
from unittest.mock import Mock

import pytest
import pytest_asyncio
from sqlalchemy import Engine, create_engine, text

from egpt.agent.agent import Agent
from egpt.integrations.db.postgres import PostgresDb
from egpt.integrations.db.sqlite import AsyncSqliteDb, SqliteDb
from egpt.models.message import Message
from egpt.run.agent import RunOutput
from egpt.run.base import RunStatus
from egpt.run.workflow import WorkflowRunOutput
from egpt.session import Session
from egpt.session.agent import AgentSession
from egpt.session.workflow import WorkflowSession

# Path fragment -> required env var for soft-skip when credentials/services are absent.
# First matching fragment wins. Opt-in flags gate local Docker DB suites in CI.
_PATH_ENV_REQUIREMENTS: tuple[tuple[str, str], ...] = (
    # OpenAI-backed suites
    ("models/openai", "OPENAI_API_KEY"),
    ("models/azure", "AZURE_OPENAI_API_KEY"),
    ("agent/", "OPENAI_API_KEY"),
    ("teams/", "OPENAI_API_KEY"),
    ("workflows/", "OPENAI_API_KEY"),
    # Cloud providers
    ("models/anthropic", "ANTHROPIC_API_KEY"),
    ("models/google", "GOOGLE_API_KEY"),
    ("models/vertexai", "GOOGLE_CLOUD_PROJECT"),
    ("models/groq", "GROQ_API_KEY"),
    ("models/cohere", "CO_API_KEY"),
    ("models/openrouter", "OPENROUTER_API_KEY"),
    ("models/xai", "XAI_API_KEY"),
    ("models/deepseek", "DEEPSEEK_API_KEY"),
    ("models/together", "TOGETHER_API_KEY"),
    ("models/fireworks", "FIREWORKS_API_KEY"),
    ("models/cerebras", "CEREBRAS_API_KEY"),
    ("models/ibm", "IBM_WATSONX_API_KEY"),
    ("models/meta", "LLAMA_API_KEY"),
    ("models/portkey", "PORTKEY_API_KEY"),
    ("models/perplexity", "PERPLEXITY_API_KEY"),
    ("models/huggingface", "HF_TOKEN"),
    ("models/nvidia", "NVIDIA_API_KEY"),
    ("models/nebius", "NEBIUS_API_KEY"),
    ("models/sambanova", "SAMBANOVA_API_KEY"),
    ("models/dashscope", "DASHSCOPE_API_KEY"),
    ("models/deepinfra", "DEEPINFRA_API_KEY"),
    ("models/cometapi", "COMETAPI_KEY"),
    ("models/aimlapi", "AIMLAPI_API_KEY"),
    ("models/langdb", "LANGDB_API_KEY"),
    ("models/vercel", "V0_API_KEY"),
    ("models/aws", "AWS_ACCESS_KEY_ID"),
    # Local / self-hosted (opt-in)
    ("models/ollama", "EGPT_INTEGRATION_OLLAMA"),
    ("models/lmstudio", "EGPT_INTEGRATION_LMSTUDIO"),
    ("models/vllm", "EGPT_INTEGRATION_VLLM"),
    # Local databases (opt-in)
    # The OS tenancy suite needs a real Postgres too (EGPT-2.4). It lives under os/ rather than
    # db/ because it exercises the HTTP surface, so it needs its own fragment -- os/ as a whole
    # is ungated and runs on SQLite.
    ("os/tenancy_postgres", "EGPT_INTEGRATION_POSTGRES"),
    # Tenant-scoped runtime hydration of DB-defined components (EGPT-2.5), same rationale.
    ("os/hydration_postgres", "EGPT_INTEGRATION_POSTGRES"),
    # The component lifecycle audit trail (EGPT-2.6), same rationale.
    ("os/audit_postgres", "EGPT_INTEGRATION_POSTGRES"),
    ("memory/neo4j", "EGPT_INTEGRATION_NEO4J"),
    # Seeding and portable import/export of definitions (EGPT-2.8). Needs real Postgres for a
    # reason SQLite cannot supply: jsonb normalizes key order, so a round-trip assertion written
    # as string equality passes on SQLite's TEXT and fails here.
    ("os/seeding_postgres", "EGPT_INTEGRATION_POSTGRES"),
    ("db/postgres", "EGPT_INTEGRATION_POSTGRES"),
    ("db/async_postgres", "EGPT_INTEGRATION_POSTGRES"),
    ("db/async_mongo", "EGPT_INTEGRATION_MONGO"),
    ("db/async_mysql", "EGPT_INTEGRATION_MYSQL"),
    ("db/surrealdb", "EGPT_INTEGRATION_SURREALDB"),
    ("db/clickhouse", "EGPT_INTEGRATION_CLICKHOUSE"),
)

# Module-level LanceDb imports fail when lancedb is not installed (e.g. macOS x86_64
# has no wheel). Linux CI with egpt[tests] installs lancedb and collects these normally.
_LANCEDB_DEPENDENT_FRAGMENTS = (
    "vector_dbs/test_lancedb.py",
    "agent/test_rag.py",
    "knowledge/test_youtube_knowledge.py",
    "knowledge/test_website_knowledge.py",
    "knowledge/test_text_knowledge.py",
    "knowledge/test_pdf_knowledge.py",
    "knowledge/test_md_knowledge.py",
    "knowledge/test_firecrawl_knowledge.py",
    "knowledge/test_csv_knowledge.py",
    "knowledge/filters/test_agentic_filtering.py",
)

_LANCEDB_QUARANTINE_REASON = (
    "Quarantined (EGPT-0.10): lancedb not installed; install egpt[lancedb] / egpt[tests] to run"
)


def _lancedb_available() -> bool:
    try:
        import lancedb  # noqa: F401
    except ImportError:
        return False
    return True


def pytest_ignore_collect(collection_path: Path, config) -> bool | None:  # noqa: ARG001
    """Skip LanceDb-dependent modules when lancedb is unavailable."""
    if _lancedb_available():
        return None
    path = str(collection_path).replace("\\", "/")
    if not any(frag in path for frag in _LANCEDB_DEPENDENT_FRAGMENTS):
        return None
    if not getattr(config, "_egpt_lancedb_int_q", False):
        config._egpt_lancedb_int_q = True
        tr = config.pluginmanager.get_plugin("terminalreporter")
        if tr is not None:
            tr.write_line(_LANCEDB_QUARANTINE_REASON)
    return True


def pytest_collection_modifyitems(config, items):  # noqa: ARG001
    """Mark integration tests and soft-skip when required env vars are unset."""
    for item in items:
        item.add_marker(pytest.mark.integration)
        path = str(getattr(item, "path", item.fspath)).replace("\\", "/")
        for frag, env in _PATH_ENV_REQUIREMENTS:
            if frag in path and not os.getenv(env):
                item.add_marker(pytest.mark.skip(reason=f"{env} not set"))
                break


@pytest.fixture(autouse=True)
def reset_async_client():
    """Reset global async HTTP client between tests to avoid event loop conflicts."""
    import egpt.utils.http as http_utils

    # Reset before test
    http_utils._global_async_client = None
    yield
    # Reset after test
    http_utils._global_async_client = None


@pytest.fixture
def temp_storage_db_file():
    """Create a temporary SQLite database file for agent storage testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as temp_file:
        db_path = temp_file.name

    yield db_path

    # Clean up the temporary file after the test
    if os.path.exists(db_path):
        os.unlink(db_path)


@pytest.fixture
def temp_memory_db_file():
    """Create a temporary SQLite database file for memory testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as temp_file:
        db_path = temp_file.name

    yield db_path

    # Clean up the temporary file after the test
    if os.path.exists(db_path):
        os.unlink(db_path)


@pytest.fixture
def shared_db(temp_storage_db_file):
    """Create a SQLite storage for sessions."""
    # Use a unique table name for each test run
    table_name = f"sessions_{uuid.uuid4().hex[:8]}"
    db = SqliteDb(session_table=table_name, db_file=temp_storage_db_file)
    return db


@pytest_asyncio.fixture
async def async_shared_db(temp_storage_db_file):
    """Create an async SQLite storage for sessions."""
    # Use a unique table name for each test run
    table_name = f"sessions_{uuid.uuid4().hex[:8]}"
    db = AsyncSqliteDb(session_table=table_name, db_file=temp_storage_db_file)

    # Initialize tables before using
    await db._create_all_tables()

    return db


@pytest.fixture
def mock_engine():
    """Create a mock SQLAlchemy engine"""
    engine = Mock(spec=Engine)
    return engine


@pytest.fixture
def mock_session():
    """Create a mock session"""
    session = Mock(spec=Session)
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=None)
    session.begin = Mock()
    session.begin().__enter__ = Mock(return_value=session)
    session.begin().__exit__ = Mock(return_value=None)
    return session


@pytest.fixture
def postgres_db(mock_engine) -> PostgresDb:
    """Create a PostgresDb instance with mock engine"""
    return PostgresDb(
        db_engine=mock_engine,
        db_schema="test_schema",
        session_table="test_sessions",
        memory_table="test_memories",
        metrics_table="test_metrics",
        eval_table="test_evals",
        knowledge_table="test_knowledge",
    )


@pytest.fixture
def postgres_engine():
    """Create a PostgreSQL engine for testing using the actual database setup"""
    # Use the same connection string as the actual implementation
    db_url = "postgresql+psycopg://ai:ai@localhost:5532/ai"
    engine = create_engine(db_url)

    # Test connection
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
        conn.commit()

    yield engine

    # Cleanup: Drop schema after tests
    with engine.connect() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS test_schema CASCADE"))
        conn.commit()


@pytest.fixture
def postgres_db_real(postgres_engine) -> PostgresDb:
    """Create PostgresDb with real PostgreSQL engine"""
    return PostgresDb(
        db_engine=postgres_engine,
        db_schema="test_schema",
        session_table="test_sessions",
        memory_table="test_memories",
        metrics_table="test_metrics",
        eval_table="test_evals",
        knowledge_table="test_knowledge",
    )


@pytest.fixture
def sqlite_db_real(temp_storage_db_file) -> SqliteDb:
    """Create SQLiteDb with real SQLite engine"""
    return SqliteDb(
        session_table="test_sessions",
        memory_table="test_memories",
        metrics_table="test_metrics",
        eval_table="test_evals",
        knowledge_table="test_knowledge",
        db_file=temp_storage_db_file,
    )


@pytest.fixture
def image_path():
    return Path(__file__).parent / "res" / "images" / "golden_gate.png"


# -- Agent session fixtures --


@pytest.fixture
def session_with_explicit_name(test_agent: Agent):
    """Session with explicit session_name in session_data."""
    run = RunOutput(
        run_id="run-1",
        agent_id=test_agent.id,
        user_id="test-user",
        status=RunStatus.completed,
        messages=[
            Message(role="user", content="Hello, how are you?"),
            Message(role="assistant", content="I'm doing great!"),
        ],
        created_at=int(time.time()),
    )
    return AgentSession(
        session_id="session-explicit-name",
        agent_id=test_agent.id,
        user_id="test-user",
        session_data={"session_name": "My Custom Session Name"},
        runs=[run],
    )


@pytest.fixture
def session_with_user_message(test_agent: Agent):
    """Session without session_name, should use first user message."""
    run = RunOutput(
        run_id="run-1",
        agent_id=test_agent.id,
        user_id="test-user",
        status=RunStatus.completed,
        messages=[
            Message(role="user", content="Hello, how are you?"),
            Message(role="assistant", content="I'm doing great!"),
        ],
        created_at=int(time.time()),
    )
    return AgentSession(
        session_id="session-user-message",
        agent_id=test_agent.id,
        user_id="test-user",
        runs=[run],
    )


@pytest.fixture
def session_with_fallback(test_agent: Agent):
    """Session where first run has no user message, should fallback to second run."""
    run1 = RunOutput(
        run_id="run-1",
        agent_id=test_agent.id,
        user_id="test-user",
        status=RunStatus.completed,
        messages=[
            Message(role="assistant", content="I'm doing great, thank you!"),
        ],
        created_at=int(time.time()) - 3600,
    )
    run2 = RunOutput(
        run_id="run-2",
        agent_id=test_agent.id,
        user_id="test-user",
        status=RunStatus.completed,
        messages=[
            Message(role="user", content="What is the weather?"),
            Message(role="assistant", content="It's sunny and 70 degrees Fahrenheit."),
        ],
        created_at=int(time.time()),
    )
    return AgentSession(
        session_id="session-fallback",
        agent_id=test_agent.id,
        user_id="test-user",
        runs=[run1, run2],
    )


@pytest.fixture
def session_empty_runs(test_agent: Agent):
    """Session with no runs."""
    return AgentSession(
        session_id="session-empty",
        agent_id=test_agent.id,
        user_id="test-user",
        runs=[],
    )


@pytest.fixture
def session_no_user_messages(test_agent: Agent):
    """Session with only assistant messages."""
    run = RunOutput(
        run_id="run-1",
        agent_id=test_agent.id,
        user_id="test-user",
        status=RunStatus.completed,
        messages=[
            Message(role="assistant", content="Hello!"),
            Message(role="assistant", content="How can I help?"),
        ],
        created_at=int(time.time()),
    )
    return AgentSession(
        session_id="session-no-user",
        agent_id=test_agent.id,
        user_id="test-user",
        runs=[run],
    )


@pytest.fixture
def session_with_introduction(test_agent: Agent):
    """Session where assistant sends intro first, then user responds."""
    run = RunOutput(
        run_id="run-1",
        agent_id=test_agent.id,
        user_id="test-user",
        status=RunStatus.completed,
        messages=[
            Message(role="assistant", content="Hello! I'm your helpful assistant."),
            Message(role="user", content="What is the weather like?"),
            Message(role="assistant", content="It's sunny today."),
        ],
        created_at=int(time.time()),
    )
    return AgentSession(
        session_id="session-with-intro",
        agent_id=test_agent.id,
        user_id="test-user",
        runs=[run],
    )


# -- Workflow session fixtures --


@pytest.fixture
def workflow_session_with_string_input():
    """Workflow session with string input."""
    run = WorkflowRunOutput(
        run_id="workflow-run-1",
        workflow_id="test-workflow",
        status=RunStatus.completed,
        input="Generate a blog post about AI",
        created_at=int(time.time()),
    )
    return WorkflowSession(
        session_id="workflow-session-string",
        workflow_id="test-workflow",
        user_id="test-user",
        runs=[run],
    )


@pytest.fixture
def workflow_session_with_dict_input():
    """Workflow session with dict input."""
    run = WorkflowRunOutput(
        run_id="workflow-run-1",
        workflow_id="test-workflow",
        status=RunStatus.completed,
        input={"topic": "AI", "style": "formal"},
        created_at=int(time.time()),
    )
    return WorkflowSession(
        session_id="workflow-session-dict",
        workflow_id="test-workflow",
        user_id="test-user",
        runs=[run],
    )


@pytest.fixture
def workflow_session_empty_runs():
    """Workflow session with no runs."""
    return WorkflowSession(
        session_id="workflow-session-empty",
        workflow_id="test-workflow",
        user_id="test-user",
        runs=[],
    )


@pytest.fixture
def workflow_session_no_input():
    """Workflow session with run but no input."""
    run = WorkflowRunOutput(
        run_id="workflow-run-1",
        workflow_id="test-workflow",
        status=RunStatus.completed,
        created_at=int(time.time()),
    )
    return WorkflowSession(
        session_id="workflow-session-no-input",
        workflow_id="test-workflow",
        user_id="test-user",
        workflow_data={"name": "BlogGenerator"},
        runs=[run],
    )
