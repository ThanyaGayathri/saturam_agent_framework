"""
Example EgptOS app with a custom FastAPI app with conflicting routes.

This example demonstrates the `on_route_conflict="preserve_base_app"` functionality which allows your
custom routes to take precedence over conflicting EgptOS routes.

When `on_route_conflict="preserve_base_app"`:
- Your custom routes (/, /health) will be preserved
- Conflicting EgptOS routes will be skipped
- Non-conflicting EgptOS routes will still be added

When `on_route_conflict="preserve_agentos"` (default):
- EgptOS routes will override your custom routes
- Warnings will be logged about the conflicts
"""

from egpt.agent import Agent
from egpt.integrations.db.postgres import PostgresDb
from egpt.integrations.tools.websearch import WebSearchTools
from egpt.models.anthropic import Claude
from egpt.os import EgptOS
from fastapi import FastAPI

# ---------------------------------------------------------------------------
# Create Example
# ---------------------------------------------------------------------------

# Setup the database
db = PostgresDb(db_url="postgresql+psycopg://ai:ai@localhost:5532/ai")

web_research_agent = Agent(
    id="web-research-agent",
    name="Web Research Agent",
    model=Claude(id="claude-sonnet-4-5"),
    db=db,
    tools=[WebSearchTools()],
    add_history_to_context=True,
    num_history_runs=3,
    add_datetime_to_context=True,
    markdown=True,
)

# Custom FastAPI app
app: FastAPI = FastAPI(
    title="Custom FastAPI App",
    version="1.0.0",
)


# Custom landing page (conflicts with EgptOS home route)
@app.get("/")
async def get_custom_home():
    return {
        "message": "Custom FastAPI App",
        "note": 'Using on_route_conflict="preserve_base_app" to preserve custom routes',
    }


# Custom health endpoint (conflicts with EgptOS health route)
@app.get("/health")
async def get_custom_health():
    return {"status": "custom_ok", "note": "This is your custom health endpoint"}


# Setup our EgptOS app by passing your FastAPI app in the app_config parameter
# Use on_route_conflict="preserve_base_app" to preserve your custom routes over EgptOS routes
agent_os = EgptOS(
    description="Example app with route replacement",
    agents=[web_research_agent],
    base_app=app,
    on_route_conflict="preserve_base_app",  # Skip conflicting EgptOS routes, keep your custom routes
)

app = agent_os.get_app()

# ---------------------------------------------------------------------------
# Run Example
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    """Run your EgptOS.

    With on_route_conflict="preserve_base_app":
    - Your custom routes are preserved: http://localhost:7777/ and http://localhost:7777/health
    - EgptOS routes are available at other paths: http://localhost:7777/sessions, etc.
    - Conflicting EgptOS routes (GET / and GET /health) are skipped
    - API docs: http://localhost:7777/docs

    Try changing on_route_conflict="preserve_agentos" to see EgptOS routes override your custom ones. This is the default behavior.
    """
    agent_os.serve(app="override_routes:app", reload=True)
