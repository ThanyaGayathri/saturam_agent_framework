# Enterprise GPT Delivery Plan

_Enterprise GPT Delivery Plan_

> Who builds what, in what order, against which contracts, and the scenario each person runs by hand to prove their piece works before it is pushed.

## How the split works

Three lanes, divided so nobody has to explain what they already know to someone else. The boundary follows where knowledge already sits, not where the architecture layers happen to sit.

**Lane K · Knowledge** — 

**Lane R · Runtime** — 

**Lane C · Connectors** — 

Lane K is the moat. The enrichment that makes a customer's data understandable is organisation-specific and not replicable, which is the reason it is worth building at all, and it is the one lane where a handover would cost more than it saves. Lane R is broader and more mechanical, and it owns everything a demo audience actually sees. Neither is a support role for the other.

**Two boundaries worth knowing about, because they could reasonably sit elsewhere.** KPI work splits at the formula: Lane K owns the definition, feasibility and suggestion, Lane R owns nightly materialisation, because materialisation shares all its machinery with the silver loader. Use case suggestion sits in Lane K despite not being memory, domain, semantic or KPI work, because it shares every input and its whole output discipline with KPI suggestion and splitting them would duplicate both.

## The five contracts

These are what let three people work at once. Each is written and agreed in phase zero, before the code that depends on it. A contract change after that is a conversation, not a commit.

| Contract | Between | What it fixes | Written by |
|---|---|---|---|
| **Landing contract** | C → R | Storage path layout, file format, one row per what, key columns, the watermark column, and how a re-run behaves. Per source. | C, reviewed by R |
| **Semantic read API** | K → R | How anything asks what tables, columns, concepts and joins exist, and what a concept's synonyms and filters are. Called by every agent and every preflight check. | K, reviewed by R |
| **KPI formula shape** | K → R | Name, description, formula with its filter parameter, grain, source tables, business area. The seam where a definition becomes something a nightly job executes. Already proven in the earlier graph retrieval work. | K, reviewed by R |
| **Executor registry** | K R | Names and signatures of every deterministic step a workflow may call. Both lanes author executors; both compose only from this list, which is what makes generated use cases possible later. | K and R jointly |
| **API contract** | R K → interface | Every endpoint the screens will call, with request and response shapes. Written now, wired in the last phase. | R, reviewed by K |

Each removes a blocking dependency. With the landing contract agreed, Lane C builds all three connectors before a warehouse exists and Lane R builds the silver layer against sample files before a connector runs. With the semantic read API agreed and stubbed, Lane R builds preflight, the playground and the spec screen before the real graph exists. With the formula shape agreed, gold materialisation is built before a single KPI is authored.

### Which of the five will actually change

A contract written before the work is a hypothesis. Some are proven wrong only by building against them, and knowing in advance which ones are fragile is worth more than trying to make them all perfect.

| Contract | Survives P0? | Where it breaks |
|---|---|---|
| **Landing** | High | It is file shape, and it is proven in P0 by a real load of real sample files. Concrete enough to be right the first time. |
| **API** | Medium, and cheap to be wrong | Interface wiring is P5, so four phases of slack absorb any fix. Being wrong here costs almost nothing. |
| **Semantic read API** | Medium | The shape holds; the meaning moves. Whether it returns the enriched view or the raw harvest, and how an unconfirmed proposal appears, both surface in P2. |
| **KPI formula shape** | Low | The one to watch. Whether the formula carries its own grain, how multiple filter parameters work, how time windows are expressed, and whether a run recomputes or appends. All of it is discovered at first materialisation in P3. |
| **Executor registry** | Not applicable | A convention, not a contract. The signature shape is fixed in P0 and the list grows all the way through the build. |

Four contracts and one convention. Treating the executor registry as something that gets finished in phase zero is the mistake to avoid, because it will still be growing in phase four.

### The change protocol

Without one, a contract becomes either a straitjacket people quietly route around, or a fiction people edit without telling anyone. Six rules, and the sixth is the one that gets skipped under pressure.

1. **Additive changes are free.** A new optional field, a new endpoint, a new executor. Announce it, do not negotiate it.
2. **Breaking changes need both lanes to agree** before the merge, and a version bump on the contract.
3. **Whoever finds it wrong proposes the fix.** A contract defect raised without a proposed shape is a complaint rather than a change request.
4. **The stub moves with the contract.** If the semantic read API changes, its stub changes in the same commit, or the other lane spends a week building against a lie.
5. **Every change is recorded in one place** per contract, appended to, with the date, the reason and who agreed. Three lines is enough.
6. **A contract never changes silently.** This is item seven of the definition of done, and it is the first thing to go when a deadline is close.

### What makes the handover hold

- **A running stub beats a document.** A contract nobody can execute is one everybody interprets differently. This is why the semantic read API stub is a phase zero deliverable rather than a specification of one.
- **Example payloads come from the sample data, never invented.** Invented payloads hide the awkward cases, and the awkward cases are the only ones worth agreeing on in advance.
- **Each contract has one named owner** who decides when it changes. Joint ownership of a decision usually means nobody makes it.

The three sync rows below are where contracts meet reality: agreed at P0, the stub replaced at P2, the formula shape populated at P3. Expect a change at each one, and treat that as the process working rather than as a planning failure.

And do not over-invest in phase zero. Five perfect contracts is a fortnight with nobody building anything. Good enough plus a running stub will teach you more in a day than another review round will. Contracts buy roughly eighty percent independence; the last twenty is discovery, and no amount of design up front removes it.

## Where the code lives

One repository, with a visible line between the framework and the retail product. The line is a directory boundary rather than a repository boundary, which is enough because the packaging boundary already enforces it.

```
saturam-agent-framework/
├── libs/agno/egpt/     framework · ships as `pip install egpt`
├── frontend/           the React application
├── product/            retail product code
├── deploy/             Dockerfile, Helm chart, compose
└── cookbook/ docs/ examples/ scripts/ benchmarks/
```

Two mechanisms already keep the framework clean, and neither needs configuring. The wheel is built from `libs/agno/` with `packages.find` including only `egpt*`, so nothing at the repository root can reach a published package. Import-linter declares `root_package = "egpt"`, so its layering contracts govern the framework alone. A root-level `frontend/` and `product/` are invisible to both.

### Framework or product, per package

Four packages are genuine framework fixes and belong inside `libs/agno/egpt/`, because every future deployment inherits them and building retail-only versions would waste the asset. Everything else is retail and lives in `product/`.

| Package | Home | Why |
|---|---|---|
| **K1** memory scope and wiring | `libs/agno/egpt/` | G1 and G2 are framework defects. The tenant middleware and the orphaned memory builder are framework concerns, not retail ones. |
| **K10** unified retrieval | `libs/agno/egpt/` | Making the three-leg orchestrator reachable from knowledge is a framework capability. A retail-only copy of it would be the wrong asset. |
| **R6** SQL guard | `libs/agno/egpt/` | A guarded, read-only SQL surface with a statement timeout and a row cap is not retail-specific. Any deployment that lets a model near a database needs it. |
| **R7** provenance record | `libs/agno/egpt/` | The rest of the executor library is product. The provenance record is not, because every deployment needs an answer it can trace. |
| All other packages | `product/` | Ontology, prose, enrichment prompts, semantic layer, KPI content, silver and gold, workflows, skills, dashboards, alerts, monitoring. |
| Interface, phase five | `frontend/` | The Vite application from the prototype. Its Vercel-style API layer mostly disappears rather than moving, since tokens come from the identity provider and streams pass through the edge directly. |

Path filters in CI, so the Python jobs do not run on frontend-only changes and a frontend job runs on those. Node modules and build output added to `.gitignore`. And `CLAUDE.md` updated to describe this layout and which directory is framework versus product. All three of us work through Claude sessions, so an undocumented layout is one that gets guessed at, and some of those guesses will put retail code inside the published package.

## Sequencing

Six phases, ordered by dependency. Sync rows mark where a lane cannot proceed until another lane's contract or deliverable lands.

| Phase | Lane K — knowledge | Lane R — runtime | Lane C — connectors |
|---|---|---|---|
| P0 | K14 skill template · agree memory scope convention · author three contracts | R1 image · R2 cluster · R3 API contract | C1 landing contract |
| Sync — all five contracts agreed, and the semantic read API stubbed, before P1. Nothing downstream is written against a guess. |  |  |  |
| P1 | K1 memory scope and wiring · K2 domain prose · K3 ontology | R4 landing to silver | C2 analytics · C3 CRM · C4 session replay |
| P2 | K4 harvest · K5 semantic graph and read API · K6 snapshots · K7 proposals | R6 SQL guard · R7 executor library | connectors running nightly |
| Sync — K5 read API live, replacing the stub, before Lane R's preflight and playground work is verified end to end. |  |  |  |
| P3 | K8 proposers · K9 business areas · K10 unified retrieval · K11 KPI registry and feasibility | R5 gold · R8 model routing · R9 spec, gap and plan | C moves into Lane R |
| Sync — K11 formula shape populated before R5 gold materialises anything real. R7 executors before R10 RFQ. |  |  |  |
| P4 | K12 KPI suggestion · K13 use case suggestion | R10 RFQ · R11 playground · R12 alerts · R13 dashboards · R14 monitoring · R15 golden sets | — |
| P5 | Interface wiring, both lanes, after design approval | — |  |

Lane K sits on the critical path for almost everything, and the person owning it is also presenting to leadership, chasing approvals and coordinating the other two. Three mitigations are built into the sequencing above. The semantic read API is stubbed in phase zero so Lane R never waits on the real one. Lane K's deepest packages land in phase two, before the suggestion agents that depend on them. The connector owner joins Lane R from phase three, which is when Lane R's load peaks.

## How each deliverable is verified

Manual verification by the person who wrote it, before it is pushed. The repository already has the convention and every package follows it.

- **Write a runnable script** under `cookbook/` in the folder for its area, following the style of the existing examples. It must run against the real deployment or a local stack, not against mocks.
- **Run the scenario** in the package below, by hand, and read the output rather than assuming it.
- **Record the result** in that folder's `TEST_LOG.md`, using the format already in use: the package identifier, the path of what was run, PASS or FAIL, what was tested, and anything notable.
- **A failing run is recorded too.** A log with only passes is a log nobody trusts.

```
### K11_kpi_feasibility.py

**Package:** K11
**Status:** PASS

**Description:** Ran feasibility over the 42 registry KPIs against the
sample harvest. Deliberately dropped the deals table from the semantic
snapshot first, to force blocked cases.

**Result:** 27 ready, 9 partial, 6 blocked. All 6 blocked named the deals
table as missing. Re-added it, re-ran, blocked went to 0.
quote_conversion_rate needs two tables and only reported the first one
missing -- fixed to report all.
```

Two repository conventions apply on every push and are not optional. Run `./scripts/format.sh` and `./scripts/validate.sh` before opening the pull request, and give every new public method both a synchronous and an asynchronous variant.

## Lane K Knowledge

Fourteen packages covering memory, domain knowledge, the semantic layer, retrieval and KPI definitions.

#### K1 — Memory scope and wiring

*P1 · implements G1, G2*

- **Build** — A fixed customer identifier from configuration, stamped onto every run context by middleware. The per-tenant memory builder wired in the application factory, which is orphaned today. A startup check that fails loudly if any agent resolves without a memory provider. The scope convention for scheduled runs, which carry no user, and a written statement of which memories such a run may read.
- **Verify** — Tell an agent something in one conversation, start a new one, ask it back. Then remove the identifier from configuration and restart. Then trigger a scheduled run and inspect what scope it wrote under.
- **Evidence** — The second conversation recalls the fact. With the identifier removed, startup fails with a clear message rather than starting and silently remembering nothing, which is the whole point of the check. The scheduled run's writes carry tenant and agent with no user, and reading them back from a user conversation behaves as documented.

#### K2 — Domain prose ingestion

*P1*

- **Build** — Curated retail markdown into the knowledge base, split on headings rather than by size, tagged by origin so shipped and customer content can be told apart in provenance later.
- **Verify** — Ingest a document with known headings. Inspect the stored chunks. Search for a phrase sitting in the middle of one section.
- **Evidence** — Chunk boundaries fall on headings and each chunk carries its heading. The search returns that whole section rather than a fragment starting mid-sentence.

#### K3 — Ontology authoring, embedding and indexing

*P1 · implements D9*

- **Build** — The shipped retail ontology as concepts, relations and synonyms, versioned as an artifact. Every node embedded over its name, description and synonyms. Vector, lexical and property indexes built. Core and overlay separated by an origin property so a customer extension survives a core upgrade.
- **Verify** — Search for a term that is a synonym rather than a node name. Search for a plain-language phrase naming no node at all. Add an overlay node, upgrade the core version, and search again.
- **Evidence** — The synonym reaches the right concept and the plain-language phrase reaches a sensible one by meaning. If either works only on exact names, the node embeddings were skipped, which is the usual mistake. The overlay node survives the core upgrade.

#### K4 — Metadata harvest

*P2 · after R4*

- **Build** — A deterministic executor reading tables, columns, types, keys, row counts and sampled values from the warehouse into a harvest record. No model involved.
- **Verify** — Run against the silver schema and compare to the schema by eye. Add a column to one table, re-run, and diff the two harvests.
- **Evidence** — Every table and column present with correct types. The diff names exactly the added column and nothing else.

#### K5 — Semantic graph store and read API

*P2 · after K4 · implements D11*

- **Build** — Its own node labels for table, column, concept and metric, written straight to Neo4j outside the memory substrate, because that substrate's labels are fixed and its query filters are refused. A read API answering what tables exist, what columns a table has, what a concept means, and what joins connect two tables. Promotion of selected facts into semantic memory as a derived, rebuildable copy.
- **Verify** — Write a harvest into the graph. Ask for the columns of one table. Ask for the join path between two tables from different sources. Delete the semantic memory copy and re-run promotion. Confirm the stub from phase zero can be swapped out without changing any caller.
- **Evidence** — Column list matches the harvest. The join path returns, or an honest empty result does, never a guess. Promotion rebuilds the memory copy from the graph, proving the graph is the source of truth. Lane R's callers work unchanged against the real API.

#### K6 — Semantic snapshots and drift

*P2 · implements G9*

- **Build** — Every harvest produces a versioned snapshot. Snapshots are diffed. Every KPI records the semantic version it was validated against. A changed or removed column moves dependent KPIs to a review state.
- **Verify** — Take a snapshot. Rename a column the sample KPIs depend on. Harvest again. Look at those KPIs.
- **Evidence** — The diff names the renamed column. Dependent KPIs enter review before the nightly run, rather than failing during it at two in the morning.

#### K7 — Proposals store and review

*P2 · implements G5, D10*

- **Build** — A proposals table holding kind, target, payload, proposer, status, reviewer and decision time, with bulk accept and reject. Only accepted rows reach the graph. Serves enrichment, ontology mapping, KPI adoption and use case suggestions, so it is built once.
- **Verify** — Insert twenty proposals of two kinds. Accept twelve in one action, reject the rest. Query the graph. Then try to accept an already-rejected proposal.
- **Evidence** — Exactly twelve appear in the graph and the eight rejected appear nowhere. Re-accepting a rejected proposal is refused rather than silently applied.

#### K8 — Enrichment and mapping proposers

*P3 · after K5, K7 · implements D10*

- **Build** — Two agents. One proposes synonyms, question patterns, scoping filters, personal-data fields, compliance tags and candidate metrics per concept. The other proposes which table is an instance of which ontology concept. Both write proposals only, never the graph.
- **Verify** — Run both over the sample harvest. Read twenty proposals by eye and judge each one. Query the graph before reviewing anything.
- **Evidence** — The graph is unchanged until proposals are accepted. How many of the twenty were wrong goes in the log, because that number is the baseline every later prompt change is measured against.

#### K9 — Business area detection

*P3 · after K8*

- **Build** — A deterministic executor, not an agent. Areas exist in the ontology; connecting a source marks which are supported, derived by graph query over confirmed mappings.
- **Verify** — With no source connected, list areas. Connect the sample source, confirm its mappings, list again. Run the detection twice on unchanged data.
- **Evidence** — Areas exist before any source is connected, which is what lets a customer configure them up front. After connecting, exactly the areas reachable from confirmed mappings are supported, and two runs agree exactly.

#### K10 — Unified retrieval

*P3 · after K2, K3, K5 · implements the gap-report finding*

- **Build** — One entry point fanning out across memory, domain prose, the ontology and the semantic layer, fusing with the reciprocal rank fusion that already exists inside the memory subsystem and is currently unreachable from knowledge. Every capability in both lanes calls this rather than retrieving its own way.
- **Verify** — Ask a question needing two sources, for example a domain rule plus a table name. Then disable one leg and ask again.
- **Evidence** — The result contains hits from both sources, each labelled with origin and score. With a leg disabled the answer visibly degrades, proving the leg was contributing rather than decorating.

#### K11 — KPI registry and feasibility

*P3 · after K5*

- **Build** — The registry with name, description, formula and its filter parameter, grain, source tables and business area, authoritative in Postgres and mirrored as graph nodes so a concept reaches its metrics by traversal. A deterministic feasibility executor: set difference between what a formula needs and what the semantic layer has.
- **Verify** — Run feasibility over the whole registry. Remove one table from the snapshot and re-run. Then construct a KPI needing two tables that are both missing.
- **Evidence** — Counts of ready, partial and blocked. Every blocked KPI names its missing tables, and the two-missing case names both rather than only the first found.

#### K12 — KPI suggestion

*P4 · after K10, K11*

- **Build** — An agent proposing candidate KPIs from the ontology, prose and semantic graph. Typed output: identifier, name, feasibility, missing tables where blocked, priority, one line of rationale. Rendered directly by the discovery screen without transformation.
- **Verify** — Run against the sample deployment. Check every suggestion's feasibility against what the feasibility executor independently says. Then run twice on unchanged data.
- **Evidence** — No suggestion carries a feasibility the executor disagrees with, because feasibility is a set difference and never the model's opinion. Two runs propose substantially the same set; wild variation on identical input means the prompt is underspecified.

#### K13 — Use case suggestion

*P4 · after K12*

- **Build** — Same inputs and output discipline as K12, producing use case candidates with feasibility badges and the KPIs each would need.
- **Verify** — Run it, take the top suggestion, and trace by hand which data made it feasible.
- **Evidence** — The trace is possible without asking the model. A suggestion that cannot be traced back to specific tables was invented rather than derived.

#### K14 — Skill template

*P0 · implements D2*

- **Build** — The prompt template every agent step follows, in the shape of the existing code-review skill: explicit inputs, phases, output format, templates. Authored here because Lane K writes four of the seven agents and writes them first.
- **Verify** — Write one trivial agent against the template and run it. Then hand the template to Lane R and have them write one without asking a question.
- **Evidence** — Output conforms to the declared schema. Lane R produces a conforming agent from the template alone, which is the only real test of whether the template is complete.

## Lane R Runtime

Fifteen packages covering the platform, the pipeline, the execution machinery and everything a demo audience sees.

#### R1 — Application image and entrypoints

*P0*

- **Build** — One image with three entrypoints: API, memory consolidator, pipelines worker. A `main.py` constructing the server with agents, workflows, database and scheduler.
- **Verify** — Run locally against a local Postgres. Call health. Call config. Start the consolidator entrypoint with nothing to consolidate.
- **Evidence** — Health returns 200. Config lists the registered agents and workflows. The consolidator logs a clean no-work exit rather than an error.

#### R2 — Cluster and managed services

*P0 · after R1*

- **Build** — Cluster, Helm chart, Postgres with pgvector behind connection pooling, the separate warehouse database, Neo4j with two databases, Redis, Key Vault, identity provider wiring, storage account. Migrations run. The consolidator pinned to one replica, because it has no distributed lock. Also the repository scaffolding from the layout section: the `product/`, `frontend/` and `deploy/` directories, CI path filters, ignore rules, and the `CLAUDE.md` update describing which directory is framework and which is product.
- **Verify** — Deploy into a clean namespace. Sign in through the identity provider and call an endpoint you have a scope for, then one you do not. Kill the API pod. Try to scale the consolidator to two. Then build the wheel and list its contents.
- **Evidence** — All pods running. Granted call 200, ungranted 403. The killed pod is replaced without intervention. The consolidator either refuses to scale or the chart makes it impossible. The built wheel contains no file from `product/` or `frontend/`, which is the check that keeps the framework publishable.

#### R3 — API contract

*P0*

- **Build** — Every endpoint the screens will call, with request and response shapes, covering configure, KPI discovery, use cases, playground, dashboards and monitoring. Published as a schema, not a document. Includes the semantic read API stub Lane K will later replace.
- **Verify** — Stand it up as a stub server returning example payloads. Point one prototype screen at it.
- **Evidence** — The screen renders from the stub without changes to its component code. If it needs a shape the contract lacks, the contract was incomplete and is fixed now rather than in the last phase.

#### R4 — Landing to silver, with quality gates

*P1 · after C1 · implements G8*

- **Build** — Loads from storage into landing, then conformance into the five silver entities: keys, deduplication, type coercion, late-arriving rows. Quality checks that block gold: row count delta against the prior load, null rates on key columns, referential integrity.
- **Verify** — Load the sample files. Load them again. Hand-edit one to null a key column and load it. Load a file a tenth the size of the previous one.
- **Evidence** — The duplicate load changes no counts. The nulled key is caught and named. The undersized load is blocked rather than quietly shrinking gold, with a reason readable without opening a log.

#### R5 — Gold materialisation and schedule

*P3 · after K11 · implements D6, D7*

- **Build** — Nightly materialisation of every ready KPI at its declared grain, executing the formula from Lane K's registry as SQL in the warehouse. Every row stamped with source and computation time. Runs as a scheduled workflow so it inherits retries, run history and the audit trail.
- **Verify** — Trigger the schedule by hand. Query gold for one KPI and compute the same number directly in SQL. Then break one formula deliberately and let the run proceed.
- **Evidence** — The two numbers match and every row carries a stamp. The broken KPI fails alone while the rest materialise, with the failure visible in run history rather than only in logs.

#### R6 — SQL guard

*P2 · implements G3*

- **Build** — A read-only database role, and above it a guard parsing each statement: reads only, tables restricted to those the semantic layer exposes, statement timeout, hard row cap. Wraps the read path of the database provider. The prompt-injection guardrail attached to every playground agent.
- **Verify** — Through the chat interface ask it to delete rows, to read a table outside the semantic layer, and to return a million rows. Then paste text containing an instruction to ignore previous instructions.
- **Evidence** — All four refused, each with a reason the user can act on, and each refusal in the audit trail. No statement reached the database in the first two cases.

#### R7 — Executor library

*P2 · implements G4*

- **Build** — The named deterministic steps every workflow composes: preflight checks, dry run, delivery, and the provenance record. Provenance holds datasets read, SQL executed, rows sampled, freshness stamps, retrieval hits with scores, and the model tier used. Registered by name so a workflow definition can reference them.
- **Verify** — Compose a throwaway workflow from these executors alone, with no agent step, and run it. Open the provenance record. Then run preflight against a use case referencing a blocked KPI.
- **Evidence** — The workflow completes without a model. The provenance record lets someone else reconstruct where every number came from. Preflight blocks and names the reason.

#### R8 — Model routing

*P3 · implements G6*

- **Build** — The routing table naming which model tier serves which step, composed through the existing escalation provider. Deterministic steps use no model. Explanation and spec extraction on the small tier; enrichment and suggestion on the frontier tier.
- **Verify** — Run one agent on both tiers. Send it an input long enough to exceed the small tier's context window.
- **Evidence** — Output conforms on both tiers. The oversized input escalates before the call rather than failing and retrying, and the escalation is visible in the run record.

#### R9 — Spec, gap check and plan

*P3 · after K11*

- **Build** — A model filling the typed spec schema from conversation, values drawn only from closed sets. A deterministic gap check deciding what is still missing for the chosen autonomy level. Plan derived from the compiled workflow, not written alongside it.
- **Verify** — Type a plain sentence describing a use case. Then one naming a metric that does not exist. Then one ambiguous about whether it should act. Then compare the plan on screen to the workflow definition.
- **Evidence** — The spec fills from plain language. The unknown metric becomes a question rather than an invented field. The ambiguous sentence produces a clarifying question rather than a guess. Every step on screen exists in the workflow and none is missing.

#### R10 — RFQ workflow and explanation skill

*P4 · after R5, R7 · implements D12*

- **Build** — The nine-step workflow: schedule, read gold, compute funnel stages, compare to prior period, join friction, explain, record provenance, deliver, score. Eight deterministic steps and one agent step.
- **Verify** — Run end to end on the sample data. Check every number in the explanation against gold. Then break the friction join and run again.
- **Evidence** — Every number traces to a gold row and none appears that is absent from the provenance record. With the join broken, the run reports the gap rather than explaining a funnel it could not see.

#### R11 — Playground, four modes

*P4 · after R6, K10*

- **Build** — Chat with data grounded in the semantic layer, chat with the knowledge base through unified retrieval, chat with a use case scoped to its runs, chat with a business area scoped to its concepts. Skills loaded on demand rather than all at once.
- **Verify** — Ask the same question in all four modes. Ask the use case mode about a specific past run. Ask the business area mode about something outside its area.
- **Evidence** — Each mode answers from its own scope, with provenance. The past run is found by reference rather than by search. The out-of-area question is declined or scoped rather than answered from everything.

#### R12 — Alerts

*P4 · implements G7*

- **Build** — An alert definition holding KPI, operator, threshold, scope, severity and recipient. An evaluation executor running after materialisation. Delivery through the same path as results.
- **Verify** — Set a threshold below the current value so it must fire, and run. Then set it above so it must not, and run again.
- **Evidence** — The first fires and delivers. The second stays silent but still records that it ran and did not fire, so a quiet week is visibly quiet rather than indistinguishable from broken.

#### R13 — Dashboard storage and reads

*P4*

- **Build** — Dashboards as named sets of KPI references with layout, filters, refresh and visibility, stored in the control-plane database. Reads go to gold only.
- **Verify** — Create a dashboard of four KPIs through the API. Load it while watching the database. Then delete a KPI it references and load again.
- **Evidence** — Loading reads gold and never silver or a live aggregation. The missing KPI renders as unavailable rather than breaking the dashboard.

#### R14 — Monitoring pipeline

*P4 · implements D14*

- **Build** — Execution health from run history: failures, runs that stopped firing, cost drift, gates waiting too long. Plus the assessment pass over output quality and input drift.
- **Verify** — Disable a schedule and wait past its window. Leave a run's output unchanged but degrade its input data. Then let a run fail.
- **Evidence** — The silent schedule is flagged, which failure monitoring alone would miss. The degraded input is flagged even though the run succeeded, which is the failure mode execution monitoring cannot see. The failure is flagged with its reason.

#### R15 — Golden sets and publish gate

*P4 · implements D14, G13*

- **Build** — A golden question set per use case and per agent skill, including Lane K's, scored with the existing eval suite and run on publish. Rehearsal review by a person still gates activation.
- **Verify** — Record a baseline. Deliberately weaken one prompt, publish, and watch the gate. Then restore it.
- **Evidence** — The weakened prompt is caught at publish with an attributable score drop. If it passes, the golden set is too easy and must be strengthened before this package is done.

## Lane C Connectors

Four packages, independent of the other lanes once C1 is agreed. From phase three this person joins Lane R, where they will know the source data better than anyone.

#### C1 — Landing contract and storage layout

*P0 · implements D4*

- **Build** — A written contract per source: storage path convention with a date partition, file format, what one row represents, key columns, the watermark column for incremental pulls, and the behaviour when a window is pulled twice.
- **Verify** — Hand-write one conforming sample file per source, upload it, and have Lane R load all three.
- **Evidence** — Lane R loads all three without asking a question. A question means the contract is incomplete.

#### C2 — Analytics connector

*P1 · after C1*

- **Build** — Event-grain extraction with user and session identifiers, event name, timestamp, and the parameters carrying brand and product identifier. Incremental by event date.
- **Verify** — Pull a three-day window. Pull the same window again. Then pull an overlapping window. Count rows after each.
- **Evidence** — The second run adds no duplicates. The overlapping run adds only new days. Counts match the source's own reporting within a stated tolerance.
- **Note** — Three RFQ funnel events do not exist in the client's setup yet. The connector must survive their absence and report which expected events were missing, rather than silently producing a short funnel.

#### C3 — CRM connector

*P1 · after C1*

- **Build** — Contacts, deals and deal stage history so funnel timing is reconstructable. Two properties are load-bearing and inconsistent today: brand as an enforced value, and the referring product URL on every quote request. Incremental by last modified.
- **Verify** — Pull a full window. Query for deals with empty or free-text brand, and quote requests with no source URL. Change one deal at source, re-run.
- **Evidence** — A count of records failing each rule, reported rather than dropped. The incremental re-run updates exactly one row.

#### C4 — Session replay connector

*P1 · after C1*

- **Build** — One row per session with rage clicks, dead clicks, script errors, the session URL, and whatever identity is set at login and at submission. Incremental by session start.
- **Verify** — Pull a window. Take three sessions from the landed data and open their recordings in the source tool.
- **Evidence** — Friction counts match what the recordings show, checked by eye on all three.

All three need one shared identity key, a hash of email, written into every system. It is gated on privacy sign-off rather than build effort. Until it exists each connector lands its own identifiers and nothing joins across sources. Lane K should chase this, since that is the lane already talking to leadership. Raise it in week one; it will not resolve by working harder.

## Definition of done

A package is done when all eight are true. Not seven.

1. **The scenario in the package has been run by hand** and the output read, not assumed.
2. **The result is recorded** in the relevant `TEST_LOG.md` with the package identifier, including anything that surprised you.
3. **A runnable script exists** under `cookbook/` so the next person can repeat the scenario without reconstructing it.
4. **Both variants exist** for every new public method, synchronous and asynchronous, per the repository's standing rule.
5. **Formatting and validation pass** using the two repository scripts, before the pull request is opened.
6. **The pull request names the decisions and gaps it implements** by identifier, so a reviewer checks the work against what was agreed rather than against taste.
7. **Nothing retail landed inside `libs/agno/egpt/`.** Product code goes in `product/`, so the framework stays publishable and its layering contracts stay meaningful.
8. **No contract changed silently.** If one of the five had to move, the other lane agreed before the merge and the change is recorded, per the change protocol.

## Alignment rules

Nine rules that keep three people building the same system. Each exists because breaking it silently produces something that still appears to work.

| Rule | Why |
|---|---|
| **Anything that permits or blocks is an executor, never a model.** | The model explains and proposes. The moment it decides what is allowed, the guarantee is gone and no test will show it. |
| **Feasibility is a set difference.** | If a model judges whether a KPI is supported, the ready and blocked badges stop meaning anything to the customer. |
| **Agent output is a typed schema, and values come from closed sets.** | An agent that can invent a metric name will, and it will be plausible. |
| **Nothing enters the graph unproposed and unconfirmed.** | A wrong synonym or scoping filter silently corrupts every answer built on it afterwards. |
| **Every gold row carries its source and computation time.** | Without it a stale answer is indistinguishable from a wrong one. |
| **Every answer writes a provenance record.** | Explainability is the product's stated advantage, and it also happens to be the only tool for debugging across the lane boundary. |
| **No SQL runs outside the guard.** | The read-only role is the floor, not the ceiling. The guard is what stops an unbounded scan and a table nobody meant to expose. |
| **The semantic layer's source of truth is the graph.** | Memory holds a derived copy that can be rebuilt. If memory becomes authoritative, decay and consolidation start editing a definition. |
| **Skill prompts follow the agreed template.** | Explicit inputs, phases, output format, templates. A prompt written another way cannot be reviewed against the others. |
