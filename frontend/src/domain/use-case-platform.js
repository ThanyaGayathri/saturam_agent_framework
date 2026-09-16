import { POLL_INTERVALS, SHAPES } from "./use-case-autonomy";
import { findTemplate } from "./extraction-templates";
import { normalizeRules } from "./guardrail-rules";
import { findMapping } from "./record-mappings";

/**
 * The spec as the platform's contract sees it (API 1.3.0): the catalog row, the stored spec
 * with its shape, template and parameters, the builder draft, and the activation request.
 * Pure functions over the local spec draft and the configured sources, so the mapping can be
 * read in one place and tested without a screen.
 *
 * Two rules the mapping keeps:
 *  - A configured source travels as its record minus any secret -- id, type, name and the
 *    credential reference the platform resolves. Never a password, never an app password.
 *  - Parameter names are the contract's own (`TEMPLATE_PARAMETERS`): "source mailbox",
 *    "per-run cap". The compiler reads them by that name and records where each came from.
 */

/** The interface's poll intervals and cadences, as the contract's `ScheduleCadence`. */
export function cadenceOf(schedule) {
 const alias = { "1m": "every_minute", "5m": "every_5_minutes", "15m": "every_15_minutes", hourly: "hourly", manual: "once" };
 const value = alias[schedule] || schedule || "daily";
 return ["once", "daily", "weekly", "monthly", "hourly", "every_15_minutes", "every_5_minutes", "every_minute"].includes(value) ? value : "daily";
}

/** A configured source by its display name (what the form stored) or id. Secrets stripped. */
export function sourceRef(nameOrId, sources = []) {
 const found = (sources || []).find((s) => s.id === nameOrId || s.name === nameOrId);
 if (!found) return nameOrId || null;
 return { id: found.id, sourceType: found.sourceType, name: found.name, credentialRef: found.credentialRef || "" };
}

/** `TEMPLATE_PARAMETERS[document_to_record]`, filled from the spec. */
export function documentParameters(spec, sources = []) {
 const filter = spec.messageFilter || {};
 return {
  "source mailbox": sourceRef(spec.mailbox, sources),
  "message filter": { unseen_only: filter.unread !== false, subject_contains: filter.senderPattern || null, since_days: 7 },
  "document type": spec.documentType || "pdf",
  "extraction template": (findTemplate(spec)?.source !== "shipped" && findTemplate(spec)) || spec.extractionTemplate || "",
  "example document": spec.exampleDocument?.name || spec.exampleDocument || null,
  "master data": spec.masterData || {},
  "match thresholds": spec.matchThresholds || {},
  guardrails: normalizeRules(spec.recordGuardrails).map((g) => ({ id: g.id, kind: g.kind, label: g.label, field: g.field, line_field: g.line_field, params: g.params, exception: g.exception })),
  recipient: spec.exceptionsTo || "",
  acknowledge: spec.acknowledge !== false,
  "write target": sourceRef(spec.writeback, sources),
  "target objects": (findMapping(spec)?.source !== "shipped" && findMapping(spec)) || spec.targetObjects || "",
  "created as": spec.createdAs || "draft",
  "per-run cap": spec.blastRadius || "",
  rollback: spec.rollback || "",
  schedule: cadenceOf(spec.schedule),
 };
}

/** The KPI shapes' parameters, best effort: the compiler names what is still missing. */
export function kpiParameters(spec, sources = []) {
 return {
  metrics: spec.metricIds || [],
  metric: (spec.metricIds || [])[0] || "",
  dimensions: spec.dimensions || [],
  comparison: spec.comparison || null,
  scope: spec.scope || "",
  operator: spec.condition?.operator || "",
  threshold: spec.condition?.threshold ?? "",
  severity: spec.severity || "",
  recipient: spec.notify || "",
  candidates: spec.candidates || null,
  guardrails: spec.guardrails || [],
  approver: spec.approver || "",
  "write target": sourceRef(spec.writeback, sources),
  "timeout behaviour": spec.onTimeout || "cancel",
  "per-run cap": spec.blastRadius || "",
  rollback: spec.rollback || "",
  schedule: cadenceOf(spec.schedule),
 };
}

export function parametersFor(spec, sources = []) {
 return spec.template === "document_to_record" ? documentParameters(spec, sources) : kpiParameters(spec, sources);
}

/** `UseCaseWrite`: the catalog row. */
export function useCaseWrite(spec) {
 const shape = SHAPES[spec.shape] || SHAPES.inform;
 return {
  title: spec.title?.trim() || spec.goal?.trim() || "Untitled use case",
  description: spec.goal || "",
  business_area: spec.businessArea || "sales",
  autonomy: spec.shape === "act" ? "autopilot" : spec.autonomy === "autopilot" ? "autopilot" : "copilot",
  level: shape.level || 1,
  tags: [spec.category, spec.template].filter(Boolean),
 };
}

/** `Spec`: what the form produced, marked as the form's. */
export function specPayload(spec, useCaseId, sources = []) {
 const concepts = spec.template === "document_to_record"
  ? [{ concept: "Customer", role: "subject" }, { concept: "Product", role: "subject" }, { concept: "Order", role: "measure" }]
  : [];
 return {
  use_case_id: useCaseId,
  question: spec.goal || spec.title || "",
  business_area: spec.businessArea || "sales",
  entities: concepts,
  kpi_identifiers: spec.template === "document_to_record" ? [] : spec.metricIds || [],
  grain: "overall",
  notes: "",
  extracted_by: { identifier: "interface.form", version: "1" },
  shape: spec.shape || null,
  template: spec.template || null,
  parameters: parametersFor(spec, sources),
 };
}

/** `UseCaseDraft`: the builder's state the contract already knew how to store. */
export function draftPayload(spec) {
 return {
  version: 1,
  step1: { title: spec.title || spec.goal || "", description: spec.goal || "", category: spec.businessArea || "sales", autonomy: spec.shape === "act" ? "autopilot" : "copilot" },
  step2: { primary_question: spec.goal || "" },
  step3: {},
  step8: { schedule: cadenceOf(spec.schedule), recipient: spec.exceptionsTo || spec.notify || "" },
 };
}

/** `ActivationRequest`. */
export function activationPayload(spec) {
 const shape = SHAPES[spec.shape] || SHAPES.inform;
 return { level: shape.level || 1, autonomy: spec.shape === "act" ? "autopilot" : "copilot", schedule: cadenceOf(spec.schedule) };
}

/** The interface's schedule label for a contract cadence. */
export function cadenceLabel(cadence) {
 const poll = POLL_INTERVALS.find((p) => cadenceOf(p.value) === cadence);
 return poll ? poll.label : cadence?.replace(/_/g, " ") || "";
}

/**
 * A platform run's `result` (the document batch) in the shape the rehearse table renders:
 * one row per email with what it reached and why. The same rows for a rehearsal and a live
 * run; only `status` and the record's url differ.
 */
export function runResultRows(result) {
 if (!result) return null;
 const rows = [];
 for (const d of result.documents || []) {
  const m = d.message || {};
  const w = d.written || {};
  const resolved = d.resolved || {};
  const site = resolved.ship_to?.row || {};
  const created = w.status === "written" || w.status === "previewed";
  rows.push({
   id: m.id || `${m.sender}-${d.extracted?.po_number}`,
   from: m.sender,
   subject: m.subject,
   po: d.extracted?.po_number,
   status: created ? "created" : "held",
   classification: null,
   detail: null,
   steps: ["screened", "extracted", "resolved", "validated", "built", w.status === "written" ? "written" : "previewed"],
   account: resolved.customer?.resolved ? { name: resolved.customer.name } : null,
   shipTo: { street: site.street || resolved.ship_to?.name || "", postcode: site.postcode || "" },
   lines: (resolved.lines || []).map((l) => ({ qty: l.quantity, desc: l.description, score: l.product?.score ?? 0, productRec: { name: l.product?.name || l.description, uom: l.product?.uom || l.unit_of_measure || "" } })),
   payload: d.record ? { Order: d.record.fields, OrderItems: (d.record.children || []).map((c) => c.fields) } : null,
   url: w.url || null,
   recordId: w.id || null,
   recordStatus: w.record_status || null,
  });
 }
 for (const e of result.exceptions || []) {
  rows.push({
   id: e.message_id || `${e.sender}-${e.subject}`,
   from: e.sender,
   subject: e.subject,
   po: e.po_number || "",
   status: e.class === "PER_RUN_CAP" ? "held" : "exception",
   classification: e.class,
   detail: e.detail,
   steps: [e.stage || "screened"],
   account: null,
   shipTo: null,
   lines: [],
   payload: null,
  });
 }
 rows.sort((a, b) => String(a.subject).localeCompare(String(b.subject)));
 const byClass = {};
 for (const r of rows) if (r.status === "exception") byClass[r.classification] = (byClass[r.classification] || 0) + 1;
 const created = rows.filter((r) => r.status === "created").length;
 return {
  results: rows,
  total: (result.stats?.received ?? rows.length),
  created,
  held: rows.filter((r) => r.status === "held").length,
  exceptions: rows.filter((r) => r.status === "exception").length,
  byClass,
  straightThrough: rows.length ? Math.round((created / rows.length) * 100) : 0,
  scanned: byClass.SCANNED_DOCUMENT || 0,
  written: rows.filter((r) => r.recordId).length,
  notify: result.status ? { status: result.status, to: result.to } : null,
  rollback: result.rollback || null,
 };
}


/** The interface's schedule value for a contract cadence (the reverse of `cadenceOf`). */
export function scheduleFromCadence(cadence) {
 const back = { every_minute: "1m", every_5_minutes: "5m", every_15_minutes: "15m", hourly: "hourly", once: "manual" };
 return back[cadence] || cadence || "daily";
}

/**
 * A builder spec rebuilt from what the platform holds (K20): the reverse of `documentParameters`
 * for the document template, so "Edit" on a use-case page reopens the same form. Sources come
 * back as their display names, custom templates and mappings as their definitions; what the
 * platform never stored (the owner, the example document) stays for the person to fill.
 */
export function specFromPlatform(useCase, platformSpec, base) {
 const p = platformSpec?.parameters || {};
 const name = (v) => (v && typeof v === "object" ? v.name || v.id || "" : v || "");
 const template = p["extraction template"];
 const mapping = p["target objects"];
 const filter = p["message filter"] || {};
 const next = {
  ...base,
  title: useCase.title || base.title,
  goal: platformSpec?.question || useCase.description || base.goal,
  businessArea: platformSpec?.business_area || useCase.business_area || base.businessArea,
  shape: platformSpec?.shape || base.shape,
  template: platformSpec?.template || base.template,
  category: platformSpec?.template === "document_to_record" ? "process_automation" : base.category,
  platform: { ...(base.platform || {}), useCaseId: useCase.id, version: useCase.version, status: useCase.status, parameters: p },
 };
 if (platformSpec?.template !== "document_to_record") return next;
 return {
  ...next,
  mailbox: name(p["source mailbox"]),
  schedule: scheduleFromCadence(p.schedule || useCase.schedule),
  messageFilter: { ...(base.messageFilter || {}), unread: filter.unseen_only !== false, senderPattern: filter.subject_contains || "" },
  documentType: p["document type"] || "pdf",
  extractionTemplate: template && typeof template === "object" ? template.id : template || "",
  extractionTemplateDef: template && typeof template === "object" ? template : null,
  masterData: p["master data"] && typeof p["master data"] === "object" && !p["master data"].source ? p["master data"] : base.masterData,
  matchThresholds: p["match thresholds"] || base.matchThresholds,
  recordGuardrails: Array.isArray(p.guardrails) ? p.guardrails : [],
  exceptionsTo: typeof p.recipient === "string" ? p.recipient : p.recipient?.who || "",
  acknowledge: p.acknowledge !== false,
  writeback: name(p["write target"]),
  targetObjects: mapping && typeof mapping === "object" ? mapping.id : mapping || "",
  recordMappingDef: mapping && typeof mapping === "object" ? mapping : null,
  createdAs: p["created as"] || "draft",
  blastRadius: p["per-run cap"] || "",
  rollback: p.rollback || "",
 };
}

/**
 * What one run was worth (K20): counts by outcome, the exception classes, and the order
 * value of the records it created -- summed from each record's line children where the
 * mapping wrote a quantity and a unit price. Real numbers from the run's own result; a run
 * that polled and found nothing is `idle`.
 */
export function runStats(run) {
 const result = run?.result || {};
 const docs = result.documents || [];
 const exceptions = result.exceptions || [];
 const received = result.stats?.received ?? docs.length + exceptions.length;
 const written = docs.filter((d) => d.written?.status === "written");
 const previewed = docs.filter((d) => d.written?.status === "previewed");
 const held = exceptions.filter((e) => e.class === "PER_RUN_CAP").length;
 const value = docs.reduce((sum, d) => sum + (d.record?.children || []).reduce((s, c) => s + (Number(c.fields?.Quantity) || 0) * (Number(c.fields?.UnitPrice) || 0), 0), 0);
 const byClass = {};
 for (const e of exceptions) byClass[e.class] = (byClass[e.class] || 0) + 1;
 const durationMs = run?.finished_at && run?.started_at ? new Date(run.finished_at) - new Date(run.started_at) : null;
 return {
  received, created: written.length, previewed: previewed.length, exceptions: exceptions.length - held, held,
  value, byClass, idle: received === 0, durationMs,
  records: written.map((d) => ({ po: d.extracted?.po_number, url: d.written?.url, customer: d.resolved?.customer?.name })),
  notify: result.status || null, recipients: result.to || [],
 };
}

export const money = (n) => n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });
