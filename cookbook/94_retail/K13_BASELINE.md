# K13 baseline — use case suggestion

Three runs of `K13_use_case_suggestion.py` against the sample deployment (five `silver` tables,
two ontology concepts, four seeded KPIs), on 2026-09-10. The script asserts the guarantees; this
records the judgements it cannot make, for the owner to mark up.

## Shape distribution, three runs

| Run | Inform | Alert | Recommend | Act | Total |
|---|---|---|---|---|---|
| A | 4 | 2 | 0 | 0 | 6 |
| B | 3 | 2 | 0 | 0 | 5 |
| C | 4 | 2 | 0 | 0 | 6 |

**The alert set was identical on all three runs** — *Stalled quote requests* and *High-friction
session alert*, in that order, over the same tables. The Inform set varied by one: run B dropped
the stage-ageing report and kept the other four. Cardinality spread 1.2×, the same figure K12
measured on the same harvest, which suggests it is a property of this agent family rather than of
either prompt.

**No Recommend and no Act was proposed on any run.** This is the single most important row in
this table and it needs the owner's judgement, because it has two readings and they call for
opposite actions:

- *Correct restraint.* This harvest is five read-only silver tables. Nothing in it is a write
  target — there is no column that reaches a system a change could be written back to — and
  section 5 of the prompt tells the agent to claim the lowest shape that is true. On that
  reading the agent did exactly right, and the absence is evidence the rule works.
- *Under-claiming.* The prompt's refusal section says "unsure between two shapes, choose the
  lower one", which is a thumb on the scale. If it is heavy enough, a deployment that *can*
  write would still see only Informs, and the two shapes a customer actually pays for would
  never be suggested.

These cannot be separated on this harvest, because it genuinely has no write target. **Separating
them needs a run against a connector that does** — a CRM with a writable stage field, say — and
that is Lane C's. Filed as **K-7** in `docs/plan/open-items.md`. Until it is run, half the shape
space is exercised only by the deterministic control in section 4, never by the agent.

## Suggestions, run C

| # | Shape | Title | Reads | Ready on bare | Owner's verdict |
|---|---|---|---|---|---|
| 1 | inform | Open pipeline value snapshot | `crm_deal` | yes | |
| 2 | alert | Stalled quote requests | `crm_deal`, `crm_deal_stage_history` | no — needs `alert_threshold` | |
| 3 | inform | Stage ageing review for quote requests | `crm_deal`, `crm_deal_stage_history` | yes | |
| 4 | inform | Session frustration trend | `session_replay_session` | yes | |
| 5 | alert | High-friction session alert | `session_replay_session` | no — needs `alert_threshold` | |
| 6 | inform | Reachable contacts snapshot | `crm_contact` | yes | |

Rows 3 and 4 are worth a second look. Row 3 reads the same two tables as row 2 and differs from
it only in what happens when the number moves, which is exactly the Inform/Alert distinction the
shapes exist to make — so the pair is either a good demonstration of the axis or one use case
counted twice, and only a reviewer can say which. Row 4 is the same question for rows 4 and 5.

## What the agent refused, and why it is right

All three runs left out cross-source use cases joining web sessions to quote requests, citing
the harvest's own note that `analytics_event.session_id` is not joinable to
`session_replay_session.session_id`. It was not asked to check joinability and no rule told it
to; it read the column comment K12.5 put there and drew the conclusion. That is the harvest
comment doing its job.

All three also named the metrics they would have needed and did not have — quote request count,
conversion, dead click rate, script error rate — rather than citing a KPI that does not exist.
This is the behaviour section 8 asks for, and it doubles as a work queue for K12.

## Known incompletenesses in this baseline

1. **`shape` and `autonomy` do not reach the API.** `UseCaseSuggestion` has no field for either
   (open item **R-12**, Lane R). The assembled list says so in `notes` rather than dropping them
   silently, and `assemble` is one line from carrying them once the contract has somewhere to
   put them. Until then a reader of the API cannot tell an Alert from an Act.
2. **Recommend and Act are exercised only by a control** — see above, **K-7**.
3. **Every table in this harvest has 0 rows.** The suggestions are schema-supported, not
   evidence-backed: the agent said so itself in `notes` on run C. What this baseline shows is
   that the agent reads a schema correctly, not that these conditions ever fire. Run A did not
   volunteer the caveat and run C did, so it is not reliably stated either.
4. **The capability profiles are literals in the cookbook.** Which setting supplies a write
   target is Lane R's to define; `SHAPE_REQUIREMENTS` names capabilities, not settings keys, so
   wiring it to real configuration is a lookup and not a redesign.

## Owner's judgement

*To be filled in by Lane K's owner. The rows above that need a mark: the Recommend/Act absence,
and whether rows 2/3 and 4/5 are two use cases each or one.*


## Re-baseline, 2026-09-10 — with domain prose and data feasibility

Run D, routed through R8 with six prose passages: **inform 3, alert 2, recommend 1**. The
Recommend ("review of long-stalled quote requests") is the first write shape proposed across
every run to date; blocked on the bare deployment for approver, write target and guardrails,
as it should be. Partial evidence on K-7: the agent had lacked grounding, not only a write
target. Data feasibility: every suggestion `ready` (all named KPIs and tables present), so the
"connect X to unlock N" line had nothing to say on this harvest; the DB-free tests cover the
mostly-ready and blocked paths.
