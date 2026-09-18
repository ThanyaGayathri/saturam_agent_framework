import { KPI_DATA_CATALOG_DATASETS } from "./kpi-data-catalog";
import { findTemplate } from "./extraction-templates";
import { findMapping } from "./record-mappings";
import { resolutionPlan } from "./use-case-roles";
import { PARAM_META, normalizeRules } from "./guardrail-rules";
import { CHANNELS, COMPARISONS, CREATED_AS, DOCUMENT_TYPES, FIELD_META, LIMIT_OPERATORS, MASTER_DATA_ROLES, OPERATORS, POLL_INTERVALS, SCHEDULES, SHAPES, TIMEOUT_BEHAVIOURS } from "./use-case-autonomy";

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
  stage("Compare to baseline", STAGE_KIND.executor, "compare_to_baseline", { consumes: ["comparison", "window"], description: "Each KPI at the run's date against gold at the baseline date -- the period before, or the same period last year -- per KPI and per breakdown row. A baseline date gold does not hold yet is materialised for it." }),
  stage("Explain", STAGE_KIND.model, null, { role: "explanation", description: "One model step. Explains the numbers; never decides anything." }),
  stage("Check grounding", STAGE_KIND.executor, "check_report_grounding", { description: "Every figure in the explanation must be one the comparison carried; an explanation that invents one is withheld and the report carries the numbers alone." }),
  stage("Record provenance", STAGE_KIND.executor, "record_provenance"),
  stage("Render report", STAGE_KIND.executor, "render_report", { consumes: ["title", "outputs"], description: "The comparison and the narrative as an HTML report and its text twin." }),
  stage("Deliver", STAGE_KIND.executor, "deliver_in_app", { consumes: ["recipient"] }),
  stage("Send report", STAGE_KIND.executor, "send_report", { consumes: ["recipient"], description: "The HTML report by email to the recipients; held in a rehearsal." }),
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

/**
 * The document-to-record template: a fifth sequence next to the four KPI-driven shapes. Every
 * stage is a generic verb; the use case is the slot values. One AI step (extraction); three
 * automated steps protect the write. Per document, not per run.
 */
export const TEMPLATE_STAGES = {
 // The diagnostic kind (K25): the Inform sequence with the decomposition before the model.
 metric_diagnosis: [
  stage("Read gold", STAGE_KIND.executor, "read_gold", { consumes: ["metrics"] }),
  computeSatisfied("Compute metrics", []),
  stage("Compare to baseline", STAGE_KIND.executor, "compare_to_baseline", { consumes: ["comparison", "window"], description: "The KPI at the run's date against gold at the baseline date. A baseline date gold does not hold yet is materialised for it." }),
  stage("Decompose the change", STAGE_KIND.executor, "decompose_change", { consumes: ["metrics", "comparison", "window", "dimensions", "sensitivity", "evidence columns"], description: "Where the change sits, by arithmetic: each slice of each dimension gets its contribution (rate and mix effects for a rate), its surprise against its own history, and a support floor; dimensions are ranked, the winning slice searched one level down, and the rows behind it fetched. The model that follows narrates this ranking; it never picks the cause." }),
  stage("Explain", STAGE_KIND.model, null, { role: "explanation", description: "One model step. Writes the sentence from the ranked drivers and the evidence; every figure is one the arithmetic produced." }),
  stage("Check grounding", STAGE_KIND.executor, "check_report_grounding", { description: "Every figure in the explanation must be one the decomposition carried; an explanation that invents one is withheld and the report carries the numbers alone." }),
  stage("Record provenance", STAGE_KIND.executor, "record_provenance"),
  stage("Render report", STAGE_KIND.executor, "render_report", { consumes: ["title", "outputs"], description: "The sentence, the ranked drivers, the evidence and the numbers as an HTML report and its text twin." }),
  stage("Deliver", STAGE_KIND.executor, "deliver_in_app", { consumes: ["recipient"] }),
  stage("Send report", STAGE_KIND.executor, "send_report", { consumes: ["recipient"], description: "The HTML report by email to the recipients; held in a rehearsal." }),
 ],
 document_to_record: [
  stage("Receive", STAGE_KIND.executor, "receive_messages", { consumes: ["source mailbox", "message filter"], description: "New messages matching the filter. None → run logged, nothing else." }),
  stage("Screen", STAGE_KIND.branch, "screen_documents", { consumes: ["document type"], description: "One document of the right type, from a known sender, under the size limit. Fails → exception." }),
  stage("Extract", STAGE_KIND.model, null, { role: "extraction", consumes: ["extraction template", "example document"], description: "One AI step. Fills the template's schema from the document; a field not on the document is null. Output validated against the schema." }),
  stage("Resolve", STAGE_KIND.branch, "resolve_entities", { consumes: ["master data", "match thresholds"], description: "Scored matching against master data, in a fixed order of attempts. The AI step may only choose between candidates already above threshold. Not found or ambiguous → exception." }),
  stage("Apply guardrails", STAGE_KIND.branch, "apply_hard_limits", { consumes: ["guardrails"], guardsWrite: true, description: "Record-level checks. Any failure → exception; nothing is written." }),
  stage("Build record", STAGE_KIND.executor, "build_record", { consumes: ["target objects", "created as"], description: "Maps the resolved fields onto the target objects. A required field it cannot fill → exception." }),
  stage("Apply change limit", STAGE_KIND.branch, "apply_per_run_cap", { consumes: ["per-run cap"], guardsWrite: true }),
  stage("Write", STAGE_KIND.executor, "write_to_target", { consumes: ["write target"] }),
  stage("Record rollback point", STAGE_KIND.executor, "record_rollback_point", { consumes: ["rollback"], guardsWrite: true }),
  stage("Notify", STAGE_KIND.executor, "send_email", { consumes: ["recipient", "acknowledge"], description: "The sender is acknowledged with the record number or the exception reason; exceptions go to the configured address." }),
  stage("Record provenance", STAGE_KIND.executor, "record_provenance"),
 ],
};

/** The model tier each model-step role runs on (`TIER_FOR_ROLE`). */
export const TIER_FOR_ROLE = { explanation: "small", proposal: "frontier", extraction: "frontier" };

/** The tools each role's agent may call inside its step -- the agentic interior of a declared
 *  skeleton (`skills/step_prompt.py`, `ROLE_TEMPLATES[...].tools`). A closed set: an agent that
 *  could pick its own tools would pick a writer. */
export const ROLE_TOOLS = {
 explanation: ["semantic_read", "sql_through_guard"],
 proposal: ["semantic_read", "sql_through_guard", "domain_prose"],
 extraction: ["document_read"],
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
const comparisonLabel = (v) => (v ? COMPARISONS.find((c) => c.value === v)?.label?.toLowerCase() || "the period before" : "nothing — the numbers as they are");
const opLabel = (op) => OPERATORS.find((o) => o.value === op)?.label || "goes above";
const limitLabel = (op) => LIMIT_OPERATORS.find((o) => o.value === op)?.label || op;
const timeoutLabel = (v) => TIMEOUT_BEHAVIOURS.find((t) => t.value === v)?.label?.toLowerCase() || "do nothing";

/** The spec value a backend slot resolves to, as text, for the "uses" line under each step. */
export function slotValue(spec, slot) {
 const doc = spec.template === "document_to_record";
 const alias = doc ? { guardrails: "recordGuardrails", recipient: "exceptionsTo", schedule: "pollInterval" } : {};
 if (slot === "title") return spec.title || spec.goal?.split(/[.\n]/)[0] || null;
 if (slot === "window") return spec.window ? `${spec.window} days back` : "each KPI's own window";
 const field = alias[slot] || Object.keys(FIELD_META).find((f) => FIELD_META[f].slot === slot);
 if (!field) return null;
 switch (field) {
  case "metrics": return spec.metricIds.join(", ") || null;
  case "metric": return spec.metricIds[0] || null;
  case "dimensions": return spec.dimensions.join(", ") || (spec.template === "metric_diagnosis" ? "every dimension the fact carries" : null);
  case "comparison": return spec.template === "metric_diagnosis" && !spec.comparison ? "the period before (a diagnosis compares by nature)" : comparisonLabel(spec.comparison);
  case "sensitivity": return { clear: "very sure (only a slice far outside its own history)", usual: "balanced", more: "show more (smaller departures count)" }[spec.sensitivity || "usual"];
  case "evidenceColumns": return (spec.evidenceColumns || []).length ? spec.evidenceColumns.join(", ") : "the fact's own columns";
  case "outputs": return (spec.outputs || []).length ? spec.outputs.join(" + ") : "report";
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
  case "schedule": return spec.template === "document_to_record" ? (POLL_INTERVALS.find((p) => p.value === spec.schedule)?.label || scheduleLabel(spec.schedule)) : scheduleLabel(spec.schedule);
  case "pollInterval": return POLL_INTERVALS.find((p) => p.value === spec.schedule)?.label || spec.schedule;
  case "mailbox": return spec.mailbox || null;
  case "messageFilter": { const f = spec.messageFilter || {}; const parts = [f.unread && "unread", f.hasAttachment && "has attachment", f.notOwnDomain && "not own domain", f.senderPattern && `from ${f.senderPattern}`].filter(Boolean); return parts.length ? parts.join(", ") : "all messages"; }
  case "documentType": return DOCUMENT_TYPES.find((d) => d.value === spec.documentType)?.label || null;
  case "extractionTemplate": return findTemplate(spec)?.label || null;
  case "exampleDocument": return spec.exampleDocument?.name || null;
  case "masterData": { const m = spec.masterData || {}; const bound = MASTER_DATA_ROLES.filter((r) => m[r.id]).map((r) => `${r.label} → ${m[r.id]}`); if (bound.length) return bound.join(" · "); const roles = resolutionPlan(spec).map((r) => r.label.toLowerCase()); return roles.length ? `${spec.writeback || "the target"}'s own ${roles.join(", ")}` : null; }
  case "matchThresholds": { const t = spec.matchThresholds || {}; return `address ≥ ${t.address} · product ≥ ${t.product} · name ≥ ${t.name}${t.aiDisambiguation ? " · AI disambiguation on" : ""}`; }
  case "recordGuardrails": { const rows = normalizeRules(spec.recordGuardrails); return rows.length ? rows.map((g) => { const ps = Object.entries(g.params || {}).map(([k, v]) => `${PARAM_META[k]?.label || k} ${v}${PARAM_META[k]?.unit || ""}`).join(", "); return `${g.label}${ps ? ` (${ps})` : ""}`; }).join(" · ") : null; }
  case "exceptionsTo": return spec.exceptionsTo || null;
  case "acknowledge": return spec.acknowledge ? "reply to the sender" : "no reply";
  case "targetObjects": return findMapping(spec)?.label || null;
  case "createdAs": return CREATED_AS.find((c) => c.value === spec.createdAs)?.label || null;
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
 receive_messages: STEP_KIND.read,
 screen_documents: STEP_KIND.check,
 resolve_entities: STEP_KIND.check,
 build_record: STEP_KIND.compute,
 send_email: STEP_KIND.notify,
 compare_to_baseline: STEP_KIND.compute,
 check_report_grounding: STEP_KIND.check,
 render_report: STEP_KIND.compute,
 send_report: STEP_KIND.notify,
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
   if (spec.template === "document_to_record") {
    const rows = spec.recordGuardrails || [];
    return { title: `Applies ${rows.length} business guardrail${rows.length === 1 ? "" : "s"}`, detail: "Record-level checks against master data and order history. Any failure stops the record and raises an exception with the guardrail that stopped it; nothing is written.", chips: normalizeRules(rows).map((g) => g.label), branch: { yes: "passes", no: "fails", outcome: "Exception, logged with the guardrail" } };
   }
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
   if (spec.template === "document_to_record") return { title: `Applies the change limit: ${spec.blastRadius || "not set"} per run`, detail: "Counted across every document in the poll. Beyond the limit, the message is held — unlabelled — and picked up on the next poll, never dropped.", branch: { yes: "within limit", no: "beyond limit", outcome: "Held for the next poll" } };
   return {
    title: `Applies the change limit: ${spec.blastRadius || "not set"} per run`,
    detail: "Changes beyond the limit are held for the next run, never dropped silently. The limit stands where the approver would: the most it can get wrong in one go.",
    branch: { yes: "within limit", no: "beyond limit", outcome: "Held for the next run" },
   };
  case "write_to_target":
   if (spec.template === "document_to_record") return { title: `Creates it in ${spec.writeback || "the target system"}`, detail: `One call sequence per document — the parent record, then its lines — recorded as a unit with the request payload and the returned ids. A target error is an exception; nothing partial is left behind.` };
   return {
    title: `Updates ${spec.writeback || "the target system"}`,
    detail: `Through the ${spec.writeback || "target"} connector — a connected system with write permission. Each change is stamped with the run, the recommendation and ${spec.shape === "recommend" ? "who approved it" : "the change limit that admitted it"}.`,
   };
  case "record_rollback_point":
   if (spec.template === "document_to_record") return { title: "Records the rollback point", detail: `${spec.rollback || "The rollback plan"} — the payload and the created ids are kept per document, so a run can be reversed as a unit. The owner and any admin hold the kill switch.` };
   return {
    title: "Records the rollback point",
    detail: `${spec.rollback || "The rollback"} — the prior value is kept per write, so any run can be reversed as a unit. The owner and any admin hold the kill switch.`,
   };
  case "receive_messages":
   return { title: `Receives from ${spec.mailbox || "the mailbox"}`, detail: `${slotValue(spec, "message filter")}. Each message is processed once; the message id is the provenance key.` };
  case "screen_documents":
   return { title: "Screens the message", detail: `Exactly one ${DOCUMENT_TYPES.find((d) => d.value === spec.documentType)?.label.toLowerCase() || "document"}, from a known sender, under the size limit. Anything else is an exception before a model runs.`, branch: { yes: "one document", no: "fails", outcome: "Exception: NO_DOCUMENT · MULTIPLE · UNKNOWN_SENDER" } };
  case "extraction":
   return { title: `Extracts the ${findTemplate(spec)?.label.toLowerCase() || "document"}`, detail: `Fills the template's schema — ${findTemplate(spec)?.header.length || 0} header fields, ${findTemplate(spec)?.lines.length || 0} per line — with a confidence and a page region per field. OCR when there is no text layer. Output validated against the schema; a field not on the document is null. Runs on the ${TIER_FOR_ROLE.extraction} tier.`, chips: [...(spec.exampleDocument ? [`example: ${spec.exampleDocument.name}`] : []), ...ROLE_TOOLS.extraction.map((t) => `may call ${t}`)] };
  case "resolve_entities":
  {
   const plan = resolutionPlan(spec);
   const reads = plan.length ? plan.map((r) => `${r.label.toLowerCase()} from ${r.reads.replace(/`/g, "")}`).join("; ") : "nothing — the mapping reads no master-data role and no guardrail needs one";
   return { title: "Resolves against master data", detail: `Only the roles this use case reads: ${reads}. A fixed order of attempts, scored; thresholds ${slotValue(spec, "match thresholds")}. Every resolution records its candidates and scores.`, branch: { yes: "all resolved", no: "not found / ambiguous", outcome: "Exception with the closest match and its score" } };
  }
  case "build_record":
   return { title: `Builds ${slotValue(spec, "target objects") || "the record"}`, detail: `Maps the resolved fields onto the target's objects, created as ${slotValue(spec, "created as")?.toLowerCase() || "draft"}. A required field the mapping cannot fill is an exception, not a blank.` };
  case "send_email":
   return { title: spec.acknowledge ? `Replies to the sender · exceptions to ${spec.exceptionsTo || "the exceptions address"}` : `Exceptions to ${spec.exceptionsTo || "the exceptions address"}`, detail: "The sender gets the created record's number, or the reason it was not created. The exception email carries the classification in words, what was extracted, and a link to the in-app record with the document and the candidate list." };
  case "compare_to_baseline":
   return spec.comparison || spec.template === "metric_diagnosis" ? {
    title: `Compares with ${spec.comparison === "prior_year" ? "the same period last year" : "the period before"}`,
    detail: `Each KPI at the run's date against gold at the baseline date -- the KPI's own window back -- per KPI and per breakdown row${spec.dimensions?.length && spec.template !== "metric_diagnosis" ? ` (${spec.dimensions.map((d) => d.replace(/_/g, " ")).join(", ")})` : ""}. A date gold does not hold yet is computed from the KPI's formula, once. Deterministic.`,
   } : {
    title: "Takes the numbers as they are",
    detail: `Each KPI at the run's date${spec.dimensions?.length ? `, broken down by ${spec.dimensions.map((d) => d.replace(/_/g, " ")).join(", ")}` : ""}, carried forward as it is. Nothing is compared; choose a baseline on the form to see what moved. A date gold does not hold yet is computed from the KPI's formula, once.`,
   };
  case "decompose_change":
   return {
    title: `Finds where the change in ${spec.metricIds[0] || "the KPI"} sits`,
    detail: `Searches ${spec.dimensions?.length ? spec.dimensions.map((d) => d.replace(/_/g, " ")).join(", ") : "every dimension the fact carries"}: each slice's contribution to the change (its own rate, or its share of the mix), how unusual it is against its own recent history -- ${slotValue(spec, "sensitivity")} -- and whether it has enough rows to be named. The winning slice is searched one level down and the rows behind it are fetched, with any reason column tallied. All arithmetic — nothing here is judged.`,
   };
  case "check_report_grounding":
   return {
    title: "Checks the narrative against the numbers",
    detail: "Every figure the AI step wrote must be one the comparison carried; a narrative that invents one is withheld and the report goes out with the numbers alone. Code, not a model.",
    branch: { yes: "every figure in the numbers", no: "a figure not in the numbers", outcome: "Narrative withheld; numbers only" },
   };
  case "render_report":
   return {
    title: "Renders the report",
    detail: `The comparison and the checked narrative as an HTML report -- tiles for the headline KPIs, tables with bars for each breakdown -- and its plain-text twin${(spec.outputs || []).includes("dashboard") ? ", with a link to the dashboard composed at activation" : ""}.`,
   };
  case "send_report":
   return {
    title: /@/.test(spec.notify || "") ? `Emails ${spec.notify}` : "No email address: in-app only",
    detail: /@/.test(spec.notify || "") ? "The HTML report as a multipart email through the connected mailbox; composed and held in a rehearsal." : "Add an address under Recipients to have the report emailed each run.",
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
 // A comparison is a choice: when none is chosen the Compare stage is what it then does.
 const stages = ((spec.template && TEMPLATE_STAGES[spec.template]) || SHAPE_STAGES[shape.id]).map((st) => (st.executorRef === "compare_to_baseline" && !spec.comparison && spec.template !== "metric_diagnosis" ? { ...st, name: "Take the numbers as they are", consumes: ["window"], description: "No comparison chosen: each KPI's value at the run's date and its breakdown rows, carried forward as they are. Nothing is compared." } : st));
 const steps = [];

 steps.push({
  id: "trigger", kind: STEP_KIND.trigger, who: "system", title: "Runs",
  detail: spec.template === "document_to_record"
   ? `${POLL_INTERVALS.find((p) => p.value === spec.schedule)?.label || scheduleLabel(spec.schedule)}, unattended. A poll is a run; every stage below runs once per document found, and the change limit counts across them.`
   : `${scheduleLabel(spec.schedule)}${spec.schedule === "manual" ? "" : ", unattended"}. The trigger consumes the schedule; no stage reads it. Each run is a versioned execution of this frozen spec.`,
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
