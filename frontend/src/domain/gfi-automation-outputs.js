/**
 * GFI automation outputs for G1 and G2.
 * Shape mirrors middleby-automation-outputs.js. Uses shared PDF/CSV helpers.
 */

import {
  buildStandardPdfBrief,
  csvBlob,
  STANDARD_FOOTER,
  WEEK_LABEL,
} from "./automation-output-helpers";

// ==========================================================================
// G1 - Inter-DC perishable transfer
// ==========================================================================

const G1_IMBALANCE_ROWS = [
  { sourceDc: "Denver", targetDc: "NJ", sku: "SKU-BR-1201", product: "Brie de Meaux 240 lbs", overstockDays: 5, targetShortDays: 3, laneCost: "$620", lane: "Denver -> NJ" },
  { sourceDc: "Denver", targetDc: "Chicago", sku: "SKU-PT-8829", product: "Prosciutto di Parma", overstockDays: 6, targetShortDays: 2, laneCost: "$390", lane: "Denver -> Chicago" },
  { sourceDc: "Seattle", targetDc: "Atlanta", sku: "SKU-CH-4437", product: "Aged Cheddar 40 lbs", overstockDays: 8, targetShortDays: 4, laneCost: "$710", lane: "Seattle -> Atlanta" },
  { sourceDc: "Boston", targetDc: "Miami", sku: "SKU-SM-2201", product: "Smoked Salmon 30 lbs", overstockDays: 4, targetShortDays: 2, laneCost: "$480", lane: "Boston -> Miami" },
];

const G1_TASK_PREVIEW = [
  { taskName: "Pick and pack 240 lbs Brie at Denver DC", assignee: "Denver pick team", dueDate: "Today, 4 PM", priority: "High", linkedDeal: "TR-DC-4102" },
  { taskName: "Dispatch multi-temp truck Denver -> NJ", assignee: "Fleet dispatcher", dueDate: "Today, 6 PM", priority: "High", linkedDeal: "TR-DC-4102" },
  { taskName: "Receive and put-away at NJ DC", assignee: "NJ receiving lead", dueDate: "Tomorrow, 8 AM", priority: "High", linkedDeal: "TR-DC-4102" },
];

const G1_SLACK = {
  channel: "#dc-ops-nj",
  author: "Enterprise GPT",
  timestamp: "Today 3:45 PM",
  body: [
    "Inter-DC perishable transfer prepared for approval.",
    "",
    "Denver overstocked on Brie de Meaux (5 days to code) while NJ will stock out in 3 days.",
    "",
    "• Transfer: 240 lbs, Denver -> NJ",
    "• GFI multi-temp fleet, dispatch 6 PM tonight, arrival 8 AM",
    "• Cost: $620 · Perishable loss avoided: ~$4,320",
    "",
    "Awaiting Logistics Manager approval before dispatch.",
  ].join("\n"),
};

const G1_EMAIL = {
  from: "Enterprise GPT <no-reply@enterprisegpt.gfi.com>",
  to: "logistics-manager@gfi.com",
  cc: "dc-denver@gfi.com, dc-nj@gfi.com",
  subject: "Approval requested: Denver -> NJ Brie transfer",
  sentAt: "Today 3:46 PM",
  body: [
    "Logistics Manager,",
    "",
    "Denver DC has 260 lbs of Brie de Meaux with 5 days to code. NJ DC will exhaust stock in 3 days at current sell-through.",
    "",
    "Recommended action:",
    "  • Transfer 240 lbs from Denver -> NJ via GFI multi-temp fleet",
    "  • Dispatch 6 PM tonight, arrival 8 AM tomorrow",
    "  • Cost: $620  ·  Perishable loss avoided: $4,320",
    "",
    "Approve or reject in the Automate panel. WMS transfer order is held until you confirm.",
    "",
    "Enterprise GPT",
  ].join("\n"),
  attachment: "inter-dc-transfer-approval-brief.pdf",
};

const G1_CSV_HEADERS = [
  { key: "sourceDc", label: "Source DC" },
  { key: "targetDc", label: "Target DC" },
  { key: "sku", label: "SKU" },
  { key: "product", label: "Product" },
  { key: "overstockDays", label: "Source days to expiry" },
  { key: "targetShortDays", label: "Target days to stockout" },
  { key: "laneCost", label: "Lane cost" },
];

function buildG1BriefPdf() {
  return buildStandardPdfBrief({
    title: "Inter-DC transfer approval brief",
    subtitle: `${WEEK_LABEL}  ·  Prepared for Logistics Manager`,
    headline:
      "Denver DC has 260 lbs of Brie de Meaux with 5 days to code expiry while NJ DC will exhaust stock in 3 days. Recommended transfer: 240 lbs on GFI's own multi-temp fleet, dispatch tonight, arrival 8 AM. Perishable loss avoided: $4,320. Transfer cost: $620.",
    sections: [
      { type: "kpis", heading: "Snapshot", items: [
        { label: "Days to expiry (Denver)", value: "5", sub: "260 lbs on hand" },
        { label: "Days to stockout (NJ)", value: "3", sub: "At current sell-through" },
        { label: "Loss avoided", value: "$4,320", sub: "Net of transfer cost" },
      ]},
      { type: "table", heading: "Rebalance opportunities this week",
        head: ["Source DC", "Target DC", "SKU", "Product", "Source d-to-expiry", "Target d-to-stock", "Lane cost"],
        body: G1_IMBALANCE_ROWS.map((r) => [r.sourceDc, r.targetDc, r.sku, r.product, String(r.overstockDays), String(r.targetShortDays), r.laneCost]) },
    ],
    footer: STANDARD_FOOTER,
  });
}

// ==========================================================================
// G2 - Weekly supplier risk digest
// ==========================================================================

const G2_RISK_ROWS = [
  { supplier: "Formaggio Alpino SRL", region: "Italy", category: "Aged cheese", signal: "Regional strike (Lombardia haulage)", exposure: "$482,000", severity: "High" },
  { supplier: "Fjord Salmon AS", region: "Norway", category: "Smoked salmon", signal: "Winter storm impacts Bergen port", exposure: "$318,000", severity: "High" },
  { supplier: "Rioja Iberia Foods", region: "Spain", category: "Cured meats", signal: "New EU tariff on select cured meats", exposure: "$602,000", severity: "Medium" },
  { supplier: "Anatolian Olive Cooperative", region: "Turkey", category: "Olives & oil", signal: "Small-producer FSMA 204 compliance risk", exposure: "$186,000", severity: "Medium" },
  { supplier: "Kyoto Umami Ltd", region: "Japan", category: "Specialty sauces", signal: "Container capacity tightness LA port", exposure: "$94,000", severity: "Low" },
  { supplier: "Pyrenees Pate Maison", region: "France", category: "Charcuterie", signal: "Seasonal supply (annual game season)", exposure: "$72,000", severity: "Low" },
  { supplier: "Aegean Yogurt House", region: "Greece", category: "Dairy specialties", signal: "Marginal quality complaints (2 batches)", exposure: "$68,000", severity: "Low" },
];

const G2_SLACK = {
  channel: "#procurement",
  author: "Enterprise GPT",
  timestamp: "Mon 7:02 AM",
  body: [
    "Weekly supplier risk digest is ready.",
    "",
    "4 elevated-risk signals across 2,043 monitored suppliers this week.",
    "",
    "• 2 High severity: Formaggio Alpino (haulage strike, $482K exposure), Fjord Salmon (Bergen storm, $318K)",
    "• 2 Medium severity: Rioja Iberia (new EU tariff, $602K), Anatolian Olive (FSMA compliance risk)",
    "",
    "Full digest attached to procurement leadership email.",
  ].join("\n"),
};

const G2_EMAIL = {
  from: "Enterprise GPT <no-reply@enterprisegpt.gfi.com>",
  to: "procurement-leadership@gfi.com",
  cc: "category-buyers@gfi.com",
  subject: `Weekly supplier risk digest · ${WEEK_LABEL}`,
  sentAt: "Mon 7:00 AM",
  body: [
    "Procurement leadership,",
    "",
    "This week's supplier risk digest is attached. 2,043 suppliers monitored; 4 elevated-risk signals surfaced.",
    "",
    "Highlights:",
    "  • Two High severity events with combined $800K exposure",
    "  • New EU tariff signal on cured meats (Rioja Iberia) - watch through the quarter",
    "  • FSMA 204 compliance risk flag on one small producer",
    "",
    "Full at-risk supplier list plus category context are in the attached PDF and CSV.",
    "",
    "Enterprise GPT",
  ].join("\n"),
  attachment: "weekly-supplier-risk-digest.pdf",
};

const G2_CSV_HEADERS = [
  { key: "supplier", label: "Supplier" },
  { key: "region", label: "Region" },
  { key: "category", label: "Category" },
  { key: "signal", label: "Risk signal" },
  { key: "exposure", label: "Exposure value" },
  { key: "severity", label: "Severity" },
];

function buildG2DigestPdf() {
  return buildStandardPdfBrief({
    title: "Weekly supplier risk digest",
    subtitle: `${WEEK_LABEL}  ·  Prepared for procurement leadership`,
    headline:
      "2,043 suppliers monitored this week. 4 elevated-risk signals surfaced across Europe and Asia. Combined at-risk exposure is $1.48M. Two High-severity events warrant immediate procurement action.",
    sections: [
      { type: "kpis", heading: "This week", items: [
        { label: "Monitored", value: "2,043", sub: "Full supplier base" },
        { label: "Elevated risk", value: "4", sub: "Above threshold" },
        { label: "Exposure", value: "$1.48M", sub: "At-risk inventory" },
      ]},
      { type: "table", heading: "At-risk suppliers",
        head: ["Supplier", "Region", "Category", "Risk signal", "Exposure", "Severity"],
        body: G2_RISK_ROWS.map((r) => [r.supplier, r.region, r.category, r.signal, r.exposure, r.severity]) },
      { type: "bullets", heading: "Recommended actions this week",
        items: [
          "Formaggio Alpino: pre-approve alternate haulage from Piedmont partners to protect the aged-cheese line.",
          "Fjord Salmon: engage back-up North Sea suppliers on standby pricing until Bergen operations resume.",
          "Rioja Iberia: model 2-quarter tariff impact and evaluate hedge via domestic cured-meat co-packers.",
        ]},
    ],
    footer: STANDARD_FOOTER,
  });
}

// ==========================================================================
// Registry
// ==========================================================================

export const GFI_AUTOMATION_OUTPUTS = {
  G1: {
    preparingMessage:
      "Confirming inter-DC imbalance, verifying perishability windows, and drafting the transfer plan for Logistics Manager approval...",
    reviewSteps: [
      { title: "Confirmed Denver DC overstock", description: "260 lbs of Brie de Meaux with 5 days to code expiry." },
      { title: "Verified NJ DC 3-day depletion", description: "Sell-through will exhaust NJ stock in 3 days." },
      { title: "Verified GFI multi-temp fleet availability", description: "Empty return leg on the Denver-NJ lane tonight; capacity to spare." },
      { title: "Drafted the transfer plan", description: "240 lbs, 6 PM dispatch, 8 AM arrival, temperature setpoint locked." },
      { title: "Prepared WMS transfer order (held for approval)", description: "Order TR-DC-4102 will be released on Logistics Manager approval." },
    ],
    preflightChecks: [
      { id: "coldchain", title: "Cold-chain temperature validated", status: "pass", detail: "Multi-temp truck compartment setpoint of 3C matches Brie storage spec. Cold-chain audit trail preserved through the transfer." },
      { id: "code-window", title: "Code-expiry window sufficient", status: "pass", detail: "240 lbs at NJ will consume at ~80 lbs/day; the code expiry gives 5 days of runway on the Denver stock, comfortably above the 3-day depletion window." },
      { id: "fleet", title: "Multi-temp fleet capacity confirmed", status: "pass", detail: "The Denver->NJ lane has an empty return truck tonight with more than enough temperature-controlled capacity for 240 lbs." },
      { id: "fsma", title: "Food traceability check", status: "warn", detail: "This is a tracked food item under FDA safety rules. The AI records the transfer data automatically, but the receiving DC still needs to scan lot codes on put-away." },
    ],
    channels: [
      { kind: "slack", title: "Slack · #dc-ops-nj", message: G1_SLACK },
      { kind: "email", title: "Email · Logistics Manager", message: G1_EMAIL },
      { kind: "hubspot-tasks", title: "WMS · tasks queued", rows: G1_TASK_PREVIEW },
    ],
    reports: [
      { id: "G1-brief-pdf", title: "Inter-DC transfer approval brief", description: "One-page approval brief with snapshot, recommendation, and rebalance context.", format: "pdf", filename: "inter-dc-transfer-approval-brief.pdf", build: () => buildG1BriefPdf() },
      { id: "G1-imbalance-csv", title: "Rebalance opportunities list", description: "All open cross-DC rebalance opportunities this week with lane cost.", format: "csv", filename: "inter-dc-rebalance-opportunities.csv", build: () => csvBlob(G1_CSV_HEADERS, G1_IMBALANCE_ROWS) },
    ],
  },

  G2: {
    preparingMessage:
      "Scanning external risk feeds, matching signals to GFI's 2,000+ suppliers, and drafting the weekly digest...",
    reviewSteps: [
      { title: "Scanned external risk feeds", description: "Tariff changes, weather anomalies, port strikes, and small-producer news over the last 7 days." },
      { title: "Matched signals to supplier records", description: "Every signal linked to specific suppliers in the ERP; 7 elevated-risk suppliers surfaced." },
      { title: "Ranked by exposure and severity", description: "Combined at-risk exposure across the 7 suppliers is $1.82M." },
      { title: "Drafted the procurement digest", description: "One-page summary with 3 recommended actions plus a full at-risk supplier list." },
      { title: "Scheduled Monday 7am distribution", description: "PDF attached to the procurement leadership email; distribution held until send." },
    ],
    preflightChecks: [
      { id: "feeds", title: "All risk feeds refreshed within 12 hours", status: "pass", detail: "News, weather, and trade data feeds refreshed overnight. Signal recency looks good." },
      { id: "mapping", title: "Supplier-signal mapping validated", status: "pass", detail: "Every elevated-risk signal maps to a supplier with an active PO in the last 90 days. No orphaned signals." },
      { id: "distro", title: "Procurement leadership list current", status: "pass", detail: "Distribution matches the procurement org chart from Monday's HRIS sync." },
    ],
    channels: [
      { kind: "slack", title: "Slack · #procurement", message: G2_SLACK },
      { kind: "email", title: "Email · procurement leadership", message: G2_EMAIL },
    ],
    reports: [
      { id: "G2-digest-pdf", title: "Weekly supplier risk digest", description: "At-risk supplier table plus 3 recommended actions for procurement.", format: "pdf", filename: "weekly-supplier-risk-digest.pdf", build: () => buildG2DigestPdf() },
      { id: "G2-suppliers-csv", title: "At-risk supplier list", description: "Elevated-risk suppliers with region, category, signal, exposure, and severity.", format: "csv", filename: "at-risk-supplier-list.csv", build: () => csvBlob(G2_CSV_HEADERS, G2_RISK_ROWS) },
    ],
  },
};

export function getGfiAutomationOutputs(playbookId) {
  return GFI_AUTOMATION_OUTPUTS[playbookId] ?? null;
}
