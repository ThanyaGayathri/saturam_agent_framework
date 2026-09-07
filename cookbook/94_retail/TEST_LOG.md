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
