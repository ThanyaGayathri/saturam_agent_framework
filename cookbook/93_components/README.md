# Agent-as-Config: Persisting Agents, Teams, and Workflows

This cookbook demonstrates how to save and load Agents, Teams, and Workflows to/from a database, enabling configuration-as-code patterns where your AI components can be versioned, shared, and restored.

## Overview

The Agent-as-Config feature allows you to:
- **Save** agents, teams, and workflows to PostgreSQL or SQLite
- **Load** them back with full functionality restored
- **Version** your configurations (each save creates a new version)
- **Delete** configurations (soft or hard delete)
- Use a **Registry** to handle non-serializable components (tools, custom functions, schemas)

## Prerequisites

1. PostgreSQL database running (or SQLite for development)
2. Database URL configured

```bash
# Start PostgreSQL with Docker
./cookbook/scripts/run_pgvector.sh
```

## Cookbooks

| File | Description |
|------|-------------|
| `save_agent.py` | Save an agent configuration to the database |
| `get_agent.py` | Load an agent from the database and run it |
| `save_team.py` | Save a team with member agents to the database |
| `get_team.py` | Load a team and run it with delegation |
| `save_workflow.py` | Save a multi-step workflow to the database |
| `get_workflow.py` | Load a workflow and execute its steps |
| `registry.py` | Use a registry for non-serializable components |
| `auto_populate_registry.py` | Inspect how EgptOS auto-discovers components from teams and workflows |
| `auto_populate_registry_os.py` | Serve an EgptOS and see the auto-discovered components over the API |
| `validate_config.py` | Validate an agent definition and read the field-level errors it produces |
| `capability_policy.py` | Restrict which models, tools and MCP servers a tenant's definition may reference |
| `capability_policy.json` | The policy document `capability_policy.py` loads |
| `tenant_scoped_hydration.py` | Hydrate DB-defined agents scoped to a tenant, with the config cache |
| `lifecycle_audit.py` | Walk a definition through draft, publish and rollback, then read its audit trail |
| `seed_and_export.py` | Export a definition to a portable bundle and import it into another tenant |

---

## Agents

### Saving an Agent

```python
from egpt.agent import Agent
from egpt.integrations.db.postgres import PostgresDb
from egpt.models.openai import OpenAIChat

db = PostgresDb(db_url="postgresql+psycopg://ai:ai@localhost:5532/ai")

agent = Agent(
    id="my-agent",
    name="My Agent",
    model=OpenAIChat(id="gpt-4o-mini"),
    db=db,
)

# Save to database - returns version number
version = agent.save()
print(f"Saved agent as version {version}")
```

### Loading an Agent

```python
from egpt.agent import get_agent_by_id
from egpt.integrations.db.postgres import PostgresDb

db = PostgresDb(db_url="postgresql+psycopg://ai:ai@localhost:5532/ai")

# Load agent by ID
agent = get_agent_by_id(db=db, id="my-agent")

# Run the agent
agent.print_response("Hello!")
```

### Listing All Agents

```python
from egpt.agent import get_agents

agents = get_agents(db=db)
for agent in agents:
    print(f"Agent: {agent.name} (ID: {agent.id})")
```

### Deleting an Agent

```python
# Soft delete (marks as deleted but keeps in database)
agent.delete()

# Hard delete (permanently removes from database)
agent.delete(hard_delete=True)
```

---

## Teams

Teams automatically save their member agents as linked components.

### Saving a Team

```python
from egpt.agent import Agent
from egpt.team import Team
from egpt.integrations.db.postgres import PostgresDb
from egpt.models.openai import OpenAIChat

db = PostgresDb(db_url="postgresql+psycopg://ai:ai@localhost:5532/ai")

# Define member agents
researcher = Agent(
    id="researcher-agent",
    name="Researcher",
    model=OpenAIChat(id="gpt-4o-mini"),
    role="Research and gather information",
)

writer = Agent(
    id="writer-agent",
    name="Writer",
    model=OpenAIChat(id="gpt-4o-mini"),
    role="Write content based on research",
)

# Create and save the team
team = Team(
    id="content-team",
    name="Content Creation Team",
    model=OpenAIChat(id="gpt-4o-mini"),
    members=[researcher, writer],
    description="A team that researches and creates content",
    db=db,
)

version = team.save()
print(f"Saved team as version {version}")
```

### Loading a Team

```python
from egpt.team import get_team_by_id

team = get_team_by_id(db=db, id="content-team")

# Run the team - it will delegate to members
team.print_response("Write about AI trends", stream=True)
```

### Listing All Teams

```python
from egpt.team import get_teams

teams = get_teams(db=db)
for team in teams:
    print(f"Team: {team.name} (ID: {team.id})")
```

---

## Workflows

Workflows save their steps with links to the agents/teams that execute them.

### Saving a Workflow

```python
from egpt.agent import Agent
from egpt.workflow import Workflow, Step
from egpt.integrations.db.postgres import PostgresDb
from egpt.models.openai import OpenAIChat

db = PostgresDb(db_url="postgresql+psycopg://ai:ai@localhost:5532/ai")

# Define agents for each step
research_agent = Agent(
    id="research-agent",
    name="Research Agent",
    model=OpenAIChat(id="gpt-4o-mini"),
    role="Extract key insights from data",
)

content_agent = Agent(
    id="content-agent",
    name="Content Agent",
    model=OpenAIChat(id="gpt-4o-mini"),
    role="Create content based on research",
)

# Define workflow steps
research_step = Step(name="Research Step", agent=research_agent)
content_step = Step(name="Content Step", agent=content_agent)

# Create and save the workflow
workflow = Workflow(
    id="content-workflow",
    name="Content Creation Workflow",
    description="Research and create content",
    db=db,
    steps=[research_step, content_step],
)

version = workflow.save()
print(f"Saved workflow as version {version}")
```

### Loading a Workflow

```python
from egpt.workflow import get_workflow_by_id

workflow = get_workflow_by_id(db=db, id="content-workflow")

# Run the workflow
workflow.print_response(input="AI trends in 2024", markdown=True)
```

### Listing All Workflows

```python
from egpt.workflow import get_workflows

workflows = get_workflows(db=db)
for workflow in workflows:
    print(f"Workflow: {workflow.name} (ID: {workflow.id})")
```

---

## Registry for Non-Serializable Components

Some components cannot be serialized to JSON (tools, custom functions, Pydantic schemas). Use a `Registry` to provide these when loading.

### Creating a Registry

```python
from egpt.registry import Registry
from egpt.integrations.tools.duckduckgo import DuckDuckGoTools
from egpt.models.openai import OpenAIChat
from pydantic import BaseModel

# Custom tool function
def my_custom_tool(query: str) -> str:
    return f"Results for: {query}"

# Custom schemas
class InputSchema(BaseModel):
    message: str

class OutputSchema(BaseModel):
    result: str
    confidence: float

# Create registry with all non-serializable components
registry = Registry(
    name="My Registry",
    tools=[DuckDuckGoTools(), my_custom_tool],
    models=[OpenAIChat(id="gpt-4o-mini")],
    schemas=[InputSchema, OutputSchema],
)
```

### Loading with a Registry

```python
from egpt.agent import get_agent_by_id

# When loading an agent that uses tools or schemas,
# pass the registry to restore non-serializable components
agent = get_agent_by_id(
    db=db,
    id="my-agent",
    registry=registry,
)

# The agent now has its tools and schemas restored
agent.print_response("Search for AI news")
```

### What Gets Restored from Registry

| Component | Serialized | Restored via Registry |
|-----------|------------|----------------------|
| Agent ID, name, description | Yes | - |
| Model configuration | Yes | - |
| Instructions, prompts | Yes | - |
| Tools (Toolkit, Function) | Name only | Full callable |
| Custom functions | Name only | Full callable |
| Pydantic schemas | Name only | Full class |

---

## Auto-Populating the Registry

You do not have to declare components twice. When you construct an `EgptOS`, it
recursively walks every agent, team, and workflow you pass in and adds the
**models**, **tools**, **databases**, and **vector databases** they reference to
the registry automatically. This keeps `GET /registry` consistent with what is
actually wired into your OS.

```python
from egpt.agent import Agent
from egpt.integrations.db.sqlite import SqliteDb
from egpt.models.openai import OpenAIResponses
from egpt.os import EgptOS
from egpt.team import Team

db = SqliteDb(db_file="tmp/auto_registry.db", id="auto-registry-db")

researcher = Agent(id="researcher", model=OpenAIResponses(id="gpt-5.4"), db=db)
writer = Agent(id="writer", model=OpenAIResponses(id="gpt-5.4-mini"))
team = Team(id="content-team", members=[researcher, writer])

# No registry passed; components are discovered from the team members
agent_os = EgptOS(teams=[team])

print([f"{m.provider}:{m.id}" for m in agent_os.registry.models])
# -> ['OpenAI:gpt-5.4', 'OpenAI:gpt-5.4-mini']
```

Details:

- The walk covers nested teams and every workflow step type (including
  `Condition` else-branches and `Router` choices).
- Models include `reasoning_model`, `parser_model`, `output_model`, and fallback
  models. Vector databases and contents databases are pulled from knowledge.
- Deduplication is by id/name, so a model shared across many agents is collected
  once, and components you pass to a `Registry` explicitly are preserved (the
  discovered ones are merged in, never duplicated).
- User objects are only referenced, never mutated.

See `auto_populate_registry.py` (offline inspection) and
`auto_populate_registry_os.py` (served app).

---

## Database Configuration

### PostgreSQL (Recommended for Production)

```python
from egpt.integrations.db.postgres import PostgresDb

db = PostgresDb(db_url="postgresql+psycopg://user:pass@host:port/dbname")
```

### SQLite (Development Only)

```python
from egpt.integrations.db.sqlite import SqliteDb

db = SqliteDb(db_url="sqlite:///agents.db")
```

---

## Versioning

Each `save()` call creates a new version of the configuration:

```python
# First save - version 1
agent.save()

# Modify and save again - version 2
agent.instructions = ["Updated instructions"]
agent.save()

# Load specific version (coming soon)
# agent = get_agent_by_id(db=db, id="my-agent", version=1)
```

---

## Tenant-Scoped Hydration

In a multi-tenant deployment every hydration entry point takes a `tenant_id` (EGPT-2.5). The
read is scoped to that tenant -- a foreign tenant's component id resolves like a missing one --
and the tenant is carried into team-member and workflow-step hydration. A
`ComponentConfigCache` parked on the `Registry` avoids a database read per hydration; the
components HTTP API evicts it on every mutation, and programmatic writers converge within the
TTL (default 30 seconds).

```python
from egpt.registry import Registry
from egpt.registry.hydrate import ComponentConfigCache

registry = Registry(component_config_cache=ComponentConfigCache(ttl_seconds=30))
agent = get_agent_by_id(db=db, id="my-agent", registry=registry, tenant_id="acme")
```

See `tenant_scoped_hydration.py` for the full example, and `docs/tenancy.md` for the
normative reference (including `HYDRATE_PUBLISHED_ONLY`, which makes hydration refuse
unpublished configs).

---

## Lifecycle and Audit Trail

A config version is either a `draft` (freely editable) or `published` (immutable, and pointed at
by the component's `current_version`). Publishing is `upsert_config(stage="published")`;
rollback is `set_current_version`, which repoints `current_version` at an already-published
version without deleting anything.

Every mutation appends a row to `egpt_component_audit` recording who did it, to which version,
and what changed (EGPT-2.6). The write happens inside the mutation's own transaction, so a
refused change leaves no trace and a successful one cannot commit without a record. Pass `actor`
to attribute it; the HTTP API fills it from the authenticated principal automatically.

```python
db.upsert_config("my-agent", version=1, stage="published", tenant_id="acme", actor="alice")
db.set_current_version("my-agent", version=1, tenant_id="acme", actor="carol", audit_note="regression")

rows, total = db.list_component_audit("my-agent", tenant_id="acme")
```

Over HTTP: `GET /components/{id}/audit` (scope `components:read`), paginated and newest first.

See `lifecycle_audit.py` for the full walk, and `docs/component-lifecycle.md` for the normative
reference.

---

## Portable Definitions

A definition travels as a **bundle**: one JSON document holding a component, everything it
depends on, and the links between them (EGPT-2.8). A team whose members are missing does not
hydrate at all, so the dependency closure travels with it.

```python
from egpt.components.io import import_component
from egpt.registry.io import export_component

document = export_component(db, "support-team", tenant_id="acme")
import_component(db, document, tenant_id="globex", registry=registry, id_prefix="globex-")
```

**Secrets never travel.** `PostgresDb.to_dict()` emits `db_url` and `Agent.to_dict()` embeds it,
so export strips connection fields and lists what it removed in `document["redacted"]`. What
survives -- `id`, `type`, `db_schema`, table names -- is what lets the config resolve against a
database registered in the target. Use `db_id=` to point it at a different one.

**Ids are global.** `component_id` is the primary key, not a per-tenant one, so the same corpus
cannot exist twice without renaming. `id_prefix` rewrites the ids, the links, and the member and
step references that hydration reads.

Seeding a tenant from templates is the same operation -- a template file *is* a bundle:

```bash
./scripts/seed_agents.py --db-url postgresql+psycopg://ai:ai@localhost:5532/ai --tenant-id acme
```

Re-running is safe. An unchanged definition writes nothing at all, a changed one appends a
version, and a published version is never rewritten.

Over HTTP: `GET /components/{id}/export` (`components:read`) and `POST /components/import`
(`components:write`). The whole bundle is validated before anything is written, so a bundle that
fails validation leaves no rows behind.

See `seed_and_export.py` for the full round trip, and `docs/component-portability.md` for the
normative reference.

---

## Running the Examples

```bash
# Start PostgreSQL
./cookbook/scripts/run_pgvector.sh

# Run save examples first
python cookbook/93_components/save_agent.py
python cookbook/93_components/save_team.py
python cookbook/93_components/save_workflow.py

# Then run get examples
python cookbook/93_components/get_agent.py
python cookbook/93_components/get_team.py
python cookbook/93_components/get_workflow.py

# Registry example
python cookbook/93_components/registry.py

# Config validation example (offline, no database needed)
python cookbook/93_components/validate_config.py
```

---

## Config Validation

Every config written through the components API (`POST /components`,
`POST /components/{id}/configs`, `PATCH /components/{id}/configs/{version}`) is validated
before it is persisted. An agent definition must name a resolvable model, may only use fields
the agent understands, and has to hydrate — so an unrunnable definition is rejected at
authoring time instead of failing in the middle of someone's run.

An invalid definition comes back as a 422 listing every problem at once:

```json
{
  "detail": "Invalid agent configuration: 2 errors.",
  "error_code": "CONFIG_VALIDATION_ERROR",
  "schema_version": 1,
  "errors": [
    {"path": "config.model", "message": "Field 'model' is required for an agent definition.",
     "code": "missing_required_field"},
    {"path": "config.instruction", "message": "Unknown field 'instruction'. Did you mean 'instructions'?",
     "code": "unknown_field"}
  ]
}
```

Configs also carry a `schema_version`. `to_dict()` stamps it, so `save()` writes versioned
configs too, and `from_dict()` ignores it — an older config without the key still loads.

Run `validate_config.py` to see a valid definition and three rejected ones. The full field
tables and error-code vocabulary are in `docs/component-config-schema.md`.
