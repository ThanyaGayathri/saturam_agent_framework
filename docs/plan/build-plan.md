# Enterprise GPT Build Plan

_Enterprise GPT Build Plan_

> What we build to turn the agent framework into a deployable Enterprise GPT: connected data, a semantic layer, suggested KPIs and use cases, and two use cases running end to end.

## Decisions taken

Settled in the planning session. Each is cited by identifier in the sections below. This table is the approval surface: disagreeing with any row changes work downstream of it.

| ID | Decision | Consequence |
|---|---|---|
| D1 | **Workflow is the spine, skill is the payload.** Workflows are fully decided and deterministic. | Each agent step carries one authored prompt. Nothing improvises its own control flow. |
| D2 | **A skill is the prompt of one agent**, authored in the `saturam-cr` shape: inputs, phases, output format, templates. | No skills control plane needed. The prompt rides inside the agent component, which is already versioned. |
| D3 | **One deployment per customer**, in their Azure tenancy. | Multi-tenant isolation work is out of scope. Missing tenant columns stop being a security problem. |
| D4 | **Raw data lands in Azure object storage**, then loads to the warehouse. | Connectors never write to Enterprise GPT directly. Loads are replayable from the raw drop. |
| D5 | **The warehouse is PostgreSQL on Azure**, separate database from the control plane. | No warehouse connector to build. `PostgresTools` and `SQLTools` already work. |
| D6 | **Enterprise GPT owns the transformation logic**, executed as SQL inside the warehouse. | Gold lives in the warehouse. The product holds definitions and orchestration, not values. |
| D7 | **Nightly refresh.** | One scheduled run before the working day, matching how the upstream exports land. |
| D8 | **Client sample data** loaded into the three connector test environments. | No backfill negotiation. The semantic layer is built over sample shapes, which must match production. |
| D9 | **One shipped retail ontology**, versioned, delivered with every deployment. | The ontology is a product asset. Customers extend it; they do not fork it. |
| D10 | **Enrichment is proposed by an agent and confirmed by a person.** | Nothing enters the semantic graph unreviewed. Matches the review screens in the prototype. |
| D11 | **The semantic layer is written straight to Neo4j** with its own labels, outside memory. Selected facts are promoted into semantic memory as a derived copy. | The source of truth never decays or consolidates. Avoids coercing tables and columns into the memory substrate's closed label set. |
| D12 | **RFQ is an explain-only use case.** | The first use case proves the chain without needing guardrails, approval gates or write-back. |
| D13 | **Results are delivered in-app with an email pointer.** | No new integration. Provenance stays attached to the answer. |
| D14 | **Monitoring is a separate pipeline**, and **golden questions plus rehearsal gate publish**. | Execution health and answer quality are measured separately. A use case that runs green but answers worse is caught. |
| D15 | **Compute is Azure Functions Flex Consumption (`api`) + Azure Container Apps (Neo4j, `pipelines`, `consolidator`, one-shot jobs), not AKS.** Reverses the "Compute platform" row this doc's own Azure-alignment section settled on AKS for. Vector retrieval moves from pgvector to Azure AI Search alongside it. Full gap analysis and phasing: `docs/plan/azure-migration-plan.md`; the day-of checklist: `deploy/AZURE_CUTOVER.md` (`deploy/AKS_CUTOVER.md` is kept only as superseded history). | Resolves the exact concern the "What AKS costs two people" callout below already raised, rather than accepting it as an unavoidable cost. `deploy/helm/egpt/`'s Kubernetes-shaped templates do not carry forward as-is — Container Apps and Functions need their own deploy artifacts, IaC tooling (Bicep vs. Terraform) undecided. Neo4j moves from "Enterprise, one cluster, two databases, licence required" to Community on Container Apps, two instances — which is what local dev already ran, so nothing new to rehearse there. |

> **Open**
>
> The second use case is deliberately unassigned. The design below carries it as a slot, so adding it is configuration and a workflow definition rather than new machinery.

## Ground truth

Read from the framework on latest main rather than from prior documents. Several capabilities we assumed were missing already exist, and two we assumed existed do not. This changes what the build actually is.

### Already built

| Capability | Where |
|---|---|
| **Workflow engine with human gates**, reject and timeout policies | `workflow/step.py:129-143`, `workflow/types.py`, twelve cookbook examples |
| **Workflows serialise to and from data**, rehydrating agents by identifier | `to_dict` and `from_dict` on Step, Condition, Loop, Parallel, Router |
| **Component lifecycle**: draft, publish, rollback, append-only audit, dry-run validation | `routers/components/components.py`, `docs/component-lifecycle.md` |
| **Approval as a tool decorator**, blocking or audit | `approval/decorator.py`, `db/schemas/approval.py` |
| **Cron scheduling** of agent, team and workflow runs over HTTP | `scheduler/`, `05_agent_os/scheduler/` |
| **Natural-language SQL** over a customer schema, read and write paths separated | `integrations/context/database/provider.py` |
| **Agent Skills** with progressive disclosure, scripts and references | `skills/`, `02_agents/16_skills` |
| **Small-model-first with rule-based escalation** to a frontier tier | `providers/escalation.py`, `providers/gateways.py` |
| **Three-leg retrieval** fusing vector, lexical and graph, with reciprocal rank fusion | `memory/retrieval/orchestrator.py:83-115` |
| **Temporal knowledge graph** with entity resolution and multi-hop traversal | `integrations/memory/graph/neo4j/` |
| **PII and prompt-injection guardrails**, eval suite with accuracy and reliability scorers | `guardrails/`, `eval/` |

### Not there

| Gap | Evidence | Impact |
|---|---|---|
| **No semantic layer, KPI, dashboard or catalog concept** | No match anywhere in the repository | The whole business-intelligence product is new |
| **The graph is hard-wired to memory**. Labels are module constants, every write needs a tier and a five-slot scope, and query filters are refused | `cypher.py:80-83`, `memory/types.py:979-984` | Drives **D11**. The semantic layer needs its own labels and read surface |
| **Knowledge retrieval is vector only.** The three-leg orchestrator is unreachable from it | `knowledge.py:554` is a single call to the vector store | Domain knowledge cannot be retrieved with memory today. Named as a gap and confirmed |
| **Skills are not control-plane components.** Only agent, team and workflow persist, and `to_dict` drops the skills field | `components/spec.py:42-48` | Neutralised by **D2** and **D3** |
| **No create, update or delete routes** for agents, teams or workflows | The scope map declares them; the routes do not exist | All provisioning goes through the components API |
| **No deployment artifacts.** No Dockerfile, compose file, chart or deployment document | One README line: runs on anything that runs containers | Section 02 is real work, not configuration |
| **No frontend.** One 48KB admin page for the components API, off by default | Zero npm anywhere in the repository | The entire customer-facing interface is ours |
| **Markdown heading splitting is off by default** | `MarkdownChunking(split_on_headings=False)` | Curated domain markdown loses its structure unless set explicitly |

## 01 Architecture spine

One rule decides where every piece of logic goes, and it is the rule the product already shows its users on the plan screen.

Deterministic, governed and repeatable work becomes workflow steps backed by named Python executors. Judgment-heavy work becomes an agent step whose prompt is a skill. Anything that can permit or block an action is always a deterministic step, never a model. The model explains and proposes; it is not the thing that says yes.

This survives the move to generated use cases in phase two. `Step.from_dict` rehydrates agents by identifier from the registry or database, but resolves deterministic executors *by name*. A generated use case can therefore only compose executors that already exist. Building that named executor library is precisely what makes the two hand-built use cases generalise, which was the requirement placed on them.

A use case is three artifacts: a stored workflow component, the skill prompts its agent steps carry, and a spec record holding what the author asked for. Two of the three already have a control plane.

## 02 Foundation

Nothing in this section exists today. Platform choices followed the Azure deployment architecture through an AKS-first, resilience-first period; **D15 reverses that** back to the architecture's own earlier Functions/Container Apps shape, for the operational-cost reason the "What AKS costs two people" callout below already named. The table rows below reflect the post-D15 state; where a row's earlier AKS-era reasoning is still worth keeping as context, it is kept rather than deleted.

| Piece | State | Work |
|---|---|---|
| **Application image** | Build | One `egptos` image with three entrypoints: API, memory consolidator, pipelines worker. Plus a `main.py` constructing `EgptOS` with agents, workflows, database and scheduler. The image is unchanged by D15 — only where each entrypoint runs moves |
| **Compute** | Build | **D15.** `api` on Azure Functions Flex Consumption, via the Python ASGI adapter over the same `egptos` image's ASGI app — no code change to the entrypoint itself. `pipelines` (its in-process scheduler poller), `consolidator` (Postgres-advisory-lock-guarded, `--once` mode already fits a cron trigger with no redesign), and the one-shot jobs (`migrate`, `warehouse_migrate`, `schedule_nightly`) run as Azure Container Apps — regular App for `pipelines`, Jobs for the rest. No `libs/agno/egpt/scheduler/` redesign needed; see `docs/plan/azure-migration-plan.md` for why |
| **Control-plane database** | Exists | Azure Database for PostgreSQL Flexible Server, zone-redundant, behind PgBouncer. **Scoped down by D15**: agent state, components, traces, KPI definitions, dashboards — knowledge and memory vectors are planned to move to Azure AI Search, so pgvector stops being a dependency of this server once AZ-B (PR #216, open and unmerged) and AZ-C (P2, not started) land. Neither has; `product/egpt_retail/memory/config.py` still reads `"backend": "pgvector"` today |
| **Raw drop zone** | Build | Azure Storage account, Blob Storage, private endpoint. Where connectors write **(D4)**. Access is planned to go through `AzureBlobObjectStore` (AZ-A, PR #213, open and unmerged) — not S3-compatible, a genuine new backend rather than a config change |
| **Warehouse** | Build | Separate PostgreSQL database for landing, silver and gold **(D5, D6)**. Separate server preferred so nightly transforms never contend with agent state. Unaffected by D15 |
| **Vector retrieval** | Build | **D15.** Azure AI Search replaces pgvector for K1 memory-vector and K2 knowledge-prose retrieval (AZ-B, PR #216 open and unmerged; AZ-C, P2, not started). K3 ontology stays on Neo4j's own native vector index, unaffected — it was never on pgvector |
| **Graph** | Exists | Neo4j **Community** on Azure Container Apps, two logical instances: memory with the semantic layer, and the domain ontology. **This is what local dev already runs** — the earlier AKS-era "Enterprise, one cluster, two databases, licence required" row is gone; there is nothing left to prove on this row that wasn't already proven locally |
| **Cache** | Exists | Azure Cache for Redis for budget counters and cross-replica run cancellation. Standard tier is enough for one customer |
| **Models** | Exists | Azure OpenAI over a private endpoint, two deployments: a small tier and a frontier tier, composed through the existing escalation provider. The GPU node pool for in-cluster serving is deferred; see the Azure alignment section |
| **Identity** | Exists | Microsoft Entra ID issuing tokens; app roles mapped to scope sets at issuance; EgptOS pointed at Entra's JWKS. **The framework gaps this needs are still open**: AZ-D is PR #214, open and unmerged, carrying an unresolved Critical finding (a blocking JWKS fetch on the event loop). Once it merges, `AuthorizationConfig.scopes_claim`/`scopes_claim_format` will handle Entra's `roles`/`scp` claims and JWKS will accept a URL with auto-refresh instead of only a static file; today `AuthorizationConfig` has neither, which is why `deploy/helm/egpt/README.md`'s ledger still marks both rows **NEVER**. User isolation on, tenancy flags off, and a fixed customer identifier stamped on every run from configuration **(G1)** |
| **Edge** | Exists | Front Door Premium + WAF, per the architecture. Streaming routes pass through unbuffered; sticky sessions only on the streaming path |
| **Secrets and settings** | Exists | Key Vault through workload identity / managed identity. Every setting is already an environment variable, so no code change |
| **Observability** | Exists | Traces to Postgres in phase one, Application Insights for infrastructure only. There is no native Azure Monitor exporter; Langfuse with a collector bridge is the phase-two path |
| **Email** | Build | Azure Communication Services for **D13**. The built-in email tools target other clouds |
| **Backups** | Build | Managed for Postgres and Redis. Neo4j is self-hosted (on Container Apps now, not AKS), so a backup job to the storage account is ours, on its own cadence |
| **API contract** | Build | Written now, wired last. Unblocks the backend and defines the interface task in section 11 |

> **What AKS costs two people — resolved by D15, not just noted**
>
> A managed Kubernetes cluster with three node pools, a self-hosted graph cluster, PgBouncer, KEDA and a Helm chart is more platform than the product needs in phase one, and every piece of it is operated by the same two people building the product. This paragraph originally concluded "the architecture is sound and it is the standing decision, so the plan follows it" — kept here as the honest record of that call, since D15 is this exact concern being acted on rather than accepted. The mitigation this callout already named — keep the number of things we author small, defer the GPU pool and the event bus — still applies unchanged; D15 just removes the Kubernetes operational surface underneath it too.

## 03 Data platform

Connectors are built by another team. What follows is the destination they write to and everything downstream of it.

- **Raw** (Stage 0) — 
- **Landing** (Stage 1) — 
- **Silver** (Stage 2) — 
- **Gold** (Stage 3) — 
Transformations are SQL owned by Enterprise GPT and executed in the warehouse **(D6)**. Each refresh is a workflow run triggered by the scheduler, so it inherits retries, run history and the audit trail without new infrastructure. The stamp on every gold row is what feeds the freshness indicator in the interface, and it is also what makes a stale answer explainable rather than merely wrong.

The semantic layer describes silver and gold. It never duplicates them. Concepts and categories live in the graph, rows stay in the warehouse, which is the type-versus-instance boundary proven in the earlier graph retrieval work.

## 04 Domain knowledge

Two layers, shipped with the product, retrieved together rather than separately.

| Layer | Home | Built from |
|---|---|---|
| **Prose** | Knowledge, on pgvector | Curated retail markdown through `MarkdownReader`, split on headings |
| **Ontology** | Neo4j, second database | Concepts, relations and synonyms, versioned and shipped as an artifact **(D9)** |
| **Mapping** | Neo4j, edges into the semantic layer | Proposed by an agent from harvested metadata, confirmed by a person **(D10)** |

### Embedding and indexing, concretely

Embedding gives each unit a vector so it is retrievable by meaning rather than exact name. This applies to *both* layers: every prose chunk, and every ontology node, computed over its name, description and synonyms. Indexing then builds the structures that make retrieval fast: an approximate nearest-neighbour index over the vectors, a full-text index for the lexical leg, and Neo4j property indexes on whatever concepts are looked up by.

Embedding the graph nodes is the step usually skipped, and it is exactly what makes a question about computers reach the Product concept. Without it the ontology is only reachable by exact match, and the synonym behaviour proven in the earlier work quietly disappears.

### Retrieving domain knowledge alongside memory

This was named as a gap and is confirmed as one. A three-leg orchestrator fusing vector, lexical and graph already exists inside the memory subsystem, and nothing outside it can reach it. Knowledge search is a single call to the vector store with no graph leg at all. The two subsystems share no retrieval code.

The work is one retrieval entry point that fans out across four sources and fuses the results: memory scoped to the user, domain prose, the domain ontology, and the semantic layer. The fusion and reranking code already exists and is lifted rather than written. Every capability in sections 06 through 08 calls this one entry point, which is what stops each of them inventing its own retrieval.

## 05 Semantic layer

Built when a source is connected. It is what turns a schema into something a question can be asked of.

- **Harvest.** Read tables, columns, types, keys, row counts and sampled values from the warehouse. Deterministic, no model involved.
- **Enrich.** Propose synonyms, question patterns, the SQL filter that scopes each concept, personal-data fields, compliance tags, and candidate KPIs. An agent proposes; a person confirms **(D10)**.
- **Map.** Attach each concept to the shipped ontology. A HubSpot deals table becomes an instance of the Quote concept, and everything the ontology knows about quotes becomes available for that table.
- **Detect business areas.** Falls out of the mapping rather than being a separate guess. Areas exist in the ontology first, so they can be configured before any source is connected, and connecting a source only marks which are now supported.
- **Promote.** Selected facts move into semantic memory, so what the system knows about the customer's data persists alongside what it learns about their people.

### How ambiguity is resolved

Four kinds, and the answer differs for each. Two are settled at build time by mapping, which is why mapping is worth a human review rather than an automatic write.

| Ambiguity | Resolved by | When |
|---|---|---|
| **Same column name, different meaning.** Two tables both have `amount` | Mapping each column to a different concept property. The mapping is the disambiguation | Build time, confirmed by a person |
| **Different names, same meaning.** Deal value, quote amount, amount | Synonyms recorded during enrichment, which is the behaviour the earlier graph retrieval work proved | Build time, confirmed by a person |
| **One word, several concepts.** Revenue could be gross or net | The ontology if the distinction is a domain one, otherwise the agent asks. The spec screen already asks about unknown metrics; the same pattern applies here | Query time |
| **Two sources could both answer** | The authoritative flag from **G20**. Without it an agent picks one and sounds equally confident either way | Build time |

Memory contributes to this, but only after the fact. Semantic memory holds that in this organisation revenue means net revenue, once somebody has said so, and procedural memory can persist the resolution as a rule. Neither helps the first time a question is asked. Memory stops the same question being asked twice; it is not a resolution strategy, and treating it as one would leave the first answer wrong.

One implementation note worth carrying. Entity resolution in the memory subsystem already does exact key match, then embedding similarity, then a merge decision, with tests. It is memory-scoped so the code does not transfer, but that is precisely the shape the mapping proposer needs, and copying a proven pattern beats inventing one.

> **Constraint worth carrying into estimates**
>
> The existing graph substrate cannot express this. Its labels are fixed at Episode, Fact and Entity, every write demands a memory tier and a five-slot scope, and populating a query filter raises an error. Under **D11** the semantic layer gets its own labels and its own read surface, written directly against Neo4j, with promotion into memory as a second step.

## 06 KPI layer

Four pieces, and only one of them involves a model.

| Piece | Kind | Behaviour |
|---|---|---|
| **Registry** | Data | Name, description, formula, grain, source tables, business area. Authoritative in Postgres, mirrored as graph nodes so a concept can reach its metrics by traversal |
| **Feasibility** | Executor | Set difference between what a formula needs and what the semantic layer says exists. Produces the ready, partial and blocked states |
| **Suggestion** | Agent | Proposes candidate KPIs from the ontology, prose and semantic graph. Returns a typed result the interface renders directly |
| **Materialisation** | Executor | Runs each formula nightly, writes gold with source and computation time **(D6, D7)** |

### Where a KPI definition comes from

Three stages, and they are often conflated. Domain knowledge ships a master registry of metric *templates*: a name, a description, and a formula shape carrying a filter parameter. A template has no source tables, because it knows nothing about any customer yet. Mapping binds it to real tables; feasibility then decides whether it can be computed here at all.

What my data supports is those templates, filtered by feasibility, merged with candidates the suggestion agent derived from the customer's own metadata. The second group are proposals, so they carry an adopt action and enter the registry only when a person accepts them.

### Computing an answer is not defining a metric

This line is implied by **D10** and by the closed-set rule, and stating it explicitly matters, because reading the closed-set rule too broadly would forbid ad-hoc calculation entirely and make chat with data far less useful than it should be.

| Action | On the fly? | Why |
|---|---|---|
| **Answer a question in chat with data** | Yes | It writes SQL, returns a number and cites provenance. Nothing persists, and the person who asked judges it the moment it appears |
| **Draft a new KPI definition** | Yes, as a proposal | The agent notices the metric is missing and drafts a formula with source tables. It lands in the proposals queue with an adopt action. One click, not a research project |
| **Name a metric in a use case spec** | No | It will be recomputed nightly and compared over time. An unreviewed definition makes that comparison meaningless |
| **Put a metric on a dashboard** | No | Same reason. A registry KPI is judged months later by someone comparing it against last quarter |

The asymmetry is the point. A one-off answer is checked immediately by the person who asked for it. A registry KPI is checked much later by someone who was not in the conversation, and by then nobody remembers it was invented mid-sentence. Four failure modes follow from letting an invented metric persist: it may not be computable at all, its formula was never validated against the schema, two people asking the same question get two different definitions, and a number from an unreviewed formula is exactly what a customer cannot put in front of their board.

Feasibility must never be a model judgment. It is a set difference, and the moment a model decides whether a KPI is supported, the badges in the interface stop meaning anything. The suggestion agent proposes; the executor decides. The suggestion output carries an identifier, name, feasibility, the missing tables where blocked, a priority and one line of rationale, because that is what the discovery screen renders without transformation.

## 07 Use case layer

Seven stages. The prototype has already fixed the shape of five of them, which removes most of the design risk.

| Stage | Kind | What it does |
|---|---|---|
| **Suggest** | Agent | Same inputs as KPI suggestion. Feeds the template list with feasibility badges |
| **Spec** | Agent + Executor | A model fills a typed schema from conversation; a deterministic pass decides what is still missing |
| **Plan** | Executor | Derived from the compiled workflow and approved before anything runs |
| **Preflight** | Executor | Data availability, joins, freshness, permissions, personal data, ownership |
| **Rehearse** | Executor | Runs the real thing against history with every side effect disabled |
| **Activate** | Person | Owner, environment, schedule, delivery, cost, and who may switch it on |
| **Execute** | Workflow | Named executors and skill-driven agent steps, with gates where a person is required |

### The four use case shapes

For a given autonomy level the step sequence is the same every time. Only the parameters differ. That is what makes generation tractable later: a new use case is not a new flow, it is one of four shapes with its slots filled.

| Shape | Steps, in order | Parameters bound from the spec |
|---|---|---|
| **Inform** no writes | Trigger · read gold · compute metrics · compare to baseline · explain · record provenance · deliver | Metrics, dimensions, comparison, schedule, recipient |
| **Alert** notifies | Trigger · read gold · compute metric · **evaluate condition**, and only if it fires: write the alert · record provenance · notify | Metric, operator, threshold, scope, severity, recipient, schedule |
| **Recommend** writes after approval | Trigger · read gold · select candidates · compute metrics · propose · **apply hard limits** · approver decides · write to target · record provenance · notify owner | Candidates, metrics, guardrails, approver, write target, timeout behaviour, schedule |
| **Act** writes unattended | Trigger · read gold · select candidates · compute metrics · propose · **apply hard limits** · **apply per-run cap** · write to target · record rollback point · record provenance · notify owner | Candidates, metrics, guardrails, write target, per-run cap, rollback, schedule |

Every branch in those sequences is a deterministic step, never a model. An alert that does not fire is still logged, so a quiet week is visibly quiet. A proposal struck by a hard limit is logged with the rule that struck it. A proposal beyond the per-run cap is held for the next run rather than dropped. Those three behaviours are what make a run auditable after the fact.

Four shapes is the complete set, because the axis is side effects rather than subject matter. A forecast and a variance analysis are both Inform with different compute steps. Reading from three sources instead of one is a parameter of the read step. Nothing about the retail domain adds a fifth shape.

> **One correction to carry from the prototype**
>
> The plan screen currently describes the run in parallel with the workflow instead of reading from it. In the product it must be a view over the compiled workflow, so no step can appear on screen that does not exist in the thing that runs. Small change, large trust payoff, and it is the difference between a diagram and an audit.

## 08 Playground

Four conversations, one agent, and the only place where progressive disclosure of skills genuinely earns its keep.

- **Chat with data.** The database context provider already gives natural-language reads through a sub-agent bound to a read-only engine. What it lacks is semantic-layer grounding and provenance, and both are ours to add. Grounding is not optional polish: the sub-agent's instructions currently carry a schema name and nothing else, so without it the model sees raw column names with no concepts, synonyms or scoping filters, and answers ambiguously with full confidence **(G21)**.
- **Chat with the knowledge base.** Calls the unified retrieval entry point from section 04, so domain knowledge and memory arrive together rather than separately.
- **Chat with a use case.** Scoped to one use case: its spec, its plan, its run history and its outputs.
- **Chat with a business area.** A filter over the same retrieval, bounded to the concepts, metrics and sources belonging to that area.

Because one agent faces four different jobs here, loading the right playbook on demand is useful rather than ceremonial. Under **D3** those playbooks can live on disk, since there is one customer per deployment.

## 09 Dashboards

Created from selected KPIs, reading gold and nothing else.

A dashboard is a named set of KPI references with layout, filters, a refresh schedule and a visibility rule. Storage is a table in the control-plane database. Reads go to gold, never to silver and never to a live aggregation, which is what keeps a four-tile dashboard from issuing four warehouse scans on every open.

Charting does not exist in the framework in any form, so rendering is entirely new work and it sits on the interface side. That places most of this section's cost inside section 11 rather than here.

## 10 Monitoring and evals

Two separate questions that are easy to conflate: is it running, and is it still right.

**Monitoring** is a pipeline of its own **(D14)**. Execution health comes free from persisted run history: failures, runs that stopped firing, cost drift, gates waiting too long on a person. On top of that sits the part that needs building, an assessment pass over output quality and input drift, because a use case that runs green while quietly returning worse answers is the failure mode that execution monitoring cannot see.

**Evals** gate publication. Each use case carries a set of golden questions with agreed answers, run automatically against every version, using the accuracy and reliability scorers that already exist. A person still reviews the rehearsal before activation. Automated checks catch regressions from a prompt edit; the human review catches the thing nobody thought to write a question about.

## 11 UI wiring

Last, and deliberately so. The design goes through approval before any of it is wired.

Every screen in the prototype must work in the deliverable. The prototype currently reads and writes through a small set of local storage helpers, and that is a clean seam: swapping those for API clients against the contract fixed in section 02 is mechanical rather than exploratory. Conversation views move to server-sent events from the run endpoints, and history comes from the sessions endpoints.

| Area | Backing |
|---|---|
| Configure, sources and semantic layer | Harvest and enrichment endpoints, with the confirmation step from **D10** |
| KPI discovery, catalog and health | Registry, feasibility and freshness from gold |
| Use cases, wizard and gallery | Components API for definitions, spec records for drafts |
| Playground | Run endpoints over server-sent events, sessions for history |
| Dashboards | Dashboard storage plus gold reads. Charting is new work |
| Monitor and alerts | Run history plus the assessment pipeline from section 10 |

## Capability inventory

Every capability in the flow, resolved to either a skill-driven agent or a named deterministic executor. Capabilities marked implicit were not on the original list but are required by the flow.

| Capability | Kind | Section | Notes |
|---|---|---|---|
| Semantic enrichment proposer *(implicit)* | Agent | 05 | Output is a proposal, never a write |
| Ontology mapping proposer *(implicit)* | Agent | 05 | Table to concept, confirmed before storing |
| KPI suggestion | Agent | 06 | Typed output for direct rendering |
| KPI formula authoring *(implicit)* | Agent | 06 | SQL with a filter parameter, validated before storing |
| Use case suggestion | Agent | 07 | Shares inputs with KPI suggestion |
| Spec extraction *(implicit)* | Agent | 07 | Values drawn only from closed sets |
| Funnel explanation | Agent | 07 | The single model step in the RFQ run |
| Chat with data, knowledge, use case, area | Agent | 08 | Four playbooks, one agent |
| Quality and drift assessment | Agent | 10 | Judges output, never permits an action |
| Metadata harvest *(implicit)* | Executor | 05 | Tables, columns, keys, samples |
| Semantic graph write *(implicit)* | Executor | 05 | Own labels, direct to Neo4j |
| Business area detection | Executor | 05 | A graph query over confirmed mappings |
| KPI feasibility *(implicit)* | Executor | 06 | Set difference. Never a model |
| KPI materialisation | Executor | 06 | Nightly, stamped with source and time |
| Spec gap check *(implicit)* | Executor | 07 | What a given autonomy level requires |
| Plan derivation *(implicit)* | Executor | 07 | A view over the compiled workflow |
| Preflight checks *(implicit)* | Executor | 07 | Failures block, warnings are acknowledged |
| Dry run *(implicit)* | Executor | 07 | Real execution, side effects disabled |
| Guardrail evaluation *(implicit)* | Executor | 07 | Needed by the second use case, not by RFQ |
| Unified retrieval *(implicit)* | Executor | 04 | Fans out across four sources and fuses |
| Delivery and provenance *(implicit)* | Executor | 07 | In-app with an email pointer **(D13)** |

## RFQ and Quote Pipeline, end to end

The first use case, at explain-only autonomy **(D12)**. Nine steps, one of which is a model. Nothing is written to any external system.

Eight deterministic steps and one model step is the shape to aim for. It is also the argument to make to a sceptical reader: the language model writes the explanation, and everything the explanation is built from was computed rather than recalled.

## Connector hand-off

For the team building the three connectors. The contract matters more than the API details: each source owes a grain, a key, a watermark and a destination.

| Source | Grain and keys | Must include | Incremental by |
|---|---|---|---|
| **Analytics** | One row per event, keyed on user and session | Event name, timestamp, and the parameters carrying brand and product identifier. Three RFQ funnel events do not exist yet and must be instrumented: add to cart, form start, submit | Event date |
| **CRM** | Contacts, deals, and deal stage history | Brand as an enforced enumeration rather than free text, and the referring product URL on every quote request. Both are inconsistent today | Last modified |
| **Session replay** | One row per session | Rage clicks, dead clicks, script errors, session URL, and the identity set at login and at submission | Session start |

All three write to Azure object storage, partitioned by source and date **(D4)**. They never write to the warehouse or to Enterprise GPT directly, which keeps loads replayable and keeps the connector team independent of our schema.

> **The one blocker that is not engineering**
>
> A single shared identity key, a hash of email, written into every system. It is the highest-value item on the list and the only one gated on privacy sign-off rather than on build effort. Without it, cross-system journeys cannot be built at all and the RFQ funnel stays confined to one source.

## Infrastructure hand-off

For the infrastructure team. Derived from all thirty-three work packages rather than from a reference architecture, so every row below is blocking something specific. The first table is the one that matters today.

### Start now, because of lead time

These five are not blocked on us. They take days or weeks of somebody else's process, and each one stops a whole lane if it arrives late.

| Item | Blocks | Why it takes time |
|---|---|---|
| **Azure OpenAI quota**, two deployments in one region: a small tier and a frontier tier | Eight packages across both lanes. Nothing that involves a model runs without it | Quota is granted per region and per model, on request, and regional capacity is genuinely constrained. The two deployments are not optional: rule-based escalation composes exactly two providers |
| **Neo4j Enterprise licence** | The whole semantic layer and the domain ontology | Community edition supports one user database. We need two, memory with the semantic layer and the ontology separately. That makes it a commercial procurement, not a deployment choice |
| **Entra app registration** with app roles, a tenant claim and a JWKS endpoint | All authentication, and the three-role model | Usually a separate identity team with its own change process. The framework needs no code change, it just points at the JWKS endpoint |
| **Source system credentials** from the client: analytics service account, CRM private app token, session replay API key | All three connectors | Client-side, and each one usually needs an admin in a different team at their end |
| **DNS name and TLS certificate** | Any demo anyone outside the team can open | Cheap once started, and reliably forgotten until the week it is needed |

### Platform and data services

| Resource | Configuration that is not the default | Needed by |
|---|---|---|
| **Container registry** | Cluster pulls by managed identity, no stored credentials | R1, R2 |
| **Kubernetes cluster** | Three node pools: system, stateful for the graph, and a GPU pool only if in-network inference is confirmed. The memory consolidator must be prevented from scaling past one replica, because it has no distributed lock | R2 |
| **PostgreSQL, control plane** | `pgvector` enabled through the server's extension allow-list, which is a server parameter change rather than just an extension install. Zone-redundant. Connection pooling in front of it | K2, K7, K10, K11, R7, R13 |
| **PostgreSQL, warehouse** | A separate database, preferably a separate server. Two roles: one read-write for materialisation, one genuinely read-only for the SQL guard. `statement_timeout` settable per role | K4, R4, R5, R6 |
| **Neo4j** | Enterprise, two databases, persistent volumes, and a backup job of our own since it is self-hosted rather than managed | K1, K3, K5, K9 |
| **Storage account** | **Hierarchical namespace must be enabled at creation.** It cannot be turned on afterwards without recreating the account, and the landing paths depend on it. Connector identity writes, application identity reads. A lifecycle policy for raw retention | C1 to C4, R4 |
| **Redis** | Budget counters and cross-replica run cancellation. Standard tier is enough for one customer | R2 |
| **Communication service for email** | A verified sender domain with its DNS records. The framework's built-in email tools target other clouds, so this is the delivery path | R12, and result delivery generally |
| **Key Vault and App Configuration** | Access by workload identity. One non-secret setting is load-bearing: the fixed customer identifier, without which memory silently does nothing | K1, and everything else |
| **Log Analytics workspace** | Infrastructure metrics only. Agent traces go to Postgres in phase one, because no native exporter exists | R14 |

### Networking, and one thing that is easy to get wrong

| Ask | Why |
|---|---|
| **Private endpoints and private DNS zones** for Postgres, Redis, the model service and storage | Standard, but each endpoint needs its zone linked to the network or name resolution fails in a way that looks like an application bug |
| **Outbound egress for the connectors** to three external APIs | Connectors are the only component that calls out to the public internet. If egress is locked down, they need a NAT gateway and an allow-list, and that is a firewall change with its own approval |
| **Streaming must pass through the edge unbuffered**, with request timeouts sized to a real agent run | The playground and every chat surface stream server-sent events. A gateway that buffers them turns a live answer into a long silence followed by a wall of text, and a short timeout kills the run outright |
| **Sticky sessions on the streaming path only** | Connection state is held per process. The rest of the surface is stateless and should round-robin freely |
| **In-cluster name resolution for the scheduler** | The scheduler triggers work by calling back into the application's own API, so its base URL has to resolve from inside the cluster |

### The open decision they need from us

> **Where the connectors run**
>
> Nothing in the plan says, and it is an infrastructure choice with real consequences. They are scheduled jobs that pull from external APIs and write files. They import nothing from the application and share none of its lifecycle. Reasonable options are scheduled jobs in the same cluster, a serverless function host, or a managed data pipeline service. The cluster keeps one thing to operate. A separate host keeps the only component holding client source credentials away from the application's blast radius. Worth settling before C2 starts rather than after.

### Also worth stating now

- **How many environments.** The plan assumes one deployment per customer, but says nothing about whether a non-production one exists. Use case activation offers a sandbox, and that is an application concept rather than a second cluster, so this needs an explicit answer either way.
- **Backups on three different stores.** Postgres and Redis are managed. The graph is self-hosted, so its backup is ours to build and ours to test restoring.
- **Budget alerts.** Model spend is the line that moves unexpectedly, and the two-tier routing exists precisely to keep it down. An alert makes that visible before an invoice does.
- **Who rotates what.** Client source credentials, the model keys and the database roles have different owners and different rotation cadences.

## Sequencing

Two people, two lanes, ordered by dependency rather than by date. Lane A owns the platform and the data. Lane B owns the agents and the product surface.

#### P0 — Foundation · no dependencies

- **Lane A — platform**
  - Image with three entrypoints; Functions app (`api`) + Container Apps environment (`pipelines`, `consolidator`, one-shot jobs) **(D15)**, not Helm/AKS
  - Control-plane database, PgBouncer, migrations
  - Storage account (Blob) and warehouse database
  - Neo4j Community on Container Apps, two instances
  - Entra roles, fixed customer identifier **(G1)**
- **Lane B — agents**
  - API contract, written and agreed
  - Ontology authoring begins
  - Skill prompt template from `saturam-cr`
  - Model routing policy across the two tiers **(G6)**

#### P1 — Data and knowledge spine · after P0

- **Lane A**
  - Object storage to landing loads
  - Silver conformance for the three sources
  - Metadata harvest executor
- **Lane B**
  - Domain prose ingestion, split on headings
  - Ontology embedded and indexed
  - Unified retrieval entry point

#### P2 — Semantic layer and KPIs · after P1. First convergence point

- **Lane A**
  - Semantic graph write with its own labels
  - KPI registry and feasibility executor
  - Gold materialisation on the scheduler
- **Lane B**
  - Enrichment and mapping proposers
  - Business area detection
  - KPI and use case suggestion agents

#### P3 — Use case chain and RFQ · after P2

- **Lane A**
  - Executor library: preflight, dry run, delivery, provenance
  - RFQ gold tables and funnel logic
- **Lane B**
  - Spec extraction and gap check
  - Plan derivation from the compiled workflow
  - RFQ workflow and its explanation skill

#### P4 — Breadth · after P3. Second use case slots in here

- **Lane A**
  - Dashboard storage and gold reads
  - Monitoring pipeline
  - Guardrails, approval gate, write-back **(G14)**
  - Alert definitions and evaluation **(G7)**
- **Lane B**
  - Playground, four modes
  - Golden question sets
  - Second use case, once chosen

#### P5 — Interface wiring · after design approval. Both lanes converge

- **Lane A**
  - Charting, which does not exist today
  - Dashboard and monitor screens
- **Lane B**
  - Storage helpers replaced with API clients
  - Conversation views on server-sent events

## Validation and gaps

The plan checked against the framework a second time, against every requirement in the flow, and against what an enterprise deployment needs that a demo does not. Twenty-one gaps. Five would have broken a decision already taken. Three were found later, while tracing how domain knowledge resolves ambiguity in the semantic layer.

Critical: breaks a decision or the deliverable Significant: required for enterprise grade Minor: operational

### Critical

#### G1 — Memory is silently disabled under D3

*touches D3, sections 04, 05, 08*

Evidence `agent/_managers.py:575` returns without touching memory when `run_context.tenant_id is None`. Single deployment per customer with tenancy flags off produces exactly that, so every run would proceed memoryless and nothing would report it. The per-tenant builder from the earlier gap report is also still orphaned.

Resolution A single-tenant deployment still carries a fixed tenant identifier, the customer slug, stamped onto every run context by middleware from configuration rather than from a token claim. Wire the per-tenant memory builder in the application factory and add a startup check that fails loudly if any agent resolves with no memory provider.

#### G2 — Workflows have no memory, and scheduled runs have no user

*touches D1, D7, section 07*

Evidence `workflow/workflow.py` carries no memory provider, and teams do not propagate theirs to members. A use case run is a workflow, so its agent steps would run without memory. A nightly run's actor is `__scheduler__`, so user-scoped memory writes are impossible from it.

Resolution Every agent step is its own component under D1, so the memory provider is attached at hydration per agent, not at the workflow. Define a system scope convention: scheduled runs write with tenant and agent set and user left empty, which `Scope` permits since only the tenant is mandatory. Interactive runs add the user. Document which memories a scheduled run may read.

#### G3 — Chat with data can execute writes

*touches section 08*

Evidence `SQLTools.run_sql` executes whatever it is handed. The comment at line 150 acknowledges that inserts, updates and schema changes simply return no rows. There is no statement timeout, and the row limit is a parameter the model controls. The prompt-injection guardrail exists but is not wired anywhere in the plan.

Resolution Two layers. The database role is read-only, which is the floor. Above it, a SQL guard executor that parses each statement, permits only reads, restricts tables to those the semantic layer exposes, applies a statement timeout and a hard row cap, and rejects anything else before execution. Wrap the read engine of the database context provider with it, and attach the prompt-injection guardrail through `pre_hooks` on every playground agent.

#### G4 — Answer provenance does not exist

*touches sections 07, 08, 09*

Evidence The `provenance_report` in the repository is memory-consolidation provenance, tracking which episodes became which facts. It has nothing to do with citing an answer. The plan's provenance step therefore has no implementation behind it, and chat with data has no provenance design at all. Explainability is the stated moat.

Resolution One provenance record per answer, whatever produced it: datasets read, SQL executed, rows sampled, freshness stamps, retrieval hits from memory, prose and graph with their scores, and the model tier used. Written by a single executor every path calls, stored beside the run, and rendered by the citation panel the prototype already has.

#### G5 — Propose and confirm has no mechanism

*touches D10, sections 05, 06*

Evidence D10 says an agent proposes and a person confirms, and the plan never says how. A workflow gate per proposal is the obvious tool and the wrong one: a single source harvest produces hundreds of enrichment proposals, and nobody will click through them one gate at a time.

Resolution A proposals table with kind, target, payload, proposer, status, reviewer and decision time, and an interface for bulk accept and reject. Only accepted rows are written to the graph. The same table serves ontology mapping, KPI adoption from the discovery screen, and use case suggestions, so it is built once.

### Significant

#### G6 — No model routing policy

*touches sections 01, 10*

Small-model-first escalation is listed as built and never used. The plan does not say which tier runs which step, and the efficiency argument to leadership depends on that being deliberate. Resolution: a routing table. Deterministic steps use no model. Explanation and spec extraction run on the in-deployment small tier through the Azure AI Foundry provider, with rule-based escalation. Enrichment and suggestion run on the frontier tier, since they are rare and quality-critical.

#### G7 — Alerts are a menu with nothing behind it

*touches D12, sections 07, 10, 11*

RFQ is explain-only, monitoring covers health and quality, and nothing evaluates a KPI against a threshold. The prototype has an Alerts screen that must work. Resolution: an alert definition holding KPI, operator, threshold, scope, severity and recipient; an evaluation executor that runs after materialisation; and delivery through the same path as results. This is the watch-and-notify level of the autonomy axis, built as reusable machinery in P4.

#### G8 — Silver conformance and data quality are unspecified

*touches D6, section 03*

The plan names five conformed entities and no rules for producing them: keys, deduplication, type coercion, late-arriving rows. Nothing checks the data before gold is built from it. Resolution: a conformance specification per entity, kept as SQL beside the transforms, and quality checks that gate gold: row count delta against the prior load, null rates on key columns, and referential integrity between entities. Failures surface in the KPI health screen rather than in a log.

#### G9 — No semantic layer versioning or schema evolution

*touches D8, sections 05, 06*

A client adds a property, a connector adds a column, and the semantic layer has no notion of before and after. A KPI formula validated against one harvest can break silently on the next. Resolution: each harvest produces a versioned snapshot; snapshots are diffed; every KPI records the semantic version it was validated against; a changed or removed column moves dependent KPIs to a review state instead of letting them fail at midnight.

#### G10 — Ontology extension is undefined

*touches D9, section 04*

One shipped ontology that customers extend, with no mechanism for extending it or for upgrading the core underneath an extension. Resolution: core and overlay in the same database, every node and edge carrying an origin property. The overlay may add and may annotate, never delete. A core upgrade re-applies the overlay, and conflicts land in the proposals table from G5.

#### G11 — No role model

*touches section 02*

The framework offers scope strings and the plan promises a mapping without defining roles. Resolution: three roles to start. Viewer reads dashboards and results. Analyst additionally authors use cases and KPIs. Admin additionally activates level three and above and manages sources. Separation of duties at activation is enforced by role, matching what the activation screen already shows.

#### G12 — Chat with a use case has nothing to read

*touches sections 07, 08*

Runs are stored as one JSON column on the session row, and a scheduled workflow run has no user session. Asking why last Tuesday's run said what it said has no index to hit. Resolution: a use case runs table keyed by use case and version, holding status, output reference and provenance reference, with outputs ingested into the knowledge base tagged by use case so the unified retrieval from section 04 can reach them.

#### G13 — Evals cover use cases and nothing else

*touches D14, section 10*

Suggestion, enrichment and spec extraction are agents with prompts that will be edited, and nothing measures whether an edit made them worse. Resolution: a golden set per agent skill, run on publish of that agent component, using the same scorers.

#### G14 — Second-level executors are in no lane

*touches sequencing*

Guardrail evaluation, the approval gate and write-back are named in the inventory as needed by the second use case and appear in no phase. Resolution: add them to Lane A in P4, so the second use case, whatever its level, has its machinery ready when it is chosen.

#### G19 — Column-name collisions are invisible

*touches sections 05, 06*

Evidence `SQLTools.describe_table` returns name, type, nullable and default, and nothing else. A model looking at an `amount` column in two tables has no signal to tell them apart, and neither does the enrichment proposer. Nothing in the harvest notices that the same column name occurs in more than one table.

Resolution Detect collisions deterministically at harvest and again on every snapshot diff. Any column name appearing in more than one table is pushed into the proposals queue as a mapping that must be reviewed, rather than passing silently. Cheap, deterministic, and it converts an invisible problem into a review item before a KPI is built on top of it.

#### G20 — Nothing declares an authoritative source

*touches sections 05, 06, 08*

Evidence Where two tables can answer the same question, the semantic layer expresses no preference. An agent picks one and sounds equally confident either way, which is worse than being unable to answer.

Resolution An authoritative flag per concept on the semantic layer, set during enrichment review. When a second source could serve, it is used only with a stated reason, and the provenance record names which source answered and why.

#### G21 — Chat with data is not grounded in the semantic layer

*touches section 08*

Evidence The database context provider hands `SQLTools` to a sub-agent whose instructions carry a schema name and nothing more. As planned, chat with data inherits exactly the blindness in **G19**: raw column names, no concepts, no synonyms, no scoping filters, no sense of which table is authoritative.

Resolution Inject the semantic layer into the provider's instructions, so the sub-agent sees concepts and their synonyms rather than only a schema. This is the difference between a text-to-SQL demo and a system that answers in the customer's own vocabulary, and it is the gap that would bite soonest.

### Minor

#### G15 — Azure operational surface

*touches section 02*

Secrets in Key Vault, private endpoints for both databases and the graph, traces to Postgres in phase one since the exporter has no native Azure Monitor path, email through Azure Communication Services since the built-in email tools target other clouds, and scheduled backups for three data stores on three cadences.

#### G16 — Three prototype menus are unmapped

*touches section 11*

Processes, Analytics and Monitor exist in the prototype navigation and appear nowhere in the wiring table. Every screen must work, so each needs a backing or a deliberate decision to retire it before design approval.

#### G17 — Customer documents

*touches sections 04, 08*

The knowledge base is described as shipped retail prose only. Chat with the knowledge base almost certainly means the customer's own documents too. Resolution: ingestion through the existing knowledge endpoints, tagged by origin so shipped and customer content can be told apart in provenance.

#### G18 — Testing and conventions

*touches every section*

No test strategy, and the repository's own rules require a cookbook and test log for every new pattern. Resolution: unit tests per executor, contract tests against the API written in P0, one end-to-end test of the chain on the sample data, and a cookbook per new primitive.

## Azure alignment

The plan checked against the Azure deployment architecture. Where they agree, nothing to do. Where they differ, the table says which document changes and why.

### Aligned

PostgreSQL Flexible Server with pgvector as the only vector store that needs no new code. Neo4j as the only graph backend. Azure OpenAI for the frontier tier. Single-tenant in phase one, the tenancy flags untested rather than trusted. Key Vault, App Configuration and workload identity with no code change. Entra app roles mapped to scope sets, which is exactly the role model **G11** asked for. KPI definitions and dashboards in governed Postgres tables and never in memory. The in-process scheduler safe across replicas. The consolidation worker pinned to one replica. No Dockerfile, compose file or chart existing today.

### Differences

| Topic | Azure architecture says | This plan says | Resolution |
|---|---|---|---|
| **Data platform** | One Postgres instance serving five control-plane roles. No object storage, no warehouse, no landing, silver or gold anywhere | Raw drops in ADLS Gen2 **(D4)**, a separate warehouse database **(D5)**, transforms in SQL **(D6)** | **Azure document changes.** Add a storage account with a private endpoint, and a warehouse database, preferably its own Flexible Server. Analytical transforms contending with agent state is the functional reason to split that the architecture says does not exist. This is the largest gap |
| **Configure pipelines in phase one** | Open assumption four recommends sequencing phase one without the Connect flow unless the demo needs it | Connect, semantic layer and suggestion are the core of the flow requirement | **Azure document changes.** The demo needs the Connect flow. Pipelines are phase one |
| **Pipeline mechanism** | Service Bus, a custom consumer, a Kubernetes Job per Connect click, a CronJob for scheduled work | Harvest, enrichment and refresh are EGPT workflows: named executors plus agent steps, so run history, audit, provenance and retries come free | **Both change.** Keep the logic as workflows. Run them on a dedicated pipelines Deployment of the same image rather than the API pods, by pointing the scheduler's callback and the Connect-click run at that Deployment. Connect is already `POST /workflows/{id}/runs` with `background=True`. This removes Service Bus, the consumer and Job orchestration from phase one, three things we would otherwise author |
| **Semantic layer home** | Pipeline output lands in governed Postgres tables, never in memory, because decay and consolidation are wrong for a definition | Semantic model in Neo4j with its own labels, then selected facts promoted into semantic memory **(D11)** | **Compatible once stated precisely, and D11 is reworded.** The authoritative semantic model lives in Neo4j under its own labels, outside memory's decay semantics; versioned snapshots and KPI definitions live in the governed Postgres tables. What enters memory is a derived, re-promotable copy, never the source of truth. The Azure document should list Neo4j as a semantic layer home alongside Postgres |
| **Small model tier** | Dedicated GPU node pool, two A100 nodes, vLLM, model family open | A routing policy **(G6)** naming which tier runs which step | **This plan defers the GPU pool.** The escalation provider composes any two providers, so two Azure OpenAI deployments, small and frontier, exercise the same code path as in-cluster serving. Two A100 nodes for one customer's phase-one demo is the single most expensive line in the architecture. Add the pool when an in-network inference requirement is real, not before. Needs your confirmation |
| **Interface topology** | The prototype's Node API layer is kept, and its run handler drains the whole event stream server-side before responding | Screens call EgptOS directly through the contract in section 02, with conversations on server-sent events | **Both change.** Once Entra issues tokens, the bespoke auth routes go away and the Node layer shrinks to almost nothing. Stream directly from the EgptOS run endpoints through the edge; never drain a stream in a proxy. Keep a thin layer only if a cookie-to-token exchange is genuinely needed |
| **Two Neo4j databases** | One causal cluster, Enterprise edition | Two databases in that cluster | **Superseded by D15.** No longer one Enterprise cluster with two databases inside it — two separate Community instances on Container Apps, which is what local dev already runs. The licence question this row raised no longer applies |
| **Memory under single tenancy** | Tenancy flags off in phase one | **G1**: with no tenant on the run, memory silently no-ops | **Azure document adds a setting.** A fixed customer identifier in App Configuration, stamped on every run context by middleware. Without it the deployment is memoryless and nothing reports it |
| **Email** | Not mentioned | In-app plus email **(D13)** | Add Azure Communication Services |
| **Neo4j backups** | Full control over backup, no mechanism | Three stores on three cadences | A backup job writing to the storage account |
| **Compute platform** | AKS | Previously said Container Apps | **This plan changed twice now, and the second change is the current state.** Section 02 followed AKS for a period; **D15 reverses to Functions (`api`) + Container Apps (everything else)** — the architecture's own original position, not a fresh third option. See D15 above and `docs/plan/azure-migration-plan.md` for why the reversal, not just that it happened |

> **Net effect on the architecture**
>
> Two additions the architecture cannot do without: the storage account and the warehouse. Three subtractions that make it operable by two people: Service Bus and its consumer, the GPU pool, and most of the Node layer. Everything else is a line item.

## Risks

| Risk | Why it matters | Handling |
|---|---|---|
| **Sample data shapes differ from production** | The semantic layer is built over what we are given **(D8)**. A shape change invalidates enrichment and every KPI formula built on it | Treat the harvest as re-runnable from day one, and diff harvests between environments |
| **Three funnel events do not exist yet** | The RFQ use case is defined over events the client has not instrumented | Raised in the connector hand-off. Until they ship, the funnel is partial and should be labelled as such rather than filled in |
| **Identity hash is gated on privacy sign-off** | Not an engineering dependency, so it will not resolve by working harder | Escalate early. Sequence any cross-system work behind it |
| **The graph substrate cannot express a semantic layer** | Its labels are fixed and its query filters are refused | Resolved by **D11**, at the cost of a second write path and a promotion step |
| **Enterprise GPT is becoming a data platform** | **D6** brings scheduling, backfills, late-arriving data and correctness into our scope | Accepted deliberately. Keep transformations as inspectable SQL, never as code, so they can be handed over later |
| **The interface is the largest single body of work** | Every prototype screen must work, and charting does not exist at all | Contract first, wiring last, and the seam through the storage helpers keeps it mechanical |
| **The platform surface exceeds the team** *(resolved by D15)* | AKS with three node pools, a self-hosted graph cluster, KEDA, PgBouncer and a Helm chart are operated by the same two people writing the product | This risk is what D15 acted on rather than continuing to accept — Functions and Container Apps remove the cluster, node-pool and KEDA surface entirely. Author as little as possible still applies to what remains; defer the GPU pool and the event bus |
| **Two people, no slack** | Both lanes converge at P2 and again at P5 | Those two points are where a slip propagates. Protect them, and let breadth in P4 absorb pressure instead |
