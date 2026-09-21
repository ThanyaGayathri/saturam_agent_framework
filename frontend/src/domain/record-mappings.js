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
import { headerFields, lineFields, uniqueCatalogueId } from "./extraction-templates";
import { MASTER_DATA_ROLES } from "./use-case-autonomy";

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
 let mapping = normalizeMapping(raw);
 if (!mapping) return mapping;
 // A mapping named like a shipped one ("Order + OrderItem") gets an id of its own; the
 // shipped id would make the platform read the shipped rows instead of the person's.
 if (SHIPPED_MAPPINGS.some((m) => m.id === mapping.id)) mapping = normalizeMapping({ ...raw, id: uniqueCatalogueId(mapping.id, SHIPPED_MAPPINGS.map((m) => m.id)), basedOn: raw.basedOn || mapping.id });
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
// --- where a value can come from, in words ---------------------------------------------------
// Three kinds of source, and the form names each as what it is: a field the AI step extracts
// from the document, a value the Resolve step looked up in the target (the roles of
// `MASTER_DATA_ROLES`), or a value of the run. A literal is the fourth: typed, never looked up.
const RESOLVED_FIELDS = {
 customer: [["id", "Salesforce id"], ["name", "name"]],
 contact: [["id", "Salesforce id"], ["name", "name"], ["email", "email"]],
 shipTo: [["street", "street"], ["city", "city"], ["state", "state code"], ["state_name", "state name"], ["postcode", "postcode"], ["country", "country"]],
 product: [["id", "Salesforce id"], ["pricebook_entry_id", "price book entry"], ["unit_price", "list price"], ["name", "name"]],
};
const RUN_VALUES = [["$today", "today's date"], ["$pricebook_id", "the standard price book"]];
export const LITERAL = "=";

/** "ship_to_address → ship-to site street": the document fields a role is resolved from, then the role's field. */
const resolvedLabel = (role, fieldLabel, feeds) => {
 const from = (feeds?.[role.id] || []).join(", ");
 return `${from ? `${from} → ` : ""}${role.label.toLowerCase()} ${fieldLabel}`;
};

/**
 * The sources a row may read, grouped for a select: `[{ group, options: [{ value, label, detail }] }]`.
 * `feeds` (from `roleFeeds(template)`) names the document fields each role is resolved from, so a
 * resolved value shows the extracted field it came from beside it.
 */
export function sourceGroups(template, { lineScope = false, feeds = null } = {}) {
 const doc = (fields, prefix) => fields.map((f) => ({ value: `${prefix}${f.name}`, label: f.name, detail: f.hint || "" }));
 const groups = [{ group: "From the document", options: doc(headerFields(template), "") }];
 if (lineScope) groups.push({ group: "From the document line", options: doc(lineFields(template), "line.") });
 for (const role of MASTER_DATA_ROLES) {
  if (!role.prefix || (role.id === "product" && !lineScope)) continue;
  const from = (feeds?.[role.id] || []).join(", ");
  groups.push({ group: `Resolved in the target: ${role.label.toLowerCase()}${from ? ` (from ${from})` : ""}`, options: RESOLVED_FIELDS[role.id].map(([f, label]) => ({ value: `${role.prefix}${f}`, label: resolvedLabel(role, label, feeds), detail: role.hint })) });
 }
 groups.push({ group: "From the run", options: RUN_VALUES.map(([value, label]) => ({ value, label, detail: "" })) });
 return groups;
}

/** Flat form of `sourceGroups`, for a datalist. */
export function sourceOptions(template, opts) {
 return sourceGroups(template, opts).flatMap((g) => g.options.map((o) => ({ ...o, group: g.group })));
}

/** One source expression in words: `{ kind, label, detail }` — kind is document | line | resolved | run | literal | unknown. */
export function describeSource(source, template, feeds = null) {
 const text = String(source || "");
 if (!text) return { kind: "unknown", label: "", detail: "" };
 if (text.startsWith("=")) return { kind: "literal", label: `"${text.slice(1)}"`, detail: "a fixed value" };
 if (text.startsWith("$")) { const run = RUN_VALUES.find(([v]) => v === text); return { kind: "run", label: run ? run[1] : text, detail: "from the run" }; }
 const dot = text.indexOf(".");
 if (dot < 0) { const f = headerFields(template).find((x) => x.name === text); return { kind: "document", label: text, detail: f ? f.hint || "from the document" : "not in this template" }; }
 const head = text.slice(0, dot); const rest = text.slice(dot + 1);
 if (head === "line") { const f = lineFields(template).find((x) => x.name === rest); return { kind: "line", label: rest, detail: f ? f.hint || "from the document line" : "not in this template" }; }
 const role = MASTER_DATA_ROLES.find((r) => r.prefix === `${head}.`);
 if (role) { const f = (RESOLVED_FIELDS[role.id] || []).find(([n]) => n === rest); return { kind: "resolved", label: resolvedLabel(role, f ? f[1] : rest, feeds), detail: role.hint }; }
 return { kind: "unknown", label: text, detail: "" };
}

/** A row's sources as one sentence: "po_number", "ship-to country, else "United States"". */
export function describeSources(sources, template, feeds = null) {
 const parts = (sources || []).map((s) => describeSource(s, template, feeds));
 return parts.map((p, i) => (i === 0 ? p.label : `else ${p.label}`)).join(", ");
}

/**
 * The mapping's document sources this template does not extract (K24): a header source the
 * header lacks, a `line.x` source the line lacks. Resolved roles (customer., product.,
 * ship_to., contact.), run values ($today) and literals (=Draft) are never stale. Mirrors
 * `mapping.stale_sources`; each entry is { object, target, source }.
 */
export function staleSources(mapping, template) {
 if (!mapping || !template) return [];
 const header = new Set(headerFields(template).map((f) => f.name));
 const lines = new Set(lineFields(template).map((f) => f.name));
 const out = [];
 const walk = (rows, object, lineScope) => {
  for (const r of rows || []) {
   for (const source of r.source || []) {
    if (source.startsWith("=") || source.startsWith("$")) continue;
    const dot = source.indexOf(".");
    if (dot < 0) { if (!header.has(source)) out.push({ object, target: r.target, source }); continue; }
    const head = source.slice(0, dot); const rest = source.slice(dot + 1);
    if (head === "line" && lineScope && !lines.has(rest)) out.push({ object, target: r.target, source });
   }
  }
 };
 walk(mapping.fields, mapping.object, false);
 if (mapping.child) walk(mapping.child.fields, mapping.child.object, true);
 return out;
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
