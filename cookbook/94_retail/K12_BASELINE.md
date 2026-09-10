# K12 KPI suggestion — baseline

*The number every later prompt change is measured against (delivery plan, K12 evidence line).
Recorded 10 Sep 2026 against the Middleby silver harvest, prompt `kpi_suggestion` v1.0.0.
**Verdicts below are the author's** — the baseline becomes real when the owner marks the last
column.*

## What the machinery guarantees, and what it does not

Two of the plan's three evidence lines are **asserted by the cookbook and cannot drift**:

- *No suggestion carries a feasibility the executor disagrees with.* The draft schema has no
  feasibility field and forbids extra keys, so the agent cannot express a verdict; `assemble`
  calls `assess_feasibility` and stamps it. Measured: **0 disagreements** across every run.
- *Traceable to named tables.* `derived_from_tables` is required and non-empty, and the draft
  is built into a `KpiDefinition`, which refuses a formula reading a table it did not declare.

The third — *"two runs propose substantially the same set"* — is a **judgement**, so it is
measured and recorded here rather than gated on a threshold I would have had to invent.

## Stability, three runs on identical input

| Measure | Value | Reading |
|---|---|---|
| Identifier core (in all three runs) | **5** | `quoterequest__count`, `websession__count`, `websession__dead_clicks`, `websession__rage_clicks`, `websession__script_errors` |
| Core / union of identifiers | **71%** | Seven distinct identifiers were proposed; five appeared every time |
| Same identifier, different grain or shape | **0** | Nothing is two numbers wearing one name |
| Business areas per run | identical | Derived from the ontology, so stable by construction |
| Cardinality | 5, 6, 6 (**1.2×**) | The open weakness — see below |

**Why three runs and not two.** Pairwise overlap between two runs measured 67%, 100% and 40%
on three consecutive pairs. A threshold on that would have been a coin toss dressed as a gate.
Three runs give a stable core, which is a claim that survives resampling.

**The open weakness: how finely to slice.** The agent agrees on *what* the data supports and
disagrees on how many metrics to carve out of it — `rage_clicks`, `rage_clicks_per_session`
and `sessions_with_rage_clicks` are three readings of one column, and different runs propose
different subsets. Cardinality is 1.2× here, and was 3.5× before the prompt was tightened. This
is the first thing to attack in v1.1.0, and this row is what will show whether the attack worked.

## The suggestions, judged

From the recorded run. Verdicts are the author's; the owner's column is empty on purpose.

| # | Suggestion | Verdict | Why | Owner |
|---|---|---|---|---|
| 1 | `quoterequest__count` — quote requests received, from `crm_deal` | **right** | `quote_request_source_url` marks the row; `deal_id` counts it. The single most obviously useful metric in this harvest | ☐ |
| 2 | `websession__count` — sessions, from `session_replay_session` | **right** | `session_id` is the key of that table | ☐ |
| 3 | `websession__rage_clicks` | **right** | `rage_click_count` is a session-grain column; the metric is its aggregate | ☐ |
| 4 | `websession__dead_clicks` | **right** | as above, `dead_click_count` | ☐ |
| 5 | `websession__script_errors` | **right** | as above, `script_error_count` | ☐ |
| — | *variants proposed on some runs only* (`..._per_session`, `sessions_with_...`) | **defensible** | Legitimate metrics, but the same column read three ways. A reviewer accepting all three gets three tiles that move together | ☐ |

**0 of 5 cite the curated catalogue.** Not a defect: the agent said why, in its own notes —
*"No curated catalogue metric could be cleanly bound to the available columns... without
inventing unsupported quantities such as orders, revenue, footfall, transactions."* That is
correct about this harvest, which has no orders and no revenue. It is worth re-checking on a
richer harvest, because the catalogue is the guard against invention and it is contributing
nothing here.

**The agent refused a cross-source metric, unprompted and correctly.** From its notes:
*"I did not propose metrics that would require joining `silver.analytics_event.session_id` to
`silver.session_replay_session.session_id` because the harvest explicitly says those id spaces
are not joinable."* It read the column comment K4 harvested and honoured G19 without being
told to. That is the strongest single signal in this baseline.

## What the owner should do

1. Mark the last column. Row 6 (the variants) is the judgement call.
2. Decide the slicing rule: should `rage_clicks` and `rage_clicks_per_session` both be
   proposable, or should the prompt pick one convention? That answer is what v1.1.0 implements
   and this baseline measures.


## Re-baseline, 2026-09-10 — routed through R8, with domain prose and warehouse samples

Same harvest, the agent now built by `build_suggestion_agents` (frontier tier via R8, no pinned
model), fed six K10 prose passages and example values read transiently from the warehouse.

| Run | Suggestions | Buckets |
|---|---|---|
| A | 14 | 5 |
| B | 10 | 4 |
| C | 21 | 5 |

Stable core **5** — `quoterequest__count`, `websession__count`, `websession__dead_clicks`,
`websession__rage_clicks`, `websession__script_errors` — one swap against the first baseline
(`websession__count` in, `websession__rage_click_rate` out). Cardinality spread 2.1×; core over
union 15%, down from 71%, because run C sliced the session-replay counts into rates and
per-session rates as well. Coverage is stable; slicing is not — the same weakness the first
baseline named.

**The finding that matters:** run without example values — the graph alone — the three runs
were 2 / 10 / 18 with an **empty** core. The harvest text names types; the samples say what a
column holds, and that is what anchors the agent. The graph must never hold them (rows stay in
the warehouse); the pass reads them from the warehouse for the prompt and nothing else.

Adoptable as KPI proposals: 13 of 14 (one without a formula). Citing the catalogue: 1 of 14 —
the review's badge would show thirteen "inferred" rows, which is itself worth the owner's eye.
