import { CATEGORIES, FIELD_META, LIMIT_OPERATORS, MATCH_THRESHOLD_DEFAULTS, SHAPES, fieldsFor, requiresSeparationOfDuties, templateFromText } from "./use-case-autonomy";
import { findTemplate, loadTemplateCatalogue } from "./extraction-templates";

/**
 * The use-case spec: the thing a user reviews, completes and freezes.
 *
 * The shape is chosen first; the form is the shape's parameter list. Everything that drives
 * execution is a CONSTRAINED reference -- a metric from the registry, a dimension from a known
 * list, an operator from an enum, a concept from the semantic layer, a write target from the
 * configured sources. Free text survives only where it changes nothing (goal, title, a filter the
 * person reviews). A model filling this in cannot invent a metric, because it can only choose one
 * that exists.
 *
 * COMPLETENESS IS NOT A JUDGEMENT. `gapsFor` is a deterministic pass over the fields the chosen
 * shape requires -- the interface's copy of `executors/gaps.check_spec_gaps`, which "consults no
 * model". A model phrases the questions and interprets the answers; it never decides whether to
 * ask.
 *
 * Every filled field carries an ORIGIN, mirroring the compiler's `ParameterOrigin`: "spec" when
 * the conversation derived it, "authored" when the person typed it on the form. The compiler
 * prefers authored, so the form shows which is which -- a value the person typed over one the
 * model heard is a correction, and it should look like one.
 */

export const GAP_KIND = {
 /** Not said yet. Answerable now. */
 missing: "missing",
 /** Maps to more than one real thing. Answerable now, from a closed list. */
 ambiguous: "ambiguous",
 /** Not in the connected data. NOT answerable by asking -- exits to a data request. */
 data: "data",
};

export const ORIGIN = { spec: "spec", authored: "authored", assumed: "assumed" };

// The person building it. Presentation-only stand-in for the signed-in identity; it exists so
// separation of duties can be enforced at activation rather than advised.
export const AUTHOR = "A. Mehta";

export function emptySpec(goal = "", shape = null, category = null, template = null) {
 return {
  category,
  template,
  shape,
  shapeChosen: !!shape,
  // Derived. The later screens read `AUTONOMY[spec.autonomy].level`; the shape is the autonomy.
  autonomy: shape || "inform",
  autonomyChosen: !!shape,
  autonomyInferred: false,
  origin: {},
  goal,
  title: "",
  businessArea: "",
  owner: "",
  author: AUTHOR,
  schedule: "daily",
  successMeasure: "",
  // inform
  metricIds: [],
  outputs: ["report", "dashboard"], // inferred, never asked: every Analytics Automation produces both
  window: null,
  analytics: null,
  metricTerms: [],
  dimensions: [],
  comparison: null,
  sensitivity: "usual", // the diagnostic kind (K25): clear | usual | more
  evidenceColumns: [],
  churnWindow: 60, // the at-risk kind (K26): days without a quote before an account is at risk
  outcome: "both", // the at-risk kind (K26): recommend | alert | both
  // alert
  condition: null,
  scope: "",
  severity: "warning",
  notify: "",
  notifyChannels: ["in_app"],
  // recommend / act
  candidates: null,
  objective: null,
  // document to record
  mailbox: "",
  messageFilter: { unread: true, hasAttachment: true, notOwnDomain: true, senderPattern: "" },
  documentType: "pdf",
  extractionTemplate: "",
  exampleDocument: null,
  extractionTemplateDef: null, // the chosen template's own definition when it is not a shipped one (K17)
  extractionTemplateDraft: null, // a document kind named in the description, not yet drafted (K17)
  recordMappingDef: null, // the chosen mapping's own definition when it is not a shipped one (K18)
  masterData: { customer: "", contact: "", shipTo: "", product: "", orderHistory: "" },
  matchThresholds: { ...MATCH_THRESHOLD_DEFAULTS },
  recordGuardrails: [],
  exceptionsTo: "",
  acknowledge: true,
  targetObjects: "",
  createdAs: "draft",
  guardrails: [],
  approver: "",
  writeback: "",
  onTimeout: "cancel",
  blastRadius: null,
  rollback: "",
  // activation
  environment: "sandbox",
  delivery: "none",
  piiAllowed: false,
  assumed: ["schedule", "severity", "timeoutBehaviour"],
 };
}

/** Pick (or change) the shape. Changing it clears the fields the new shape does not carry, so a
 *  threshold typed for an alert cannot leak into a recommendation as a guardrail. */
export function withShape(spec, shape, category = spec.category, template = spec.template) {
 const keep = new Set(fieldsFor({ shape, category, template }).map((f) => FIELD_META[f].key));
 const blank = emptySpec(spec.goal, shape, category, template);
 const next = { ...blank, title: spec.title, owner: spec.owner, author: spec.author, environment: spec.environment, delivery: spec.delivery, notifyChannels: spec.notifyChannels || blank.notifyChannels, businessArea: spec.businessArea || "" };
 for (const key of keep) next[key] = spec[key] ?? blank[key];
 next.metricTerms = keep.has("metricIds") ? spec.metricTerms || [] : [];
 next.origin = Object.fromEntries(Object.entries(spec.origin || {}).filter(([f]) => keep.has(FIELD_META[f]?.key)));
 return next;
}

/** Pick an enterprise category; the backend shape follows from it (and the trigger, where the
 *  category spans two). */
export function withCategory(spec, category, shape = null) {
 const c = CATEGORIES[category];
 // A category with templates starts undecided; the template is read from the requirement.
 const template = c?.templates ? templateFromText(spec.goal) : null;
 return withShape(spec, shape || c?.shapes[0] || "inform", category, template);
}

/** Switch the template within a category; the shape stays, the form changes. */
export function withTemplate(spec, template) {
 return withShape(spec, spec.shape, spec.category, template);
}

/** Apply a patch and record where each value came from. */
export function applyPatch(spec, patch, origin) {
 const next = { ...spec, ...patch, origin: { ...(spec.origin || {}) } };
 for (const field of Object.keys(FIELD_META)) {
  if (FIELD_META[field].key in patch) next.origin[field] = origin;
 }
 if ("schedule" in patch || "severity" in patch || "onTimeout" in patch) {
  const cleared = new Set(["schedule" in patch && "schedule", "severity" in patch && "severity", "onTimeout" in patch && "timeoutBehaviour"]);
  next.assumed = (spec.assumed || []).filter((a) => !cleared.has(a));
 }
 return next;
}

/** A title from the fields, once there is something to name. Deterministic, so two people
 *  building the same thing get the same name. Overwritten by anything typed. */
export function proposeTitle(spec) {
 const m = spec.metricIds?.[0];
 if (spec.template === "document_to_record") { const t = findTemplate(spec); return t ? `${t.label} → ${spec.writeback ? spec.writeback.split(/\s*[-–]\s*/)[0] : "record"}` : ""; }
 if (spec.template === "at_risk") {
  // The at-risk kind is named for what it watches, not for the question: a sales
  // organisation calls this its at-risk pipeline.
  const cadence = spec.schedule === "weekly" ? "weekly" : spec.schedule === "daily" ? "daily" : spec.schedule === "monthly" ? "monthly" : "";
  return `At-risk pipeline${cadence ? ` — ${cadence}` : ""}`;
 }
 if (spec.category === "analytics_automation" && spec.analytics) {
  // Named from the ask, the way a person would title a report; the identifier is not a name.
  // The first clause of the ask, the way a person would title a report: "Full visibility
  // into the quote pipeline, from a quote…" -> "Visibility into the quote pipeline".
  const first = String(spec.goal || "").split(/[.\n]/)[0].split(/[,;:—–(]/)[0].replace(/^(full|complete|give me|show me|i want|we need|please)\s+/i, "").trim();
  const cadence = spec.schedule === "weekly" ? "weekly" : spec.schedule === "daily" ? "daily" : spec.schedule === "monthly" ? "monthly" : "";
  const words = first.split(/\s+/);
  const short = words.length > 8 ? words.slice(0, 8).join(" ") : first;
  return short ? `${short.charAt(0).toUpperCase()}${short.slice(1)}${cadence ? ` — ${cadence} report` : ""}` : "";
 }
 if (!m) return "";
 switch (spec.shape) {
  case "inform": return `${m}${spec.dimensions?.length ? ` by ${spec.dimensions.slice(0, 2).join(" and ").toLowerCase()}` : ""}`;
  case "alert": return `${m} alert${spec.scope && spec.scope !== "Everywhere" ? ` — ${spec.scope.toLowerCase()}` : ""}`;
  case "recommend": return `${spec.writeback ? spec.writeback.split(/\s*[-–]\s*/)[0] : m} proposals${spec.candidates?.concept ? ` for ${spec.candidates.concept}` : ""}`;
  case "act":
   if (spec.template === "document_to_record") { const t = findTemplate(spec); return t ? `${t.label} → ${spec.writeback ? spec.writeback.split(/\s*[-–]\s*/)[0] : "record"}` : ""; }
   return spec.objective?.metric ? `${spec.objective.metric} optimisation${spec.candidates?.concept ? ` · ${spec.candidates.concept}` : ""}` : `${spec.writeback ? spec.writeback.split(/\s*[-–]\s*/)[0] : m} updates${spec.candidates?.concept ? ` for ${spec.candidates.concept}` : ""}`;
  default: return m;
 }
}

export function resolveMetricTerm(term, registryNames) {
 const q = String(term || "").trim().toLowerCase();
 if (!q) return { term, matches: [] };
 const exact = registryNames.filter((n) => n.toLowerCase() === q);
 if (exact.length) return { term, matches: exact };
 return { term, matches: registryNames.filter((n) => n.toLowerCase().includes(q)) };
}

/** A guardrail as one line, for the screens that list them. */
export function guardrailRule(g) {
 const op = LIMIT_OPERATORS.find((o) => o.value === g.operator)?.label || g.operator;
 return `${g.metric || "?"} ${op} ${g.threshold ?? "?"}${g.unit || ""}`;
}

/** Everything still standing between this spec and a freeze, in the shape's field order. */
export function gapsFor(spec, registryNames, measurable) {
 const gaps = [];
 const push = (g) => gaps.push(g);
 const shape = SHAPES[spec.shape];
 if (!shape) {
  push({ id: "shape", kind: GAP_KIND.missing, field: "shape", ask: "What kind of use case is this?" });
  return gaps;
 }
 const need = new Set(fieldsFor(spec));

 if (!String(spec.goal || "").trim()) {
  push({ id: "goal", kind: GAP_KIND.missing, field: "goal", ask: "In a sentence, what is this for?" });
 }
 // A category with templates, and the words have not said which yet.
 if (CATEGORIES[spec.category]?.templates && !spec.template) {
  push({ id: "template", kind: GAP_KIND.missing, field: "template", ask: "What arrives — a KPI on a schedule, or a document (by email, in a folder)?", options: CATEGORIES[spec.category].templates.map((t) => t.label) });
  return gaps;
 }

 // Metric terms typed but not yet resolved to a registry entry.
 for (const term of spec.metricTerms || []) {
  const { matches } = resolveMetricTerm(term, registryNames);
  if (matches.length === 0) {
   push({
    id: `metric-unknown-${term}`, kind: GAP_KIND.missing, field: need.has("metric") ? "metric" : "metrics",
    ask: `Nothing in your registry is called "${term}". Which KPI did you mean?`,
    options: registryNames.slice(0, 8),
   });
  } else if (matches.length > 1 && !matches.some((m) => spec.metricIds.includes(m))) {
   push({
    id: `metric-ambiguous-${term}`, kind: GAP_KIND.ambiguous, field: need.has("metric") ? "metric" : "metrics",
    ask: `"${term}" matches ${matches.length} KPIs. Which one?`, options: matches,
   });
  }
 }

 if ((need.has("metrics") || need.has("metric")) && spec.metricIds.length === 0 && (spec.metricTerms || []).length === 0) {
  push({
   id: "metrics", kind: GAP_KIND.missing, field: need.has("metric") ? "metric" : "metrics",
   ask: need.has("metric") ? "Which KPI should it watch?" : "Which KPIs is this built on?",
   options: registryNames.slice(0, 8),
  });
 }

 // Chosen metrics the connected data cannot compute. Not a question -- the customer cannot answer
 // it -- so it exits to a data request instead.
 for (const id of spec.metricIds || []) {
  if (!measurable.has(id)) {
   push({ id: `data-${id}`, kind: GAP_KIND.data, field: "metrics", ask: `${id} cannot be computed from your connected data yet.`, metric: id });
  }
 }

 if (spec.template === "at_risk" && spec.metricIds.length === 0) {
  push({ id: "metric-context", kind: GAP_KIND.missing, field: "metrics", ask: "Which KPIs give the risk its context (stalled quotes, churn rate, at-risk accounts)?", options: [] });
 }
 if (spec.template === "metric_diagnosis" && spec.metricIds.length !== 1) {
  push({ id: "metric-one", kind: GAP_KIND.missing, field: "metrics", ask: spec.metricIds.length ? "A diagnosis explains one KPI. Which one?" : "Which KPI changed?", options: spec.metricIds.slice(0, 8) });
 }
 // The diagnostic searches every dimension unless told otherwise, so none chosen is not a gap.
 if (need.has("dimensions") && spec.dimensions.length === 0 && spec.template !== "metric_diagnosis") {
  push({ id: "dimensions", kind: GAP_KIND.missing, field: "dimensions", ask: "Analyse it by which of these?", multi: true });
 }
 // No gap for a missing comparison: it is a choice, and a report without one carries
 // the numbers as they are.
 if (need.has("operator") && !spec.condition?.operator) {
  push({ id: "operator", kind: GAP_KIND.missing, field: "operator", ask: "Should it fire when the metric goes above, falls below, or moves by more than a number?" });
 }
 if (need.has("threshold") && (spec.condition?.threshold == null || spec.condition?.threshold === "")) {
  push({ id: "threshold", kind: GAP_KIND.missing, field: "threshold", ask: "What number should trigger it?" });
 }
 if (need.has("scope") && !spec.scope) {
  push({ id: "scope", kind: GAP_KIND.missing, field: "scope", ask: "Where should it watch — everywhere, or any store, category, brand?" });
 }
 if (need.has("recipient") && !spec.notify) {
  push({ id: "recipient", kind: GAP_KIND.missing, field: "recipient", ask: spec.shape === "alert" ? "Who should hear about it?" : "Who should receive it?" });
 }
 if (need.has("candidates")) {
  if (!spec.candidates?.concept) {
   push({ id: "candidates", kind: GAP_KIND.missing, field: "candidates", ask: "Which records should each run consider — SKUs, categories, suppliers?" });
  } else if (!String(spec.candidates.filter || "").trim()) {
   push({ id: "candidates-filter", kind: GAP_KIND.missing, field: "candidates", ask: `Which ${spec.candidates.concept.toLowerCase()}? Give the filter — e.g. "more than 8 weeks of cover".` });
  }
 }
 if (need.has("mailbox") && !spec.mailbox) {
  push({ id: "mailbox", kind: GAP_KIND.missing, field: "mailbox", ask: "Which connected mailbox do the documents arrive in?" });
 }
 if (need.has("extractionTemplate") && !spec.extractionTemplate) {
  push({ id: "extractionTemplate", kind: GAP_KIND.missing, field: "extractionTemplate", ask: "What kind of document is it?", options: loadTemplateCatalogue().map((t) => t.label) });
 }
 // Master data is not asked for (K24): the roles the mapping reads and the guardrails need
 // are resolved against the target's own data at run time.
 if (need.has("recordGuardrails") && (spec.recordGuardrails || []).length === 0) {
  push({ id: "recordGuardrails", kind: GAP_KIND.missing, field: "recordGuardrails", ask: "Which business guardrails should stop a record before it is written? At least one." });
 }
 if (need.has("exceptionsTo") && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(spec.exceptionsTo || "")) {
  push({ id: "exceptionsTo", kind: GAP_KIND.missing, field: "exceptionsTo", ask: "Which email address should exceptions go to?" });
 }
 if (need.has("targetObjects") && !spec.targetObjects) {
  push({ id: "targetObjects", kind: GAP_KIND.missing, field: "targetObjects", ask: "What should be created in the target system?" });
 }
 if (need.has("objective")) {
  if (!spec.objective?.metric) {
   push({ id: "objective", kind: GAP_KIND.missing, field: "objective", ask: "Which KPI is this tuning the process towards, and to what target?", options: spec.metricIds.length ? spec.metricIds : registryNames.slice(0, 8) });
  } else if (spec.objective.target == null) {
   push({ id: "objective-target", kind: GAP_KIND.missing, field: "objective", ask: `What target for ${spec.objective.metric}?` });
  }
 }
 if (need.has("guardrails")) {
  if (spec.guardrails.length === 0) {
   push({ id: "guardrails", kind: GAP_KIND.missing, field: "guardrails", ask: "What may it never do? At least one business guardrail — a KPI, a floor or ceiling, and a number." });
  }
  for (const g of spec.guardrails) {
   if (!g.metric) {
    push({ id: `guardrail-metric-${g.threshold}`, kind: GAP_KIND.missing, field: "guardrails", ask: `Which KPI is the guardrail "${g.rule}" on?`, options: spec.metricIds.length ? spec.metricIds : registryNames.slice(0, 8), guardrail: g });
   } else if (!registryNames.includes(g.metric)) {
    const { matches } = resolveMetricTerm(g.metric, registryNames);
    push({
     id: `guardrail-metric-${g.metric}`, kind: matches.length > 1 ? GAP_KIND.ambiguous : GAP_KIND.missing, field: "guardrails",
     ask: matches.length > 1 ? `The limit on "${g.metric}" matches ${matches.length} metrics. Which one?` : `Nothing in your registry is called "${g.metric}". Which metric is the limit on?`,
     options: matches.length ? matches : registryNames.slice(0, 8), guardrail: g,
    });
   }
  }
 }
 if (need.has("approver")) {
  if (!spec.approver) {
   push({ id: "approver", kind: GAP_KIND.missing, field: "approver", ask: "Who approves each recommendation before it is written?" });
  } else if (requiresSeparationOfDuties(spec.shape) && spec.approver.trim().toLowerCase() === spec.author.toLowerCase()) {
   push({ id: "approver-sod", kind: GAP_KIND.missing, field: "approver", ask: "The approver cannot be the author. Who else signs this off?" });
  }
 }
 if (need.has("writeTarget") && !spec.writeback) {
  push({ id: "writeTarget", kind: GAP_KIND.missing, field: "writeTarget", ask: "Which system does the change go to? It has to be a connected system with write permission." });
 }
 if (need.has("perRunCap") && !spec.blastRadius) {
  push({ id: "perRunCap", kind: GAP_KIND.missing, field: "perRunCap", ask: "Running unattended needs a change limit. How much may it change in one run?" });
 }
 if (need.has("rollback") && !spec.rollback) {
  push({ id: "rollback", kind: GAP_KIND.missing, field: "rollback", ask: "If a run gets it wrong, how is it undone?" });
 }
 if (!String(spec.owner || "").trim()) {
  push({ id: "owner", kind: GAP_KIND.missing, field: "owner", ask: "Who owns this once it is live?" });
 }
 return gaps;
}

export function blockingGaps(gaps) {
 return gaps.filter((g) => g.kind !== GAP_KIND.data);
}

export function draftState(gaps) {
 if (gaps.some((g) => g.kind === GAP_KIND.data)) return "blocked_on_data";
 if (gaps.length > 0) return "unfinished";
 return "ready";
}
