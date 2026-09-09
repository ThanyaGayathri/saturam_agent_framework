# Open items

Things that are decided by a person rather than by code, and things one lane is waiting on
another for. Maintained by Lane K; anyone may add a row.

Started 8 September 2026. Each item says **who** it is waiting on, since the common failure is
an item everybody agrees is important and nobody owns.

---

## Waiting on Kiruthiga

| # | Item | Why it matters | Since |
|---|---|---|---|
| ~~K-1~~ | ~~**`include_proposed` defaults to `False`**~~ **Decided 8 Sep: proposed rows are visible only after review.** Every read returns accepted rows unless a caller asks for `include_proposed=True`; the enrichment review screen is that caller. Matches what was built in P0 under a stated assumption — no code change | closed 8 Sep |
| ~~K-2~~ | ~~**Practitioner review of the shipped ontology**~~ **Provisionally closed 8 Sep**, on the Fable-model review recorded in `TEST_LOG.md`, which found three defects, six missing concepts and a wrong cardinality. **Caveat, recorded so the decision stays informed:** that reviewer is the same system as the author, so it is a rigorous self-review rather than an outside one — it cannot catch a wrong idea both passes share. A retail practitioner reading the 27 concepts remains worth ~15 minutes before K8 maps customer tables onto them | closed 8 Sep |
| ~~K-3~~ | ~~**Consolidation scope: should it carry `agent_id`?**~~ **Decided: yes, keep it.** Reads drop `agent_id` anyway (`_managers.py`), so facts are shared on read either way; keeping it on the write bounds each consolidation pass and preserves attribution, and costs nothing on recall. Lane R's implementation already does this — no change | closed 8 Sep |
| ~~K-4~~ | ~~**Does `format.sh` / `validate.sh` extend to cover `product/`?**~~ **Resolved by Lane R** — R2 added a `product` target to `validate.sh` (ruff, mypy, api contract, pytest). Found while verifying P1. It surfaced two environmental gaps, X-5 and X-6 below | closed 8 Sep |
| K-5 | **Promotion cannot prune, because the memory provider has no delete-by-id** | K5's promotion rebuilds memory's copy of the semantic layer from the graph by stable id, so a re-run is an overwrite -- but a concept *removed* from the graph leaves its promoted row behind, and "rebuilt from the graph" then means "a superset of the graph". The provider API (`write_tier`, recall) has no way to remove a row by id; K05's cookbook deletes with raw SQL against the substrate table, which product code must not do. Needs a `forget(scope, ids)` on the provider, sync and async, every backend. That is a K1 framework change (`libs/agno/egpt/memory/`) and belongs in its own PR, not inside P2. Until then a removed concept stays recallable, labelled with the topic it was promoted under | 9 Sep |

---

## Waiting on Lane R

| # | Item | Why it matters | Since |
|---|---|---|---|
| R-1 | **Sign off the KPI formula shape** — v1.0.0, v2.0.0 (breaking), v2.0.1 | A `K → R` contract. Lane R materialises from it at R5. Five changes and their costs are itemised in `product/egpt_retail/contracts/CHANGELOG.md` | 7 Sep |
| R-2 | **Review the semantic read API** v1.0.0, including K-1 above | The other `K → R` contract. Preflight, playground and spec screen all build against it | 7 Sep |
| R-3 | **Co-sign the executor registry convention**, including *one executor, one name* | A joint convention. The naming rule only works if both lanes follow it — the namespace is flat and a collision resolves silently to whoever registered first | 7 Sep |
| ~~R-4~~ | ~~**Confirm K14's second half is done**~~ **Closed: done, and it earned its keep.** Lane R wrote `join_path_explainer` from the template alone and logged five questions instead of asking. All five were real gaps; all five are fixed in `SKILL_TEMPLATE.md` | closed 8 Sep |
| R-5 | **Helm hook Job calling `egpt-retail load-knowledge`** | The entrypoint exists and is idempotent; nothing calls it at deploy time, so a fresh cluster comes up with no ontology and no prose. Wants a `post-install,post-upgrade` hook beside the existing migrate hook. Left to Lane R because the chart is theirs | 8 Sep |
| R-7 | **`R03_build_contract_fixture` fails on a stale committed fixture** | Asks for `./scripts/gen_api_models.sh`. Verified pre-existing — fails with Lane K's changes stashed. Their fixture and their generator, so left alone rather than regenerated across a lane boundary | 8 Sep |
| R-8 | **Switching provider mode on a live deployment needs a re-embedding migration, and nothing performs one** | Mode A embeds at 768, Mode B at 1024. The pgvector substrate creates `memory_vectors` at whichever dimension it first sees and then correctly refuses to reuse a table of a different width, so flipping `EGPT_RETAIL_PROVIDER_MODE` on a deployment that already holds memories fails at startup with no path forward but a manual drop. Found on the dev box, where a stale 768 table from local-mode work broke both `R01` cookbooks; fixed *for cookbooks* by making the table name a setting (`EGPT_RETAIL_MEMORY_TABLE`) so a local-mode cookbook writes to its own table rather than the deployment's. That is not a migration — it only stops the two from colliding. A real mode switch still needs re-embedding (SM-V2 #96), and it is a deployment concern | 9 Sep |
| R-6 | **`demo_setup.sh` should install `unstructured`** | `MarkdownChunking` needs it and `.venvs/demo` does not have it. Installed by hand to run K2; the next person hits the same wall | 8 Sep |

---

## Waiting on Lane C, or on the client

| # | Item | Why it matters | Since |
|---|---|---|---|
| C-1 | **Shared email hash — privacy sign-off** | The one blocker that is not engineering. Without it GA4 cannot join to CRM at all, and the RFQ funnel stays confined to one source. Escalate; it will not resolve by working harder | 7 Sep · **day 2** |
| C-2 | **Event-grain GA4 export** | The sample export is brand-level aggregate with no user identifier — it does not meet the landing contract's grain. Needs the BigQuery export turned on, which has a lead time | 8 Sep |
| C-3 | **Three RFQ funnel events are not instrumented** | Every key-event rate in the sample is 0.00 across all 47 brand rows in both files. Until they ship, the funnel is partial and must be labelled as such rather than filled in | 8 Sep |
| C-4 | **Referring product URL is absent, not inconsistent** | Neither the deals export nor the quote report has any URL, link or page column. The build plan recorded it as inconsistent; it does not exist. Client-side instrumentation | 8 Sep |
| C-5 | **C1 landing contract not written** | The last of the five contracts. Blocks R4, C2, C3 and C4 — Lane R's and Lane C's whole P1. Does not block Lane K's P1, but K4 waits on R4 at P2, so it reaches the critical path one phase out | 8 Sep |
| C-6 | **Quote-report grain must go into C1** | The report fans out 23.5× — 7,296 rows are 310 quote requests repeated per `Form ID` / `Engagement ID`. Every row is distinct, so R4's deduplication will not catch it. Taylor reads as 84.5% of quote volume and is actually 1.6% | 8 Sep |

---

## Nobody owns these yet

| # | Item | Why it matters |
|---|---|---|
| X-0 | **Inherit the parent heading into child prose chunks** (K2 follow-up, Lane K) | Heading-only shells are now dropped, which removed a useful signpost: `## 2. Gross Margin Analysis` used to win the gross-margin query; without it a case study outranks `### 2.1 Gross Margin by Category`. Prefixing each `###` chunk with its `##` path keeps the signal without the empty chunk |
| X-5 | **`validate.sh product` mypy fails on numpy's stubs**, not on our code | `numpy/__init__.pyi:737: Type statement is only supported in Python 3.12` — the product mypy step runs with the framework's `py39` target against stubs that need 3.12. Config, not code. `mypy product/egpt_retail` under the dev interpreter is clean |
| X-6 | **`validate.sh product` api-contract check needs `openapi_spec_validator`** | `ModuleNotFoundError` in `.venv`. A dev dependency missing from `dev_setup.sh`. Lane R's step; Lane K just hit it first |
| X-1 | **`Registry.get_function` has no duplicate-name warning** | The tool lookup twenty lines above it in the same file does warn. Executors resolve by `__name__`, first match wins, silently. Lane K ships a check for its own registry; the framework fix is a separate PR |
| X-2 | **Two pre-existing mypy errors** in `libs/agno/egpt/integrations/knowledge/` | `validate.sh` fails locally and passes in CI, which runs without the optional deps that pull them in. So "I ran validate.sh" is not currently a claim anyone can make on a dev machine — it quietly undermines item 5 of the definition of done for everyone |
| X-3 | **Neo4j Enterprise licence** | D11 and the Azure alignment assume two databases in one cluster. Local development is community edition, which supports one. K3 works either way because the ontology has its own label; K5 will share one database locally |
| X-4 | **`format.sh` reformats ~107 unrelated files** | Pre-existing import-sort drift across `cookbook/` and `libs/`. Anyone running it gets that noise in their diff, and the review skill says it does not belong in a PR. **It bit Lane K's P1 branch**: twelve framework files landed via one broad `ruff --fix libs/agno` and were only caught by a branch-scoped diff. Fix the drift on `main` once, or every branch pays |

---

## Decided, recorded so nobody re-opens it

| Decision | Where it is written |
|---|---|
| Scheduled runs write memory as `__scheduler__`, not with `user_id` unset — G2's stated resolution is unimplementable, because an unset slot means *any* rather than *none* | `contracts/MEMORY_SCOPE.md` |
| `grain` and `business_area` are vocabulary terms, not Python enums — a frozen enum breaks D9/G10's extension rule | `contracts/CHANGELOG.md` 2.0.0 |
| `QuoteRequest` and `Quote` are overlay concepts, not shipped core — the retail corpus confines quotes to B2B, 3 of 31 documents | `ontology/overlays/middleby.json` |
| Knowledge uses the memory subsystem's embedder decision rather than making a second one — Mode A `bge-base-en-v1.5` at 768, Mode B Azure `text-embedding-3-large` at 1024 | `knowledge/embedding.py` |
| `operations` added to the shipped `business_area` vocabulary — an employee had no home in the original six | `ontology/vocabularies.json` 1.1.0 |
| One executor, one name: an I/O executor is `async def` under the same name, not a sync/async pair | `contracts/EXECUTOR_REGISTRY.md` |
| Semantic reads return accepted rows only; `include_proposed=True` is for the review screen and nothing else (K-1) | `contracts/semantic.py` |
| The consolidation scope carries `agent_id` — one pass per agent partition (K-3) | `memory/scopes.py`, `open-items.md` |
| Memory and the knowledge layers read one `retail_provider_mode`. It was hardcoded to FRONTIER in memory and configurable in knowledge, which would have given one deployment two vector spaces | `memory/config.py` |
| A vector index is not built when `CREATE` returns — Neo4j populates it asynchronously and a query against a partial index answers confidently and wrongly. Every ontology write blocks until ONLINE | `ontology/store.py` `await_indexes` |
| `Store` is a first-class concept and `Location` its generalisation; `QuoteRequest`-era language is out of core `Contact` | `ontology/retail.json` 2.2.0 |
