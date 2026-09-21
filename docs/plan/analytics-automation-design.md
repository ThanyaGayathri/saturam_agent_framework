# Analytics Automation -- design

*A question about the business, answered continuously. Draft for discussion, 16 Sep 2026.*

---

## 1. What it is

Process Automation turns a **document** into a **record**. Analytics Automation turns a
**question** into a **standing answer**: it picks the KPIs the question is about, reads them
on a schedule, compares them to a baseline, explains the movement in words grounded in the
rows, and puts the result where the audience already looks -- a dashboard, an email, an
alert.

Two kinds of question arrive, and the platform should take both from the same form:

| Kind | Example | What the person wants |
|---|---|---|
| **Monitoring** ("visibility into X") | Middleby's RFQ & Quote Pipeline Performance | The same numbers every week, broken out the same way, with what moved and why, for a broad audience |
| **Diagnostic** ("why is X …") | "Why is my revenue low last week?" | One investigation now -- which segment, channel or product explains the drop -- and, if it is worth keeping, the same investigation every week |

The category already exists in the builder (`analytics_automation`, shapes **Inform** and
**Alert**) and the run chain already exists in the compiler (read gold → compute → compare to
baseline → explain → grounding check → provenance → deliver). What is missing is the front
half (question → KPIs → outputs) and the outputs themselves. This document is about those.

---

## 2. The flow, step by step

Same nine steps as the document use case; different data.

**Step 1 -- Describe.** The person types the question or pastes the ask (the RFQ text as
given works as-is). The reader resolves it against **what the connected data supports** --
the semantic layer over the warehouse (facts, measures, dimensions, their freshness) --
and only then against the KPI registry. Decided 16 Sep: a KPI the data cannot compute is
never suggested, and gold is materialised for exactly the KPIs the use case names.

- **Business area** from the words (`detect_business_areas`): sales pipeline, margin, stock.
- **Data first.** The semantic layer says which facts and dimensions exist for that area
  (a quote/deal fact with brand, category, territory, state, segment; a sales fact with
  channel, category, store) and how fresh they are. This is the universe the suggestions
  are drawn from.
- **KPIs** that the data supports: registry KPIs already computable on those facts
  ("quotes generated", "closed-won rate" → `rfq__*`; "revenue" → net sales), and, for a
  phrase with no KPI yet, a **proposed KPI** derived by the K12 agent *from the fact's own
  columns* (lost-reason breakdown from `crm_deal.lost_reason`), handed to the KPI screen
  for approval. A use case never runs on a KPI nobody approved, and never proposes one the
  data cannot compute.
- **Feasibility** per KPI (`assess_kpi_feasibility`): ready (gold exists, fresh), buildable
  (silver exists, gold missing -- built on activation), blocked (the data is not there).
  The form shows all three states; a blocked KPI is a gap with the fix named.
- **Dimensions** from the text: brand, PIM category, sales-rep territory, state, customer
  segment -- validated against the semantic layer (is `territory` a dimension on the quote
  fact?).
- **Comparison baseline:** prior period, same period last year, plan/target -- the reader
  picks one from cues ("against plan", "vs last year"), default prior period.
- **Time grain and window:** weekly / monthly; "last week" fixes both.
- **Audience and delivery:** who, and where they look.
- **Trigger:** every run (Inform) or only when a KPI crosses a line (Alert) -- the
  category's existing choice.

**Step 2 -- Spec.** `metrics`, `dimensions`, `comparison`, `scope` (filters), `grain`,
`window`, `schedule`, `outputs` (see §3), `recipients`, `condition` (for Alert),
`explanation` (depth: numbers only / movers / drivers). Slot names, as today.

**Step 3 -- Readiness, real probes.** Per KPI: gold present and its last load time (stale
gold is a warning with the age); row count for the window; each dimension exists on the
fact; the baseline period has data (a "vs last year" with no last year is a failure now, not
an empty report later). Delivery: SMTP login (the executor exists), in-app channel.
Model: one call. And a **dry answer**: compute the KPIs for the window now and show the
numbers -- the analytics equivalent of "36 messages match the filter".

**Step 4 -- Plan.** The Inform / Alert stage sequence, extended with output stages the
spec turns on: `compose_dashboard`, `render_report`, `send_email`. Two views as today.

**Step 5 -- Rehearse.** Run over a chosen window (last complete week) with delivery held:
the person sees the numbers, the movers, the narrative, and the grounding verdict (every
figure in the narrative traced to a row). "Accept as the golden report" keeps that run as
the reference; a material spec change re-runs it and diffs the numbers.

**Step 6 -- Activate.** Version frozen, compiled snapshot stored, dashboard created (if
chosen) and linked to the use case.

**Steps 7-9 -- Schedule, run, read.** Poller or orchestrator fires; the chain runs; the
dashboard's tiles refresh from the run's gold read; the report goes out; Monitor shows the
run, and a *silent* verdict if a week passes without one.

---

## 3. Outputs -- what comes out, and when to use which

| Output | What it is | Best for | Exists today? |
|---|---|---|---|
| **Dashboard** | Tiles composed from the use case's KPIs × dimensions, filters from the scope, refreshed by each run. Lives under **KPIs → Dashboards** in the same store as a hand-built one, tagged with the use case that owns it; editable by hand afterwards. Composed automatically at activation (decided 16 Sep). | Monitoring questions with a broad audience (the RFQ case) | Store and tiles exist (R13); the *composer* from a spec does not |
| **Scheduled report** | The narrative: headline numbers vs baseline, top movers by dimension, why (grounded), with a table appendix. **HTML email with rendered charts and tiles from release 1** (decided 16 Sep), the same content in-app; Slack/Teams later. | Every Inform; the weekly digest people actually read | `deliver_in_app` exists; `send_email` exists (documents); the HTML report renderer does not |
| **Alert** | Fires when a KPI crosses a threshold; quiet otherwise. | "Tell me when closed-won rate drops under 25 %" | Exists (R12) |
| **Anomaly watch** | Like an alert, but the line is statistical (outside the KPI's own recent band) rather than typed in. | KPIs nobody knows the right threshold for | Not yet; a second `condition` kind on the Alert shape |
| **One-off answer** | The diagnostic question run once, now, on the chosen window; can be promoted to a scheduled report with one click. | "Why is revenue low last week?" | The run exists (Inform, manual trigger); the "run once, then promote" flow does not |
| **Driver analysis** | Which values of which dimension explain the movement (contribution), not just which moved most. | Every diagnostic question | `rank_movers` does the "most moved" half; contribution is new |

Recommendation: the first release does **Dashboard + Scheduled report (email + in-app) +
Alert**, with the one-off answer as the entry point for diagnostic questions. Anomaly watch
and driver analysis are the second release.

---

## 4. How this differs from the KPI screen's dashboards

| | KPI screen → dashboard | Analytics Automation use case |
|---|---|---|
| Starts from | A person picking KPIs and arranging tiles | A question or an ask in plain words |
| KPI choice | Manual | Resolved from the question; missing KPIs proposed for approval |
| Comparison | A tile shows a number | Every number against a baseline (prior period, last year, plan) |
| Explanation | None | A grounded narrative: what moved, where, why -- every figure traceable |
| Delivery | People open the dashboard | Report reaches the audience on the schedule (email, in-app); dashboard refreshed alongside |
| Watching | None | Alert / anomaly on the same KPIs |
| Lifecycle | Static once built | Versioned spec, readiness probes, rehearsal, run history, Monitor's silence check |
| Ownership | The person who built it | An owner and an audience recorded on the use case |

The relationship: **the use case produces a dashboard; it does not replace the dashboard
screen.** The tiles it composes are ordinary tiles in the same store, editable by hand
afterwards. The KPI screen stays the manual path; the use case is the automated one -- the
same relationship as "create a record by hand in Salesforce" and "Process Automation".

---

## 5. Industry standard

Gartner calls this **augmented analytics**; the products people compare against:

- **Tableau Pulse** -- metric definitions in a layer, AI digests ("what changed and why")
  delivered on a schedule, threshold and trend alerts, follow-up questions on a digest.
- **ThoughtSpot** -- natural-language questions over a semantic model, SpotIQ anomaly and
  key-driver analysis, Liveboards, scheduled delivery.
- **Power BI** -- metrics/goals, data alerts, Copilot narratives on a report.
- **Looker / dbt Semantic Layer** -- the one-definition-per-metric governance underneath.

The common pattern, which is what §2 follows: **a governed metrics layer → questions
resolved to metrics → scheduled digests with narrative → threshold and anomaly alerts →
driver analysis and drill → self-serve follow-up**, with provenance on every figure. Where
this platform already differs in its favour: the narrative is produced by a model but
**checked by code** (`check_explanation_grounding`), every run is versioned and monitored
like a process, and a KPI that is not approved and ready cannot be reported on.

---

## 6. What else can be scoped under Analytics Automation

In rough priority:

1. **Audience-scoped delivery** -- one use case, one report per territory / dealer, each
   recipient seeing their slice (the RFQ audience is four roles with different scopes).
2. **Plan variance** -- targets as a baseline (needs a targets table per KPI).
3. **KPI data health gate** -- freshness and completeness checked before a report goes out;
   a stale week says "not refreshed since …" instead of showing last week's numbers again.
4. **Follow-up questions** -- "why did Texas drop?" asked on a delivered report, answered
   from the same run's rows (the chat surface over provenance).
5. **Hand-off to Decision Intelligence** -- a diagnostic that finds the driver can propose
   a Recommend use case on it ("markdown the SKUs that drove the margin drop").
6. **Slack / Teams delivery** -- one more channel on the same report renderer.
7. **Forecast vs actual** -- when a forecasting KPI exists.

---

## 7. The RFQ ask, mapped

**Shape:** Inform, weekly, plus a Dashboard, plus one Alert.

- **KPIs:** quote requests (platform-wide), quote-to-deal / closed-won rate, lost reasons
  (count and share, monthly) -- `rfq__quote_requests_by_brand`, `rfq__quote_to_deal_rate`
  exist; lost-reason breakdown and the HubSpot "can I buy" intent signal are **proposed
  KPIs** (the second needs the HubSpot source connected -- a readiness gap, named).
- **Dimensions:** brand, PIM category, sales-rep territory, state, customer segment.
- **Baseline:** prior week and same week last year.
- **Outputs:** a dashboard (quotes by each dimension, win rate, lost reasons); a Monday
  report to Middleby Shop, divisions, reps and dealers; an alert when closed-won rate for
  any brand drops below its 12-week median by more than 5 points (anomaly watch, release 2;
  a fixed threshold in release 1).
- **Audience scoping:** reps see their territory, dealers their accounts (§6.1) -- release 2.

**"Why is revenue low last week?"**, mapped: diagnostic Inform, KPI net sales, window last
week, baseline prior week, dimensions the semantic layer offers on the sales fact (channel,
category, store, customer segment), explanation depth "drivers", run once now; promote to
weekly if wanted.

---

## 8. Decisions (16 Sep 2026)

- Suggestions are drawn from **what the data supports** (semantic layer first), then the
  registry; gold is built for the KPIs the use case names.
- Dashboards produced by a use case live under **KPIs → Dashboards**, composed automatically
  at activation, editable afterwards.
- Emailed reports are **HTML with charts** from release 1.
- The use-case screen **suggests from the ask** -- KPIs, dimensions, baseline, outputs --
  that is the intelligence of the screen; the person confirms.
- **No targets** (no "vs plan") and **no HubSpot** in this release.
- Release 1 builds two use cases: **RFQ & Quote Pipeline Performance** (monitoring) and
  **"Why is my revenue low last week?"** (diagnostic).

## 9. Built (17 Sep, K23)

Release 1 for the monitoring question: the suggestion from the ask, the Inform chain
(compare with backfill, grounded narrative, HTML report by email and in-app), readiness
probes, simulation, activation composing the dashboard, run history and Monitor. See
`cookbook/94_retail/TEST_LOG.md`, K23.

## 10. Still open

1. **Proposed KPIs** -- approved by the use-case author on the spot, or by the KPI owner on
   the KPI screen (the K12 proposals path)?
2. **Diagnostic entry point** -- "why is revenue low" on the builder form like the rest, or
   also from the Playground with "make this a weekly report"?
3. **The data** -- where it is loaded and what it holds (see the questions sent 16 Sep).

## 11. The diagnostic kind -- plan (18 Sep 2026, agreed in brainstorm, revised the same day; built the same day as K25 -- see `cookbook/94_retail/TEST_LOG.md`)

One category, two kinds. Both are Analytics Automation; both start from an ask in words,
resolve KPIs the data supports, run **every run** on a schedule, produce **a report and a
dashboard**, and carry readiness, rehearsal, run history and Monitor. The diagnostic kind
is the monitoring kind plus one deterministic step -- the decomposition -- and the two
questions that step cannot answer from the data alone.

### 11.1 Monitoring vs diagnostic

| | Monitoring ("visibility into X") | Diagnostic ("why did X change") |
|---|---|---|
| The ask | A state to watch, for an audience | A change to explain, for a decision |
| Headline | Several KPIs, each with breakdowns | **One** KPI (the one that moved) |
| Comparison | Optional, the person's choice (never a default, 18 Sep) | Inherent: two periods, inferred from the words, editable |
| Dimensions | What to break the numbers down by | What to **search the cause across** -- every grain the fact carries, all ticked by default |
| Norm | none | Each slice's own recent history, so "unusual" is measured, not typed |
| Chain | read gold → (compare) → explain → ground → render → deliver | read gold → compare → **decompose → pull evidence** → explain → ground → render → deliver |
| The model's job | Narrate the comparison rows | Narrate the **winner of a ranking code produced**; it never picks the cause |
| Report | KPI tiles and breakdown tables | One sentence whose subject changes per run; under it the ranked drivers; under that the evidence rows |
| Dashboard | Tiles per KPI × dimension | A **"Why"** tile at the top (last run's sentence and drivers, winner linking to the filtered view) above the same tiles |
| Trigger | Every run | Every run (thresholds stay the Alert shape's business) |

### 11.2 What the form asks, for a diagnostic ask

Derived from the words first; every one editable; nothing the data can decide is asked.

1. **The KPI** -- one. Several matches → the form asks which.
2. **The two periods** -- inferred ("last week" → last complete week vs the one before;
   "vs last year" → prior year), shown as dates. Readiness checks the grain the data can
   bear from the row counts (the quote fact closes ~100/week: weekly holds, daily does
   not for a rate) and says so with the number.
3. **Search the cause across** -- the grains the fact carries, all ticked; untick to
   exclude one.
4. **Sensitivity** -- how readily a slice is called unusual: *only clear outliers · usual ·
   flag more*. Three words, not a number. It sets the statistical bar (§11.3) and the
   support floor together. Default *usual*.
5. **Evidence columns** -- which columns of the evidence rows to show (defaults from the
   fact's descriptive columns; for a rate, the losing side's reason column is always in).

Not asked, shown instead: the **history used for the norm** (trailing 12 periods, stated
on readiness), the **minimum support** (derived from the data and stated: "a slice needs
20 closed quotes in the week to be named"), and the **row counts per period** the dry
answer already gives.

Decided 18 Sep: no "only when it moved by more than X" -- a diagnostic runs every run; a
line to cross is what the Alert shape is for.

### 11.3 The decomposition executor -- generalised spec

Deterministic, unit-tested on synthetic data, one executor for every KPI the registry can
break down. It follows the published root-cause pattern for multi-dimensional metrics
(Microsoft Adtributor; ThoughtSpot SpotIQ change analysis; Power BI key influencers and
decomposition tree; Tableau Explain Data): **explanatory power × surprise, one dimension
at a time, then the winning dimension's intersections, with a support floor.**

**Inputs.** The KPI's definition from the registry -- its **type** (*additive*: count, sum;
*ratio*: numerator over denominator, which is also what "average" is) and its **parts**
(the numerator and denominator formulas -- the same "Made of" metadata the KPI audit
panel shows); the two periods; the grains to search; sensitivity; the fact's rows for the
two periods and for the trailing N periods, read from silver through the KPI's own
formula grouped by each grain (the semantic layer's breakdown, as the dashboard's bar
tiles read it today).

**Step 1 -- headline.** Metric at both periods, delta, delta %.

**Step 2 -- contribution per slice, per dimension.**
- Additive: contribution of slice *s* = Δ*s*; share of gap = Δ*s* / Δ.
- Ratio: two-way variance split per slice -- **rate effect** (the slice's own rate moved,
  weighted by its share) and **mix effect** (the slice's share of the denominator moved,
  weighted by its rate gap to the whole); the two sum to the headline delta across
  slices. "Ovens' own win rate fell" and "more of the closes were Ovens, which win less"
  are different findings and are reported as such.

**Step 3 -- surprise per slice.** The slice's current value against its own trailing N
periods: robust z (median and MAD, so one wild week does not set the bar). Sensitivity
maps to the bar: clear outliers |z| ≥ 3 · usual ≥ 2 · flag more ≥ 1.5. This is what makes
"7 of 9 lost vs a normal 35%" a statement rather than a coincidence.

**Step 4 -- support floor.** A slice needs at least max(*k*, *p* % of the period's rows)
rows in the period (defaults *k* = 20, *p* = 5, scaled by sensitivity) to be named; below
it, its rows fold into "other" and, if it would have won, the report says "too few to
say" with the count.

**Step 5 -- rank dimensions, then slices.** A dimension's explanatory power = the share of
the gap its top slices account for (Adtributor's measure); the best dimension is the one
that explains the most with the fewest slices. Within it, slices rank by
share of gap × surprise. Ties within 10 % are reported as ties, not broken.

**Step 6 -- one level down.** For the winning slice only, repeat step 2–5 across the
remaining dimensions (Ovens → South Territory), depth 2, same support floor. Deeper is
noise on this data volume.

**Step 7 -- evidence.** The rows behind the winner for the current period: for a ratio,
the losing side (lost quotes) with the reason column tallied; for a count, the rows of
the slice. Capped, with the count.

**Output** (the only thing the model sees):
`headline {kpi, period, baseline_period, current, baseline, delta, delta_pct}`,
`drivers [{dimension, value, effect: volume | rate | mix, contribution, share_of_gap,
current, baseline, norm, z, support}]` (top 3 + ties), `intersections [...]` (depth 2),
`evidence {rows, count, tallies}`, `confidence {share_explained_by_top, ties,
below_support: [...]}`, `caveats [...]`. The explanation model writes the sentence from
this and nothing else; `check_report_grounding` verifies every figure against it, as
today.

**Generalisation.** Any registry KPI qualifies when the registry knows (a) its type and
parts and (b) the grains its fact carries. (a) is the "Made of" metadata from the KPI
audit design (§9 of that document) -- derived once from the formula by the same
explainability pass and stored on the KPI; (b) the semantic layer holds. A KPI without
parts is treated as additive; a KPI whose fact carries no grains cannot be diagnosed, and
readiness says so.

### 11.4 Not in this plan

Targets (no plan data); the collinear web-session funnel flags; daily grain on the quote
fact; seasonality in the norm (same-weekday history -- a later refinement); cross-KPI
causes ("win rate fell because volume rose") -- the first release names *where* the change
sits, not the mechanism behind it.

## 12. The predictive and prescriptive kinds -- plan (20 Sep 2026, agreed in brainstorm; not built)

Decided 20 Sep with the CEO's steer: the purpose of Analytics Automation is **insight as a
recommendation or an alert**, which a Decision Intelligence use case then acts on. The
report and the dashboard present the outcome; they are not the outcome. Two kinds join the
monitoring report and the diagnosis: **at-risk** (who or what is about to go wrong) and,
designed on synthetic inventory data, **stockout risk**.

### 12.1 One chain, four kinds

```
Define → Measure → Detect → Predict → Explain → Recommend / Alert → Hand off → Track
```

| Stage | Monitoring | Diagnosis | At-risk (K26) | Stockout (K27) |
|---|---|---|---|---|
| Define (registry + what my data supports) | KPIs | one KPI | the KPIs the score reads | days of cover |
| Measure (gold) | ✓ | ✓ | ✓ | ✓ |
| Detect (compare / decompose) | compare, optional | decompose | *not run* -- a risk is not a change | compare (optional) |
| **Predict** | -- | -- | `score_risk`: a trained model over each open quote and its account | `forecast_demand`: demand over the lead time |
| Explain (grounded) | narrative | the sentence | per subject: why it is at risk | per SKU: why it will run out |
| **Recommend / Alert** | -- | -- | from the catalogue, by risk level | from the catalogue, by days of cover |
| Hand off | -- | -- | the recommendations store | the recommendations store |
| Track | -- | -- | did it convert / lapse | did it stock out |

**Detect → Predict, answered.** The stages are not a strict pipe; each reads what it needs
from the run's context. Predict reads *Measure* (gold and the rows) and, when Detect ran,
its drivers as features and as explanation. For the at-risk kind Detect does not run at
all: nothing changed that needs decomposing, the question is who will fail next. For
stockout Detect is optional (a demand shift is worth naming next to the forecast).

**Recommendation, alert, or both** is a per-use-case choice on the form: *recommend*
produces a recommendation for every subject above the risk line; *alert* fires only when a
subject crosses it (or when the count above the line crosses a number); *both* does both.

**More than one KPI** is normal for these kinds: the at-risk score reads quote-level KPIs
(days open vs typical time to close, stage) and account-level RFM (recency, frequency,
monetary); each is a registered KPI, each checked against what the data supports, so the
"story" the demo needs -- registry → data supports → KPIs → score → outcome -- holds.

### 12.2 At-risk pipeline (K26) -- the Middleby demo

**Subject:** an open quote, with its account. **Terminology:** *quote conversion risk*
("stalled pipeline") for the quote; *at-risk account* (RFM churn) for the account.

**Decided:** risk = the quote will not convert within its usual range (the category's
90th-percentile time to close, 27 days on this data); an account has churned when it has
placed no quote for **60 days**; a **trained model**, not a rule table; delivery **both** --
a weekly ranked report to sales and an alert per account to its rep; actions from a
**catalogue**.

**KPIs (registered, data-supported; `PA06_middleby_at_risk_kpis.py`):** only genuine
industry KPIs -- `rfq__sales_cycle_length_by_category` (the usual, p90),
`rfq__stalled_quotes` and `_by_territory`, `rfq__stalled_pipeline_value`,
`rfq__quote_conversion_within_usual`, `rfq__customer_churn_rate_60d`,
`rfq__at_risk_accounts` and `_by_segment`, `rfq__rfm_segments`, `rfq__avg_account_recency`,
`rfq__avg_account_frequency_180d`, `rfq__avg_account_monetary_180d`. A per-quote age or a
per-account win rate is a model feature, not a KPI. Existing: `rfq__open_pipeline_value`,
`rfq__average_days_to_close`, `rfq__closed_won_rate`.

**The model.** Labels from history: every quote closed in the past is a training row (won
= converted, lost or lapsed past the usual range = not); features are the KPIs above at the
quote's own date (no leakage: computed as-of, the way gold is). Logistic regression with
calibrated probabilities first -- explainable per row as feature contributions, which is
what the grounded narrative needs -- gradient boosting behind a flag once there is a
holdout to justify it. Trained at activation and retrained on the schedule; readiness
shows the holdout AUC, calibration and the base rate, and refuses activation below a floor
(AUC 0.65) with the reason. Scoring is per run over every open quote; the account's RFM
segment (Champions / Loyal / At risk / Can't lose them / Hibernating / Lost, the standard
quintile scheme) is attached as context, and "at-risk account" is its own flag when
recency exceeds 60 days on an account that was active.

**Outcome -- the Recommendation record** (an Alert is one with `kind = alert`):

```
id, use_case_id, run_id, kind: recommendation | alert
subject: {type: quote, id, label, account_id, account_label}
risk: {score 0..1, level: high | medium | low, horizon_days, model_version}
drivers: [{feature, value, typical, contribution, text}]      -- grounded
recommended_action: {code, text, from_catalogue}              -- null on an alert
value_at_stake: {amount, unit}
evidence: {columns, rows}
status: open | accepted | dismissed | actioned | expired      -- Decision Intelligence writes these
expires_at, created_at, owner
```

**The action catalogue -- "next best action".** Shipped as data the use case author can
edit, worded the way a sales organisation already talks (sales plays, owners, SLAs). The
recommend step picks by rule from the drivers and the risk level, never by the model, and
the record shows the play, who owns it, by when, and why it was chosen.

| Code | Sales play | When (drivers) | Owner · SLA |
|---|---|---|---|
| `RE_ENGAGE` | **Re-engage before the quote lapses** -- personal follow-up, confirm the requirement is still live | stalled past the typical time to close, no recent activity | Sales rep · 48 h |
| `ESCALATE` | **Bring in the territory manager** -- senior contact on a high-value, high-risk quote | high risk and value above the territory's median open quote | Territory manager · 3 days |
| `REQUOTE_SPEC` | **Re-quote with the alternative specification** -- match the configuration the customer compared us against | account's last loss: *specification alternative* | Sales rep · 5 days |
| `COMMERCIAL_OFFER` | **Targeted commercial offer within policy** -- discount tier or financing on the open quote | account's last loss: *price too high* / *budget constraints* | Sales rep + pricing desk · 3 days |
| `COMPETITIVE_RESPONSE` | **Position against the named competitor** -- comparison sheet, reference site, warranty terms | account's last loss: *chosen competitor* | Sales rep · 3 days |
| `DECISION_SUPPORT` | **Send the total-cost-of-ownership summary to the decision maker** | account's last loss: *decision delayed* / *no response* | Sales rep · 5 days |
| `CHANNEL_PARTNER` | **Engage the dealer or foodservice consultant on the account** | segment dealer / distributor / consultant; quote stalled in *Qualification* | Channel manager · 5 days |
| `ACCOUNT_REVIEW` | **Account review call; refresh the pricing agreement** -- win-back for a valuable account gone quiet | at-risk account: recency > 60 days, RFM *Can't lose them* / *At risk* | Account owner · 7 days |
| `REACTIVATE` | **Reactivation touch** -- marketing sequence for a small account gone quiet | at-risk account: RFM *Hibernating*, low monetary | Marketing · next campaign |
| `RELEASE` | **Release from the active pipeline** -- move to nurture, stop counting it as open | low value, no reply after re-engagement, past twice the typical time | Sales rep · on the run |

An **alert** carries the same drivers and no play -- it says *what*; a recommendation says
*what to do*. The demo's use case produces both: the weekly ranked report to sales, and an
alert to the rep for each high-risk quote of theirs.

### 12.3 Hand off and track

**Hand off** is a store, not a message: `retail_recommendations` on the control plane,
written by the recommend step, read by `GET /use-cases/{id}/recommendations` and
`GET /recommendations?status=open&kind=...` (contract 1.13.0). A Decision Intelligence use
case's *Read gold* becomes *Read recommendations* -- its candidates are the open ones --
and its write step sets `status` and records what was done. Until that type exists, the
screen lets a person accept or dismiss, which is the same contract. Nothing is pushed;
the poller of the consuming use case pulls, so the two types stay independently
versioned and a recommendation is never lost between them.

**Track** closes the loop on the schedule: when a recommendation's horizon ends, the run
looks up the outcome on the fact -- the quote converted, lapsed or is still open; the
account quoted again or not -- and writes it on the record (`outcome`, `outcome_at`). From
those the use case reports its own precision ("of the 40 quotes named high risk in
August, 31 did not convert") on the dashboard and in Monitor, and the retraining reads
them. That is the feedback that lets the number be trusted, and it is the same table.

### 12.4 Stockout risk (K27) -- on curated synthetic data

`silver.inv_stock_snapshot` (sku, location, on_hand, on_order, as_of), `silver.inv_demand`
(sku, location, date, quantity), `silver.inv_supplier_leadtime` (sku, supplier, lead_days).
KPIs: `days_of_cover`, `forecast_daily_demand`, `reorder_point`. Predict: exponential
smoothing per SKU (Croston for intermittent demand), lead-time demand distribution, risk =
P(demand over the lead time > on hand + on order). Recommend: reorder quantity to the
order-up-to level, from the catalogue (*reorder now*, *expedite the open PO*, *transfer
from another location*). Alert when days of cover < lead time. The same record, the same
store, the same hand off.

### 12.5 "What my data supports" carries every KPI the story needs

The demo's story is *registry → what my data supports → KPIs → score → outcome*, so before
the use case is built, the KPI screen must show, under **Ready to measure**, every KPI the
score reads -- each with a gold formula the data can run, a description a business user
reads, and its business area and function set:

| KPI | Grain | Reads | Status |
|---|---|---|---|
| Open pipeline value | overall | dc_rfq_quotes | exists |
| Average days to close | overall | dc_rfq_quotes | exists |
| Closed-won rate | overall | dc_rfq_quotes | exists |
| Typical time to close | product category | dc_rfq_quotes (90th percentile of days_to_close) | register |
| Open quotes past typical time (stalled) | overall, and by territory | dc_rfq_quotes | register |
| Stalled pipeline value | overall | dc_rfq_quotes | register |
| Quote age (days open) | per quote | dc_rfq_quotes | register (row-level feature KPI) |
| Account recency (days since last quote) | per account | dc_rfq_quotes | register |
| Account frequency (quotes, trailing 180 days) | per account | dc_rfq_quotes | register |
| Account monetary (won value, trailing 180 days) | per account | dc_rfq_quotes | register |
| Account win rate (trailing 12 months) | per account | dc_rfq_quotes | register |
| At-risk accounts (no quote in 60 days, previously active) | overall, and by segment | dc_rfq_quotes | register |
| RFM segment distribution | rfm_segment | derived | register (grain vocabulary + overlay) |

Registered through the same path a customer would use (the K12 proposal and accept flow
or `PA05`-style registration), materialised into gold, so the screen's *Ready to measure*
bucket is honest -- the deciding demo cannot rest on a KPI the platform only pretends to
have. The K12 suggester should also *propose* these from the harvest when asked "which
KPIs tell me about quote conversion risk", which is the first thing to try on the screen.

### 12.6 Not in this plan

Deep-learning forecasters; a model choosing the action (the catalogue is rules); pushing
recommendations into external systems (that is Decision Intelligence's write); account
churn without quote context on a data set where the median account has one quote.

### 12.7 Implementation phases (K26, built 20 Sep 2026 -- phases 1-6; evidence in `cookbook/94_retail/TEST_LOG.md`)

| Phase | Delivers | Proof |
|---|---|---|
| **1 · KPIs** | `PA06_middleby_at_risk_kpis.py`: the eleven at-risk KPIs registered through `POST /kpis` (sales cycle length by category; stalled quotes, by territory; stalled pipeline value; quote conversion within the usual range; 60-day churn rate; at-risk accounts, by segment; RFM segment distribution; average account recency / frequency / monetary), the `rfm_segment` grain in the Middleby overlay, gold materialised | every one under *Ready to measure* on "What my data supports", with today's value |
| **2 · Score** | `product/egpt_retail/predict/`: features per open quote as-of a date (no leakage), labels from closed history, logistic regression in numpy with L2 and calibration, holdout AUC / Brier / base rate; RFM quintile segmentation; `score_risk` executor; readiness `risk_model` (AUC floor 0.65) | unit tests on synthetic rows; the real data's holdout metrics in the TEST_LOG |
| **3 · Outcome** | the Recommendation record; `retail_recommendations` store; `recommend` executor with the next-best-action catalogue (rules over drivers and level); `GET /recommendations`, `GET /use-cases/{id}/recommendations`, `POST /recommendations/{id}/status`; contract 1.13.0 | records written by a run, read back, accepted / dismissed |
| **4 · Chain** | `at_risk` template (Inform): Read gold → Take the numbers as they are → Track what happened → Score risk → Recommend → Explain → Check grounding → Record provenance → Render report → Deliver → Send report → Send alerts; the report as a ranked list with plays; the dashboard's tiles from the at-risk KPIs and its panel the open recommendations | a rehearsal and a live run from the API |
| **5 · Form** | the fourth kind ("find who or what is at risk"): subject, horizon (usual range), churn window, KPIs read (suggested), outcome (recommend / alert / both), catalogue, delivery; readiness, plan mirror, rehearse, run view, a Recommendations screen | built end to end in the browser, alerts and report emailed |
| **6 · Track** | outcome at horizon end written on each record; precision on the dashboard and in Monitor; retraining reads outcomes | a run after the horizon labels earlier records |
| **7 · Stockout (K27)** | curated synthetic inventory tables; `days_of_cover` KPI; `forecast_demand`; the same record and store | designed here, built after the demo |
