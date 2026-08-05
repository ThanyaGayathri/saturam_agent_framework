# PostgreSQL Integration

Examples demonstrating PostgreSQL database integration with Agno agents, teams, and workflows.

## Setup

```shell
uv pip install psycopg2-binary
```

## Configuration

```python
from egpt.agent import Agent
from egpt.db.postgres import PostgresDb

db = PostgresDb(db_url="postgresql+psycopg://username:password@localhost:5432/database")

agent = Agent(
    db=db,
    add_history_to_context=True,
)
```

## Async usage

Agno also supports using your PostgreSQL database asynchronously, via the `AsyncPostgresDb` class:

```python
from egpt.agent import Agent
from egpt.db.postgres import AsyncPostgresDb

db = AsyncPostgresDb(db_url="postgresql+psycopg://username:password@localhost:5432/database")

agent = Agent(
    db=db,
    add_history_to_context=True,
)
```

## Examples

- [`postgres_for_agent.py`](postgres_for_agent.py) - Agent with PostgreSQL storage
- [`postgres_for_team.py`](postgres_for_team.py) - Team with PostgreSQL storage
- [`postgres_for_workflow.py`](postgres_for_workflow.py) - Workflow with PostgreSQL storage
