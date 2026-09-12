/**
 * The four use-case shapes, and what each one needs from the person building it.
 *
 * The shape is a property of the spec, chosen before the spec is filled -- each shape has its own
 * form (product/egpt_retail/contracts/use_case.py, `UseCaseShape` and `SHAPE_PARAMETERS`). It is
 * not inferred from the wording, and it is not a numbered autonomy level: "Level 3" means nothing
 * to a merchandiser, and a model guessing what someone meant is how a request to "propose
 * markdowns" becomes an unattended write. So the person picks one of four things it can do, in
 * plain words, and everything else follows from that choice.
 *
 * Two other axes exist but are derived, not asked:
 *   - `autonomy` (copilot / autopilot) is a runtime property; the interface never asks for it.
 *   - `level` (1-4) is what governance reads at preflight and activation. It is a consequence of
 *     the shape, so the untouched screens keep reading `AUTONOMY[spec.autonomy].level`.
 *
 * Each shape's `fields` is the backend's `SHAPE_PARAMETERS` tuple for that shape, in the same
 * order. `FIELD_META[field].slot` is the backend slot name (with its spaces), so the form and the
 * compiler's stage table (`use-case-plan.js`) agree on what a value is called.
 */

export const SHAPES = {
 inform: {
  id: "inform",
  level: 1,
  label: "Inform",
  plain: "Explain what happened",
  verb: "reads and explains",
  blurb: "Reads the numbers, compares them to a baseline, and explains the movement. Changes nothing.",
  sideEffect: "none",
  retail: {
   title: "Gross Margin Analysis",
   templateId: "gallery-uc-09",
   prompt: "Every Monday, explain gross margin rate by category and channel against plan and the same period last year. Send it to the finance team.",
  },
  fields: ["metrics", "dimensions", "comparison", "schedule", "recipient"],
 },
 alert: {
  id: "alert",
  level: 2,
  label: "Alert",
  plain: "Watch a metric and tell someone",
  verb: "watches and notifies",
  blurb: "Checks one metric against a threshold on a schedule and notifies someone when it is breached. A threshold, not an opinion.",
  sideEffect: "a notification",
  retail: {
   title: "Stockout Risk Alert",
   templateId: "gallery-uc-01",
   prompt: "Every morning, tell the replenishment team when stockout rate goes above 5% in any store. That's critical.",
  },
  fields: ["metric", "operator", "threshold", "scope", "severity", "recipient", "schedule"],
 },
 recommend: {
  id: "recommend",
  level: 3,
  label: "Recommend",
  plain: "Propose changes for someone to approve",
  verb: "proposes, then acts once approved",
  blurb: "Selects candidates, proposes a change for each, checks every proposal against hard limits, and holds them for a person before anything is written.",
  sideEffect: "a write to a system of record, after approval",
  retail: {
   title: "Markdown Optimisation",
   templateId: "gallery-uc-08",
   prompt: "Every Monday, propose markdowns for SKUs with more than 8 weeks of cover, using sell-through rate and markdown rate. Never take gross margin rate below 15%. Write to the pricing system, approved by Priya Nair.",
  },
  fields: ["candidates", "metrics", "guardrails", "approver", "writeTarget", "timeoutBehaviour", "schedule"],
 },
 act: {
  id: "act",
  level: 4,
  label: "Act",
  plain: "Act on its own, within a cap",
  verb: "acts on its own",
  blurb: "Same as Recommend, but a per-run cap and a rollback point stand where the approver would. Bounded, reversible, admin-activated.",
  sideEffect: "a write to a system of record, unattended",
  retail: {
   title: "Safety Stock Parameter Review",
   templateId: "gallery-uc-41",
   prompt: "Every Monday, review safety stock for SKUs whose supplier lead time varies by more than 20%, using safety stock coverage and stockout rate. Never set coverage below 14 days or above 60 days. Write to the inventory system, at most 50 SKUs per run, and keep the previous value so it can be restored.",
  },
  fields: ["candidates", "metrics", "guardrails", "writeTarget", "perRunCap", "rollback", "schedule"],
 },
};

export const SHAPE_LIST = Object.values(SHAPES);

/**
 * What the person chooses: four enterprise categories, mapped onto the backend's four shapes.
 * The shape stays the unit of contract, compilation and governance; the category is the label
 * layer an enterprise buyer recognises. Analytics Automation covers two shapes and asks which
 * trigger it runs on; Process Optimization is the Act shape with an objective -- a KPI it is
 * tuning a process parameter towards -- which the proposal agent's prompt carries.
 */
export const CATEGORIES = {
 analytics_automation: {
  id: "analytics_automation",
  label: "Analytics Automation",
  plain: "Analyse, monitor and explain",
  blurb: "Automates the analysis a team does by hand: reads the KPIs, compares them to a baseline, explains the movement — or watches one KPI and notifies when it crosses a line. Changes nothing.",
  sideEffect: "a report or a notification",
  shapes: ["inform", "alert"],
  // Chosen on the form, not on the card: which shape an Analytics Automation is depends on
  // whether it should report every run or only speak when a KPI crosses a line -- a decision
  // that comes after the KPI, not before it.
  trigger: [
   { shape: "inform", label: "Every run, on a schedule", hint: "A report each time: what moved, where, and why." },
   { shape: "alert", label: "Only when a KPI crosses a threshold", hint: "Notifies when it fires. A quiet day sends nothing." },
  ],
  examples: [
   { shape: "inform", title: "Gross Margin Analysis", templateId: "gallery-uc-09", prompt: SHAPES.inform.retail.prompt },
   { shape: "alert", title: "Stockout Risk Alert", templateId: "gallery-uc-01", prompt: SHAPES.alert.retail.prompt },
  ],
 },
 decision_intelligence: {
  id: "decision_intelligence",
  label: "Decision Intelligence",
  plain: "Recommend; your team decides",
  blurb: "Selects the records in scope, recommends a change for each within business guardrails, and holds every recommendation for an approver before anything is written.",
  sideEffect: "a change to a target system, after approval",
  shapes: ["recommend"],
  examples: [{ shape: "recommend", title: "Markdown Optimisation", templateId: "gallery-uc-08", prompt: SHAPES.recommend.retail.prompt }],
 },
 process_automation: {
  id: "process_automation",
  label: "Process Automation",
  plain: "Execute a defined action within limits",
  blurb: "Carries out a rule-based action unattended — reorder, re-price, re-assign — inside business guardrails, a change limit per run, and a rollback plan. Switched on by an admin.",
  sideEffect: "a change to a target system, unattended",
  shapes: ["act"],
  examples: [{
   shape: "act", title: "Low-Stock Auto-Reorder", templateId: null,
   prompt: "Every morning, reorder SKUs below their reorder point using days of stock remaining and inventory turnover. Never order beyond 60 days of cover. Write to the inventory system, at most 100 SKUs per run, and keep the previous order quantity so it can be restored.",
  }],
 },
 process_optimization: {
  id: "process_optimization",
  label: "Process Optimization",
  plain: "Tune a process towards a KPI target",
  blurb: "Continuously adjusts the parameters of a process — safety stock, reorder points, markdown depth — to move a KPI towards a target, inside business guardrails, a change limit per run, and a rollback plan.",
  sideEffect: "a change to a target system, unattended",
  shapes: ["act"],
  extraFields: ["objective"],
  examples: [{
   shape: "act", title: "Safety Stock Parameter Review", templateId: "gallery-uc-41",
   prompt: "Every Monday, tune safety stock for SKUs whose supplier lead time varies by more than 20%, to bring stockout rate below 5%. Never set coverage below 14 days or above 60 days. Write to the inventory system, at most 50 SKUs per run, and keep the previous value so it can be restored.",
  }],
 },
};
export const CATEGORY_LIST = Object.values(CATEGORIES);

export const categoryOf = (spec) => CATEGORIES[spec.category] || null;

/** The category name a person sees, with the trigger where the category spans two shapes. */
export function displayName(spec) {
 const c = categoryOf(spec);
 if (!c) return SHAPES[spec.shape]?.label || "Use case";
 if (c.trigger) { const t = c.trigger.find((x) => x.shape === spec.shape); return t ? `${c.label} · ${t.label.replace(/^\w/, (ch) => ch.toLowerCase())}` : c.label; }
 return c.label;
}

/** The form's fields: the backend shape's parameters, plus what the category adds. */
export function fieldsFor(spec) {
 const base = SHAPES[spec.shape]?.fields || [];
 const extra = categoryOf(spec)?.extraFields || [];
 if (!extra.length) return base;
 const i = base.indexOf("candidates");
 return i >= 0 ? [...base.slice(0, i + 1), ...extra, ...base.slice(i + 1)] : [...extra, ...base];
}

// The untouched screens (preflight, rehearse, activate) read `AUTONOMY[spec.autonomy].level`.
// The shape carries the level, so the alias keeps them working without a change.
export const AUTONOMY = SHAPES;
export const AUTONOMY_LIST = SHAPE_LIST;

/**
 * Every field a shape form can carry. `slot` is the backend name in `SHAPE_PARAMETERS`; `key` is
 * where the value lives on the spec (the spec keeps the names the later screens already read).
 */
export const FIELD_META = {
 metrics: { slot: "metrics", key: "metricIds", label: "KPIs", hint: "From your KPI registry. Only KPIs that exist." },
 metric: { slot: "metric", key: "metricIds", label: "KPI", hint: "One KPI. Monitoring watches one thing." },
 dimensions: { slot: "dimensions", key: "dimensions", label: "Analyse by", hint: "The dimensions the analysis cuts across." },
 comparison: { slot: "comparison", key: "comparison", label: "Baseline", hint: "What turns a value into a change." },
 operator: { slot: "operator", key: "condition", label: "Trigger condition", hint: "The direction of the breach." },
 threshold: { slot: "threshold", key: "condition", label: "Threshold", hint: "The number worth notifying someone for." },
 scope: { slot: "scope", key: "scope", label: "Scope", hint: "Where the condition is evaluated." },
 severity: { slot: "severity", key: "severity", label: "Severity", hint: "How loudly." },
 recipient: { slot: "recipient", key: "notify", label: "Recipients", hint: "Who receives it, and how they are pointed at it. The result lands in the app; every other channel gets a link to it (D13)." },
 candidates: { slot: "candidates", key: "candidates", label: "Scope of records", hint: "Which records each run considers: a business object from the semantic layer, and the filter that selects them." },
 guardrails: { slot: "guardrails", key: "guardrails", label: "Business guardrails", hint: "KPI, floor or ceiling, number. Checked as rules before any change is made; the AI step never sees them." },
 approver: { slot: "approver", key: "approver", label: "Approver", hint: "Who releases each recommendation. Must not be the author." },
 writeTarget: { slot: "write target", key: "writeback", label: "Target system", hint: "A connected system with write permission." },
 timeoutBehaviour: { slot: "timeout behaviour", key: "onTimeout", label: "If no response", hint: "What happens when the approver does not decide." },
 perRunCap: { slot: "per-run cap", key: "blastRadius", label: "Change limit per run", hint: "The most it may change in one run. Stands in for the approver." },
 rollback: { slot: "rollback", key: "rollback", label: "Rollback plan", hint: "How a run is reversed as a unit." },
 schedule: { slot: "schedule", key: "schedule", label: "Frequency", hint: "When it runs. Starts the workflow; no step reads it." },
 goal: { slot: null, key: "goal", label: "Goal", hint: "What this exists to do, in your words. Free text; it drives nothing." },
 title: { slot: null, key: "title", label: "Title", hint: "How it appears in the catalogue." },
 mode: { slot: null, key: "shape", label: "Output", hint: "Every run, or only when a KPI crosses a threshold. Decides which workflow is generated." },
 objective: { slot: "objective", key: "objective", label: "Objective", hint: "The KPI this tunes the process towards, and the target. Carried into the AI step's brief; not a contract slot yet." },
};

/** The slots the backend has no contract field for yet (open item R-15). Shown on the form, sent
 *  as flat keys, and flagged so a demo audience knows which fields are proposed. */
export const PROPOSED_CONTRACT_FIELDS = new Set(["operator", "threshold", "severity", "candidates", "approver", "writeTarget", "timeoutBehaviour", "perRunCap", "rollback", "objective"]);

export const describeShape = (spec) => {
 const m = spec.metricIds?.[0];
 switch (spec.shape) {
  case "inform":
   return `Explains ${spec.metricIds?.length ? spec.metricIds.join(" and ") : "the KPIs"}${spec.dimensions?.length ? ` by ${spec.dimensions.join(", ").toLowerCase()}` : ""}, and delivers it to ${spec.notify || "someone"}. Changes nothing.`;
  case "alert":
   return `Watches ${m || "one KPI"} and tells ${spec.notify || "someone"}${(spec.notifyChannels || []).filter((c) => c !== "in_app").length ? ` (in app, plus ${(spec.notifyChannels || []).filter((c) => c !== "in_app").map((c) => CHANNELS.find((x) => x.value === c)?.label || c).join(" and ")})` : " in app"} when it ${OPERATORS.find((o) => o.value === spec.condition?.operator)?.label || "crosses"} ${spec.condition?.threshold ?? "a threshold"}${spec.condition?.threshold != null ? spec.condition.unit || "" : ""}${spec.scope ? ` in ${spec.scope.toLowerCase()}` : ""}. Changes nothing.`;
  case "recommend":
   return `Recommends changes to ${spec.writeback || "a target system"}. ${spec.approver || "An approver"} releases each one before anything is written.`;
  case "act":
   return `${spec.objective?.metric ? `Tunes the process to bring ${spec.objective.metric} ${spec.objective.direction || "to"} ${spec.objective.target ?? "target"}${spec.objective.unit || ""}. ` : ""}Executes on its own — changes ${spec.writeback || "a target system"} with nobody in the loop, at most ${spec.blastRadius || "a limit you set"} per run, with a rollback plan.`;
  default:
   return "Choose what it should do.";
 }
};
export const describeAutonomy = describeShape;

/** Shapes whose activation must be done by someone other than the author. */
export function requiresSeparationOfDuties(shapeId) {
 return (SHAPES[shapeId]?.level || 1) >= 3;
}

/** Shapes that need an admin, not just a second person. */
export function requiresAdminActivation(shapeId) {
 return (SHAPES[shapeId]?.level || 1) >= 4;
}

// ── Closed sets ─────────────────────────────────────────────────────────────────────────────
// Everything a form value can be. The extractor recognises these; it never writes anything else.

/** Labels the person sees; `grain` is the semantic-layer vocabulary the backend stores. */
export const DIMENSION_GRAINS = [
 { label: "Category", grain: "product_category" },
 { label: "Brand", grain: "brand" },
 { label: "Channel", grain: "channel" },
 { label: "Store", grain: "location" },
 { label: "Region", grain: "location" },
 { label: "Customer segment", grain: "customer_segment" },
 { label: "Supplier", grain: "supplier" },
 { label: "Product", grain: "product" },
];
export const DIMENSIONS = DIMENSION_GRAINS.map((d) => d.label);

export const COMPARISONS = [
 { value: "prior_period", label: "The period before" },
 { value: "prior_year", label: "The same period last year" },
 { value: "plan", label: "Plan or target" },
 { value: "peer", label: "Peer average" },
];

/** Contract `Operator`: gt, gte, lt, lte, eq, delta_gt. The three an alert needs. */
export const OPERATORS = [
 { value: "gt", label: "goes above" },
 { value: "lt", label: "falls below" },
 { value: "delta_gt", label: "moves by more than" },
];

/** The two a hard limit needs: a floor or a ceiling. */
export const LIMIT_OPERATORS = [
 { value: "gte", label: "stays at or above" },
 { value: "lte", label: "stays at or below" },
];

export const SCOPES = ["Everywhere", "Any store", "Any category", "Any brand", "Any channel", "Any region"];

/** Contract `Severity`: critical, warning, info. */
export const SEVERITIES = [
 { value: "info", label: "Log it" },
 { value: "warning", label: "Flag it" },
 { value: "critical", label: "Interrupt someone" },
];

/** Contract `ScheduleCadence` has once/daily/weekly/monthly; hourly and manual are demo extras. */
export const SCHEDULES = [
 { value: "manual", label: "Only when someone asks" },
 { value: "hourly", label: "Every hour" },
 { value: "daily", label: "Every morning" },
 { value: "weekly", label: "Monday mornings" },
 { value: "monthly", label: "First of the month" },
];

/** Contract `DeliveryChannel`: in_app, email, slack. Teams is a frontend addition pending the contract. */
export const CHANNELS = [
 { value: "in_app", label: "In app", always: true },
 { value: "email", label: "Email" },
 { value: "slack", label: "Slack", providerId: "slack" },
 { value: "teams", label: "Teams", providerId: "teams" },
];

export const TIMEOUT_BEHAVIOURS = [
 { value: "cancel", label: "Do nothing", safe: true },
 { value: "remind", label: "Remind and wait", safe: true },
 { value: "escalate", label: "Escalate to the owner", safe: true },
 { value: "proceed", label: "Go ahead anyway", safe: false },
];

export const PER_RUN_CAPS = ["10 SKUs", "50 SKUs", "200 SKUs", "1% of catalogue"];

export const ROLLBACKS = ["Restore the previous value", "Revert the write", "Raise a ticket for manual reversal"];

/** Fallback write targets, for when no configured source carries write permission. The form
 *  prefers the configured sources with `permissionLevel: "read_write"`. */
export const WRITEBACK_TARGETS = ["Pricing system", "Inventory system"];

/** Rough per-run cost, in credits, so activation can show cost x schedule before the switch is
 *  thrown. Presentation-only; the platform meters real spend per tenant. */
export function estimatedRunsPerMonth(schedule) {
 return { manual: 4, hourly: 720, daily: 30, weekly: 4, monthly: 1 }[schedule] ?? 30;
}
