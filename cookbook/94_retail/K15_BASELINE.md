# K15 baseline — orchestration

Run of `K15_orchestration.py` against the registry as it stands on 2026-09-10, 15 executors.
The script asserts the guarantees; this records the judgements it cannot make.

## What the deployment can build, per shape

| Shape | Stages buildable | Model steps | Still needs |
|---|---|---|---|
| Inform | 4 of 6 | 1 | `compare_to_baseline` |
| Alert | 3 of 6 | 0 | `evaluate_alert_condition`, `write_alert` |
| Recommend | 4 of 9 | 1 | `select_candidates`, `apply_hard_limits`, `await_approval`, `write_to_target` |
| Act | 4 of 10 | 1 | `select_candidates`, `apply_hard_limits`, `apply_per_run_cap`, `write_to_target`, `record_rollback_point` |

Every row is a set difference between build-plan §07's sequence and what `build_registry()`
resolves. **Nothing is publishable today in any shape**, and that is the correct answer rather
than a defect: Lane R has landed R1–R9, and the executors the write shapes need are R10's and
later. The value of this table now is that it is the exact list, in dependency order, of what
has to exist before a generated use case can run.

Two entries deserve reading closely:

- **`compute_metrics` is not on any of those lists.** R5 materialises every ready KPI into
  `gold.kpi_value` nightly, so the metric is computed before a use case runs and `read_gold`
  returns the value. The plan's separate compute stage predates the gold layer. Recorded as
  `satisfied_elsewhere` rather than deleted, so a reviewer can overturn the call.
- **`await_approval` is the one whose absence refuses the whole compilation.** A Recommend
  missing any other executor still emits its buildable steps for inspection; missing its gate,
  it emits nothing at all, because every step after the gate would otherwise run with the
  approver compiled out.

## Generated vs R9's hand-built Inform skeleton

The plan's evidence line is a diff against R10's hand-built RFQ workflow. R10 has not landed,
so this diffs against R9's `inform_skeleton` in `R09_plan_view.py` — five steps written by
Lane R from the same §07 table, independently. Three differences, and the plan allows them
where *"every difference is explainable and one of the two is wrong"*:

| Difference | Reading | Owner's verdict |
|---|---|---|
| Hand-built starts with `check_preflight`; generated does not | §07 lists Preflight as one of the **seven stages**, not as a step of the Inform **sequence**. Both readings are defensible and they cannot both be right — either the sequence table is short a step, or the skeleton includes a stage that belongs outside the workflow | |
| Generated model step is `weekly_pipeline__explanation`; hand-built is `funnel_explanation` | Per-use-case vs shared. The contract says a use case is *"a stored workflow component, the skill prompts its agent steps carry, and a spec record"* — prompts per use case, which needs an id per use case. R9's fixed id is what makes it routable today. Same tension as K-9 | |
| Neither carries `compare_to_baseline` | The skeleton omits it silently; the generated one reports it missing. The Inform shape binds a `comparison` parameter from `spec.time_window` and **no step consumes it** — a use case saying "versus the previous four weeks" compiles to a workflow that never compares | |

The third is the finding. It is not a difference of opinion: a declared parameter with no
consumer is a gap in the shape, and it reaches a customer as a report whose headline promises
a comparison it does not contain.

## The shape is the template

Two Alert use cases — `stalled_quotes` and `friction_spike` — compile to **the same sequence**
and differ only in what fills its slots:

| Slot | stalled_quotes | friction_spike |
|---|---|---|
| metric | `k13_deal__stage_ageing_days` | `k13_session__rage_click_rate` |
| operator | `>=` | `>=` |
| **threshold** | 7 | 0.15 |
| scope | overall | overall |
| **severity** | high | medium |
| recipient | sales@example.com | sales@example.com |
| schedule | `0 7 * * 1` | `0 7 * * 1` |

Three slots differ. Everything else — the sequence, the slot list, the gate placement — is the
Alert template, and it was written once. That is the build plan's tractability argument holding
in practice: *"a new use case is not a new flow, it is one of four shapes with its slots
filled."*

**The same discipline applies to the prompts**, and this is where the first cut was wrong. Two
Inform use cases both carry an `explanation` step doing the same job, so the invariant half —
identity, inputs, phases, rules, refusals — is a **role template** in `ROLE_TEMPLATES`, written
once by hand. The model supplies only what this use case changes: what its metrics mean, any
rule arising from them, and a worked example using them. Authoring the whole prompt per use case
would have produced prompts differing in wording and not in meaning, which is the opposite of
reviewable. `test_two_use_cases_of_one_role_share_everything_but_their_grounding` holds the line.

**Parameter origins.** Each bound value records whether it came from the spec (derived from the
conversation) or was authored (typed on the shape's form). On an Act, a per-run cap a model
inferred from a sentence and one a person set deliberately are not the same evidence, and the
activation screen has nothing else to tell them apart.

Whether an Act should *require* explicit acknowledgement of a spec-derived `per-run cap` and
`rollback` before it can be switched on is a product decision, not mine. Flagged because the
data to enforce it now exists.

## Known incompletenesses

1. **The R10 diff and the second use case are not done** — R10 has not landed. This is K15's
   stated primary evidence and it is outstanding, not substituted for. Filed as **K-10**.
2. **A generated model step cannot be planned** — R8's `ROUTING_TABLE` is a static literal
   keyed by agent id, and K15 mints an id per use case, so R9's walker refuses the step. The
   compiler names the tier each step needs, so closing it is a lookup. **K-9**.
3. **R9's `AutonomyShape` has three shapes and infers them from autonomy plus level.** K15
   uses the four-shape `UseCaseShape` and takes the shape from the spec. The two must be
   reconciled or the plan screen and the compiled workflow will disagree about Alert. **K-8**.
4. **The capability profile in `SHAPE_STAGES` names executors that do not exist yet.** Those
   names are my guesses at what Lane R will call them; if they land under other names, the
   table needs one edit each. Deliberate — naming them is what makes the gap list concrete.
5. **A generated use case has no golden questions.** K14's checklist requires a set per skill
   so the next edit is measurable, and R15's golden sets assume a person authored the use
   case. Nothing produces them for a generated one, so a generated Inform ships with a prompt
   nobody has scored and no way to detect a regression when its role template is edited.
   Raised in the architecture review — *"generation makes creating use cases cheap; it does
   not make knowing whether they are right any cheaper"* — and filed as **K-12**.
6. **`product/` ships no starter use cases**, so `template_id` on a K13 suggestion is always
   null. That is the catalog sense of template, unrelated to the shape sense above. **K-11**.

## Owner's judgement

*To be filled in by Lane K's owner. The rows above that need a mark: the three diff readings,
and whether an Act should require explicit acknowledgement of a templated per-run cap.*
