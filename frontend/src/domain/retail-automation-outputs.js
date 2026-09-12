/**
 * Retail (default profile) automation outputs for R1 and R2.
 * Shape mirrors middleby-automation-outputs.js. Uses shared PDF/CSV helpers.
 */

import {
  buildStandardPdfBrief,
  csvBlob,
  STANDARD_FOOTER,
  WEEK_LABEL,
} from "./automation-output-helpers";

// ==========================================================================
// R1 - Cross-store stockout transfer
// ==========================================================================

const R1_STOCKOUT_RISKS = [
  { store: "Store 42", sku: "SKU-9821", product: "Sony WH-1000XM5", onHand: 3, forecast14d: 43, source: "Store 17 (+45)", risk: "6h" },
  { store: "Store 51", sku: "SKU-7234", product: "Samsung 65\" QLED", onHand: 5, forecast14d: 22, source: "Store 33 (+12)", risk: "18h" },
  { store: "Store 08", sku: "SKU-4419", product: "Bose QC45", onHand: 4, forecast14d: 18, source: "Store 12 (+9)", risk: "22h" },
  { store: "Store 22", sku: "SKU-1188", product: "Apple AirPods Pro", onHand: 8, forecast14d: 34, source: "Store 41 (+22)", risk: "1.5d" },
  { store: "Store 63", sku: "SKU-3311", product: "LG C4 OLED 55\"", onHand: 6, forecast14d: 21, source: "Store 14 (+7)", risk: "2d" },
  { store: "Store 30", sku: "SKU-6612", product: "Sonos Beam Gen 2", onHand: 4, forecast14d: 14, source: "Store 09 (+6)", risk: "2d" },
  { store: "Store 47", sku: "SKU-8817", product: "Anker Nano 40W", onHand: 12, forecast14d: 32, source: "Store 21 (+18)", risk: "3d" },
  { store: "Store 17", sku: "SKU-0299", product: "Roku Ultra 4K", onHand: 5, forecast14d: 12, source: "Store 44 (+8)", risk: "3d" },
];

const R1_TASK_PREVIEW = [
  { taskName: "Dispatch Store 17 to Store 42 (SKU-9821)", assignee: "Fleet dispatcher", dueDate: "Today, 3 PM", priority: "High", linkedDeal: "TR-24102" },
  { taskName: "Confirm receipt at Store 42", assignee: "Store 42 manager", dueDate: "Today, 8 PM", priority: "High", linkedDeal: "TR-24102" },
  { taskName: "Update floor plan for restocked SKU-9821", assignee: "Store 42 associate", dueDate: "Today, 9 PM", priority: "Normal", linkedDeal: "TR-24102" },
];

const R1_SLACK = {
  channel: "#store-ops-region-e",
  author: "Enterprise GPT",
  timestamp: "Today 2:14 PM",
  body: [
    "Cross-store transfer prepared for approval.",
    "",
    "Store 42 will stock out of SKU-9821 (Sony WH-1000XM5) in ~6h. Store 17 has 45 units of overstock (18 days on hand).",
    "",
    "• Transfer: 40 units, Store 17 -> Store 42",
    "• Standard fleet, est. arrival in 5h",
    "• Cost: $180 · Sales protected: ~$8,400",
    "",
    "Awaiting Regional Manager approval before dispatch.",
  ].join("\n"),
};

const R1_EMAIL = {
  from: "Enterprise GPT <no-reply@enterprisegpt.retail.co>",
  to: "regional-manager-e@retail.co",
  cc: "store-42-manager@retail.co, store-17-manager@retail.co",
  subject: "Approval requested: cross-store transfer SKU-9821",
  sentAt: "Today 2:15 PM",
  body: [
    "Regional Manager,",
    "",
    "Store 42 is projected to stock out of SKU-9821 within 6 hours. Store 17 has 45 units of overstock in the same region.",
    "",
    "Recommended action:",
    "  • Transfer 40 units from Store 17 to Store 42 via standard fleet",
    "  • Cost: $180  ·  Est. sales protected: $8,400  ·  Est. arrival: 5 hours",
    "",
    "Approve or reject in the Automate panel. The transfer PO is held until you confirm.",
    "",
    "Enterprise GPT",
  ].join("\n"),
  attachment: "cross-store-transfer-approval-brief.pdf",
};

const R1_STOCKOUT_CSV_HEADERS = [
  { key: "store", label: "Store" },
  { key: "sku", label: "SKU" },
  { key: "product", label: "Product" },
  { key: "onHand", label: "On hand" },
  { key: "forecast14d", label: "14d forecast" },
  { key: "source", label: "Nearest overstock" },
  { key: "risk", label: "Time to stockout" },
];

function buildR1BriefPdf() {
  return buildStandardPdfBrief({
    title: "Cross-store transfer approval brief",
    subtitle: `${WEEK_LABEL}  ·  Prepared for Regional Manager`,
    headline:
      "Store 42 projected to stock out of SKU-9821 (Sony WH-1000XM5) in ~6 hours. Store 17 in the same region has 45 units of overstock. Recommended transfer: 40 units, standard fleet, ~5 hour arrival, cost $180. Est. sales protected: $8,400.",
    sections: [
      { type: "kpis", heading: "Snapshot", items: [
        { label: "Time to stockout", value: "~6h", sub: "Store 42" },
        { label: "Transfer cost", value: "$180", sub: "Standard fleet" },
        { label: "Sales protected", value: "$8,400", sub: "7-day est." },
      ]},
      { type: "table", heading: "Regional stockout risks this week",
        head: ["Store", "SKU", "Product", "On hand", "14d forecast", "Nearest overstock", "Risk"],
        body: R1_STOCKOUT_RISKS.map((r) => [r.store, r.sku, r.product, String(r.onHand), String(r.forecast14d), r.source, r.risk]) },
    ],
    footer: STANDARD_FOOTER,
  });
}

// ==========================================================================
// R2 - Weekly gross margin briefing
// ==========================================================================

const R2_MARGIN_MOVERS = [
  { category: "Consumer electronics", grossMargin: "22.4%", changeWoW: "+1.2pt", topDriver: "Promo cadence tightened on TVs" },
  { category: "Small appliances", grossMargin: "31.8%", changeWoW: "+0.4pt", topDriver: "Mix shift toward premium blenders" },
  { category: "Home audio", grossMargin: "28.1%", changeWoW: "+0.2pt", topDriver: "Steady sell-through" },
  { category: "Grocery & pantry", grossMargin: "24.6%", changeWoW: "-0.1pt", topDriver: "Slight competitor pressure" },
  { category: "Apparel & accessories", grossMargin: "39.7%", changeWoW: "-0.3pt", topDriver: "Markdown depth increased on seasonal" },
  { category: "Health & beauty", grossMargin: "35.2%", changeWoW: "-0.4pt", topDriver: "New-brand launch dilution" },
  { category: "Kitchenware", grossMargin: "30.9%", changeWoW: "-0.6pt", topDriver: "Cookware clearance in flight" },
  { category: "Outdoor & sports", grossMargin: "26.3%", changeWoW: "-1.4pt", topDriver: "Deep markdown on seasonal exit" },
];

const R2_SLACK = {
  channel: "#margin-watch",
  author: "Enterprise GPT",
  timestamp: "Fri 7:02 AM",
  body: [
    "Weekly gross margin briefing is ready.",
    "",
    "8 categories tracked. 3 up, 5 down.",
    "",
    "• Consumer electronics +1.2pt (promo tightening) - largest positive mover",
    "• Outdoor & sports -1.4pt (seasonal exit markdown) - largest negative",
    "• Small appliances +0.4pt - premium mix shift continues",
    "",
    "Full briefing attached to the executive email.",
  ].join("\n"),
};

const R2_EMAIL = {
  from: "Enterprise GPT <no-reply@enterprisegpt.retail.co>",
  to: "leadership@retail.co",
  cc: "",
  subject: `Weekly gross margin briefing · ${WEEK_LABEL}`,
  sentAt: "Fri 7:00 AM",
  body: [
    "Leadership team,",
    "",
    "Weekly gross margin briefing attached. 8 categories tracked.",
    "",
    "Highlights:",
    "  • Consumer electronics +1.2pt on tighter promo cadence",
    "  • Outdoor & sports -1.4pt on seasonal exit markdown",
    "  • Aggregate blended margin -0.1pt WoW",
    "",
    "Full detail plus CSV cut of category performance are attached.",
    "",
    "Enterprise GPT",
  ].join("\n"),
  attachment: "weekly-gross-margin-briefing.pdf",
};

const R2_CSV_HEADERS = [
  { key: "category", label: "Category" },
  { key: "grossMargin", label: "Gross margin" },
  { key: "changeWoW", label: "Change WoW" },
  { key: "topDriver", label: "Top driver" },
];

function buildR2BriefingPdf() {
  return buildStandardPdfBrief({
    title: "Weekly gross margin briefing",
    subtitle: `${WEEK_LABEL}  ·  Prepared for leadership`,
    headline:
      "8 L1 categories tracked this week. 3 up, 5 down. Aggregate blended margin down 0.1pt WoW. Largest positive mover is consumer electronics (+1.2pt on tighter promo cadence). Largest negative is outdoor & sports (-1.4pt on seasonal exit markdown).",
    sections: [
      { type: "table", heading: "Category margin movers",
        head: ["Category", "Gross margin", "Change WoW", "Top driver"],
        body: R2_MARGIN_MOVERS.map((r) => [r.category, r.grossMargin, r.changeWoW, r.topDriver]) },
      { type: "bullets", heading: "Worth a leadership conversation",
        items: [
          "Consumer electronics gain suggests we should hold this promo cadence through the next 4 weeks.",
          "Outdoor & sports margin loss is expected during seasonal exit; watch clearance completion vs plan.",
          "Health & beauty dilution from the new-brand launch may need co-op negotiation to protect margin.",
        ]},
    ],
    footer: STANDARD_FOOTER,
  });
}

// ==========================================================================
// Registry
// ==========================================================================

export const RETAIL_AUTOMATION_OUTPUTS = {
  R1: {
    preparingMessage:
      "Confirming stockout risk, finding nearest overstock, and drafting the transfer PO for Regional Manager approval...",
    reviewSteps: [
      { title: "Confirmed stockout risk at Store 42", description: "14-day sell-through vs on-hand puts SKU-9821 at ~6h to stockout." },
      { title: "Found nearest overstock at Store 17", description: "45 units on hand, 18 days on hand - safe to reduce." },
      { title: "Sized the transfer at 40 units", description: "Protects both stores through the 7-day forecast." },
      { title: "Drafted carrier dispatch for standard fleet", description: "Route Store 17 to Store 42, est. arrival 5 hours." },
      { title: "Prepared transfer PO in ERP (held for approval)", description: "PO TR-24102 will be released on Regional Manager approval." },
    ],
    preflightChecks: [
      { id: "capacity", title: "Overstock validated at Store 17", status: "pass", detail: "45 units on hand, 3 recent same-day dispatches confirm the SKU is moving. Safe to reduce inventory here without risking a secondary stockout." },
      { id: "fleet", title: "Standard fleet capacity confirmed", status: "pass", detail: "Route Store 17 to Store 42 has an outbound truck at 3 PM with 60 lbs of unused capacity - well within scope for 40 units." },
      { id: "approval", title: "Transfer within Regional Manager authority", status: "pass", detail: "Total value under the $5K approval limit for a same-region SKU transfer." },
      { id: "recency", title: "Store 42 forecast is 8 days old", status: "warn", detail: "The demand forecast that estimates 7-day depletion was refreshed 8 days ago (48h beyond the typical refresh cadence). The risk is directionally correct but confirm with store on the day of dispatch." },
    ],
    channels: [
      { kind: "slack", title: "Slack · #store-ops-region-e", message: R1_SLACK },
      { kind: "email", title: "Email · Regional Manager", message: R1_EMAIL },
      { kind: "hubspot-tasks", title: "ERP · tasks queued", rows: R1_TASK_PREVIEW },
    ],
    reports: [
      { id: "R1-brief-pdf", title: "Cross-store transfer approval brief", description: "One-page approval brief with snapshot, recommendation, and regional risk context.", format: "pdf", filename: "cross-store-transfer-approval-brief.pdf", build: () => buildR1BriefPdf() },
      { id: "R1-risks-csv", title: "Regional stockout risk list", description: "All at-risk SKUs across the region with nearest overstock candidates.", format: "csv", filename: "regional-stockout-risks.csv", build: () => csvBlob(R1_STOCKOUT_CSV_HEADERS, R1_STOCKOUT_RISKS) },
    ],
  },

  R2: {
    preparingMessage:
      "Pulling weekly category margin data from the warehouse and drafting the executive briefing...",
    reviewSteps: [
      { title: "Pulled 8 L1 category margin cuts", description: "8 weeks of history to compute WoW change and short trends." },
      { title: "Ranked categories by margin change", description: "3 up, 5 down. Consumer electronics leads positive; outdoor & sports leads negative." },
      { title: "Drafted the executive briefing", description: "One-page summary with headline, movers table, and 3 leadership talking points." },
      { title: "Drafted the Slack summary for #margin-watch", description: "Short overview with top positive and negative movers." },
      { title: "Scheduled the Friday 7am distribution", description: "PDF attached; distribution held until send." },
    ],
    preflightChecks: [
      { id: "freshness", title: "All 8 categories refreshed within 24 hours", status: "pass", detail: "Data warehouse category cuts refreshed at 2 AM today. All 8 L1 categories have complete week-over-week data." },
      { id: "recipients", title: "Executive distribution list current", status: "pass", detail: "Distribution matches the exec org chart from Monday's HRIS sync." },
      { id: "content", title: "No content policy violations detected", status: "pass", detail: "No customer PII, restricted vendor terms, or unreleased-strategy mentions in the brief." },
    ],
    channels: [
      { kind: "slack", title: "Slack · #margin-watch", message: R2_SLACK },
      { kind: "email", title: "Email · leadership distribution", message: R2_EMAIL },
    ],
    reports: [
      { id: "R2-briefing-pdf", title: "Weekly gross margin briefing", description: "Category movers table plus 3 leadership talking points.", format: "pdf", filename: "weekly-gross-margin-briefing.pdf", build: () => buildR2BriefingPdf() },
      { id: "R2-margin-csv", title: "Category margin detail (8 rows)", description: "Full category margin table with WoW change and top driver.", format: "csv", filename: "category-margin-detail.csv", build: () => csvBlob(R2_CSV_HEADERS, R2_MARGIN_MOVERS) },
    ],
  },
};

export function getRetailAutomationOutputs(playbookId) {
  return RETAIL_AUTOMATION_OUTPUTS[playbookId] ?? null;
}
