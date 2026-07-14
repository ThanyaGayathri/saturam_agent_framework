"""
AgentOS Registry Demo
=====================

Demonstrates using Registry with AgentOS for tools, functions, schemas,
models, and vector database components.
"""

from egpt.db.postgres import PostgresDb
from egpt.models.anthropic import Claude
from egpt.models.google.gemini import Gemini
from egpt.models.openai import OpenAIChat
from egpt.os import AgentOS
from egpt.registry import Registry
from egpt.tools.calculator import CalculatorTools
from egpt.tools.parallel import ParallelTools
from egpt.tools.youtube import YouTubeTools
from egpt.vectordb.pgvector import PgVector
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
db = PostgresDb(db_url="postgresql+psycopg://ai:ai@localhost:5532/ai", id="postgres_db")
pgvector = PgVector(
    db_url="postgresql+psycopg://ai:ai@localhost:5532/ai", table_name="custom_table"
)


def custom_function(input: str) -> str:
    return input + "Hello, world!"


class CustomInputSchema(BaseModel):
    input: str
    description: str


class CustomOutputSchema(BaseModel):
    output: str
    description: str


def custom_tool(input: str) -> str:
    return input + "Hello, world!"


# ---------------------------------------------------------------------------
# Create Registry
# ---------------------------------------------------------------------------
registry = Registry(
    name="Agno Registry",
    tools=[ParallelTools(), CalculatorTools(), YouTubeTools(), custom_tool],
    functions=[custom_function],
    schemas=[CustomInputSchema, CustomOutputSchema],
    models=[
        OpenAIChat(id="gpt-5-mini"),
        OpenAIChat(id="gpt-5"),
        Claude(id="claude-sonnet-4-5"),
        Gemini(id="gemini-3.5-flash"),
    ],
    dbs=[db],
    vector_dbs=[pgvector],
)

# ---------------------------------------------------------------------------
# Create AgentOS App
# ---------------------------------------------------------------------------
agent_os = AgentOS(
    id="demo-agent-os",
    registry=registry,
    db=db,
)

app = agent_os.get_app()

# ---------------------------------------------------------------------------
# Run AgentOS App
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    agent_os.serve(app="demo:app", reload=True)
