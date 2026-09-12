/**
 * Keyword → assistant reply mapping for Play playground chat (demo, no backend).
 * First matching rule wins (order matters: specific topics before broad geography).
 */

import {
 DEFAULT_DEMO_PROFILE_ID,
 GOURMET_FOODS_PROFILE_ID,
 MEDIA_PROFILE_ID,
 MIDDLEBY_PROFILE_ID,
 normalizeDemoProfileId,
} from "./demo-profiles";
import { tryMatchGfiPlaygroundReply } from "./gfi-playground-chat";
import {
  tryMatchMediaPlaygroundReply,
  PLAYGROUND_MEDIA_DEFAULT_ACK,
} from "./media-playground-chat";
import {
 PLAYGROUND_MIDDLEBY_DEFAULT_ACK,
 tryMatchMiddlebyPlaygroundReply,
} from "./middleby-playground-chat";
import { getPlaygroundExploreHubReply, resolveExploreHorizonKey } from "./playground-explore-hub-replies";

export const PLAYGROUND_DEFAULT_ACK =
 "I've reviewed your request. I can help with retail operations, inventory and supply chain, store performance, orders and fulfillment, and customer insights grounded in your connected data. Ask a more specific question or name a region, metric, or system so I can tailor the analysis.";

export const PLAYGROUND_GOURMET_DEFAULT_ACK =
 "For Gourmet Foods International’s 14-DC specialty network, I can dive into perishable forecasting, inter-DC rebalancing on your fleet, import and tariff-sensitive buys, FSMA-ready traceability, INFRA program assortments, or competitive moves vs. broadline. Name a region, SKU family, or DC pair and I’ll narrow it.";

/** When hub handoff has a catalog + horizon but no seeded row, avoid keyword rules re-writing intent from long prompt text. */
const HUB_HANDOFF_WEEK_FALLBACK =
 "This week I’m keeping the lens on live exceptions and near-term execution-cut times, lanes, and service risk rather than quarterly rollups. Name a region, DC pair, or SKU cluster to sharpen the demo cut.";

const HUB_HANDOFF_QUARTER_FALLBACK =
 "For the quarter I’m anchoring on trend, plan vs actual, and structural fixes-not week-to-week noise. Name the KPI or program you want to stress-test in the demo.";

/** Rich cards + actions per topic (keys match rule.topic). */
export const PLAYGROUND_TOPIC_PANELS = {
 inventory: {
 findings: [
 {
 title: "Stockout Rate",
 value: "12.4%",
 change: "+3.2%",
 desc: "Above threshold across 8 stores in the North East region",
 },
 {
 title: "Unmet Demand",
 value: "$2.4M",
 change: "+18%",
 desc: "Estimated revenue loss from out-of-stock products in Q4",
 },
 {
 title: "Replenishment Delay",
 value: "4.2 days",
 change: "+1.8 days",
 desc: "Average delay between reorder point trigger and DC shipment",
 },
 ],
 recommendations: [
 "Increase safety stock levels by 15% for top 50 SKUs in NE region",
 "Shift 3 replenishment cycles from weekly to bi-weekly for high-velocity items",
 "Enable automated PO generation for items below reorder point threshold",
 ],
 },
 store_ops: {
 findings: [
 {
 title: "Coverage gaps",
 value: "3 stores",
 change: "vs plan",
 desc: "Below minimum staffing during peak windows this week",
 },
 {
 title: "Avg queue time",
 value: "8.4 min",
 change: "+2.1 min",
 desc: "Checkout wait elevated in high-traffic doors",
 },
 {
 title: "Task completion",
 value: "91%",
 change: "-4%",
 desc: "Opening checklist items delayed before noon",
 },
 ],
 recommendations: [
 "Shift one backup cashier to doors with average wait over 10 minutes from 12-2pm",
 "Rebalance task bundles so cycle counts finish before regional truck arrival",
 "Publish a 7-day staffing view to store leads with override notes",
 ],
 },
 orders: {
 findings: [
 {
 title: "SLA risk orders",
 value: "1.8k",
 change: "+12%",
 desc: "Units at risk of missing ship-by date in the next 48 hours",
 },
 {
 title: "Pick accuracy",
 value: "98.1%",
 change: "-0.4%",
 desc: "Down slightly at high-volume DCs after SKU location changes",
 },
 {
 title: "Carrier delays",
 value: "6.2%",
 change: "+1.1%",
 desc: "Shipments with carrier exceptions versus prior week",
 },
 ],
 recommendations: [
 "Prioritize wave picks for SKUs tied to at-risk SLAs before 2pm cutoffs",
 "Run a slot-verification pass on aisles touched in last replenishment batch",
 "Surface carrier hold reasons to CS for top-value orders automatically",
 ],
 },
};

function textMatchesKeyword(lower, keyword) {
 const k = keyword.trim().toLowerCase();
 if (!k) return false;
 if (k.includes(" ")) return lower.includes(k);
 try {
 return new RegExp(`\\b${k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`, "i").test(lower);
 } catch {
 return lower.includes(k);
 }
}

const RULES = [
 {
 keywords: [
 "inventory",
 "invnetory",
 "stock",
 "sku",
 "stockout",
 "out of stock",
 "replenish",
 "warehouse",
 "on hand",
 "safety stock",
 "wos",
 "weeks of supply",
 "returns",
 "return volume",
 "refund",
 "gap",
 "gaps",
 "shortage",
 "allocation",
 "supply chain",
 "on-shelf",
 ],
 topic: "inventory",
 intro: "Here's a snapshot focused on inventory and stock health (demo data):",
 },
 {
 keywords: [
 "store ops",
 "storeops",
 "staffing",
 "labor",
 "checkout queue",
 "cashier",
 "foot traffic",
 "opening checklist",
 "transaction failure",
 "failure rate",
 "pos",
 "lanes",
 "payment terminal",
 ],
 topic: "store_ops",
 intro: "Pulling store operations signals for your network (demo data):",
 },
 {
 keywords: [
 "fulfillment",
 "shipping",
 "delivery",
 "pick pack",
 "distribution center",
 "backorder",
 "order accuracy",
 "orders",
 "order",
 "shipment",
 "carrier",
 "cart",
 "abandonment",
 "checkout",
 "conversion",
 "funnel",
 "mobile",
 "e-com",
 "ecom",
 "sla",
 "wave",
 "picking",
 "invoice",
 "last mile",
 ],
 topic: "orders",
 intro: "Here's what stands out for orders and fulfillment (demo data):",
 },
 {
 keywords: ["north east", "northeast", "ne region"],
 topic: "inventory",
 intro: "Summarizing demand and inventory pressure for the North East (demo data):",
 },
 {
 keywords: ["gourmet", "specialty", "artisan", "deli", "short shelf", "cold chain", "spoilage"],
 topic: "inventory",
 intro: "Pulling specialty and fresh-lane signals for your network (demo data):",
 },
 {
 keywords: ["demand", "unmet"],
 topic: "inventory",
 intro: "I've analyzed demand signals tied to stock and availability (demo data):",
 },
];

/**
 * @param {string} userText
 * @param {string} [profileId]
 * @param {{ catalogId?: string, horizon?: string, lens?: string } | undefined} [exploreHub] - hub handoff (prefer `horizon`: week|quarter)
 * @returns {{ text: string, meta?: { variant: string, topic: string } }}
 */
/** True when the reply is generic static copy that may be replaced by an LLM (see `resolvePlaygroundAssistantReplyAsync`). */
export function isPlaygroundReplyEligibleForLlm(reply) {
 if (!reply?.text) return false;
 const t = reply.text;
 return (
 t === PLAYGROUND_DEFAULT_ACK ||
 t === PLAYGROUND_GOURMET_DEFAULT_ACK ||
 t === PLAYGROUND_MIDDLEBY_DEFAULT_ACK ||
 t === HUB_HANDOFF_WEEK_FALLBACK ||
 t === HUB_HANDOFF_QUARTER_FALLBACK
 );
}

export function getPlaygroundAssistantReply(userText, profileId = DEFAULT_DEMO_PROFILE_ID, exploreHub) {
 const id = normalizeDemoProfileId(profileId);
 const exploreKey = resolveExploreHorizonKey(exploreHub);

 if (id === MIDDLEBY_PROFILE_ID) {
 const middleby = tryMatchMiddlebyPlaygroundReply(userText, exploreHub);
 if (middleby) return middleby;
 if (exploreHub?.catalogId) {
 return { text: PLAYGROUND_MIDDLEBY_DEFAULT_ACK, meta: undefined };
 }
 return { text: PLAYGROUND_MIDDLEBY_DEFAULT_ACK, meta: undefined };
 }

 if (exploreHub?.catalogId && exploreKey) {
 const seeded = getPlaygroundExploreHubReply(exploreHub.catalogId, exploreKey, id);
 if (seeded) return seeded;
 }

 if (exploreHub?.catalogId) {
 // Media profile: fall back to a media panel with a media-flavored intro
 // so we never leak retail language (stockouts, DCs, SKUs) into a
 // media / OTT demo when the specific UC has no explicit reply.
 if (id === MEDIA_PROFILE_ID) {
 if (exploreKey === "week") {
 return {
 text: "This week I'm keeping the lens on live audience signals, competitor moves, and content or ad-yield exceptions across the OTT and broadcast footprint. Name a title, region, or business area to sharpen the cut.",
 meta: { variant: "playground-rich", topic: "content_trend_intelligence" },
 };
 }
 if (exploreKey === "quarter") {
 return {
 text: "This quarter I'd stack sustained audience trends, competitor slate movement, and monetization or ARPU signals against plan across content, OTT, broadcast, subscribers, and monetization.",
 meta: { variant: "playground-rich", topic: "content_scoping" },
 };
 }
 }
 if (exploreKey === "week") {
 return { text: HUB_HANDOFF_WEEK_FALLBACK, meta: { variant: "playground-rich", topic: "inventory" } };
 }
 if (exploreKey === "quarter") {
 return {
 text: HUB_HANDOFF_QUARTER_FALLBACK,
 meta: { variant: "playground-rich", topic: "inventory" },
 };
 }
 const fallbackNoHorizon =
 id === GOURMET_FOODS_PROFILE_ID
 ? PLAYGROUND_GOURMET_DEFAULT_ACK
 : id === MIDDLEBY_PROFILE_ID
 ? PLAYGROUND_MIDDLEBY_DEFAULT_ACK
 : id === MEDIA_PROFILE_ID
 ? PLAYGROUND_MEDIA_DEFAULT_ACK
 : PLAYGROUND_DEFAULT_ACK;
 return { text: fallbackNoHorizon, meta: undefined };
 }

 if (id === GOURMET_FOODS_PROFILE_ID && !exploreHub?.catalogId) {
 const gfi = tryMatchGfiPlaygroundReply(userText);
 if (gfi) return gfi;
 }

 if (id === MEDIA_PROFILE_ID && !exploreHub?.catalogId) {
 const media = tryMatchMediaPlaygroundReply(userText);
 if (media) return media;
 }

 const lower = userText.toLowerCase();
 for (const rule of RULES) {
 if (rule.keywords.some((k) => textMatchesKeyword(lower, k))) {
 return {
 text: rule.intro,
 meta: { variant: "playground-rich", topic: rule.topic },
 };
 }
 }
 const fallback =
 id === GOURMET_FOODS_PROFILE_ID
 ? PLAYGROUND_GOURMET_DEFAULT_ACK
 : id === MIDDLEBY_PROFILE_ID
 ? PLAYGROUND_MIDDLEBY_DEFAULT_ACK
 : id === MEDIA_PROFILE_ID
 ? PLAYGROUND_MEDIA_DEFAULT_ACK
 : PLAYGROUND_DEFAULT_ACK;
 return { text: fallback, meta: undefined };
}
