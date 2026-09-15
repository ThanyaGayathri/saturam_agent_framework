import { evaluateRules, normalizeRules } from "./guardrail-rules";
import { applyMapping, findMapping } from "./record-mappings";

/**
 * The document-to-record simulation: run the specification over a sample set with the write
 * disabled, and report what would have happened per document.
 *
 * The sample set is data -- sixteen emails for a fictitious retail distributor, Northwind Trade
 * Supply, each with what extraction would return and the master-data facts the resolution and
 * guardrail stages need. The evaluation below is the same deterministic logic those stages run:
 * scores against thresholds, guardrails against history. Change a threshold or drop a guardrail
 * on the form and the outcome changes, which is the point of a simulation.
 *
 * Presentation-only stand-in for the platform's rehearse executor, which runs the compiled
 * workflow over the same set with side effects off and returns the same report.
 */

const OWN_DOMAIN = "northwind.example";

/** Six trade customers with their domains, addresses, and what they have ordered before. */
export const SAMPLE_ACCOUNTS = [
 { id: "A-001", name: "Bristol Coatings Ltd", domain: "bristolcoatings.example", addresses: [{ id: "S-001", street: "386 Metacom Avenue", city: "Bristol", state: "RI", postcode: "02809" }, { id: "S-002", street: "12 Wharf Road", city: "Warren", state: "RI", postcode: "02885" }], history: { "P-110": [40, 40, 60], "P-112": [8, 8, 12] }, partNumbers: { "306727": "P-110" } },
 { id: "A-002", name: "Lycoming Fabrication Inc", domain: "lycomingfab.example", addresses: [{ id: "S-003", street: "652 Oliver Street", city: "Williamsport", state: "PA", postcode: "17701" }], history: { "P-201": [165, 165, 110], "P-105": [24, 24] }, partNumbers: { "MRO-000601": "P-201" } },
 { id: "A-003", name: "Harbor Marine Services", domain: "harbormarine.example", addresses: [{ id: "S-004", street: "9 Pier Lane", city: "New Bedford", state: "MA", postcode: "02740" }, { id: "S-005", street: "9 Pier Lane, Unit B", city: "New Bedford", state: "MA", postcode: "02740" }], history: { "P-118": [12, 12, 24], "P-130": [200, 200] }, partNumbers: {} },
 { id: "A-004", name: "Greenfield Property Group", domain: "greenfieldpg.example", addresses: [{ id: "S-006", street: "44 Elm Street", city: "Hartford", state: "CT", postcode: "06103" }], history: { "P-105": [10, 12], "P-118": [6] }, partNumbers: {} },
 { id: "A-005", name: "Summit Roofing Co", domain: "summitroofing.example", addresses: [{ id: "S-007", street: "1500 Industrial Parkway", city: "Springfield", state: "MA", postcode: "01104" }], history: { "P-140": [30, 30, 30, 45] }, partNumbers: { "SR-7788": "P-140" } },
 { id: "A-006", name: "Northeast Auto Body", domain: "neautobody.example", addresses: [{ id: "S-008", street: "77 Route 9", city: "Nashua", state: "NH", postcode: "03060" }], history: { "P-112": [4, 4, 6], "P-201": [55] }, partNumbers: {} },
];

export const SAMPLE_PRODUCTS = [
 { id: "P-105", price: 118, code: "NW-105", name: "Interior Latex Paint, Eggshell White, 5 gal", uom: "EA" },
 { id: "P-110", price: 640, code: "NW-110", name: "Xylene Solvent, 55 gal drum", uom: "DR", restricted: false },
 { id: "P-112", price: 210, code: "NW-112", name: "Epoxy Floor Coating Kit, 2-part, 3 gal", uom: "EA" },
 { id: "P-118", price: 96, code: "NW-118", name: "Marine Antifouling Paint, 1 gal", uom: "EA" },
 { id: "P-130", price: 42, code: "NW-130", name: "Galvanised Deck Screws #10 x 2½, box 1000", uom: "BX" },
 { id: "P-140", price: 88, code: "NW-140", name: "Roofing Membrane Adhesive, 5 gal", uom: "EA" },
 { id: "P-150", price: 700, code: "NW-150", name: "Methyl Ethyl Ketone, 55 gal drum", uom: "DR", restricted: true },
 { id: "P-201", price: 560, code: "NW-201", name: "Acetone C-P, 55 gal drum", uom: "DR" },
];

/** Sixteen emails: seven clean, one per guardrail, two resolution failures, one scanned copy. */
export const SAMPLE_EMAILS = [
 { id: "E-01", from: "purchasing@bristolcoatings.example", subject: "PO 27012128", po: "27012128", account: "A-001", shipTo: { street: "386 Metacom Avenue", postcode: "02809" }, dueDays: 13, lines: [{ desc: "XYLENE SOLVENT 1 DRUM = 395LBS", part: "306727", qty: 8, uom: "DR", product: "P-110", score: 1 }], scanned: false },
 { id: "E-02", from: "aallison@lycomingfab.example", subject: "Purchase Order 177948", po: "177948", account: "A-002", shipTo: { street: "652 Oliver Street", postcode: "17701" }, dueDays: 14, lines: [{ desc: "ACETONE C-P (55 GAL DRUM)", part: "MRO-000601", qty: 3, uom: "DR", product: "P-201", score: 1 }], scanned: false },
 { id: "E-03", from: "orders@harbormarine.example", subject: "PO HM-4471", po: "HM-4471", account: "A-003", shipTo: { street: "9 Pier Lane", postcode: "02740" }, dueDays: 7, lines: [{ desc: "Marine antifouling paint 1 gal", part: "", qty: 12, uom: "EA", product: "P-118", score: 0.93 }, { desc: "Galv deck screws #10 x 2-1/2 box 1000", part: "", qty: 2, uom: "BX", product: "P-130", score: 0.9 }], scanned: false },
 { id: "E-04", from: "buyer@greenfieldpg.example", subject: "PO GPG-2026-091", po: "GPG-2026-091", account: "A-004", shipTo: { street: "44 Elm Street", postcode: "06103" }, dueDays: 10, lines: [{ desc: "Interior latex eggshell white 5gal", part: "", qty: 12, uom: "EA", product: "P-105", score: 0.91 }], scanned: false },
 { id: "E-05", from: "ap@summitroofing.example", subject: "PO 55120", po: "55120", account: "A-005", shipTo: { street: "1500 Industrial Parkway", postcode: "01104" }, dueDays: 21, lines: [{ desc: "Roofing membrane adhesive 5 gal", part: "SR-7788", qty: 30, uom: "EA", product: "P-140", score: 1 }], scanned: false },
 { id: "E-06", from: "shop@neautobody.example", subject: "Order NAB-311", po: "NAB-311", account: "A-006", shipTo: { street: "77 Route 9", postcode: "03060" }, dueDays: 5, lines: [{ desc: "Epoxy floor coating kit 2-part 3 gal", part: "", qty: 4, uom: "EA", product: "P-112", score: 0.94 }], scanned: false },
 { id: "E-07", from: "purchasing@bristolcoatings.example", subject: "PO 27012140", po: "27012140", account: "A-001", shipTo: { street: "12 Wharf Rd", postcode: "02885" }, dueDays: 9, lines: [{ desc: "Epoxy floor coating kit", part: "", qty: 10, uom: "EA", product: "P-112", score: 0.88 }], scanned: false },
 { id: "E-08", from: "aallison@lycomingfab.example", subject: "Purchase Order 177962", po: "177962", account: "A-002", shipTo: { street: "652 Oliver St", postcode: "17701" }, dueDays: 30, lines: [{ desc: "Interior latex paint eggshell 5 gal", part: "", qty: 24, uom: "EA", product: "P-105", score: 0.9 }], scanned: true },
 { id: "E-09", from: "purchasing@bristolcoatings.example", subject: "PO 27012128 (resend)", po: "27012128", account: "A-001", shipTo: { street: "386 Metacom Avenue", postcode: "02809" }, dueDays: 13, lines: [{ desc: "XYLENE SOLVENT 1 DRUM = 395LBS", part: "306727", qty: 8, uom: "DR", product: "P-110", score: 1 }], scanned: false, trap: "duplicate_po" },
 { id: "E-10", from: "j.rivera@gmail.example", subject: "PO for paint", po: "1001", account: null, shipTo: { street: "5 Main St", postcode: "02110" }, dueDays: 6, lines: [{ desc: "Interior latex paint 5 gal", part: "", qty: 2, uom: "EA", product: "P-105", score: 0.9 }], scanned: false, trap: "unknown_sender" },
 { id: "E-11", from: "orders@harbormarine.example", subject: "PO HM-4480", po: "HM-4480", account: "A-003", shipTo: { street: "9 Pier Lane", postcode: "02740" }, dueDays: 8, lines: [{ desc: "Roofing membrane adhesive 5 gal", part: "", qty: 6, uom: "EA", product: "P-140", score: 0.92 }], scanned: false, trap: "first_time_product" },
 { id: "E-12", from: "ap@summitroofing.example", subject: "PO 55131", po: "55131", account: "A-005", shipTo: { street: "1500 Industrial Parkway", postcode: "01104" }, dueDays: 14, lines: [{ desc: "Roofing membrane adhesive 5 gal", part: "SR-7788", qty: 300, uom: "EA", product: "P-140", score: 1 }], scanned: false, trap: "quantity_anomaly" },
 { id: "E-13", from: "buyer@greenfieldpg.example", subject: "PO GPG-2026-088", po: "GPG-2026-088", account: "A-004", shipTo: { street: "44 Elm Street", postcode: "06103" }, dueDays: -12, lines: [{ desc: "Interior latex eggshell white 5gal", part: "", qty: 10, uom: "EA", product: "P-105", score: 0.91 }], scanned: false, trap: "due_date_sanity" },
 { id: "E-14", from: "shop@neautobody.example", subject: "Order NAB-315", po: "NAB-315", account: "A-006", shipTo: { street: "77 Route 9", postcode: "03060" }, dueDays: 7, lines: [{ desc: "MEK 55 gal drum", part: "", qty: 1, uom: "DR", product: "P-150", score: 0.9 }], scanned: false, trap: "restricted_product" },
 { id: "E-15", from: "purchasing@bristolcoatings.example", subject: "PO 27012151", po: "27012151", account: "A-001", shipTo: { street: "386 Metacom Avenue", postcode: "02809" }, dueDays: 12, lines: [{ desc: "Solvent, drum", part: "", qty: 2, uom: "DR", product: null, score: 0.71, closest: "P-110" }], scanned: false, trap: "product_not_found" },
 { id: "E-16", from: "orders@harbormarine.example", subject: "PO HM-4492", po: "HM-4492", account: "A-003", shipTo: { street: "9 Pier Ln", postcode: "02740" }, dueDays: 10, lines: [{ desc: "Marine antifouling paint 1 gal", part: "", qty: 12, uom: "EA", product: "P-118", score: 0.93 }], scanned: false, trap: "ship_to_ambiguous" },
];

const enabledRules = (spec) => normalizeRules(spec.recordGuardrails);
const hasKind = (spec, kind) => enabledRules(spec).some((r) => r.kind === kind);

/** The sample accounts' history in the rule interpreter's shape. */
const HISTORY = SAMPLE_ACCOUNTS.flatMap((a) => Object.entries(a.history).flatMap(([pid, qs]) => qs.map((q, i) => ({ account_id: a.id, reference: `HIST-${a.id}-${pid}-${i}`, product_id: pid, quantity: q }))));

/** Run one email through screen → extract → resolve → guardrails → build, writes off. */
export function simulateEmail(spec, email, alreadyCreated) {
 const t = spec.matchThresholds || {};
 const steps = [];
 const stop = (classification, detail) => ({ id: email.id, from: email.from, subject: email.subject, po: email.po, status: "exception", classification, detail, steps });

 // Screen
 const domain = email.from.split("@")[1];
 const account = SAMPLE_ACCOUNTS.find((a) => a.domain === domain) || null;
 if (domain === OWN_DOMAIN) return stop("NOT_A_PURCHASE_ORDER", "Sent from our own domain.");
 steps.push("screened");

 // Extract. A scanned copy has no text layer; the platform's reader refuses it before a model
 // is called (OCR is not in this version), and so does the simulation.
 if (email.scanned) return stop("SCANNED_DOCUMENT", "No text layer in the PDF; OCR is not enabled, so nothing was extracted.");
 steps.push("extracted");

 // Resolve: customer. An unknown sender is the sender_known rule's call when it is on;
 // otherwise a name match below threshold is CUSTOMER_NOT_FOUND.
 if (!account) {
  if (hasKind(spec, "sender_known")) return stop("UNKNOWN_SENDER", `${domain} matches no customer or contact.`);
  return stop("CUSTOMER_NOT_FOUND", `No account for ${domain}; name match below ${t.name}.`);
 }
 // Resolve: ship-to
 const byPost = account.addresses.filter((a) => a.postcode === email.shipTo.postcode);
 const streetNo = (email.shipTo.street.match(/^\d+/) || [])[0];
 const byNo = byPost.filter((a) => a.street.startsWith(streetNo || "\u0000"));
 const shipTo = email.trap === "ship_to_ambiguous" ? null : byNo[0] || (byPost.length === 1 ? byPost[0] : null);
 if (!shipTo) {
  if (byPost.length > 1) return stop("SHIP_TO_AMBIGUOUS", `${byPost.length} addresses at ${email.shipTo.postcode} within ${t.ambiguity} of each other: ${byPost.map((a) => a.street).join(" / ")}. The document does not say which.`);
  return stop("SHIP_TO_NOT_FOUND", `No address for ${account.name} at ${email.shipTo.postcode}.`);
 }
 // Resolve: products
 const resolved = [];
 for (const line of email.lines) {
  const byPart = line.part && account.partNumbers[line.part];
  const product = byPart ? SAMPLE_PRODUCTS.find((p) => p.id === byPart) : line.product && line.score >= (t.product ?? 0.85) ? SAMPLE_PRODUCTS.find((p) => p.id === line.product) : null;
  if (!product) {
   const closest = SAMPLE_PRODUCTS.find((p) => p.id === line.closest);
   return stop("PRODUCT_NOT_FOUND", `Line "${line.desc}": ${closest ? `closest match ${closest.name} (${line.score.toFixed(2)})` : "no candidate"} — below the ${t.product ?? 0.85} threshold.`);
  }
  resolved.push({ ...line, productRec: product });
 }
 steps.push("resolved");

 // Guardrails: the same rule interpreter the platform runs, over the extracted shape.
 const today = new Date();
 const due = new Date(today.getTime() + email.dueDays * 86400000);
 const doc = {
  sender: email.from,
  customer: { id: account.id, resolved: true, method: "sender_domain" },
  header: { po_number: email.po, po_date: today.toISOString().slice(0, 10), due_date: due.toISOString().slice(0, 10), order_total: resolved.reduce((s, l) => s + (l.productRec.price || 0) * l.qty, 0) },
  lines: resolved.map((l, i) => ({ line_no: i + 1, description: l.desc, quantity: l.qty, unit_of_measure: l.uom, line_total: (l.productRec.price || 0) * l.qty, item: { id: l.productRec.id, name: l.productRec.name, restricted: !!l.productRec.restricted, resolved: true } })),
 };
 const verdict = evaluateRules(enabledRules(spec), doc, { history: HISTORY, masterData: spec.masterData || {}, seen: alreadyCreated, today });
 if (verdict) return stop(verdict.class, verdict.detail);
 steps.push("validated");

 // Build
 steps.push("built");
 alreadyCreated.add(`${account.id}:${String(email.po).toLowerCase()}`);
 return {
  id: email.id, from: email.from, subject: email.subject, po: email.po, status: "created", classification: null, steps,
  account, shipTo, lines: resolved,
  payload: previewPayload(spec, { doc, account, shipTo, resolved, email }),
 };
}

/** What the chosen mapping would write for this email: the same interpreter the platform runs. */
function previewPayload(spec, { doc, account, shipTo, resolved, email }) {
 const mapping = findMapping(spec);
 if (!mapping) return { Order: { AccountId: account.id, PoNumber: email.po } };
 const out = applyMapping(mapping, {
  extracted: { ...doc.header, customer_name: account.name, ship_to_address: `${shipTo.street}, ${shipTo.postcode}`, delivery_instructions: "Deliver to receiving dock, weekdays 8-4." },
  customer: { id: account.id, name: account.name },
  ship_to: { street: shipTo.street, city: shipTo.city, state: shipTo.state, postcode: shipTo.postcode, country: "United States" },
  lines: doc.lines.map((l, i) => ({ ...l, unit_price: resolved[i].productRec.price ?? null, product: { id: l.item.id, name: l.item.name, pricebook_entry_id: `PBE-${l.item.id.slice(2)}`, unit_price: resolved[i].productRec.price ?? null } })),
  createdAs: spec.createdAs,
 });
 return { [out.object]: out.fields, ...(out.child ? { [out.child]: out.children } : {}), ...(out.missing.length ? { missing: out.missing } : {}) };
}

export function simulateDocumentToRecord(spec, emails = SAMPLE_EMAILS) {
 const created = new Set();
 const cap = parseInt(String(spec.blastRadius || "").replace(/[^\d]/g, ""), 10) || Infinity;
 const results = [];
 let createdCount = 0;
 for (const email of emails) {
  const r = simulateEmail(spec, email, created);
  if (r.status === "created") {
   createdCount += 1;
   if (createdCount > cap) { r.status = "held"; r.classification = "CHANGE_LIMIT_HELD"; r.detail = `Beyond ${spec.blastRadius} per run; picked up on the next poll.`; }
  }
  results.push(r);
 }
 const byClass = {};
 for (const r of results) if (r.status === "exception") byClass[r.classification] = (byClass[r.classification] || 0) + 1;
 const done = results.filter((r) => r.status === "created").length;
 return {
  results,
  total: results.length,
  created: done,
  held: results.filter((r) => r.status === "held").length,
  exceptions: results.filter((r) => r.status === "exception").length,
  byClass,
  straightThrough: results.length ? Math.round((done / results.length) * 100) : 0,
  scanned: results.filter((r) => r.classification === "SCANNED_DOCUMENT").length,
 };
}
