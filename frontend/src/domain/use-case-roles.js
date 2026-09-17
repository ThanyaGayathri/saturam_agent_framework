/**
 * Master-data roles as data (K21) — the mirror of `product/egpt_retail/documents/roles.py`.
 *
 * A document-to-record use case resolves only the roles its mapping reads (`customer.id`,
 * `ship_to.street`, `product.pricebook_entry_id`, `contact.id`; `$pricebook_id` rides with the
 * product) and its guardrails need, plus what those depend on. Which extracted field feeds
 * each role is derived from the template's field names — the same regexes as the backend —
 * unless the template declares `resolution`. Nothing here is the purchase order's.
 */
import { MASTER_DATA_ROLES } from "./use-case-autonomy";
import { findMapping, normalizeMapping } from "./record-mappings";
import { needsOf, normalizeRules } from "./guardrail-rules";
import { findTemplate, headerFields, lineFields } from "./extraction-templates";

const ROLE_BY_ID = Object.fromEntries(MASTER_DATA_ROLES.map((r) => [r.id, r]));
const PRODUCT_KINDS = new Set(["first_time_item", "quantity_outlier", "flagged_item"]);

function sourcesOf(mapping) {
 const rows = [...(mapping?.fields || []), ...(mapping?.child?.fields || [])];
 return rows.flatMap((r) => (Array.isArray(r.source) ? r.source : r.source ? [r.source] : []).map(String));
}

/** The role ids a mapping's source expressions read. */
export function rolesReadByMapping(mapping) {
 const out = new Set();
 for (const source of sourcesOf(mapping)) {
  for (const role of MASTER_DATA_ROLES) if (role.prefix && source.startsWith(role.prefix)) out.add(role.id);
  if (source === "$pricebook_id") out.add("product");
 }
 return out;
}

/** The role ids the enabled guardrails need. */
export function rolesNeededByRules(rules) {
 const out = new Set();
 for (const rule of normalizeRules(rules)) {
  const needs = needsOf(rule);
  if (needs && ROLE_BY_ID[needs]) out.add(needs);
  if (PRODUCT_KINDS.has(rule.kind)) out.add("product");
 }
 return out;
}

function closeOver(set) {
 let changed = true;
 while (changed) {
  changed = false;
  for (const id of [...set]) for (const dep of ROLE_BY_ID[id]?.depends || []) if (!set.has(dep)) { set.add(dep); changed = true; }
 }
 return set;
}

/** Every role this spec resolves, with why: `{ id, role, by: ["mapping"|"guardrails"|"dependency"] }`. */
export function rolesNeeded(spec, catalogue = null) {
 const mapping = spec?.recordMappingDef && spec.recordMappingDef.id === spec.targetObjects ? normalizeMapping(spec.recordMappingDef) : findMapping(spec, catalogue);
 const byMapping = rolesReadByMapping(mapping);
 const byRules = rolesNeededByRules(spec?.recordGuardrails);
 const all = closeOver(new Set([...byMapping, ...byRules]));
 return MASTER_DATA_ROLES.filter((r) => all.has(r.id)).map((r) => ({
  id: r.id, role: r,
  by: [...(byMapping.has(r.id) ? ["mapping"] : []), ...(byRules.has(r.id) ? ["guardrails"] : []), ...(!byMapping.has(r.id) && !byRules.has(r.id) ? ["dependency"] : [])],
 }));
}
export const neededRoleIds = (spec, catalogue = null) => new Set(rolesNeeded(spec, catalogue).map((r) => r.id));
export const needsRole = (spec, roleId) => neededRoleIds(spec).has(roleId);

/** Why a role is needed, in words, or null when it is not. */
export function roleReason(spec, roleId) {
 const entry = rolesNeeded(spec).find((r) => r.id === roleId);
 if (!entry) return null;
 if (entry.by.includes("mapping") && entry.by.includes("guardrails")) return "read by the mapping and a guardrail";
 if (entry.by.includes("mapping")) return "read by the mapping";
 if (entry.by.includes("guardrails")) return "needed by a guardrail";
 const dependents = rolesNeeded(spec).filter((r) => r.role.depends?.includes(roleId)).map((r) => r.role.label.toLowerCase());
 return `needed to resolve ${dependents.join(", ") || "another role"}`;
}

// --- which extracted field feeds each role: the backend's regexes, verbatim -------------------
const ACCOUNT_NAME = /^(customer|buyer|supplier|vendor|account|company|client|sold_to|sender|from)(_?name)?$/;
const ACCOUNT_NAME_LOOSE = /(customer|supplier|vendor|account|client)_name/;
const SHIP_TO = /(ship|deliver|delivery|shipping).*(address|to|site)|^(site|location)$/;
const ITEM = /^(description|product|item|material|article|sku_description|product_description|item_description|product_name|item_name)$/;
const THEIR_PART = /^(customer|buyer|their|client)_?(part|item|sku|code|ref|product)/;
const OUR_CODE = /^(supplier|our|vendor|seller)?_?(product_code|sku|part_number|part_no|part|item_code|item_number|item_no|code|product_id|material_number|catalogue_number)$/;
const REFERENCE = /(number|reference|ref|id|no)$/;
const first = (names, re) => names.find((n) => re.test(n)) || null;

/** The header field a document is known by (`po_number`, `invoice_number`, `rma_reference`). */
export function referenceField(template) {
 if (!template) return null;
 if (template.reference) return template.reference;
 return first(headerFields(template).map((f) => f.name).filter((n) => !n.startsWith("line")), REFERENCE);
}
export const referenceLabel = (template) => { const f = referenceField(template); return f ? f.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase()) : "Reference"; };

/** `{ customer: { name }, ship_to: { address }, product: { description, customer_part_number, supplier_product_code }, reference }`. */
export function resolutionSources(template) {
 const header = headerFields(template).map((f) => f.name);
 const lines = lineFields(template).map((f) => f.name);
 const derived = {
  customer: { name: first(header, ACCOUNT_NAME) || first(header, ACCOUNT_NAME_LOOSE) },
  ship_to: { address: first(header, SHIP_TO) },
  product: { description: first(lines, ITEM), customer_part_number: first(lines, THEIR_PART), supplier_product_code: first(lines, OUR_CODE) },
  reference: referenceField(template),
 };
 for (const [role, value] of Object.entries(template?.resolution || {})) {
  if (value && typeof value === "object" && derived[role] && typeof derived[role] === "object") Object.assign(derived[role], Object.fromEntries(Object.entries(value).filter(([, v]) => v)));
  else if (value) derived[role] = value;
 }
 return derived;
}

/**
 * The document fields each role is resolved from, by `MASTER_DATA_ROLES` id:
 * `{ customer: ["customer_name", "sender email"], contact: ["sender email"], shipTo: ["ship_to_address"],
 *   product: ["part_no", "description"] }` — what the mapping form shows beside a resolved value,
 * so `ship-to site street` reads as `ship_to_address → ship-to site street`.
 */
export function roleFeeds(template) {
 const sources = resolutionSources(template);
 return {
  customer: ["sender email", sources.customer.name].filter(Boolean),
  contact: ["sender email"],
  shipTo: [sources.ship_to.address].filter(Boolean),
  product: [sources.product.customer_part_number, sources.product.supplier_product_code, sources.product.description].filter(Boolean),
 };
}

/** What each needed role will read from this spec's template — for the plan and the readiness check. */
export function resolutionPlan(spec) {
 const template = findTemplate(spec);
 const sources = resolutionSources(template);
 return rolesNeeded(spec).map(({ id, role, by }) => {
  let reads = "the sender's email";
  if (id === "customer") reads = sources.customer.name ? `the sender's email domain, then \`${sources.customer.name}\`` : "the sender's email domain";
  if (id === "contact") reads = "the sender's email address";
  if (id === "shipTo") reads = sources.ship_to.address ? `\`${sources.ship_to.address}\` against the account's sites` : "the account's only site (no address field on the document)";
  if (id === "product") { const f = [sources.product.customer_part_number, sources.product.supplier_product_code, sources.product.description].filter(Boolean); reads = f.length ? `each line's ${f.map((x) => `\`${x}\``).join(", then ")}` : "nothing — the template has no item field"; }
  if (id === "orderHistory") reads = "the target object's own records per account";
  return { id, label: role.label, by, reads };
 });
}
