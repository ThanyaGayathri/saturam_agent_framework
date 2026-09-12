import { KPI_DATA_CATALOG_DATASETS } from "./kpi-data-catalog";
import { CHANNELS, COMPARISONS, FIELD_META, LIMIT_OPERATORS, OPERATORS, SCHEDULES, SHAPES, TIMEOUT_BEHAVIOURS } from "./use-case-autonomy";

/**
 * The plan: how a frozen spec will actually run, as a sequence of steps a person can read and
 * approve before anything executes.
 *
 * This is the interface's mirror of the orchestration compiler's stage table
 * (product/egpt_retail/orchestration/compiler.py, `SHAPE_STAGES`): four shapes, each an ordered
 * list of stages, each stage naming the executor it runs, the parameter slots it consumes, and
 * whether a write may not run without it. The plan is DERIVED from that table and the spec --
 * never authored -- so it cannot drift from what the compiler will build. Approve the plan and you
 * have approved a mechanism, not a wish.
 *
 * Every stage carries who performs it, because that is the question governance asks:
 *
 *   system  -- deterministic. An executor over the gold layer, a threshold, a rule. Same input,
 *              same output, and it can be replayed.
 *   model   -- a language model. Produces text and proposals. It never decides whether an action
 *              is allowed; that is always a system step that follows it.
 *   person  -- a human gate. The step waits.
 *
 * The rule the table makes visible: every branch is a deterministic step, never a model. The
 * model explains and proposes; it is not the thing that says yes.
 */

export const STAGE_KIND = { executor: "executor", model: "model", branch: "branch", gate: "gate" };

export const STEP_KIND = {
 trigger: "trigger",
 read: "read",
 compute: "compute",
 check: "check",
 model: "model",
 gate: "gate",
 write: "write",
 notify: "notify",
 record: "record",
};

/** Slots consumed by the trigger rather than by a stage. */
export const TRIGGER_SLOTS = new Set(["schedule"]);

/** What "compute metrics" means once there is a gold layer: R5 materialises every ready KPI into
 *  gold.kpi_value nightly, so by the time a use case runs the metric is already computed and
 *  read_gold returns the value. The stage stays in the table, marked as satisfied, rather than
 *  being quietly dropped. */
const COMPUTE_SATISFIED = "R5 materialises every ready KPI into gold nightly, so read_gold already returns the computed value.";

const stage = (name, kind, executorRef, extra = {}) => ({ name, kind, executorRef, consumes: [], guardsWrite: false, ...extra });
const computeSatisfied = (name, consumes = []) => stage(name, STAGE_KIND.executor, "compute_metrics", { satisfiedBy: "read_gold", satisfiedBecause: COMPUTE_SATISFIED, consumes });
const propose = () => stage("Propose", STAGE_KIND.model, null, { role: "proposal", description: "One model step. Proposes changes; the limits below decide which stand." });

/** `SHAPE_STAGES`, verbatim from the compiler, keyed by shape id. Slot names are the backend's. */
export const SHAPE_STAGES = {
 inform: [
  stage("Read gold", STAGE_KIND.executor, "read_gold", { consumes: ["metrics"] }),
  computeSatisfied("Compute metrics", ["dimensions"]),
  stage("Compare to baseline", STAGE_KIND.executor, "compare_to_baseline", { consumes: ["comparison"] }),
  stage("Explain", STAGE_KIND.model, null, { role: "explanation", description: "One model step. Explains the numbers; never decides anything." }),
  stage("Record provenance", STAGE_KIND.executor, "record_provenance"),
  stage("Deliver", STAGE_KIND.executor, "deliver_in_app", { consumes: ["recipient"] }),
 ],
 alert: [
  stage("Read gold", STAGE_KIND.executor, "read_gold", { consumes: ["metric"] }),
  computeSatisfied("Compute metric", ["scope"]),
  stage("Evaluate condition", STAGE_KIND.branch, "evaluate_alert_condition", {
   consumes: ["operator", "threshold"],
   description: "Deterministic. Compares the metric to the threshold and decides whether the steps after it run. Logs the outcome either way -- an alert that does not fire is still logged, so a quiet week is visibly quiet.",
  }),
  stage("Write the alert", STAGE_KIND.executor, "write_alert", { consumes: ["severity"], description: "Only reached when the condition fired." }),
  stage("Record provenance", STAGE_KIND.executor, "record_provenance"),
  stage("Notify", STAGE_KIND.executor, "deliver_in_app", { consumes: ["recipient"] }),
 ],
 recommend: [
  stage("Read gold", STAGE_KIND.executor, "read_gold", { consumes: ["metrics"] }),
  stage("Select candidates", STAGE_KIND.executor, "select_candidates", { consumes: ["candidates"] }),
  computeSatisfied("Compute metrics"),
  propose(),
  stage("Apply hard limits", STAGE_KIND.executor, "apply_hard_limits", { consumes: ["guardrails"], guardsWrite: true }),
  stage("Approver decides", STAGE_KIND.gate, "await_approval", { consumes: ["approver", "timeout behaviour"], guardsWrite: true, description: "A person decides. The run stops here until they do." }),
  stage("Write to target", STAGE_KIND.executor, "write_to_target", { consumes: ["write target"] }),
  stage("Record provenance", STAGE_KIND.executor, "record_provenance"),
  stage("Notify owner", STAGE_KIND.executor, "deliver_in_app"),
 ],
 act: [
  stage("Read gold", STAGE_KIND.executor, "read_gold", { consumes: ["metrics"] }),
  stage("Select candidates", STAGE_KIND.executor, "select_candidates", { consumes: ["candidates"] }),
  computeSatisfied("Compute metrics"),
  propose(),
  stage("Apply hard limits", STAGE_KIND.executor, "apply_hard_limits", { consumes: ["guardrails"], guardsWrite: true }),
  stage("Apply per-run cap", STAGE_KIND.executor, "apply_per_run_cap", { consumes: ["per-run cap"], guardsWrite: true }),
  stage("Write to target", STAGE_KIND.executor, "write_to_target", { consumes: ["write target"] }),
  stage("Record rollback point", STAGE_KIND.executor, "record_rollback_point", { consumes: ["rollback"], guardsWrite: true }),
  stage("Record provenance", STAGE_KIND.executor, "record_provenance"),
  stage("Notify owner", STAGE_KIND.executor, "deliver_in_app"),
 ],
};

/** The model tier each model-step role runs on (`TIER_FOR_ROLE`). */
export const TIER_FOR_ROLE = { explanation: "small", proposal: "frontier" };

/** The tools each role's agent may call inside its step -- the agentic interior of a declared
 *  skeleton (`skills/step_prompt.py`, `ROLE_TEMPLATES[...].tools`). A closed set: an agent that
 *  could pick its own tools would pick a writer. */
export const ROLE_TOOLS = {
 explanation: ["semantic_read", "sql_through_guard"],
 proposal: ["semantic_read", "sql_through_guard", "domain_prose"],
};

/** Datasets the spec's metrics resolve to, grouped by source, for the "reads" step. */
export function datasetsForSpec(spec) {
 const bySource = new Map();
 for (const ds of KPI_DATA_CATALOG_DATASETS) {
  const hits = (ds.linkedKpiNames || []).filter((n) => spec.metricIds.includes(n));
  if (!hits.length) continue;
  if (!bySource.has(ds.sourceLabel)) bySource.set(ds.sourceLabel, []);
  bySource.get(ds.sourceLabel).push({ name: ds.technicalName, metrics: hits, stale: ds.status === "stale" });
 }
 return [...bySource.entries()].map(([source, datasets]) => ({ source, datasets }));
}

const scheduleLabel = (v) => SCHEDULES.find((s) => s.value === v)?.label || "Every morning";
const comparisonLabel = (v) => COMPARISONS.find((c) => c.value === v)?.label?.toLowerCase() || "the period before";
const opLabel = (op) => OPERATORS.find((o) => o.value === op)?.label || "goes above";
const limitLabel = (op) => LIMIT_OPERATORS.find((o) => o.value === op)?.label || op;
const timeoutLabel = (v) => TIMEOUT_BEHAVIOURS.find((t) => t.value === v)?.label?.toLowerCase() || "do nothing";

/** The spec value a backend slot resolves to, as text, for the "uses" line under each step. */
export function slotValue(spec, slot) {
 const field = Object.keys(FIELD_META).find((f) => FIELD_META[f].slot === slot);
 if (!field) return null;
 switch (field) {
  case "metrics": return spec.metricIds.join(", ") || null;
  case "metric": return spec.metricIds[0] || null;
  case "dimensions": return spec.dimensions.join(", ") || null;
  case "comparison": return spec.comparison ? comparisonLabel(spec.comparison) : null;
  case "operator": return spec.condition?.operator ? opLabel(spec.condition.operator) : null;
  case "threshold": return spec.condition?.threshold != null ? `${spec.condition.threshold}${spec.condition.unit || ""}` : null;
  case "scope": return spec.scope || null;
  case "severity": return spec.severity || null;
  case "recipient": return spec.notify ? `${spec.notify} · ${(spec.notifyChannels || ["in_app"]).map((c) => CHANNELS.find((x) => x.value === c)?.label || c).join(", ")}` : null;
  case "candidates": return spec.candidates?.concept ? `${spec.candidates.concept}${spec.candidates.filter ? ` where ${spec.candidates.filter}` : ""}` : null;
  case "guardrails": return spec.guardrails.length ? spec.guardrails.map((g) => g.rule || `${g.metric} ${limitLabel(g.operator)} ${g.threshold}${g.unit || ""}`).join(" · ") : null;
  case "approver": return spec.approver || null;
  case "writeTarget": return spec.writeback || null;
  case "timeoutBehaviour": return timeoutLabel(spec.onTimeout);
  case "perRunCap": return spec.blastRadius || null;
  case "rollback": return spec.rollback || null;
  case "schedule": return scheduleLabel(spec.schedule);
  default: return null;
 }
}

const STEP_KIND_FOR_EXECUTOR = {
 read_gold: STEP_KIND.read,
 compute_metrics: STEP_KIND.compute,
 compare_to_baseline: STEP_KIND.compute,
 select_candidates: STEP_KIND.check,
 evaluate_alert_condition: STEP_KIND.check,
 write_alert: STEP_KIND.write,
 apply_hard_limits: STEP_KIND.check,
 apply_per_run_cap: STEP_KIND.check,
 await_approval: STEP_KIND.gate,
 write_to_target: STEP_KIND.write,
 record_rollback_point: STEP_KIND.record,
 record_provenance: STEP_KIND.record,
 deliver_in_app: STEP_KIND.notify,
};

/** Human wording per executor, from the spec. The stage table says WHAT runs; this says what it
 *  means for this spec. Nothing here changes the order or the set of steps. */
function describe(st, spec, reads) {
 const sources = reads.map((r) => r.source);
 const metrics = spec.metricIds;
 const n = metrics.length;
 switch (st.executorRef || st.role) {
  case "read_gold":
   return {
    title: `Reads ${n} KPI${n === 1 ? "" : "s"}`,
    detail: `${metrics.join(", ") || "The KPIs"} — each by its registry definition, already computed${sources.length ? ` from ${sources.join(" and ")}` : ""}. Read-only.`,
    chips: metrics, reads,
   };
  case "compute_metrics":
   return {
    title: st.name,
    detail: spec.shape === "alert"
     ? `Evaluated ${spec.scope ? `per ${spec.scope.replace(/^any /i, "").toLowerCase()}` : "overall"}. ${st.satisfiedBecause}`
     : `${spec.dimensions.length ? `Broken down by ${spec.dimensions.join(", ").toLowerCase()}. ` : ""}${st.satisfiedBecause}`,
   };
  case "compare_to_baseline":
   return {
    title: "Compares to baseline",
    detail: `Against ${comparisonLabel(spec.comparison)}. Ranks which ${spec.dimensions[0]?.toLowerCase() || "segment"} moved most. All arithmetic — nothing here is judged.`,
   };
  case "explanation":
   return {
    title: "Explains",
    detail: `Writes the narrative: what moved, where, and the likeliest drivers. Every number it cites comes from the step above; it cannot introduce one. Runs on the ${TIER_FOR_ROLE.explanation} tier.`,
    chips: ROLE_TOOLS.explanation.map((t) => `may call ${t}`),
   };
  case "proposal":
   return {
    title: "Recommends",
    detail: `One recommendation per record in scope, with the reasoning and the numbers behind it${spec.objective?.metric ? ` — each chosen to move ${spec.objective.metric} ${spec.objective.direction} ${spec.objective.target}${spec.objective.unit || ""}` : ""}. It recommends; it cannot write. What it may recommend is bounded by the next step. Runs on the ${TIER_FOR_ROLE.proposal} tier.`,
    chips: [...(spec.objective?.metric ? [`objective: ${spec.objective.metric} ${spec.objective.direction} ${spec.objective.target}${spec.objective.unit || ""}`] : []), ...ROLE_TOOLS.proposal.map((t) => `may call ${t}`)],
   };
  case "evaluate_alert_condition":
   return {
    title: "Evaluates the condition",
    detail: `Fires when ${metrics[0] || "the metric"} ${opLabel(spec.condition?.operator)} ${spec.condition?.threshold ?? "the threshold"}${spec.condition?.threshold != null ? spec.condition.unit || "" : ""}${spec.scope ? `, in ${spec.scope.toLowerCase()}` : ""}. A threshold, not an opinion. Logged either way.`,
    branch: { yes: "fires", no: "doesn't fire", outcome: "Run logged, nothing sent" },
   };
  case "write_alert":
   return {
    title: "Writes the alert",
    detail: `An alert record at severity ${spec.severity || "warning"}: what breached, where, by how much. Drawn from the computed numbers — no model in the Alert shape.`,
    conditional: "only when it fires",
   };
  case "select_candidates":
   return {
    title: "Selects candidates",
    detail: spec.candidates?.concept
     ? `${spec.candidates.concept}${spec.candidates.filter ? ` where ${spec.candidates.filter}` : ""}. A deterministic filter over the semantic layer, so the same records are considered on every run.`
     : "A deterministic filter, so the same records are considered on every run.",
   };
  case "apply_hard_limits":
   return {
    title: `Applies ${spec.guardrails.length} business guardrail${spec.guardrails.length === 1 ? "" : "s"}`,
    detail: "Each recommendation is tested against every guardrail. Any that fail are struck and logged with the guardrail that struck them. The AI step never sees this step and cannot argue with it.",
    chips: spec.guardrails.map((g) => g.rule || `${g.metric} ${limitLabel(g.operator)} ${g.threshold}${g.unit || ""}`),
    branch: { yes: "passes", no: "breaks a guardrail", outcome: "Struck, logged with the guardrail" },
   };
  case "await_approval":
   return {
    title: `${spec.approver || "The approver"} decides`,
    detail: `Sees each surviving recommendation with its reasoning and can approve, reject or edit. Nothing below this line happens without that. If no response: ${timeoutLabel(spec.onTimeout)}.`,
    branch: { yes: "approved", no: "rejected or no answer", outcome: "Dropped, logged with who and why" },
   };
  case "apply_per_run_cap":
   return {
    title: `Applies the change limit: ${spec.blastRadius || "not set"} per run`,
    detail: "Changes beyond the limit are held for the next run, never dropped silently. The limit stands where the approver would: the most it can get wrong in one go.",
    branch: { yes: "within limit", no: "beyond limit", outcome: "Held for the next run" },
   };
  case "write_to_target":
   return {
    title: `Updates ${spec.writeback || "the target system"}`,
    detail: `Through the ${spec.writeback || "target"} connector — a connected system with write permission. Each change is stamped with the run, the recommendation and ${spec.shape === "recommend" ? "who approved it" : "the change limit that admitted it"}.`,
   };
  case "record_rollback_point":
   return {
    title: "Records the rollback point",
    detail: `${spec.rollback || "The rollback"} — the prior value is kept per write, so any run can be reversed as a unit. The owner and any admin hold the kill switch.`,
   };
  case "record_provenance":
   return {
    title: "Records provenance",
    detail: `What was read, ${spec.shape === "inform" ? "computed and said" : spec.shape === "alert" ? "evaluated and sent" : "considered, struck, written, and why"}. The audit trail; it is not optional.`,
   };
  case "deliver_in_app":
   return {
    title: spec.shape === "alert" ? `Tells ${spec.notify || "someone"}` : spec.shape === "inform" ? `Delivers to ${spec.notify || "the recipient"}` : `Tells ${spec.owner || "the owner"}`,
    detail: spec.shape === "alert"
     ? `The alert record lands in the app${(spec.notifyChannels || []).filter((c) => c !== "in_app").length ? `; ${(spec.notifyChannels || []).filter((c) => c !== "in_app").map((c) => CHANNELS.find((x) => x.value === c)?.label || c).join(" and ")} ${(spec.notifyChannels || []).filter((c) => c !== "in_app").length > 1 ? "get" : "gets"} a link to it, never the alert itself, so provenance stays attached` : ""}. Severity ${spec.severity || "warning"}. Silent runs are still logged, so a quiet month is visibly quiet rather than possibly broken.`
     : spec.shape === "inform"
      ? "The run, its inputs and the narrative are kept together."
      : "What was written, what was struck, and what was held. In app; other channels at activation.",
    conditional: spec.shape === "alert" ? "only when it fires" : undefined,
   };
  default:
   return { title: st.name, detail: st.description || "" };
 }
}

export function planFor(spec) {
 const shape = SHAPES[spec.shape] || SHAPES.inform;
 const level = shape;
 const reads = datasetsForSpec(spec);
 const sources = reads.map((r) => r.source);
 const stages = SHAPE_STAGES[shape.id];
 const steps = [];

 steps.push({
  id: "trigger", kind: STEP_KIND.trigger, who: "system", title: "Runs",
  detail: `${scheduleLabel(spec.schedule)}${spec.schedule === "manual" ? "" : ", unattended"}. The trigger consumes the schedule; no stage reads it. Each run is a versioned execution of this frozen spec.`,
  consumes: [{ slot: "schedule", value: slotValue(spec, "schedule") }],
 });

 stages.forEach((st, i) => {
  const who = st.kind === STAGE_KIND.model ? "model" : st.kind === STAGE_KIND.gate ? "person" : "system";
  const words = describe(st, spec, reads);
  steps.push({
   id: `s${i + 1}`,
   kind: st.kind === STAGE_KIND.model ? STEP_KIND.model : STEP_KIND_FOR_EXECUTOR[st.executorRef] || STEP_KIND.check,
   who,
   stage: st.name,
   executorRef: st.executorRef,
   role: st.role,
   satisfiedBy: st.satisfiedBy || null,
   guardsWrite: st.guardsWrite,
   consumes: st.consumes.map((slot) => ({ slot, value: slotValue(spec, slot) })),
   ...words,
  });
 });

 const counts = { system: 0, model: 0, person: 0 };
 for (const s of steps) if (!s.satisfiedBy) counts[s.who] += 1;
 return { shape, level, stages, steps, sources, counts, reads };
}
