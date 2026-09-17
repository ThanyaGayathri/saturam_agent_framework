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

import { findTemplate } from "./extraction-templates";
import { mappingLabel } from "./record-mappings";
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
  fields: ["metrics", "dimensions", "comparison", "outputs", "schedule", "recipient"],
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
  blurb: "Carries out a rule-based action unattended — reorder, re-price, create a record from a document — inside business guardrails, a change limit per run, and a rollback plan. Switched on by an admin.",
  sideEffect: "a change to a target system, unattended",
  shapes: ["act"],
  // Two templates share the shape. Which one is chosen on the form, not on the card: it is a
  // question about the input -- a KPI or a document -- and the person answers it once they
  // know what arrives.
  templates: [
   { id: "kpi_change", label: "Change a parameter from a KPI", hint: "Reads KPIs on a schedule, recommends a change per record, writes it within limits." },
   { id: "document_to_record", label: "Create a record from a document", hint: "A document arrives — by email, in a folder — is extracted, resolved against master data, checked, and written as a record." },
  ],
  examples: [
   {
    shape: "act", template: "kpi_change", title: "Low-Stock Auto-Reorder", templateId: null,
    prompt: "Every morning, reorder SKUs below their reorder point using days of stock remaining and inventory turnover. Never order beyond 60 days of cover. Write to the inventory system, at most 100 SKUs per run, and keep the previous order quantity so it can be restored.",
   },
   {
    shape: "act", template: "document_to_record", title: "Order Creation Automation", templateId: null,
    prompt: "Create sales orders in Salesforce from purchase orders that arrive as PDFs in the orders mailbox, checking every 5 minutes. Send exceptions to orders-desk@northwind.example. At most 20 orders per run.",
   },
  ],
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

/**
 * Which template a Process Automation is, read from the person's own words. Never asked up
 * front: the answer is in the requirement ("purchase orders arrive as PDFs" is a document;
 * "reorder SKUs below their reorder point" is a KPI). Returns null when the words say neither,
 * and the form stays neutral until they do.
 */
export function templateFromText(text) {
 const t = String(text || "").toLowerCase();
 const doc = /\b(pdf|attachment|e-?mail|mailbox|inbox|document|invoice|purchase orders?|\bpos?\b|scanned?|form|extract|ocr|return requests?|delivery notes?|rma)\b/.test(t);
 const kpi = /\b(kpi|metric|rate\b|reorder point|safety stock|markdown|re-?price|price|parameter|tune|adjust|threshold|days of stock|turnover|coverage)\b/.test(t);
 if (doc && !kpi) return "document_to_record";
 if (kpi && !doc) return "kpi_change";
 if (doc && kpi) return /\b(pdf|attachment|e-?mail|mailbox|inbox|document|invoice)\b/.test(t) ? "document_to_record" : "kpi_change";
 return null;
}
export const templateOf = (spec) => categoryOf(spec)?.templates?.find((t) => t.id === spec.template) || null;

/**
 * The document-to-record template's form, in order. Slots named as the compiler's stage table
 * names them. Nothing here is a KPI: the input is a document, the "candidates" are messages,
 * and the AI step extracts rather than recommends.
 */
export const TEMPLATE_FIELDS = {
 // The form's order is the person's order: what arrives, what to read from it, what to
 // check, where it goes and as what, then the limits. Master data is not a section any
 // more (K24): the roles a use case resolves against follow from the mapping and the
 // guardrails and are read from the target itself; the thresholds keep their defaults.
 document_to_record: [
  "mailbox", "pollInterval", "messageFilter", "documentType", "extractionTemplate",
  "recordGuardrails", "writeTarget", "targetObjects", "exceptionsTo", "acknowledge",
  "createdAs", "perRunCap", "rollback",
 ],
};

/** The category name a person sees, with the trigger where the category spans two shapes. */
export function displayName(spec) {
 const c = categoryOf(spec);
 if (!c) return SHAPES[spec.shape]?.label || "Use case";
 if (c.trigger) { const t = c.trigger.find((x) => x.shape === spec.shape); return t ? `${c.label} · ${t.label.replace(/^\w/, (ch) => ch.toLowerCase())}` : c.label; }
 if (c.templates) { const t = c.templates.find((x) => x.id === spec.template); return t ? `${c.label} · ${t.label.replace(/^\w/, (ch) => ch.toLowerCase())}` : c.label; }
 return c.label;
}

/** The form's fields: the backend shape's parameters, plus what the category adds. */
export function fieldsFor(spec) {
 if (spec.template && TEMPLATE_FIELDS[spec.template]) return TEMPLATE_FIELDS[spec.template];
 if (CATEGORIES[spec.category]?.templates && !spec.template) return [];
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
 outputs: { slot: "outputs", key: "outputs", label: "Outputs", hint: "What each run produces: the report (email + in-app), a dashboard composed at activation, or both." },
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
 template: { slot: null, key: "template", label: "What arrives", hint: "Read from your description: a KPI on a schedule, or a document. Decides the form and the workflow." },
 mailbox: { slot: "source mailbox", key: "mailbox", label: "Source mailbox", hint: "The connected mailbox the documents arrive in." },
 pollInterval: { slot: "schedule", key: "schedule", label: "Check every", hint: "How often the mailbox is polled. The trigger." },
 messageFilter: { slot: "message filter", key: "messageFilter", label: "Message filter", hint: "Which messages count. Everything else is left alone." },
 documentType: { slot: "document type", key: "documentType", label: "Document type", hint: "Where the order is: an attachment, the email body, or either." },
 extractionTemplate: { slot: "extraction template", key: "extractionTemplate", label: "Extraction template", hint: "The schema the AI step fills. Shipped templates; the fields are fixed per template." },
 exampleDocument: { slot: "example document", key: "exampleDocument", label: "Example document", hint: "One sample of the format with its expected values. Becomes the AI step's example." },
 masterData: { slot: "master data", key: "masterData", label: "Master data", hint: "The business objects the document is resolved against, from the connected system's semantic layer." },
 matchThresholds: { slot: "match thresholds", key: "matchThresholds", label: "Match thresholds", hint: "Below the threshold is an exception, never a guess. The AI step may only choose between candidates above it." },
 recordGuardrails: { slot: "guardrails", key: "recordGuardrails", label: "Business guardrails", hint: "Checks on the resolved record before anything is written. Any failure stops the record and raises an exception." },
 exceptionsTo: { slot: "recipient", key: "exceptionsTo", label: "Exceptions go to", hint: "An email address for anything the automation could not complete. The in-app record always exists." },
 acknowledge: { slot: "acknowledge", key: "acknowledge", label: "Acknowledge the sender", hint: "Reply with the created record's number, or the exception reason." },
 targetObjects: { slot: "target objects", key: "targetObjects", label: "Target objects", hint: "What is created in the target system, and how the extracted fields map onto it." },
 createdAs: { slot: "created as", key: "createdAs", label: "Created as", hint: "The status a new record starts in. Draft keeps a person's activation in the loop without an approval step." },
 mode: { slot: null, key: "shape", label: "Output", hint: "Every run, or only when a KPI crosses a threshold. Decides which workflow is generated." },
 objective: { slot: "objective", key: "objective", label: "Objective", hint: "The KPI this tunes the process towards, and the target. Carried into the AI step's brief; not a contract slot yet." },
};

/** The slots the backend has no contract field for yet (open item R-15). Shown on the form, sent
 *  as flat keys, and flagged so a demo audience knows which fields are proposed. */
export const PROPOSED_CONTRACT_FIELDS = new Set(["operator", "threshold", "severity", "candidates", "approver", "writeTarget", "timeoutBehaviour", "perRunCap", "rollback", "objective", "mailbox", "messageFilter", "documentType", "extractionTemplate", "exampleDocument", "masterData", "matchThresholds", "recordGuardrails", "exceptionsTo", "acknowledge", "targetObjects", "createdAs"]);

/** The roles a document is resolved against follow from the mapping and the guardrails and
 *  are read from the target's own data (K24); the plan names them one by one. */
function resolvedRolesText(spec) {
 const target = spec.writeback ? spec.writeback.split(/\s*[-–]\s*/)[0] : "the target";
 return `each against ${target}'s own reference data (the roles the mapping reads)`;
}

export const describeShape = (spec) => {
 const m = spec.metricIds?.[0];
 switch (spec.shape) {
  case "inform": {
   if (spec.category === "analytics_automation" && spec.analytics) {
    const names = (spec.analytics.kpis || []).filter((k) => spec.metricIds?.includes(k.identifier)).map((k) => k.name);
    const n = spec.metricIds?.length || 0;
    const outputs = spec.outputs || [];
    const cadence = SCHEDULES.find((x) => x.value === spec.schedule)?.label?.toLowerCase() || "on a schedule";
    return `Reads ${n} KPI${n === 1 ? "" : "s"} the data supports${names.length ? ` (${names.slice(0, 4).join(", ")}${names.length > 4 ? ", …" : ""})` : ""}, compares each with ${spec.comparison === "prior_year" ? "the same period last year" : "the period before"}${spec.dimensions?.length ? ` and by ${spec.dimensions.map((d) => d.replace(/_/g, " ")).join(", ")}` : ""}, has the AI step explain what moved -- every figure checked against the numbers -- and ${outputs.includes("report") ? `sends the report ${cadence}${spec.notify ? ` to ${spec.notify}` : " in-app"}` : "keeps the result in-app"}${outputs.includes("dashboard") ? "; a dashboard is composed from the same KPIs at activation" : ""}. Changes nothing.`;
   }
   return `Explains ${spec.metricIds?.length ? spec.metricIds.join(" and ") : "the KPIs"}${spec.dimensions?.length ? ` by ${spec.dimensions.join(", ").toLowerCase()}` : ""}, and delivers it to ${spec.notify || "someone"}. Changes nothing.`;
  }
  case "alert":
   return `Watches ${m || "one KPI"} and tells ${spec.notify || "someone"}${(spec.notifyChannels || []).filter((c) => c !== "in_app").length ? ` (in app, plus ${(spec.notifyChannels || []).filter((c) => c !== "in_app").map((c) => CHANNELS.find((x) => x.value === c)?.label || c).join(" and ")})` : " in app"} when it ${OPERATORS.find((o) => o.value === spec.condition?.operator)?.label || "crosses"} ${spec.condition?.threshold ?? "a threshold"}${spec.condition?.threshold != null ? spec.condition.unit || "" : ""}${spec.scope ? ` in ${spec.scope.toLowerCase()}` : ""}. Changes nothing.`;
  case "recommend":
   return `Recommends changes to ${spec.writeback || "a target system"}. ${spec.approver || "An approver"} releases each one before anything is written.`;
  case "act":
   if (spec.template === "document_to_record") {
    const tpl = findTemplate(spec);
    return `Watches ${spec.mailbox || "a mailbox"} for ${tpl ? `${tpl.label.toLowerCase()}s` : "documents"}${spec.documentType === "pdf" ? " as PDF attachments" : ""}, extracts each one, resolves ${resolvedRolesText(spec)}, checks ${(spec.recordGuardrails || []).length} business guardrail${(spec.recordGuardrails || []).length === 1 ? "" : "s"}, and creates ${mappingLabel(spec)?.replace(/ \(standard\)/, "") || "a record"} in ${spec.writeback || "the target system"} as ${spec.createdAs === "active" ? "active" : "a draft"} — nobody in the loop, at most ${spec.blastRadius || "a limit you set"} per run. Anything it cannot complete goes to ${spec.exceptionsTo || "the exceptions address"}.`;
   }
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

// ── Document-to-record closed sets ──────────────────────────────────────────────────────────

export const POLL_INTERVALS = [
 { value: "1m", label: "Every minute" },
 { value: "5m", label: "Every 5 minutes" },
 { value: "15m", label: "Every 15 minutes" },
 { value: "hourly", label: "Every hour" },
];

export const DOCUMENT_TYPES = [
 { value: "pdf", label: "PDF attachment", hint: "Text layer required; a scanned copy is an exception in this version." },
 { value: "body", label: "Email body", hint: "The order is written in the message." },
 { value: "either", label: "Either", hint: "Attachment first, body if none." },
];

/** Shipped document templates. The catalogue, custom templates and the field helpers live in
 *  `extraction-templates.js`; this alias keeps older imports working. */
export { SHIPPED_TEMPLATES as EXTRACTION_TEMPLATES } from "./extraction-templates";

/** The roles a document-to-record use case can resolve against. Each binds to a concept. Which
 *  of them a given use case needs is not fixed here: `use-case-roles.js` derives it from the
 *  mapping's source expressions (`prefix`) and the guardrails, plus `depends`. */
export const MASTER_DATA_ROLES = [
 { id: "customer", label: "Sender account", concept: "Customer", prefix: "customer.", depends: [], hint: "The organisation the document comes from — a customer or a supplier — matched by sender email domain, then by name." },
 { id: "contact", label: "Sender contact", concept: "Contact", prefix: "contact.", depends: ["customer"], hint: "The person who sent it, matched by sender email." },
 { id: "shipTo", label: "Ship-to site", concept: "Location", prefix: "ship_to.", depends: ["customer"], hint: "The account's addresses; matched on postcode + street number, then full text." },
 { id: "product", label: "Product", concept: "Product", prefix: "product.", depends: [], hint: "Matched by the sender's part number, then product code, then description." },
 { id: "orderHistory", label: "Record history", concept: "Order", prefix: null, depends: ["customer"], hint: "What the target already holds per account; powers the history guardrails." },
];

export const MATCH_THRESHOLD_DEFAULTS = { address: 0.75, product: 0.85, name: 0.8, ambiguity: 0.05, aiDisambiguation: true };

/** Business guardrails are rules over the template fields (`guardrail-rules.js`). This alias
 *  is the purchase-order preset, for older imports. */
export { PURCHASE_ORDER_RULES as RECORD_GUARDRAILS } from "./guardrail-rules";

/** What the target creates is a record mapping (`record-mappings.js`): the shipped two plus the
 *  workspace's own. This alias keeps older imports working. */
export { SHIPPED_MAPPINGS as TARGET_MAPPINGS } from "./record-mappings";

export const CREATED_AS = [
 { value: "draft", label: "Draft", hint: "A person activates it in the target system." },
 { value: "active", label: "Activated", hint: "Live on creation." },
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
