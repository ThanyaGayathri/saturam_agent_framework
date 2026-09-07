# Contract changelog

One place per contract, appended to, with the date, the reason and who agreed. Rule 5 of
the change protocol in `docs/plan/delivery-plan.md`.

## KPI formula shape

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
