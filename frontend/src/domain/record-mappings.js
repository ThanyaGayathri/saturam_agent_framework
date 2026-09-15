/**
 * Record mappings as data (K18): how a resolved document becomes the target's objects.
 *
 * A mapping is a parent object with field rows -- each a target field and the source
 * expressions its value comes from -- an optional child repeated per line, the status field
 * "created as" sets, and the rollback action. The two Salesforce mappings that ship are rows of
 * this shape; a mapping the person assembles over the org's live fields is the same shape and
 * runs the same interpreter on the platform. Mirrors `product/egpt_retail/documents/mapping.py`.
 *
 * Custom mappings live in the workspace catalogue (browser storage) and the chosen one is
 * embedded on the spec as `recordMappingDef`, so the spec is self-contained on the platform.
 */
import { readLocalJson, writeLocalJson } from "../storage/local-json";
import { headerFields, lineFields } from "./extraction-templates";

export const MAPPING_CATALOGUE_KEY = "retailgpt:record-mappings:v1";

const row = (target, source, required = false, transform = null, label = "") => ({ target, source: Array.isArray(source) ? source : [source], required, transform, label });

export const SHIPPED_MAPPINGS = [
 {
  id: "order_orderitem", label: "Order + OrderItem (standard)", target: "salesforce", object: "Order", record: "order", source: "shipped",
  fields: [
   row("AccountId", "customer.id", true, null, "the resolved customer"),
   row("PoNumber", "po_number", true),
   row("PoDate", ["po_date", "$today"], false, "date"),
   row("EffectiveDate", ["po_date", "$today"], true, "date_not_before_today"),
   row("Pricebook2Id", "$pricebook_id", true, null, "the standard pricebook"),
   row("ShippingStreet", "ship_to.street", true, null, "the resolved ship-to"),
   row("ShippingCity", "ship_to.city"), row("ShippingState", ["ship_to.state_name", "ship_to.state"]), row("ShippingPostalCode", "ship_to.postcode"), row("ShippingCountry", ["ship_to.country", "=United States"]),
   row("Description", "delivery_instructions", false, "text:255"),
  ],
  child: { object: "OrderItem", parent_field: "OrderId", fields: [row("PricebookEntryId", "product.pricebook_entry_id", true, null, "the resolved product's price entry"), row("Quantity", "line.quantity", true, "number"), row("UnitPrice", ["line.unit_price", "product.unit_price"], true, "number"), row("ServiceDate", ["line.line_due_date", "due_date"], false, "date"), row("Description", "line.description", false, "text:255")] },
  status_field: "Status", status_values: { draft: "Draft", active: "Activated" }, rollback_action: "delete",
 },
 {
  id: "opportunity", label: "Opportunity + Products", target: "salesforce", object: "Opportunity", record: "opportunity", source: "shipped",
  fields: [row("AccountId", "customer.id", true, null, "the resolved customer"), row("Name", ["po_number", "customer_name"], true, "text:120"), row("CloseDate", ["due_date", "$today"], true, "date_not_before_today"), row("StageName", "=Prospecting", true), row("Pricebook2Id", "$pricebook_id", true, null, "the standard pricebook"), row("Description", "delivery_instructions", false, "text:255")],
  child: { object: "OpportunityLineItem", parent_field: "OpportunityId", fields: [row("PricebookEntryId", "product.pricebook_entry_id", true), row("Quantity", "line.quantity", true, "number"), row("UnitPrice", ["line.unit_price", "product.unit_price"], true, "number")] },
  status_field: null, status_values: { draft: "Draft", active: "Activated" }, rollback_action: "delete",
 },
];

export const TRANSFORMS = [
 { value: "", label: "as is" },
 { value: "date", label: "date (ISO)" },
 { value: "date_not_before_today", label: "date, not before today" },
 { value: "number", label: "number" },
 { value: "text:255", label: "text, 255 chars" },
 { value: "text:80", label: "text, 80 chars" },
];

const slugify = (t) => String(t || "").trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");

export function normalizeRow(raw) {
 if (!raw) return null;
 if (typeof raw === "string") { const m = raw.match(/^\s*([^←<=]+?)\s*(?:←|<-|=)\s*(.*)$/); return m ? row(m[1].trim(), m[2].split("|").map((s) => s.trim()).filter(Boolean)) : null; }
 if (!raw.target) return null;
 const source = Array.isArray(raw.source) ? raw.source : String(raw.source || "").split("|");
 return row(String(raw.target).trim(), source.map((s) => String(s).trim()).filter(Boolean), !!raw.required, raw.transform || null, raw.label || "");
}

export function normalizeMapping(raw) {
 if (!raw) return null;
 const label = String(raw.label || raw.object || "Mapping").trim();
 const fields = (raw.fields || []).map(normalizeRow).filter(Boolean);
 if (!fields.length && !raw.object) return null;
 const child = raw.child && raw.child.object ? { object: String(raw.child.object), parent_field: String(raw.child.parent_field || `${raw.object}Id`), fields: (raw.child.fields || []).map(normalizeRow).filter(Boolean) } : null;
 return {
  id: slugify(raw.id || label), label, target: String(raw.target || "salesforce").toLowerCase(), object: String(raw.object || ""), record: String(raw.record || "") || slugify(raw.object || ""),
  source: raw.source || "authored", fields, child, status_field: raw.status_field || null,
  status_values: raw.status_values && typeof raw.status_values === "object" ? raw.status_values : { draft: "Draft", active: "Activated" }, rollback_action: raw.rollback_action || "delete",
 };
}

export function loadMappingCatalogue() {
 const custom = readLocalJson(MAPPING_CATALOGUE_KEY, []);
 const rows = Array.isArray(custom) ? custom.map(normalizeMapping).filter(Boolean) : [];
 const seen = new Set(SHIPPED_MAPPINGS.map((m) => m.id));
 return [...SHIPPED_MAPPINGS, ...rows.filter((m) => !seen.has(m.id) && seen.add(m.id))];
}
export function saveMappingToCatalogue(raw) {
 const mapping = normalizeMapping(raw);
 if (!mapping || SHIPPED_MAPPINGS.some((m) => m.id === mapping.id)) return mapping;
 const custom = readLocalJson(MAPPING_CATALOGUE_KEY, []);
 writeLocalJson(MAPPING_CATALOGUE_KEY, [...(Array.isArray(custom) ? custom : []).filter((m) => m && m.id !== mapping.id), mapping]);
 return mapping;
}
export function removeMappingFromCatalogue(id) {
 const custom = readLocalJson(MAPPING_CATALOGUE_KEY, []);
 writeLocalJson(MAPPING_CATALOGUE_KEY, (Array.isArray(custom) ? custom : []).filter((m) => m && m.id !== id));
}

/** The mapping a spec names: the one embedded on the spec, else the catalogue's. */
export function findMapping(spec, catalogue = null) {
 if (!spec?.targetObjects) return null;
 if (spec.recordMappingDef && spec.recordMappingDef.id === spec.targetObjects) return normalizeMapping(spec.recordMappingDef);
 return (catalogue || loadMappingCatalogue()).find((m) => m.id === spec.targetObjects) || null;
}
export const mappingLabel = (spec, catalogue = null) => findMapping(spec, catalogue)?.label || null;

/** What a row's source may be, for the editor: the template's fields, the resolved entities,
 *  run values, and a literal. Grouped for a select. */
export function sourceOptions(template, { lineScope = false } = {}) {
 const header = headerFields(template).map((f) => ({ value: f.name, label: f.name, group: "Document header" }));
 const lines = lineScope ? lineFields(template).map((f) => ({ value: `line.${f.name}`, label: `line.${f.name}`, group: "Document line" })) : [];
 const resolved = [
  { value: "customer.id", label: "customer.id", group: "Resolved" }, { value: "customer.name", label: "customer.name", group: "Resolved" },
  { value: "ship_to.street", label: "ship_to.street", group: "Resolved" }, { value: "ship_to.city", label: "ship_to.city", group: "Resolved" }, { value: "ship_to.state", label: "ship_to.state", group: "Resolved" }, { value: "ship_to.postcode", label: "ship_to.postcode", group: "Resolved" }, { value: "ship_to.country", label: "ship_to.country", group: "Resolved" },
  ...(lineScope ? [{ value: "product.id", label: "product.id", group: "Resolved" }, { value: "product.pricebook_entry_id", label: "product.pricebook_entry_id", group: "Resolved" }, { value: "product.unit_price", label: "product.unit_price", group: "Resolved" }, { value: "product.name", label: "product.name", group: "Resolved" }] : []),
 ];
 const run = [{ value: "$pricebook_id", label: "$pricebook_id (standard pricebook)", group: "Run" }, { value: "$today", label: "$today", group: "Run" }];
 return [...header, ...lines, ...resolved, ...run];
}

/** Required target fields no row covers, given the object's live description. */
export function uncoveredRequired(described, rows) {
 if (!described) return [];
 const covered = new Set((rows || []).filter((r) => r.source?.length).map((r) => r.target));
 return (described.fields || []).filter((f) => f.required && !covered.has(f.name) && f.name.toLowerCase() !== "status").map((f) => f.name);
}

// --- the interface's preview of the platform's interpreter ------------------------------------
const lookup = (path, ctx) => {
 if (path.startsWith("=")) return path.slice(1);
 if (path.startsWith("$")) return ctx.$?.[path.slice(1)] ?? null;
 const [head, ...rest] = path.split(".");
 if (!rest.length) return ctx.extracted?.[head] ?? null;
 return ctx[head]?.[rest.join(".")] ?? null;
};
const transformValue = (v, t, today) => {
 if (v == null || v === "") return null;
 if (!t) return v;
 if (t === "date") { const d = new Date(v); return Number.isNaN(d.getTime()) ? null : d.toISOString().slice(0, 10); }
 if (t === "date_not_before_today") { const d = new Date(v); const base = Number.isNaN(d.getTime()) ? today : d; return (base < today ? today : base).toISOString().slice(0, 10); }
 if (t === "number") { const n = Number(v); return Number.isNaN(n) ? null : n; }
 if (t.startsWith("text:")) return String(v).slice(0, Number(t.split(":")[1]));
 return v;
};
const fill = (rows, ctx, today) => {
 const fields = {}; const missing = [];
 for (const r of rows) {
  let value = null;
  for (const s of r.source) { value = transformValue(lookup(s, ctx), r.transform, today); if (value != null) break; }
  if (value == null) { if (r.required) missing.push(`${r.target} (${r.label || r.source.join(" or ")})`); continue; }
  fields[r.target] = value;
 }
 return { fields, missing };
};

/** `ctx` = { extracted, customer, ship_to, lines: [{...line, product}], $: {pricebook_id, today}, createdAs } */
export function applyMapping(mapping, ctx) {
 const m = normalizeMapping(mapping);
 const today = ctx.today || new Date();
 const base = { extracted: ctx.extracted || {}, customer: ctx.customer || {}, ship_to: ctx.ship_to || {}, $: { pricebook_id: ctx.pricebook_id ?? "$pricebook_id", today: today.toISOString().slice(0, 10) } };
 const parent = fill(m.fields, base, today);
 if (m.status_field) parent.fields[m.status_field] = m.status_values[ctx.createdAs === "active" ? "active" : "draft"] || "Draft";
 const children = []; const missing = [...parent.missing];
 if (m.child) for (const line of ctx.lines || []) { const c = fill(m.child.fields, { ...base, line, product: line.product || {} }, today); missing.push(...c.missing.map((x) => `line ${line.line_no}: ${x}`)); if (!c.missing.length) children.push(c.fields); }
 return { object: m.object, fields: parent.fields, child: m.child?.object || null, children, missing };
}
