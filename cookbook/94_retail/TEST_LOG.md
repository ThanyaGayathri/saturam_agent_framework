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
