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

> **Open**
>
> The second use case is deliberately unassigned. The design below carries it as a slot, so adding it is configuration and a workflow definition rather than new machinery.
>
> Deterministic, governed and repeatable work becomes workflow steps backed by named Python executors. Judgment-heavy work becomes an agent step whose prompt is a skill. Anything that can permit or block an action is always a deterministic step, never a model. The model explains and proposes; it is not the thing that says yes.
>
> This survives the move to generated use cases in phase two. `Step.from_dict` rehydrates agents by identifier from the registry or database, but resolves deterministic executors *by name*. A generated use case can therefore only compose executors that already exist. Building that named executor library is precisely what makes the two hand-built use cases generalise, which was the requirement placed on them.
>
> A use case is three artifacts: a stored workflow component, the skill prompts its agent steps carry, and a spec record holding what the author asked for. Two of the three already have a control plane.
>
> A managed Kubernetes cluster with three node pools, a self-hosted graph cluster, PgBouncer, KEDA and a Helm chart is more platform than the product needs in phase one, and every piece of it is operated by the same two people building the product. The architecture is sound and it is the standing decision, so the plan follows it. The mitigation is in the alignment section: keep the number of things we author small, and defer the GPU pool and the event bus until something needs them.

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

> **Constraint worth carrying into estimates**
>
> The existing graph substrate cannot express this. Its labels are fixed at Episode, Fact and Entity, every write demands a memory tier and a five-slot scope, and populating a query filter raises an error. Under **D11** the semantic layer gets its own labels and its own read surface, written directly against Neo4j, with promotion into memory as a second step.
>
> Feasibility must never be a model judgment. It is a set difference, and the moment a model decides whether a KPI is supported, the badges in the interface stop meaning anything. The suggestion agent proposes; the executor decides. The suggestion output carries an identifier, name, feasibility, the missing tables where blocked, a priority and one line of rationale, because that is what the discovery screen renders without transformation.
>
> The plan screen currently describes the run in parallel with the workflow instead of reading from it. In the product it must be a view over the compiled workflow, so no step can appear on screen that does not exist in the thing that runs. Small change, large trust payoff, and it is the difference between a diagram and an audit.
>
> Because one agent faces four different jobs here, loading the right playbook on demand is useful rather than ceremonial. Under **D3** those playbooks can live on disk, since there is one customer per deployment.
>
> A dashboard is a named set of KPI references with layout, filters, a refresh schedule and a visibility rule. Storage is a table in the control-plane database. Reads go to gold, never to silver and never to a live aggregation, which is what keeps a four-tile dashboard from issuing four warehouse scans on every open.
>
> Charting does not exist in the framework in any form, so rendering is entirely new work and it sits on the interface side. That places most of this section's cost inside section 11 rather than here.
>
> **Monitoring** is a pipeline of its own **(D14)**. Execution health comes free from persisted run history: failures, runs that stopped firing, cost drift, gates waiting too long on a person. On top of that sits the part that needs building, an assessment pass over output quality and input drift, because a use case that runs green while quietly returning worse answers is the failure mode that execution monitoring cannot see.
>
> **Evals** gate publication. Each use case carries a set of golden questions with agreed answers, run automatically against every version, using the accuracy and reliability scorers that already exist. A person still reviews the rehearsal before activation. Automated checks catch regressions from a prompt edit; the human review catches the thing nobody thought to write a question about.
>
> Every screen in the prototype must work in the deliverable. The prototype currently reads and writes through a small set of local storage helpers, and that is a clean seam: swapping those for API clients against the contract fixed in section 02 is mechanical rather than exploratory. Conversation views move to server-sent events from the run endpoints, and history comes from the sessions endpoints.

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

## Validation and gaps

The plan checked against the framework a second time, against every requirement in the flow, and against what an enterprise deployment needs that a demo does not. Eighteen gaps. Five would have broken a decision already taken.

Critical: breaks a decision or the deliverable Significant: required for enterprise grade Minor: operational

### Critical

Evidence `agent/_managers.py:575` returns without touching memory when `run_context.tenant_id is None`. Single deployment per customer with tenancy flags off produces exactly that, so every run would proceed memoryless and nothing would report it. The per-tenant builder from the earlier gap report is also still orphaned.

Resolution A single-tenant deployment still carries a fixed tenant identifier, the customer slug, stamped onto every run context by middleware from configuration rather than from a token claim. Wire the per-tenant memory builder in the application factory and add a startup check that fails loudly if any agent resolves with no memory provider.

Evidence `workflow/workflow.py` carries no memory provider, and teams do not propagate theirs to members. A use case run is a workflow, so its agent steps would run without memory. A nightly run's actor is `__scheduler__`, so user-scoped memory writes are impossible from it.

Resolution Every agent step is its own component under D1, so the memory provider is attached at hydration per agent, not at the workflow. Define a system scope convention: scheduled runs write with tenant and agent set and user left empty, which `Scope` permits since only the tenant is mandatory. Interactive runs add the user. Document which memories a scheduled run may read.

Evidence `SQLTools.run_sql` executes whatever it is handed. The comment at line 150 acknowledges that inserts, updates and schema changes simply return no rows. There is no statement timeout, and the row limit is a parameter the model controls. The prompt-injection guardrail exists but is not wired anywhere in the plan.

Resolution Two layers. The database role is read-only, which is the floor. Above it, a SQL guard executor that parses each statement, permits only reads, restricts tables to those the semantic layer exposes, applies a statement timeout and a hard row cap, and rejects anything else before execution. Wrap the read engine of the database context provider with it, and attach the prompt-injection guardrail through `pre_hooks` on every playground agent.

Evidence The `provenance_report` in the repository is memory-consolidation provenance, tracking which episodes became which facts. It has nothing to do with citing an answer. The plan's provenance step therefore has no implementation behind it, and chat with data has no provenance design at all. Explainability is the stated moat.

Resolution One provenance record per answer, whatever produced it: datasets read, SQL executed, rows sampled, freshness stamps, retrieval hits from memory, prose and graph with their scores, and the model tier used. Written by a single executor every path calls, stored beside the run, and rendered by the citation panel the prototype already has.

Evidence D10 says an agent proposes and a person confirms, and the plan never says how. A workflow gate per proposal is the obvious tool and the wrong one: a single source harvest produces hundreds of enrichment proposals, and nobody will click through them one gate at a time.

Resolution A proposals table with kind, target, payload, proposer, status, reviewer and decision time, and an interface for bulk accept and reject. Only accepted rows are written to the graph. The same table serves ontology mapping, KPI adoption from the discovery screen, and use case suggestions, so it is built once.

### Significant

Small-model-first escalation is listed as built and never used. The plan does not say which tier runs which step, and the efficiency argument to leadership depends on that being deliberate. Resolution: a routing table. Deterministic steps use no model. Explanation and spec extraction run on the in-deployment small tier through the Azure AI Foundry provider, with rule-based escalation. Enrichment and suggestion run on the frontier tier, since they are rare and quality-critical.

RFQ is explain-only, monitoring covers health and quality, and nothing evaluates a KPI against a threshold. The prototype has an Alerts screen that must work. Resolution: an alert definition holding KPI, operator, threshold, scope, severity and recipient; an evaluation executor that runs after materialisation; and delivery through the same path as results. This is the watch-and-notify level of the autonomy axis, built as reusable machinery in P4.

The plan names five conformed entities and no rules for producing them: keys, deduplication, type coercion, late-arriving rows. Nothing checks the data before gold is built from it. Resolution: a conformance specification per entity, kept as SQL beside the transforms, and quality checks that gate gold: row count delta against the prior load, null rates on key columns, and referential integrity between entities. Failures surface in the KPI health screen rather than in a log.

A client adds a property, a connector adds a column, and the semantic layer has no notion of before and after. A KPI formula validated against one harvest can break silently on the next. Resolution: each harvest produces a versioned snapshot; snapshots are diffed; every KPI records the semantic version it was validated against; a changed or removed column moves dependent KPIs to a review state instead of letting them fail at midnight.

One shipped ontology that customers extend, with no mechanism for extending it or for upgrading the core underneath an extension. Resolution: core and overlay in the same database, every node and edge carrying an origin property. The overlay may add and may annotate, never delete. A core upgrade re-applies the overlay, and conflicts land in the proposals table from G5.

The framework offers scope strings and the plan promises a mapping without defining roles. Resolution: three roles to start. Viewer reads dashboards and results. Analyst additionally authors use cases and KPIs. Admin additionally activates level three and above and manages sources. Separation of duties at activation is enforced by role, matching what the activation screen already shows.

Runs are stored as one JSON column on the session row, and a scheduled workflow run has no user session. Asking why last Tuesday's run said what it said has no index to hit. Resolution: a use case runs table keyed by use case and version, holding status, output reference and provenance reference, with outputs ingested into the knowledge base tagged by use case so the unified retrieval from section 04 can reach them.

Suggestion, enrichment and spec extraction are agents with prompts that will be edited, and nothing measures whether an edit made them worse. Resolution: a golden set per agent skill, run on publish of that agent component, using the same scorers.

Guardrail evaluation, the approval gate and write-back are named in the inventory as needed by the second use case and appear in no phase. Resolution: add them to Lane A in P4, so the second use case, whatever its level, has its machinery ready when it is chosen.

### Minor

Secrets in Key Vault, private endpoints for both databases and the graph, traces to Postgres in phase one since the exporter has no native Azure Monitor path, email through Azure Communication Services since the built-in email tools target other clouds, and scheduled backups for three data stores on three cadences.

Processes, Analytics and Monitor exist in the prototype navigation and appear nowhere in the wiring table. Every screen must work, so each needs a backing or a deliberate decision to retire it before design approval.

The knowledge base is described as shipped retail prose only. Chat with the knowledge base almost certainly means the customer's own documents too. Resolution: ingestion through the existing knowledge endpoints, tagged by origin so shipped and customer content can be told apart in provenance.

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
| **Two Neo4j databases** | One causal cluster, Enterprise edition | Two databases in that cluster | **Azure document adds a line.** Enterprise supports multiple databases, so this is configuration. Note the licence |
| **Memory under single tenancy** | Tenancy flags off in phase one | **G1**: with no tenant on the run, memory silently no-ops | **Azure document adds a setting.** A fixed customer identifier in App Configuration, stamped on every run context by middleware. Without it the deployment is memoryless and nothing reports it |
| **Email** | Not mentioned | In-app plus email **(D13)** | Add Azure Communication Services |
| **Neo4j backups** | Full control over backup, no mechanism | Three stores on three cadences | A backup job writing to the storage account |
| **Compute platform** | AKS | Previously said Container Apps | **This plan changed.** Section 02 now follows AKS |

Two additions the architecture cannot do without: the storage account and the warehouse. Three subtractions that make it operable by two people: Service Bus and its consumer, the GPU pool, and most of the Node layer. Everything else is a line item.

## Risks

| Risk | Why it matters | Handling |
|---|---|---|
| **Sample data shapes differ from production** | The semantic layer is built over what we are given **(D8)**. A shape change invalidates enrichment and every KPI formula built on it | Treat the harvest as re-runnable from day one, and diff harvests between environments |
| **Three funnel events do not exist yet** | The RFQ use case is defined over events the client has not instrumented | Raised in the connector hand-off. Until they ship, the funnel is partial and should be labelled as such rather than filled in |
| **Identity hash is gated on privacy sign-off** | Not an engineering dependency, so it will not resolve by working harder | Escalate early. Sequence any cross-system work behind it |
| **The graph substrate cannot express a semantic layer** | Its labels are fixed and its query filters are refused | Resolved by **D11**, at the cost of a second write path and a promotion step |
| **Enterprise GPT is becoming a data platform** | **D6** brings scheduling, backfills, late-arriving data and correctness into our scope | Accepted deliberately. Keep transformations as inspectable SQL, never as code, so they can be handed over later |
| **The interface is the largest single body of work** | Every prototype screen must work, and charting does not exist at all | Contract first, wiring last, and the seam through the storage helpers keeps it mechanical |
| **The platform surface exceeds the team** | AKS with three node pools, a self-hosted graph cluster, KEDA, PgBouncer and a Helm chart are operated by the same two people writing the product | Author as little as possible. Defer the GPU pool and the event bus. Revisit if operations start displacing build time |
| **Two people, no slack** | Both lanes converge at P2 and again at P5 | Those two points are where a slip propagates. Protect them, and let breadth in P4 absorb pressure instead |
