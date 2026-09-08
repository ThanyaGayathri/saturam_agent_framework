# Contract changelog

One place per contract, appended to, with the date, the reason and who agreed. Rule 5 of
the change protocol in `docs/plan/delivery-plan.md`.

## KPI formula shape

### Patch — 2026-09-07 — correctness fixes from automated review (two rounds)

Not a shape change; no field moved, so `CONTRACT_VERSION` stays `2.0.0` per this file's own
policy of bumping only on a breaking change. Headed by date rather than a patch-style version
number for that reason — nothing at runtime will ever equal a `2.0.1`, so a heading implying
one exists is worse than a heading that doesn't try. All were defects that made
`KpiDefinition` refuse a valid formula, or (round two) a fix for one of those defects
overshooting into a new false positive.

| Fix | Was |
|---|---|
| `source_tables` is normalised to lower case at construction | `referenced_tables` lower-cased what it read from the SQL but the drift check compared against the raw `source_tables`, so `source_tables={"Orders"}` against `FROM Orders` was refused — and the refusal blamed the author for omitting a table they had listed |
| `_TABLE_RE` captures an optional schema qualifier and keeps the table | `FROM silver.orders` captured `silver`. Since `TableRef.schema` defaults to `"silver"`, qualified formulas are the expected case: listing `orders` failed construction, and listing `silver` passed construction and then left the KPI permanently blocked, because no snapshot holds a table called `silver` |
| `_POSITIONAL_RE` no longer requires a non-word character before `?` | `WHERE a=?AND b=1` and `col?` slipped past the one check that refuses positional binding |
| `_POSITIONAL_RE` excludes a marker only when it is glued on **both** sides at once (round two) | The first fix's lookahead removal was too broad: any `?` glued to a following word character now matched, including one sitting inside an unrelated string literal (a URL query string like `'foo.com/page?x=1'`, or free text like `'what?next'`) — a valid formula that happened to compare against one was refused at construction |

Also in the validation harness, not the contract: the result-shape heuristic now strips
subqueries and treats any outer `GROUP BY` as a rowset, instead of counting projected columns.
The old rule tagged `SELECT SUM(x) FROM t GROUP BY t.id` as `SCALAR` — a rowset materialised
as a metric and thresholded by an alert that could never be right. **This changes the corpus
result: rowsets found went from 2 to 3**, the third being `Product.sell_through_rate`. Round
two: that stripping was not string-literal-aware, so an unbalanced `(` inside a string (a
store name like `'Toys(R)Us'`) could swallow a genuine outer `ORDER BY`/`GROUP BY`/`LIMIT`
appearing after it, and `GROUP BY` text sitting inside a string literal could itself be
misread as a real outer clause. Fixed by tracking single-quoted string state while scanning
and dropping string content from the output entirely, in both directions.


### 2.0.0 — 2026-09-07 — closed sets move out of Python

Agreed by: Lane K. **Breaking; still awaiting Lane R review** along with 1.0.0.

`grain` and `business_area` were Python enums whose members came, unexamined, from the
POC's synthetic ecommerce ontology — `stock_level`, `warehouse_zone`, `category`. Two things
were wrong with that. They were one dataset's concepts masquerading as facts about retail,
and a frozen enum cannot be extended by a customer overlay, which contradicts D9 and G10:
one ontology ships, customers extend it, and the overlay survives a core upgrade.

Both fields are now `str`, drawn from a vocabulary loaded from the shipped ontology plus the
customer's overlay. The closed-set rule is unchanged and now enforced at the point it
matters: `KpiRegistry.build` refuses a term the deployment does not hold, and a skill's
prompt renders its enumerated list from the same vocabulary rather than hand-typing it, so
the prompt stays correct after an overlay is applied.

`result_shape` and `feasibility` stay Python enums, because a customer's business cannot add
a fourth outcome to a set difference or a third way for SQL to return. They are mirrored in
the vocabulary so prompts can render them uniformly, with `enums_match_vocabulary()`
checking the two definitions have not drifted.

Shipped `grain` now has eleven terms, all defensibly retail-general. The test applied to
each: would the next retail customer recognise it without being told what business we built
it for? `stock_level` and `warehouse_zone` failed that test and were dropped.

| Change | Cost to Lane R |
|---|---|
| `grain: Grain` → `grain: str` | Type change on a field they have not yet consumed |
| `business_area: BusinessArea` → `str` | Same |
| Terms validated by `KpiRegistry`, not `KpiDefinition` | A bare definition is unchecked until it enters a registry; build registries, not lists |
| Vocabularies load from disk | Both sync and async variants provided |


### 1.0.0 — 2026-09-07 — initial, after validation against the POC corpus

Agreed by: Lane K. **Not yet reviewed by Lane R** — this contract is `K → R`, so it is a
proposal until Lane R has read it.

The shape as written in the delivery plan is *name, description, formula with its filter
parameter, grain, source tables, business area*. Validating it against nineteen real
formulas (`cookbook/94_retail/K11_kpi_formula_shape.py`) changed it in five places. Each is
a departure from the agreed wording and therefore needs Lane R's agreement before P3.

| Change | Why | Cost to Lane R |
|---|---|---|
| **Named parameters only; positional `?` refused** | 15 of 19 formulas bind positionally. A `?` carries no name, so nothing can check that supplied arguments match expected ones, and 4 formulas declared a `param` name the SQL never used — undetectable while the marker is anonymous. | Mechanical rewrite, done in `migrate_poc_record`. |
| **`filter parameter` becomes `parameters`, plural, each with a kind** | The contract assumed one filter parameter. The corpus uses two mechanisms that are not interchangeable: a `PREDICATE` (an interpolated boolean SQL fragment, the concept's scoping filter) and a `SCALAR` (a driver-bound value). Collapsing them would mean either interpolating a value or binding a fragment; both are wrong. | Lane R binds the two kinds differently. |
| **`result_shape` added** | 2 of 19 return row listings, not metrics. An alert cannot be evaluated against a top-10 list and a trend cannot be drawn from it. | R5 materialises the two shapes differently; R12 skips `ROWSET`. |
| **`time_window` declared, not written into the SQL** | 4 formulas express a lookback as wall-clock `now` minus an interval. The same formula run twice gives two answers, yesterday's number cannot be recomputed and a backfill is impossible. | Lane R binds an as-of date at execution. Settles the "recompute or append" question the plan flagged: **recompute against a bound as-of date**. |
| **`dialect` added** | 5 formulas use SQLite-only functions against a PostgreSQL warehouse (D5). | Naming it turns a 2 a.m. runtime failure into a listed translation task. |

Two fields were considered and **not** added:

- *Formula-carried grain.* The plan asked whether the formula should carry its own grain.
  It should not: grain is a property of the definition, and letting the SQL imply it means
  Lane R parses SQL to decide how to materialise. `grain` stays a declared closed-set field.
- *A correctness assertion.* `StockLevel.stockout_risk_count` cancels to a constant and
  would still be `ready`. Tempting to add a check; wrong place for it. Feasibility answers
  *can this be computed*, golden questions (R15) answer *is the answer right*. Merging them
  would make the badge mean two things.

## Semantic read API

### 1.0.0 — 2026-09-07 — initial, with a running stub

Agreed by: Lane K. **Not yet reviewed by Lane R** — this contract is `K → R`.

Four reads, as K5 specifies: what tables exist, what columns a table has, what a concept
means, and what joins connect two tables. Plus `list_business_areas`, because K9 derives
supported areas from confirmed mappings and the discovery screen groups by area. Every
method has a sync and an async variant.

Two decisions are encoded in the shape rather than left to callers to remember.

**`include_proposed` defaults to `False` on every read.** The constraint is that nothing
unproposed and unconfirmed enters the semantic layer; a read surface whose default returned
unreviewed rows would make that constraint depend on every caller opting out. A proposed
column nested inside an accepted table is filtered too, so a caller that did not ask for
proposals cannot receive one inside something it did ask for. **Open question for Lane R** —
raised in P0, unanswered so far, and the review screen is the caller that will need `True`.

**`find_join_path` distinguishes three outcomes.** A path, `None` when the graph holds no
path, and a raised `SemanticReadError` when the reader cannot answer. Collapsing the last two
is how a model ends up inventing a join: a caller handed `None` tells the user the datasets
cannot be joined, and that must never be a way of saying the graph was unreachable.

**The stub ships with no fixture**, per the P0 decision. Seeding it from the POC's synthetic
ecommerce payloads would have handed Lane R plausible retail shapes that are not the
customer's; an unloaded read raises `FixtureNotLoaded` naming the fix instead. Lane R gets
the half that actually blocks them — signatures, types and the semantics of an empty result —
and swaps in a fixture when the harvest lands.

### 1.0.0 — 2026-09-07 — reviewed by Lane R: accepted, with an HTTP hardening

Agreed by: Lane R. The `K → R` contract is now agreed on both sides.

`include_proposed=False` as the default: **accepted**. The argument holds — a read surface
whose default returns unreviewed rows makes the constraint depend on every caller opting out.

Over HTTP the default is kept and strengthened: `include_proposed=true` additionally requires
the `semantic:read_proposed` scope, because in-process it is a deliberate act by our own
code and over HTTP it is an attacker-controlled query parameter returning unreviewed
synonyms. The three join-path outcomes map to `200` with a path, `200` with `path: null`,
and `503` — never `404` for "no path", since every HTTP client collapses 404 into "empty".
Both are recorded in the API contract's own changelog
(`egpt_retail/api/openapi/CHANGELOG.md`, 1.0.0); they change nothing in this module.

**Additive, announced (rule 1):** `_fixtures/r3_contract.json`, generated by
`egpt_retail.contracts.fixture_builder` from the shipped vocabularies and the vendored KPI
corpus. The stub still ships with no fixture; this file exists so the phase-zero stub
*server* has something to serve and every example in the API contract is drawn from one
source. Header reads "contract-shaped, not customer-shaped. Replace at K5." Nothing about
the stub or this contract changes.

**Open question raised, not resolved:** the prototype's business hierarchy carries ten retail
areas (`inventory`, `orders`, `merchandise`, `store-ops`, `e-com`, `pos`, `support`,
`pricing`, `finance`, `workforce`); the shipped `business_area` vocabulary carries six. The
API contract treats area ids as vocabulary terms plus overlay, so today four of the
prototype's areas would be overlay terms. Whether the shipped vocabulary should grow, or the
prototype should remap, is Lane K's call and needs settling before P5 wiring.

## KPI formula shape — reviewed by Lane R

### 1.0.0 and 2.0.0 — 2026-09-07 — accepted

Agreed by: Lane R. Both versions are now agreed on both sides.

All five 1.0.0 departures accepted. Consequences Lane R records for its own packages:

- **R6's SQL guard parses the assembled statement, not the template.** An interpolated
  `PREDICATE` is a fragment that only exists post-assembly, so a guard over the template
  would pass a formula whose accepted `sql_filter` was later edited into something else.
- **R5 recomputes against a bound as-of date**, replacing that partition rather than
  appending. `time_window` declared-not-baked is what makes yesterday recomputable.
- **R5 refuses a `sqlite` formula against the warehouse** rather than translating silently.
  Translation is a listed task, not a runtime surprise.
- **R12 skips `ROWSET`** — an alert cannot be evaluated against a listing.

2.0.0 accepted: enums-to-vocabulary-terms is the only shape compatible with D9 and G10, and
`KpiRegistry.build` is the right enforcement point — the set is closed at runtime against
the deployment's vocabulary, which is what "closed sets" has to mean once customers extend
the ontology. One request, additive: **expose the vocabulary version `build` validated
against** on the registry, so R5's provenance record (`Provenance.vocabulary_version` in the
API contract) can name it without a second lookup.

## Executor registry — reviewed by Lane R

### 2026-09-07 — one-name exemption accepted, one amendment

The one-executor-one-name exemption from the standing sync/async rule is **accepted**: the
flat `__name__` namespace cannot afford a doubled surface for a choice nobody makes, and the
exemption is scoped to executors only.

Amendment, recorded in `EXECUTOR_REGISTRY.md`: `check_registry` inspects live functions and
cannot see the `executor_ref` strings already persisted in stored workflows, so a rename
passes the check and breaks every persisted workflow at once. That is the concrete reason a
rename is a change-protocol event, and it belongs in the naming rule rather than in a test
log.

Lane R also takes ownership of the upstream finding: `Registry.get_function`
(`libs/agno/egpt/registry/registry.py:366`) has no duplicate warning while
`_entrypoint_lookup` twenty lines above does. A framework fix, in Lane R's framework bucket
beside R6 and R7; not part of any phase-zero PR.
