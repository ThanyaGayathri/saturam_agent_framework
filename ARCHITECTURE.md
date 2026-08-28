# EGPT Architecture

A complete overview of the EGPT framework architecture, layers, components, and data flow.

---

## High-Level Overview

EGPT is a five-layer architecture with dependencies pointing **inward** toward stable core interfaces. Integrations are self-contained and reusable. Runtime resolution of concrete adapters goes through the Registry.

```
Clients / Interfaces
        │
        ▼
EgptOS (Control Plane / Runtime)
        │
        ▼
Orchestration (Team / Workflow)
        │
        ▼
Agent (Core Primitive)
        │
        ▼
Services + Core Interfaces
        │
        ▼
Integrations (Pluggable Adapters)
```

---

## Layer Diagram

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                         CLIENTS / INTERFACES                                  │
│                                                                              │
│   Browser (Swagger UI)    CLI (egpt connect)    Slack / Telegram / WhatsApp  │
│   AG-UI (frontend SDK)    A2A (Agent-to-Agent)  MCP (Model Context Protocol) │
└──────────────────────────────┬───────────────────────────────────────────────┘
                               │ HTTP / WebSocket / SSE
┌──────────────────────────────▼───────────────────────────────────────────────┐
│                      EgptOS — CONTROL PLANE / RUNTIME                         │
│                                                                              │
│  FastAPI Application (localhost:7777)                                         │
│  ┌────────────┐ ┌──────────┐ ┌───────────┐ ┌──────────┐ ┌───────────────┐  │
│  │  Routers   │ │   Auth   │ │ Scheduler │ │ Tracing  │ │  MCP Server   │  │
│  │ /agents    │ │ JWT/RBAC │ │ Cron Jobs │ │ OTel     │ │ Tool exposure │  │
│  │ /teams     │ │ Tenancy  │ │           │ │          │ │               │  │
│  │ /workflows │ │ Scopes   │ └───────────┘ └──────────┘ └───────────────┘  │
│  │ /knowledge │ └──────────┘                                                │
│  │ /memory    │ ┌───────────────────────────────────────────────────────┐   │
│  │ /sessions  │ │            REGISTRY (Component Catalog)               │   │
│  │ /traces    │ │  Schema validation, hydration, capability policy      │   │
│  │ /approvals │ │  Resolves models/tools/DBs by name at runtime         │   │
│  │ /schedules │ │  Entry-point discovery for external plugins           │   │
│  └────────────┘ └───────────────────────────────────────────────────────┘   │
└──────────────────────────────┬───────────────────────────────────────────────┘
                               │
┌──────────────────────────────▼───────────────────────────────────────────────┐
│                         ORCHESTRATION LAYER                                   │
│                                                                              │
│  ┌─────────────────────────────┐  ┌──────────────────────────────────────┐  │
│  │           TEAM              │  │            WORKFLOW                   │  │
│  │                             │  │                                      │  │
│  │  Modes:                     │  │  Steps: Agent / Team / Function      │  │
│  │  - coordinate (supervisor)  │  │  Parallel execution                  │  │
│  │  - route (dynamic routing)  │  │  Conditional branching               │  │
│  │  - broadcast (fan-out)      │  │  Loop iteration                      │  │
│  │  - tasks (decomposition)    │  │  Dynamic routing                     │  │
│  │                             │  │  CEL expressions                     │  │
│  │  Members: Agents or Teams   │  │  Background runs                     │  │
│  └─────────────────────────────┘  └──────────────────────────────────────┘  │
└──────────────────────────────┬───────────────────────────────────────────────┘
                               │
┌──────────────────────────────▼───────────────────────────────────────────────┐
│                       AGENT (Core Primitive)                                  │
│                                                                              │
│  ┌──────────┐ ┌──────────┐ ┌───────────┐ ┌──────────┐ ┌─────────────────┐  │
│  │  Model   │ │  Tools   │ │ Knowledge │ │  Memory  │ │   Reasoning     │  │
│  │ (LLM)   │ │ (Actions)│ │ (RAG)     │ │ (Long-   │ │ (Chain-of-      │  │
│  │          │ │          │ │           │ │  term)   │ │  thought)       │  │
│  └──────────┘ └──────────┘ └───────────┘ └──────────┘ └─────────────────┘  │
│  ┌──────────┐ ┌──────────┐ ┌───────────┐ ┌──────────┐ ┌─────────────────┐  │
│  │ Sessions │ │  Hooks   │ │Guardrails │ │ Learning │ │ Structured Out  │  │
│  │ (State)  │ │ (Pre/    │ │ (Safety)  │ │ (Self-   │ │ (Pydantic)      │  │
│  │          │ │  Post)   │ │           │ │  improve)│ │                 │  │
│  └──────────┘ └──────────┘ └───────────┘ └──────────┘ └─────────────────┘  │
└──────────────────────────────┬───────────────────────────────────────────────┘
                               │
┌──────────────────────────────▼───────────────────────────────────────────────┐
│                        SERVICES LAYER                                         │
│                                                                              │
│  Eval  |  Memory Manager  |  Learning  |  Compression  |  Factory            │
│  Scheduler  |  Skills  |  Culture  |  Guardrails  |  Approval                │
└──────────────────────────────┬───────────────────────────────────────────────┘
                               │
┌──────────────────────────────▼───────────────────────────────────────────────┐
│                      CORE INTERFACES                                          │
│                                                                              │
│  Model (base)      |  BaseDb / AsyncBaseDb   |  VectorDb (base)              │
│  Function/Toolkit  |  Knowledge (protocol)   |  Embedder (base)              │
│  ContextProvider   |  Run / Session types     |  Registry protocols           │
└──────────────────────────────┬───────────────────────────────────────────────┘
                               │ (dependencies point inward only)
┌──────────────────────────────▼───────────────────────────────────────────────┐
│                       INTEGRATIONS LAYER                                      │
│                                                                              │
│  ┌─────────────────┐  ┌────────────────┐  ┌─────────────────────────────┐   │
│  │  MODEL PROVIDERS │  │  DB BACKENDS   │  │     VECTOR DB BACKENDS      │   │
│  │  (46 providers)  │  │  (14 backends) │  │     (18 backends)           │   │
│  │                  │  │                │  │                             │   │
│  │  OpenAI          │  │  PostgreSQL    │  │  PGVector    Pinecone       │   │
│  │  Anthropic       │  │  SQLite        │  │  ChromaDB    Qdrant         │   │
│  │  Google/Gemini   │  │  MongoDB       │  │  LanceDB     Weaviate       │   │
│  │  AWS Bedrock     │  │  DynamoDB      │  │  Milvus      Redis          │   │
│  │  Ollama          │  │  Redis         │  │  SingleStore MongoDB        │   │
│  │  DeepSeek        │  │  ClickHouse    │  │                             │   │
│  │  Mistral, etc.   │  │  Firestore     │  │                             │   │
│  └─────────────────┘  └────────────────┘  └─────────────────────────────┘   │
│                                                                              │
│  ┌──────────────────────────────┐  ┌─────────────────────────────────────┐  │
│  │     TOOLS / TOOLKITS (120+)  │  │       CONTEXT PROVIDERS             │  │
│  │                              │  │                                     │  │
│  │  YFinance, GitHub, Slack     │  │  Google Calendar, Gmail, GDrive     │  │
│  │  SQL, Shell, Docker          │  │  Slack, MCP, Wiki (Git/Notion)      │  │
│  │  Web search (Brave, Tavily)  │  │  Database, Web                      │  │
│  │  Email, Jira, Linear         │  │                                     │  │
│  │  File, Python, Coding        │  └─────────────────────────────────────┘  │
│  │  MCP (dynamic tool import)   │                                           │
│  └──────────────────────────────┘  ┌─────────────────────────────────────┐  │
│                                    │  KNOWLEDGE (Readers/Embedders)       │  │
│  ┌──────────────────────────────┐  │  PDF, URL, S3, GCS, YouTube         │  │
│  │   CHANNEL INTERFACES         │  │  OpenAI, Gemini, HF embedders      │  │
│  │   Discord, Slack, Telegram   │  │  Rerankers (Cohere, etc.)           │  │
│  │   WhatsApp, A2A, AG-UI      │  └─────────────────────────────────────┘  │
│  └──────────────────────────────┘                                           │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## Layer Responsibilities

| Layer | Responsibility |
|-------|----------------|
| **Core** | Stable interfaces, domain types, generic utilities, and the Registry resolution seam. No concrete external-system adapters. |
| **Orchestration** | Agent / Team / Workflow runtime: planning, hooks, reasoning, run context wiring. Depends on core and services. |
| **Services** | Supporting capabilities (eval, learn, memory, culture, skills, scheduler, compression, factory, guardrails). Depends on core only. |
| **Integrations** | Concrete external-system adapters: model providers, DB backends, toolkits, knowledge readers/embedders, tracing exporters, channel integrations. Depends on core only. |
| **API / Control-plane** | HTTP/OS surfaces (EgptOS, routers, middleware, auth). May depend on orchestration, services, and core. Imports integrations only at startup for registration. |

---

## Data Flow

```
User sends message
       │
       ▼
EgptOS receives HTTP request at POST /agents/{id}/runs
       │
       ▼
Registry resolves the agent by ID
       │
       ▼
Agent.run() executes:
  1. Build system message (instructions + context + knowledge)
  2. Add history from session storage
  3. Call Model provider (Gemini / OpenAI / Claude / etc.)
  4. Model decides to use tools -> execute tools -> loop back
  5. Model produces final response
  6. Run hooks (post-hooks, guardrails, evals)
  7. Save session to DB
  8. Stream or return response
       │
       ▼
Response sent back via SSE stream or JSON
```

---

## Code Layout

| What | Where |
|------|-------|
| Core agent runtime | `libs/agno/egpt/agent/` |
| Teams | `libs/agno/egpt/team/` |
| Workflows | `libs/agno/egpt/workflow/` |
| Tool interface | `libs/agno/egpt/tools/` (Function, Toolkit, @tool, registry) |
| Toolkits + MCP | `libs/agno/egpt/integrations/tools/` |
| Model interface | `libs/agno/egpt/models/` (Model base, message, metrics) |
| Model providers | `libs/agno/egpt/integrations/models/` |
| Knowledge/RAG | `libs/agno/egpt/knowledge/` (pipeline, Reader/Embedder interfaces) |
| Knowledge integrations | `libs/agno/egpt/integrations/knowledge/` (readers, embedders, rerankers) |
| Memory | `libs/agno/egpt/memory/` |
| Learning | `libs/agno/egpt/learn/` |
| DB interface | `libs/agno/egpt/db/` (BaseDb/AsyncBaseDb, schemas, migrations) |
| DB backends | `libs/agno/egpt/integrations/db/` |
| Vector DB interface | `libs/agno/egpt/vectordb/` (VectorDb + distance/score/search) |
| Vector DB backends | `libs/agno/egpt/integrations/vectordb/` |
| Context providers | `libs/agno/egpt/integrations/context/` |
| EgptOS (runtime) | `libs/agno/egpt/os/` |
| Registry | `libs/agno/egpt/registry/` |
| CLI (egptctl) | `libs/egptctl/` |
| Examples | `cookbook/` |
| Tests | `libs/agno/tests/` |

---

## Core Primitives

### Agent

The atomic unit. An Agent wraps:

- **Model** - any LLM provider (Gemini, OpenAI, Claude, Ollama, etc.)
- **Tools** - actions the agent can take (API calls, code execution, search)
- **Knowledge** - RAG pipeline for document search at runtime
- **Memory** - long-term recall across sessions
- **Reasoning** - chain-of-thought with tool use
- **Sessions** - persistent conversation state
- **Hooks** - pre/post execution logic
- **Guardrails** - input validation and safety checks
- **Learning** - self-improvement from interactions
- **Structured Output** - typed Pydantic responses

### Team

Coordinates multiple agents:

| Mode | Description |
|------|-------------|
| `coordinate` | Supervisor agent delegates tasks to members |
| `route` | Dynamically routes messages to the right agent |
| `broadcast` | Sends to all members in parallel |
| `tasks` | Autonomous task decomposition and execution |

### Workflow

Programmatic orchestration of agents/teams/functions:

- Sequential steps with explicit ordering
- Parallel execution of independent steps
- Conditional branching based on results
- Loop iteration for repeated work
- Dynamic routing between paths
- Background execution with WebSocket updates

---

## Registry

The Registry is the central component catalog:

- Stores models, tools, DBs, vector DBs, agents, teams, knowledge
- Resolves components by name at runtime (lazy imports)
- Supports DB-defined agent hydration (define agents in config, build at request time)
- Entry-point discovery for external plugins (no repo fork needed)
- Capability policy to restrict what DB-authored definitions may reference

```python
# External plugin registration (pyproject.toml)
[project.entry-points."egpt.models"]
acme = "acme_egpt.model:AcmeModel"

# Runtime resolution
from egpt.registry.discovery import construct_model
model = construct_model("acme", id="acme-1")
```

---

## EgptOS (Control Plane)

Serves agents as a production API with:

- **50+ REST endpoints** - agent/team/workflow CRUD and execution
- **SSE streaming** - real-time response streaming
- **WebSocket** - workflow event streams
- **JWT auth + RBAC** - multi-user, multi-tenant isolation
- **Scheduler** - cron-based background jobs
- **Tracing** - OpenTelemetry integration
- **MCP server** - expose tools via Model Context Protocol
- **Channel interfaces** - Slack, Telegram, WhatsApp, Discord, A2A, AG-UI

---

## Integration Counts

| Category | Count |
|----------|-------|
| Model providers | 46 |
| DB backends | 14 |
| Vector DB backends | 18 |
| Tool integrations | 120+ |
| Context providers | 9 |
| Channel interfaces | 6 |
| API endpoints | 50+ |

---

## Dependency Rules

1. **Core** imports nothing outside itself
2. **Integrations** depend on core interfaces only (never orchestration or services)
3. **Services** depend on core interfaces only (use Registry to resolve integrations)
4. **Orchestration** depends on core and services (never integrations directly)
5. **API/Control-plane** imports integrations only at startup for registration

These rules are enforced by `import-linter` in CI (see `docs/adr/0001-layered-architecture.md`).

---

## Quick Start

```python
from egpt.agent import Agent
from egpt.integrations.models.google import Gemini
from egpt.integrations.tools.yfinance import YFinanceTools

agent = Agent(
    model=Gemini(id="gemini-3.5-flash"),
    tools=[YFinanceTools(all=True)],
    instructions="You are a finance analyst",
)

agent.print_response("What is AAPL trading at?", stream=True)
```

Serve as a production API:

```python
from egpt.os import EgptOS

os_app = EgptOS(agents=[agent])
os_app.serve()  # http://localhost:7777/docs
```
