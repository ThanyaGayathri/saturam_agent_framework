# Test Log — 94_retail

### K11_kpi_formula_shape.py

**Package:** K11 (contract validation, P0)
**Status:** PASS — with findings that change the contract

**Description:** Ran the nineteen KPI formulas from the `ecom_graphrag_poc` semantic layer
through the KPI formula shape. The corpus is synthetic ecommerce data, which is the right
corpus for this question: it tests whether the shape can express formulas somebody wrote in
earnest, not whether the numbers describe a real business. Then migrated all nineteen into
the shape and ran feasibility over the result, including the two-tables-missing case the
package calls for.

**Result:** 69 findings over 19 formulas, in three severities.

- **19 incompatible** — 15 formulas bind positionally with `?`, and 4 of those also declare
  a `param` name that the SQL never uses, so the name is decorative. Only the 4
  `CustomerSegment` formulas used the named `{sql_filter}` mechanism the contract assumed
  was universal.
- **38 metadata gaps** — no formula records a grain or a business area. Both are required by
  the contract. Recoverable by hand from the concept each formula hangs off; not a contract
  defect.
- **12 recorded** — 5 formulas use SQLite-only functions (`julianday`, `date('now', ...)`)
  against a PostgreSQL warehouse; 4 bake their lookback into the SQL text; 2 return row
  listings rather than a metric; 1 has an arithmetic bug.

**Migration:** all 19 constructed as `KpiDefinition` after the mechanical fixes (positional
markers renamed, grain and area filled from the concept). Dialect translation and de-baking
the four time windows were deliberately left manual — a migration script that rewrote the
meaning of a formula silently would hide the only cost worth reporting.

**Feasibility:** 19 ready against the full snapshot. Dropping `orders` moved 6 to partial,
each naming `orders`. Dropping `orders` and `order_items` together produced
`customersegment__lifetime_value` naming **both** missing tables, which is the case the
package flags as the one usually got wrong.

**What surprised me, in order of how much it matters:**

1. **`source_tables` did not drift once across 19 formulas.** I built the cross-check
   expecting it to be the main finding, since a hand-authored table list feeding a set
   difference is exactly where feasibility goes quietly wrong. Zero hits. The POC's lists
   were accurate. That raises rather than lowers the priority of keeping the check — it
   passed on a corpus one person wrote carefully, and the production corpus will be written
   by an agent.

2. **`StockLevel.stockout_risk_count` is arithmetically dead.** The formula computes
   `qty_on_hand * 30.0 / NULLIF(qty_on_hand, 1) < 7`, which cancels to `30 < 7` and is
   false for every row. It has presumably always returned 0 and nobody noticed, because 0
   is a plausible answer to "how many products are near stockout". Feasibility would have
   called it `ready`. This is the sharpest argument I have for why feasibility and
   correctness are different questions, and why golden questions (R15) are not optional.

3. **Two "KPIs" are not metrics.** `reorder_urgency_list` returns a top-10 listing and
   `days_of_stock` returns one row per product. Neither can be thresholded by an alert
   (R12) or drawn as a trend. The shape now records `result_shape`, so R5 can materialise
   them differently rather than discovering the difference at runtime.

4. **My own first harness had a false positive.** Splitting the projection on `,` counted
   `NULLIF(x, 0)` as two columns and reported a scalar metric as a row listing. Fixed with
   a paren-depth-aware split. Recorded because a check nobody trusts is worse than no check.

**Pre-existing failure, unrelated:** `./scripts/validate.sh` fails on two mypy errors in
`libs/agno/egpt/integrations/knowledge/` (`llms_txt_reader.py:94`,
`reranker/sentence_transformer.py:38`). Untouched by this work. `product/` and
`cookbook/94_retail/` pass ruff and mypy clean.

---

### K14_skill_template.py

**Package:** K14 (P0, implements D2)
**Status:** PASS — first half only

**Description:** The template's verification is two-sided: write one trivial agent against
it and run it, then hand the template to Lane R and have them write one without asking a
question. This is the first half. The agent is a synonym proposer — the smallest real agent
in the system and a strict subset of K8's enrichment proposer, chosen so that a template
that cannot carry it cannot carry the four Lane K agents either. Run against Azure OpenAI
(`gpt-5.4` deployment). Three checks: prompt and code agree on every closed set, output
parses into the declared schema, and the agent refuses correctly on an empty input.

**Result:** All three passed. Seven proposals for `SupplierReliability`, all within the
eight-item cap, none re-proposing the supplied existing synonym, every one carrying a
rationale that names the part of the description it derives from. The empty-description run
returned zero proposals with `notes` reading "concept_description was empty, so no proposal
would be traceable to an input" — the schema-shaped refusal the template asks for, rather
than prose or a padded list.

**What surprised me:**

1. **An enum-typed pydantic field cannot carry `Field(description=...)`.** Pydantic
   serialises it to a `$ref`, and strict structured-output schemas reject a `$ref` with
   sibling keywords. Azure returns a 400 *before the model is called* —
   `$ref cannot have keywords {'description'}`. Cost me a run to find. Now documented in
   section 6 of the template and in the authoring checklist, so the other six agents do not
   rediscover it. This is exactly the class of thing K14 exists to absorb once.

2. **The refusal behaved better than I expected on the first attempt.** I had assumed the
   model would pad the list rather than return empty, since returning nothing reads as
   unhelpful. It did not. The near-miss example in section 7 appears to be doing that work —
   worth keeping when the template is copied, and worth re-testing when anyone shortens it.

3. **My own closed-set drift check had a bug and passed a false negative first.** It matched
   the field name against the whole table cell, so `proposals[].kind` never matched `kind`
   and both enums reported DRIFT. Fixed to match on the final dotted segment. Recorded
   because a check that cries wolf gets deleted.

**Not yet done:** the second half of the verification. Lane R has to write a conforming
agent from the template alone, without asking a question. Until that happens this package is
not done, per the definition of done.

---

### K05_semantic_read_api.py

**Package:** K5 read surface (P0 contract + stub)
**Status:** PASS

**Description:** Exercised the semantic read API stub against an illustrative fixture: an
unloaded stub, the four reads, proposal visibility, join paths, and the join to the KPI
contract through feasibility. Async variants checked against their sync twins.

**Result:** All pass. An unloaded stub raises `FixtureNotLoaded` naming the fix rather than
returning empty. `include_proposed=False` hides a proposed table *and* a proposed column
nested inside an accepted table. A two-hop join path resolves `contacts -> brand_traffic`
via `quote_requests`; with no edges declared the same call returns `None`; an unknown table
raises. Feasibility read its table set through `available_tables(reader)`, so the KPI shape
and the read API meet in one function rather than in each caller.

**What surprised me:** nothing in the run, but one design point only became obvious while
writing it. Filtering proposed *tables* is the easy half; a proposed **column inside an
accepted table** is the case that would actually have leaked, because the caller asked for a
table it is entitled to see. That check is now explicit in `get_table` and in this script.

**Deliberately not done:** the stub ships with no fixture. Seeding it from the POC's
synthetic ecommerce payloads would teach Lane R shapes that are not the customer's. Once a
harvest exists, a fixture is one call.

---

### K01_memory_scope_convention.py

**Package:** K1 scope convention (P0 agreement; K1 implements in P1) · G1, G2
**Status:** PASS — and it invalidates G2's stated resolution

**Description:** Built every scope in `MEMORY_SCOPE.md` against the real `Scope` class and
printed the predicate each produces, plus an isolation matrix of three stored rows against
three reads. No database: `scope_predicate` is pure, and its output is the thing being agreed.

**Result:** The convention holds. A user's read does not see the scheduled run's memory, the
system read does not see a user's memory, and the tenant-wide read sees all three — which is
why the convention forbids it.

**What surprised me — G2's resolution does not work as written.** It says scheduled runs
should write "with tenant and agent set and user left empty, which `Scope` permits since only
the tenant is mandatory". `Scope` does permit it. But `scope_predicate` treats an unset slot
as *any*, not as *none*, so:

- a user-scoped read filters `user_id = 'alice'` and can never match a row with no `user_id`,
  making every scheduled write invisible to every user conversation;
- the only scope that reaches those rows is tenant-wide, which also returns every other
  person's private memories;
- and there is no way to ask for "rows with no user" — the empty-string sentinel that would
  express it is refused by `Scope.__post_init__`, with the reason spelled out in the message.

**The fix, and the good news:** scheduled runs write with `user_id = "__scheduler__"`. That
is not a name I invented — the framework already assigns it (`os/auth.py:255`), reserves it
(`os/middleware/jwt.py:34`), documents it as a principal (`os/actor.py:15`), refuses a client
claiming it (`os/middleware/user_scope.py:149`), and forbids a leading underscore in
service-account names specifically so nothing can collide with it
(`os/service_accounts.py:60`). The scheduler's memory partition and its audit attribution
become the same string, and no new reserved word has to be defended.

**Two operational rules that fell out:** an agent that writes memory needs a stable `id` or
`name`, or `agent_id` is a fresh uuid4-derived partition every process start; and a
single-tenant deployment still needs its tenant id stamped, or `_managers.py:576` returns
without touching memory and reports nothing.

**Left open deliberately:** whether a scheduled run's episodes may consolidate into facts a
user-scoped read can see. Default is no. Raise it when a use case wants otherwise.

---

### K00_executor_registry.py

**Package:** Executor registry (P0 convention, joint Lane K / Lane R)
**Status:** PASS

**Description:** Demonstrated the executor name collision against the framework's own
`Registry`, then showed the check refusing it. Also exercised the other malformed shapes:
too many parameters, a wrongly named parameter, a leading underscore, and a lambda.

**Result:** The collision reproduces exactly as feared. Two functions both named
`check_freshness`, one per lane, registered together: `get_function` returns Lane K's,
Lane R's is unreachable, and **nothing warns** — not at registration, not at resolution.
`assert_registry_valid` catches it and refuses to start.

**What surprised me:**

1. **`Registry.get_function` has no duplicate warning, but the tool lookup in the same file
   does.** `_entrypoint_lookup` explicitly logs "multiple distinct tools share the name" and
   tells the user to disambiguate. `get_function` is a bare
   `next((f for f in self.functions if f.__name__ == name), None)` twenty lines away. So the
   framework already knows this failure mode and guards it for tools only. Worth raising
   upstream as a framework fix, separately from this convention.

2. **The executor name is stored data, not just code.** `Step.to_dict` writes
   `executor_ref = executor.__name__`, so renaming a Python function breaks every stored
   workflow that references it. That makes a rename a breaking change under the change
   protocol rather than a refactor, which is not obvious from reading either lane's code.

**A judgment call worth flagging:** the convention is **one executor, one name** — where an
executor does I/O it is authored as `async def` under the same name, not as a sync/async
pair. That departs from the repository's standing rule, deliberately. The rule serves public
API where a caller picks a variant; an executor has one caller, the workflow engine, which
detects both forms at runtime. Publishing `check_freshness` and `acheck_freshness` would
double a flat namespace that cannot survive ambiguity, for a choice nobody makes. Recorded
here because the next reviewer will read it as a rule violation.

---

### Review round 1 — all five scenarios re-run

**Packages:** K14, K11, K5, K1, executor registry
**Status:** PASS — after fixing 11 findings from automated review on PR #186

**Description:** An automated review raised 6 Major, 2 Minor and 3 Nit findings. Every one was
reproduced against the real code before being fixed; none was taken on faith.

**Reproduced and fixed:**

1. **`source_tables` case sensitivity** — `source_tables={"Orders"}` with `FROM Orders` was
   refused at construction, because `referenced_tables` lower-cases and the comparison did not.
   Now normalised at construction.
2. **Schema-qualified table names** — `FROM silver.orders` captured `silver`. Worse than it
   looks: `TableRef.schema` defaults to `"silver"`, so this is the expected case, and the
   obvious workaround leaves the KPI permanently blocked. Regex now captures an optional
   qualifier and keeps the table.
3. **Result-shape heuristic, both directions** — `SELECT SUM(x) FROM t GROUP BY t.id` returned
   `SCALAR` (a rowset materialised as a metric), and a `GROUP BY` inside a subquery leaked into
   the outer classification. Replaced column-counting with subquery stripping plus "any outer
   `GROUP BY` is a rowset". **Corpus rowsets went 2 → 3.**
4. **K01 never called the function it claimed to verify** — `matches()` reimplemented the
   "unset means any" rule in Python, so the isolation matrix agreed with itself whatever
   `scope_predicate` did. Now runs the emitted fragment against a one-row SQLite table.
5. **Cookbooks printed failures and exited 0** — all five now assert and exit non-zero.
6. **`assert_registry_valid` docstring claimed it was wired** into the application factory.
   Nothing calls it; there is no factory yet. Corrected to say the guarantee is available, not
   active.

**Mutation-tested the K01 fix**, which is the finding that mattered most. Mutated the real
`scope_predicate` so an unset slot emits `IS NULL` instead of no clause — the change that in
production would zero out the system read this convention exists to protect. Before the fix
the cookbook was unmoved and exited 0; after, it exits 1 naming the broken invariant. `libs/`
restored afterwards and verified clean.

**What surprised me:** finding 4 is the one I would not have found myself. The harness looked
thorough — three scopes, three rows, a printed matrix — and was checking my own restatement of
the rule rather than the rule. A test that reimplements what it verifies passes for exactly as
long as both copies are wrong in the same way.

**Not fixed, deliberately:** the reviewer's Minor on the D10 refusal having no schema-level
backstop. No output schema can stop a model emitting a bad proposal. The guarantee is
structural and downstream — the agent has no write path, and only accepted rows reach the
graph — so the docstring now says where the guarantee actually lives instead of implying the
schema provides it.

**Also found, not a code issue:** CI reported `ci-success` failing. It is the `run-ci` label
gate, not a test failure — every other job was skipped.

---

## Lane R — phase zero

### R03_build_contract_fixture.py

**Package:** R3 (P0, API contract — the fixture the stub serves)
**Status:** PASS

**Description:** Built the contract fixture from the shipped vocabularies and the vendored
KPI corpus, confirmed the committed copy is byte-identical to a fresh build, loaded it into
Lane K's `StubSemanticRead`, and checked every behaviour the contract's examples claim:
seven accepted tables, one proposed table hidden by default, one proposed column hidden
inside an accepted table, a three-hop path `customers -> products` via `orders` and
`order_items`, no path between `supplier_deliveries` and `product_reviews`. Then ran the
three KPI examples from the contract through `assess_feasibility(available_tables(reader))`.

**Result:** All pass. Two `ready`, one `partial` naming `web_sessions` — the badges the
`KpiRegistry` example renders, derived rather than typed.

**What surprised me:**

1. **The corpus and the ontology do not share nouns.** The vocabulary's business areas are
   `customer, product, inventory, supply_chain, marketing, sales`; the corpus's concepts are
   `CustomerSegment, Product, Category, Supplier, StockLevel`. Mapping one to the other is a
   table in the builder (`_CONCEPTS`), and it is a judgment — `Category` became
   `ProductCategory` under `product`, `Supplier` went under `supply_chain`. Lane K should
   read that table; it is the closest thing to an ontology mapping that exists today.
2. **A fixture has to be generated to be trustworthy.** My first draft was hand-written and
   drifted from the contract examples within an hour (a fourth metric on `StockLevel`). The
   builder plus a drift check is what made "the stub moves with the contract" mechanical.

**Deliberately not done:** vendoring anything from client sample data. The fixture's header
says "contract-shaped, not customer-shaped. Replace at K5." and the `customers.email`
proposed column is there to exercise the review path, not because a customer has it.

---

### R03_stub_server.py

**Package:** R3 (P0, API contract — published as a schema, running)
**Status:** PASS

**Description:** Stood the contract up as a FastAPI application built from the bundle at
startup, and checked that every operation the bundle declares is mounted (105), that every
example-served operation returns its first declared 2xx example with its declared status
(87; the other 18 are the 11 `DELETE`s answering 204 and the 7 live semantic reads), and
that the `?example=<name>` / `X-Example` selector switches `GET /kpis` between its populated
and empty states, refusing an unknown name with a 400 that lists the available ones.

**Result:** 105 mounted, 87 serve their declared example, selector works.

**What surprised me, in order of how much it matters:**

1. **Example names collided across status codes.** `default` was the name under both the
   200 and the 404 of the same operation, so `?example=default` was ambiguous and the
   stub's first-match rule silently picked the 200. Fixed by renaming every error example
   to its code (`not_found`, `insufficient_scope`, `conflict`, `invalid`) and adding a
   uniqueness check to `scripts/check_api_contract.py`. A contract lint that only checks
   validity would never have found this; the stub found it by serving the wrong thing.
2. **My own YAML was silently wrong in a way the validator could not see.** A flow-style
   example like `{description: What is sold, and how each product performs.}` parses as two
   keys, not one string, and the document is still valid OpenAPI. The example-versus-schema
   check (every example must validate against its declared schema) is what caught 32 of
   these; it now runs in `validate.sh`. Recorded because "the spec validates" is a much
   weaker claim than it sounds.
3. **The generator invents names for inline shapes** — `Kpi1`, `Freshnes`, `ModelTier2`,
   `Origin1` — and those would have been frozen into the published models. Every inline
   object and enum is now a named schema; the generated module has 166 classes and every one
   carries a schema name.
4. **`DELETE` served its 403 example instead of its 204.** The stub preferred "any example"
   over "a declared no-content response". Fixed; the contract tests pin it.

**Also:** `product/pyproject.toml` initially set `line-length = 120` to match the framework,
and `ruff format` rewrapped five of Lane K's files. Lane K's code and cookbooks are formatted
at 88 (ruff's default, because `product/` had no config), so the product side now declares
88 and their files are untouched by this PR.

---

### R03_semantic_over_http.py

**Package:** R3 (P0, the semantic read API over HTTP) · reviews K5 v1.0.0
**Status:** PASS

**Description:** The seven `SemanticRead` methods over HTTP, answered live from the fixture
by Lane K's stub rather than from canned examples; the three join-path outcomes as three
distinct responses; and the `include_proposed` scope gate, using HS256 tokens minted in
the script so no issuer is needed.

**Result:** Seven reads live. `found` is 200 with a path, `no path` is 200 with
`path: null`, an unknown endpoint table is 400, an unloaded reader is 503. No token 401;
`semantic:read` 200; `semantic:read` with `include_proposed=true` **403**;
`semantic:read` plus `semantic:read_proposed` 200 with the proposed table present;
`agents:read` alone 403.

**What surprised me:** the scope gate has nowhere to live in the framework. `AuthorizationConfig`
has no scope-mapping field and scopes are a framework enum, so the stub reads the JWT claim
itself. That is fine for phase zero and a named cost for phase five, recorded in the API
contract's changelog. The framework's own pattern — the components audit scope needed a
feature flag to exist — suggests the fix is a small framework change in Lane R's bucket.

**A judgment call worth flagging:** an unknown endpoint table is a **400**, not a 404 and
not "no path". Lane K's stub raises for it ("no path from a table I have never heard of
would be a misleading way to report a typo"), and the HTTP mapping keeps that distinction.

---

### K14_join_path_explainer.py

**Package:** K14, second half (Lane R writes a conforming agent from the template alone)
**Status:** NOT RUN — the live model call needs `azure_key.env`, which is not present in
this environment. The skill, its schema, and the closed-set drift check are written and
checked; the four live checks (verdict mirrors the deterministic rule twice, a null path
invents nothing, a missing input refuses as `unknown`) run the moment credentials are sourced.

**Description:** Wrote `product/egpt_retail/skills/join_path_explainer.md` and `.py` from
`SKILL_TEMPLATE.md` alone. The agent explains a join path that a deterministic lookup already
computed; its verdict must mirror that input and never its own judgment, which is the
closed-set discipline in the shape that matters most for Lane R. `closed_sets_match_prompt()`
passes: the prompt's section 6 and the `JoinVerdict` enum agree.

**Questions that came up while writing it — recorded here rather than asked, because the
questions are the finding:**

1. **Section 6 says "render closed sets from the vocabulary". My closed set is not a
   vocabulary term.** `verdict` cannot vary by customer — a lookup has three outcomes — so
   it is a Python enum mirrored in the prompt table, like `result_shape`. The template
   describes that case for `result_shape` and `feasibility` specifically; it should say the
   rule in general: *a set that cannot vary by customer stays an enum and is enumerated in
   the table; a set that can varies is a vocabulary and is rendered.* One sentence.
2. **`writes: proposals | nothing` — what does an explanation agent write?** Nothing. The
   template's header comment says "never graph"; it does not say that `nothing` is the
   normal value for every explanation agent. It should, since Lane R writes three of those.
3. **The four standing rules include "You propose."** An explainer does not propose. I
   copied the rule verbatim as instructed and it reads oddly. Either the rule's first
   sentence generalises ("You produce output for a person or a caller to act on; you never
   write…") or the template says the four are to be copied even where a sentence does not
   fit. I did the latter; Lane K should decide.
4. **Where does the deterministic rule the agent must agree with live?** I put
   `verdict_for_path()` beside the schema so a cookbook can assert the model's verdict
   equals it. The template does not have a place for "the executor this agent's output is
   checked against"; for every Lane R agent there will be one.
5. **The worked example must come from the shipped ontology, never a harvest.** My inputs
   come from the contract fixture, which is derived from the ontology and the vendored
   corpus — but the corpus tables (`orders`, `customers`) are not ontology terms. I judged
   that acceptable because the fixture is a product asset; the template should say whether
   "shipped ontology" includes product fixtures.

None of the five blocked authoring; all five are places the template could be one sentence
more explicit. That is the second half of K14's evidence: the template holds, and here is
exactly where it leaked.

**Round-1 review fix (PR #187):** the null-path "did not invent a join" check was lexical
(`"product_id" in explanation` and `"no" not in explanation`) and nearly impossible to fail --
`"...on product_id, though this is not in the graph."` passes it, because `"not"` contains the
substring `"no"`. Reproduced against that exact sentence (see the PR comment reply). Replaced
with a structural check: `caveats == []` plus none of the fixture's actual join-column tokens
(`product_id`, `customer_id`, `order_id`) appearing anywhere in `explanation`. Still NOT RUN
against the live model for the same reason as above; the check is stronger whenever it is.

---

### validate.sh — the two "pre-existing mypy errors" do not reproduce

**Package:** definition of done, item 5
**Status:** PASS — `./scripts/format.sh` and `./scripts/validate.sh` both exit 0

**Description:** PR #186 reported `validate.sh` failing on two mypy errors in
`libs/agno/egpt/integrations/knowledge/` (`reader/llms_txt_reader.py:94`,
`reranker/sentence_transformer.py:38`), untouched by that work. Before planning a fix I
rebuilt `.venv` from scratch with `./scripts/dev_setup.sh` and ran the script unchanged.

**Result:** `mypy — Success: no issues found in 1741 source files`. The full script passes,
including the new `product` target (ruff, mypy, API contract check). The errors are
environment drift in the venv they were observed in, not defects in the framework — which is
what CI on `main` being green already implied. **No framework fix is needed**, and the
inline fix planned for the R2 PR is withdrawn. Recorded so the next person does not chase it.

---

### R03_prototype_screen.md

**Package:** R3 (P0, the evidence clause: one prototype screen renders from the stub)
**Status:** PASS — with two findings that are contract work, not screen work

**Description:** Pointed the prototype's `KpiRegistry` screen (`/kpis`) at the stub
(`python -m egpt_retail.api.stub`, port 7778). The prototype's storage accessors are
synchronous and `fetch` is not, so a hydrate-at-boot shim (`frontend/src/storage/api-hydrate.js`)
fetches `/kpis` before the React tree mounts and `loadKpiRegistry` reads the cache. The
change lives entirely in `frontend/src/storage/` and `main.jsx`; **no file under
`frontend/src/components/` changed**, which is the evidence bar as written. Captured with
headless Chrome in both states.

**Ran against:** `Retail-GPT-prototype` branch `egpt/r3-stub-evidence`, commit `c614437`
(3 files, 64 insertions, 0 under `components/`). Stub: this branch, fixture `r3-contract.1`.

**Result:**

- `evidence/R03_kpis_populated.png` — "Registry · 3 KPIs", three business areas
  (`inventory`, `product`, `supply_chain`), one KPI expanded: *Days of Stock Remaining*,
  with the contract's description text. The localStorage seed has 25 KPIs across 10 areas;
  this is unambiguously the stub's payload.
- `evidence/R03_kpis_empty.png` — hydrated from `/kpis?example=empty`: "Registry · 0 KPIs",
  "No KPIs match the current filters." The empty state the nav map's `ConfigureDatabaseEmpty`
  family exists for, rendered by the same component with no change.
- The stub's access log shows `GET /kpis` and `GET /kpis?example=empty` from the browser.

**What surprised me, in order of how much it matters:**

1. **Business-area labels rendered as raw ids** (`supply_chain`, not "Supply Chain"). The
   screen labels areas from its own `BUSINESS_AREAS` constant, which holds the prototype's
   ten ids; the contract's six vocabulary ids are not in it, so the id falls through. This is
   the ten-versus-six mismatch already raised to Lane K in `contracts/CHANGELOG.md`, seen
   on a screen. P5 must label from `GET /business-areas`, not from a constant.
2. **"Powered by" matched catalog entries by KPI *name*.** The screen joins use cases to
   KPIs on `subjectArea::name`, so a shipped use case that happens to share a name with a
   contract KPI lit up as "powered by" it. The contract keys by `identifier`, which is the
   stable one (provenance and gold reference it). P5 maps on `identifier`; a contract
   field (`Kpi.identifier`) is already there for it.
3. **The seam is clean at the module boundary and not quite clean at the call signature.**
   Every accessor is `load*(storageKey, profileId)` returning synchronously. Hydrate-at-boot
   keeps the claim literally true for one screen; for the whole app in P5 the accessors
   become async and every `useState(() => load*(...))` initialiser becomes an effect. That
   is the real integration cost, and it is now known in phase zero rather than phase five.
4. **Headless Chrome hangs on exit on this machine** (`CVDisplayLinkCreateWithCGDisplay`
   errors) but writes the screenshot first. Cost me two attempts; noted so the next run
   uses `pkill` after the capture rather than waiting.

**No contract field was missing.** Everything the screen rendered came from `Kpi` as
declared; the mapping in the shim is naming (`snake_case` to `camelCase`, `level`
capitalised), not shape.

---

### R01_local_stack.py

**Package:** R1 (P0, application image and entrypoints — the API entrypoint)
**Status:** PASS

**Description:** Ran the same `egpt_retail.app.main:app` the image serves, in-process with its
lifespans, against a real Postgres (`agnohq/pgvector:18` on 5532 from `deploy/compose.yaml`).
Called `/health` and `/config`. Then built the image and ran the compose stack — `migrate`,
`api`, `consolidator` — and called both routes on the container from the host.

**Result:** `/health` → 200 `{"status": "ok"}`. `/config` → 200, `os_id: egpt-retail`,
`databases: [egpt-control-plane]`, one agent `synonym_proposer` with `model: {id: gpt-5.4,
provider: Azure}`, no workflows (the RFQ chain is R10). The container's own access log shows
the two requests; the evidence was taken from port 7780 after the finding below.

**What surprised me, in order of how much it matters:**

1. **The image had no `openai` SDK, and only the container knew.** `egpt[os]` carries the
   server stack and no provider SDK; the dev venv gets `openai` through `egpt[dev]`. So the
   in-process run passed, and the `api` container exited 1 at import with
   `ImportError: openai not installed`. Fixed by making `openai` a runtime dependency of
   `product/` — the product chooses the provider, so the product declares the SDK. This is
   the single best argument for "run the image, not just the code" that this lane has.
2. **The first compose evidence was from the wrong server.** Host port 7777 was already held
   by a local EgptOS (`os_id: retail-playground-demo`, from the retail-playground branch), so
   the compose `api` could not bind and my host-side curl hit the squatter — and reported it
   as healthy. Caught because the body named a different OS. `compose.yaml` now takes
   `EGPT_RETAIL_API_PORT`; the evidence is from 7780 with the container shown owning it.
   Recorded because a green check against the wrong process is worse than a red one.
3. **`migrate` on a fresh database is not a no-op.** I had written that a clean install only
   skips and `auto_provision_dbs` creates the tables. The log shows `up()` applying 2.7.0
   through 2.10.0 and creating the memory-tier tables (`egpt_memory_link` …) on the first run,
   then skipping every table on the second. Docstring and compose comment corrected; the
   Helm hook Job in R2 therefore does real work on a clean namespace.

---

### R01_consolidator_no_work.py

**Package:** R1 (P0, the memory consolidator entrypoint) · touches G1, G2
**Status:** PASS — re-run after fixing 3 findings from automated review on PR #188 (spurious
advisory-unlock on a refused lock, the memory provider's engine never being closed, and
`psycopg_dsn` only stripping the `+psycopg` suffix)

**Description:** The package's evidence line — "the consolidator logs a clean no-work exit
rather than an error" — with both shapes it can take, plus the single-instance refusal:
slug unset (no provider built, nothing but a log line); slug set to `acme` against the real
Postgres, which builds the vector-only Mode B provider (pgvector table self-provisioned, no
Neo4j, no embedder key) and runs one tick; and a second instance while this script holds the
customer's advisory lock.

**Re-verified for PR #188's fixes.** This script's own assertions never covered engine
cleanup, so the leak the review found would have passed silently here too. Checked
separately, against the same live Postgres: before the fix, running `main(once=True)` three
times in one process (this script's exact pattern) left 5 open server-side connections
(`pg_stat_activity`, still-running process) against a baseline of 1; after wrapping the
provider in `try/finally: await provider.aclose()`, three runs left the baseline unchanged at
1. This script's own three exit codes (0, 0, 3) and log lines were unaffected by any of the
three fixes and still match the **Result** below exactly.

**Result:**

```
consolidator: no scopes configured -- EGPT_RETAIL_CUSTOMER_SLUG is unset. Nothing to consolidate. Exiting 0.
consolidator: 1 scope(s) configured: acme/synonym_proposer/__scheduler__/*/*
consolidator: tick complete -- 1 scope(s) checked, 0 with work, 1 pass(es), 0 error(s) in 0.01s
consolidator: another consolidator holds the lock for customer 'acme'; this instance exits 3.
```

Exit codes 0, 0, 3. The compose `consolidator` service (loop mode, slug unset) logged the
first line and exited 0 on its own.

**What surprised me:**

1. **The worker is silent on an empty scope list — every line above is new code.** With no
   scopes `run_once()` returns `[]` and logs nothing at any level; `start()`/`stop()` log two
   lines regardless of work. The package's evidence could not have been produced without the
   entrypoint owning the summary. Kept in `product/`, never in the framework: the worker does
   not know what "no work" means for this deployment, and the framework wiring is K1's.
2. **Mode A refuses the embedder the deployment uses.** `ProviderMode.LOCAL` only accepts
   offline-capable embedders (`sentence_transformer`, `fastembed`, `vllm`, `ollama`); an
   Azure OpenAI embedding deployment forces `FRONTIER`. Consistent with the build plan (two
   Azure deployments, GPU pool deferred), and worth knowing before anyone writes
   `mode="local"` into a values file.
3. **The pgvector store provisions itself and the migration runner cannot see it.**
   `CREATE EXTENSION IF NOT EXISTS vector`, schema, partitioned table and indexes on
   construction; `MigrationManager._TABLE_TYPE_TO_ATTR` covers the relational memory-tier
   tables only. Two different things with the same word in their name.

**Deliberately not done:** any user's memory. The consolidation scope carries
`user_id="__scheduler__"`, so it reaches exactly the scheduler's partition and nothing a
person wrote — there is no slug on a run context until K1 stamps it. **Open question for
Lane K, raised not resolved:** whether the consolidation scope should carry `agent_id` (one
pass per agent partition, as here) or drop it (one pass per tenant). Narrowing is conjunctive
over every set slot, so the choice decides what the facts are keyed to.

**A deviation upward, stated:** the package says "the consolidator pinned to one replica,
because it has no distributed lock". The worker is idempotent by upsert-on-content-id, so
two replicas cannot corrupt state — they redo the same window. Pinning is about wasted work,
and a chart cannot make `kubectl scale` impossible. The advisory lock on a dedicated session
turns the pin into a refusal the log names; R2's chart guards are belt-and-braces on top.

---

### R02_cluster_evidence.sh

**Package:** R2 (P0, cluster and services) · D3 (one deployment per customer), D4 (Helm), D5
(separate warehouse database), D14 (RBAC by scope) · G4, G11
**Status:** PASS (third run; the first two failed and are recorded below)

**Description:** The package's four scenarios, on kind (`deploy/kind/cluster.yaml`, two nodes,
v1.37.0), as one script: install the infrastructure chart, mint two RS256 tokens offline,
install the application chart (migrate as a `pre-install` hook), then 200/403/open-`/health`,
pod kill under an in-cluster request loop, `kubectl scale` on the consolidator, and the Helm
render guard. Azure is a placeholder throughout: plain `Secret` for Key Vault, a JWKS file for
Entra, two Neo4j Community instances for two databases, MinIO for the storage account.

**Result (third run, unedited):**

```
   NAME                                   READY   STATUS      RESTARTS
   egpt-api-7c558c8578-4bzpr              true    Running     0
   egpt-api-7c558c8578-w47gn              true    Running     0
   egpt-consolidator-d4f7958b8-nlg8x      true    Running     0
   egpt-migrate-slwdx                     false   Succeeded   0
   egpt-minio / egpt-neo4j-memory / egpt-neo4j-ontology / egpt-pgbouncer / egpt-pipelines /
   egpt-postgres / egpt-redis             true    Running     0

   GET /health (no token)             -> 200
   GET /config alice [config:read]    -> 200      os_id egpt-retail | agents ['synonym_proposer']
   GET /config bob   [agents:read]    -> 403      {"detail":"Insufficient permissions. Required scope(s): config:read"}
   GET /config (no token)             -> 401

   deleting pod/egpt-api-7c558c8578-4bzpr at 23:32:05
   requests: 188   non-200: 0   distinct codes:  188 200
   api pods after : pod/egpt-api-7c558c8578-2wq8d pod/egpt-api-7c558c8578-w47gn

   kubectl scale --replicas=2 accepted (a chart cannot stop this)
   egpt-consolidator-d4f7958b8-6lhqn   Running   LASTEXIT 3
   consolidator: another consolidator holds the lock for customer 'acme'; this instance exits 3. One replica per customer -- scale the API, not this.

   OK   helm template fails on consolidator.replicaCount
All R2 cluster scenarios passed.
```

Migrate hook, first install (fresh database): `Storing version 2.10.0 in database for table
egpt_memory_link ... migrate: done`. Third install, same database: `Skipping migration: the
version of table 'egpt_memory_link' (2.10.0) is less or equal to the target version (2.10.0)`.
Both paths of the runner have now executed under the hook.

**The two failed runs, and what each one taught:**

1. **Run 1 — `CreateContainerConfigError`, `configmap "egpt-env" not found`.** The rule the
   two-chart split exists for applies to the chart's own resources: a `pre-install` hook runs
   before the release's ConfigMap and Secret exist, so a Job that `envFrom`s them can never
   start on a clean install. The migrate Job now carries its one variable inline
   (`EGPT_RETAIL_DB_URL`, the direct URL). A hook that depends on nothing but the database is
   the right shape anyway.
2. **Run 2 — every `api`/`pipelines` pod crash-looping (5 restarts), curl loop 39/39 `000`.**
   First log line: `Failed to parse JWKS key: RS256 requires 'cryptography' to be installed`,
   then `ValueError: At least one JWT verification key or JWKS file is required`. The
   framework declares bare `PyJWT` (`libs/agno/pyproject.toml:60,78`), which verifies HS256
   only; every RSA key in the JWKS fails to parse and the validator starts with zero keys. The
   dev venv has `cryptography` transitively, so `test_r2_auth.py` and the devauth round trip
   passed everywhere except the image. **Framework finding, Lane R's bucket:** any RS256
   deployment of `egpt[os]` — Entra on AKS included — hits this; `PyJWT[crypto]` in the `os`
   extra is the fix. For now `cryptography>=41.0` is a product runtime dependency with the
   reason beside it.

**What surprised me:**

1. **The consolidator refusal came out first, on a broken cluster.** In run 2 the API pods
   could not start, and the advisory lock still refused the second consolidator — `exits 3`
   naming `acme`. It depends on Postgres alone, which is the property that makes it a real
   guard rather than a chart convention.
2. **Zero non-200 in 188 requests across a pod kill** is the readiness probe plus exec-form
   `ENTRYPOINT`: SIGTERM reaches uvicorn, the pod leaves the endpoints before it stops. From
   inside the cluster against the Service, as planned — a `port-forward` would have shown the
   tunnel dying, not the app surviving.
3. **`--set auth.jwks='{"keys":[]}'` is a list to Helm.** `--set` parses braces; the
   real path is `--set-file auth.jwks=jwks.json`, which is a string. The lint command in
   the chart README uses `--set-file` so nobody rediscovers this.
4. **A failed first `helm install` blocks `upgrade --install` forever.** The script now
   uninstalls a release in `failed` state before installing, so a rerun after a fix is one
   command.

**Deviations from the package text, restated here so they are not lost:** hard, not
impossible — the chart cannot stop `kubectl scale`; the lock does. Two Neo4j Community
instances, not two databases (Enterprise). PgBouncer in **session** mode; transaction mode
needs `prepare_threshold=None` and would let the lock evaporate. Every Azure values path is
listed as **NEVER** executed in `deploy/helm/egpt/README.md`; `deploy/AKS_CUTOVER.md` is the
day they run.

**Re-run after the review response (same kind cluster, fourth run):** review found that a
`helm upgrade` changing only the JWKS/env/secrets values never rolled the pods (`envFrom` and a
mounted ConfigMap are read once, at container start) -- reproduced live: rotating `auth.jwks` via
`--set-file` left both api pods at their original age and the same `kid`. Pod-template
`checksum/env` / `checksum/secrets` / `checksum/jwks` annotations fixed it; re-verified live: an
identical re-apply left the pods untouched, a changed JWKS rolled both. All four scenarios above
were re-run against the fixed chart, unchanged, and passed.

The script itself gained `NAMESPACE`, `VALUES_FILE`, `SKIP_INFRA`, `TOKEN_GRANTED`/
`TOKEN_UNGRANTED` overrides so it is what actually re-runs on AKS, not a re-run-by-hand
substitute -- `AKS_CUTOVER.md` §4 said "unchanged" while the script hardcoded the kind path
(installed `egpt-local-infra`, which that document says not to; used the `default` namespace
while §3 installs into `egpt`). Exercised on kind with `SKIP_INFRA=1` and pre-minted
`TOKEN_GRANTED`/`TOKEN_UNGRANTED` (standing in for Entra tokens) to prove the AKS-shaped path,
not only the default one -- same PASS. The `helm uninstall` on a `failed` release is now guarded
to `kind-*` contexts only; this branch could not be exercised against a real non-kind context
(none exists here) and is verified by reading, not by running.

The chart also gained a `PodDisruptionBudget` (api; pipelines only above one replica) and a
`securityContext` (`runAsNonRoot`, `runAsUser: 10001`, `allowPrivilegeEscalation: false`,
`readOnlyRootFilesystem: true`, all capabilities dropped) on every container, api/pipelines/
consolidator/migrate. `readOnlyRootFilesystem` was the one part with real risk of breaking
something at runtime rather than at render time, so it was verified the same way as the rest of
this package: applied through the real chart, not asserted, and all four scenarios above re-run
against it -- api, pipelines and the migrate hook all started clean and served/completed
normally with a read-only root filesystem.

Separately, `product/egpt_retail/app/main.py`'s `build_agent_os` now refuses to start when a JWKS
file is configured with no audience (previously: silent `verify_audience=False`, any RS256 token
the JWKS could verify accepted regardless of `aud`) unless `EGPT_RETAIL_AUTH_SKIP_AUDIENCE_CHECK`
is set; `_helpers.tpl`'s `egpt.authGuard` gained the matching `helm template` failure. Both
verified: `helm template --set auth.enabled=true` (no jwks, no audience) still fails on the jwks
check first, as before; `--set auth.audience=""` with a jwks value now fails on the new audience
check. This package's four scenarios (which always set an audience) are unaffected.

---

### R02_wheel_contents.py

**Package:** R2 (P0, the framework stays publishable)
**Status:** PASS

**Description:** Builds `libs/agno` into a temporary directory and lists what the wheel holds;
then runs the same check CI runs (`scripts/check_wheel_contents.py`) after `python -m build`.

**Result:**

```
   wheel: egpt-0.2.0-py3-none-any.whl (4120 KB)
   members : 1746
   top-level: ['egpt', 'egpt-0.2.0.dist-info']
   product/frontend/deploy members: none
   shipped data files: 5 (registry templates, console page)
The egpt wheel carries only egpt/ and its dist-info. The framework stays publishable.
```

**What surprised me:**

1. **The build broke `validate.sh`.** `python -m build --outdir <tmp>` still writes
   `libs/agno/build/lib/egpt/` into the source tree, and the next `validate.sh` failed its
   framework mypy step with `Duplicate module named "egpt" (also at "./build/lib/egpt/__init__.py")`
   — an hour after the cookbook ran, in a step that has nothing to do with wheels. `build/` is
   gitignored, so it is invisible to `git status`. `build_wheel()` now removes the tree it
   created; the cookbook prints whether anything was left behind.
2. **The check itself found nothing, and that is the point.** The property already holds because
   of `packages.find include = ["egpt*"]`; the check exists for the day someone adds a
   `package-dir`, a `MANIFEST.in`, or moves retail code under `libs/`. A guard on a property you
   have costs one CI step; discovering it gone costs a release.

**Correction, review response:** point 1 above overstated what the original `build_wheel()` did.
It only removed `build/` when `build/` had **not** already existed before the run
(`had_build_dir`) — the normal case (CI's own earlier `python -m build`, a previous cookbook run)
left it skipped, which is exactly the scenario the cleanup exists for. Reproduced live: pre-create
`libs/agno/build/`, run `build_wheel()`, `build/lib/egpt` was still there afterward. Fixed to
remove `build/lib` unconditionally — it is always this invocation's own artifact regardless of
what else is in `build/` — and reproduced-then-reverted to confirm. Separately, the build
subprocess's `check=True` + `capture_output=True` combination hid setuptools' own error text on
failure; the wrapper now prints `stdout`/`stderr` before re-raising, verified against a forced
build failure.
### K03_ontology.py

**Package:** K3 (P1, implements D9, G10)
**Status:** PASS

**Description:** Authored the shipped retail ontology as a versioned artifact, wrote it to
Neo4j under its own label with every node embedded, built the three indexes, and ran the
package's three scenarios: a synonym that is not a node name, a plain-language phrase naming
no node at all, and an overlay surviving a core upgrade.

**Result:** 20 concepts, 21 relations, 81 synonyms, six business areas. All three scenarios
pass.

- Synonyms reach their concept: `RFQ` to QuoteRequest (0.698), `SKU` to Product (0.671),
  `vendor` to Supplier (0.796).
- Plain language reaches a sensible concept by meaning, naming no node: *"what do we need to
  reorder?"* to PurchaseOrder/StockLevel/Inventory, *"goods turned up late from the people we
  buy from"* to Delivery (0.756)/Supplier/PurchaseOrder.
- The overlay adds RepGroup, annotates core QuoteRequest with `shop enquiry`, and both
  survive an upgrade of the core from 1.0.0 to 1.1.0 that adds a Warranty concept.

**Mutation-tested the claim the package exists for.** Changed `embedding_text` to return the
concept name alone -- the mistake the plan names as usual -- and re-ran. `RFQ` reached
**Channel**, `SKU` reached **Location**, and two of the four plain-language phrases missed
entirely. Exit 1. Reverted, exit 0. So the check genuinely detects a skipped embedding rather
than passing on any ontology that happens to be present.

**What surprised me, in order:**

1. **My own artifact had an ambiguous synonym, and the check caught it before Neo4j did.**
   `source` was a synonym of both Supplier and Channel. `synonym_index()` refuses a term
   claimed by two concepts, because a term that means two things routes questions to whichever
   was written first. Changed to `supply partner` and `traffic source`. This is precisely what
   the practitioner review in D9 is for, and it found something on a twenty-concept ontology
   written carefully in one sitting.

2. **An overlay is a fragment, not an ontology, and my first design could not express one.**
   The dangling-relation check sat in `__post_init__`, so a customer overlay adding a RepGroup
   that `REPRESENTS` a core Brand was refused at construction -- the single most obvious
   overlay shape there is. Moved completeness to an explicit `validate_relations()`, called on
   the merged result and again in `OntologyStore.write`. The write path is where it actually
   matters: a `MERGE` on a missing endpoint does not fail, it silently invents an empty
   concept with no description and no embedding.

3. **The lexical and vector legs disagree usefully.** For `stock band`, vector gives
   StockLevel/Inventory/Product and lexical gives StockLevel/Inventory/PurchaseOrder. Neither
   is wrong; they are answering different questions, which is the argument for K10 fusing them
   rather than picking one.

**Environment:** needs a Neo4j. I started a dedicated one rather than writing into the
existing `smg1-neo4j` container, which has been up three weeks and holds seven
Episode/Fact nodes belonging to someone else's memory testing. Ports 7688/7475 were already
taken, so it runs on **bolt 7701 / http 7700**:

    docker run -d --name egpt-retail-neo4j -p 7701:7687 -p 7700:7474 \
      -e NEO4J_AUTH=neo4j/egptretail neo4j:5

Needs `.venvs/demo` for `sentence_transformers`; embeddings are local, so no API key.

**Worth flagging:** the local Neo4j is **community edition**, which supports one user database.
D11 and the Azure alignment assume two databases in one cluster, which needs Enterprise. K3
works either way because the ontology has its own label, but K5 will put the semantic layer in
the same database locally rather than a second one. Not a blocker; a licence line for the
infrastructure hand-off.

---

### K02_domain_prose.py

**Package:** K2 (P1)
**Status:** PASS

**Description:** Ingested the 31-document curated retail corpus
(`Saturam-Inc/retailGPT`, `research_docs`, 483,339 words) into pgvector, split on headings and
tagged by origin. Then inspected the stored chunks and searched for phrases sitting in the
middle of sections.

**Result:** 1,457 chunks. Of 400 sampled, **400 begin at a heading**. All tagged
`origin=shipped`. Mid-section phrases return whole sections: *"what does a category captain
actually do"* returns `## 4. The Category Captain Model`; *"how should we set a markdown on
end-of-season stock"* returns `### 2.4 Markdown Cadence and Off-Price Channels`.

**The documented trap is real, and now measured.** With `MarkdownChunking` left at its
default, one document produced 24 chunks of which **zero** began at a heading — every one
started mid-sentence carrying no indication of its section. `split_on_headings=3` with
`chunk_size=12000` gives 55/55 starting at their heading. Identical output at 20,000
characters proves the size cap never binds, so no section is ever split by size. The settings
were chosen from that measurement and `chunking_evidence()` reproduces it.

**What surprised me:** `MarkdownReader`'s own default is `MarkdownChunking(chunk_size=...)`,
which leaves `split_on_headings` unset — so the trap propagates through the reader too, and
passing a chunker explicitly is required at both levels, not one.

**Environment:** `MarkdownChunking` needs the optional `unstructured` package, absent from
`.venvs/demo`. Installed with `uv pip install --python .venvs/demo/bin/python unstructured
markdown`. Worth adding to `demo_setup.sh` if other cookbooks start using markdown chunking.

---

### K03_ontology.py — re-derived from the curated corpus

**Package:** K3 (P1, implements D9, G10)
**Status:** PASS — artifact rebuilt at version 2.0.0

**Description:** Replaced the model-authored draft ontology with one derived from and traced
to the 31-document retail corpus. Re-ran the three package scenarios against it.

**Result:** 18 core concepts, 17 relations, each concept citing up to four corpus documents in
a `sources` field so a reviewing practitioner can check a claim rather than take the author's
word. Overlay concepts moved to `ontology/overlays/middleby.json`. All scenarios pass.

**What the corpus actually settled, in order of how much it changed:**

1. **A glossary is the wrong source for a data-entity ontology.** All 31 documents carry a
   `Term | Definition | Also Known As` table — 1,293 entries, which looks like exactly the
   ontology shape. Only **2 of my 20 concepts** appear in them. The reason is structural: a
   glossary defines the jargon a practitioner needs explained (GMROI, planogram, bullwhip
   effect), and nobody needs "Customer" explained. Glossary and entity ontology are near
   disjoint by design. Checked against the corpus *body* with word-boundary matching instead,
   19 of 20 concepts are strongly supported — Customer 2,432 mentions, Product 2,177, Brand
   1,786, Order 980.

2. **QuoteRequest is not general retail, and the corpus says so.** 3 of 31 documents, zero
   mentions of the phrase; quotes appear only in the B2B and wholesale documents. It failed
   the artifact's own test — *would a practitioner at any customer recognise this?* — so it
   and `Quote` moved to the Middleby overlay along with `RepGroup` (which comes from the
   client sample data's `Rep Group Owner` column). The shipped core no longer covers the
   flagship RFQ use case, which is correct rather than convenient.

3. **A synonym collision the corpus caught.** `offer` was a synonym of `Quote`. In this corpus
   "offer" means a *promotional* offer, so the synonym would have routed promotion questions
   to a quotation concept. Same class of defect as `source` meaning both Supplier and Channel,
   which the ambiguity check caught earlier. Removed.

4. **Three concepts are structural, not cited.** `StockLevel`, `OrderLine` and `WebSession`
   have thin corpus support because they are data-modelling facts rather than retail-practice
   topics — prose discusses baskets, not order lines. `sources` is empty for them and the
   model documents empty as *a flag for review, not a defect*.

**The overlay test is now meaningful rather than mechanical.** It was "load an overlay, check
the concept is present", which passes whether or not the overlay does anything. It now asks
the same question of both: *"someone asked us for a price before buying"* returns
Order/OrderLine/Contact against the core alone, and QuoteRequest/Quote/Order with the overlay
applied. The overlay has to change the answer.

**Caught by the assertions:** moving QuoteRequest out of core broke two tests in sections 3
and 4 that depended on it. Exactly what asserting instead of printing is for.

---

### Retail metric catalogue harvested for K11

**Status:** PASS (data, not a package)

**Description:** Every document carries a `Metric | Formula | Industry Benchmark | Notes`
section. Harvested to `product/egpt_retail/kpi/retail_metrics.json`.

**Result:** **295 metrics from 30 documents, every one with a formula and an industry
benchmark** — `Inventory Turns` = `COGS / Average Inventory at Cost`, 14–20x grocery, 4–6x
apparel. This is a K11 *input*, not a registry: the formulas name quantities, not columns, so
feasibility cannot be computed from them until they are bound to a customer schema.

**Worth carrying forward:** the corpus's own sourcing note warns that some figures are
*approximate benchmarks*, directional and not independently verified. Recorded in the
catalogue README. No benchmark from this file should become a customer-facing alert threshold
without a primary source behind it.

---

### load-knowledge entrypoint, and the embedder correction

**Package:** K2 / K3 deployment path
**Status:** PASS — and it corrected a wrong choice in both

**Description:** Added `egpt-retail load-knowledge`, the deploy-time counterpart to `migrate`:
ontology into Neo4j, prose into pgvector, idempotent, intended as a Helm
`post-install,post-upgrade` hook. Vendored the 31-document corpus into the package. Then
checked which embedder the memory layer had already decided on, and aligned to it.

**The correction, which is the substance of this entry.** K2 and K3 were using a bare
`SentenceTransformerEmbedder()` — `all-MiniLM-L6-v2` at 384 dimensions, the library default.
That is **not** the decision. `egpt.memory.embedding.MODE_DEFAULTS`, straight from SM-R4's
`DECISION.md`, sets:

| Mode | Backend / model | Dimensions |
|---|---|---|
| A `local` | `sentence_transformer` / `BAAI/bge-base-en-v1.5` | 768 |
| B `frontier` | `azure_openai` / `text-embedding-3-large` | 1024 |

Mode A is offline-capable, which ADR 0003 A.3c makes a hard constraint for air-gapped
deployments. `bge-base` was measured significantly ahead of a 384-dimension model
(Holm p=0.002). Mode B's 1024 is a constraint rather than a preference: the full 3072 exceeds
pgvector's 2000-dimension limit and 1024 tested as a statistical tie.

Knowledge now resolves through `resolve_memory_embedder` rather than making a second choice,
so prose, ontology and memory share one vector space. Three different embedders behind K10's
single retrieval entry point would have been three spaces nobody could reason about together —
and it fails silently, as worse retrieval rather than an error.

**Retrieval measurably improved at 768**, which is the SM-R4 evidence reproducing on this
corpus: `SKU` to Product went 0.671 to **0.857**, `vendor` to Supplier 0.796 to **0.827**.

**The dimension guard fired for real, on my own data.** `CREATE VECTOR INDEX IF NOT EXISTS`
does nothing when an index of that name exists, whatever width it was built at — so a model swap
leaves the old index and every write embeds at a width it cannot serve. Nothing errors.
The loader now reads the existing index width and refuses:

> The ontology vector index exists at 384 dimensions and the configured embedder produces 768.
> Every stored vector came from the previous model and is not comparable with the new one.
> Re-run with --rebuild-indexes to drop the index and re-embed.

Exit 1 without the flag; with it, 21 concepts re-embedded at 768 and 31 documents ingested.

**What surprised me:** I predicted this failure in the abstract when reviewing Azure
portability, then walked straight into it an hour later with my own store. Predicting a
failure is not the same as being guarded against it — the guard is.

**Deliberately not done:** the Helm `post-install,post-upgrade` Job that calls this. The chart
is Lane R's (R2, merged in #189) and adding a hook to someone else's chart without asking is
how two lanes end up with two conflicting jobs. Flagged for them; the entrypoint is ready.

---

### Embedding alignment completed: version tag, and two counting bugs

**Status:** PASS

**Description:** Checked what the memory decision fixes beyond the model, and closed the rest
of the gap.

**Result:** The decision also fixes **cosine** as the metric and an **`embedding_version`** tag
of the form `<model>|d<dim>|<metric>|<rev>`, stamped on every stored vector, which is what a
re-embedding migration cuts over on. PgVector already defaults to cosine, so that half was
aligned by accident. The tag was not stamped at all. Both stores now carry
`BAAI/bge-base-en-v1.5|d768|cos|v1`, identical to memory's, and the loader compares the tag as
well as the width — two models can share a dimension and still produce vectors that are not
comparable, so width alone is necessary and not sufficient.

**Two counting bugs, both found by reading output rather than by a check:**

1. **The corpus README was being ingested as prose.** `read_corpus` globs `*.md` and excluded
   only the table of contents, so the note *about* the corpus became four chunks of it. The
   run said "32 documents" and the corpus has 31. Now excluded, and the docstring says why
   both exclusions are the same kind of thing.

2. **The chunk count doubled to 2,914.** Not a loader defect: the cookbook and the
   `load-knowledge` entrypoint had the same default table name, so they were two writers of
   the same rows. Verified the loader is idempotent with itself — 1,457 after one run and
   1,457 after two, in an isolated table — and gave the cookbook its own table.

**What surprised me:** I wrote "idempotent, running it twice changes nothing" in the
entrypoint docstring before testing it. It happened to be true, but I had asserted it from
design rather than measurement, and the doubled count looked exactly like it being false. The
claim and the evidence arrived in the wrong order.

---

### K03_ontology.py — six concepts added from the corpus

**Package:** K3, artifact version 2.1.0
**Status:** PASS

**Description:** The ontology was thin. Measured which entity types the corpus discusses
heavily and the ontology lacked, then added the six clearest, grounded in corpus definitions
with source citations.

**The measurement:** `Price` appeared in all 31 documents with 1,496 mentions and had no
concept. `Payment` 27 documents, `Assortment` 27, `Promotion` 26, `Markdown` 20, `Employee`
17. Core goes from 18 to 24 concepts and 17 to 25 relations; with the Middleby overlay the
graph holds 27 concepts and 31 relations.

**Result:** all six reach their concept by meaning, on questions naming none of them —
*"how do people pay us?"* to Payment (0.808), *"how many staff are on the roster?"* to Employee
(0.802), *"what is on deal this week?"* to Promotion (0.805), *"what did we reduce to clear the
old season?"* to Markdown (0.787), *"is our range too wide?"* to Assortment (0.777).

**Two things the additions forced:**

1. **A new business area.** `Employee` had no home in the shipped six — customer, product,
   inventory, supply_chain, marketing, sales. The corpus has a whole document on store
   operations and workforce, so `operations` was added to the vocabulary (1.1.0). Worth noting
   the vocabulary being extensible is exactly what made this a data change rather than a code
   change, which is what moving off Python enums bought.

2. **A third synonym collision, caught again.** `range` was claimed by both `ProductCategory`
   and the new `Assortment`. In retail, range means assortment, so it was removed from
   ProductCategory. That check has now caught three real ambiguities — `source`, `offer`,
   `range` — each of which would have silently routed questions to whichever concept was
   written first. It is the highest-yield check in the package.

**Also corrected:** `Markdown` initially missed *"what did we knock down to clear it?"*,
returning Inventory and Order. Its synonyms and question patterns were thin relative to its
description. Adding *"reduced to clear"* and a matching question pattern fixed it. A concept's
retrievability depends on the words in it, not on the concept being correct.

---

### K2 and K3 reviewed together, practitioner lens — three defects, one of them new

**Packages:** K2, K3 (artifact 2.2.0)
**Status:** PASS — after fixes

**Description:** Read the whole ontology fresh, inspected graph structure, sampled stored
chunks, and ran ten questions a retail practitioner would actually ask — none of which the
artifact had been written around — against both layers. Then fixed what that found.

**Two defects the review surfaced before the practitioner lens even applied:**

1. **The K03 cookbook left a fake concept in the live ontology.** *"what is the return rate
   on electronics"* answered `Return, Warranty`. There is no Warranty in the artifact — it is
   the core-upgrade test fixture, and the cookbook never restored the store afterwards. Anyone
   running the cookbook after the loader had a phantom concept in their deployment. The
   cookbook now writes the real artifact back and asserts Warranty is gone.

2. **28% of K2's chunks were heading-only shells.** 410 of 1,457 were an `##` title whose text
   lived entirely in the `###`s beneath. Each was embedded and could match on its title alone.
   `BodiedChunking` now drops any chunk with no body: 1,457 became 1,049. On the sample
   document the library default has 0 shells and the heading split has 13 — the default's
   chunks are not empty, they are fragments starting mid-sentence; shells are the price of
   the heading split, now paid and removed.

**The practitioner test, before and after** (ontology top-2):

| question | before | after |
|---|---|---|
| which stores are underperforming | Order, PurchaseOrder | **Store**, Order |
| what is our shrink | Inventory, Assortment | **Shrinkage**, Markdown |
| return rate on electronics | Return, *Warranty* | Return, **Refund** |
| how much did that promo lift sales | Promotion, Campaign | Promotion, Campaign |

**Content changes, all corpus-cited:** added `Store` (retail says store; `Location` is a
data-modeller's word and *"which stores are underperforming"* had missed entirely), `Shrinkage`
(a top-three store-ops concern with a whole corpus section and no concept), `Refund` (Return
had a `refund` synonym but nothing linked a return back to its payment). Fixed `Product
HAS_PRICE Price` from one-to-one to one-to-many — zone, channel and time pricing make
one-to-one wrong for any real retailer. Stripped `promotion` from Campaign's synonyms.
De-B2B'd core `Contact`, which still said *"request a quote"* after quotes moved to the
overlay. Removed editorialising from `Employee` and `Brand` — an ontology should define, not
opine. Core is now 27 concepts, 31 relations; 30 with the overlay.

**A fourth collision class, now tooled.** `promotion` was a synonym on Campaign while a
Promotion *concept* existed. The synonym check could not see it: the other concept never lists
its own name as a synonym. `synonym_index()` now reserves every concept's name (written and
spaced forms) and refuses a synonym that equals one. Verified against the deliberate
Promotion/Campaign case. That makes four real ambiguities caught across three passes —
`source`, `offer`, `range`, `promotion` — the strongest argument that a fifth pass by the
author is not the answer, and K-2 in `open-items.md` stays open.

**What surprised me most, and it is the most important line in this entry.** The first
post-fix run showed two *regressions*: *"promo lift"* fell to Order/OrderLine and *"gross
margin"* lost Markdown. Minutes later, with nothing changed, Promotion was top at 0.794 and
Markdown top at 0.763. The difference was the vector index: **Neo4j populates it
asynchronously, and a query against a half-built index does not fail — it returns a confident
answer from whatever fraction is indexed.** On 30 nodes the window is under a second, which
is exactly why it was never seen before. `OntologyStore.write()` and `ensure_indexes()` now
block on `SHOW INDEXES` until ONLINE; verified that the very first query after a rebuild is
correct. The loader was reporting success before the store could answer correctly.

**Two of my own claims in the review were wrong, corrected here.** The `## 12. Key Metrics`
chunk that answered *"repeat buyers"* is not a shell — it has a 1,900-character body holding a
repeat-purchase-rate metric, so it is a fair answer. And dropping shells has a real cost I
had not weighed: `## 2. Gross Margin Analysis` was a shell, and while it existed it was a
signpost that won the gross-margin query; without it a case-study chunk outranks
`### 2.1 Gross Margin by Category`. The better design is to *inherit* the parent heading into
each child chunk rather than delete the signpost. Logged as a follow-up, not done.

---

### K1_memory_wiring.py

**Package:** K1 (P1, implements G1, G2)
**Status:** PASS

**Description:** Wired memory end to end and ran the package's three scenarios: recall across
a session boundary, startup with the customer identifier removed, and the scope a scheduled
run writes under.

**Result:** All pass, including the one that matters most — **a fact stored in one
conversation was recalled in the next.** Told the agent *"Our warehouse in Rotterdam is the
one that handles all EU returns"* in session `k1-conv-1`; asked *"Which warehouse handles our
EU returns?"* in `k1-conv-2` and got *"Your Rotterdam warehouse handles all EU returns."*
That is G1 closed in fact rather than in wiring.

**What K1 actually had to fix, in three parts:**

1. **The per-tenant builder was orphaned.** `build_memory_provider_for` existed and only the
   consolidator called it; no agent had a provider. `build_agents` now attaches one.
2. **A provider alone is silent.** `_agent_memory_scope` returns `None` when
   `run_context.tenant_id` is unset, so an agent with a provider and no tenant is exactly as
   memoryless as one without. `FixedTenantMiddleware` stamps the configured customer slug.
3. **Stamping the attribute is not enough**, and this is the part that would have looked
   finished and not worked. `build_run_context_for_request` never reads
   `request.state.tenant_id`; it calls `resolve_tenant_id` over `request.state.claims`. A
   middleware writing only the attribute leaves every `RunContext` at `tenant_id=None` and
   fixes nothing on the path that matters. The middleware injects the claim, so the run path
   keeps using the one security-reviewed resolver instead of gaining a second way to arrive
   at a tenant.

**Mutation-tested exactly that.** Changed the middleware to stamp the attribute and skip the
claim — the plausible half-fix — and the cookbook failed with *"the middleware did not stamp
the claim the run path reads"*, exit 1. Restored, exit 0.

**A worse failure than `None`, found while reading the resolver.** `resolve_tenant_id` falls
back to the verified `user_id` when no claim is present. So without a configured slug a
single-customer deployment does not merely lose memory — it silently partitions into **one
tenant per person**, and the shared memory the deployment exists to accumulate never
accumulates. The middleware never overrides a real claim, only fills an absent one, so it
cannot cause the cross-tenant version of this.

**A misalignment K1 also had to close.** `build_memory_config` hardcoded
`mode=ProviderMode.FRONTIER` while the knowledge layers read `retail_provider_mode`, which
defaults to `LOCAL`. Memory would have embedded at Mode B's 1024 while prose and the ontology
embedded at Mode A's 768 — three vector spaces behind K10's single retrieval entry point,
failing silently as worse answers. Both now read one setting.

**Deliberate design note on the startup check.** `assert_memory_wired` reports every fault at
once rather than the first, so a misconfigured deployment is fixed in one pass instead of one
restart per fault; verified with a three-fault deployment. It also distinguishes *"this
deployment has no configured tenant"* from *"it has one and it is unset"* via a sentinel,
because `tenant_id=None` alone made the refusal name the symptom — an agent with no provider —
rather than the cause, the missing customer slug.

**Two things I broke and fixed while doing this.** I assumed `build_agents` returned wrappers
with an `.agent` attribute; it returns agents directly, and the factory raised
`AttributeError` on every start until corrected — caught by running Lane R's cookbooks, not
mine. And `R01_local_stack` then failed legitimately: it configures no customer slug, so the
new check refused to build. That is the check working, not a defect, but it is a real
consequence of K1 for a cookbook written before memory was wired, so R01 now sets a slug the
way a real deployment does. Running the *other lane's* cookbooks is what surfaced both.

**Pre-existing, not mine:** `R03_build_contract_fixture` fails on a stale committed fixture
and asks for `./scripts/gen_api_models.sh`. Verified it fails identically with my changes
stashed. Lane R's asset and generator; left alone and raised as R-7.

---

### K14 second half — closed, with five template fixes

**Package:** K14
**Status:** PASS — the template held, and leaked in five places

**Description:** Lane R wrote `join_path_explainer` from `SKILL_TEMPLATE.md` alone and
recorded five questions rather than asking them. That is the evidence K14 asked for: a
conforming agent produced from the template without a conversation. All five were real gaps
and all five are now fixed in the template.

1. **The enum-versus-vocabulary rule was stated only for two named fields.** Generalised: a
   set that can vary by customer is a vocabulary and is rendered; one that enumerates outcomes
   of a computation stays an enum and is written out.
2. **`writes: nothing` was undocumented as a normal value.** It is the normal value for an
   explanation agent, and Lane R writes three.
3. **"You propose" reads oddly for an explainer.** Generalised the rule's first sentence to
   cover both proposing and explaining, and added the instruction Lane R had to guess: copy
   the four verbatim even where one reads oddly, because reviewing seven prompts against each
   other only works if the same four sentences appear in all seven.
4. **No place to name the executor an agent's output is checked against.** Added
   `checked_against` to the header. Lane R had invented the right thing —
   `verdict_for_path()` beside the schema — with nowhere to declare it.
5. **"Shipped ontology" was ambiguous about product fixtures.** Clarified: the line is
   ownership, not vocabulary. Anything we ship is fair game; anything read from a customer's
   warehouse is not.

**What surprised me:** every one of the five is a sentence I would have written if asked, and
none of them occurred to me while writing the template. Handing it to someone else was worth
more than another pass by the author — which is the same lesson the ontology's four synonym
collisions taught, arriving from the opposite direction.

---

### P1 verification pass — three defects in K1, and one invalid piece of evidence

**Packages:** K1, K2, K3 (all of Lane K's P1)
**Status:** PASS — after fixes; 155 product tests, 8 Lane K and 5 Lane R cookbooks green

**Description:** Verified P1 against the definition of done rather than from memory: sync/async
audit over every public def, framework purity, `validate.sh`, `pytest product`, and every
cookbook in the folder including Lane R's. Then fixed what that found.

**Lane R had added a `product` target to `validate.sh`** — K-4 resolves itself — and it
failed. Two failures were environmental (X-5, X-6). Two were mine, and pulling on them found
three defects in K1 and one piece of evidence that did not prove what it claimed.

**Defect 1 — importing the app opened a database.** `build_memory_provider_for` connects to
pgvector and provisions schema on construction, and `app/main.py` calls the factory at module
import. So `import egpt_retail.app.main` opened Postgres, and Lane R's pure-construction test
broke. Fixed by moving the provider and `assert_memory_wired` into an EgptOS lifespan. That is
not a workaround — it is what G1 literally asks for. *Fail at startup* means when the server
boots, not when a test imports a module.

**Defect 2 — the tenant middleware was in the wrong position, and only a real request could
show it.** `jwt.py:1111` does `request.state.claims = payload`: a replace, not a merge.
Starlette runs the last-added middleware first. Installed after `get_app()`, mine was
outermost — it injected the tenant claim, then auth ran and overwrote the dict. Every
authenticated request lost the tenant, which is exactly the Entra deployment shape (JWTs with
no tenant claim). The K1 cookbook tests the middleware by calling `dispatch` directly, so it
could never see this. Now installed on a `base_app` that EgptOS builds on, so auth sits
outside it. Two tests pin the order: one with a replacing fake auth that also proves the
wrong order fails, one through the real EgptOS stack with `TestClient`.

**Defect 3 — `str(None)`.** With no slug, the factory would have stamped a tenant literally
named `"None"` — a partition nobody meant to create. The middleware now installs only when a
slug exists; an unconfigured deployment is refused by the lifespan instead, with the message
naming the missing slug.

**The invalid evidence.** K1's recall test ran both conversations in one process on one
`Agent` object with `enable_user_memories=True`, a soon-deprecated in-process path. It
"recalled" Rotterdam and proved nothing: the store could have been empty and the answer
identical. The store *had* written — 12 rows under `middleby/k1_recall_probe/alice` — but the
test could not tell provider recall from in-process recall. Rewritten: process A stores two
facts and flushes with `provider.close()`; process B, with a fresh provider and agent, asks
cold. **Result: "Your Rotterdam warehouse handles all EU returns."** And `memory_vectors`
queried directly: `middleby/k1_recall_probe/alice ×16`, `middleby/k1_recall_probe/__scheduler__ ×2`.
Scenario 3 — what scope a scheduled run writes under — now has real rows, written the way a
scheduled run writes, rather than a scope builder returning the right string.

**Also closed:** `aload_knowledge` (an I/O function with no async twin, missed by my own
audit heuristic); `product/tests/test_k1_memory_wiring.py`, ten tests, so `validate.sh
product` guards K1 from here; and `retail_provider_mode` now defaults to `frontier` — the
deployment's truth — with local set explicitly, which makes Lane R's original test correct as
written rather than mine.

**What surprised me:** the middleware bug. Every test I had written for it passed, because
every test called the middleware in isolation. The failure only exists in composition with
auth, and only on authenticated requests, and only because of one assignment on one line of
a file I had not read. It would have shipped. Testing the *stack* rather than the *unit* is
the difference, and it is now the pattern for anything that touches a request.

**Pre-existing, recorded, not mine:** `mypy product` fails on numpy's stubs under a py39
target (X-5); the api-contract check needs `openapi_spec_validator` (X-6); Lane R's stale
fixture (R-7); the two mypy errors in `libs/agno/egpt/integrations/knowledge/` (X-2).

---

### Verification pass, addendum — the pass contaminated `libs/` and then caught itself

**Status:** FIXED

The DoD #7 check ("nothing in `libs/` but the two intended files") was run as `git diff HEAD`
and printed empty, which I read as clean. It was empty because the drift was already
*committed*. Run across the whole branch — `git diff origin/main..HEAD -- libs/` — it showed
**14 files**. Twelve were 1–3 line import-reorder diffs in framework files and tests I never
opened: the same `format.sh` import-sort drift reverted in P0 (X-4), reintroduced by one
broad `ruff --select I --fix libs/agno` during K1 and swept up by `git add -A libs/agno`.

Restored all twelve to `origin/main` in a dedicated revert commit rather than rewriting
history, so the mistake and its correction are both on the record. `libs/` now carries
exactly `memory/startup.py` and `os/middleware/fixed_tenant.py`.

**Two lessons, both about the check rather than the drift.** Compare against `origin/main`,
never `HEAD` — a branch-scoped claim needs a branch-scoped diff. And never point a fixer at
`libs/agno` as a directory; name the files. X-4 is not a curiosity about other people's
diffs; it bit this branch.

---

## Lane C — phase zero

### C01_landing_contract.py

**Package:** C1 (P0, landing contract and storage layout — implements D4)
**Status:** PASS

**Description:** Ran the loader half of the verify step by hand: discovered the three
hand-written fixture files under `product/egpt_retail/contracts/_fixtures/landing/` (one per
source — analytics, CRM, session replay), parsed each path with
`egpt_retail.contracts.landing.parse_landing_path`, and validated every row with
`validate_row`. The script imports nothing but that module and the standard library, so
"loads all three without asking a question" is checkable rather than asserted. Second half:
wrote a second, overlapping CRM pull (a byte-identical duplicate row and one row with a
corrected, previously-null `brand` and an advanced watermark) and ran the last-write-wins
dedup rule the contract specifies.

**Result:** All three sources loaded — 5 analytics rows, 7 CRM rows across two files (3
contact/deal/deal-stage-history shapes), 2 session-replay rows. One row-level defect
surfaced: CRM contact `c_501` landed with `brand: null`, the known-dirty case the contract
names explicitly — reported, not dropped, matching C3's own evidence bar. After the dedup
pass over both CRM files (7 raw rows → 4 deduplicated, grouped by `(record_type,
object_id)`, kept by greatest `last_modified_at`), the duplicate `d_900`/`c_500` rows
collapsed to one each and `c_501`'s later pull (`brand: "Follett"`) resolved the earlier
defect — `validate_row` returns no defects for it post-dedup.

**What this proves that the prose alone doesn't:** the contract's re-run clause ("raw is
immutable; a re-pulled window lands a new file; de-duplication is downstream by key columns,
last write wins by watermark") is not just asserted — the second half of the script builds
both files, keeps them both on disk unmutated, and shows the reduction actually produces the
row count and values the clause claims.

---

### C01_minio_roundtrip.sh

**Package:** C1 (P0, landing contract and storage layout — implements D4)
**Status:** PASS

**Description:** The delivery plan's verify step says "upload it"; the fixture-file run above
proves the contract's path/format/schema logic but stands in object storage with a local
directory. This script closes that gap: started MinIO via `deploy/compose.yaml --profile
full` (new `minio` service, reusing the `egpt-landing` bucket name already reserved for it in
R2's kind-cluster local-infra chart), used the official `minio/mc` image to upload the four
fixture files to `egpt-landing`, listed the bucket, downloaded everything back to a fresh
directory, then re-ran `C01_landing_contract.py` unmodified (via its new
`EGPT_LANDING_FIXTURES_ROOT` override) against the round-tripped copy.

**Result:** All four objects landed at exactly the paths `landing_path()` specifies —
`raw/{source}/dt=2026-09-01/{source}-{ingested_at}.ndjson` — confirmed by `mc ls -r` before
download. The re-run against the downloaded copy reproduced every result from the local-disk
run identically: same 5/7/2 row counts, the same `c_501` `brand: null` defect surfaced (not
silently dropped by the round trip), and the same last-write-wins dedup outcome (7 → 4 rows,
`c_501` resolved to `"Follett"` with no remaining defects).

**One thing worth recording:** `mc cp --recursive SRC DST` includes `SRC`'s own directory
name in the destination keys — `mc cp --recursive /fixtures/raw local/bucket/raw` lands at
`bucket/raw/raw/...`, not `bucket/raw/...`. Confirmed by hand against a throwaway MinIO
container before writing the script, rather than guessed: the fix in the final script is to
target the bucket root (`local/$BUCKET`, not `local/$BUCKET/raw`) and let `mc` append `raw/`
itself.

---

### Review round 1 — all three scripts re-run

**Package:** C1
**Status:** PASS — after fixing 13 findings from automated review on PR #190

**Description:** An automated review (two independent reviewers plus an auditor pass that
reproduced every finding against the worktree at `45d4802`) raised 3 Major, 9 Minor and 1
Nit finding. Every one was reproduced before being fixed; none was taken on faith.

**Reproduced and fixed, `landing.py`:**

1. **Naive-datetime silent mis-shift (Major)** — `landing_path()`'s
   `ingested_at.astimezone(timezone.utc)` on a naive value assumes the *process's local
   timezone*, not UTC. Reproduced: the same instant, built naive under `TZ=Asia/Kolkata` as
   `datetime.utcnow()` would, produced a filename 5.5 hours off. `landing_path` now refuses a
   naive `ingested_at` outright. Pinned by
   `test_landing_path_rejects_naive_datetime` and
   `test_landing_path_is_stable_across_input_timezones` (the latter proves the same instant
   in two different tzinfos now produces the identical path).
2. **Non-scalar key values pass clean, crash the caller's dedup (Major)** — a list/dict in a
   key column returned zero defects from `validate_row`, then raised `TypeError: unhashable
   type` in the cookbook's own `(record_type, object_id)`-keyed dedup. `validate_row` now
   flags a non-scalar key column as a defect. Pinned by
   `test_validate_row_rejects_non_scalar_key_value`.
3. **A non-object JSON row crashes instead of being reported (Major)** — NDJSON permits a
   bare string, list or number per line; neither `validate_row` nor `resolve_stream_id`
   checked for it, so each raised an unhandled `AttributeError`/`TypeError`. Both now check
   `isinstance(row, Mapping)` first — `validate_row` returns a defect tuple (it never raises
   on malformed input), `resolve_stream_id` raises a clean `ValueError` (consistent with its
   existing refusal of an unknown `record_type`, which was already exception-based). Pinned
   by `test_validate_row_rejects_non_mapping_row` and
   `test_resolve_stream_id_rejects_non_mapping_row`.
4. **Key+watermark column double-reported when null (Minor)** — `analytics.event.occurred_at`
   is both; a null value produced two defects for one problem. The watermark check now skips
   its own null report when the key-column loop already covered it, but still checks
   parseability unconditionally. Pinned by
   `test_validate_row_does_not_double_report_key_and_watermark_column` and
   `test_validate_row_still_checks_watermark_parseability_when_also_a_key` (the second exists
   specifically so the first fix couldn't be over-applied into swallowing a real parse
   failure).
5. **`parse_landing_path` leaked a raw stdlib message for a calendar-invalid date (Minor)** —
   `dt=2026-13-40` matches the regex, then `date.fromisoformat` raised its own `ValueError`
   text instead of this function's consistent wrapper. All three fallible calls now share one
   `try`/`except`. Pinned by the `dt=2026-13-40` case in the parametrized
   `test_parse_landing_path_rejects_adversarial_input`.
6. **Empty string not treated as dirty (Minor)** — only literal `null` triggered the
   known-dirty report; `brand: ""` passed clean, which is exactly the "silently coerced" case
   `LANDING_CONTRACT.md` says the contract refuses to let through. A new `_is_blank` helper
   treats a whitespace-only string the same as `null`. Pinned by
   `test_validate_row_treats_blank_string_like_null`.
7. **`LandingSchema.__post_init__`'s six guards were unexercised anywhere in the repo
   (Minor)** — correct, but disabling any one of them produced byte-identical cookbook
   output, because nothing ever constructed a broken schema. Closed with direct unit tests,
   one per guard, in the new `product/tests/test_landing_contract.py`.
8. **`LANDING_SCHEMAS` documented as frozen, actually a mutable `dict` (Minor)** — now wrapped
   in `types.MappingProxyType`, so the claim is enforced, not just written down. Pinned by
   `test_landing_schemas_is_immutable`.

**Reproduced and fixed, the cookbook and fixtures:**

9. **Dedup tie-break untested (Minor)** — `>` vs `>=` on the last-write-wins comparison
   produced identical output, because the fixtures' only two ties (`c_500`, `d_900`) were
   byte-identical across both pulls. Added `h_9002`, a `deal_stage_history` row present in
   both CRM pulls with the *same* `last_modified_at` but a different `stage`
   (`quote_requested` vs. `quote_sent`), and asserted on which one survives dedup
   (first-processed, since the comparison is strictly `>`). Documented as an intentional,
   if non-obvious, tie-break in `LANDING_CONTRACT.md`'s re-run section.
10. **`total_defects` never asserted; the "missing outright" branch unexercised (Minor)** —
    added `d_901`, a `deal` row missing `quote_request_source_url` entirely (the exact column
    the review named), which exercises `validate_row`'s "missing required column" branch
    separately from `c_501`'s "present but null" branch. `total_defects == 2` is now asserted,
    not just printed.
11. **`parse_landing_path`'s round-trip check never adversarially exercised (Minor)** —
    closed by `test_parse_landing_path_rejects_adversarial_input` (mismatched source,
    traversal, wrong extension, malformed timestamp, calendar-invalid date, uppercase source,
    unknown source) and `test_parse_landing_path_round_trips_landing_path`.
12. **MinIO round trip checked object names, not bytes (Minor)** — `mc ls -r` proves the keys
    round-tripped but not the content. `C01_minio_roundtrip.sh` now computes a sha256
    manifest of every uploaded file and every downloaded file and diffs them before handing
    off to the Python re-verification; a lossy or truncating round trip now fails the script
    instead of passing silently.
13. **Redundant re-read of the two CRM fixture files (Nit)** — the discovery loop now keeps
    every file's parsed rows in `rows_by_path`, and part 2 reuses them instead of calling
    `load_ndjson` again.

**Result after fixing:** `python -m pytest product/tests/test_landing_contract.py` — 30
passed. `C01_landing_contract.py` — same shape as before (5/10/2 row counts, now 10 CRM rows
across two files after the new fixture rows, `crm` 10 → 6 after dedup, 2 asserted defects
instead of 1), plus the new tie-break section. `C01_minio_roundtrip.sh` re-run end to end
against real MinIO — checksums match, Python re-verification against the downloaded copy
reproduces the local-disk run exactly. `./scripts/format.sh` and `./scripts/validate.sh`
clean on every touched file.

**Not changed:** the tie-break's actual outcome (first-processed pull wins on an exact
watermark tie) — the review flagged the *absence of a test*, not the behaviour itself, and
the behaviour is defensible as written (a genuine tie means the source system didn't
timestamp the change, which this contract can't resolve on the loader's behalf).

---

### Review round 2 — correctness fixes, coverage, and contract-vs-plan gaps

**Package:** C1
**Status:** PASS — after fixing 9 findings (2 High, 3 Medium, 4 Minor/Nit) from a second
automated review pass on PR #190

**Description:** Round 1 closed 13 findings against the code as written. This pass checked
the contract against `docs/plan/delivery-plan.md` and `build-plan.md` directly, not just
against itself, plus follow-up coverage gaps from the round-1 fixes. Every finding was
reproduced before being fixed.

**Reproduced and fixed, `landing.py` and `LANDING_CONTRACT.md`:**

1. **`brand` missing from `crm.deal` (High)** — C3's own verify step is "deals with empty or
   free-text brand", but `crm.deal` had no `brand` column; that verification could not run
   against this contract as written, and RFQ funnel attribution needs brand at deal grain
   since one contact can request quotes across more than one brand. `brand` is now a
   required column on `crm.deal`, documented as authoritative there (`crm.contact`'s `brand`
   reflects the contact's own record, not the per-quote-request brand). Pinned by
   `test_validate_row_requires_brand_on_deal`.
2. **Watermarks accepted naive and non-UTC-offset timestamps (High)** — the dedup rule
   compares watermarks lexically (`C01_landing_contract.py`), and
   `"2026-09-01T09:00:00+02:00" > "2026-09-01T07:30:00Z"` is true as strings but false as
   instants. `_parses_as_timestamp` now requires a literal `Z` suffix, closing the row-level
   counterpart to round 1's naive-`ingested_at` fix. Pinned by
   `test_validate_row_rejects_naive_watermark_timestamp` and
   `test_validate_row_rejects_non_utc_offset_watermark_timestamp`.
3. **Key-column type check looser than its own comment (Minor)** — `isinstance(value, (str,
   int, float, bool))` let a `bool`/`int`/`float` key value pass clean, contradicting "every
   current key column is string-typed." Narrowed to `isinstance(value, str)`. Pinned by
   `test_validate_row_rejects_non_string_key_value`.
4. **Key+watermark double-report guard covered blank but not non-scalar (Minor)** — a
   `list`/`dict` value in a combined key+watermark column (`analytics.event.occurred_at`)
   still produced two defects for one root cause. The guard now also covers "already flagged
   non-string by the key-column loop". Pinned by
   `test_validate_row_does_not_double_report_non_scalar_key_and_watermark_column`.
5. **Analytics `brand`/`product_id` non-nullable (Medium)** — every analytics event required
   both, making a homepage `page_view` a false defect. Now nullable, required only for the
   named RFQ-funnel events. Pinned by `test_validate_row_suppresses_defect_for_nullable_column`.
6. **No `identity_key` column anywhere (Medium)** — the plan's one shared-identity blocker
   had no reserved column, and raw email landed with no acknowledgement of the gap. Added a
   required-but-nullable `identity_key` to `analytics.event`, `crm.contact` and
   `session_replay.session`; `LANDING_CONTRACT.md` now states explicitly that raw email lands
   in the clear in 1.0.0 as a known privacy gap pending the plan's privacy sign-off.
7. **`quote_request_source_url` required on every deal (Low)** — a renewal or upsell deal
   has no referring URL and would false-defect. Made nullable on `crm.deal`, with the
   conditional-on-`stage` requirement documented as a per-row rule for R4.

**Reproduced and fixed, tests and fixtures:**

8. **Coverage gaps from round 1's fixes (Minor)** — `test_validate_row_rejects_non_scalar_
   key_value` only covered `list`, not `dict` (now parametrized over both); the "missing
   required column" and `nullable_columns` runtime-suppression branches each had no dedicated
   unit test (only cookbook coverage). Closed by
   `test_validate_row_reports_missing_required_column` and
   `test_validate_row_suppresses_defect_for_nullable_column`.
9. **Fixtures README stale (Nit)** — round 1 added `d_901` and `h_9002` to the CRM fixtures
   but never updated the README describing them. Brought back in sync, including the new
   homepage `page_view` row and every row's new `identity_key: null`.

Also fixed, not from this review but caught while touching the same file:
`deploy/compose.yaml`'s MinIO image pinned to a release tag instead of `:latest`, matching
every other image in the file.

Two gaps recorded rather than fixed, per each finding's own "not a blocker, document it"
recommendation, in `LANDING_CONTRACT.md`'s new "Known gaps in 1.0.0" section: no dataset
segment in the storage path (breaking to add, and nothing has landed against the convention
yet), and no deletion/tombstone semantics.

**Result after fixing:** `python -m pytest product/tests/test_landing_contract.py` — 40
passed. `C01_landing_contract.py` — analytics 5 → 6 rows (new homepage `page_view`), CRM
fixture rows gained `brand` (deals) and `identity_key` (contacts); `total_defects` stays
exactly 2, unchanged — the new required columns were added with values that keep the
existing fixtures conforming rather than introducing new defects. `./scripts/format.sh` and
`./scripts/validate.sh` clean on every touched file.

**Not changed:** whether `crm.contact` should drop `brand` entirely now that `crm.deal`
carries the authoritative one — kept on both and documented which is which, since removing
it would be a guess about what the real CRM models without evidence either way.

---

### Review round 3 — a real gap in round 2's own fix, plus doc/robustness nits

**Package:** C1
**Status:** PASS — after fixing 1 real finding and 3 nits from a review of round 2's fix
(`f6580c34`) on PR #190

**Description:** A review of the round-2 commit itself, not the original code. Every
finding was reproduced against `f6580c34` before being fixed or, in one case, ruled out.

**Reproduced and fixed:**

1. **Non-scalar value in a non-key required column passed clean (Minor)** — round 2's fix
   for finding #6 (blank/empty-string columns) added `_is_blank` to the required-columns
   loop but never added the type check the key-column loop already has. Reproduced:
   `validate_row("crm.contact", {..., "brand": ["Blodgett", "Follett"], ...})` returned
   `()`. `validate_row` now also flags a list/dict in a non-key required column, checking
   `isinstance(value, (str, int, float, bool))` rather than `isinstance(value, str)` --
   unlike a key, a required column may legitimately be numeric (session replay's click
   counts). Pinned by `test_validate_row_rejects_non_scalar_required_column_value`. First
   attempt at this fix broke `test_validate_row_accepts_a_conforming_row` and
   `test_validate_row_suppresses_defect_for_nullable_column`: the new check ran even on a
   nullable column's `None`, which is not an `isinstance` of any of those four types. Fixed
   by restructuring so the type check is only reached for a non-blank value.
2. **`LandingSchema.__post_init__` guard count said "four", is six (Nit)** — corrected in
   both `TEST_LOG.md` (this file, the round-1 entry) and `CHANGELOG.md`.
3. **`C01_minio_roundtrip.sh`'s manifest loop not NUL-delimited (Nit)** — `find ... | sort |
   while read -r f` would corrupt on a filename with a newline. Not exploitable against
   today's fixed fixture filenames; fixed anyway with `-print0` / `sort -z` / `read -d ''`
   for whoever copies the pattern later.

**Investigated, not reproduced:**

4. **Claimed double-report for a non-scalar key+watermark value.** The finding said
   `watermark_already_reported` only guarded `None`, not the non-scalar case, for a column
   that is both a key and the watermark (`analytics.event.occurred_at`). Reproducing the
   exact input (`occurred_at: ["not", "a", "scalar"]`) against `f6580c34` returns exactly
   one defect, not two -- round 2's fix (`_is_blank(watermark) or not isinstance(watermark,
   str)`) already covers this. No change made; replied on the thread with the reproduction.

**Also fixed, not from this review but caught while touching the same test file:** the
adversarial `uppercase source` case in `test_parse_landing_path_rejects_adversarial_input`
now notes it pins the `SourceName` enum layer, not `_PATH_RE`'s own case restriction, since
the regex's `[a-z_]+` already excludes uppercase.

**Result after fixing:** `python -m pytest product/tests/test_landing_contract.py` — 41
passed. `C01_landing_contract.py` and `C01_minio_roundtrip.sh` (against real MinIO)
re-run end to end, unchanged output -- `total_defects` stays exactly 2, no fixture touched
this round. `./scripts/format.sh` and `./scripts/validate.sh` clean on every touched file.

---

### Review round 4 (post-merge) — session_replay's watermark, a packaging gap, and a follow-up PR

**Package:** C1
**Status:** PASS — one real, breaking finding fixed (1.1.0); one packaging gap fixed;
rounds 2 and 3 above carried forward into `main` via a follow-up PR

**Description:** PR #190 merged at the round-1 commit (`b7f7a459`) before rounds 2 and 3
above were pushed to it, so those fixes never reached `main` through that PR — they landed
through this follow-up PR instead, cherry-picked onto a fresh branch off `main` and verified
again end to end (41 tests, cookbook, real-MinIO roundtrip, all unchanged from when they
were first fixed).

One new finding arrived on the merged PR after the fact, from Lane R having built R4
against this contract in PR #193 (still open, not yet merged):

1. **`session_replay.session`'s watermark cannot detect a correction (breaking).**
   `session_start_at` never moves for a session, so a re-pull with corrected click counts
   ties on it forever and the tie-break (earliest-pulled wins) silently discards the
   correction — the contract's own re-run rule producing real data loss, not a loader bug.
   The other four streams don't have this problem (analytics is immutable; the three CRM
   streams carry `last_modified_at`, which moves). Fixed by making `last_updated_at` the
   watermark instead, `session_start_at` staying required but no longer structural.
   `CONTRACT_VERSION` bumped to `1.1.0` -- the one breaking change, scoped to this stream
   only. Pinned by `test_session_replay_watermark_is_last_updated_not_session_start` and
   `test_validate_row_accepts_both_pulls_of_a_corrected_session_replay_row` (renamed in
   review round 5 below -- its original name overclaimed what it tested).
2. **Fixtures not covered by `package-data` (additive).** `product/pyproject.toml` globbed
   `contracts/_fixtures/*.json`, which doesn't match
   `contracts/_fixtures/landing/raw/**/*.ndjson` -- a non-editable install shipped the
   contract without its demonstration fixtures. Added
   `contracts/_fixtures/landing/raw/*/*/*.ndjson` and a coverage test,
   `test_landing_fixtures_are_covered_by_package_data_glob`, mirroring R4's own
   `test_r4_sql_assets.py` shape.

**Result after fixing:** `python -m pytest product/tests/test_landing_contract.py` — 44
passed. `C01_landing_contract.py` unchanged output (`total_defects` still exactly 2).
`C01_minio_roundtrip.sh` re-run against real MinIO -- unaffected, since it doesn't touch
session replay's re-run behaviour. `./scripts/format.sh` and `./scripts/validate.sh` clean
on every touched file.

**Not changed:** whether R4's own SQL needs updating for the new watermark column --
that's Lane R's call in PR #193, flagged in this PR's description rather than assumed here.

---

### Review round 5 — PR #194's own automated review: format-checking gap, version-compat gap, coverage

**Package:** C1
**Status:** PASS — two real gaps in the 1.1.0 change fixed, one test renamed for an
overclaim, five coverage/doc gaps closed

**Description:** PR #194 (this follow-up) received two independent round-1 automated
reviews, both posted under the same reviewer, 3 Major + 6 Minor + 3 Nit total (one Minor
duplicated across both reviews). All three Majors were about the 1.1.0 change itself, not
rounds 1-3:

1. **`session_start_at` silently lost timestamp format-checking (major).** It was
   format-checked before 1.1.0 because it was the watermark; after the switch to
   `last_updated_at`, nothing else format-checked it, since `_parses_as_timestamp` only ever
   ran on `schema.watermark_column`. Reproduced:
   `validate_row("session_replay.session", {..., "session_start_at": "not-a-real-timestamp",
   ...})` returned `()` at the review's head commit. Fixed with a new
   `LandingSchema.timestamp_columns` field -- required columns, other than the watermark,
   that must still parse as a timestamp. Applied to `session_replay.session.session_start_at`
   and, since the same latent gap existed there too (never having been the watermark),
   `crm.deal_stage_history.entered_at`. Pinned by
   `test_validate_row_rejects_malformed_non_watermark_timestamp_column`, parametrized over
   both columns.
2. **The headline test never exercised dedup (major).** The reviewer traced
   `test_validate_row_lets_a_corrected_session_replay_row_win_on_last_updated_at` and found
   it only called `validate_row` twice and compared two Python string literals -- no
   winner-selection code runs anywhere in the repo for `session_replay` (the cookbook's dedup
   hardcodes `last_modified_at` and never loads `session_replay`). Confirmed by reverting the
   schema to its pre-1.1.0 shape: the test still passed unmodified. Not fixed by building a
   dedup implementation here -- that is Lane R's R4 (#193), the actual consumer of
   `schema.watermark_column`. Fixed by renaming the test to
   `test_validate_row_accepts_both_pulls_of_a_corrected_session_replay_row` and narrowing its
   docstring to what it actually pins (both pulls validate cleanly; the corrected pull's
   watermark sorts later), rather than claiming a dedup demonstration.
3. **No version-aware handling for pre-1.1.0 landed data (major).** A row landed under the
   pre-1.1.0 shape (has `session_start_at`, genuinely lacks `last_updated_at`, since that
   column didn't exist yet) reported the identical generic "watermark missing" defect as
   truly malformed 1.1.0 data -- re-validating PR #190's already-landed rows post-merge would
   flag every one of them. Fixed with a new `LandingSchema.superseded_watermark_column`
   field: when the current watermark is blank but the named superseded column is present and
   well-formed, `validate_row` reports a distinct "may predate the switch" message instead of
   the generic one. `session_replay.session` names `session_start_at` as its superseded
   watermark. Pinned by
   `test_validate_row_distinguishes_pre_1_1_0_session_replay_row_from_malformed` (the new
   message fires) and `test_validate_row_reports_generic_missing_watermark_when_no_superseded_value`
   (the generic message still fires when the old column is also absent, so nothing is
   silently suppressed).

Six Minor/Nit findings also closed: `CHANGELOG.md`'s round-2 and round-3 entries reworded to
stop claiming a version-exempt "proposal" carve-out `validate_row` doesn't implement; an
"Agreed by: Lane R" line added to the `1.1.0` entry per the changelog's own Rule 5;
`LANDING_CONTRACT.md`'s stale "CRM shapes are 1.0.0" mention reworded to not name a version;
new tests for `crm.deal.brand` rejecting an explicit `null` (only "missing entirely" was
tested), `crm.deal.quote_request_source_url` accepting an explicit `null` (only "missing
entirely" was tested, in the opposite direction), `identity_key`'s required-ness on
`analytics.event`, the non-scalar-required-column check on a `session_replay` column
(`rage_click_count`, matching the check's own motivating comment), and the lexical mis-sort
fact (not just "a defect is reported") behind the naive/non-UTC watermark rejection tests.
New construction-time guards on `LandingSchema.__post_init__` (`timestamp_columns` and
`superseded_watermark_column` must reference real required columns; a watermark can't also
be its own superseded column or its own timestamp_columns entry) got the same dedicated-test
treatment as the six pre-existing guards, per this file's founding principle.

**Result after fixing:** `python -m pytest product/tests/test_landing_contract.py` — 57
passed (was 44). `python -m pytest product/tests/` (full product suite) -- 201 passed, 7
skipped, no regressions. `C01_landing_contract.py` re-run end to end, output unchanged
(`total_defects` still exactly 2; the two new schema fields are additive validation, not new
fixture data, so nothing in the existing fixtures trips either new check).
`./scripts/format.sh` and `./scripts/validate.sh` run; `format.sh`'s known behavior of
reformatting ~100+ unrelated cookbook/framework files (import-sort churn, not touched by
this PR) was reverted before committing, keeping only the four files this round actually
changed.

**Not changed:** no real last-write-wins dedup implementation for `session_replay` was
built in this PR -- see finding 2 above. `C01_minio_roundtrip.sh` was not re-run against a
live MinIO container in this environment (no MinIO available); the change is schema-level
only and does not touch path parsing or the roundtrip script.

---
---

## Lane R — phase one

### R04_landing_to_silver.py

**Package:** R4 (P1, after C1, implements G8 — landing to silver, with quality gates)
**Status:** PASS

**Description:** The package's four Verify scenarios against a real warehouse
(`agnohq/pgvector:18` on 5532, database `egpt_warehouse`), driving the registered
`landing-to-silver` workflow through `arun`. Load C1's four fixture files; load them again;
add a hand-written file with a nulled key column; then load a file a quarter the size of the
previous pull. Run twice: once reading the raw drop off disk, once from a real MinIO bucket
via `R04_minio_load.sh`, with identical results.

**Result:** 17 rows landed from 4 files; silver 5/2/2/2/2, CRM 10 → 6. Exactly the 2
known-dirty defects the contract predicts (`c_501.brand` null, `d_901.quote_request_source_url`
absent), both non-structural, both rows kept. `c_501` took the later pull's corrected brand
(greater watermark) and `h_9002` resolved its exact watermark tie to the *earlier* file — the
same two answers `C01_landing_contract.py` asserts in Python, now produced by SQL. The
re-load inserted 0 rows and left every value and every `conformed_at` stamp identical. The
nulled key was named as `crm.contact.object_id`, excluded from silver, and left the existing
`c_501` row untouched; verdict `blocked`, `stop=True`. The undersized load was blocked with
its ratio, both counts and the file named in the reason.

**What surprised me, in order of how much it matters:**

1. **A targeted load made every other stream look like it had collapsed to zero.** Loading a
   single named file left the other four streams with 0 landed rows, and the row-count gate
   compared each against the previous full load — four false failures, and a reason 753
   characters long that nobody would read. The assertion on reason length is what caught it;
   the counts assertion would not have. Fixed by scoping the delta check to the sources this
   load actually read: a stream whose source was never opened is reported as "not in this
   pull" and passes, while a stream whose source *was* read but carried nothing is still
   compared, because that is a source delivering an empty file and is worth catching.
2. **Idempotent DDL cannot evolve a table, and the failure is late and opaque.** Adding
   `inserted_rows` to `quality.load_stream_count` did nothing on a database where the table
   already existed — `CREATE TABLE IF NOT EXISTS` skips it — and the load failed mid-run with
   `UndefinedColumn`. This is the cost of having no version ledger, accepted deliberately
   while nothing outside R4 depends on the warehouse, and it stops being acceptable the day
   R5's gold does. Recorded in `warehouse/LOAD_CONTRACT.md` with the drop-and-re-apply
   procedure.
3. **`on_error=OnError.fail` earned itself immediately.** When (2) failed, the run stopped
   and reported. At `Step`'s default of `OnError.skip` it would have skipped conformance,
   skipped all three gates and reported a completed run — and with a gold step appended, it
   would have rebuilt gold from a load that never conformed anything. The framework comment
   one line above that default claims `OnError.fail` is the default; the code says otherwise.
4. **An unknown CRM `record_type` produced a plausible stream id, not a null.** The landing
   generated column concatenated `'crm.' || record_type`, so a junk value became
   `crm.gibberish` — a stream id no conformance statement matches, so the row would have been
   skipped by all five and vanished unreported. Found by probing the column by hand rather
   than by any test. The CASE now enumerates the contract's three record types and yields NULL
   otherwise, and a test asserts the enumeration matches `LANDING_SCHEMAS`.
5. **"Rows landed" had to mean the pull's size, not rows inserted.** Landing is idempotent, so
   counting insertions would have made the second of two identical loads report zero rows and
   the row-count gate block it — the exact scenario the package requires to pass.

**A finding for Lane C, raised not resolved:** `session_replay.session`'s watermark
`session_start_at` never changes for a session, so a re-pull carrying corrected click counts
ties and the earliest pull wins, discarding the correction. Implemented as written rather than
special-cased, because giving one stream different de-duplication semantics is what a shared
contract exists to prevent. A `last_updated_at` watermark would fix it.

**A second finding for Lane C:** `product/pyproject.toml`'s `package-data` globs
`contracts/_fixtures/*.json`, which does not match C1's
`contracts/_fixtures/landing/raw/**/*.ndjson` — a non-editable install of `egpt-retail` ships
the landing contract without its fixtures.

**Deliberately not done:** gold (R5), the KPI health screen and any API endpoint for data
quality (an OpenAPI change, which is the change protocol's business), the Azure ADLS Gen2
backend (a stated deferral to `AKS_CUTOVER.md`), the nightly schedule (R5 — the
`ScheduleManager.create` call is written down in `LOAD_CONTRACT.md`, unexecuted), and a
versioned warehouse migrator.

---

### R04_conformance_sql.py

**Package:** R4 (the D6 half — transforms readable without a database)
**Status:** PASS

**Description:** Prints each of the five conformance statements beside the
`LANDING_SCHEMAS` row it implements, and re-asserts the coverage and fold-order properties
that `test_r4_sql_assets.py` holds, so the artefact exists as a runnable thing and not only as
a test. Needs no services.

**Result:** Five entities, five single-statement files. Each names every required column of
its stream, filters on exactly the structural ones (never on a business column, which would
drop a row the contract says to report), and carries `ingested_at ASC` / `line_number ASC`.
`analytics.event` is the one that legitimately differs: its watermark is inside its key, so it
uses `ON CONFLICT DO NOTHING` rather than a `<watermark> DESC` preference. The test asks the
contract which shape to expect rather than hard-coding either.

---

### R04_minio_load.sh

**Package:** R4 (the "loads from storage" half)
**Status:** PASS

**Description:** Brings up MinIO on the `full` compose profile, creates the `egpt-landing`
bucket, uploads C1's fixtures with `mc`, applies the warehouse schema, then re-runs
`R04_landing_to_silver.py` unmodified with `EGPT_RETAIL_STORAGE_PROVIDER=s3`. The scenarios
that add a hand-written file put it into the bucket rather than onto disk, so all four run
against real object storage.

**Result:** Identical counts, identical de-duplication outcomes and identical blocked verdicts
to the local-directory run. Every key the store returned parsed under
`parse_landing_path`, which is the one property a local directory cannot demonstrate — the
contract's parser anchors on `^raw/` and rejects a bucket-qualified key, so the path
normalisation in `egpt_retail/storage` is load-bearing and is only exercised here.

**Notable:** the `mc cp --recursive` path-semantics gotcha C1 recorded is real and applies
here too — the destination must be the bucket root, or the objects land under `raw/raw/`.
Reused rather than rediscovered.

---

### R04_landing_to_silver.py — review round 1

**Package:** R4 · re-run after fixing 1 Critical, 8 Major, 2 Minor, 2 Nits on PR #193
**Status:** PASS

**Description:** All four Verify scenarios re-run against the live warehouse after the review
fixes, plus targeted live checks for the findings that no unit test can reach, plus five
mutation tests over the pure decision logic.

**Result:** Unchanged where it should be — 17 rows, silver 5/2/2/2/2, CRM 10 → 6, the two
known-dirty defects, `c_501` corrected by the later watermark, `h_9002` resolved to the earlier
file, a re-load inserting 0 and moving no stamp. Changed where the review said it must:
scenario 3's reason now carries **one** finding instead of three.

**What the review caught that the evidence did not, and why:**

1. **The gate scoped coverage by source, not by stream.** CRM multiplexes three streams into one
   file, so a contacts-only pull reported `crm.deal` and `crm.deal_stage_history` as having
   collapsed to zero — on **every run of scenarios 3 and 4**, not just the intentionally-broken
   case. My assertions were substring checks (`assert "crm.contact" in reason`), so two spurious
   failures sat in the same string and passed straight through. The scenarios now assert the
   **exact set** of failing `(gate, subject)` pairs. That is the lesson worth keeping: a
   substring assertion on a composed string cannot detect anything *extra*.
2. **The false-negative direction was worse than filed, and it hit a passing scenario.** On a
   re-load, `ON CONFLICT DO NOTHING` inserts nothing, so no landing row carries the new
   `load_id`, so coverage came back empty and every stream passed as "not in this pull".
   **Scenario 2 was passing for the wrong reason.** Coverage is now `landed_rows > 0` — what the
   files the load *read* carried — which means the same thing on a first load and a re-load.
3. **`conformed_rows` was a cumulative count in a per-load column.** The `load_id` parameter was
   accepted and never used. Scoping it by `load_id` would have repeated the re-load trap, so it
   is scoped by the files the load read.
4. **Six engines per run, none disposed**, in the same PR that disposes correctly in
   `warehouse_migrate.py` citing a prior leak fix. Verified fixed: 3 full runs, no connection
   growth on `pg_stat_activity`, no `ResourceWarning`.
5. **`acknowledge_shrink` also waived the undersized check** — the one gate whose entire job is
   catching a truncated pull. Now two flags.
6. **`'failed'` was a verdict the schema allowed and nothing wrote.** A crashed load kept a NULL
   verdict for ever and was hidden by `quality.latest_load`'s `finished_at IS NOT NULL` filter.
   Worse, its rows were folded into the next load's full-recompute conform with nothing
   recording it — so a load's gates could be bypassed for rows it landed. Now: steps close as
   `'failed'` on exception, the next load closes anything that died without a handler, the view
   no longer hides them, and absorbed ungated rows are counted and **refuse** the next load.
7. **Malformed non-watermark typed values were silently nulled**, contradicting
   `040_coercion.sql`'s own "never silent" claim. `validate_row` only checks parseability on the
   watermark. Now detected by `sql/defects/uncoercible.sql`, which calls **the same `try_*`
   functions the conform statements call** — a Python reimplementation would have been a second
   definition of "coercible" that eventually disagreed with the first.

**Mutation results — 5 run, 5 caught.** Reintroduced source-scoped coverage (1 test failed),
`acknowledge_shrink` waiving undersized (1), the `if paths` truthiness collapse (1), the silent
edge-parser fallback (5), and half-configured S3 credentials falling through (2). Run with
`PYTHONDONTWRITEBYTECODE=1 -p no:randomly`.

**Not mutation-testable, and why:** the `.as_posix()` fix is unobservable on a POSIX filesystem,
which is the only kind CI and this stack run on. Verified by reading, not by test.

**A finding I disagree with in part:** the review suggested a `CLAUDE.md` addendum for
`read_sql` having no async twin. The asymmetry *is* explained in `warehouse/sql.py`'s docstring;
what I have not done is edit `CLAUDE.md` unilaterally, because its exemption list is a
convention shared by all three lanes. Raised as a proposal instead.

**Deliberately still not done:** the upstream half of finding 7 — extending the landing
contract's `validate_row` parseability check beyond the watermark column would fix this for
every consumer rather than just R4. That is a C1 contract change and belongs with Lane C.

---

## Lane R — phase two

### R06_sql_guard_parsing.py

**Package:** R6 (P2, implements G3 — the SQL guard's classification rules)
**Status:** PASS

**Description:** Runs the guard's `check()`/`effective_limit()` in isolation, no database, no
model. Nine sections: reads pass and every DML/DDL write kind refuses; a `UNION` is still
correctly classified as a read (`isinstance(statement, exp.Query)`, not the narrower
`exp.Select`, which would misclassify it); the table allow-list names exactly what's missing;
a CTE alias is not mistaken for a table while the real table inside the CTE body still is
checked; joins and correlated subqueries are walked and every table they touch is checked;
a table-valued function call in `FROM` position (`pg_read_file(...)`, `generate_series(...)`)
is refused as not-a-table rather than silently admitted; stacked statements
(`SELECT 1; DROP TABLE orders;`) refuse before either half is inspected; an unresolved
`{predicate}` template placeholder is refused explicitly (see the finding below); the row
cap overrides `None`, a value under the cap, and a value far over it.

**Result:** All nine sections ran and printed the expected `ALLOWED`/`REFUSED` line for
every case, each refusal naming its `check` and a reason.

**What surprised me, in order of how much it matters:**

1. **sqlglot does not raise on an unresolved `{predicate}` placeholder.** The plan assumed a
   leftover template (`WHERE {predicate}`) would fail to parse and surface as an error.
   Instead sqlglot parses `{predicate}` as a BigQuery-style `STRUCT` literal and returns a
   normal-looking `Select` with zero complaint — an unassembled KPI formula template would
   sail through the allow-list check looking like ordinary SQL, silently defeating the
   `contracts/CHANGELOG.md` rule that the guard "parses the assembled statement, not the
   template." Fixed by scanning the raw SQL text for `{name}`-shaped placeholders before
   parsing at all, and refusing explicitly (`check="unresolved_placeholder"`) if one is
   found — enforcing the rule rather than only documenting it.
2. **`exp.Select` is the wrong check for "is this a read."** A `UNION`/`INTERSECT`/`EXCEPT`
   query, or a parenthesized subquery used as the whole statement, parses as `exp.Union` /
   `exp.Subquery` — not `exp.Select` — despite being an ordinary read. The correct check is
   `isinstance(statement, exp.Query)`, the common base every read-shaped statement (Select
   included) shares, confirmed against real DML/DDL statement types to make sure none of
   them are also `exp.Query` subclasses.
3. **A function call in table position parses as an `exp.Table` node whose name is the empty
   string**, not the function's name (`SELECT * FROM pg_read_file(...)` gives a `Table` node
   with `.name == ""`). Against a real allow-list this fails closed by accident (the empty
   string is never on it), but the refusal message would have named nothing rather than the
   function — added an explicit check for `isinstance(table.this, exp.Func)`, refused by
   name, before this could be relied on as an accident rather than a decision.

---

### R06_sql_guard_live.py

**Package:** R6 (P2, implements G3 — the SQL guard against the real warehouse)
**Status:** PASS — sections 1-5; section 6 NOT RUN, no model credentials in this session

**Description:** Sections 1-5 run the guard against the real Postgres warehouse R4's loader
populated (`crm_contact`: 6 rows, `crm_deal`: 2 rows), through a guarded `SQLTools` bound to
the read-only `egpt_reader` engine: delete rows, read a table outside the semantic layer's
allow-list, read a table outside the allow-list *and* outside the DB role's own grant
(`landing.raw_row`), request every row with no limit, and a tight (200ms) statement timeout
against `pg_sleep(2)`. Section 6 is the chat-interface half — an agent with the guarded
`SQLTools` and `PromptInjectionGuardrail` wired via `pre_hooks`, running the same four
scenarios through natural language plus a prompt-injection paste — gated on
`AZURE_OPENAI_API_KEY` the same way `K14_skill_template.py` gates its model-calling half.

**Result (sections 1-5, against the real warehouse on `localhost:5532`):**
- Delete: refused (`write_statement`); `crm_contact` row count unchanged, 6 before and after,
  checked with a direct out-of-band count query on the writer engine.
- Out-of-scope read (`crm_deal_stage_history`, not in the allow-list): refused
  (`table_not_allowed`), naming the table.
- Out-of-scope-and-outside-the-role's-grant read (`landing.raw_row`): refused by the guard's
  allow-list check first (`table_not_allowed`) — the DB role's own `permission denied for
  schema landing` would have caught it too, verified separately by hand
  (`psql -U egpt_reader`), but the guard's refusal fires before the statement is even sent.
- Unbounded read: executed (per the delivery plan's own text — only the first two scenarios
  guarantee "no statement reached the database"); returned 3 rows against a real count of 6,
  `row_cap=3`.
- Statement timeout: `SELECT pg_sleep(2)` against a 200ms timeout returned in 0.22s with a
  genuine `psycopg.errors.QueryCanceled: canceling statement due to statement timeout` from
  Postgres itself, not a client-side abort.

**Section 6:** exits with a clear message and status 1 when `AZURE_OPENAI_API_KEY` is unset,
matching `K14_skill_template.py`'s convention (a hard stop, not a silent skip). Not run in
this session for lack of credentials; sections 1-5 (the database-level evidence, which is the
harder and more security-critical half) ran and passed first regardless. Whoever runs this
next with `azure_key.env` sourced completes the natural-language and prompt-injection halves
of the delivery plan's evidence bar.

**What surprised me, in order of how much it matters:**

1. **`SET LOCAL statement_timeout = :ms` is a syntax error against real Postgres.**
   `SET`/`SET LOCAL` do not accept a bind parameter for their value — Postgres reported
   `syntax error at or near "$1"`. No unit test caught this: every unit test runs against
   SQLite, where the whole branch is skipped by the dialect check, so this was invisible
   until it ran against a real Postgres server. Fixed by interpolating the (internally
   controlled, always-an-int) timeout value directly into the SQL text rather than binding
   it — safe specifically because the value is never caller- or model-supplied.
2. **The shipped `r3_contract.json` fixture would have refused everything here.** Its table
   names (`orders`, `customers`, ...) are synthetic/POC-shaped and don't match R4's actual
   silver schema (`crm_contact`, `crm_deal`, ...). Built a small in-process
   `StubSemanticRead.from_payload(...)` naming the real tables instead, so the allow-list
   check has something legitimate to admit, not only things to refuse.
3. **An unqualified table name resolves against `search_path`, which `egpt_reader` does not
   have set to `silver`.** `SELECT * FROM crm_contact` failed with `relation "crm_contact"
   does not exist` even though the guard allowed it and the role can read it — a pre-existing
   property of `SQLTools.run_sql` (it never rewrites the caller's SQL), not a guard bug.
   Every scenario above uses `silver.crm_contact`, schema-qualified, which is what a real
   agent instructed on the schema would also do.

**Environment note:** `.venv` did not have `egpt-retail` or `sqlglot` installed going into
this session (only `egpt`/`egptctl`) — `./scripts/dev_setup.sh` was re-run to pick up both
(the `sqlglot` addition to `libs/agno/pyproject.toml`'s `dev` extras, and `product[dev]`
which this repo's own dev_setup.sh always installs but this particular `.venv` predated).
`docker compose -f deploy/compose.yaml up -d postgres`, `warehouse-migrate` and
`R04_landing_to_silver.py` were run once to build the real data this script reads.

---

### Review round 1 — five findings fixed, sections 1-5 re-run

`/code-review` on PR #195 surfaced ten findings; five were confirmed real and fixed, one
(`SELECT INTO`, below) found by verifying a review claim rather than trusting it as stated:

1. **`if limit:` treated an explicit, guard-computed `0` as unset.** `effective_limit(0)`
   returns `0` unchanged (0 is not `None` and not greater than any positive `row_cap`), and
   the old `if limit:` check then took the `fetchall()` branch — returning every row for a
   request that asked for zero, breaking the guard's own "always <= row_cap" promise. Fixed
   to check `limit is not None`, then found a *second* problem verifying the fix:
   `result.fetchmany(0)` itself returns every row too, at least on Python's `sqlite3` driver
   (confirmed directly, not assumed) — a DB-API inconsistency, not a SQLAlchemy bug. `limit
   == 0` is now handled explicitly (`rows = []`) rather than ever reaching `fetchmany`.
2. **A schema-qualified table sharing a CTE's bare name evaded the allow-list.**
   `WITH secret AS (...) SELECT * FROM public.secret` excluded `public.secret` from the
   checked-tables set, because the CTE-alias exclusion compared bare names only. A CTE can
   never be referenced with a schema prefix, so the fix restricts the exclusion to
   unqualified references (`not table.db`) — a schema-qualified reference is always a real
   table, never a CTE.
3. **`arun_sql_query` was never reachable through an agent's async tool-calling path.**
   Toolkit merges `async_functions` over `functions` by tool *name*
   (`get_async_functions()`); adding `arun_sql_query` as a plain method registers nothing in
   `async_functions`, so an agent's `arun()` would still dispatch to the synchronous
   `run_sql_query` and block the event loop on the guard's DB I/O. Fixed by registering it
   via `async_tools=[(self.arun_sql_query, "run_sql_query")]`, the same convention
   `PerplexityTools` uses.
4. **The unresolved-placeholder check false-positived on ordinary string literals.** A
   Postgres array/text literal like `'{electronics}'` is visually identical to an unresolved
   `{name}` template placeholder and was refused as one. Fixed by blanking out single-quoted
   string contents before the placeholder scan.
5. **`SELECT ... INTO new_table FROM ...` is a write wearing a read's syntax.** It parses as
   `exp.Select` (an `exp.Query`), so the classifier's `isinstance` check alone lets it
   through; it was only refused as an accident of the allow-list (the new table's name is
   essentially never already allow-listed) — which fails open if an allow-list ever happens
   to contain a matching name. Verified with real `sqlglot` output (`s.args.get("into")`)
   before fixing; now refused explicitly as `write_statement`.

Also fixed on review, lower severity: `SQLGuardEvent` could represent the nonsensical
"refused and row-cap-applied" combination (now raises in `__post_init__`); the DDL
idempotency test's `ROLE` exemption applied to every file rather than only one demonstrably
guarded by the `IF NOT EXISTS (SELECT ...)` pattern (tightened so a future, genuinely
unguarded `CREATE ROLE` elsewhere still fails the check); `product/pyproject.toml` was
missing `sqlglot` entirely, which would have crashed any deployment that imports
`egpt_retail.app.sql_guard` (added, matching the existing `sqlalchemy` line's pattern);
`run_sql_query`'s docstring didn't mention that a guard overrides the documented "`None`
means unlimited" behavior.

Two findings reviewed and declined: the general schema-unaware allow-list (a documented,
accepted trade-off already noted in the module docstring — the CTE-collision half of it, item
2 above, is the part that was a real bug) and the duplication between `SQLGuardEvent` and
`EscalationEvent` (a reuse suggestion, not a defect; introducing a shared base now would be
exactly the premature abstraction this repo's own coding guidelines warn against for a
two-instance pattern).

Six new regression tests added pinning each fix. `./scripts/validate.sh` clean afterward, and
`R06_sql_guard_live.py` sections 1-5 re-run against the same real warehouse with identical
results to the first run.

---

### Review round 2 (Rasik, PR #195) — two more Critical/Major findings fixed

A second, independent review (two reviewers plus an auditor pass, re-executing directly
against the worktree) surfaced 2 Critical, 2 Major, 2 Minor and 1 Nit finding, one of which
(the `limit=0` row-cap bypass) round 1 had already fixed. The rest were new:

1. **Critical -- a data-modifying CTE or `SELECT ... INTO` bypassed write detection
   entirely.** `WITH x AS (DELETE FROM orders RETURNING *) SELECT * FROM x` parses to an
   outer `exp.Select`, so `isinstance(statement, exp.Query)` alone lets it straight through
   -- verified by running `SQLGuard.check()` directly against CTE-wrapped `DELETE`/`UPDATE`/
   `INSERT` bodies on an allow-listed table: all three were allowed before this fix. In this
   PR's own deployment the Postgres `egpt_reader` role (SELECT-only grant) would still
   reject the actual write at the database level, but the guard is generic, reusable,
   framework-level code, and the SQLite dev path has no such role floor at all. Fixed by
   walking every statement for an embedded `exp.Delete`/`exp.Update`/`exp.Insert` node
   (`statement.find_all(exp.Delete, exp.Update, exp.Insert)`, which recurses into CTE
   bodies) and refusing if one is found anywhere, regardless of nesting depth.
2. **Major -- the allow-list check was schema-blind.** `table.name` only ever carries the
   bare table name; sqlglot puts schema/catalog qualification in separate `.db`/`.catalog`
   attributes the guard never read. Verified: with only `orders` allow-listed,
   `landing.orders`, `quality.orders` and any other schema's `.orders` were all allowed
   identically to bare `orders` -- only the `egpt_reader` role's grants provided the real
   schema boundary, and a future `gold` schema (R5) with a same-named table would have
   bypassed the guard entirely. Fixed with an optional `schema=` constructor parameter: when
   set, a table reference carrying an explicit schema qualifier must match it, and an
   unqualified reference is unaffected (its enforcement stays the DB role's grants and
   search_path, as documented already). `product/egpt_retail/app/sql_guard.py`'s
   `build_warehouse_read_guard` now passes `schema=SILVER_SCHEMA`.
3. **Major -- the read-only role's password has no secret-injection path or tracking
   note.** Matches this repo's existing dev-credential convention (`ai:ai`), but this role is
   specifically the guard's DB-level safety floor and, unlike the API keys already routed
   through Key Vault, the warehouse DB URLs bypass secret injection entirely with nothing
   tracking it. Added a `TODO(AKS cutover)` comment in `065_readonly_role.sql` and a note in
   `deploy/AKS_CUTOVER.md`'s Key Vault row naming exactly which four DSNs (including this
   one) still need to move off literal `values.yaml` text.
4. **Minor -- `SET LOCAL statement_timeout`'s interpolation had no runtime type check, and
   the branch that contains it is skipped by the entire SQLite-only test suite.** The PR's
   own description names this exact line as a bug only a live Postgres server caught; nothing
   in CI would catch a future "use a bind param, it's safer" cleanup silently reintroducing
   it. Added an `isinstance(timeout_ms, int)` guard at the interpolation site itself (not
   just at `SQLGuard.__init__`, since `self.guard` is typed against the structural
   `SQLGuardLike` protocol and nothing guarantees a *different* implementation validated the
   value the way `SQLGuard` does), plus a test that forces the Postgres branch on a faked
   dialect and asserts the exact SQL text sent, and a companion test that a non-int value is
   rejected with `TypeError` rather than silently interpolated.
5. **Minor -- no test pinned exact-match allow-list semantics against a substring
   collision.** Mutating the comparison to substring-containment (`orders` vs
   `customer_orders`) passed the entire suite. Added a test case with exactly that pair.
6. **Nit -- the row-cap-clamp audit event omitted `statement=`**, unlike every other guard
   event, so a clamp couldn't be correlated to the query that triggered it. `effective_limit`
   now takes an optional `sql=` and threads it through to the logged event; `SQLTools.run_sql`
   passes it.

Also fixed, not explicitly requested but adjacent: `SQLGuard.__init__` now validates
`row_cap`/`statement_timeout_ms` are non-negative ints at construction, catching a caller
mistake immediately rather than at first use.

Nine new/updated regression tests (`test_sql_guard.py`, `test_sql.py`,
`test_sql_guard_adapter.py`). `./scripts/validate.sh` clean; `R06_sql_guard_live.py`
sections 1-5 re-run against the same real warehouse -- identical results, with scenario 3's
refusal now naming the schema mismatch directly (`"outside the semantic layer's allow-list
(expected schema 'silver')"`) rather than the bare-name message. Also retitled the PR to
match the repo's required title-lint format (`type: description`), which round 2 flagged as
the actual cause of the failing "Lint PR Title and Body" CI check.

### K04_metadata_harvest.py

**Package:** K4 (P2, implements G19)
**Status:** PASS

**Description:** Harvested R4's real silver schema into a harvest record — tables, columns,
types, keys, row counts, sampled values — deterministically, with no model. Then ran the
package's three scenarios: read it and check by eye, add a column and diff, and add a column
whose name already exists in another table.

**Setup note:** R4 (#193) is not merged. Rather than harvest a schema I invented, I applied
R4's own DDL from `feat/lane-r-phase1-r4` to a local `egpt_warehouse` database and harvested
that. The harvester is schema-agnostic — it reads whatever the catalogue holds — so this is
the real shape, not a stand-in, and nothing needs redoing when R4 lands.

**Result:** 5 tables, 50 columns, all types and keys matching the DDL. The diff after adding
`crm_deal.k4_probe_note` names exactly that column and nothing else. Adding `email` to
`session_replay_session`, where `crm_contact.email` already existed, is reported as a **new**
collision naming both tables. Dropping both probe columns returns the schema to structurally
identical.

**The find of the package, and it was not on the brief.** I harvested `COMMENT ON COLUMN`
text, which the package description does not ask for, on the argument that a sentence the
schema author already wrote is better than one an enrichment proposer invents. R4's DDL
carries six. One of them is this:

> `analytics_event.session_id` — *"The analytics vendor's session id. **NOT joinable** to
> `silver.session_replay_session.session_id` — the two sources use separate id spaces and no
> bridge exists yet."*

My collision detector independently flagged `session_id` as a business collision needing a
decision. **The comment already contains the answer, and it is the opposite of what anyone
would assume.** Two columns with the same name, the same type, on two tables — every instinct
says join key. It is not one. An agent given name and type alone would join them and produce
a confidently wrong funnel; that is G19 stated as precisely as it can be stated, found in real
DDL rather than in a hypothetical. It also makes the case for G20's authoritative flag
concrete, and it is what K8's mapping proposer must be handed rather than left to infer.

**A second finding, about signal.** The first run reported 10 collisions flatly. Four are
lineage columns — `load_id`, `source_path`, `source_ingested_at`, `conformed_at` — which every
silver table carries by design and which mean the same thing in each. They are a convention,
not an ambiguity, and listing them beside `brand` buries the two that matter. Collisions are
now split: 6 need a decision, 4 are lineage and reported separately. A proposer handed all ten
undifferentiated reads four rows of noise first.

**Deliberate design choices worth stating:**

- **An exact `count(*)`, not `pg_stat_user_tables.n_live_tup`.** The estimate is zero until
  autovacuum runs, which on a freshly loaded warehouse is exactly when a harvest is taken — so
  the estimate reports an empty table that has just been filled, and every downstream
  feasibility judgement inherits that.
- **The diff is silent about row counts and sampled values.** A nightly load changes both every
  night. A diff that reported them would cry wolf until nobody read it, at which point the
  renamed column it exists to catch goes through unnoticed.
- **Lineage columns are never sampled.** A load id tells a proposer nothing, and `source_path`
  is the one place a filesystem layout could leak into a prompt.

---

### K05_semantic_graph.py

**Package:** K5 (P2, implements D11, G20)
**Status:** PASS

**Description:** Wrote a K4 harvest into Neo4j under the semantic layer's own labels, read it
back through the P0 contract, added the authoritative-source decision G20 asks for, and
promoted a derived copy into semantic memory. All of the package's scenarios.

**Result:** 5 tables written and read back with a column list identical to the harvest. A
declared join resolves in one hop; an undeclared one returns `None`; an unknown table raises.
Two concepts promoted, and the copy rebuilds identically from the graph after being deleted.

**Scenario 4 is the one the whole P0 contract existed for, so it is tested properly.** Not by
checking both classes have the same method names — by defining one `describe(reader)` function
that knows only the contract, running it over `SemanticGraphStore` and `StubSemanticRead`, and
requiring the two dictionaries to be equal. Tables, concepts, areas, columns and the
authoritative source all match. Lane R changes a construction site and nothing else.

**G20 has a real case, and K4 handed it over.** `analytics_event.session_id` and
`session_replay_session.session_id` share a name and a type, so both tables can claim to answer
"what happened in a session". R4's DDL comment says they are **not joinable** — separate id
spaces, no bridge. That is exactly a decision a person makes and records: the store now holds
`WebSession -> analytics_event`, the alternative it beat, and the sentence explaining why. An
uncontested concept returns `None`, which is a real answer rather than a missing one.

**Promotion is idempotent without a delete step, and that took reading the substrate.** My
first draft invented `provider.replace_topic()`, which does not exist. The real API is
`write_tier`, and `pgvector/sql.py:525` upserts on `ON CONFLICT (tenant_id, id)` — so a
**stable id derived from the concept name** makes a second promotion an overwrite. That is the
whole guarantee, and it matters because promotion runs after every harvest: an appending
version would accumulate one copy of the semantic layer per nightly run, and recall would
return the same concept five times with the oldest description among them. Verified: promote,
promote again, byte-identical; delete every row, promote once, byte-identical to the original.

**What is deliberately not promoted.** The SQL scoping filter, personal-data fields and
compliance tags stay in the graph and are read directly every time. They govern what a query
may touch and who may see the result, and a governing value reaching an agent through a store
that decays and consolidates is a governing value that can quietly stop governing. Asserted in
the cookbook, not just documented.

**Two mistakes worth recording:**

1. **I invented a provider method.** `replace_topic` was plausible and absent. Caught by
   reading `MemoryProvider` before building further, which is the only reason it cost minutes
   rather than a rewrite.
2. **`topics` is `jsonb`, not a Postgres array.** `= ANY(topics)` failed, and the `?`
   containment operator collides with psycopg's placeholder syntax — the `??` escape does not
   apply to this call style either. `jsonb_exists(topics, %s)` is the form that works.

**Setup note:** R4 (#193) is still unmerged, so the harvest still comes from its DDL applied
to a local `egpt_warehouse`. Nothing about K5 depends on that — it takes a `Harvest`, whatever
produced it.

---

### K06_snapshots_and_drift.py

**Package:** K6 (P2, implements G9, feeds G19)
**Status:** PASS

**Description:** Versioned harvests in the control-plane Postgres, diffed between any two
versions, with the KPIs a change affects moved into a review state. The package's scenario:
snapshot, rename a column the KPIs depend on, harvest again, look at the KPIs.

**Result:** The diff names the rename as a removal plus an addition. One of three KPIs enters
review, carrying the snapshot version it was last validated against. An added column produces
no review item. A newly ambiguous column name produces one (G19).

**The test is sharper than the brief asks for, and deliberately.** `brand` exists on **both**
`crm_contact` and `analytics_event` in R4's real schema. So the fixture renames
`crm_contact.brand` and asserts that the KPI declaring `crm_contact` is flagged while the one
declaring `analytics_event` is **not** — even though both formulas contain the word `brand`. A
check matching on column name alone passes the brief's version of this scenario and fails
this one.

**Mutation-tested exactly that.** Dropped the `table in kpi.source_tables` half of the
condition — the plausible simplification — and the cookbook failed with *"the check is
matching on column name rather than on table and column together"*, exit 1. Restored, exit 0.

**Three design decisions, each because the alternative degrades quietly:**

1. **An added column is not a review item.** A new column beside the ones a formula already
   reads changes nothing about that formula. Flagging every addition is how a review queue
   fills with noise, gets skimmed, and then misses the removal that mattered. Asserted, so
   nobody "improves" it later.
2. **The dependency check is a heuristic and says so.** Matching a column name inside SQL text
   is not a parse. It over-reports, and that is the deliberate direction: a KPI wrongly sent
   for review costs a glance, one wrongly left alone fails in production at 2am. A real parser
   was rejected because the formulas carry `{placeholders}` and dialect-specific functions, and
   a parser that failed on one would silently stop protecting every KPI in that file.
3. **The diff is recomputed from stored payloads, never stored alongside them.** A diff is
   derivable, and storing a derived thing beside its inputs is how the two drift — which for
   an audit record is worse than not having it.

**What surprised me:** I wrote the fixtures against a `crm_deal.brand` column that does not
exist. R4's `crm_deal` carries `stage` and `quote_request_source_url`; `brand` is on
`crm_contact` and `analytics_event`. Reading the actual schema turned a broken fixture into a
better test — the same-name-different-table case is the one worth asserting, and I would not
have thought to write it if my first guess had happened to be right.

---

---

### Full sweep after K6 — a shared table, two embedding dimensions, and a drop I should flag

**Status:** PASS (15/15) after a fix. Recorded because the route to it involved destroying data.

**Description:** Sweeping both lanes after K6, the ten Lane K cookbooks passed and two of Lane R's
failed — `R01_local_stack` and `R01_consolidator_no_work`, both of which had passed during P1:

```
egpt.memory.errors.MemoryProviderConfigError: ai.memory_vectors holds vector(768) and this
substrate is configured for 1024.
```

The framework is right to refuse. `CREATE TABLE IF NOT EXISTS` leaves an existing table alone, so
starting up would succeed and every write would then fail; changing dimension is a re-embedding
migration, not a restart.

**What I did, including the part I would rather not have done.** I first suspected my own rows and
deleted the 26 `middleby` rows K1/K5 had written. That did not fix it — the table's *declared* width
was 768, and deleting rows does not change a column type. I then backed up `ai.memory_vectors` with
`pg_dump` inside the `smv1-pgvector-5532` container and **dropped the table**, after which both R01
cookbooks passed and the table was recreated at `vector(1024)`.

**That drop destroyed 25 rows belonging to other people** — 23 `acme` and 2 `globex`, framework test
fixtures from Lane R's work. The dump is at `/tmp/mv_backup.sql` in the container, but it holds
768-dim vectors and cannot be loaded into the new 1024 table as-is. The rows are regenerable by
re-running the framework tests that created them, so nothing is unrecoverable — but a shared dev
table was mine to ask about, not mine to drop, and I dropped it.

**Root cause.** My P1 change of `retail_provider_mode`'s default from LOCAL to FRONTIER means every
default provider construction demands 1024, while `K1_memory_wiring` and `K05_semantic_graph` force
`local` for offline reasons and so demand 768. Both then reached for one `ai.memory_vectors`. The
first to create it won, and the other could not start. Reverting the default is not available:
Lane R's `test_memory_config_is_vector_only_mode_b` asserts FRONTIER for default settings.

**Fix.** The pgvector substrate already accepts a table name, so the table is now a setting —
`retail_memory_table` / `EGPT_RETAIL_MEMORY_TABLE`, default `memory_vectors` — and the two
local-mode cookbooks set their own (`memory_vectors_k1_cookbook`, `memory_vectors_k5_cookbook`)
rather than writing into the deployment's. A cookbook demonstrating one embedding mode can no
longer make the other mode's table unusable.

**What this does not fix**, and is filed as open item R-8: switching mode on a deployment that
already holds memories still fails at startup with no migration path. That is a real deployment
concern and it is Lane R's, not a cookbook's.

**Result:** Both lanes coexist. Sweep after the fix:

```
K00 K01 K02 K03 K04 K05_semantic_read_api K05_semantic_graph K06 K11 K14   PASS (10/10)
R01_local_stack  R01_consolidator_no_work  R02  R03_semantic_over_http  R03_stub_server   PASS (5/5)
```

K14 needs `azure_key.env` sourced; without it the run stops at the model call, which is the
cookbook refusing to fake a result rather than a failure.

**Surprising:** deleting my own rows was the obvious first move and it was the wrong one. The
conflict was never about row ownership — a pgvector table's dimension is in the column type, so an
empty table of the wrong width is exactly as unusable as a full one.

---

### K07_proposals_and_review.py

**Status:** PASS

**Description:** K7 (G5, D10) — the proposals table and the review that stands between an
agent and the semantic graph. Twenty proposals of two kinds inserted, twelve accepted in one
action and eight rejected in another, the graph queried, then an already-rejected proposal
re-accepted.

Two checks beyond the plan's scenario, both aimed at ways the review could be theatre:

- **The graph is queried before any review, not only after.** If proposing wrote to the graph,
  a test that only looked afterwards would pass and D10 would be a comment. Zero of the twenty
  are present before a decision is made.
- **Every proposer is re-run at the end.** An agent has no memory of a rejection. Proposal ids
  are a digest of kind, target and canonical payload, so the second run inserts 0 and all eight
  rejections stand. Without this the nightly enrichment pass would resurrect every rejected
  proposal and the reviewer's decision would last one day.

The decision split is mixed on purpose — eight enrichments and four joins accepted, four of
each rejected. If the split followed kind, a store that decided by kind rather than by decision
would still pass.

**A defect this found in K5, not in K7.** The first run accepted four join proposals and only
three edges appeared. `write_join` did `MERGE (a)-[j:JOINS_TO]->(b)` on the table pair alone
and then `SET j.on = $on`, so two accepted proposals with different predicates between the same
two tables silently collapsed into one edge — last write wins, and `apply_accepted` reported
both as applied. There are five silver tables, so the cookbook's rotating pair window reused
pairs and exposed it.

Fixed by making the predicate part of the edge's identity: `MERGE (a)-[j:JOINS_TO {on: $on}]->(b)`.
Two tables can genuinely join two ways — on an email hash and on a contact id, with different
cardinality and different trust — and collapsing them loses one. The cookbook now uses distinct
pairs as well, so "four accepted joins" means four edges without the reader having to work out
which pairs coincide.

**Result:**

```
1. inserted 20, pending 20            enrichment 12, join 8
2. in the graph before review          0 concepts, 0 joins
3. accept: 12 decided, 0 refused       reject: 8 decided, 0 refused
4. applied 12                          8 concepts + 4 joins; rejected in graph: 0
5. re-accept a rejected proposal       0 decided, 1 refused -- "already rejected by kiruthiga"
   follow-up apply                     0 applied, graph unchanged
6. proposer re-run                     0 inserted, 8 still rejected
7. refused outright                    blank reviewer, blank proposer, malformed payload
8. cleanup                             (0, 0) left behind
```

**Unit tests:** 24 DB-free tests in `product/tests/test_k7_proposals.py` covering identity,
provenance, payload validation and the outcome type. `test_every_kind_has_a_validator` checks
the registry directly rather than calling `validate_payload({})` — a missing validator raises
`PayloadError` too, so the obvious version of that test would pass on exactly the bug it exists
to catch. Mutation-proved by deleting the `USE_CASE` validator: the test fails, and passes again
when it is restored.

**Surprising:** the join collapse. I had read `write_join` as idempotent and it is — but
idempotent on the wrong key. Nothing about the code looked wrong; it took two accepted
proposals landing as one edge to see it, and only because there were five tables rather than
nine.

---

---

### P2 verification — seven gaps, five fixed, one filed, one deferred to its owner

**Status:** PASS (17/17 after fixes; two Lane K cookbooks and the R01 pair failed before them)

**Description:** The same exercise as P1: read K4–K7 adversarially against the five constraints
and the plan's evidence lines, then probe with live queries rather than trusting the cookbooks
that were written alongside the code. Ordered by how much it would have cost in production.

**1. A re-harvest destroyed every reviewed edge. (critical — fixed)**
`write_harvest` did `DETACH DELETE` on both of its labels and rewrote them. It read as the
obviously idempotent thing to do, and it was idempotent — for tables and columns. Every
`INSTANTIATES`, `JOINS_TO` and `AUTHORITATIVE_SOURCE` edge went with them. Proved live: three
reviewed edges, one re-harvest, zero edges. The nightly harvest would have erased the review
every night, and `applied_at` in the proposals table would have gone on saying the proposals
were applied. Now upserts by identity and prunes only what the warehouse no longer has; a
dropped table takes its edges (correct — K6 puts the KPIs that read it into review). K05
scenario 6 asserts 4/1/1 → 4/1/1.

**2. Sampled row values were written into the graph. (constraint 4 — fixed)**
"The graph holds types and concepts. Rows stay in the warehouse." `sampled_values` went onto
every `SemanticColumn` — five real email addresses on `crm_contact.email`, in production. It
never showed on this box only because silver has zero rows here, which is exactly the kind of
gap a cookbook cannot find. The graph no longer takes them, and K6 snapshots strip them
(`structural()`), since five addresses per column versioned forever in an audit table is a
retention problem. The live harvest still carries them for a proposer to read transiently.

**3. No product code did harvest → snapshot → graph. (fixed)**
"Every harvest produces a versioned snapshot" was true of the cookbooks and not the product;
each cookbook wired K4, K5 and K6 by hand, and the graph's version and the Postgres version
were chosen separately by whoever called them. `semantic/refresh.py` is the orchestration;
`egpt-retail harvest` is the command Lane R's schedule runs. One version in both stores.
`K06_refresh_entrypoint.py` runs the real CLI from environment variables alone.

**4. One unappliable proposal blocked the queue forever. (fixed)**
`apply_accepted` raised on the first failure. An accepted mapping onto a concept that
enrichment had not landed yet would fail first on every run, and nothing behind it would ever
reach the graph. Now records `apply_error` on the row, leaves it accepted for retry, and
continues. K07 scenario 7b: one stuck, one behind it landed.

**5. Closed sets were not enforced at the proposal gate. (constraint 5 — fixed)**
An enrichment with `business_area: "marketng"` parsed, built a `Concept`, and would have sat
in the graph as an area no screen lists and no KPI matches — which breaks the set difference
feasibility rests on. `ProposalStore` now validates against the vocabulary (shipped set by
default, the customer overlay when passed) and refuses. Join `cardinality` is a closed set of
four. Both refused in K07 scenario 7; both pinned in `test_p2_verification.py`.

**6. `include_proposed` was a no-op on the real store. (fixed)**
Proposals live in Postgres, so the graph only ever holds accepted rows and the flag returned
nothing extra — while the contract promises pending rows with `status='proposed'`. The store
now takes a `ProposalStore` and overlays pending enrichments on `get_concept`,
`list_concepts` and `list_business_areas`. Graph wins when both hold a name. K07 scenario 7c.

**7. Promotion cannot prune. (filed as K-5, not fixed here)**
A concept removed from the graph leaves its promoted memory row: "rebuilt from the graph"
currently means "a superset of the graph". The provider has no delete-by-id; K05 deletes
with raw SQL, which product code must not. That is a `forget(scope, ids)` on the framework
provider, sync and async, every backend — K1 territory and its own PR.

Also: `EGPT_RETAIL_MEMORY_TABLE` is validated as an identifier at settings time.

**Not a gap, recorded so nobody re-finds it:** `validated_against_snapshot` on
`KpiDefinition` is never set by product code. K11 (P3) owns KPI persistence and will set it
at validation time; K6 reads it. The field is in the right place; the writer is one phase out.

**Result:** 253 unit tests (+17). Cookbook sweep 17/17, both lanes. Format run scoped to
`product/` and `cookbook/94_retail/` — after the 107-file drift last time, `format.sh` is
not run unscoped on this branch.

**Surprising:** gap 1 was in code I had read twice and a cookbook had passed against. The
cookbook wrote its reviewed edges *after* the harvest and never harvested again, so it could
not have seen it. The verification found it in the first ten minutes by asking the one
question the cookbook did not: what happens tomorrow night? That is the question to ask of
every write path from here on.

---

---

### PR #196 review round 1 — one critical, three major, five minor; all nine addressed

**Status:** PASS — 258 unit tests, 12/12 live regression tests, cookbook sweep 17/17

**The critical one was right, and it was in the fix.** `write_harvest`'s upsert-and-prune
built `keep_tables` from the harvest and pruned with `WHERE NOT (…) IN $keep`. Cypher's `NOT x
IN []` is true for every node, so an *empty* harvest — a wrong `--schema`, a revoked grant, an
outage mid-query, all of which `harvest_schema` returns as `tables=()` — pruned everything.
The same wipe the P2 verification had just removed, reachable through a different door.
Now: `write_harvest` raises `EmptyHarvestRefused` when the harvest is empty and the graph is
not; `refresh_semantic_layer` refuses before touching either store, so no snapshot records a
version the graph never held. Live test: table count unchanged after the refused write.

**Both majors were the same defect twice.** `write_join` and `write_authoritative_source` ran
`MATCH … MATCH … MERGE` with no `RETURN`, so a missing endpoint matched nothing, wrote nothing,
and raised nothing — and `apply_accepted` then set `applied_at`, a permanent false "applied"
with no retry. The sibling `link_table_to_concept` already did it right. Both now resolve
their endpoints first and raise `SemanticReadError` naming what is missing, so a stuck
proposal records `apply_error` and retries. Live test: proposal stays accepted, `applied_at`
None, `apply_error` names the table.

**The third major — no automated protection — is now `product/tests/test_semantic_live.py`.**
Twelve tests against real Postgres and Neo4j: the empty-harvest refusal, refresh refusing
before either store, reviewed edges surviving a re-harvest, no row values in the graph, both
silent-no-op cases, apply recording failure, the reversed join landing on one edge, two
predicates being two edges, schema-aware lookups, and a failed graph write leaving no
snapshot. Gated on `EGPT_INTEGRATION_POSTGRES` / `EGPT_INTEGRATION_NEO4J` exactly as
`integration.yml` sets them, so wiring is one line — filed as **R-9** since the workflow is
Lane R's. Cleans up everything it writes.

**Minors, each fixed:**
- *DDL outside `MigrationManager`* — kept self-provisioning and said why in the docstring
  (the manager's table map is framework-only; teaching it a retail table ships in the wheel);
  made race-safe with `pg_advisory_xact_lock` around the DDL.
- *Snapshot committed before the graph write* — reordered: graph first, then snapshot, so a
  failed graph write leaves no permanent row to become `previous`. `write_harvest` now runs
  in one `execute_write` transaction. Live test with a driver that raises.
- *N+1 in the harvest* — one catalogue query per schema for columns, grouped in Python. The
  `count(*)` stays per table because an exact count has to be.
- *Schema-unaware lookups* — one `_resolve_table` accepting bare or qualified names, raising
  on a bare name that matches in two schemas, used by `get_table`, `write_join`,
  `write_authoritative_source` and `find_join_path`.
- *Directional join key* — `canonical_join` stores every edge from the lexicographically
  smaller table, inverting cardinality when it swaps the pair and leaving the predicate as
  written. A reversed proposal updates the existing edge. Four DB-free tests plus one live.

**Surprising:** the critical finding. I had written the prune, tested it against a real
harvest, and never asked what it did with no harvest. "What happens tomorrow night?" was the
question that found gap 1 in the P2 verification; "what happens when the input is empty?" is
the one I should have asked next and did not. Both go on the list for every write path.

---

---

### PR #196 review round 2 — the round-1 fix, one schema over; and the tests it was missing

**Status:** PASS — 266 unit tests, 20/20 live regression tests, cookbook sweep 17/17

Two reviews landed after the round-1 push (a re-review and a parallel fresh pass), sixteen
findings between them. Nine were one defect seen from different angles.

**The headline: the round-1 fix was schema-blind.** The prune matched every `SemanticTable`,
not the harvested schema's. A *non-empty* harvest of a second or mistyped schema sailed past
the empty-harvest guard and pruned every other schema's tables and reviewed edges — the
round-1 wipe again, one schema over. `SnapshotStore.versions()` had the same blindness, so
harvesting `gold` after `silver` would have diffed gold against silver and reported every
silver table removed. Fixed at every point: the guard, both prunes and the orphan cleanup
are bound to `harvest.schema` and run inside the write transaction (which also closes the
TOCTOU the review noted); `versions(schema)` filters; `refresh_semantic_layer` threads the
schema through. A schema that is genuinely gone is removed with the new `drop_schema()`,
by name, on purpose — the refusal message points there.

Live test: a synthetic `livetest_gold.crm_contact` (the same bare name silver has, on
purpose) harvested into the graph leaves silver's tables and 3/1/1 reviewed edges exactly as
they were; the bare name is then refused as ambiguous; `drop_schema` removes it and the count
returns. DB-free test with a recording fake driver: both prunes bind `schema` and use it in
the pattern, and the guard counts only the harvested schema.

**"The retry fix has zero test coverage" — measured, and right.** Reverting `apply_accepted`
to raise-on-first-failure left every unit test green. Four DB-free tests now pin it against a
recording connection: one failure does not stop the batch, the failure is recorded and the
success stamped, a later successful retry clears the error, kinds with no graph target are
left alone. Mutation-checked: reverting the `try/except` fails three of them.

**K07's fixture could not exercise the collapse it was written for.** `itertools.combinations`
guarantees distinct pairs, so two joins over one pair — the round-1 finding — was
structurally impossible in the cookbook's data. Two of the eight joins now share a pair with
different predicates and are both accepted; the cookbook asserts two edges between that pair.

**`include_proposed` overlaid enrichments only.** Pending mappings now appear on
`Table.concepts` when asked. Pending joins are *not* traversed by `find_join_path`, and the
docstring says why: that method has no `include_proposed` in the contract on purpose — a path
through an unreviewed join is a query plan nobody approved, and a planner cannot be handed
one. A review screen lists pending joins from the `ProposalStore`.

**Minors, each done:** guard inside the transaction; dead `if True:` removed; ambiguous
bare name tested live; `write_join` / `write_authoritative_source` resolve-and-write in one
`execute_write`; the reverse ordering case (snapshot fails after the graph write) pinned live
— the graph is one ahead, the next run corrects; a reused explicit `--version` now diffs
against what its row held *before* the overwrite, since with a constant version there is no
other row to diff against (the review's suggested "second-newest" does not exist in that
case); every graph write stamps `applied_by_proposal` so the audit trail reaches the artifact.
Per-proposal commits kept, with the reason in the docstring.

**CI protection — partially closed, and it needs a decision.** The live suite is now 20
tests, gated exactly as `integration.yml` gates its own. But that job has no `pull_request`
trigger, installs `libs/agno` only, and its pgvector service has no warehouse database; and
`ci.yml`'s product job sets no live-DB variables. Wiring is filed as R-9 because both
workflows are Lane R's. The DB-free tests above are what CI *does* gate today: the retry
control flow, the guard, the schema binding on every prune, `canonical_join`, and the closed
sets. The graph-side proofs still run only where there is a graph.

**Surprising:** "one schema over" is a pattern, not a one-off. Every predicate I write over a
shared store now gets the question "shared with what?" before "does it match?".

---

---

### K11_kpi_registry_and_feasibility.py

**Status:** PASS

**Description:** K11 — the KPI registry, its graph mirror, and feasibility. The plan's
scenario: assess the whole registry, remove one table from the snapshot and re-run, then a
KPI needing two tables that are both missing.

Six definitions over the real silver schema, one of them naming `orders` and `order_items`,
which this warehouse has never held.

**Three things this package deliberately does not do.**

*Feasibility is never stored.* There is no verdict column and no write carries one — a cached
verdict goes stale the moment a harvest runs, and a stale verdict still renders as a confident
badge. It is computed against the live snapshot every time it is asked for. Two DB-free tests
assert the CREATE TABLE has no such column and no INSERT invents one.

*Feasibility is never a judgment.* `assess_registry` is a set difference over two frozensets:
no model, no client, no callable. The executor's whole job is wiring — resolve the two stores,
subtract, return the rows the discovery screen renders.

*A blocked KPI is not stamped as validated.* `revalidate` writes `validated_against_snapshot`
— the field P2 flagged as read by K6 and written by nobody — but only for definitions whose
every table is present. "Validated against snapshot X" has to mean the formula could run
against X, or K6's review screen inherits a claim nobody checked.

**The middle step is the one worth reading.** Removing `crm_contact` produces *both* a blocked
and a partial from one removal: `k11_contact__reachable` reads only that table and is blocked;
`k11_deal__pipeline_by_contact` reads it beside `crm_deal` and is partial. That distinction is
what a customer deciding what to connect next actually needs — "this area is unavailable"
versus "you are one table away".

**Result:**

```
1. 6 definitions stored, 5 tables in the layer   {'ready': 5, 'partial': 0, 'blocked': 1}
2. two-missing case      missing: ['order_items', 'orders']   -- both, not the first found
3. revalidation          5 validated against k11-full.1, 1 left unstamped
4. mirror                6 KPIs; Deal -> 3 metrics by traversal
                         KPIs reading crm_contact: contact__reachable, deal__pipeline_by_contact
5. crm_contact removed   {'ready': 3, 'partial': 1, 'blocked': 2}
                         contact__reachable    ready -> blocked
                         deal__pipeline_by_contact  ready -> partial
6. restored              {'ready': 5, 'partial': 0, 'blocked': 1}; 0 nodes left behind
```

**Tests:** 15 DB-free in `product/tests/test_k11_registry.py` (round-trip through the stored
row, the vocabulary gate refusing to write anything on a bad term, the mirror's prune being
label-scoped, discovery rows leading with blocked). 6 live in `test_semantic_live.py` covering
the mirror, its prune, a formula that stops reading a table losing its `READS` edge, and
revalidation.

**A defect the sweep found in this package, before it was committed.** The first mirror did
`MERGE (c:SemanticConcept {name: row.concept})` to hang its `MEASURED_BY` edge, so registering
six KPIs invented three concepts — `Deal`, `Contact`, `Order` — that no proposal had ever
produced. That breaks D10 outright: concepts enter the graph through review and nowhere else,
and a KPI naming one must not be able to conjure it. It surfaced as `K05_semantic_graph`
failing with *"the graph store and the stub gave a caller different answers"* — the invented
nodes showed up in `list_concepts()` on the real store and not in the stub.

Fixed by matching instead of merging, which is the rule the same method already applied to
tables and which I had written down two paragraphs above the line that broke it: *"a source
table the graph does not hold simply gets no edge — inventing a placeholder would put a table
in the semantic layer that no harvest ever saw."* The cookbook now writes `Deal`, `Contact`
and `WebSession` as accepted enrichment would, leaves `Order` unproposed on purpose, and
asserts the KPI naming it carries no edge and no node was created.

**A second defect, found by the same sweep.** With the concepts fixed, `K05_semantic_graph`
still failed — this time on *"the rebuilt copy differs from the original"*. K11's cookbook had
written a concept called `WebSession`, which is a name K05's cookbook owns, overwriting its
description in the shared graph; K05 then promoted a different set of concepts into shared
memory than it had written. K05 passed when run alone and failed in the sweep, which is the
signature of order-dependent state rather than a code defect.

Fixed by giving K11's cookbook its own names (`K11Deal`, `K11Contact`, `K11Session`,
`K11Order`) and a cleanup that removes anything starting `K11`. **A cookbook must not reuse
another cookbook's fixture names** — the stores are shared, so a name collision is a mutation,
and it surfaces two cookbooks away from its cause.

**Surprising, twice over.** First: the mirror needed to *delete* a KPI's `READS` edges before
rebuilding them, not just MERGE new ones. A formula edited to stop reading a table would
otherwise keep the old edge forever, and that edge's only purpose is answering "which KPIs
does this table break" — which it would then answer wrongly, over-reporting, on a review
screen someone is trying to trust.

Second, and more instructive: I wrote the correct rule for tables and broke it for concepts in
the same method, and no test of mine caught it. Another package's cookbook did, because it
compares two implementations that must agree. Cross-implementation equivalence checks find
things single-implementation assertions cannot — that is the second time K5's stub-versus-store
scenario has paid for itself.

---

---

### K10_unified_retrieval.py

**Status:** PASS

**Description:** K10 — one entry point across memory, domain prose, the ontology and the
semantic layer, fused by rank. The plan's scenario: ask a question needing two sources (a
domain rule plus a table name), then disable a leg and ask again.

The question is *"what is a quote request and which table holds deal records"*. No single
store can answer it: the prose explains quoting, the ontology defines `QuoteRequest`, and only
this customer's semantic layer knows the table is `crm_deal`.

**What was actually missing.** The framework already had Reciprocal Rank Fusion — in
`memory/retrieval/fuse.py`, typed to `MemoryRecord`, so nothing above the memory store could
reach it. And `unified_read` already spanned KB and memory but deliberately held the two
apart: *"Ranking the two together is out of scope."* So the gap was not a missing algorithm,
it was an algorithm one store owned. K10 extracts a generic `rrf_rank` core, has the existing
`rrf_fuse` delegate to it (all 17 of its tests pass unchanged), and gives `UnifiedSlice` a
`fused()` that ranks every leg together.

**Why fusing incomparable stores is defensible at all.** A prose chunk's score is a vector
similarity, an ontology hit's is a Neo4j index score, the semantic layer's is a name overlap.
RRF reads none of them — it reads each leg's *position*. That is the property that made
reaching for the existing implementation right rather than writing a second one: two
implementations of a ranking rule are two chances to disagree, and on a fused result set a
disagreement is invisible — the order is merely different, never obviously wrong.

**Result:**

```
1. four legs asked          kb 5 · memory 0 · ontology 5 · semantic 3
2. fused ranking            origins present: kb, ontology, semantic
                            a domain rule from the prose: True
                            a table name from the semantic layer: True
3. semantic leg disabled    origins: kb, ontology — no table name survives
4. prose leg disabled       origins: ontology, semantic — no domain rule survives
5. asked-and-empty          all four legs present, semantic 0 hits
   vs never-asked           three legs present
```

**Two design decisions worth recording.**

*Identity across stores is normalised content, not id.* Ids are per-store, so identity by id
would make every cross-store pair distinct and reduce fusion to an interleave — the one thing
a unified read exists to improve on. Content is the only key two stores share, and the case it
gets right is the case that matters: a rule the prose states and memory has also recorded
reads as corroborated by two legs.

*A corroborated hit reports every leg that had it.* The fused item keeps the copy from the leg
that saw it first, so `origin` alone names one contributor. A unit test caught that while I was
writing it — the fused set was missing `ontology` entirely because its only hit was one the KB
also had. `metadata["fused_origins"]` now lists them all: *"the prose and the ontology both say
this"* is a different claim from *"two legs said this"*.

**Framework changes** (`libs/agno/egpt/memory/retrieval/`, so every deployment inherits them):
generic `rrf_rank` + `FusedItem`; `UnifiedResult.origin`; `UnifiedSlice.extra`, `legs()` and
`fused()`; extra named legs on both `unified_read` and `aunified_read`, run concurrently on the
async path. One existing framework test asserted the old `UnifiedResult` shape and was updated
— the added field is deliberate. 3759 memory tests pass; import-linter contracts both kept.

**Surprising:** the hardest part was not the fusion, it was resisting a second implementation.
The obvious move was a fresh RRF over `UnifiedResult` — it would have been thirty lines and
looked clean. It would also have been the second ranking rule in the codebase, and the first
time the two disagreed nobody would have noticed.

---
