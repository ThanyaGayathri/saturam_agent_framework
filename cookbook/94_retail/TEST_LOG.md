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
