/**
 * Business guardrails as rules (K17).
 *
 * A guardrail is a *kind* the platform can evaluate, applied to a field of the template, with
 * parameters and the exception it raises. Eight kinds cover every check a document-driven use
 * case has asked for; the six purchase-order guardrails are a preset of them with their old ids
 * and exception classes, so a use case authored before this change reads the same.
 *
 * `suggestRules(template)` proposes rules from a template's own field names, so a document kind
 * onboarded tomorrow gets its checks without anyone writing one. Mirrors
 * `product/egpt_retail/documents/guardrails.py`; `evaluateRules` is the interface's own preview
 * of the same interpreter, used by the rehearse simulation.
 */
import { MASTER_DATA_ROLES } from "./use-case-autonomy";
import { isShippedTemplate, headerFields, lineFields } from "./extraction-templates";

export const RULE_KINDS = [
 { id: "sender_known", label: "Sender known", check: "The sender's email domain matches no customer or contact", scope: "none", params: {}, needs: "customer", exception: "UNKNOWN_SENDER", priority: 0 },
 { id: "flagged_item", label: "Flagged item", check: "A resolved {field} is flagged restricted or inactive", scope: "line", params: {}, needs: "product", exception: "RESTRICTED_ITEM", priority: 1 },
 { id: "duplicate_reference", label: "Duplicate reference", check: "A record for this customer with the same {field} already exists (last {lookback_months} months)", scope: "header", params: { lookback_months: 12 }, needs: "orderHistory", exception: "DUPLICATE_REFERENCE", priority: 2 },
 { id: "date_window", label: "Date window", check: "{field} is in the past, unparseable, or more than {max_days} days out", scope: "header", params: { max_days: 180 }, needs: null, exception: "DATE_INVALID", priority: 3 },
 { id: "numeric_range", label: "Numeric range", check: "{field} is outside {min} to {max}", scope: "any", params: { min: 0, max: 1000000 }, needs: null, exception: "OUT_OF_RANGE", priority: 4 },
 { id: "total_matches_lines", label: "Total matches lines", check: "{field} differs from the sum of the lines by more than {tolerance}", scope: "header", params: { tolerance: 0.01 }, needs: null, exception: "TOTAL_MISMATCH", priority: 5 },
 { id: "quantity_outlier", label: "Quantity outlier", check: "{field} is more than {multiplier} × this customer's median for the item", scope: "line", params: { multiplier: 3 }, needs: "orderHistory", exception: "QUANTITY_ANOMALY", priority: 6 },
 { id: "first_time_item", label: "First-time item", check: "This customer has never ordered this {field} (last {lookback_months} months)", scope: "line", params: { lookback_months: 12 }, needs: "orderHistory", exception: "FIRST_TIME_ITEM", priority: 7 },
];
export const kindOf = (id) => RULE_KINDS.find((k) => k.id === id) || null;

/** Parameter labels and units for the form. */
export const PARAM_META = {
 lookback_months: { label: "lookback", unit: "months" },
 multiplier: { label: "N", unit: "×" },
 max_days: { label: "max", unit: "days" },
 min: { label: "min", unit: "" },
 max: { label: "max", unit: "" },
 tolerance: { label: "tolerance", unit: "" },
};

/** The purchase-order preset: the six, with their original ids and exception classes. */
export const PURCHASE_ORDER_RULES = [
 { id: "duplicate_po", kind: "duplicate_reference", label: "Duplicate PO", field: "po_number", params: { lookback_months: 12 }, exception: "DUPLICATE_PO" },
 { id: "unknown_sender", kind: "sender_known", label: "Unknown sender", field: null, params: {}, exception: "UNKNOWN_SENDER" },
 { id: "first_time_product", kind: "first_time_item", label: "First-time product", field: "description", line_field: "product", params: { lookback_months: 12 }, exception: "FIRST_TIME_PRODUCT" },
 { id: "quantity_anomaly", kind: "quantity_outlier", label: "Quantity anomaly", field: "quantity", line_field: "product", params: { multiplier: 3 }, exception: "QUANTITY_ANOMALY" },
 { id: "due_date_sanity", kind: "date_window", label: "Due-date sanity", field: "due_date", params: { max_days: 180 }, exception: "DUE_DATE_INVALID" },
 { id: "restricted_product", kind: "flagged_item", label: "Restricted product", field: "description", line_field: "product", params: {}, exception: "RESTRICTED_PRODUCT" },
];
const PRESET = Object.fromEntries(PURCHASE_ORDER_RULES.map((r) => [r.id, r]));
const LEGACY_PARAM = { lookbackMonths: "lookback_months", maxDays: "max_days", multiplier: "multiplier" };

/** A rule row from any shape the spec has carried: a preset id, `{id, param}`, or a full rule. */
export function normalizeRule(row) {
 if (!row) return null;
 const raw = typeof row === "string" ? { id: row } : row;
 const preset = PRESET[raw.id];
 const kind = kindOf(raw.kind) || (preset && kindOf(preset.kind));
 if (!kind) return null;
 const params = { ...(preset?.params || kind.params) };
 for (const [k, v] of Object.entries(raw.params || {})) { const key = LEGACY_PARAM[k] || k; if (v !== "" && v != null && !Number.isNaN(Number(v))) params[key] = Number(v); }
 if (raw.param != null && raw.param !== "" && Object.keys(params).length) params[Object.keys(params)[0]] = Number(raw.param);
 const field = raw.field ?? preset?.field ?? null;
 return {
  id: raw.id || `${kind.id}_${field || "rule"}`,
  kind: kind.id,
  label: raw.label || preset?.label || `${kind.label}${field ? `: ${field.replace(/_/g, " ")}` : ""}`,
  field,
  line_field: raw.line_field ?? preset?.line_field ?? (kind.scope === "line" && kind.id !== "numeric_range" ? "product" : null),
  params,
  exception: raw.exception || preset?.exception || kind.exception,
 };
}
export const normalizeRules = (rows) => (rows || []).map(normalizeRule).filter(Boolean);
export const needsOf = (rule) => kindOf(rule?.kind)?.needs || null;

/** The check in plain words, with the field and parameters filled in. */
export function ruleCheck(rule) {
 const kind = kindOf(rule.kind);
 if (!kind) return "";
 const itemKinds = kind.id === "flagged_item" || kind.id === "first_time_item";
 let text = kind.check.replace("{field}", !rule.field || itemKinds ? "item" : rule.field.replace(/_/g, " "));
 for (const [k, v] of Object.entries(rule.params || {})) text = text.replace(`{${k}}`, String(v));
 return text.charAt(0).toUpperCase() + text.slice(1);
}

const NUMBER_LIKE = /(number|reference|ref|id|no)$/;
const DATE_LIKE = /(date|due|deadline)/;
const FUTURE_DATE = /(due|deliver|requested|deadline|expected|ship)/;
const TOTAL_LIKE = /^(order_)?total$|_total$|^amount$/;
const QUANTITY_LIKE = /^(quantity|qty|units?)$/;
const LINE_TOTAL_LIKE = /^(line_total|line_amount|amount)$/;
const ITEM_LIKE = /^(description|product|item|sku|material)$/;
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);

/** Rules a template's own field names call for. Mirrors the platform's `suggest_rules`. */
export function suggestRules(template) {
 if (!template) return [];
 if (template.id === "purchase_order" && isShippedTemplate(template)) return PURCHASE_ORDER_RULES.map(normalizeRule);
 const header = headerFields(template).map((f) => f.name);
 const lines = lineFields(template).map((f) => f.name);
 const out = [{ id: "sender_known", kind: "sender_known", label: "Sender known", field: null, params: {} }];
 const reference = header.find((n) => NUMBER_LIKE.test(n) && !n.startsWith("line"));
 if (reference) out.push({ id: `duplicate_${reference}`, kind: "duplicate_reference", label: `Duplicate ${reference.replace(/_/g, " ")}`, field: reference, params: { lookback_months: 12 } });
 for (const name of header) if (DATE_LIKE.test(name) && FUTURE_DATE.test(name)) out.push({ id: `date_window_${name}`, kind: "date_window", label: `${cap(name.replace(/_/g, " "))} sanity`, field: name, params: { max_days: 180 } });
 const item = lines.find((n) => ITEM_LIKE.test(n));
 if (item) {
  out.push({ id: "flagged_item", kind: "flagged_item", label: "Restricted item", field: item, line_field: "product", params: {} });
  out.push({ id: "first_time_item", kind: "first_time_item", label: "First-time item", field: item, line_field: "product", params: { lookback_months: 12 } });
 }
 const quantity = lines.find((n) => QUANTITY_LIKE.test(n));
 if (quantity) out.push({ id: `outlier_${quantity}`, kind: "quantity_outlier", label: `${cap(quantity)} anomaly`, field: quantity, line_field: "product", params: { multiplier: 3 } });
 const total = header.find((n) => TOTAL_LIKE.test(n));
 const lineTotal = lines.find((n) => LINE_TOTAL_LIKE.test(n));
 if (total && lineTotal) out.push({ id: `total_${total}`, kind: "total_matches_lines", label: `${cap(total.replace(/_/g, " "))} matches the lines`, field: total, line_field: lineTotal, params: { tolerance: 0.01 } });
 return out.map(normalizeRule);
}

/** Which rules can run given the master data bound on the form. */
export function unfedRules(rules, masterData) {
 const m = masterData || {};
 return normalizeRules(rules).filter((r) => needsOf(r) && !m[needsOf(r)]).map((r) => ({ rule: r, needs: MASTER_DATA_ROLES.find((x) => x.id === needsOf(r))?.label || needsOf(r) }));
}

const median = (xs) => { const s = [...xs].sort((a, b) => a - b); return s.length ? s[Math.floor(s.length / 2)] : null; };
const parseDate = (v) => { if (v == null || v === "") return null; const d = new Date(v); return Number.isNaN(d.getTime()) ? null : d; };
const daysFrom = (today, d) => Math.round((d - today) / 86400000);

/**
 * The interface's preview of the platform's rule interpreter over one document.
 * `doc` = { sender, header: {...fields}, lines: [{...fields, item: {id, name, restricted, resolved}}],
 *           customer: {id, resolved, method} }; `ctx` = { history: [{account_id, reference, product_id, quantity}],
 *           masterData, seen: Set<string>, today: Date }.
 * Returns the first failing rule's `{ class, guardrail, detail }` in priority order, or null.
 */
export function evaluateRules(rules, doc, ctx) {
 const ordered = normalizeRules(rules).sort((a, b) => (kindOf(a.kind)?.priority ?? 9) - (kindOf(b.kind)?.priority ?? 9));
 const md = ctx.masterData || {};
 const today = ctx.today || new Date();
 const account = doc.customer?.resolved ? doc.customer.id : null;
 const history = (ctx.history || []).filter((h) => h.account_id === account);
 for (const rule of ordered) {
  const needs = needsOf(rule);
  if (needs && !md[needs]) continue; // skipped, never passed
  const p = rule.params || {};
  const fail = (detail) => ({ class: rule.exception, guardrail: rule.id, detail });
  const label = (rule.field || "").replace(/_/g, " ");
  switch (rule.kind) {
   case "sender_known":
    if (!doc.customer?.resolved || !["sender_domain", "contact_email"].includes(doc.customer.method)) return fail(`${doc.sender || "the sender"} matches no customer or contact.`);
    break;
   case "duplicate_reference": {
    const ref = String(doc.header?.[rule.field] ?? "").trim().toLowerCase();
    if (account && ref) {
     if (ctx.seen?.has(`${account}:${ref}`)) return fail(`${label} ${doc.header[rule.field]} already arrived earlier in this run.`);
     if (history.some((h) => String(h[rule.field] ?? h.reference ?? "").toLowerCase() === ref)) return fail(`${label} ${doc.header[rule.field]} already exists for this customer.`);
    }
    break;
   }
   case "first_time_item":
    if (account) { const ordered = new Set(history.map((h) => h.product_id)); for (const line of doc.lines || []) if (line.item?.resolved && !ordered.has(line.item.id)) return fail(`${line.item.name} never ordered by this customer before.`); }
    break;
   case "quantity_outlier":
    if (account) for (const line of doc.lines || []) {
     if (!line.item?.resolved) continue;
     const past = history.filter((h) => h.product_id === line.item.id && h.quantity != null).map((h) => Number(h.quantity));
     const q = Number(line[rule.field ?? "quantity"]);
     if (past.length && !Number.isNaN(q) && q > (p.multiplier ?? 3) * median(past)) return fail(`${label} ${q} is over ${p.multiplier ?? 3} × the customer's median of ${median(past)}.`);
    }
    break;
   case "date_window": {
    const raw = doc.header?.[rule.field];
    if (raw != null && raw !== "") { const d = parseDate(raw); if (!d) return fail(`${label} "${raw}" is unparseable.`); const days = daysFrom(today, d); if (days < 0) return fail(`${label} is in the past.`); if (days > (p.max_days ?? 180)) return fail(`${label} is more than ${p.max_days ?? 180} days out.`); }
    break;
   }
   case "flagged_item":
    for (const line of doc.lines || []) if (line.item?.resolved && (line.item.restricted || line.item.active === false)) return fail(`${line.item.name} is restricted.`);
    break;
   case "numeric_range": {
    const values = [];
    if (rule.field in (doc.header || {})) values.push(["", doc.header[rule.field]]);
    for (const line of doc.lines || []) if (rule.field in line) values.push([`line ${line.line_no}: `, line[rule.field]]);
    for (const [prefix, v] of values) { const n = Number(v); if (v == null || Number.isNaN(n)) continue; if ((p.min != null && n < p.min) || (p.max != null && n > p.max)) return fail(`${prefix}${label} ${n} is outside ${p.min} to ${p.max}.`); }
    break;
   }
   case "total_matches_lines": {
    const total = Number(doc.header?.[rule.field]);
    const parts = (doc.lines || []).map((l) => Number(l[rule.line_field || "line_total"]));
    if (!Number.isNaN(total) && parts.length && parts.every((x) => !Number.isNaN(x))) { const sum = parts.reduce((a, b) => a + b, 0); const tol = p.tolerance ?? 0.01; if (Math.abs(sum - total) > Math.max(tol, tol * Math.abs(total))) return fail(`${label} ${total} differs from the lines' sum ${sum}.`); }
    break;
   }
   default:
  }
 }
 return null;
}
