/**
 * Extraction templates as data (K17).
 *
 * A template is the list of fields the AI step fills from one kind of document, and what the
 * record created from it is. The three that ship are rows of this shape; a template a person
 * assembles on the form, or the platform drafts from the description and an example, is the
 * same shape and runs the same extractor. Nothing about a template is code, which is what lets
 * a use case onboarded tomorrow bring a document kind the platform has never seen.
 *
 * Custom templates live in the workspace catalogue (browser storage) so the next use case can
 * pick them, and the chosen template is embedded on the spec as `extractionTemplateDef` so the
 * spec is self-contained when it reaches the platform.
 */
import { readLocalJson, writeLocalJson } from "../storage/local-json";

export const CATALOGUE_KEY = "retailgpt:extraction-templates:v1";

/** Shipped document templates. Fields are `name*` when required. */
export const SHIPPED_TEMPLATES = [
 {
  id: "purchase_order", label: "Purchase order", record: "order", source: "shipped",
  header: ["po_number*", "po_date", "customer_name*", "buyer_name", "buyer_email", "ship_to_address*", "bill_to_address", "due_date", "currency", "payment_terms", "order_total", "delivery_instructions"],
  lines: ["line_no*", "customer_part_number", "supplier_product_code", "description*", "quantity*", "unit_of_measure*", "unit_price", "line_total", "line_due_date", "line_notes"],
 },
 { id: "supplier_invoice", label: "Supplier invoice", record: "payable", source: "shipped", header: ["invoice_number*", "invoice_date*", "supplier_name*", "po_reference", "due_date", "currency", "total*"], lines: ["line_no*", "description*", "quantity", "unit_price", "line_total*", "tax"] },
 { id: "return_request", label: "Return request", record: "return", source: "shipped", header: ["rma_reference", "customer_name*", "order_reference*", "reason"], lines: ["line_no*", "product*", "quantity*", "condition"] },
];

export const slugify = (text) => String(text || "").trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "") || "document";

/** A field as the form edits it: `{ name, required, hint }` from either shape. */
export function fieldOf(raw) {
 if (raw && typeof raw === "object") return { name: slugify(raw.name || raw.id), required: !!raw.required, hint: raw.hint || "" };
 const text = String(raw || "").trim();
 return { name: slugify(text.replace(/\*$/, "")), required: text.endsWith("*"), hint: "" };
}

/** The shape the platform reads: fields as `name*` strings. */
export function normalizeTemplate(raw) {
 if (!raw) return null;
 const label = String(raw.label || raw.name || raw.id || "Document").trim();
 const id = slugify(raw.id || label);
 const header = (raw.header || []).map(fieldOf).filter((f) => f.name);
 const lines = (raw.lines || []).map(fieldOf).filter((f) => f.name);
 return {
  id, label,
  record: String(raw.record || raw.creates || "").trim() || id,
  source: raw.source || "authored",
  header: header.map((f) => (f.hint ? f : `${f.name}${f.required ? "*" : ""}`)),
  lines: lines.map((f) => (f.hint ? f : `${f.name}${f.required ? "*" : ""}`)),
 };
}

/** Header and line fields as `{ name, required, hint }` rows. */
export const headerFields = (t) => (t?.header || []).map(fieldOf);
export const lineFields = (t) => (t?.lines || []).map(fieldOf);
export const fieldNames = (t) => [...headerFields(t), ...lineFields(t)].map((f) => f.name);

/** The workspace catalogue: shipped first, then whatever people saved. */
export function loadTemplateCatalogue() {
 const custom = readLocalJson(CATALOGUE_KEY, []);
 const rows = Array.isArray(custom) ? custom.map(normalizeTemplate).filter(Boolean) : [];
 const seen = new Set(SHIPPED_TEMPLATES.map((t) => t.id));
 return [...SHIPPED_TEMPLATES, ...rows.filter((t) => !seen.has(t.id) && seen.add(t.id))];
}

export function saveTemplateToCatalogue(raw) {
 const template = normalizeTemplate(raw);
 if (!template || SHIPPED_TEMPLATES.some((t) => t.id === template.id)) return template;
 const custom = readLocalJson(CATALOGUE_KEY, []);
 const rows = (Array.isArray(custom) ? custom : []).filter((t) => t && t.id !== template.id);
 writeLocalJson(CATALOGUE_KEY, [...rows, template]);
 return template;
}

export function removeTemplateFromCatalogue(id) {
 const custom = readLocalJson(CATALOGUE_KEY, []);
 writeLocalJson(CATALOGUE_KEY, (Array.isArray(custom) ? custom : []).filter((t) => t && t.id !== id));
}

/** The template a spec names: the one embedded on the spec, else the catalogue's. */
export function findTemplate(spec, catalogue = null) {
 if (!spec?.extractionTemplate) return null;
 if (spec.extractionTemplateDef && spec.extractionTemplateDef.id === spec.extractionTemplate) return normalizeTemplate(spec.extractionTemplateDef);
 return (catalogue || loadTemplateCatalogue()).find((t) => t.id === spec.extractionTemplate) || null;
}

export const templateLabel = (spec, catalogue = null) => findTemplate(spec, catalogue)?.label || null;

/** A document kind named in prose that no template covers: "delivery notes" -> Delivery note. */
const KIND_WORDS = "(order|orders|invoice|invoices|note|notes|memo|memos|request|requests|form|forms|claim|claims|statement|statements|receipt|receipts|slip|slips|confirmation|confirmations|advice|quotation|quotations|quote|quotes|ticket|tickets|application|applications)";
const KIND_RE = new RegExp(`\\b((?:[a-z][a-z-]+\\s){0,2}${KIND_WORDS})\\b`, "gi");

export function proposeTemplateNames(text, catalogue = null) {
 const known = new Set((catalogue || loadTemplateCatalogue()).map((t) => t.label.toLowerCase()));
 const stop = new Set(["the", "a", "an", "each", "every", "new", "incoming", "our", "their", "any", "all", "as", "in", "into", "from", "these", "those", "of", "per", "one", "sales", "create", "creates", "turn", "salesforce", "email", "emails", "send", "sends", "with", "and", "or"]);
 const out = [];
 for (const m of text.matchAll(KIND_RE)) {
  const words = m[1].toLowerCase().split(/\s+/).filter((w) => !stop.has(w));
  // A bare kind word ("note", "form") names nothing; a qualified one ("delivery note") does.
  if (words.length < 2) continue;
  const singular = words.join(" ").replace(/(ices)$/, "ice").replace(/(ses)$/, "s").replace(/s$/, "");
  const label = singular.charAt(0).toUpperCase() + singular.slice(1);
  if (known.has(label.toLowerCase()) || out.some((o) => o.label === label)) continue;
  if (["Order", "Sales order", "Record"].includes(label)) continue;
  out.push({ id: slugify(label), label });
 }
 return out;
}

/** The catalogue entry a piece of prose names, by label or id, or null. */
export function matchTemplateInText(text, catalogue = null) {
 const rest = String(text || "").toLowerCase();
 const rows = catalogue || loadTemplateCatalogue();
 const byName = rows.find((t) => new RegExp(`\\b${t.label.toLowerCase()}s?\\b|\\b${t.id.replace(/_/g, " ")}s?\\b`).test(rest));
 if (byName) return byName;
 if (/\bpos?\b|\bpurchase orders?\b/.test(rest)) return rows.find((t) => t.id === "purchase_order") || null;
 return null;
}
