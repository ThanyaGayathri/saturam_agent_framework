# Integrations Inventory

**Ticket:** EGPT-1.3 ([#44](https://github.com/Saturam-Inc/aicoe-tracker/issues/44)) &nbsp;|&nbsp; **Epic:** 1 -- Modular architecture &nbsp;|&nbsp; **Status:** Draft for review

**Governed by:** [ADR 0001 -- Layered architecture](adr/0001-layered-architecture.md). Where this inventory and the EGPT-1.3 ticket description disagree, the ADR wins and the difference is called out below.

**Drives:** EGPT-1.4 (models), 1.5 (vector DBs), 1.6 (DB backends), 1.7 (tools/MCP/context connectors), 1.8 (knowledge). Registry/discovery is EGPT-1.9 and out of scope here.

**Scanned:** `libs/agno/egpt` at commit `f6fc4d9`.

---

## 1. Purpose

One row per external touchpoint in the framework, each carrying the core interface it implements, its destination in the integrations layer, and the extraction ticket that owns the move. This is the checklist EGPT-1.4 through 1.8 execute against; anything not listed here is, by definition, not scheduled to move.

## 2. Method

1. Fresh tree scan of every package under `libs/agno/egpt` (commands in section 8).
2. Each module's third-party imports were extracted with an AST pass, so "touches an external system" is a measured property rather than a guess from the filename.
3. Targets were taken from ADR 0001's layer mapping. Where the ADR is silent, a target is proposed and marked.
4. Anything the ADR and the ticket disagree on, or that has no clean home, is raised in section 6 with a proposed disposition.

### Scope rule, and the areas deliberately excluded

**Rule applied:** a module is a touchpoint if it imports a library that speaks to a system outside this process -- a vendor SDK, a network API, a database driver, a message channel. Libraries that are pure local computation or framework plumbing (`pydantic`, `fastapi`, `rich`, `numpy`, `croniter`, `cryptography`, ...) do not make a module a touchpoint on their own.

Earlier drafts of this document scanned only the areas named in the ticket's mapping table, which meant a touchpoint in an unscanned package could not be found by construction -- four families were missed that way and added in review. The scope is now the **whole package**, and the modules that match the import test but are still excluded are recorded here rather than left implicit:

| Module | External import | Why excluded |
|---|---|---|
| `eval/utils.py` | `sqlalchemy` | Consumes the DB layer; is not itself a backend. Moves only if `db/` moves under it. |
| `os/routers/service_accounts/router.py` | `sqlalchemy` | Same: control-plane router over the DB layer. |
| `os/middleware/jwt.py` | `jwt` (PyJWT) | Token verification is local computation; no external system is contacted. |
| `workflow/cel.py` | `celpy` | CEL expression evaluator -- pure local computation, no external system. |
| `scheduler/` | `croniter`, `pytz` | Date/schedule arithmetic; no external system. |
| `client/`, `remote/`, `api/` | `fastapi`, `pydantic_settings` | Framework plumbing and config. |

If a reviewer disagrees with any exclusion, the fix is a row in section 4, not a change to the rule.

## 3. Reconciliation against the ticket's estimates

| Area | Ticket estimate | Measured | Reconciliation |
|---|---|---|---|
| Model providers | ~46 | **46** provider dirs (+ `models/utils.py`) | Matches. Ticket omits `models/utils.py`, which ADR prereq #2 requires to move. |
| Vector DB backends | ~18 | **18** | Matches. |
| DB backends | ~14 | **14** (+ `schemas/`, `migrations/`) | Matches. |
| Tools/toolkits | ~137 | **136** top-level `.py` + **18** in subpackages | Ticket's 137 counts `__init__.py`. Ticket omits `tools/google/` (8), `tools/google/auth/` (4), `tools/models/` (5), `tools/streamlit/` (1). |
| MCP client + server | 4 (+`os/mcp`) | **3** client modules in `tools/mcp/`, **2** server modules in `os/` | Client count matches once `__init__.py` is excluded. The ticket's `os/mcp` is `os/mcp.py` (plus `os/mcp_results.py`) -- a **file, not a directory**, and a genuine `fastmcp` touchpoint. See A12. |
| Knowledge readers | ~22 | **19** concrete (+ `base.py`, `reader_factory.py`, **2** in `reader/utils/`) | Ticket's 22 counts `__init__.py` and `base.py`, and misses the `reader/utils/` subpackage. |
| Knowledge embedders | ~20 | **18** concrete (+ `base.py`) | Same off-by-two. |
| Knowledge chunkers | ~10 | **8** concrete (+ `strategy.py`) | Same off-by-two. ADR keeps chunking in core -- see A1. |
| Knowledge rerankers | *not listed* | **4** (+ `base.py`) | **Gap in the ticket.** ADR 0001 assigns to integrations. |
| Knowledge loaders | *not listed* | **5** (+ `base.py`) | **Gap in the ticket.** ADR 0001 assigns to integrations. |
| Knowledge remote content | *not listed* | **5** (+ `base.py`, `remote_content.py`) | **Gap in the ticket.** ADR 0001 assigns to integrations. |
| `knowledge/remote_knowledge.py` | *not listed* | **1** | **Gap in the ticket.** ADR 0001 assigns to integrations. |
| Tracing exporters | "OTel" | **1** in-tree (`exporter.py`); **0** vendor exporters | No Langfuse/Arize code exists in-tree -- see A9. |
| Context providers | "~context" | **11** provider dirs (8 move, 3 stay) | Ticket never fixed a number and routes all of `context/` to integrations; ADR splits it -- see A8. |
| Cloud provider adapters | *not listed* | **4** modules under `cloud/aws/` | **Gap in the ticket.** ADR 0001 line 164 extracts these to integrations. |
| Provider-specific `utils/` | *not listed* | **14** (`gemini`, `openai`, `mcp`, `streamlit` + **10** in `utils/models/`) | **Gap in the ticket.** ADR 0001 decision 5 + split table (lines 152/178) split `utils/`. |
| Channel integrations (existing) | "only `discord/`" | **1** | Matches; already in the target layer. |
| `os/interfaces/` channels | *not listed* | **35** (34 adapter modules + `base.py`) | **Gap in the ticket.** Slack, Telegram, WhatsApp, A2A, AG-UI. ADR 0001 line 37 puts channel integrations in the integrations layer -- see A13. |
| `agents/` external frameworks | *not listed* | **6** (+ `base.py`) | **Gap in the ticket.** `claude_agent_sdk`, `dspy`, `langchain_core`, Google Generative Language API. |
| `guardrails/openai.py` | *not listed* | **1** (of 4 in `guardrails/`) | **Gap in the ticket.** Instantiates an OpenAI client and calls the moderation API. |
| `run/cancellation_management/` | *not listed* | **1** (of 3) | **Gap in the ticket.** Redis backend. |

**Totals: 401 modules inventoried.** 293 move to the integrations layer unambiguously; 67 need a decision first (section 6), of which 56 are proposed to move and 11 to stay or split; 35 are interfaces, domain types or pure logic that stay in core / orchestration; 6 are deprecation shims to delete. That is **~349 modules destined for the integrations layer**, against the ticket's ~270 estimate. The excess is the rerankers, loaders, remote content, cloud adapters and provider utils the ticket's table did not account for.

### Disposition legend

| Code | Meaning |
|---|---|
| `MOVE` | Unambiguous external touchpoint; extraction ticket can move it mechanically. |
| `STAY` | Interface, domain type, or pure logic. Remains in core / orchestration. |
| `FLAG` | Needs a decision before extraction. Each carries a pointer to section 6. |
| `DELETE` | Deprecation shim; removed rather than moved. |

---

## 4. Inventory

### 4.1 Model providers (47)

`Model` in `models/base.py`. Target `integrations/models/*`, owner EGPT-1.4.

| Module | Interface (core) | Target module | Ticket | Disposition | Note |
|---|---|---|---|---|---|
| `models/aimlapi/` | `Model` (`models/base.py`) | `integrations/models/aimlapi` | EGPT-1.4 | MOVE | provider package |
| `models/anthropic/` | `Model` (`models/base.py`) | `integrations/models/anthropic` | EGPT-1.4 | MOVE | provider package |
| `models/aws/` | `Model` (`models/base.py`) | `integrations/models/aws` | EGPT-1.4 | MOVE | provider package |
| `models/azure/` | `Model` (`models/base.py`) | `integrations/models/azure` | EGPT-1.4 | MOVE | provider package |
| `models/cerebras/` | `Model` (`models/base.py`) | `integrations/models/cerebras` | EGPT-1.4 | MOVE | provider package |
| `models/cloudflare/` | `Model` (`models/base.py`) | `integrations/models/cloudflare` | EGPT-1.4 | MOVE | provider package |
| `models/cohere/` | `Model` (`models/base.py`) | `integrations/models/cohere` | EGPT-1.4 | MOVE | provider package |
| `models/cometapi/` | `Model` (`models/base.py`) | `integrations/models/cometapi` | EGPT-1.4 | MOVE | provider package |
| `models/dashscope/` | `Model` (`models/base.py`) | `integrations/models/dashscope` | EGPT-1.4 | MOVE | provider package |
| `models/deepinfra/` | `Model` (`models/base.py`) | `integrations/models/deepinfra` | EGPT-1.4 | MOVE | provider package |
| `models/deepseek/` | `Model` (`models/base.py`) | `integrations/models/deepseek` | EGPT-1.4 | MOVE | provider package |
| `models/fireworks/` | `Model` (`models/base.py`) | `integrations/models/fireworks` | EGPT-1.4 | MOVE | provider package |
| `models/google/` | `Model` (`models/base.py`) | `integrations/models/google` | EGPT-1.4 | MOVE | provider package |
| `models/groq/` | `Model` (`models/base.py`) | `integrations/models/groq` | EGPT-1.4 | MOVE | provider package |
| `models/huggingface/` | `Model` (`models/base.py`) | `integrations/models/huggingface` | EGPT-1.4 | MOVE | provider package |
| `models/ibm/` | `Model` (`models/base.py`) | `integrations/models/ibm` | EGPT-1.4 | MOVE | provider package |
| `models/inception/` | `Model` (`models/base.py`) | `integrations/models/inception` | EGPT-1.4 | MOVE | provider package |
| `models/internlm/` | `Model` (`models/base.py`) | `integrations/models/internlm` | EGPT-1.4 | MOVE | provider package |
| `models/langdb/` | `Model` (`models/base.py`) | `integrations/models/langdb` | EGPT-1.4 | MOVE | provider package |
| `models/litellm/` | `Model` (`models/base.py`) | `integrations/models/litellm` | EGPT-1.4 | MOVE | provider package |
| `models/llama_cpp/` | `Model` (`models/base.py`) | `integrations/models/llama_cpp` | EGPT-1.4 | MOVE | provider package |
| `models/lmstudio/` | `Model` (`models/base.py`) | `integrations/models/lmstudio` | EGPT-1.4 | MOVE | provider package |
| `models/meta/` | `Model` (`models/base.py`) | `integrations/models/meta` | EGPT-1.4 | MOVE | provider package |
| `models/minimax/` | `Model` (`models/base.py`) | `integrations/models/minimax` | EGPT-1.4 | MOVE | provider package |
| `models/mistral/` | `Model` (`models/base.py`) | `integrations/models/mistral` | EGPT-1.4 | MOVE | provider package |
| `models/moonshot/` | `Model` (`models/base.py`) | `integrations/models/moonshot` | EGPT-1.4 | MOVE | provider package |
| `models/n1n/` | `Model` (`models/base.py`) | `integrations/models/n1n` | EGPT-1.4 | MOVE | provider package |
| `models/nebius/` | `Model` (`models/base.py`) | `integrations/models/nebius` | EGPT-1.4 | MOVE | provider package |
| `models/neosantara/` | `Model` (`models/base.py`) | `integrations/models/neosantara` | EGPT-1.4 | MOVE | provider package |
| `models/nexus/` | `Model` (`models/base.py`) | `integrations/models/nexus` | EGPT-1.4 | MOVE | provider package |
| `models/nvidia/` | `Model` (`models/base.py`) | `integrations/models/nvidia` | EGPT-1.4 | MOVE | provider package |
| `models/ollama/` | `Model` (`models/base.py`) | `integrations/models/ollama` | EGPT-1.4 | MOVE | provider package |
| `models/openai/` | `Model` (`models/base.py`) | `integrations/models/openai` | EGPT-1.4 | MOVE | provider package |
| `models/openrouter/` | `Model` (`models/base.py`) | `integrations/models/openrouter` | EGPT-1.4 | MOVE | provider package |
| `models/perplexity/` | `Model` (`models/base.py`) | `integrations/models/perplexity` | EGPT-1.4 | MOVE | provider package |
| `models/portkey/` | `Model` (`models/base.py`) | `integrations/models/portkey` | EGPT-1.4 | MOVE | provider package |
| `models/requesty/` | `Model` (`models/base.py`) | `integrations/models/requesty` | EGPT-1.4 | MOVE | provider package |
| `models/sambanova/` | `Model` (`models/base.py`) | `integrations/models/sambanova` | EGPT-1.4 | MOVE | provider package |
| `models/siliconflow/` | `Model` (`models/base.py`) | `integrations/models/siliconflow` | EGPT-1.4 | MOVE | provider package |
| `models/together/` | `Model` (`models/base.py`) | `integrations/models/together` | EGPT-1.4 | MOVE | provider package |
| `models/tuning_engines/` | `Model` (`models/base.py`) | `integrations/models/tuning_engines` | EGPT-1.4 | MOVE | verified: `TuningEngines(OpenAILike)` implements `Model` -- see A10 |
| `models/vercel/` | `Model` (`models/base.py`) | `integrations/models/vercel` | EGPT-1.4 | MOVE | provider package |
| `models/vertexai/` | `Model` (`models/base.py`) | `integrations/models/vertexai` | EGPT-1.4 | MOVE | provider package |
| `models/vllm/` | `Model` (`models/base.py`) | `integrations/models/vllm` | EGPT-1.4 | MOVE | provider package |
| `models/xai/` | `Model` (`models/base.py`) | `integrations/models/xai` | EGPT-1.4 | MOVE | provider package |
| `models/xiaomi/` | `Model` (`models/base.py`) | `integrations/models/xiaomi` | EGPT-1.4 | MOVE | provider package |
| `models/utils.py` | -- | `integrations/models/utils` | EGPT-1.4 | MOVE | `_PROVIDERS` construction table; ADR prereq #2 -- must register constructors on `Registry`, never be imported by services |

### 4.2 Vector DB backends (18)

`VectorDb` in `vectordb/base.py`. Target `integrations/vectordb/*`, owner EGPT-1.5.

| Module | Interface (core) | Target module | Ticket | Disposition | Note |
|---|---|---|---|---|---|
| `vectordb/cassandra/` | `VectorDb` (`vectordb/base.py`) | `integrations/vectordb/cassandra` | EGPT-1.5 | MOVE |  |
| `vectordb/chroma/` | `VectorDb` (`vectordb/base.py`) | `integrations/vectordb/chroma` | EGPT-1.5 | MOVE |  |
| `vectordb/clickhouse/` | `VectorDb` (`vectordb/base.py`) | `integrations/vectordb/clickhouse` | EGPT-1.5 | MOVE |  |
| `vectordb/couchbase/` | `VectorDb` (`vectordb/base.py`) | `integrations/vectordb/couchbase` | EGPT-1.5 | MOVE |  |
| `vectordb/lancedb/` | `VectorDb` (`vectordb/base.py`) | `integrations/vectordb/lancedb` | EGPT-1.5 | MOVE |  |
| `vectordb/langchaindb/` | `VectorDb` (`vectordb/base.py`) | `integrations/vectordb/langchaindb` | EGPT-1.5 | FLAG | wraps another framework, not a store -- confirm it belongs in `integrations/vectordb` |
| `vectordb/lightrag/` | `VectorDb` (`vectordb/base.py`) | `integrations/vectordb/lightrag` | EGPT-1.5 | FLAG | wraps another framework, not a store -- confirm it belongs in `integrations/vectordb` |
| `vectordb/llamaindex/` | `VectorDb` (`vectordb/base.py`) | `integrations/vectordb/llamaindex` | EGPT-1.5 | FLAG | wraps another framework, not a store -- confirm it belongs in `integrations/vectordb` |
| `vectordb/milvus/` | `VectorDb` (`vectordb/base.py`) | `integrations/vectordb/milvus` | EGPT-1.5 | MOVE |  |
| `vectordb/mongodb/` | `VectorDb` (`vectordb/base.py`) | `integrations/vectordb/mongodb` | EGPT-1.5 | MOVE |  |
| `vectordb/pgvector/` | `VectorDb` (`vectordb/base.py`) | `integrations/vectordb/pgvector` | EGPT-1.5 | MOVE |  |
| `vectordb/pineconedb/` | `VectorDb` (`vectordb/base.py`) | `integrations/vectordb/pineconedb` | EGPT-1.5 | MOVE |  |
| `vectordb/qdrant/` | `VectorDb` (`vectordb/base.py`) | `integrations/vectordb/qdrant` | EGPT-1.5 | MOVE |  |
| `vectordb/redis/` | `VectorDb` (`vectordb/base.py`) | `integrations/vectordb/redis` | EGPT-1.5 | MOVE |  |
| `vectordb/singlestore/` | `VectorDb` (`vectordb/base.py`) | `integrations/vectordb/singlestore` | EGPT-1.5 | MOVE |  |
| `vectordb/surrealdb/` | `VectorDb` (`vectordb/base.py`) | `integrations/vectordb/surrealdb` | EGPT-1.5 | MOVE |  |
| `vectordb/upstashdb/` | `VectorDb` (`vectordb/base.py`) | `integrations/vectordb/upstashdb` | EGPT-1.5 | MOVE |  |
| `vectordb/weaviate/` | `VectorDb` (`vectordb/base.py`) | `integrations/vectordb/weaviate` | EGPT-1.5 | MOVE |  |

### 4.3 DB backends (16)

`BaseDb` / `AsyncBaseDb` in `db/base.py`. Target `integrations/db/*`, owner EGPT-1.6.

| Module | Interface (core) | Target module | Ticket | Disposition | Note |
|---|---|---|---|---|---|
| `db/async_postgres/` | `BaseDb` / `AsyncBaseDb` | `integrations/db/async_postgres` | EGPT-1.6 | MOVE |  |
| `db/clickhouse/` | `BaseDb` / `AsyncBaseDb` | `integrations/db/clickhouse` | EGPT-1.6 | MOVE |  |
| `db/dynamo/` | `BaseDb` / `AsyncBaseDb` | `integrations/db/dynamo` | EGPT-1.6 | MOVE |  |
| `db/firestore/` | `BaseDb` / `AsyncBaseDb` | `integrations/db/firestore` | EGPT-1.6 | MOVE |  |
| `db/gcs_json/` | `BaseDb` / `AsyncBaseDb` | `integrations/db/gcs_json` | EGPT-1.6 | MOVE |  |
| `db/in_memory/` | `BaseDb` / `AsyncBaseDb` | `integrations/db/in_memory` | EGPT-1.6 | FLAG | no external system (process memory / local file) -- see A2 |
| `db/json/` | `BaseDb` / `AsyncBaseDb` | `integrations/db/json` | EGPT-1.6 | FLAG | no external system (process memory / local file) -- see A2 |
| `db/mongo/` | `BaseDb` / `AsyncBaseDb` | `integrations/db/mongo` | EGPT-1.6 | MOVE |  |
| `db/mysql/` | `BaseDb` / `AsyncBaseDb` | `integrations/db/mysql` | EGPT-1.6 | MOVE |  |
| `db/postgres/` | `BaseDb` / `AsyncBaseDb` | `integrations/db/postgres` | EGPT-1.6 | MOVE |  |
| `db/redis/` | `BaseDb` / `AsyncBaseDb` | `integrations/db/redis` | EGPT-1.6 | MOVE |  |
| `db/singlestore/` | `BaseDb` / `AsyncBaseDb` | `integrations/db/singlestore` | EGPT-1.6 | MOVE |  |
| `db/sqlite/` | `BaseDb` / `AsyncBaseDb` | `integrations/db/sqlite` | EGPT-1.6 | MOVE |  |
| `db/surrealdb/` | `BaseDb` / `AsyncBaseDb` | `integrations/db/surrealdb` | EGPT-1.6 | MOVE |  |
| `db/schemas/` | -- | stay in core | -- | STAY | shared table schemas, not a backend |
| `db/migrations/` | -- | stay in core | -- | STAY | shared migrations (incl. `migrations/versions/`), not a backend |

### 4.4 Tools and toolkits (154)

`Toolkit` in `tools/toolkit.py`, `Function` in `tools/function.py`. Target `integrations/tools/*`, owner EGPT-1.7.

| Module | Interface (core) | Target module | Ticket | Disposition | Note |
|---|---|---|---|---|---|
| `tools/_local_file_utils.py` | `Toolkit` / `Function` | stay in core | -- | STAY | tool interface + registration infra (ADR: core) |
| `tools/agentql.py` | `Toolkit` | `integrations/tools/agentql` | EGPT-1.7 | MOVE |  |
| `tools/airflow.py` | `Toolkit` | `integrations/tools/airflow` | EGPT-1.7 | FLAG | writes local DAG files, no Airflow client -- see A4 |
| `tools/antigravity.py` | `Toolkit` | `integrations/tools/antigravity` | EGPT-1.7 | MOVE |  |
| `tools/api.py` | `Toolkit` | `integrations/tools/api` | EGPT-1.7 | MOVE |  |
| `tools/apify.py` | `Toolkit` | `integrations/tools/apify` | EGPT-1.7 | MOVE |  |
| `tools/arxiv.py` | `Toolkit` | `integrations/tools/arxiv` | EGPT-1.7 | MOVE |  |
| `tools/aws_lambda.py` | `Toolkit` | `integrations/tools/aws_lambda` | EGPT-1.7 | MOVE |  |
| `tools/aws_ses.py` | `Toolkit` | `integrations/tools/aws_ses` | EGPT-1.7 | MOVE |  |
| `tools/baidusearch.py` | `Toolkit` | `integrations/tools/baidusearch` | EGPT-1.7 | MOVE |  |
| `tools/bitbucket.py` | `Toolkit` | `integrations/tools/bitbucket` | EGPT-1.7 | MOVE |  |
| `tools/brandfetch.py` | `Toolkit` | `integrations/tools/brandfetch` | EGPT-1.7 | MOVE |  |
| `tools/bravesearch.py` | `Toolkit` | `integrations/tools/bravesearch` | EGPT-1.7 | MOVE |  |
| `tools/brightdata.py` | `Toolkit` | `integrations/tools/brightdata` | EGPT-1.7 | MOVE |  |
| `tools/browserbase.py` | `Toolkit` | `integrations/tools/browserbase` | EGPT-1.7 | MOVE |  |
| `tools/calcom.py` | `Toolkit` | `integrations/tools/calcom` | EGPT-1.7 | MOVE |  |
| `tools/calculator.py` | `Toolkit` | `integrations/tools/calculator` | EGPT-1.7 | FLAG | pure arithmetic, no external system -- see A4 |
| `tools/cartesia.py` | `Toolkit` | `integrations/tools/cartesia` | EGPT-1.7 | MOVE |  |
| `tools/clickup.py` | `Toolkit` | `integrations/tools/clickup` | EGPT-1.7 | MOVE |  |
| `tools/coding.py` | `Toolkit` | `integrations/tools/coding` | EGPT-1.7 | FLAG | local subprocess -- see A4 |
| `tools/confluence.py` | `Toolkit` | `integrations/tools/confluence` | EGPT-1.7 | MOVE |  |
| `tools/crawl4ai.py` | `Toolkit` | `integrations/tools/crawl4ai` | EGPT-1.7 | MOVE |  |
| `tools/csv_toolkit.py` | `Toolkit` | `integrations/tools/csv_toolkit` | EGPT-1.7 | FLAG | local duckdb engine -- see A4 |
| `tools/dalle.py` | `Toolkit` | `integrations/tools/dalle` | EGPT-1.7 | MOVE |  |
| `tools/daytona.py` | `Toolkit` | `integrations/tools/daytona` | EGPT-1.7 | MOVE |  |
| `tools/decorator.py` | `Toolkit` / `Function` | stay in core | -- | STAY | tool interface + registration infra (ADR: core) |
| `tools/desi_vocal.py` | `Toolkit` | `integrations/tools/desi_vocal` | EGPT-1.7 | MOVE |  |
| `tools/discord.py` | `Toolkit` | `integrations/tools/discord` | EGPT-1.7 | MOVE |  |
| `tools/docker.py` | `Toolkit` | `integrations/tools/docker` | EGPT-1.7 | FLAG | local Docker daemon socket -- see A4 |
| `tools/docling.py` | `Toolkit` | `integrations/tools/docling` | EGPT-1.7 | FLAG | local docling library -- see A4 |
| `tools/duckdb.py` | `Toolkit` | `integrations/tools/duckdb` | EGPT-1.7 | FLAG | local duckdb engine -- see A4 |
| `tools/duckduckgo.py` | `Toolkit` | `integrations/tools/duckduckgo` | EGPT-1.7 | MOVE |  |
| `tools/e2b.py` | `Toolkit` | `integrations/tools/e2b` | EGPT-1.7 | MOVE |  |
| `tools/eleven_labs.py` | `Toolkit` | `integrations/tools/eleven_labs` | EGPT-1.7 | MOVE |  |
| `tools/email.py` | `Toolkit` | `integrations/tools/email` | EGPT-1.7 | MOVE |  |
| `tools/evm.py` | `Toolkit` | `integrations/tools/evm` | EGPT-1.7 | MOVE |  |
| `tools/exa.py` | `Toolkit` | `integrations/tools/exa` | EGPT-1.7 | MOVE |  |
| `tools/fal.py` | `Toolkit` | `integrations/tools/fal` | EGPT-1.7 | MOVE |  |
| `tools/file.py` | `Toolkit` | `integrations/tools/file` | EGPT-1.7 | FLAG | local filesystem -- see A4 |
| `tools/file_generation.py` | `Toolkit` | `integrations/tools/file_generation` | EGPT-1.7 | FLAG | local docx/reportlab -- see A4 |
| `tools/financial_datasets.py` | `Toolkit` | `integrations/tools/financial_datasets` | EGPT-1.7 | MOVE |  |
| `tools/firecrawl.py` | `Toolkit` | `integrations/tools/firecrawl` | EGPT-1.7 | MOVE |  |
| `tools/function.py` | `Toolkit` / `Function` | stay in core | -- | STAY | tool interface + registration infra (ADR: core) |
| `tools/giphy.py` | `Toolkit` | `integrations/tools/giphy` | EGPT-1.7 | MOVE |  |
| `tools/github.py` | `Toolkit` | `integrations/tools/github` | EGPT-1.7 | MOVE |  |
| `tools/gitlab.py` | `Toolkit` | `integrations/tools/gitlab` | EGPT-1.7 | MOVE |  |
| `tools/gmail.py` | deprecation shim | delete; superseded by `tools/google/` | EGPT-1.7 | DELETE | 14-15 line `warnings.warn` re-export of `tools/google/*` |
| `tools/google_bigquery.py` | deprecation shim | delete; superseded by `tools/google/` | EGPT-1.7 | DELETE | 14-15 line `warnings.warn` re-export of `tools/google/*` |
| `tools/google_drive.py` | deprecation shim | delete; superseded by `tools/google/` | EGPT-1.7 | DELETE | 14-15 line `warnings.warn` re-export of `tools/google/*` |
| `tools/google_maps.py` | deprecation shim | delete; superseded by `tools/google/` | EGPT-1.7 | DELETE | 14-15 line `warnings.warn` re-export of `tools/google/*` |
| `tools/googlecalendar.py` | deprecation shim | delete; superseded by `tools/google/` | EGPT-1.7 | DELETE | 14-15 line `warnings.warn` re-export of `tools/google/*` |
| `tools/googlesheets.py` | deprecation shim | delete; superseded by `tools/google/` | EGPT-1.7 | DELETE | 14-15 line `warnings.warn` re-export of `tools/google/*` |
| `tools/hackernews.py` | `Toolkit` | `integrations/tools/hackernews` | EGPT-1.7 | MOVE |  |
| `tools/jina.py` | `Toolkit` | `integrations/tools/jina` | EGPT-1.7 | MOVE |  |
| `tools/jira.py` | `Toolkit` | `integrations/tools/jira` | EGPT-1.7 | MOVE |  |
| `tools/knowledge.py` | `Toolkit` | stay in core/services | -- | FLAG | wraps a framework capability, not an external system -- see A3 |
| `tools/linear.py` | `Toolkit` | `integrations/tools/linear` | EGPT-1.7 | MOVE |  |
| `tools/linkup.py` | `Toolkit` | `integrations/tools/linkup` | EGPT-1.7 | MOVE |  |
| `tools/llms_txt.py` | `Toolkit` | `integrations/tools/llms_txt` | EGPT-1.7 | MOVE |  |
| `tools/local_file_system.py` | `Toolkit` | `integrations/tools/local_file_system` | EGPT-1.7 | FLAG | local filesystem -- see A4 |
| `tools/lumalab.py` | `Toolkit` | `integrations/tools/lumalab` | EGPT-1.7 | MOVE |  |
| `tools/mcp_toolbox.py` | `Toolkit` | `integrations/tools/mcp_toolbox` | EGPT-1.7 | MOVE |  |
| `tools/mem0.py` | `Toolkit` | `integrations/tools/mem0` | EGPT-1.7 | MOVE |  |
| `tools/memory.py` | `Toolkit` | stay in core/services | -- | FLAG | wraps a framework capability, not an external system -- see A3 |
| `tools/mlx_transcribe.py` | `Toolkit` | `integrations/tools/mlx_transcribe` | EGPT-1.7 | FLAG | local mlx model -- see A4 |
| `tools/models_labs.py` | `Toolkit` | `integrations/tools/models_labs` | EGPT-1.7 | MOVE |  |
| `tools/moviepy_video.py` | `Toolkit` | `integrations/tools/moviepy_video` | EGPT-1.7 | FLAG | local moviepy -- see A4 |
| `tools/nano_banana.py` | `Toolkit` | `integrations/tools/nano_banana` | EGPT-1.7 | MOVE |  |
| `tools/neo4j.py` | `Toolkit` | `integrations/tools/neo4j` | EGPT-1.7 | MOVE |  |
| `tools/newspaper.py` | `Toolkit` | `integrations/tools/newspaper` | EGPT-1.7 | MOVE |  |
| `tools/newspaper4k.py` | `Toolkit` | `integrations/tools/newspaper4k` | EGPT-1.7 | MOVE |  |
| `tools/notion.py` | `Toolkit` | `integrations/tools/notion` | EGPT-1.7 | MOVE |  |
| `tools/openai.py` | `Toolkit` | `integrations/tools/openai` | EGPT-1.7 | MOVE |  |
| `tools/openbb.py` | `Toolkit` | `integrations/tools/openbb` | EGPT-1.7 | MOVE |  |
| `tools/opencv.py` | `Toolkit` | `integrations/tools/opencv` | EGPT-1.7 | FLAG | local cv2 -- see A4 |
| `tools/openweather.py` | `Toolkit` | `integrations/tools/openweather` | EGPT-1.7 | MOVE |  |
| `tools/oxylabs.py` | `Toolkit` | `integrations/tools/oxylabs` | EGPT-1.7 | MOVE |  |
| `tools/pandas.py` | `Toolkit` | `integrations/tools/pandas` | EGPT-1.7 | FLAG | local library -- see A4 |
| `tools/parallel.py` | `Toolkit` | `integrations/tools/parallel` | EGPT-1.7 | MOVE |  |
| `tools/perplexity.py` | `Toolkit` | `integrations/tools/perplexity` | EGPT-1.7 | MOVE |  |
| `tools/postgres.py` | `Toolkit` | `integrations/tools/postgres` | EGPT-1.7 | MOVE |  |
| `tools/pubmed.py` | `Toolkit` | `integrations/tools/pubmed` | EGPT-1.7 | MOVE |  |
| `tools/python.py` | `Toolkit` | `integrations/tools/python` | EGPT-1.7 | FLAG | local interpreter -- see A4 |
| `tools/reasoning.py` | `Toolkit` | stay in core/services | -- | FLAG | wraps a framework capability, not an external system -- see A3 |
| `tools/reddit.py` | `Toolkit` | `integrations/tools/reddit` | EGPT-1.7 | MOVE |  |
| `tools/redshift.py` | `Toolkit` | `integrations/tools/redshift` | EGPT-1.7 | MOVE |  |
| `tools/replicate.py` | `Toolkit` | `integrations/tools/replicate` | EGPT-1.7 | MOVE |  |
| `tools/resend.py` | `Toolkit` | `integrations/tools/resend` | EGPT-1.7 | MOVE |  |
| `tools/salesforce.py` | `Toolkit` | `integrations/tools/salesforce` | EGPT-1.7 | MOVE |  |
| `tools/scavio.py` | `Toolkit` | `integrations/tools/scavio` | EGPT-1.7 | MOVE |  |
| `tools/scheduler.py` | `Toolkit` | stay in core/services | -- | FLAG | wraps a framework capability, not an external system -- see A3 |
| `tools/scrapegraph.py` | `Toolkit` | `integrations/tools/scrapegraph` | EGPT-1.7 | MOVE |  |
| `tools/searchapi.py` | `Toolkit` | `integrations/tools/searchapi` | EGPT-1.7 | MOVE |  |
| `tools/searxng.py` | `Toolkit` | `integrations/tools/searxng` | EGPT-1.7 | MOVE |  |
| `tools/seltz.py` | `Toolkit` | `integrations/tools/seltz` | EGPT-1.7 | MOVE |  |
| `tools/serpapi.py` | `Toolkit` | `integrations/tools/serpapi` | EGPT-1.7 | MOVE |  |
| `tools/serper.py` | `Toolkit` | `integrations/tools/serper` | EGPT-1.7 | MOVE |  |
| `tools/shell.py` | `Toolkit` | `integrations/tools/shell` | EGPT-1.7 | FLAG | local subprocess -- see A4 |
| `tools/shopify.py` | `Toolkit` | `integrations/tools/shopify` | EGPT-1.7 | MOVE |  |
| `tools/slack.py` | `Toolkit` | `integrations/tools/slack` | EGPT-1.7 | MOVE |  |
| `tools/sleep.py` | `Toolkit` | `integrations/tools/sleep` | EGPT-1.7 | FLAG | no external system -- see A4 |
| `tools/sofya.py` | `Toolkit` | `integrations/tools/sofya` | EGPT-1.7 | MOVE |  |
| `tools/spider.py` | `Toolkit` | `integrations/tools/spider` | EGPT-1.7 | MOVE |  |
| `tools/spotify.py` | `Toolkit` | `integrations/tools/spotify` | EGPT-1.7 | MOVE |  |
| `tools/sql.py` | `Toolkit` | `integrations/tools/sql` | EGPT-1.7 | MOVE |  |
| `tools/studio.py` | `Toolkit` | stay in core/services | -- | FLAG | wraps a framework capability, not an external system -- see A3 |
| `tools/tavily.py` | `Toolkit` | `integrations/tools/tavily` | EGPT-1.7 | MOVE |  |
| `tools/telegram.py` | `Toolkit` | `integrations/tools/telegram` | EGPT-1.7 | MOVE |  |
| `tools/todoist.py` | `Toolkit` | `integrations/tools/todoist` | EGPT-1.7 | MOVE |  |
| `tools/tool_registry.py` | `Toolkit` / `Function` | stay in core | -- | STAY | tool interface + registration infra (ADR: core) |
| `tools/toolkit.py` | `Toolkit` / `Function` | stay in core | -- | STAY | tool interface + registration infra (ADR: core) |
| `tools/trafilatura.py` | `Toolkit` | `integrations/tools/trafilatura` | EGPT-1.7 | MOVE |  |
| `tools/trello.py` | `Toolkit` | `integrations/tools/trello` | EGPT-1.7 | MOVE |  |
| `tools/twelvelabs.py` | `Toolkit` | `integrations/tools/twelvelabs` | EGPT-1.7 | MOVE |  |
| `tools/twilio.py` | `Toolkit` | `integrations/tools/twilio` | EGPT-1.7 | MOVE |  |
| `tools/unsplash.py` | `Toolkit` | `integrations/tools/unsplash` | EGPT-1.7 | MOVE |  |
| `tools/user_control_flow.py` | `Toolkit` | stay in core/services | -- | FLAG | wraps a framework capability, not an external system -- see A3 |
| `tools/user_feedback.py` | `Toolkit` | stay in core/services | -- | FLAG | wraps a framework capability, not an external system -- see A3 |
| `tools/valyu.py` | `Toolkit` | `integrations/tools/valyu` | EGPT-1.7 | MOVE |  |
| `tools/visualization.py` | `Toolkit` | `integrations/tools/visualization` | EGPT-1.7 | FLAG | local matplotlib -- see A4 |
| `tools/webbrowser.py` | `Toolkit` | `integrations/tools/webbrowser` | EGPT-1.7 | FLAG | stdlib, opens local browser -- see A4 |
| `tools/webex.py` | `Toolkit` | `integrations/tools/webex` | EGPT-1.7 | MOVE |  |
| `tools/websearch.py` | `Toolkit` | `integrations/tools/websearch` | EGPT-1.7 | MOVE |  |
| `tools/website.py` | `Toolkit` | `integrations/tools/website` | EGPT-1.7 | MOVE |  |
| `tools/webtools.py` | `Toolkit` | `integrations/tools/webtools` | EGPT-1.7 | MOVE |  |
| `tools/whatsapp.py` | `Toolkit` | `integrations/tools/whatsapp` | EGPT-1.7 | MOVE |  |
| `tools/wikipedia.py` | `Toolkit` | `integrations/tools/wikipedia` | EGPT-1.7 | MOVE |  |
| `tools/workflow.py` | `Toolkit` | stay in core/services | -- | FLAG | wraps a framework capability, not an external system -- see A3 |
| `tools/workspace.py` | `Toolkit` | stay in core/services | -- | FLAG | wraps a framework capability, not an external system -- see A3 |
| `tools/x.py` | `Toolkit` | `integrations/tools/x` | EGPT-1.7 | MOVE |  |
| `tools/yfinance.py` | `Toolkit` | `integrations/tools/yfinance` | EGPT-1.7 | MOVE |  |
| `tools/youcom.py` | `Toolkit` | `integrations/tools/youcom` | EGPT-1.7 | MOVE |  |
| `tools/youtube.py` | `Toolkit` | `integrations/tools/youtube` | EGPT-1.7 | MOVE |  |
| `tools/zendesk.py` | `Toolkit` | `integrations/tools/zendesk` | EGPT-1.7 | MOVE |  |
| `tools/zep.py` | `Toolkit` | `integrations/tools/zep` | EGPT-1.7 | MOVE |  |
| `tools/zoom.py` | `Toolkit` | `integrations/tools/zoom` | EGPT-1.7 | MOVE |  |
| `tools/google/base.py` | `Toolkit` | `integrations/tools/google/base` | EGPT-1.7 | MOVE | real implementation behind the top-level shims |
| `tools/google/bigquery.py` | `Toolkit` | `integrations/tools/google/bigquery` | EGPT-1.7 | MOVE | real implementation behind the top-level shims |
| `tools/google/calendar.py` | `Toolkit` | `integrations/tools/google/calendar` | EGPT-1.7 | MOVE | real implementation behind the top-level shims |
| `tools/google/drive.py` | `Toolkit` | `integrations/tools/google/drive` | EGPT-1.7 | MOVE | real implementation behind the top-level shims |
| `tools/google/gmail.py` | `Toolkit` | `integrations/tools/google/gmail` | EGPT-1.7 | MOVE | real implementation behind the top-level shims |
| `tools/google/maps.py` | `Toolkit` | `integrations/tools/google/maps` | EGPT-1.7 | MOVE | real implementation behind the top-level shims |
| `tools/google/sheets.py` | `Toolkit` | `integrations/tools/google/sheets` | EGPT-1.7 | MOVE | real implementation behind the top-level shims |
| `tools/google/slides.py` | `Toolkit` | `integrations/tools/google/slides` | EGPT-1.7 | MOVE | real implementation behind the top-level shims |
| `tools/google/auth/credentials.py` | `Toolkit` (`toolkit.py`) / OAuth helpers | `integrations/tools/google/auth/credentials` | EGPT-1.7 | MOVE | imported by `base.py`, `gmail`, `drive`, `calendar`, `sheets`, `slides` -- **must move with them or their imports break**; **missing from the EGPT-1.3 ticket table** |
| `tools/google/auth/decorator.py` | `Toolkit` (`toolkit.py`) / OAuth helpers | `integrations/tools/google/auth/decorator` | EGPT-1.7 | MOVE | imported by `base.py`, `gmail`, `drive`, `calendar`, `sheets`, `slides` -- **must move with them or their imports break**; **missing from the EGPT-1.3 ticket table** |
| `tools/google/auth/tokens.py` | `Toolkit` (`toolkit.py`) / OAuth helpers | `integrations/tools/google/auth/tokens` | EGPT-1.7 | MOVE | imported by `base.py`, `gmail`, `drive`, `calendar`, `sheets`, `slides` -- **must move with them or their imports break**; **missing from the EGPT-1.3 ticket table** |
| `tools/google/auth/toolkit.py` | `Toolkit` (`toolkit.py`) / OAuth helpers | `integrations/tools/google/auth/toolkit` | EGPT-1.7 | MOVE | imported by `base.py`, `gmail`, `drive`, `calendar`, `sheets`, `slides` -- **must move with them or their imports break**; **missing from the EGPT-1.3 ticket table** |
| `tools/models/azure_openai.py` | `Toolkit` | `integrations/tools/models/azure_openai` | EGPT-1.7 | FLAG | model-backed toolkit -- `integrations/tools/models` or `integrations/models`? see A5 |
| `tools/models/gemini.py` | `Toolkit` | `integrations/tools/models/gemini` | EGPT-1.7 | FLAG | model-backed toolkit -- `integrations/tools/models` or `integrations/models`? see A5 |
| `tools/models/groq.py` | `Toolkit` | `integrations/tools/models/groq` | EGPT-1.7 | FLAG | model-backed toolkit -- `integrations/tools/models` or `integrations/models`? see A5 |
| `tools/models/morph.py` | `Toolkit` | `integrations/tools/models/morph` | EGPT-1.7 | FLAG | model-backed toolkit -- `integrations/tools/models` or `integrations/models`? see A5 |
| `tools/models/nebius.py` | `Toolkit` | `integrations/tools/models/nebius` | EGPT-1.7 | FLAG | model-backed toolkit -- `integrations/tools/models` or `integrations/models`? see A5 |
| `tools/streamlit/components.py` | -- | `integrations/tools/streamlit/components` | EGPT-1.7 | FLAG | UI components, not a `Toolkit` -- see A6 |

### 4.5 MCP client and server (5)

Tool interface. Target `integrations/tools/mcp`, owner EGPT-1.7. The client lives in `tools/mcp/`; the server lives in `os/` and is flagged under A12.

| Module | Interface (core) | Target module | Ticket | Disposition | Note |
|---|---|---|---|---|---|
| `tools/mcp/mcp.py` | `Toolkit` (tool interface) | `integrations/tools/mcp/mcp` | EGPT-1.7 | MOVE | MCP **client** -- consumes external MCP servers |
| `tools/mcp/multi_mcp.py` | `Toolkit` (tool interface) | `integrations/tools/mcp/multi_mcp` | EGPT-1.7 | MOVE | MCP **client** -- consumes external MCP servers |
| `tools/mcp/params.py` | `Toolkit` (tool interface) | `integrations/tools/mcp/params` | EGPT-1.7 | MOVE | MCP **client** -- consumes external MCP servers |
| `os/mcp.py` | -- | integrations/tools/mcp/server (proposed) | EGPT-1.7 | FLAG | MCP **server** -- exposes EgptOS over MCP; imports `fastmcp` directly from the api layer. See A12 |
| `os/mcp_results.py` | -- | integrations/tools/mcp/server (proposed) | EGPT-1.7 | FLAG | result shaping for the MCP server; imports `mcp.types`. See A12 |

### 4.6 Knowledge -- readers (23)

`Reader` in `knowledge/reader/base.py`. Owner EGPT-1.8.

| Module | Interface (core) | Target module | Ticket | Disposition | Note |
|---|---|---|---|---|---|
| `knowledge/reader/arxiv_reader.py` | `Reader` (`knowledge/reader/base.py`) | `integrations/knowledge/reader/arxiv_reader` | EGPT-1.8 | MOVE |  |
| `knowledge/reader/csv_reader.py` | `Reader` (`knowledge/reader/base.py`) | `integrations/knowledge/reader/csv_reader` | EGPT-1.8 | MOVE |  |
| `knowledge/reader/docling_reader.py` | `Reader` (`knowledge/reader/base.py`) | `integrations/knowledge/reader/docling_reader` | EGPT-1.8 | MOVE |  |
| `knowledge/reader/docx_reader.py` | `Reader` (`knowledge/reader/base.py`) | `integrations/knowledge/reader/docx_reader` | EGPT-1.8 | MOVE |  |
| `knowledge/reader/excel_reader.py` | `Reader` (`knowledge/reader/base.py`) | `integrations/knowledge/reader/excel_reader` | EGPT-1.8 | MOVE |  |
| `knowledge/reader/field_labeled_csv_reader.py` | `Reader` (`knowledge/reader/base.py`) | `integrations/knowledge/reader/field_labeled_csv_reader` | EGPT-1.8 | MOVE |  |
| `knowledge/reader/firecrawl_reader.py` | `Reader` (`knowledge/reader/base.py`) | `integrations/knowledge/reader/firecrawl_reader` | EGPT-1.8 | MOVE |  |
| `knowledge/reader/json_reader.py` | `Reader` (`knowledge/reader/base.py`) | `integrations/knowledge/reader/json_reader` | EGPT-1.8 | MOVE |  |
| `knowledge/reader/llms_txt_reader.py` | `Reader` (`knowledge/reader/base.py`) | `integrations/knowledge/reader/llms_txt_reader` | EGPT-1.8 | MOVE |  |
| `knowledge/reader/markdown_reader.py` | `Reader` (`knowledge/reader/base.py`) | `integrations/knowledge/reader/markdown_reader` | EGPT-1.8 | MOVE |  |
| `knowledge/reader/pdf_reader.py` | `Reader` (`knowledge/reader/base.py`) | `integrations/knowledge/reader/pdf_reader` | EGPT-1.8 | MOVE |  |
| `knowledge/reader/pptx_reader.py` | `Reader` (`knowledge/reader/base.py`) | `integrations/knowledge/reader/pptx_reader` | EGPT-1.8 | MOVE |  |
| `knowledge/reader/s3_reader.py` | `Reader` (`knowledge/reader/base.py`) | `integrations/knowledge/reader/s3_reader` | EGPT-1.8 | MOVE |  |
| `knowledge/reader/tavily_reader.py` | `Reader` (`knowledge/reader/base.py`) | `integrations/knowledge/reader/tavily_reader` | EGPT-1.8 | MOVE |  |
| `knowledge/reader/text_reader.py` | `Reader` (`knowledge/reader/base.py`) | `integrations/knowledge/reader/text_reader` | EGPT-1.8 | MOVE |  |
| `knowledge/reader/web_search_reader.py` | `Reader` (`knowledge/reader/base.py`) | `integrations/knowledge/reader/web_search_reader` | EGPT-1.8 | MOVE |  |
| `knowledge/reader/website_reader.py` | `Reader` (`knowledge/reader/base.py`) | `integrations/knowledge/reader/website_reader` | EGPT-1.8 | MOVE |  |
| `knowledge/reader/wikipedia_reader.py` | `Reader` (`knowledge/reader/base.py`) | `integrations/knowledge/reader/wikipedia_reader` | EGPT-1.8 | MOVE |  |
| `knowledge/reader/youtube_reader.py` | `Reader` (`knowledge/reader/base.py`) | `integrations/knowledge/reader/youtube_reader` | EGPT-1.8 | MOVE |  |
| `knowledge/reader/base.py` | `Reader` | stay in core | -- | STAY | interface |
| `knowledge/reader/utils/spreadsheet.py` | -- | `integrations/knowledge/reader/utils/spreadsheet` | EGPT-1.8 | MOVE | imported by the csv / excel / website / firecrawl / docling / llms_txt readers -- **must move with them or their imports break**; **missing from the EGPT-1.3 ticket table** |
| `knowledge/reader/utils/url_validation.py` | -- | `integrations/knowledge/reader/utils/url_validation` | EGPT-1.8 | MOVE | imported by the csv / excel / website / firecrawl / docling / llms_txt readers -- **must move with them or their imports break**; **missing from the EGPT-1.3 ticket table** |
| `knowledge/reader/reader_factory.py` | -- | `integrations/knowledge/reader/reader_factory` | EGPT-1.8 | FLAG | maps extensions -> concrete readers; same constructor-registration prereq as `models/utils.py` -- see A7 |

### 4.7 Knowledge -- embedders (19)

`Embedder` in `knowledge/embedder/base.py`. Owner EGPT-1.8.

| Module | Interface (core) | Target module | Ticket | Disposition | Note |
|---|---|---|---|---|---|
| `knowledge/embedder/aws_bedrock.py` | `Embedder` (`knowledge/embedder/base.py`) | `integrations/knowledge/embedder/aws_bedrock` | EGPT-1.8 | MOVE |  |
| `knowledge/embedder/azure_openai.py` | `Embedder` (`knowledge/embedder/base.py`) | `integrations/knowledge/embedder/azure_openai` | EGPT-1.8 | MOVE |  |
| `knowledge/embedder/cohere.py` | `Embedder` (`knowledge/embedder/base.py`) | `integrations/knowledge/embedder/cohere` | EGPT-1.8 | MOVE |  |
| `knowledge/embedder/fastembed.py` | `Embedder` (`knowledge/embedder/base.py`) | `integrations/knowledge/embedder/fastembed` | EGPT-1.8 | MOVE |  |
| `knowledge/embedder/fireworks.py` | `Embedder` (`knowledge/embedder/base.py`) | `integrations/knowledge/embedder/fireworks` | EGPT-1.8 | MOVE |  |
| `knowledge/embedder/google.py` | `Embedder` (`knowledge/embedder/base.py`) | `integrations/knowledge/embedder/google` | EGPT-1.8 | MOVE |  |
| `knowledge/embedder/huggingface.py` | `Embedder` (`knowledge/embedder/base.py`) | `integrations/knowledge/embedder/huggingface` | EGPT-1.8 | MOVE |  |
| `knowledge/embedder/jina.py` | `Embedder` (`knowledge/embedder/base.py`) | `integrations/knowledge/embedder/jina` | EGPT-1.8 | MOVE |  |
| `knowledge/embedder/langdb.py` | `Embedder` (`knowledge/embedder/base.py`) | `integrations/knowledge/embedder/langdb` | EGPT-1.8 | MOVE |  |
| `knowledge/embedder/mistral.py` | `Embedder` (`knowledge/embedder/base.py`) | `integrations/knowledge/embedder/mistral` | EGPT-1.8 | MOVE |  |
| `knowledge/embedder/nebius.py` | `Embedder` (`knowledge/embedder/base.py`) | `integrations/knowledge/embedder/nebius` | EGPT-1.8 | MOVE |  |
| `knowledge/embedder/ollama.py` | `Embedder` (`knowledge/embedder/base.py`) | `integrations/knowledge/embedder/ollama` | EGPT-1.8 | MOVE |  |
| `knowledge/embedder/openai.py` | `Embedder` (`knowledge/embedder/base.py`) | `integrations/knowledge/embedder/openai` | EGPT-1.8 | MOVE |  |
| `knowledge/embedder/openai_like.py` | `Embedder` | `integrations/knowledge/embedder/openai_like` | EGPT-1.8 | MOVE | shared base for OpenAI-compatible embedders; must move with its subclasses |
| `knowledge/embedder/sentence_transformer.py` | `Embedder` (`knowledge/embedder/base.py`) | `integrations/knowledge/embedder/sentence_transformer` | EGPT-1.8 | MOVE |  |
| `knowledge/embedder/together.py` | `Embedder` (`knowledge/embedder/base.py`) | `integrations/knowledge/embedder/together` | EGPT-1.8 | MOVE |  |
| `knowledge/embedder/vllm.py` | `Embedder` (`knowledge/embedder/base.py`) | `integrations/knowledge/embedder/vllm` | EGPT-1.8 | MOVE |  |
| `knowledge/embedder/voyageai.py` | `Embedder` (`knowledge/embedder/base.py`) | `integrations/knowledge/embedder/voyageai` | EGPT-1.8 | MOVE |  |
| `knowledge/embedder/base.py` | `Embedder` | stay in core | -- | STAY | interface |

### 4.8 Knowledge -- chunkers (9)

`ChunkingStrategy` in `knowledge/chunking/strategy.py`. ADR 0001 keeps chunking in **core**; four of eight have hard external dependencies. See A1.

| Module | Interface (core) | Target module | Ticket | Disposition | Note |
|---|---|---|---|---|---|
| `knowledge/chunking/agentic.py` | `ChunkingStrategy` | integrations/knowledge/chunking/agentic (proposed) | EGPT-1.8 | FLAG | builds a `Model` and falls back to concrete `OpenAIChat` -- see A1 |
| `knowledge/chunking/code.py` | `ChunkingStrategy` | integrations/knowledge/chunking/code (proposed) | EGPT-1.8 | FLAG | hard dependency on `chonkie` -- see A1 |
| `knowledge/chunking/document.py` | `ChunkingStrategy` | stay in core (ADR 0001) | EGPT-1.8 | STAY | pure text logic, no external system |
| `knowledge/chunking/fixed.py` | `ChunkingStrategy` | stay in core (ADR 0001) | EGPT-1.8 | STAY | pure text logic, no external system |
| `knowledge/chunking/markdown.py` | `ChunkingStrategy` | integrations/knowledge/chunking/markdown (proposed) | EGPT-1.8 | FLAG | hard dependency on `unstructured` -- see A1 |
| `knowledge/chunking/recursive.py` | `ChunkingStrategy` | stay in core (ADR 0001) | EGPT-1.8 | STAY | pure text logic, no external system |
| `knowledge/chunking/row.py` | `ChunkingStrategy` | stay in core (ADR 0001) | EGPT-1.8 | STAY | pure text logic, no external system |
| `knowledge/chunking/semantic.py` | `ChunkingStrategy` | integrations/knowledge/chunking/semantic (proposed) | EGPT-1.8 | FLAG | depends on `chonkie` + falls back to concrete `OpenAIEmbedder` -- see A1 |
| `knowledge/chunking/strategy.py` | `ChunkingStrategy` | stay in core | -- | STAY | interface + `ChunkingStrategyFactory`; factory statically imports every chunker -- same seam issue as A7 |

### 4.9 Knowledge -- rerankers (5)

`Reranker` in `knowledge/reranker/base.py`. Owner EGPT-1.8. **Absent from the ticket's mapping table.**

| Module | Interface (core) | Target module | Ticket | Disposition | Note |
|---|---|---|---|---|---|
| `knowledge/reranker/aws_bedrock.py` | `Reranker` (`knowledge/reranker/base.py`) | `integrations/knowledge/reranker/aws_bedrock` | EGPT-1.8 | MOVE | **missing from the EGPT-1.3 ticket table** |
| `knowledge/reranker/cohere.py` | `Reranker` (`knowledge/reranker/base.py`) | `integrations/knowledge/reranker/cohere` | EGPT-1.8 | MOVE | **missing from the EGPT-1.3 ticket table** |
| `knowledge/reranker/infinity.py` | `Reranker` (`knowledge/reranker/base.py`) | `integrations/knowledge/reranker/infinity` | EGPT-1.8 | MOVE | **missing from the EGPT-1.3 ticket table** |
| `knowledge/reranker/sentence_transformer.py` | `Reranker` (`knowledge/reranker/base.py`) | `integrations/knowledge/reranker/sentence_transformer` | EGPT-1.8 | MOVE | **missing from the EGPT-1.3 ticket table** |
| `knowledge/reranker/base.py` | `Reranker` | stay in core | -- | STAY | interface |

### 4.10 Knowledge -- loaders (6)

`BaseLoader` in `knowledge/loaders/base.py`. Owner EGPT-1.8. **Absent from the ticket's mapping table.**

| Module | Interface (core) | Target module | Ticket | Disposition | Note |
|---|---|---|---|---|---|
| `knowledge/loaders/azure_blob.py` | `BaseLoader` (`knowledge/loaders/base.py`) | `integrations/knowledge/loaders/azure_blob` | EGPT-1.8 | MOVE | **missing from the EGPT-1.3 ticket table** |
| `knowledge/loaders/gcs.py` | `BaseLoader` (`knowledge/loaders/base.py`) | `integrations/knowledge/loaders/gcs` | EGPT-1.8 | MOVE | **missing from the EGPT-1.3 ticket table** |
| `knowledge/loaders/github.py` | `BaseLoader` (`knowledge/loaders/base.py`) | `integrations/knowledge/loaders/github` | EGPT-1.8 | MOVE | **missing from the EGPT-1.3 ticket table** |
| `knowledge/loaders/s3.py` | `BaseLoader` (`knowledge/loaders/base.py`) | `integrations/knowledge/loaders/s3` | EGPT-1.8 | MOVE | **missing from the EGPT-1.3 ticket table** |
| `knowledge/loaders/sharepoint.py` | `BaseLoader` (`knowledge/loaders/base.py`) | `integrations/knowledge/loaders/sharepoint` | EGPT-1.8 | MOVE | **missing from the EGPT-1.3 ticket table** |
| `knowledge/loaders/base.py` | `BaseLoader` | stay in core | -- | STAY | interface |

### 4.11 Knowledge -- remote content (9)

`BaseStorageConfig` in `knowledge/remote_content/base.py`. Owner EGPT-1.8. **Absent from the ticket's mapping table.**

| Module | Interface (core) | Target module | Ticket | Disposition | Note |
|---|---|---|---|---|---|
| `knowledge/remote_content/azure_blob.py` | `BaseStorageConfig` | `integrations/knowledge/remote_content/azure_blob` | EGPT-1.8 | MOVE | **missing from the EGPT-1.3 ticket table** |
| `knowledge/remote_content/gcs.py` | `BaseStorageConfig` | `integrations/knowledge/remote_content/gcs` | EGPT-1.8 | MOVE | **missing from the EGPT-1.3 ticket table** |
| `knowledge/remote_content/github.py` | `BaseStorageConfig` | `integrations/knowledge/remote_content/github` | EGPT-1.8 | MOVE | **missing from the EGPT-1.3 ticket table** |
| `knowledge/remote_content/s3.py` | `BaseStorageConfig` | `integrations/knowledge/remote_content/s3` | EGPT-1.8 | MOVE | **missing from the EGPT-1.3 ticket table** |
| `knowledge/remote_content/sharepoint.py` | `BaseStorageConfig` | `integrations/knowledge/remote_content/sharepoint` | EGPT-1.8 | MOVE | **missing from the EGPT-1.3 ticket table** |
| `knowledge/remote_content/base.py` | `BaseStorageConfig` | stay in core | -- | STAY | interface |
| `knowledge/remote_content/remote_content.py` | -- | `integrations/knowledge/remote_content` | EGPT-1.8 | FLAG | concrete `*Content` classes per backend; move with the backends |
| `knowledge/document/` | `Document` | stay in core | -- | STAY | knowledge domain type (ADR 0001 line 103); listed so no subpackage is unaccounted for |
| `knowledge/remote_knowledge.py` | -- | `integrations/knowledge/remote_knowledge` | EGPT-1.8 | MOVE | ADR 0001 assigns to integrations; **missing from the EGPT-1.3 ticket table** |

### 4.12 Context providers (18)

`ContextProvider` / `ContextBackend`. ADR 0001 splits this package: builders stay in orchestration, external connectors move. See A8.

| Module | Interface (core) | Target module | Ticket | Disposition | Note |
|---|---|---|---|---|---|
| `context/calendar/` | `ContextProvider` (`context/provider.py`) | `integrations/context/calendar` | EGPT-1.7/1.8 | MOVE |  |
| `context/database/` | `ContextProvider` (`context/provider.py`) | `integrations/context/database` | EGPT-1.7/1.8 | MOVE |  |
| `context/fs/` | `ContextProvider` (`context/provider.py`) | stay in orchestration (ADR 0001) | -- | STAY | local filesystem provider (ADR: orchestration) |
| `context/gdrive/` | `ContextProvider` (`context/provider.py`) | `integrations/context/gdrive` | EGPT-1.7/1.8 | MOVE |  |
| `context/gmail/` | `ContextProvider` (`context/provider.py`) | `integrations/context/gmail` | EGPT-1.7/1.8 | MOVE |  |
| `context/google/` | `ContextProvider` (`context/provider.py`) | `integrations/context/google` | EGPT-1.7/1.8 | MOVE | shared Google auth helpers for the gmail/gdrive/calendar providers |
| `context/mcp/` | `ContextProvider` (`context/provider.py`) | `integrations/context/mcp` | EGPT-1.7/1.8 | MOVE |  |
| `context/slack/` | `ContextProvider` (`context/provider.py`) | `integrations/context/slack` | EGPT-1.7/1.8 | MOVE |  |
| `context/web/` | `ContextProvider` (`context/provider.py`) | `integrations/context/web` | EGPT-1.7/1.8 | MOVE | holds `exa.py`, `parallel.py` + their MCP variants |
| `context/wiki/provider.py` | `ContextProvider` | stay in orchestration (ADR 0001) | -- | STAY | imports `CommitSummary` + `WikiBackend` from `backend.py` and takes `WikiBackend` in its constructor |
| `context/wiki/backend.py` | `WikiBackend` (defined here) | **split** -- see A8 | EGPT-1.7 | FLAG | single file holding the ABC, `CommitSummary`, `WikiBackendError`, `FileSystemBackend` **and** `GitBackend` / `NotionDatabaseBackend` / `NotionPageBackend`. Cannot move or stay whole -- see A8 |
| `context/wiki/git_ops.py` | -- | `integrations/context/wiki/git_ops` | EGPT-1.7 | MOVE | git subprocess driver used only by `GitBackend` |
| `context/wiki/notion_ops.py` | -- | `integrations/context/wiki/notion_ops` | EGPT-1.7 | MOVE | Notion API driver used only by the Notion backends |
| `context/workspace/` | `ContextProvider` (`context/provider.py`) | stay in orchestration (ADR 0001) | -- | STAY | local workspace provider (ADR: orchestration) |
| `context/provider.py` | -- | stay in core/orchestration | -- | STAY | `ContextProvider` ABC |
| `context/backend.py` | -- | stay in core/orchestration | -- | STAY | `ContextBackend` ABC |
| `context/mode.py` | -- | stay in core/orchestration | -- | STAY | context mode enum |
| `context/_utils.py` | -- | stay in core/orchestration | -- | STAY | shared helpers |

### 4.13 Tracing (3)

OTel `SpanExporter`. Owner Epic 4.

| Module | Interface (core) | Target module | Ticket | Disposition | Note |
|---|---|---|---|---|---|
| `tracing/exporter.py` | OTel `SpanExporter` | `integrations/tracing/exporter` | Epic 4 | MOVE | ADR 0001 assigns to integrations |
| `tracing/schemas.py` | -- | stay in core | -- | STAY | tracing domain types (ADR 0001) |
| `tracing/setup.py` | -- | composition root | Epic 4 | FLAG | ADR prereq #5: imports `tracing.exporter` + `remote.base`; stays out of enforceable core until it takes an exporter port |

### 4.14 Cloud provider adapters (4)

No core interface today. ADR 0001 line 164 requires extraction so `cloud/` stays provider-agnostic. **Absent from the ticket's mapping table.**

| Module | Interface (core) | Target module | Ticket | Disposition | Note |
|---|---|---|---|---|---|
| `cloud/aws/base.py` | -- | `integrations/cloud/aws/*` | EGPT-1.7 (TBC) | MOVE | ADR 0001: provider-specific deploy code extracts to integrations; **missing from the EGPT-1.3 ticket table** |
| `cloud/aws/s3/api_client.py` | -- | `integrations/cloud/aws/*` | EGPT-1.7 (TBC) | MOVE | ADR 0001: provider-specific deploy code extracts to integrations; **missing from the EGPT-1.3 ticket table** |
| `cloud/aws/s3/bucket.py` | -- | `integrations/cloud/aws/*` | EGPT-1.7 (TBC) | MOVE | ADR 0001: provider-specific deploy code extracts to integrations; **missing from the EGPT-1.3 ticket table** |
| `cloud/aws/s3/object.py` | -- | `integrations/cloud/aws/*` | EGPT-1.7 (TBC) | MOVE | ADR 0001: provider-specific deploy code extracts to integrations; **missing from the EGPT-1.3 ticket table** |

### 4.15 Provider-specific utils (15)

No core interface. ADR 0001 decision 5 and its split table (lines 152/178) assign these to integrations. **Absent from the ticket's mapping table.**

| Module | Interface (core) | Target module | Ticket | Disposition | Note |
|---|---|---|---|---|---|
| `utils/gemini.py` | -- | integrations/... (with its provider) | EGPT-1.4/1.7 | MOVE | Google GenAI SDK helpers; ADR 0001 decision 5 + split table (line 178); **missing from the EGPT-1.3 ticket table** |
| `utils/openai.py` | -- | integrations/... (with its provider) | EGPT-1.4/1.7 | MOVE | OpenAI-shaped payload helpers; ADR 0001 decision 5 + split table (line 178); **missing from the EGPT-1.3 ticket table** |
| `utils/mcp.py` | -- | integrations/... (with its provider) | EGPT-1.4/1.7 | MOVE | MCP SDK helpers; ADR 0001 decision 5 + split table (line 178); **missing from the EGPT-1.3 ticket table** |
| `utils/streamlit.py` | -- | integrations/... (with its provider) | EGPT-1.4/1.7 | MOVE | Streamlit SDK helpers; ADR 0001 decision 5 + split table (line 178); **missing from the EGPT-1.3 ticket table** |
| `utils/models/_genai_compat.py` | -- | integrations/... (with its provider) | EGPT-1.4 | MOVE | Google GenAI SDK version shims -- move with `models/google`; ADR 0001 lines 152/178 assign `utils/models/` to integrations; **missing from the EGPT-1.3 ticket table** |
| `utils/models/_mistral_compat.py` | -- | integrations/... (with its provider) | EGPT-1.4 | MOVE | Mistral SDK version shims -- move with `models/mistral`; ADR 0001 lines 152/178 assign `utils/models/` to integrations; **missing from the EGPT-1.3 ticket table** |
| `utils/models/ai_foundry.py` | -- | integrations/... (with its provider) | EGPT-1.4 | MOVE | Azure AI Foundry helpers -- move with `models/azure`; ADR 0001 lines 152/178 assign `utils/models/` to integrations; **missing from the EGPT-1.3 ticket table** |
| `utils/models/claude.py` | -- | integrations/... (with its provider) | EGPT-1.4 | MOVE | Anthropic helpers -- move with `models/anthropic`; ADR 0001 lines 152/178 assign `utils/models/` to integrations; **missing from the EGPT-1.3 ticket table** |
| `utils/models/cohere.py` | -- | integrations/... (with its provider) | EGPT-1.4 | MOVE | Cohere helpers -- move with `models/cohere`; ADR 0001 lines 152/178 assign `utils/models/` to integrations; **missing from the EGPT-1.3 ticket table** |
| `utils/models/llama.py` | -- | integrations/... (with its provider) | EGPT-1.4 | MOVE | Llama helpers -- move with `models/meta`; ADR 0001 lines 152/178 assign `utils/models/` to integrations; **missing from the EGPT-1.3 ticket table** |
| `utils/models/mistral.py` | -- | integrations/... (with its provider) | EGPT-1.4 | MOVE | Mistral helpers -- move with `models/mistral`; ADR 0001 lines 152/178 assign `utils/models/` to integrations; **missing from the EGPT-1.3 ticket table** |
| `utils/models/openai_responses.py` | -- | integrations/... (with its provider) | EGPT-1.4 | MOVE | OpenAI Responses helpers -- move with `models/openai`; ADR 0001 lines 152/178 assign `utils/models/` to integrations; **missing from the EGPT-1.3 ticket table** |
| `utils/models/schema_utils.py` | -- | integrations/... (with its provider) | EGPT-1.4 | FLAG | shared schema helpers used across providers -- see A11; ADR 0001 lines 152/178 assign `utils/models/` to integrations; **missing from the EGPT-1.3 ticket table** |
| `utils/models/watsonx.py` | -- | integrations/... (with its provider) | EGPT-1.4 | MOVE | IBM watsonx helpers -- move with `models/ibm`; ADR 0001 lines 152/178 assign `utils/models/` to integrations; **missing from the EGPT-1.3 ticket table** |
| `utils/print_response/` | -- | orchestration (not integrations) | -- | STAY | ADR 0001 line 178 assigns to orchestration alongside `utils/{agent,team,events,reasoning,response,hooks}.py`; listed here only so the `utils/` split is fully accounted for |

### 4.16 Channel integrations (existing) (1)

Already in the integrations layer.

| Module | Interface (core) | Target module | Ticket | Disposition | Note |
|---|---|---|---|---|---|
| `integrations/discord/client.py` | -- | integrations/discord (already in place) | -- | STAY | the only pre-existing occupant of the integrations layer |

### 4.17 Channel and protocol interfaces (35)

`BaseInterface` in `os/interfaces/base.py`. ADR 0001 line 37 names **channel integrations** as part of the integrations layer, and `integrations/discord/` (section 4.16) is already there -- these are its structural twins. Target `integrations/channels/*`, owner EGPT-1.7. **Absent from the ticket's mapping table.** See A13.

| Module | Interface (core) | Target module | Ticket | Disposition | Note |
|---|---|---|---|---|---|
| `os/interfaces/a2a/a2a.py` | `BaseInterface` (`os/interfaces/base.py`) | `integrations/channels/a2a/a2a` | EGPT-1.7 | FLAG | FastAPI routing / glue for the same adapter -- see A13 |
| `os/interfaces/a2a/router.py` | `BaseInterface` (`os/interfaces/base.py`) | `integrations/channels/a2a/router` | EGPT-1.7 | MOVE | vendor SDK: `a2a`; **missing from the EGPT-1.3 ticket table** |
| `os/interfaces/a2a/utils.py` | `BaseInterface` (`os/interfaces/base.py`) | `integrations/channels/a2a/utils` | EGPT-1.7 | MOVE | vendor SDK: `a2a`; **missing from the EGPT-1.3 ticket table** |
| `os/interfaces/agui/agui.py` | `BaseInterface` (`os/interfaces/base.py`) | `integrations/channels/agui/agui` | EGPT-1.7 | FLAG | FastAPI routing / glue for the same adapter -- see A13 |
| `os/interfaces/agui/handlers.py` | `BaseInterface` (`os/interfaces/base.py`) | `integrations/channels/agui/handlers` | EGPT-1.7 | MOVE | vendor SDK: `ag_ui`; **missing from the EGPT-1.3 ticket table** |
| `os/interfaces/agui/input.py` | `BaseInterface` (`os/interfaces/base.py`) | `integrations/channels/agui/input` | EGPT-1.7 | MOVE | vendor SDK: `ag_ui`; **missing from the EGPT-1.3 ticket table** |
| `os/interfaces/agui/router.py` | `BaseInterface` (`os/interfaces/base.py`) | `integrations/channels/agui/router` | EGPT-1.7 | MOVE | vendor SDK: `ag_ui`; **missing from the EGPT-1.3 ticket table** |
| `os/interfaces/agui/state.py` | `BaseInterface` (`os/interfaces/base.py`) | `integrations/channels/agui/state` | EGPT-1.7 | FLAG | FastAPI routing / glue for the same adapter -- see A13 |
| `os/interfaces/agui/stream.py` | `BaseInterface` (`os/interfaces/base.py`) | `integrations/channels/agui/stream` | EGPT-1.7 | MOVE | vendor SDK: `ag_ui`; **missing from the EGPT-1.3 ticket table** |
| `os/interfaces/slack/builders.py` | `BaseInterface` (`os/interfaces/base.py`) | `integrations/channels/slack/builders` | EGPT-1.7 | MOVE | vendor SDK: `slack_sdk`; **missing from the EGPT-1.3 ticket table** |
| `os/interfaces/slack/components.py` | `BaseInterface` (`os/interfaces/base.py`) | `integrations/channels/slack/components` | EGPT-1.7 | MOVE | vendor SDK: `slack_sdk`; **missing from the EGPT-1.3 ticket table** |
| `os/interfaces/slack/event_handler.py` | `BaseInterface` (`os/interfaces/base.py`) | `integrations/channels/slack/event_handler` | EGPT-1.7 | MOVE | vendor SDK: `slack_sdk`; **missing from the EGPT-1.3 ticket table** |
| `os/interfaces/slack/events.py` | `BaseInterface` (`os/interfaces/base.py`) | `integrations/channels/slack/events` | EGPT-1.7 | MOVE | vendor SDK: `slack_sdk`; **missing from the EGPT-1.3 ticket table** |
| `os/interfaces/slack/helpers.py` | `BaseInterface` (`os/interfaces/base.py`) | `integrations/channels/slack/helpers` | EGPT-1.7 | MOVE | vendor SDK: `httpx`; **missing from the EGPT-1.3 ticket table** |
| `os/interfaces/slack/hitl.py` | `BaseInterface` (`os/interfaces/base.py`) | `integrations/channels/slack/hitl` | EGPT-1.7 | MOVE | vendor SDK: `slack_sdk`; **missing from the EGPT-1.3 ticket table** |
| `os/interfaces/slack/ids.py` | `BaseInterface` (`os/interfaces/base.py`) | `integrations/channels/slack/ids` | EGPT-1.7 | FLAG | FastAPI routing / glue for the same adapter -- see A13 |
| `os/interfaces/slack/interactions.py` | `BaseInterface` (`os/interfaces/base.py`) | `integrations/channels/slack/interactions` | EGPT-1.7 | FLAG | FastAPI routing / glue for the same adapter -- see A13 |
| `os/interfaces/slack/pause.py` | `BaseInterface` (`os/interfaces/base.py`) | `integrations/channels/slack/pause` | EGPT-1.7 | MOVE | vendor SDK: `slack_sdk`; **missing from the EGPT-1.3 ticket table** |
| `os/interfaces/slack/router.py` | `BaseInterface` (`os/interfaces/base.py`) | `integrations/channels/slack/router` | EGPT-1.7 | FLAG | FastAPI routing / glue for the same adapter -- see A13 |
| `os/interfaces/slack/security.py` | `BaseInterface` (`os/interfaces/base.py`) | `integrations/channels/slack/security` | EGPT-1.7 | FLAG | FastAPI routing / glue for the same adapter -- see A13 |
| `os/interfaces/slack/slack.py` | `BaseInterface` (`os/interfaces/base.py`) | `integrations/channels/slack/slack` | EGPT-1.7 | FLAG | FastAPI routing / glue for the same adapter -- see A13 |
| `os/interfaces/slack/state.py` | `BaseInterface` (`os/interfaces/base.py`) | `integrations/channels/slack/state` | EGPT-1.7 | FLAG | FastAPI routing / glue for the same adapter -- see A13 |
| `os/interfaces/slack/types.py` | `BaseInterface` (`os/interfaces/base.py`) | `integrations/channels/slack/types` | EGPT-1.7 | FLAG | FastAPI routing / glue for the same adapter -- see A13 |
| `os/interfaces/telegram/events.py` | `BaseInterface` (`os/interfaces/base.py`) | `integrations/channels/telegram/events` | EGPT-1.7 | FLAG | FastAPI routing / glue for the same adapter -- see A13 |
| `os/interfaces/telegram/formatting.py` | `BaseInterface` (`os/interfaces/base.py`) | `integrations/channels/telegram/formatting` | EGPT-1.7 | FLAG | FastAPI routing / glue for the same adapter -- see A13 |
| `os/interfaces/telegram/helpers.py` | `BaseInterface` (`os/interfaces/base.py`) | `integrations/channels/telegram/helpers` | EGPT-1.7 | MOVE | vendor SDK: `telebot`; **missing from the EGPT-1.3 ticket table** |
| `os/interfaces/telegram/router.py` | `BaseInterface` (`os/interfaces/base.py`) | `integrations/channels/telegram/router` | EGPT-1.7 | MOVE | vendor SDK: `telebot`; **missing from the EGPT-1.3 ticket table** |
| `os/interfaces/telegram/security.py` | `BaseInterface` (`os/interfaces/base.py`) | `integrations/channels/telegram/security` | EGPT-1.7 | FLAG | FastAPI routing / glue for the same adapter -- see A13 |
| `os/interfaces/telegram/state.py` | `BaseInterface` (`os/interfaces/base.py`) | `integrations/channels/telegram/state` | EGPT-1.7 | MOVE | vendor SDK: `telebot`; **missing from the EGPT-1.3 ticket table** |
| `os/interfaces/telegram/telegram.py` | `BaseInterface` (`os/interfaces/base.py`) | `integrations/channels/telegram/telegram` | EGPT-1.7 | FLAG | FastAPI routing / glue for the same adapter -- see A13 |
| `os/interfaces/whatsapp/helpers.py` | `BaseInterface` (`os/interfaces/base.py`) | `integrations/channels/whatsapp/helpers` | EGPT-1.7 | MOVE | vendor SDK: `httpx`; **missing from the EGPT-1.3 ticket table** |
| `os/interfaces/whatsapp/router.py` | `BaseInterface` (`os/interfaces/base.py`) | `integrations/channels/whatsapp/router` | EGPT-1.7 | FLAG | FastAPI routing / glue for the same adapter -- see A13 |
| `os/interfaces/whatsapp/security.py` | `BaseInterface` (`os/interfaces/base.py`) | `integrations/channels/whatsapp/security` | EGPT-1.7 | FLAG | FastAPI routing / glue for the same adapter -- see A13 |
| `os/interfaces/whatsapp/whatsapp.py` | `BaseInterface` (`os/interfaces/base.py`) | `integrations/channels/whatsapp/whatsapp` | EGPT-1.7 | FLAG | FastAPI routing / glue for the same adapter -- see A13 |
| `os/interfaces/base.py` | `BaseInterface` | stay in core/api | -- | STAY | interface ABC for all channel adapters |

### 4.18 External agent frameworks (7)

`BaseAgent` in `agents/base.py`. Adapters wrapping third-party agent frameworks. Target `integrations/agents/*`, owner EGPT-1.7. **Absent from the ticket's mapping table.**

| Module | Interface (core) | Target module | Ticket | Disposition | Note |
|---|---|---|---|---|---|
| `agents/antigravity/agent.py` | `BaseAgent` (`agents/base.py`) | `integrations/agents/antigravity/agent` | EGPT-1.7 | MOVE | calls the Google Generative Language API directly (`generativelanguage.googleapis.com`); **missing from the EGPT-1.3 ticket table** |
| `agents/claude/agent.py` | `BaseAgent` (`agents/base.py`) | `integrations/agents/claude/agent` | EGPT-1.7 | MOVE | wraps `claude_agent_sdk`; **missing from the EGPT-1.3 ticket table** |
| `agents/dspy/agent.py` | `BaseAgent` (`agents/base.py`) | `integrations/agents/dspy/agent` | EGPT-1.7 | MOVE | wraps `dspy`; **missing from the EGPT-1.3 ticket table** |
| `agents/dspy/utils.py` | `BaseAgent` (`agents/base.py`) | `integrations/agents/dspy/utils` | EGPT-1.7 | MOVE | wraps `dspy`; **missing from the EGPT-1.3 ticket table** |
| `agents/langgraph/agent.py` | `BaseAgent` (`agents/base.py`) | `integrations/agents/langgraph/agent` | EGPT-1.7 | MOVE | wraps `langchain_core` / LangGraph; **missing from the EGPT-1.3 ticket table** |
| `agents/langgraph/utils.py` | `BaseAgent` (`agents/base.py`) | `integrations/agents/langgraph/utils` | EGPT-1.7 | MOVE | wraps `langchain_core` / LangGraph; **missing from the EGPT-1.3 ticket table** |
| `agents/base.py` | `BaseAgent` | stay in core | -- | STAY | external-agent interface; `rich` is a cross-cutting core dependency |

### 4.19 Other external touchpoints (7)

Touchpoints in packages that are otherwise core/services. Listed with their non-external siblings so the boundary is explicit. **Absent from the ticket's mapping table.**

| Module | Interface (core) | Target module | Ticket | Disposition | Note |
|---|---|---|---|---|---|
| `guardrails/openai.py` | `BaseGuardrail` (`guardrails/base.py`) | `integrations/guardrails/openai` | EGPT-1.7 | MOVE | instantiates `OpenAI(api_key=...)` and calls the moderation API; **missing from the EGPT-1.3 ticket table** |
| `guardrails/base.py` | `BaseGuardrail` | stay in core/services | -- | STAY | interface / pure local logic, no external system |
| `guardrails/pii.py` | `BaseGuardrail` | stay in core/services | -- | STAY | interface / pure local logic, no external system |
| `guardrails/prompt_injection.py` | `BaseGuardrail` | stay in core/services | -- | STAY | interface / pure local logic, no external system |
| `run/cancellation_management/redis_cancellation_manager.py` | `CancellationManager` | `integrations/run/redis_cancellation_manager` | EGPT-1.7 | MOVE | Redis backend; **missing from the EGPT-1.3 ticket table** |
| `run/cancellation_management/base.py` | `CancellationManager` | stay in core | -- | STAY | interface |
| `run/cancellation_management/in_memory_cancellation_manager.py` | `CancellationManager` | stay in core | -- | STAY | process memory, no external system |

---

## 5. Items missing from the EGPT-1.3 ticket table

These are real touchpoints with no row in the ticket's mapping table. All are already assigned by ADR 0001, so they need no new decision -- only inclusion in the owning extraction ticket's scope.

| Area | Modules | Owner |
|---|---|---|
| `knowledge/reranker/` | 4 | EGPT-1.8 |
| `knowledge/loaders/` | 5 | EGPT-1.8 |
| `knowledge/remote_content/` | 6 | EGPT-1.8 |
| `knowledge/remote_knowledge.py` | 1 | EGPT-1.8 |
| `tools/google/` | 8 | EGPT-1.7 |
| `tools/google/auth/` | 4 | EGPT-1.7 |
| `tools/models/` | 5 | EGPT-1.7 |
| `tools/streamlit/` | 1 | EGPT-1.7 |
| `cloud/aws/` | 4 | EGPT-1.7 (to be confirmed) |
| `utils/{gemini,openai,mcp,streamlit}.py` | 4 | EGPT-1.4 / 1.7 |
| `utils/models/` | 10 | EGPT-1.4 |
| `knowledge/reader/utils/` | 2 | EGPT-1.8 |
| `models/utils.py` | 1 | EGPT-1.4 |
| `os/interfaces/` (Slack, Telegram, WhatsApp, A2A, AG-UI) | 34 | EGPT-1.7 |
| `agents/{antigravity,claude,dspy,langgraph}` | 6 | EGPT-1.7 |
| `guardrails/openai.py` | 1 | EGPT-1.7 |
| `run/cancellation_management/redis_cancellation_manager.py` | 1 | EGPT-1.7 |

**97 modules.** Six of these -- `tools/google/auth/` (4) and `knowledge/reader/utils/` (2) -- are not merely unlisted but load-bearing: they are imported by nearly every module scheduled to move alongside them (`tools/google/{base,gmail,drive,calendar,sheets,slides}.py` and the csv / excel / website / firecrawl / docling / llms_txt readers respectively). Moving the listed modules without them breaks those imports.

The largest block -- the 42 modules under `os/interfaces/`, `agents/`, `guardrails/` and `run/cancellation_management/` -- was invisible to earlier drafts because those packages sat outside the scanned areas entirely. Section 2 now records the scope rule and the exclusions, and section 8 leads with a whole-tree sweep so an area-scoped blind spot cannot recur.

---

## 6. Ambiguous items and proposed dispositions

### A1 -- Chunkers: ADR says core, but half of them are integrations

ADR 0001 (lines 103, 175) places `knowledge/chunking/` wholly in **core**. The AST scan says four of the eight concrete chunkers cannot satisfy that:

| Chunker | External dependency |
|---|---|
| `code.py` | `chonkie` |
| `semantic.py` | `chonkie`, plus a concrete `OpenAIEmbedder` fallback |
| `markdown.py` | `unstructured` |
| `agentic.py` | builds a `Model`, with a concrete `OpenAIChat` fallback |

`document.py`, `fixed.py`, `recursive.py`, `row.py` are pure text logic and belong in core exactly as the ADR says.

**Proposed disposition:** split the package. Keep `ChunkingStrategy`, `document`, `fixed`, `recursive`, `row` in core; move the four above to `integrations/knowledge/chunking/*` under EGPT-1.8. This preserves the ADR's intent (pure logic does not leave core) while honouring the import matrix (core must not depend on `chonkie` or on a concrete model). **Requires a one-line amendment to ADR 0001.**

### A2 -- `db/in_memory/` and `db/json/` are not external systems

Both implement `BaseDb` but talk to process memory and the local filesystem respectively.

**Proposed disposition:** move them anyway, to `integrations/db/*`. The layering rule is about *implementations versus interfaces*, not about whether a network hop occurs; splitting `BaseDb` implementations across two layers would make the import lint in EGPT-1.11 harder to state than it is worth. Note that core tests and cookbooks depend on `in_memory`, so integrations must remain importable from the test composition root.

### A3 -- Framework-internal toolkits

Nine toolkits wrap the framework's own capabilities rather than any external system: `knowledge`, `memory`, `reasoning`, `workflow`, `user_control_flow`, `user_feedback`, `scheduler`, `studio`, `workspace`.

**Proposed disposition:** these stay put -- they are orchestration/services surfaces expressed as tools, and moving them to integrations would force integrations to depend on services, inverting the ADR's arrow. Confirm placement per-toolkit with the EGPT-1.7 owner.

### A4 -- Local-runtime toolkits

Nineteen toolkits touch only the local machine (`shell`, `python`, `file`, `docker`, `pandas`, `opencv`, ...). They are not remote services, but they *are* concrete adapters over things outside the framework.

**Proposed disposition:** move to `integrations/tools/*` with the rest. Same reasoning as A2 -- one rule for all `Toolkit` implementations is cheaper to enforce than a case-by-case boundary.

### A5 -- `tools/models/`

Five toolkits (`azure_openai`, `gemini`, `groq`, `morph`, `nebius`) that call model providers *as tools*.

**Proposed disposition:** `integrations/tools/models/*`, keeping the toolkit taxonomy intact. They implement `Toolkit`, not `Model`, so filing them under `integrations/models` would break the interface-to-directory correspondence the registry relies on.

### A6 -- `tools/streamlit/components.py`

A UI helper module that does not implement `Toolkit`.

**Proposed disposition:** move to `integrations/tools/streamlit`, or drop it from the framework entirely if cookbook-only. Needs an owner decision -- it is the one module here with no interface at all.

### A7 -- Static factory tables

`knowledge/reader/reader_factory.py` and `knowledge/chunking/strategy.py`'s `ChunkingStrategyFactory` both statically import every concrete implementation, exactly like `models/utils.py`'s `_PROVIDERS` table that ADR prereq #2 calls out.

**Proposed disposition:** apply the same fix -- each concrete reader/chunker registers a constructor on `Registry` at import time, and the factory resolves by name. This is a prerequisite for EGPT-1.8, not a consequence of it, and should be added to the EGPT-1.9 registry scope.

### A8 -- `context/` is split, not moved

The EGPT-1.3 ticket routes all of `context/` to `integrations/context/*`. ADR 0001 (lines 123, 153) splits it: builders and assemblers stay in orchestration, external connectors move.

**Proposed disposition:** follow the ADR. `provider.py`, `mode.py`, `_utils.py`, `fs/`, `workspace/` stay; `calendar/`, `database/`, `gdrive/`, `gmail/`, `google/`, `mcp/`, `slack/`, `web/` move.

`wiki/` is the exception the ADR gets wrong -- it is listed as orchestration but drives git and Notion. An earlier draft of this document proposed moving `wiki/backend.py` wholesale into integrations while leaving `provider.py` in orchestration. **That was wrong and would have produced the exact violation this epic exists to remove:** `provider.py:31` does `from egpt.context.wiki.backend import CommitSummary, WikiBackend` and takes a `WikiBackend` in its constructor, so the move would create an orchestration -> integrations import.

The file cannot move or stay as a unit, because it is both sides of the seam at once:

| Symbol in `context/wiki/backend.py` | Nature | Side |
|---|---|---|
| `WikiBackend` (ABC) | contract `provider.py` depends on | core / orchestration |
| `CommitSummary` | domain type in that contract | core / orchestration |
| `WikiBackendError` | exception in that contract | core / orchestration |
| `FileSystemBackend` | local files, no external system | core / orchestration |
| `GitBackend` | drives `git_ops.py` | **integrations** |
| `NotionDatabaseBackend`, `NotionPageBackend` | drive `notion_ops.py` | **integrations** |

**Revised proposal:** split `backend.py` itself. Keep the ABC, `CommitSummary`, `WikiBackendError` and `FileSystemBackend` on the inward side; move `GitBackend` and the two Notion backends to `integrations/context/wiki/` alongside `git_ops.py` and `notion_ops.py`; inject the concrete backend into `WikiContextProvider` through the retained contract. `provider.py` then imports only the ABC and domain types, and the arrow stays inward.

### A9 -- Tracing exporters do not exist yet

The ticket's table implies a set of *vendor* exporters to move. There are none: `tracing/` contains only `setup.py`, `exporter.py` and `schemas.py`, and Langfuse / Arize attach through OTel configuration rather than through code in this tree. `tracing/` is not third-party-free, though -- `setup.py:13` imports `openinference.instrumentation.agno`, which is one more reason it cannot sit in enforceable core.

**Proposed disposition:** the row is a forward declaration, not extraction work. `exporter.py` moves to `integrations/tracing/`; `schemas.py` stays in core; `setup.py` is blocked on ADR prereq #5 (it imports `remote.base` and the exporter directly). Epic 4 creates the first real occupant when it wires Langfuse.

### A10 -- `models/tuning_engines/` (resolved)

Sits among the 46 provider directories but the name suggested it might not be an LLM provider. Verified during review: `TuningEngines` subclasses `OpenAILike`, so it does implement `Model`.

**Disposition:** MOVE with the other providers under EGPT-1.4. No further decision needed.

### A11 -- `utils/models/schema_utils.py`

The other nine modules in `utils/models/` are each tied to one provider and travel with it. `schema_utils.py` is shared across providers (pydantic-only, no SDK).

**Proposed disposition:** move to a shared spot inside the integrations layer (e.g. `integrations/models/_shared`), not core -- it exists only to serve provider adapters. Confirm with the EGPT-1.4 owner.

### A12 -- MCP server in the api layer (`os/mcp.py`)

`tools/mcp/` is the MCP **client**: it consumes external MCP servers and is an unambiguous integration. `os/mcp.py` and `os/mcp_results.py` are the **server** side -- they expose EgptOS itself over MCP and import `fastmcp` and `mcp.types` directly from what ADR 0001 classifies as the api / control-plane layer. ADR rule 4 says api must not import concrete integrations outside the composition-root allowlist.

**Proposed disposition:** move the SDK-facing transport to `integrations/tools/mcp/server` and leave a thin control-plane surface in `os/` that wires it at the composition root, matching the treatment of `tracing/setup.py` in A9. The alternative -- an explicit composition-root exemption for `os/mcp.py` -- is also defensible; the EGPT-1.7 owner should pick one before extraction starts.

### A13 -- How much of `os/interfaces/` goes with the channel adapters

ADR 0001 line 37 puts **channel integrations** in the integrations layer, and `integrations/discord/` is already there, so Slack / Telegram / WhatsApp / A2A / AG-UI belong beside it rather than in the api layer. The complication is that each adapter is part vendor SDK and part FastAPI surface. Of the 34 channel modules, 17 import a vendor SDK directly (`slack_sdk`, `telebot`, `a2a`, `ag_ui`, or raw `httpx` for the Meta Graph API) and are marked MOVE; the remaining 17 are routers, security middleware and state glue that import only FastAPI/Pydantic and are marked FLAG.

**Proposed disposition:** move each adapter as a whole package to `integrations/channels/<name>/`, including its routers, and have `os/` mount them at the composition root -- the same shape proposed for the MCP server in A12. Splitting mid-adapter would put a router in api importing an SDK wrapper in integrations for every channel, which is more coupling than it removes. The alternative -- keep routers in `os/interfaces/` and move only the SDK wrappers -- is viable but needs a per-channel interface definition. **EGPT-1.7 owner decides; this is the largest single open question in the inventory.**

---

## 7. Open questions for reviewers

1. **A1 requires an ADR 0001 amendment.** Who owns that change -- this ticket, or a follow-up to EGPT-1.1?
2. **Physical vs logical move.** ADR 0001 line 168 allows implementations to stay as leaf packages that integrations owns for import-lint purposes, instead of physically relocating under `integrations/`. This inventory assumes physical moves. If 1.4-1.8 choose the logical option, every target in this document becomes an ownership label rather than a path.
3. **`cloud/aws/` owner.** ADR assigns it to integrations but no extraction ticket claims it. EGPT-1.7, or a new ticket?
4. **Deprecation shims.** Are the six `tools/google*` shims safe to delete during 1.7, or do they need a release-cycle grace period?
5. **EGPT-1.7 scope.** This document routes four newly-found families (`os/interfaces/`, `agents/`, `guardrails/openai.py`, `run/cancellation_management/`) to EGPT-1.7, which was scoped as the tools ticket. That is 42 additional modules including all the channel adapters. Split them into a new extraction ticket, or widen 1.7?

---

## 8. Reconciliation commands

Run from `libs/agno/egpt`. Re-run before each extraction ticket starts; any delta means this document is stale.

> Two whole classes of miss were found by review rather than by these commands, and the checks below exist to close both. First, a `*.py` glob cannot see subpackages (`tools/google/auth/`, `knowledge/reader/utils/`, `utils/models/` were all missed that way). Second, and more seriously, an area-scoped scan cannot see a touchpoint in an area nobody thought to scan -- that is how `os/interfaces/`, `agents/`, `guardrails/openai.py` and `run/cancellation_management/` were missed. **The authoritative check is therefore the whole-tree AST sweep, not the per-area counts.**

```bash
# AUTHORITATIVE: every module in the package that imports a non-stdlib, non-plumbing library.
# Any hit not present in section 4 or in the section 2 exclusion table is an orphan.
python3 - <<'PY'
import ast, pathlib, sys
STD = set(sys.stdlib_module_names)
PLUMBING = {'pydantic','pydantic_settings','typing_extensions','rich','fastapi','starlette',
            'uvicorn','yaml','dotenv','click','packaging','docstring_parser','jsonpatch',
            'cryptography','croniter','pytz','dateutil','numpy','httpx','requests','aiofiles'}
for p in sorted(pathlib.Path('.').rglob('*.py')):
    if '__pycache__' in str(p) or p.name == '__init__.py':
        continue
    ext = set()
    for n in ast.walk(ast.parse(p.read_text())):
        if isinstance(n, ast.Import):
            ext |= {a.name.split('.')[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            ext.add(n.module.split('.')[0])
    hits = ext - STD - PLUMBING - {'egpt'}
    if hits:
        print(f'{p}: {sorted(hits)}')
PY
```

```bash
# every subpackage in the whole tree -- each must appear in section 4 or the section 2 exclusion table
find . -mindepth 1 -type d ! -name __pycache__ ! -path './__pycache__*' | sort
```

```bash
find models    -maxdepth 1 -type d ! -name __pycache__ | tail -n +2 | wc -l   # 46
find vectordb  -maxdepth 1 -type d ! -name __pycache__ | tail -n +2 | wc -l   # 18
find db        -maxdepth 1 -type d ! -name __pycache__ | tail -n +2 | wc -l   # 16 (14 backends + schemas + migrations)
ls tools/*.py             | grep -v __init__ | wc -l                          # 136
ls tools/google/*.py      | grep -v __init__ | wc -l                          # 8
ls tools/models/*.py      | grep -v __init__ | wc -l                          # 5
ls tools/mcp/*.py         | grep -v __init__ | wc -l                          # 3
ls knowledge/reader/*.py  | grep -v __init__ | wc -l                          # 21 (19 + base + factory)
ls knowledge/embedder/*.py| grep -v __init__ | wc -l                          # 19 (18 + base)
ls knowledge/chunking/*.py| grep -v __init__ | wc -l                          # 9  (8 + strategy)
ls knowledge/reranker/*.py| grep -v __init__ | wc -l                          # 5  (4 + base)
ls knowledge/loaders/*.py | grep -v __init__ | wc -l                          # 6  (5 + base)
ls knowledge/remote_content/*.py | grep -v __init__ | wc -l                   # 7  (5 + base + remote_content)
find context   -maxdepth 1 -type d ! -name __pycache__ | tail -n +2 | wc -l   # 11
find cloud -name '*.py' ! -name __init__.py | wc -l                           # 4
ls utils/models/*.py      | grep -v __init__ | wc -l                          # 10
ls tools/google/auth/*.py | grep -v __init__ | wc -l                          # 4
ls knowledge/reader/utils/*.py | grep -v __init__ | wc -l                     # 2
ls os/mcp*.py                                                                 # 2
```

---

## 9. Acceptance criteria

| Criterion | Status |
|---|---|
| Inventory lists every external touchpoint | Done -- section 4, 401 modules across 19 areas, scope rule and exclusions in section 2 |
| Each entry has interface, target module, owning ticket | Done -- all four columns populated in every table |
| Counts reconcile with a fresh tree scan, no orphans | Done -- section 3 reconciles each area; section 5 lists the 97 modules the ticket's table omitted; commands in section 8 |
| Ambiguous items flagged with a proposed disposition | Done -- A1 through A13 in section 6 (A10 resolved during review), each with a proposal |
| Peer review by an EGPT-1.4-1.8 executor | **Pending** |
