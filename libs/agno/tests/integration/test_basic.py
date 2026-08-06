import pytest

from egpt.agent.agent import Agent
from egpt.integrations.models.openai import OpenAIChat
from egpt.integrations.vectordb.chroma import ChromaDb
from egpt.knowledge.knowledge import Knowledge
from egpt.run.agent import RunOutput
from egpt.team.team import Team


@pytest.fixture
def vector_db():
    """Setup a temporary vector DB for testing."""
    vector_db = ChromaDb(collection="vectors", path="tmp/chromadb", persistent_client=True)
    yield vector_db
    # Clean up after test
    vector_db.drop()


def test_basic_with_no_import_errors(shared_db, vector_db):
    knowledge = Knowledge(
        vector_db=vector_db,
        contents_db=shared_db,
    )
    agent = Agent(model=OpenAIChat(id="gpt-4o-mini"), knowledge=knowledge, db=shared_db, markdown=True, telemetry=False)
    team = Team(members=[agent], model=OpenAIChat(id="gpt-4o-mini"), db=shared_db, markdown=True, telemetry=False)

    # Simple test to ensure that we can run agents/team without any import errors
    response: RunOutput = team.run("Share a 2 sentence horror story")

    assert response.content is not None
