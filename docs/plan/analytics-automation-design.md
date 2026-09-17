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
