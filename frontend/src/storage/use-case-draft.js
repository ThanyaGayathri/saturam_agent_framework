import { readLocalJson, writeLocalJson } from "./local-json";
import { KPI_DATA_CATALOG_DATASETS } from "../domain/kpi-data-catalog";
import { planItemsForCatalogEntry } from "../domain/use-case-plan-items";

export const USE_CASE_DRAFT_VERSION = 2;

/** Maps builder Step 1 categories to `businessTags` on KPI Gallery datasets (prototype). */
const BUILDER_CATEGORY_TO_GALLERY_TAGS = {
  inventory: ["Inventory", "Supply Chain", "Merchandise"],
  orders: ["Orders"],
  "store-ops": ["Retail Performance", "POS"],
  ecom: ["E-Commerce"],
  pos: ["POS"],
  support: ["Support"],
  pricing: ["Pricing", "Finance"],
  merchandise: ["Merchandise"],
  finance: ["Finance"],
  workforce: ["Retail Performance"],
  default: [],
};

/** KPI Gallery dataset ids suggested for Data Scope (Step 3); capped for UI. */
export function getBuilderEnterpriseDatasetIds(category) {
  const key = typeof category === "string" && category ? category : "default";
  const tags = BUILDER_CATEGORY_TO_GALLERY_TAGS[key] ?? BUILDER_CATEGORY_TO_GALLERY_TAGS.default;
  const tagSet = new Set(tags);
  let rows = KPI_DATA_CATALOG_DATASETS.filter((d) => (d.businessTags || []).some((t) => tagSet.has(t)));
  if (rows.length === 0) rows = [...KPI_DATA_CATALOG_DATASETS];
  const seen = new Set();
  const out = [];
  for (const d of rows) {
    if (seen.has(d.id)) continue;
    seen.add(d.id);
    out.push(d.id);
    if (out.length >= 5) break;
  }
  return out;
}

// ── Question chips per business area (Step 2) ─────────────────────────────────
export const QUESTION_CHIPS_BY_AREA = {
  inventory: [
    "Which SKUs are at risk of stockout in the next 14 days?",
    "What products need urgent replenishment?",
    "Which suppliers have the longest lead times?",
    "Show me reorder recommendations by category.",
  ],
  orders: [
    "Which orders are at risk of missing SLA today?",
    "What is the exception rate by fulfillment center?",
    "Show me orders flagged for fraud review.",
    "Which delivery partners have the lowest on-time rate?",
  ],
  "store-ops": [
    "Where are we understaffed this weekend?",
    "Which stores have the highest task non-compliance?",
    "Show me foot traffic vs staffing correlation.",
    "What is the average checkout wait time by store?",
  ],
  ecom: [
    "Where are customers dropping off in the purchase funnel?",
    "Which products have the highest cart abandonment rate?",
    "What is our conversion rate by traffic source?",
    "Which promotions are driving the most incremental revenue?",
  ],
  pos: [
    "Which POS terminals have the highest failure rate?",
    "Show me transaction error trends over the past 30 days.",
    "What is the average queue time by store and hour?",
    "Which payment methods are causing most declines?",
  ],
  support: [
    "What are the top reasons customers are contacting support?",
    "Which stores have the highest return rate?",
    "Show me unresolved tickets older than 48 hours.",
    "What is the NPS trend over the past quarter?",
  ],
  pricing: [
    "Which products are over-indexed on markdown?",
    "Where is our pricing out of range vs competitors?",
    "Show me sell-through rates by category and week.",
    "Which promotions are diluting margin the most?",
  ],
  merchandise: [
    "Which categories are underperforming vs plan?",
    "Show me planogram compliance by store cluster.",
    "What is the performance of Q2 buys vs forecast?",
    "Which new range lines need range review?",
  ],
  finance: [
    "What is our gross margin trend vs budget?",
    "Where are we over-spending vs cost plan?",
    "Show me shrinkage loss by store cluster.",
    "What is the current EBITDA vs forecast?",
  ],
  workforce: [
    "Which stores have the highest overtime cost?",
    "Show me scheduling compliance by region.",
    "What is the labor cost as a percentage of revenue?",
    "Which departments have the highest turnover?",
  ],
};

// ── Suggested data sources per business area (Step 3) ────────────────────────
export const SUGGESTED_SOURCES_BY_AREA = {
  inventory: [
    { id: "postgresql-retail", name: "PostgreSQL - retail_db", description: "Tables: inventory, products, suppliers, sales_transactions", iconKey: "Server", enabled: true, syncAge: "4 mins ago" },
    { id: "snowflake-demand", name: "Snowflake - demand_warehouse", description: "Table: forecast_model_outputs", iconKey: "Snowflake", enabled: true, syncAge: "12 mins ago" },
  ],
  orders: [
    { id: "postgresql-orders", name: "PostgreSQL - orders_db", description: "Tables: orders, shipments, fulfillment, exceptions", iconKey: "Server", enabled: true, syncAge: "2 mins ago" },
    { id: "snowflake-ops", name: "Snowflake - ops_warehouse", description: "Tables: sla_tracking, delivery_partner_performance", iconKey: "Snowflake", enabled: true, syncAge: "8 mins ago" },
  ],
  "store-ops": [
    { id: "postgresql-hr", name: "PostgreSQL - hr_db", description: "Tables: staff_schedule, task_compliance, store_roster", iconKey: "Server", enabled: true, syncAge: "6 mins ago" },
    { id: "snowflake-traffic", name: "Snowflake - store_analytics", description: "Tables: foot_traffic, queue_times, checkout_stats", iconKey: "Snowflake", enabled: true, syncAge: "15 mins ago" },
  ],
  ecom: [
    { id: "ga4", name: "Google Analytics 4 - ecom_property", description: "Sessions, conversion funnel, traffic sources", iconKey: "Cloud", enabled: true, syncAge: "1 min ago" },
    { id: "shopify", name: "Shopify - retail_store", description: "Orders, products, cart abandonment, customer segments", iconKey: "Cloud", enabled: true, syncAge: "3 mins ago" },
  ],
  pos: [
    { id: "postgresql-pos", name: "PostgreSQL - pos_db", description: "Tables: transactions, terminal_errors, payment_methods", iconKey: "Server", enabled: true, syncAge: "2 mins ago" },
    { id: "snowflake-pos", name: "Snowflake - pos_analytics", description: "Tables: error_trends, queue_metrics, hourly_stats", iconKey: "Snowflake", enabled: true, syncAge: "10 mins ago" },
  ],
  default: [
    { id: "snowflake-main", name: "Snowflake - main_warehouse", description: "Primary analytics warehouse", iconKey: "Snowflake", enabled: true, syncAge: "5 mins ago" },
    { id: "postgresql-core", name: "PostgreSQL - core_db", description: "Core transactional database", iconKey: "Server", enabled: true, syncAge: "8 mins ago" },
  ],
};

export const ADDITIONAL_SOURCES = [
  { id: "mysql-legacy", name: "MySQL - legacy_db", description: "Legacy transactional records", iconKey: "Server", enabled: false, syncAge: "7 days ago" },
  { id: "azure-blob", name: "Azure Blob - reports_container", description: "Scheduled report exports and CSV archives", iconKey: "Cloud", enabled: false, syncAge: "1 day ago" },
  { id: "salesforce", name: "Salesforce - CRM", description: "Customer profiles, loyalty data, support tickets", iconKey: "Cloud", enabled: false, syncAge: "30 mins ago" },
];

// ── Media profile: suggested sources per (media) business area ──────────────
export const MEDIA_SUGGESTED_SOURCES_BY_AREA = {
  merchandise: [
    { id: "content-catalog", name: "PostgreSQL - content_catalog_db", description: "Titles, genre, language, rights window, licensing cost", iconKey: "Server", enabled: true, syncAge: "1 hr ago" },
    { id: "social-listening", name: "Social listening - Twitter, Instagram, YouTube", description: "Topic, entity, sentiment, and volume signals", iconKey: "Cloud", enabled: true, syncAge: "15 mins ago" },
    { id: "similarweb", name: "Similarweb - competitor traffic", description: "Competitor site + app traffic and engagement", iconKey: "Cloud", enabled: true, syncAge: "1 day ago" },
  ],
  "e-com": [
    { id: "ott-events", name: "BigQuery - ott_events", description: "Play and session events across web / mobile / CTV", iconKey: "Cloud", enabled: true, syncAge: "5 mins ago" },
    { id: "ga4-media", name: "GA4 - media discovery", description: "Web + mobile discovery events and referrers", iconKey: "Cloud", enabled: true, syncAge: "45 mins ago" },
    { id: "ai-answers", name: "AEO / GEO - ChatGPT, Perplexity, Google AI", description: "Prompt-set citations across AI answer engines", iconKey: "Cloud", enabled: true, syncAge: "6 hrs ago" },
  ],
  support: [
    { id: "subscribers-crm", name: "PostgreSQL - subscribers_db", description: "Active plans, renewal status, dunning attempts", iconKey: "Server", enabled: true, syncAge: "15 mins ago" },
    { id: "care-contacts", name: "Salesforce - subscriber care", description: "Care contacts, escalations, resolution times", iconKey: "Cloud", enabled: true, syncAge: "1 hr ago" },
  ],
  pricing: [
    { id: "subscribers-crm-p", name: "PostgreSQL - subscribers_db", description: "Plan tier, ARPU, upgrade / downgrade history", iconKey: "Server", enabled: true, syncAge: "15 mins ago" },
    { id: "competitor-pricing", name: "Similarweb - competitor pricing", description: "Public plan and pricing changes across rival platforms", iconKey: "Cloud", enabled: true, syncAge: "1 day ago" },
  ],
  finance: [
    { id: "ad-server-gam", name: "BigQuery - GAM ad delivery", description: "Ad impressions, fill status, demand partner, yield", iconKey: "Cloud", enabled: true, syncAge: "30 mins ago" },
    { id: "content-catalog-fin", name: "PostgreSQL - content_catalog_db", description: "Acquisition and licensing cost per title", iconKey: "Server", enabled: true, syncAge: "1 hr ago" },
  ],
  default: [
    { id: "ott-events-d", name: "BigQuery - ott_events", description: "Play and session events across web / mobile / CTV", iconKey: "Cloud", enabled: true, syncAge: "5 mins ago" },
    { id: "content-catalog-d", name: "PostgreSQL - content_catalog_db", description: "Titles, genre, language, rights window", iconKey: "Server", enabled: true, syncAge: "1 hr ago" },
  ],
};

export const MEDIA_ADDITIONAL_SOURCES = [
  { id: "google-trends", name: "Google Trends - search interest", description: "Normalized search interest by term and region", iconKey: "Cloud", enabled: false, syncAge: "12 hrs ago" },
  { id: "cdn-logs", name: "Snowflake - CDN logs", description: "Regional streaming performance and buffer health", iconKey: "Snowflake", enabled: false, syncAge: "2 days ago" },
  { id: "barc-ratings", name: "BARC India - linear TV ratings", description: "Linear TV impressions, share, reach by channel", iconKey: "Cloud", enabled: false, syncAge: "1 day ago" },
];

export const MEDIA_SUGGESTED_KNOWLEDGE_BY_AREA = {
  merchandise: [
    { id: "content-programming-playbook", name: "Content Programming Playbook", description: "Slate strategy, marquee-title standards, and regional-language balance", enabled: true },
    { id: "commissioning-brief-template", name: "Commissioning Brief Template", description: "Standard brief structure for new-title research and greenlight", enabled: true },
    { id: "trend-explainability-standard", name: "Trend Explainability Standard", description: "Fact / inferred trend / recommendation separation for content briefs", enabled: true },
  ],
  "e-com": [
    { id: "personalisation-guardrails", name: "Personalisation Guardrails", description: "Homepage row rules, editorial vs. algorithmic slot policy", enabled: true },
    { id: "aeo-geo-editorial-standard", name: "AEO / GEO Editorial Standard", description: "How to shape title metadata and long-form editorial for AI answer engines", enabled: true },
  ],
  support: [
    { id: "care-playbook", name: "Subscriber Care Playbook", description: "Escalation ladder, save-offer eligibility, refund policy", enabled: true },
    { id: "piracy-response-runbook", name: "Piracy Response Runbook", description: "Legal-authorized takedown workflow and escalation triggers", enabled: true },
  ],
  pricing: [
    { id: "plan-pricing-policy", name: "Plan Pricing Policy", description: "Approved plan tiers, bundle rules, and telco partnership terms", enabled: true },
    { id: "competitor-pricing-tracker-m", name: "Competitor Pricing Tracker", description: "Latest OTT competitor plan-price and bundle changes", enabled: true },
  ],
  finance: [
    { id: "ad-yield-runbook", name: "Ad Yield Runbook", description: "Programmatic floor / bidder priority tuning guide by device", enabled: true },
    { id: "content-roi-standards", name: "Content ROI Standards", description: "How we attribute revenue to titles and read cost per watch hour", enabled: true },
  ],
  default: [
    { id: "operations-manual-media", name: "Operations Manual", description: "Standard operating procedures for OTT and broadcast operations", enabled: true },
    { id: "kpi-definitions-media", name: "KPI Definitions Library", description: "Agreed definitions and targets for media & OTT KPIs", enabled: true },
  ],
};

export const MEDIA_ADDITIONAL_KNOWLEDGE = [
  { id: "regional-language-strategy", name: "Regional-Language Strategy", description: "Hindi vs. regional catalog investment and commissioning priorities", enabled: false },
  { id: "brand-safety-guidelines", name: "Brand Safety Guidelines", description: "Ad-adjacency rules for sensitive content and creative approvals", enabled: false },
];

/** Profile-aware suggested-sources lookup for the Step 3 Data Scope panel. */
export function getSuggestedSourcesByArea(area, profileId) {
  const isMedia = profileId === "default-media";
  const map = isMedia ? MEDIA_SUGGESTED_SOURCES_BY_AREA : SUGGESTED_SOURCES_BY_AREA;
  return map[area] ?? map.default;
}

/** Profile-aware additional-sources list (below-the-fold in Step 3). */
export function getAdditionalSources(profileId) {
  return profileId === "default-media" ? MEDIA_ADDITIONAL_SOURCES : ADDITIONAL_SOURCES;
}

/** Profile-aware suggested-knowledge lookup for the Step 3 Data Scope panel. */
export function getSuggestedKnowledgeByArea(area, profileId) {
  const isMedia = profileId === "default-media";
  const map = isMedia ? MEDIA_SUGGESTED_KNOWLEDGE_BY_AREA : SUGGESTED_KNOWLEDGE_BY_AREA;
  return map[area] ?? map.default;
}

/** Profile-aware additional-knowledge list. */
export function getAdditionalKnowledge(profileId) {
  return profileId === "default-media" ? MEDIA_ADDITIONAL_KNOWLEDGE : ADDITIONAL_KNOWLEDGE;
}

// ── Suggested knowledge bases per business area (Step 3) ─────────────────────
export const SUGGESTED_KNOWLEDGE_BY_AREA = {
  inventory: [
    { id: "reorder-policy", name: "Reorder Policy Playbook", description: "Minimum stock thresholds and reorder rules by category", enabled: true },
    { id: "supplier-lead", name: "Supplier Lead Time Rules", description: "Standard and express lead times per supplier", enabled: true },
    { id: "buying-guide", name: "Category Buying Guide", description: "Seasonal demand patterns and preferred vendor list", enabled: true },
  ],
  orders: [
    { id: "sla-policy", name: "SLA Policy Document", description: "Delivery SLA targets by region and customer tier", enabled: true },
    { id: "fraud-rules", name: "Fraud Detection Rules", description: "Rule set for order anomaly flagging and review", enabled: true },
  ],
  "store-ops": [
    { id: "staffing-model", name: "Staffing Model Guidelines", description: "Labor ratios by store format and footfall band", enabled: true },
    { id: "compliance-checklist", name: "Store Compliance Checklist", description: "Required daily and weekly task list by role", enabled: true },
  ],
  pricing: [
    { id: "markdown-policy", name: "Markdown Policy", description: "Markdown rules by category, age of stock, and season", enabled: true },
    { id: "competitor-tracker", name: "Competitor Pricing Tracker", description: "Price index vs key competitors by category", enabled: true },
  ],
  default: [
    { id: "operations-manual", name: "Operations Manual", description: "Standard operating procedures and business rules", enabled: true },
    { id: "kpi-definitions", name: "KPI Definitions Library", description: "Agreed definitions, formulas, and targets for all KPIs", enabled: true },
  ],
};

export const ADDITIONAL_KNOWLEDGE = [
  { id: "returns-policy", name: "Returns Policy Playbook", description: "Accepted return conditions, timeframes, and handling rules", enabled: false },
  { id: "promo-calendar", name: "Promotional Calendar 2026", description: "Planned promotions, black-out periods, and uplift expectations", enabled: false },
  { id: "sustainability", name: "Sustainability Guidelines", description: "Carbon targets and supplier sustainability requirements", enabled: false },
];

// ── Contextual plan items seeded per question / category (Step 4) ────────────
// Keys are question chip strings (exact match first, then category fallback).
// Each item can use {source1}, {source2}, {knowledge1} as placeholders
// which get filled from confirmed sources/knowledge at resolve time.
export const PLAN_ITEMS_BY_QUESTION = {
  // ── Inventory ──
  "Which SKUs are at risk of stockout in the next 14 days?": [
    { title: "Fetch current inventory levels from {source1}", description: "Query inventory and products tables for all active SKUs. Filter to those with on-hand quantity below 30-day average sales velocity." },
    { title: "Apply 14-day demand forecast from {source2}", description: "Run the demand forecast model on each SKU using 90-day sales history. Calculate days-of-cover remaining at current sell rate." },
    { title: "Apply reorder thresholds from {knowledge1}", description: "Cross-reference each at-risk SKU against minimum stock thresholds and reorder rules by category from the confirmed knowledge base." },
    { title: "Rank SKUs by urgency and flag stockout risk", description: "Score each SKU by days-of-cover remaining, supplier lead time, and seasonal uplift. Flag any SKU with fewer than 14 days cover as at-risk." },
    { title: "Generate reorder recommendations and route for action", description: "Produce a ranked reorder list with suggested quantities and preferred suppliers. Route as Decision Cards for approval (Co-Pilot) or auto-raise POs within guardrails (Autopilot)." },
  ],
  "What products need urgent replenishment?": [
    { title: "Fetch stock-on-hand and pending orders from {source1}", description: "Query inventory and open purchase orders. Identify products where current stock plus incoming supply does not cover next 7 days of demand." },
    { title: "Calculate demand run-rate from {source2}", description: "Use recent sales velocity to estimate daily demand per product. Factor in any upcoming promotions or seasonal events." },
    { title: "Cross-reference supplier lead times from {knowledge1}", description: "For each at-risk product, look up standard supplier lead times. Prioritise where lead time exceeds remaining days-of-cover." },
    { title: "Build urgency-ranked replenishment list", description: "Sort products by criticality: days-of-cover vs lead time gap. Flag products where a stockout is unavoidable unless expedited shipping is used." },
    { title: "Surface replenishment actions for review", description: "Present the ranked list with suggested order quantities, estimated costs, and recommended suppliers. Route for approval or auto-execute within spend guardrails." },
  ],
  "Which suppliers have the longest lead times?": [
    { title: "Fetch purchase order history from {source1}", description: "Query historical POs and goods receipts from the last 12 months. Calculate actual lead time (order date to receipt date) per supplier." },
    { title: "Aggregate lead time statistics by supplier", description: "Compute average, median, and 90th-percentile lead times. Flag suppliers with high variance as additional risk." },
    { title: "Cross-reference expected lead times from {knowledge1}", description: "Compare actual lead times against contracted SLAs from the supplier knowledge base. Identify where suppliers are consistently missing targets." },
    { title: "Rank suppliers by lead time risk", description: "Score suppliers on average lead time, variance, and SLA compliance rate. Flag the top outliers for review." },
    { title: "Generate supplier performance summary", description: "Produce a ranked table of lead time performance. Highlight suppliers where lead times are creating stockout risk and suggest alternatives where available." },
  ],
  // ── Store Ops ──
  "Where are we understaffed this weekend?": [
    { title: "Fetch weekend roster from {source1}", description: "Query confirmed staff schedules for Saturday and Sunday across all stores. Identify stores with unfilled shifts or below-minimum headcount." },
    { title: "Forecast foot traffic and transaction volume from {source2}", description: "Apply demand model to predict hourly customer volume by store for this weekend. Factor in any local events or promotions." },
    { title: "Apply staffing ratio rules from {knowledge1}", description: "Calculate required headcount per store and hour using the staffing model guidelines for each store format and projected footfall band." },
    { title: "Identify understaffed stores and time slots", description: "Compare required vs confirmed headcount for each store-hour combination. Flag any slot that falls below minimum coverage threshold." },
    { title: "Generate staffing gap report and cover options", description: "Produce a prioritised list of gaps by store and shift. Suggest cover options including overtime, internal transfers, or agency staff. Route for manager approval." },
  ],
  "Which stores have the highest task non-compliance?": [
    { title: "Fetch task completion records from {source1}", description: "Query task compliance records for the past 30 days. Retrieve required vs completed tasks by store, role, and task type." },
    { title: "Calculate compliance rates by store from {source2}", description: "Compute completion rate, overdue rate, and repeat-miss rate per store. Identify stores with compliance below the defined threshold." },
    { title: "Cross-reference compliance requirements from {knowledge1}", description: "Apply the store compliance checklist to validate which task categories are most frequently missed and which carry the highest operational risk." },
    { title: "Rank stores by compliance risk", description: "Score stores on overall compliance rate, trend direction, and severity of missed tasks. Surface the bottom 10% for immediate attention." },
    { title: "Generate non-compliance report with root cause signals", description: "Produce a ranked store list with compliance scores, most-missed task categories, and trend vs prior period. Route summary to store managers for review." },
  ],
  // ── Orders ──
  "Which orders are at risk of missing SLA today?": [
    { title: "Fetch open orders and current fulfilment status from {source1}", description: "Query all orders placed in the last 48 hours that are not yet shipped or delivered. Join with fulfilment events to get current processing stage." },
    { title: "Calculate time-to-SLA for each order from {source2}", description: "Compute hours remaining to SLA deadline for each open order. Apply SLA rules by order tier, region, and delivery method." },
    { title: "Apply SLA rules and exception flags from {knowledge1}", description: "Cross-reference each order against the SLA policy thresholds. Flag orders in the final 20% of their SLA window as at-risk." },
    { title: "Rank at-risk orders by urgency and root cause", description: "Score at-risk orders by time remaining, order value, and customer tier. Group by root cause (warehouse delay, carrier hold, stock shortage)." },
    { title: "Surface SLA exceptions for action", description: "Produce an at-risk order list with time-to-breach, root cause, and suggested intervention. Route as Decision Cards for ops team review or auto-escalate within guardrails." },
  ],
  // ── E-Com ──
  "Where are customers dropping off in the purchase funnel?": [
    { title: "Fetch funnel event data from {source1}", description: "Query session-level events from the analytics platform: page views, product views, add-to-cart, checkout start, and purchase events for the last 30 days." },
    { title: "Calculate step-by-step conversion rates from {source2}", description: "Compute drop-off rates at each funnel stage. Segment by traffic source, device type, and product category to identify where the biggest losses occur." },
    { title: "Identify high-impact drop-off points", description: "Rank funnel stages by absolute visitor loss and revenue impact. Focus on stages where a 10% improvement would have the highest revenue uplift." },
    { title: "Cross-reference with product and session data", description: "Join funnel drop-offs with product performance data to identify if specific categories, price points, or page load times are contributing to abandonment." },
    { title: "Generate funnel analysis report with recommendations", description: "Produce a funnel breakdown with annotated drop-off points, contributing factors, and prioritised improvement recommendations. Present as a visualised data output." },
  ],
  // ── POS ──
  "Which POS terminals have the highest failure rate?": [
    { title: "Fetch terminal error logs from {source1}", description: "Query transaction error records for all POS terminals over the past 30 days. Retrieve error type, frequency, terminal ID, and store location." },
    { title: "Calculate failure rates and error patterns from {source2}", description: "Compute errors-per-transaction ratio by terminal. Identify terminals with rates above the fleet average and classify recurring error types." },
    { title: "Rank terminals by failure severity", description: "Score each terminal by error frequency, error type severity, and impact on checkout throughput. Flag terminals where failure rate has increased week-on-week." },
    { title: "Cross-reference maintenance history", description: "Check last service date and known hardware issues for high-failure terminals. Identify whether failures correlate with age, software version, or usage volume." },
    { title: "Generate terminal health report and action list", description: "Produce a ranked list of terminals requiring attention with failure rate, error type breakdown, and recommended action (restart, service, replacement)." },
  ],
  // ── Pricing ──
  "Which products are over-indexed on markdown?": [
    { title: "Fetch current pricing and markdown events from {source1}", description: "Query active markdowns, discount depth, and markdown history by product and category for the current season." },
    { title: "Calculate markdown intensity vs sell-through from {source2}", description: "Compute markdown depth as a percentage of original price. Compare against sell-through rate to identify products receiving heavy discounts without proportional sell-through improvement." },
    { title: "Apply markdown policy rules from {knowledge1}", description: "Cross-reference markdown depth and duration against the markdown policy thresholds by category, age-of-stock, and season position." },
    { title: "Identify over-indexed products and margin impact", description: "Flag products where markdown depth exceeds policy limits or where incremental sell-through does not justify the margin cost. Estimate total margin erosion." },
    { title: "Generate markdown review list with recommendations", description: "Produce a ranked list of over-indexed products with suggested corrective actions: reduce depth, exit the markdown, or apply a targeted promotion instead." },
  ],
};

// Category-level fallback plan items (used when question doesn't match a seed)
export const PLAN_ITEMS_BY_CATEGORY = {
  inventory: [
    { title: "Fetch current inventory and demand data from {source1}", description: "Query stock levels, sales history, and replenishment records across all active SKUs. Validate data freshness before analysis." },
    { title: "Apply demand forecasting model from {source2}", description: "Run the configured forecast model to project demand over the analysis window. Identify SKUs trending above or below expected levels." },
    { title: "Cross-reference business rules from {knowledge1}", description: "Apply reorder thresholds, lead time rules, and category buying guidelines to filter and prioritise findings." },
    { title: "Identify exceptions and at-risk items", description: "Score each SKU against the forecast and rules. Rank by risk level and potential business impact." },
    { title: "Generate recommendations and route for action", description: "Produce a ranked action list. Route as Decision Cards for approval (Co-Pilot) or execute within guardrails (Autopilot)." },
  ],
  orders: [
    { title: "Fetch open orders and fulfilment status from {source1}", description: "Query all in-flight orders and their current processing stage. Join with SLA targets and carrier status data." },
    { title: "Calculate SLA exposure and exception rates from {source2}", description: "Identify orders at risk of breaching SLA. Compute exception rates by fulfilment centre, carrier, and order type." },
    { title: "Apply SLA policy rules from {knowledge1}", description: "Cross-reference order status against SLA thresholds and exception handling rules from the policy knowledge base." },
    { title: "Rank exceptions by urgency and root cause", description: "Group at-risk orders by root cause. Prioritise by time-to-breach, order value, and customer tier." },
    { title: "Surface exceptions for action", description: "Produce a prioritised exception list. Route to operations team via Decision Cards or auto-escalate within guardrails." },
  ],
  "store-ops": [
    { title: "Fetch staffing schedules and task records from {source1}", description: "Query confirmed rosters, task completion logs, and store compliance records across all locations." },
    { title: "Model demand and coverage requirements from {source2}", description: "Apply foot traffic forecasts and transaction volume models to calculate required coverage by store and hour." },
    { title: "Apply staffing and compliance rules from {knowledge1}", description: "Cross-reference actual vs required headcount and task completion against the staffing model and compliance checklist." },
    { title: "Identify gaps and non-compliance by store", description: "Flag stores with understaffing or compliance issues. Rank by severity and operational risk." },
    { title: "Generate store action plan", description: "Produce a prioritised list of stores requiring action with suggested interventions. Route for manager review." },
  ],
  ecom: [
    { title: "Fetch session and conversion data from {source1}", description: "Query funnel events, session data, and purchase records from the analytics platform for the configured time window." },
    { title: "Calculate conversion rates and drop-off points from {source2}", description: "Compute step-by-step conversion rates. Segment by traffic source, device, and product category." },
    { title: "Identify highest-impact optimisation opportunities", description: "Rank funnel stages and segments by revenue impact of a 10% conversion improvement." },
    { title: "Cross-reference product and promotion data", description: "Join conversion signals with product performance and active promotions to identify contributing factors." },
    { title: "Generate optimisation report with recommendations", description: "Produce an annotated funnel breakdown with prioritised recommendations and estimated revenue uplift." },
  ],
  pricing: [
    { title: "Fetch pricing and markdown data from {source1}", description: "Query current prices, markdown events, and competitor index data for the configured category and time window." },
    { title: "Calculate margin and sell-through by product", description: "Compute markdown depth, sell-through rate, and gross margin impact by product and category." },
    { title: "Apply markdown policy rules from {knowledge1}", description: "Cross-reference markdown depth and duration against policy thresholds. Flag products outside approved ranges." },
    { title: "Identify pricing exceptions and opportunities", description: "Rank products by over-markdown risk, competitive gap, and margin recovery opportunity." },
    { title: "Generate pricing action list", description: "Produce a prioritised list of pricing interventions with recommended actions and expected margin impact." },
  ],
  merchandise: [
    { title: "Fetch assortment and sell-through data from {source1}", description: "Query current assortment, sell-through rates, and inventory positions across categories and stores." },
    { title: "Analyze performance vs plan from {source2}", description: "Compare actual sell-through against category plans. Identify over- and under-performing SKUs and gaps in assortment coverage." },
    { title: "Apply assortment rules from {knowledge1}", description: "Cross-reference performance against category strategy, brand mix, and planogram rules from the knowledge base." },
    { title: "Rank assortment opportunities and risks", description: "Score each SKU against target performance and category goals. Highlight introduction, retirement, and rebalancing candidates." },
    { title: "Generate merchandising action list", description: "Produce ranked recommendations for assortment changes, planogram updates, or promotional adjustments." },
  ],
  pos: [
    { title: "Fetch POS transaction and hardware telemetry from {source1}", description: "Query transaction logs, peripheral status, and error events across all POS terminals and stores." },
    { title: "Detect anomalies and failure patterns from {source2}", description: "Apply anomaly detection to identify terminals or peripherals trending toward failure. Correlate with recent releases or maintenance events." },
    { title: "Apply POS uptime rules from {knowledge1}", description: "Cross-reference failure signals against SLA thresholds and maintenance schedules to prioritise interventions." },
    { title: "Rank hotspots by service impact", description: "Group affected terminals by store and service impact. Estimate lost transactions and customer wait-time impact." },
    { title: "Generate field service request list", description: "Produce a prioritised list of dispatches, preventive maintenance actions, or configuration changes for the field service team." },
  ],
  support: [
    { title: "Fetch support ticket and contact data from {source1}", description: "Query current ticket volumes, wait times, resolution rates, and escalation patterns across channels." },
    { title: "Model contact demand and staffing from {source2}", description: "Apply forecast models to project contact volume by hour and channel. Compare against current staffing and utilisation." },
    { title: "Apply escalation and staffing rules from {knowledge1}", description: "Cross-reference wait-time thresholds, agent tier authority, and escalation policy from the support knowledge base." },
    { title: "Identify at-risk queues and escalation drivers", description: "Rank contact queues and escalation categories by customer impact and cost of missed SLA." },
    { title: "Generate support action list", description: "Produce a prioritised list of staffing changes, overflow enablement, or knowledge base updates for the support team." },
  ],
  finance: [
    { title: "Fetch financial and margin data from {source1}", description: "Query gross margin, OpEx, and contribution margin data by category, store, and time window." },
    { title: "Analyze margin drivers and variance from {source2}", description: "Decompose margin movement by product mix, price, cost, and markdown. Identify the largest movers and outliers." },
    { title: "Apply finance policy rules from {knowledge1}", description: "Cross-reference variance against tolerance thresholds, close cadence, and reporting policy from the knowledge base." },
    { title: "Rank financial exceptions and opportunities", description: "Score categories and cost centres by variance magnitude, materiality, and time-to-close impact." },
    { title: "Generate finance action list", description: "Produce a prioritised list of margin actions, cost interventions, or vendor negotiations with recommended next steps." },
  ],
  workforce: [
    { title: "Fetch schedule, attendance, and payroll data from {source1}", description: "Query current schedules, actual hours, overtime, and adherence data across teams and stores." },
    { title: "Model labor demand and utilisation from {source2}", description: "Apply demand forecasts to calculate required coverage. Compare against actual staffing and utilisation." },
    { title: "Apply workforce policy rules from {knowledge1}", description: "Cross-reference overtime approval limits, break compliance, and scheduling policy from the knowledge base." },
    { title: "Identify workforce risks and hotspots", description: "Rank teams by overtime creep, coverage gaps, or compliance risk. Highlight burnout and utilisation outliers." },
    { title: "Generate workforce action list", description: "Produce a prioritised list of schedule adjustments, cross-training moves, or overtime approvals with recommended actions." },
  ],
};

// ── Execution stats seeded per question / category (Step 9) ──────────────────
export const EXECUTION_STATS_BY_QUESTION = {
  "Which SKUs are at risk of stockout in the next 14 days?":        { records: "1,247 SKUs analysed", findings: "34 at-risk SKUs", actions: "34 reorder recommendations generated" },
  "What products need urgent replenishment?":                        { records: "892 products scanned", findings: "18 need urgent reorder", actions: "18 purchase orders prepared" },
  "Which suppliers have the longest lead times?":                    { records: "47 active suppliers evaluated", findings: "9 suppliers above threshold", actions: "9 supplier performance flags raised" },
  "Where are we understaffed this weekend?":                         { records: "127 shifts across 43 stores", findings: "12 stores understaffed", actions: "12 gap reports sent to store managers" },
  "Which stores have the highest task non-compliance?":              { records: "4,830 tasks reviewed across 89 stores", findings: "11 stores below compliance threshold", actions: "11 store compliance alerts created" },
  "Which orders are at risk of missing SLA today?":                  { records: "3,412 open orders checked", findings: "67 orders at SLA risk", actions: "67 exception cards queued for ops review" },
  "Where are customers dropping off in the purchase funnel?":        { records: "142,000 sessions analysed", findings: "3 high-impact drop-off points found", actions: "3 optimisation recommendations prepared" },
  "Which POS terminals have the highest failure rate?":              { records: "318 terminals across 89 stores", findings: "14 terminals above failure threshold", actions: "14 maintenance requests prepared" },
  "Which products are over-indexed on markdown?":                    { records: "2,140 active markdown events reviewed", findings: "28 products over-indexed", actions: "28 markdown adjustment recommendations prepared" },
};

export const EXECUTION_STATS_BY_CATEGORY = {
  inventory:  { records: "1,200+ SKUs analysed", findings: "At-risk items identified",      actions: "Reorder recommendations prepared" },
  orders:     { records: "3,000+ orders checked",  findings: "SLA exceptions identified",    actions: "Exception cards queued for review" },
  "store-ops":{ records: "100+ stores evaluated",  findings: "Gaps and non-compliance found", actions: "Action items sent to store managers" },
  ecom:       { records: "100K+ sessions analysed", findings: "Drop-off points identified",  actions: "Optimisation recommendations prepared" },
  pricing:    { records: "2,000+ products reviewed", findings: "Pricing exceptions found",   actions: "Adjustment recommendations prepared" },
};

export function resolveExecutionStats(question, category) {
  if (question && EXECUTION_STATS_BY_QUESTION[question]) return EXECUTION_STATS_BY_QUESTION[question];
  if (category && EXECUTION_STATS_BY_CATEGORY[category]) return EXECUTION_STATS_BY_CATEGORY[category];
  return { records: "Records analysed", findings: "Exceptions identified", actions: "Actions prepared" };
}

// ── Derive Step 5 tasks from Step 4 plan items ────────────────────────────────
// Infer agent type from title keywords and position
function inferAgentType(title, idx, total) {
  const t = title.toLowerCase();
  if (idx === 0) return "retriever";
  if (t.includes("display") || t.includes("surface") || t.includes("present") || t.includes("kpi")) return "display";
  if (t.includes("generat") || t.includes("recommend") || t.includes("rank") || t.includes("route") || t.includes("deliver") || idx === total - 1) return idx === total - 1 ? "action" : "planning";
  if (t.includes("apply") || t.includes("run") || t.includes("calculat") || t.includes("comput") || t.includes("identify") || t.includes("analys")) return "analytics";
  if (t.includes("cross-ref") || t.includes("validat") || t.includes("check") || t.includes("flag")) return "guard";
  return "analytics";
}

function inferOutputType(title, idx, total, autonomyLevel) {
  if (idx < total - 1) {
    const t = title.toLowerCase();
    if (t.includes("display") || t.includes("surface") || t.includes("present") || t.includes("dashboard") || t.includes("chart")) return "display-data";
    if (t.includes("kpi")) return "display-kpi";
    if (t.includes("alert") || t.includes("notify") || t.includes("send")) return "alert";
    return "action";
  }
  // Last task output type depends on autonomy mode
  return autonomyLevel === "autopilot" ? "auto-execute" : "decision-card";
}

export function deriveTasksFromPlanItems(planItems, autonomyLevel = "copilot") {
  return planItems.map((item, i) => ({
    id: `t${i + 1}`,
    name: item.title,
    agentType: inferAgentType(item.title, i, planItems.length),
    outputType: inferOutputType(item.title, i, planItems.length, autonomyLevel),
    estimatedTime: i === 0 ? "< 1 min" : i === planItems.length - 1 ? "< 1 min" : "~1 min",
    chartType: "table",
    kpiIds: [],
  }));
}

// Resolve contextual plan items. Priority:
//   1. Per-catalog-id hand-authored override (highest-visibility use cases)
//   2. Entry-derived generator (uses catalog entry's title / problemSignal /
//      items[0] so every seeded use case gets bespoke-feeling steps)
//   3. Question-specific template (PLAN_ITEMS_BY_QUESTION - seed questions)
//   4. Title-derived fallback (Builder wizard when only a title exists)
//   5. Category template (last resort - Builder wizard mid-configuration)
//
// Both 1 and 2 live in domain/use-case-plan-items.js. Category templates
// intentionally drop to the bottom of the chain because "same category ->
// same 5 steps" is exactly what the user complained about (e.g. NPI
// Tracking and Vendor Scorecards both showing merchandise-category steps).
export function resolveContextualPlanItems(
  question,
  category,
  confirmedSources,
  confirmedKnowledge,
  useCaseTitle = "",
  catalogEntry = null
) {
  const source1 = confirmedSources[0]?.name ?? "the primary data source";
  const source2 = confirmedSources[1]?.name ?? "the secondary data source";
  const knowledge1 = confirmedKnowledge[0]?.name ?? "the knowledge base";

  const fill = (items) =>
    items.map((item) => ({
      title: item.title.replace("{source1}", source1).replace("{source2}", source2).replace("{knowledge1}", knowledge1),
      description: item.description.replace("{source1}", source1).replace("{source2}", source2).replace("{knowledge1}", knowledge1),
    }));

  // Layer 1 + 2: catalog-entry-scoped resolution (per-id override or
  // entry-derived generator). Preferred whenever we know which specific
  // seed use case this is.
  if (catalogEntry) {
    const fromEntry = planItemsForCatalogEntry(catalogEntry, source1, source2, knowledge1);
    if (fromEntry) return fromEntry;
  }

  // Layer 3: seed question template.
  if (question && PLAN_ITEMS_BY_QUESTION[question]) {
    return fill(PLAN_ITEMS_BY_QUESTION[question]);
  }

  // Layer 4: title-derived fallback so a Builder-in-progress draft with a
  // title but no catalog id still gets steps that reference that title.
  const focus = String(useCaseTitle || "").trim();
  if (focus) {
    return [
      { title: `Fetch data supporting ${focus} from ${source1}`, description: `Query the confirmed data source for records relevant to ${focus}. Validate freshness before processing.` },
      { title: `Detect ${focus} signals from ${source2}`, description: `Run pattern detection on the retrieved data to surface anomalies and trends specific to ${focus}.` },
      { title: `Apply business rules from ${knowledge1}`, description: `Cross-reference ${focus} findings against policy thresholds and business rules from the knowledge base.` },
      { title: `Rank ${focus} findings by impact and urgency`, description: `Score each finding by business impact and time sensitivity. Highlight the most actionable items first.` },
      { title: `Generate ${focus} action list`, description: `Produce a prioritised list of recommended actions for ${focus}. Route as Decision Cards for approval or execute within guardrails.` },
    ];
  }

  // Layer 5: category template (last resort - only reached if no title AND
  // no catalog id are available, i.e. mid-wizard on a brand-new draft).
  if (category && PLAN_ITEMS_BY_CATEGORY[category]) {
    return fill(PLAN_ITEMS_BY_CATEGORY[category]);
  }

  // Final safety net - should be unreachable in practice.
  return [
    { title: `Fetch data from ${source1}`, description: `Query the confirmed data source. Validate freshness before processing.` },
    { title: `Detect signals from ${source2}`, description: `Run pattern detection on the retrieved data.` },
    { title: `Apply business rules from ${knowledge1}`, description: `Cross-reference findings against policy thresholds and business rules.` },
    { title: `Rank findings by impact and urgency`, description: `Score each finding by business impact and time sensitivity.` },
    { title: `Generate action list`, description: `Produce a prioritised list of recommended actions.` },
  ];
}

// ── AI plan items (Step 4) ────────────────────────────────────────────────────
export const DEFAULT_AI_PLAN_ITEMS = [
  {
    title: "Fetch and validate data from connected sources",
    description: "Query confirmed data sources to retrieve current records. Validate schema compatibility and data freshness before processing begins.",
  },
  {
    title: "Apply analysis and pattern recognition",
    description: "Run analytical models against the retrieved data to identify anomalies, trends, and at-risk conditions relevant to your question.",
  },
  {
    title: "Cross-reference with knowledge bases",
    description: "Apply business rules from confirmed knowledge bases (policies, thresholds, playbooks) to filter and prioritize findings.",
  },
  {
    title: "Generate recommendations and display outputs",
    description: "Produce ranked recommendations, data tables, and KPI tiles based on the analysis and knowledge-base rules.",
  },
  {
    title: "Deliver results and trigger actions",
    description: "Send outputs to configured channels. For Co-Pilot: surface Decision Cards for approval. For Autopilot: execute within guardrails and log all actions taken.",
  },
];

// ── Default tasks for Plan Breakdown (Step 5) ─────────────────────────────────
export const DEFAULT_TASKS = [
  { id: "t1", name: "Fetch current data from sources", agentType: "retriever", outputType: "action", estimatedTime: "< 1 min", chartType: "table", kpiIds: [] },
  { id: "t2", name: "Apply demand and trend analysis", agentType: "analytics", outputType: "action", estimatedTime: "~2 min", chartType: "table", kpiIds: [] },
  { id: "t3", name: "Identify at-risk items", agentType: "analytics", outputType: "display-data", estimatedTime: "~1 min", chartType: "table", kpiIds: [] },
  { id: "t4", name: "Display KPI impact summary", agentType: "display", outputType: "display-kpi", estimatedTime: "< 1 min", chartType: "table", kpiIds: ["revenue", "conversion"] },
  { id: "t5", name: "Generate recommendations", agentType: "planning", outputType: "action", estimatedTime: "~2 min", chartType: "table", kpiIds: [] },
  { id: "t6", name: "Send results for review", agentType: "action", outputType: "decision-card", estimatedTime: "< 1 min", chartType: "table", kpiIds: [] },
];

// ── Draft factory ─────────────────────────────────────────────────────────────
export function createEmptyDraft() {
  return {
    version: USE_CASE_DRAFT_VERSION,
    /** UI flags: e.g. hide "draft in progress" on /use-cases when editing a launched row from catalog. */
    builderSession: { hideTemplatesDraftBanner: false, reopenedCatalogId: null, fromSeedTemplateId: null },
    step1: { title: "", description: "", category: "", tags: "", autonomyLevel: "copilot" },
    step2: {
      primaryQuestion: "",
      supportingQuestions: [
        "What are the top 5 underperforming SKUs this quarter?",
        "Which stores have the highest shrinkage rate?",
        "How does weekend foot traffic correlate with staffing levels?",
      ],
    },
    step3: {
      suggestedSources: [],
      confirmedSources: [],
      suggestedKnowledge: [],
      confirmedKnowledge: [],
      addMoreSources: structuredClone(ADDITIONAL_SOURCES),
      addMoreKnowledge: structuredClone(ADDITIONAL_KNOWLEDGE),
      /** `null` = all suggested gallery datasets for the category are in scope (default). */
      enterpriseDatasetIds: null,
    },
    // Do NOT pre-populate with generic default plan items. Step 4 will
    // compute contextual items via resolveContextualPlanItems using the
    // use case title + category + confirmed sources, so every use case
    // shows plan items relevant to itself instead of an identical
    // generic list.
    step4: { planItems: null },
    // Step 5 tasks derive from Step 4 plan items via deriveTasksFromPlanItems.
    // Start null so Step 5 recomputes from whatever contextual plan items
    // are in play instead of showing the generic DEFAULT_TASKS list.
    step5: { tasks: null },
    step6: { viewMode: "flow" },
    step7: { acknowledged: false },
    step8: { schedule: "once", guardrailAmount: "5000" },
    step9: { executionDone: false },
    builderKind: null,
    analytics: { linkedKpiKeys: [] },
  };
}

// Titles that identify the OLD generic default plan items / tasks. When
// legacy persisted drafts carry these, we strip them so Step 4 / Step 5
// recompute contextual items derived from the use case title + category.
const LEGACY_GENERIC_PLAN_TITLES = new Set([
  "Fetch and validate data from connected sources",
  "Apply analysis and pattern recognition",
  "Cross-reference with knowledge bases",
  "Generate recommendations and display outputs",
  "Deliver results and trigger actions",
]);
const LEGACY_GENERIC_TASK_NAMES = new Set([
  "Fetch current data from sources",
  "Apply demand and trend analysis",
]);

function stripLegacyGenericPlan(step4) {
  const items = step4?.planItems;
  if (!Array.isArray(items) || items.length === 0) return step4;
  if (items.some((it) => LEGACY_GENERIC_PLAN_TITLES.has(String(it?.title || "")))) {
    return { ...step4, planItems: null };
  }
  return step4;
}

function stripLegacyGenericTasks(step5) {
  const tasks = step5?.tasks;
  if (!Array.isArray(tasks) || tasks.length === 0) return step5;
  if (tasks.some((t) => LEGACY_GENERIC_TASK_NAMES.has(String(t?.name || "")))) {
    return { ...step5, tasks: null };
  }
  return step5;
}

/** Merge a stored or catalog snapshot with current schema defaults (same rules as load). */
export function mergeDraftFromSnapshot(snapshot) {
  if (!snapshot || typeof snapshot !== "object") return createEmptyDraft();
  const defaults = createEmptyDraft();
  const parsed = snapshot;
  const cleanStep4 = stripLegacyGenericPlan(parsed.step4);
  const cleanStep5 = stripLegacyGenericTasks(parsed.step5);
  return {
    ...defaults,
    ...parsed,
    builderSession: { ...defaults.builderSession, ...parsed.builderSession },
    step1: { ...defaults.step1, ...parsed.step1 },
    step2: { ...defaults.step2, ...parsed.step2 },
    step3: { ...defaults.step3, ...parsed.step3 },
    step4: { ...defaults.step4, ...cleanStep4 },
    step5: { ...defaults.step5, ...cleanStep5 },
    step6: { ...defaults.step6, ...parsed.step6 },
    step7: { ...defaults.step7, ...parsed.step7 },
    step8: { ...defaults.step8, ...parsed.step8 },
    step9: { ...defaults.step9, ...parsed.step9 },
    analytics: { ...defaults.analytics, ...(parsed.analytics || {}) },
    version: USE_CASE_DRAFT_VERSION,
  };
}

export function loadUseCaseDraft(storageKey) {
  const parsed = readLocalJson(storageKey, null);
  if (!parsed || typeof parsed !== "object" || parsed.version !== USE_CASE_DRAFT_VERSION) {
    const empty = createEmptyDraft();
    writeLocalJson(storageKey, empty);
    return empty;
  }
  return mergeDraftFromSnapshot(parsed);
}

export function saveUseCaseDraft(draft, storageKey) {
  writeLocalJson(storageKey, { ...draft, version: USE_CASE_DRAFT_VERSION });
}
