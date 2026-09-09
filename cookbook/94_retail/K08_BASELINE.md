# K8 proposer baseline — three runs, judged by eye

*The number every later prompt change is measured against (delivery plan, K8 evidence line).
These verdicts are the author's, made on 2026-09-09 against the Middleby silver harvest with
the customer overlay loaded and `taken_terms` passed. **The baseline becomes real when the
owner confirms or overrules each verdict** — mark the last column and commit.*

Setup for all three runs: `silver` harvest with sampled values (5 silver tables, 8 colliding
column names), 30 concepts offered (27 core + 3 overlay), enrichment run for `QuoteRequest`,
`Customer`, `WebSession`. Model: the configured Azure deployment, temperature as shipped.

## Stability across runs

| Output | Run A (committed) | Run B | Run C | Stable? |
|---|---|---|---|---|
| Mappings produced | 6 | 5 | 5 | The five high/medium ones identical in all three; `crm_contact → Customer` (low) appeared once |
| Authoritative sources | QuoteRequest→crm_deal, WebSession→session_replay_session | same | same | **Yes** |
| Collisions resolved on both sides | 8/8 | 8/8 | 8/8 | **Yes**, and every `joinable` was `false` |
| `crm_deal_stage_history → QuoteRequest` confidence | low | low | medium | Wobbles, but present every time |
| Enrichment: `WebSession.business_area` | customer | customer | **operations** | **No** — ontology says `marketing` |
| Enrichment: `WebSession` `pii` tag | not proposed (reasoned) | medium | high | **No** |
| Enrichment: candidate metrics per concept | 3–5 | 3–6 | 3–7 | Names vary; the high-confidence ones (`rage_click_count`, `dead_click_count`, `script_error_count`) stable |

**Reading:** the mapping agent is steady where the evidence is strong and wobbles only on the
one low-confidence mapping. The enrichment agent is steadier on structural claims (which
column is personal data, which synonym the harvest supports) than on judgement claims (which
area, whether an identity field is `pii` or `pseudonymous_id`). That split is the right
shape, and it says two things: a golden set (G13/R15) must score the confident half
separately, and **no judgement the enrichment agent makes about business area should be
consumed** — the P3 verification removed K9's dependence on it, and the ontology's own
assignment is used instead.

## The proposals, judged

Kinds as they land in the queue after the P3 verification moved G19 resolutions onto the
column (`column_meaning`). One row per distinct proposal across the three runs; the run
column says where it appeared.

| # | Kind | Proposal | Runs | Verdict | Why | Owner |
|---|---|---|---|---|---|---|
| 1 | mapping | `silver.analytics_event → WebSession` (medium) | ABC | **right** | event stream keyed by the vendor session id | ☐ |
| 2 | mapping | `silver.crm_contact → Contact` (high) | ABC | **right** | email plus CRM identifiers | ☐ |
| 3 | mapping | `silver.crm_deal → QuoteRequest` (medium) | ABC | **right** | `quote_request_source_url` is the tell; the overlay concept is the right one | ☐ |
| 4 | mapping | `silver.session_replay_session → WebSession` (high) | ABC | **right** | one captured session per row | ☐ |
| 5 | mapping | `silver.crm_deal_stage_history → QuoteRequest` (low/medium) | ABC | **wrong** | a stage-history table is a record of transitions *about* a quote request, not an instance of one; the agent's own rationale says "secondary". Should map to nothing, or to a concept the ontology lacks (a lifecycle/state-change concept) | ☐ |
| 6 | mapping | `silver.crm_contact → Customer` (low) | A | **defensible** | a contact table can instantiate both; flagged low. Not wrong, but a reviewer should pick one authoritative concept per table eventually | ☐ |
| 7 | authoritative | `QuoteRequest ← crm_deal` over `crm_deal_stage_history` | ABC | **right** | the primary record, not its history | ☐ |
| 8 | authoritative | `WebSession ← session_replay_session` over `analytics_event` | ABC | **arguable** | replay is typically sampled; for "how many sessions" `analytics_event` is the broader source. Exactly the G20 judgement a person must make — the agent's evidence (richer per-session fields) is real, and so is the objection | ☐ |
| 9–16 | column_meaning | eight collisions, each resolved per table (`brand`, `last_modified_at`, `object_id`, `record_type`, `session_id`, `stage`, …) | ABC | **right** | every `session_id` resolution names the separate id space from the DDL comment; every `joinable` is false; no resolution restates the column name | ☐ |
| 17 | enrichment | `QuoteRequest`: synonym `RFQ` / `quote request`; patterns; `crm_contact.email` as personal data; `pii`; candidate `quote_request_count` | ABC | **right** | every item cites a column; no `sql_filter` proposed, with the reason stated (no sampled value ties `record_type` to RFQ — correct, silver is empty here) | ☐ |
| 18 | enrichment | `Customer`: synonym `buyer` (+`account` in B); patterns; `crm_contact.email`; `pii`+`pseudonymous_id`; candidate `customer_count` | ABC | **right, one item wrong** | `identity_at_login` / `identity_at_submit` as *Customer* personal-data fields is wrong — those are session identities on a replay table and belong to `WebSession`, where the agent also (correctly) lists them | ☐ |
| 19 | enrichment | `WebSession`: synonyms `session`/`visit`; patterns incl. "which sessions ended before login?"; identity fields; `pseudonymous_id`; candidates `rage_click_count` etc. | ABC | **right, area wrong in C** | the pattern derived from the column comment ("NULL when the session ended before login") is the best single proposal in the set; `business_area=operations` in run C is wrong and is no longer consumed | ☐ |
| 20 | enrichment (before fix) | `Customer`: synonym `contact` | A, pre-fix only | **wrong, fixed** | another concept's name; K3's index refuses it. Disappeared once `taken_terms` was passed | — |

## The number

**Of the 19 distinct proposals in the post-fix runs: 1 wrong (#5), 1 arguable (#8), 1 right
with one wrong item inside it (#18), and 1 right with a wrong field no longer consumed (#19).**
Counting a proposal as wrong if a reviewer would have to reject or edit it: **3 of 19**, or
2 of 19 if #8 is accepted as the agent argued it.

That is the baseline. A prompt change that moves it must say which row moved and why.

## What the owner should do with this

1. Fill the last column (`☑` confirm / `✗` overrule, with a word) for rows 5, 6, 8 and 18 —
   those are the judgement calls. The rest are mechanical and can be confirmed in bulk.
2. Decide #8 as a product question: is the authoritative source for a concept the table with
   the *richest* description of one instance, or the table with the *most complete* set of
   instances? The agent chose the former. Both are defensible; only one can be the rule.
3. If #5 is confirmed wrong, the fix is in the prompt: "a table recording *changes to* an
   entity over time is not an instance of that entity" — and that line's effect is measured
   against this row.
