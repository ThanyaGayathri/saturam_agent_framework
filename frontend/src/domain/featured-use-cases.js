import {
 DEFAULT_DEMO_PROFILE_ID,
 GOURMET_FOODS_PROFILE_ID,
 MEDIA_PROFILE_ID,
 MIDDLEBY_PROFILE_ID,
 DEMO_ROLE_EXECUTIVE,
 DEMO_ROLE_OPERATIONS,
 DEMO_ROLE_ANALYST,
 DEFAULT_DEMO_ROLE,
} from "./demo-profiles";
import { MIDDLEBY_RECOMMENDED_DEMO_IDS } from "./middleby-catalog-demo-tiers";

/**
 * Role-aware use case priority lists per profile.
 * Kept separate from the seed so order can change without touching seed rows.
 *
 * Three roles:
 * executive - ROI, margin, strategic outcomes. High-level impact.
 * operations - Process efficiency, SLAs, execution. Day-to-day improvement.
 * analyst - Diagnostics, forecasting, investigative signals.
 */

const ROLE_IDS_BY_PROFILE = {
 [DEFAULT_DEMO_PROFILE_ID]: {
 [DEMO_ROLE_EXECUTIVE]: [
 "gallery-uc-09", // Gross Margin Analysis - #1 exec metric
 "gallery-uc-01", // Stockout Risk Alert - direct revenue risk
 "gallery-uc-08", // Markdown Optimisation - margin recovery
 "gallery-uc-04", // Labor Scheduling Optimisation - biggest controllable cost
 "gallery-uc-02", // Order Fulfillment Routing - cost + customer experience
 ],
 [DEMO_ROLE_OPERATIONS]: [
 "gallery-uc-04", // Labor Scheduling Optimisation - staffing vs demand
 "gallery-uc-02", // Order Fulfillment Routing - fulfillment efficiency
 "gallery-uc-13", // Cross-DC Allocation Balancing - stock redistribution
 "gallery-uc-07", // Return Surge Triage - exception handling
 "gallery-uc-06", // POS Failure Detection - uptime / ops health
 "gallery-uc-22", // Opening Checklist Compliance - store readiness
 ],
 [DEMO_ROLE_ANALYST]: [
 "gallery-uc-09", // Gross Margin Analysis - category margin deep-dive
 "gallery-uc-11", // Demand Forecast Accuracy - supply chain signal
 "gallery-uc-01", // Stockout Risk Alert - inventory signal
 "gallery-uc-03", // Assortment Gap Analysis - range vs demand
 "gallery-uc-15", // SLA Breach Root Cause - order diagnostics
 "gallery-uc-34", // Promo ROI Attribution - campaign analysis
 ],
 },
 [GOURMET_FOODS_PROFILE_ID]: {
 [DEMO_ROLE_EXECUTIVE]: [
 "gfi-uc-01-perishable-forecast", // Perishable demand forecasting - core to GFI model
 "gfi-uc-09-fsma-traceability", // FSMA 204 compliance - 2026 regulatory deadline
 "gfi-uc-06-multi-temp-routing", // Multi-temp route optimisation - fleet differentiator
 "gfi-uc-04-markdown-optimization", // Perishable markdown - margin before write-off
 "gfi-uc-13-shrinkage-detection", // Shrinkage & loss detection - sub-1.5% COGS target
 ],
 [DEMO_ROLE_OPERATIONS]: [
 "gfi-uc-05-labor-optimization", // Warehouse labor optimization - DC staffing
 "gfi-uc-06-multi-temp-routing", // Multi-temp route optimisation - fleet efficiency
 "gfi-uc-02-inter-dc-rebalance", // Inter-DC rebalancing - network redistribution
 "gfi-uc-04-markdown-optimization", // Perishable markdown - waste reduction ops
 "gfi-uc-03-supplier-risk", // Supplier risk monitoring - supply continuity
 "gfi-uc-10-order-routing", // Order fulfillment routing - DC assignment
 ],
 [DEMO_ROLE_ANALYST]: [
 "gfi-uc-01-perishable-forecast", // Perishable demand forecasting - forecast accuracy
 "gfi-uc-07-vendor-scorecards", // Vendor performance scorecards - supplier analysis
 "gfi-uc-08-npi-tracking", // NPI tracking - launch diagnostics
 "gfi-uc-13-shrinkage-detection", // Shrinkage & loss detection - margin signal
 "gfi-uc-12-customer-demand-intelligence", // Customer demand intelligence - B2B insights
 "gfi-uc-15-competitive-positioning", // Specialty moat analysis - competitive intel
 ],
 },
 [MIDDLEBY_PROFILE_ID]: {
 [DEMO_ROLE_EXECUTIVE]: [
 "middleby-uc-07",
 "middleby-uc-09",
 "middleby-uc-11",
 "middleby-uc-18",
 "middleby-uc-04",
 ],
 [DEMO_ROLE_OPERATIONS]: [
 "middleby-uc-04",
 "middleby-uc-06",
 "middleby-uc-10",
 "middleby-uc-13",
 "middleby-uc-02",
 ],
 [DEMO_ROLE_ANALYST]: [
 "middleby-uc-11",
 "middleby-uc-09",
 "middleby-uc-18",
 "middleby-uc-08",
 "middleby-uc-01",
 "middleby-uc-17",
 ],
 },
};

/**
 * Media profile has NO viewer roles. Featured use cases are a flat list
 * (role state is ignored). Consumers should call MEDIA_FEATURED_UC_IDS
 * directly or via the short-circuit in `getUseCasesForRole` below.
 */
export const MEDIA_FEATURED_UC_IDS = [
 "media-uc-01-content-conversation-trends",
 "media-uc-02-future-content-opportunity",
 "media-uc-03-competitor-content-benchmark",
 "media-uc-04-digital-market-landscape",
 "media-uc-05-content-gap-analysis",
 "media-uc-10-aeo-geo-visibility",
 "media-uc-11-subscription-churn-predictor",
 "media-uc-13-ad-yield-optimization",
];

/** Short impact line per use case id - shown on home strip card and impact dashboard. */
export const FEATURED_IMPACT = {
 // Default - Executive
 "gallery-uc-09": "Category and channel margin vs plan and prior year",
 "gallery-uc-01": "Reduce stockouts 40-60%, protect service level in real time",
 "gallery-uc-08": "Recover margin on slow-moving stock with optimal markdown timing",
 "gallery-uc-04": "Align staffing to demand forecasts, cut labor waste 15-25%",
 "gallery-uc-02": "Cut fulfillment cost 15-25%, reduce split shipments 30%",
 // Default - Operations extras
 "gallery-uc-13": "Balance stock across DCs to eliminate regional shortfalls",
 "gallery-uc-07": "Identify return spike root cause before it escalates",
 "gallery-uc-06": "Detect POS failures before they hit transaction volume",
 "gallery-uc-22": "Ensure stores open ready: checklists, equipment, staff in place",
 // Default - Analyst extras
 "gallery-uc-11": "Pinpoint forecast error driving excess stock in seasonal categories",
 "gallery-uc-03": "Surface assortment gaps vs competitor range and customer demand",
 "gallery-uc-15": "Trace SLA breaches to root cause across order and carrier data",
 "gallery-uc-34": "Quantify incremental lift from overlapping promotions",
 "gallery-uc-66": "Quantify revenue tied to inventory gaps before stockouts and markdowns bite",
 "gallery-uc-67": "Contain supplier service slips before they cascade to shelf availability",
 "gallery-uc-68": "Isolate fraud patterns without blocking legitimate high-value orders",
 "gallery-uc-69": "Convert foot traffic gains into sales where conversion has stalled",
 "gallery-uc-70": "Prioritise experiments that lift conversion without conflicting UX",
 "gallery-uc-71": "Reallocate spend toward traffic sources with durable efficiency",
 "gallery-uc-72": "Tie site reliability regressions to measurable funnel impact",
 "gallery-uc-73": "Cut checkout queues at peak without linear labor increases",
 "gallery-uc-74": "Stabilise tender performance across wallets, BNPL, and chip fallback",
 "gallery-uc-75": "Turn satisfaction signals into targeted operational fixes",
 "gallery-uc-76": "Balance contact centre capacity with intent mix and FCR goals",
 "gallery-uc-77": "Prevent wait-time SLA breaches before abandon volume spikes",
 "gallery-uc-78": "Clear escalation debt and reduce repeat executive-touch cases",
 "gallery-uc-79": "Protect competitiveness while defending differentiated SKU margin",
 "gallery-uc-80": "Accelerate sell-through before markdown depth erodes margin",
 "gallery-uc-81": "Surface structural cost opportunities vs benchmarks",
 "gallery-uc-82": "Separate surge overtime from chronic scheduling leakage",
 // GFI - Executive
 "gfi-uc-01-perishable-forecast": "Cut perishable waste 20-30%, reduce specialty SKU stockouts",
 "gfi-uc-09-fsma-traceability": "Lot-level traceback across 11,340 SKUs, audit-ready for 2026",
 "gfi-uc-06-multi-temp-routing": "Lower delivery cost, fewer excursions on mixed-temp routes",
 "gfi-uc-04-markdown-optimization": "Recover margin on short-code perishables before write-off",
 "gfi-uc-13-shrinkage-detection": "Drive shrink below 1.5% of COGS across 14 DCs",
 // GFI - Operations extras
 "gfi-uc-05-labor-optimization": "Match DC pick teams to forecast volume, cut overtime 25%",
 "gfi-uc-02-inter-dc-rebalance": "Move stranded stock before it expires; cut inter-DC emergency transfers",
 "gfi-uc-03-supplier-risk": "Surface supplier disruption risk before allocations are affected",
 "gfi-uc-10-order-routing": "Assign B2B orders to best DC, cut split shipments 30%",
 // GFI - Analyst extras
 "gfi-uc-07-vendor-scorecards": "Always-on fill rate, quality, and lead-time scores across 2,000+ suppliers",
 "gfi-uc-08-npi-tracking": "Scale winners and cut losers in first 8 weeks after launch",
 "gfi-uc-12-customer-demand-intelligence": "Surface trending items and regional benchmarks for key accounts",
 "gfi-uc-15-competitive-positioning": "Quantify broadline encroachment risk and counter with curation",
 // Middleby analytics
 "middleby-uc-01": "Rank illustrative anonymous sessions by RFQ intent before quote submit",
 "middleby-uc-02": "Find where shoppers drop out of the RFQ funnel and why",
 "middleby-uc-03": "Spot categories with rising interest but weak conversion",
 "middleby-uc-04": "Prioritise dealer follow-up on urgent timeframes and consultation requests",
 "middleby-uc-05": "Follow the path from browse to quote for high-intent buyers",
 "middleby-uc-06": "Surface dealer regions with the largest quote pipeline gaps",
 "middleby-uc-07": "Leadership view of traffic, errors, and quote pipeline in one place",
 "middleby-uc-08": "See which UX errors reach the most sessions and quote risk",
 "middleby-uc-09": "See purchase timeframe and consultation mix from HubSpot quote export",
 "middleby-uc-10": "Split the pipeline by consultation requests and deal stage",
 "middleby-uc-11": "Compare repeat-brand vs new-to-brand quote volume from HubSpot",
 "middleby-uc-12": "Map quote delivery and region concentration from HubSpot",
 "middleby-uc-13": "See where quotes stall by brand and pipeline stage",
 "middleby-uc-14": "Compare illustrative channel mix until GA channel export is wired",
 "middleby-uc-15": "Measure how often buyers browse multiple brands before quoting",
 "middleby-uc-16": "Weekly briefing on traffic, site errors, and quote movement",
 "middleby-uc-17": "Ask in plain language and chart GA brand traffic",
 "middleby-uc-18": "Quantify browse vs quote gap by brand across GA and HubSpot",
 "middleby-uc-19": "Find SKUs with strong browse interest but few quotes",
 "middleby-uc-20": "Tie site errors to estimated quote impact by type",
 "middleby-uc-21": "See which pages earn engagement but weak RFQ conversion",
 "middleby-uc-22": "Flag sessions that look like buyers but never submitted a quote",
 "middleby-uc-23": "Headline metrics from GA4, LogRocket, and HubSpot side by side",
 "middleby-uc-24": "Link a HubSpot quote to example on-site session behaviour",
 "middleby-uc-25": "Review attach rates and multi-product quote carts",
 "middleby-uc-26": "Compare buyer paths and how they shift by brand over time",
 "middleby-uc-27": "Suggest cross-sell pairs from browse and quote patterns",
 "middleby-ex-01": "Explain conversion decline with funnel and trend context",
 "middleby-ex-02": "Compare GA brand sessions to HubSpot quote volume by brand",
 "middleby-ex-03": "Score lead quality from HubSpot quote export attributes",
 "middleby-ex-05": "Estimate revenue at risk when category demand outpaces quotes",
 "middleby-ex-06": "Executive growth dashboard across traffic, UX, and quotes",
};

/** Customer-facing subtitle for the Insights home priority strip (five Vivek themes). */
export const MIDDLEBY_PRIORITY_STRIP_SUBTITLE =
  "Five use cases of interest: RFQ funnel intelligence, product interest vs RFQ gap, customer journey visibility, UX insights, and executive weekly summary.";

/** Tier-A featured strip for Middleby Insights home (matches catalog use cases of interest). */
export const MIDDLEBY_TIER_A_IDS = MIDDLEBY_RECOMMENDED_DEMO_IDS;

/** Joined multi-source strip (row-level brand join). */
export const MIDDLEBY_JOINED_STRIP_IDS = ["middleby-uc-18"];

/** Side-by-side KPI panel strip (one row per source, not joined). */
export const MIDDLEBY_SUMMARY_STRIP_IDS = ["middleby-uc-23", "middleby-uc-07", "middleby-uc-16"];

/** @deprecated Use getMiddlebyJoinedStripUseCases + getMiddlebySummaryStripUseCases */
export const MIDDLEBY_STORY_STRIP_IDS = MIDDLEBY_SUMMARY_STRIP_IDS;

/** Role display metadata for retail/GFI home strips (not used on Middleby Insights home). */
export const ROLE_STYLE = {
 [DEMO_ROLE_EXECUTIVE]: {
 heading: "High-Impact Use Cases",
 badge: "Executive priority",
 badgeCls:
 "border border-primary/22 bg-primary-light text-primary dark:border-white/20 dark:bg-white/[0.1] dark:text-[#edebff]",
 iconCls: "text-primary dark:text-[#c9bffc]",
 cardHoverBorder: "hover:border-primary",
 areaBadgeCls: "bg-primary-light text-primary",
 },
 [DEMO_ROLE_OPERATIONS]: {
 heading: "Operational Priorities",
 badge: "Efficiency + execution",
 badgeCls:
 "border border-blue-200/85 bg-[#eff6ff] text-[#2563eb] dark:border-sky-500/35 dark:bg-sky-500/12 dark:text-sky-100",
 iconCls: "text-[#2563eb] dark:text-sky-300",
 cardHoverBorder: "hover:border-[#3b82f6]",
 areaBadgeCls: "bg-[#eff6ff] text-[#2563eb]",
 },
 [DEMO_ROLE_ANALYST]: {
 heading: "Analysis Workbench",
 badge: "Insights + diagnostics",
 badgeCls:
 "border border-border bg-surface-secondary text-text-secondary dark:border-border-light dark:bg-white/[0.06] dark:text-text-tertiary",
 iconCls: "text-text-secondary",
 cardHoverBorder: "hover:border-border",
 areaBadgeCls: "bg-surface-tertiary text-text-secondary",
 },
};

function resolveIds(profileId, role) {
 const byRole = ROLE_IDS_BY_PROFILE[profileId];
 if (!byRole) {
 return ROLE_IDS_BY_PROFILE[DEFAULT_DEMO_PROFILE_ID]?.[role] ?? [];
 }
 return byRole[role] ?? byRole[DEFAULT_DEMO_ROLE] ?? [];
}

export function getRoleIds(profileId, role = DEFAULT_DEMO_ROLE) {
 return resolveIds(profileId, role);
}

/** Resolve ordered use case objects for the active role. */
export function getUseCasesForRole(entries, profileId, role = DEFAULT_DEMO_ROLE) {
 // Media profile has no viewer roles — return the flat featured list.
 if (profileId === MEDIA_PROFILE_ID) {
 const byId = new Map((entries || []).map((e) => [e.id, e]));
 return MEDIA_FEATURED_UC_IDS.map((id) => byId.get(id)).filter(Boolean);
 }
 const ids = resolveIds(profileId, role);
 const byId = new Map((entries || []).map((e) => [e.id, e]));
 return ids.map((id) => byId.get(id)).filter(Boolean);
}

/** Middleby Insights home: fixed Tier-A strip (not role-filtered). */
export function getMiddlebyTierAUseCases(entries) {
 const byId = new Map((entries || []).map((e) => [e.id, e]));
 return MIDDLEBY_TIER_A_IDS.map((id) => byId.get(id)).filter(Boolean);
}

/** Middleby Insights home: row-level joined multi-source strip. */
export function getMiddlebyJoinedStripUseCases(entries) {
  const byId = new Map((entries || []).map((e) => [e.id, e]));
  return MIDDLEBY_JOINED_STRIP_IDS.map((id) => byId.get(id)).filter(Boolean);
}

/** Middleby Insights home: side-by-side KPI summary strip. */
export function getMiddlebySummaryStripUseCases(entries) {
  const byId = new Map((entries || []).map((e) => [e.id, e]));
  return MIDDLEBY_SUMMARY_STRIP_IDS.map((id) => byId.get(id)).filter(Boolean);
}

/** @deprecated Use getMiddlebySummaryStripUseCases */
export function getMiddlebyStoryStripUseCases(entries) {
  return getMiddlebySummaryStripUseCases(entries);
}

/** 0-based rank within the role list, or -1 if not in the list. */
export function roleRank(id, profileId, role = DEFAULT_DEMO_ROLE) {
 return resolveIds(profileId, role).indexOf(id);
}

export function isInRole(id, profileId, role = DEFAULT_DEMO_ROLE) {
 return roleRank(id, profileId, role) !== -1;
}

// ── Back-compat: executive = "featured" ──────────────────────────────────────

export function getFeaturedIds(profileId) {
 return resolveIds(profileId, DEMO_ROLE_EXECUTIVE);
}

export function getFeaturedUseCases(entries, profileId) {
 return getUseCasesForRole(entries, profileId, DEMO_ROLE_EXECUTIVE);
}

export function featuredRank(id, profileId) {
 return roleRank(id, profileId, DEMO_ROLE_EXECUTIVE);
}

export function isFeatured(id, profileId) {
 return featuredRank(id, profileId) !== -1;
}
