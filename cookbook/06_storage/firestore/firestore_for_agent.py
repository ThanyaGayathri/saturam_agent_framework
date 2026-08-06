"""
This recipe shows how to store agent sessions in a Firestore database.

Steps:
1. Ensure your gcloud project is enabled with Firestore. Reference https://cloud.google.com/firestore/docs/create-database-server-client-library ?
2. Run: `uv pip install openai google-cloud-firestore` to install dependencies
3. Make sure your gcloud project is set up and you have the necessary permissions to access Firestore
4. Run: `python cookbook/storage/firestore_storage.py` to run the agent
"""

from egpt.agent import Agent
from egpt.db.firestore import FirestoreDb
from egpt.tools.websearch import WebSearchTools

PROJECT_ID = "egpt-os-test"  # Use your project ID here

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
# The only required argument is the collection name.
# Firestore will connect automatically using your google cloud credentials.
# The class uses the (default) database by default to allow free tier access to firestore.
# You can specify a project_id if you'd like to connect to firestore in a different GCP project
db = FirestoreDb(project_id=PROJECT_ID)

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------
agent = Agent(
    db=db,
    tools=[WebSearchTools()],
    add_history_to_context=True,
)

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    agent.print_response("How many people live in Canada?")
    agent.print_response("What is their national anthem called?")
