/**
 * Auto-generates a playbook + outputs shape for any catalog entry that
 * doesn't have an explicit playbook seed. Uses per-business-area templates
 * to produce sensible defaults for:
 *
 *   preparingMessage / reviewSteps / preflightChecks
 *   channels (Slack + Email)
 *   reports (PDF brief + CSV export)
 *   kpiTargets, impact tiles, run steps, journey, automatedActions
 *
 * The generated playbook has id "AUTO-<catalogId>" so it does not collide
 * with the explicit A- / R- / G- playbooks and is transparently generated
 * on lookup by automation-registry.js.
 */

import {
  buildStandardPdfBrief,
  csvBlob,
  STANDARD_FOOTER,
  WEEK_LABEL,
} from "./automation-output-helpers";
import { resolveContextualPlanItems } from "../storage/use-case-draft";

/** Same category label -> Builder category slug mapping used by the Builder
 *  wizard, kept here so the generator picks the same PLAN_ITEMS_BY_CATEGORY
 *  bucket for a given catalog entry that the Builder's Step 3 would. */
const BUSINESS_AREA_LABEL_TO_CATEGORY = {
  Inventory: "inventory",
  Orders: "orders",
  "Store Ops": "store-ops",
  "E-Com": "ecom",
  "E-Commerce": "ecom",
  POS: "pos",
  Support: "support",
  Pricing: "pricing",
  Merchandise: "merchandise",
  Finance: "finance",
  Workforce: "workforce",
  // Media profile labels
  Content: "merchandise",
  "OTT Platform": "e-com",
  Broadcast: "store-ops",
  Subscribers: "support",
  Monetization: "finance",
};

// ==========================================================================
// Per-business-area configuration
// ==========================================================================

const AREA_CONFIG = {
  inventory: {
    owner: "Inventory ops",
    monitorTitle: "Inventory Health",
    slackChannel: "#inventory-ops",
    emailTo: "inventory-lead@company.com",
    reviewNoun: "inventory positions",
    signalNoun: "at-risk SKUs",
    kpiHints: ["Stockout", "Order Fill Rate", "Days on hand", "Inventory Turns", "Sell-through"],
  },
  orders: {
    owner: "Fulfilment ops",
    monitorTitle: "Order Health",
    slackChannel: "#order-ops",
    emailTo: "fulfilment-lead@company.com",
    reviewNoun: "order flows and SLA compliance",
    signalNoun: "at-risk orders",
    kpiHints: ["SLA", "Fill rate", "Split shipment", "Fulfillment cost", "Order accuracy"],
  },
  merchandise: {
    owner: "Merchandising",
    monitorTitle: "Merchandise Performance",
    slackChannel: "#merchandising",
    emailTo: "merchandising-lead@company.com",
    reviewNoun: "assortment and product performance",
    signalNoun: "under-performing SKUs",
    kpiHints: ["Sell-through", "Gross margin", "Assortment", "New product", "Planogram"],
  },
  "store ops": {
    owner: "Store operations",
    monitorTitle: "Store Ops",
    slackChannel: "#store-ops",
    emailTo: "store-ops-lead@company.com",
    reviewNoun: "store activity and compliance",
    signalNoun: "operational gaps",
    kpiHints: ["Task completion", "Compliance", "Labor", "Foot traffic"],
  },
  "e-com": {
    owner: "E-commerce team",
    monitorTitle: "E-Com Funnel",
    slackChannel: "#ecom-ops",
    emailTo: "ecom-lead@company.com",
    reviewNoun: "site behaviour and funnel performance",
    signalNoun: "drop-off patterns",
    kpiHints: ["Conversion", "Cart abandonment", "Bounce", "Session"],
  },
  pos: {
    owner: "POS operations",
    monitorTitle: "POS Health",
    slackChannel: "#pos-ops",
    emailTo: "pos-lead@company.com",
    reviewNoun: "POS activity and hardware health",
    signalNoun: "failures or slowdowns",
    kpiHints: ["Uptime", "Transaction time", "Error rate"],
  },
  support: {
    owner: "Support ops",
    monitorTitle: "Support Health",
    slackChannel: "#support-ops",
    emailTo: "support-lead@company.com",
    reviewNoun: "support tickets and escalation patterns",
    signalNoun: "escalation drivers",
    kpiHints: ["Wait time", "First contact resolution", "CSAT", "Escalation rate"],
  },
  pricing: {
    owner: "Pricing team",
    monitorTitle: "Pricing Health",
    slackChannel: "#pricing-ops",
    emailTo: "pricing-lead@company.com",
    reviewNoun: "price positioning and elasticity signals",
    signalNoun: "price actions",
    kpiHints: ["Gross margin", "Sell-through", "Price index", "Markdown"],
  },
  finance: {
    owner: "Finance",
    monitorTitle: "Finance",
    slackChannel: "#finance-ops",
    emailTo: "finance-lead@company.com",
    reviewNoun: "financial signals and margin composition",
    signalNoun: "margin movers",
    kpiHints: ["Gross margin", "OpEx", "Contribution margin", "Revenue"],
  },
  workforce: {
    owner: "Workforce",
    monitorTitle: "Workforce Health",
    slackChannel: "#workforce-ops",
    emailTo: "workforce-lead@company.com",
    reviewNoun: "labor scheduling and utilisation",
    signalNoun: "overtime or coverage gaps",
    kpiHints: ["Labor cost", "Overtime", "Schedule adherence", "Utilisation"],
  },

  // ── Media & OTT area configs (matches MEDIA_BUSINESS_AREAS labels). ──────
  content: {
    owner: "Content Strategy leadership",
    monitorTitle: "Content Intelligence",
    slackChannel: "#content-strategy",
    emailTo: "content-strategy@media.co",
    reviewNoun: "audience-conversation trends and content-slate performance",
    signalNoun: "sustained trends and under-represented themes",
    kpiHints: [
      "Trend Momentum Score",
      "Watch Hours",
      "Content ROI",
      "Regional-Language Watch Share",
      "Genre Mix Share",
      "Cost per Watch Hour",
    ],
  },
  "ott platform": {
    owner: "Product & Discovery",
    monitorTitle: "OTT Platform",
    slackChannel: "#ott-platform",
    emailTo: "product-discovery@media.co",
    reviewNoun: "OTT engagement, discovery, and AEO / GEO visibility",
    signalNoun: "engagement gaps or discovery leaks",
    kpiHints: [
      "Monthly Active Viewers",
      "Content Completion Rate",
      "Avg Watch Time per Session",
      "AI Answer Presence Score",
      "Content Discoverability Index",
    ],
  },
  broadcast: {
    owner: "Broadcast Ops",
    monitorTitle: "Broadcast",
    slackChannel: "#broadcast-ops",
    emailTo: "broadcast-ops@media.co",
    reviewNoun: "linear playout and CDN streaming performance",
    signalNoun: "playout or delivery regressions",
    kpiHints: ["Playout Health", "CDN Buffer Ratio", "Start Time", "BARC Ratings"],
  },
  subscribers: {
    owner: "Subscriber Growth & Retention",
    monitorTitle: "Subscribers",
    slackChannel: "#subscribers",
    emailTo: "subscribers-growth@media.co",
    reviewNoun: "signup funnel, plans, and churn signals",
    signalNoun: "churn-risk cohorts and price-competition impact",
    kpiHints: [
      "Subscriber Churn Rate",
      "ARPU",
      "D7 / D30 Retention",
      "Signup Funnel Conversion",
      "Payment Decline Rate",
    ],
  },
  monetization: {
    owner: "Ad-Ops & Monetization",
    monitorTitle: "Monetization",
    slackChannel: "#ad-ops",
    emailTo: "ad-ops@media.co",
    reviewNoun: "programmatic ad yield and content ROI attribution",
    signalNoun: "fill / eCPM regressions and title-ROI opportunities",
    kpiHints: [
      "Ad Fill Rate",
      "eCPM",
      "Ad Completion Rate",
      "Yield per Impression",
      "Content ROI",
      "Cost per Watch Hour",
    ],
  },
};

const DEFAULT_CONFIG = {
  owner: "Operations lead",
  monitorTitle: "Business Health",
  slackChannel: "#ops",
  emailTo: "ops-lead@company.com",
  reviewNoun: "business signals",
  signalNoun: "opportunities",
  kpiHints: [],
};

function configForBusinessArea(businessArea) {
  const key = String(businessArea || "").trim().toLowerCase();
  return AREA_CONFIG[key] || DEFAULT_CONFIG;
}

// ==========================================================================
// Playbook + outputs generation
// ==========================================================================

/**
 * Generate a playbook (with the same shape as MIDDLEBY_AUTOMATION_PLAYBOOKS
 * entries) for a catalog entry that does not have an explicit playbook seed.
 */
export function generatePlaybookForEntry(entry) {
  if (!entry?.id) return null;
  const cfg = configForBusinessArea(entry.businessArea);
  const title = entry.title || "Use case";
  const question = entry.sampleQuestion || (entry.items && entry.items[0]) || "";
  // Pull the user's plan items and tasks from the Builder draft snapshot if
  // present, so the playbook run steps / automated actions match Step 4 /
  // Step 5 of the wizard the user just walked.
  const draftPlanItems = Array.isArray(entry.draftSnapshot?.step4?.planItems)
    ? entry.draftSnapshot.step4.planItems
    : [];
  const draftTasks = Array.isArray(entry.draftSnapshot?.step5?.tasks)
    ? entry.draftSnapshot.step5.tasks
    : [];

  // When the user hasn't walked the Builder yet, still surface use-case-
  // specific automatedActions and runSteps by computing them from the
  // catalog entry itself (per-id override or entry-derived generator) via
  // the same resolver Builder Step 4 uses. This means the pre-activation
  // preview on the activation page reflects the specific use case rather
  // than the previous generic "Pull data / Analyze / Draft actions" list.
  const categorySlug = BUSINESS_AREA_LABEL_TO_CATEGORY[entry.businessArea] || "";
  const contextualPlanItems = resolveContextualPlanItems(
    entry.sampleQuestion || "",
    categorySlug,
    entry.draftSnapshot?.step3?.confirmedSources?.filter((s) => s.enabled) || [],
    entry.draftSnapshot?.step3?.confirmedKnowledge?.filter((k) => k.enabled) || [],
    title,
    entry
  );

  return {
    id: `AUTO-${entry.id}`,
    label: title,
    catalogIds: [entry.id],
    trigger:
      question ||
      `Enterprise GPT detected a signal on ${cfg.reviewNoun} that warrants review this cycle.`,
    analyzeTitle: title,
    monitorTitle: cfg.monitorTitle,
    owner: cfg.owner,
    decisionCardId: `${entry.id}-card`,
    decisionCard: {
      id: `${entry.id}-card`,
      title: `Approve action: ${title}`,
      reasoning: `Enterprise GPT analyzed ${cfg.reviewNoun} and identified ${cfg.signalNoun}. Recommended action is prepared and awaiting your approval.`,
      action: `Execute the ${title} workflow and notify ${cfg.owner}.`,
    },
    automationSummary: `Runs the ${title} workflow and prepares actions for ${cfg.owner} review.`,
    automatedActions:
      draftPlanItems.length > 0
        ? draftPlanItems.map((pi) => ({
            what: String(pi.title || "Prepared step"),
            where: pi.description ? "Configured sources" : "Enterprise GPT",
            detail: pi.description ? String(pi.description) : "",
          }))
        : contextualPlanItems.map((pi) => ({
            what: String(pi.title || "Prepared step"),
            where: pi.description ? "Configured sources" : "Enterprise GPT",
            detail: pi.description ? String(pi.description) : "",
          })),
    staysHuman: [
      `${cfg.owner} approves the action before it is taken.`,
      "Downstream teams execute operational follow-ups.",
    ],
    journey: [
      { phase: "analyze", title: "Detect signal", detail: `Signal detected on ${cfg.reviewNoun}.` },
      { phase: "monitor", title: `Confirm on ${cfg.monitorTitle}`, detail: "Cross-check against the business monitor." },
      { phase: "automate", title: "Draft actions", detail: "Recommended actions ready for review." },
      { phase: "automate", title: "Approval gate", detail: `${cfg.owner} reviews and approves.` },
      { phase: "automate", title: "Execute and log", detail: "Actions executed; audit trail preserved." },
    ],
    runSteps:
      draftTasks.length > 0
        ? draftTasks.map((t, i) => ({
            label: String(t.name || t.title || `Step ${i + 1}`),
            duration: String(t.estimatedTime || "~1s"),
          }))
        : (draftPlanItems.length > 0 ? draftPlanItems : contextualPlanItems).map((pi, i, arr) => ({
            label: String(pi.title || `Step ${i + 1}`),
            duration: i === 0 || i === arr.length - 1 ? "0.3s" : "0.6s",
          })),
    impact: [
      { label: `${cfg.signalNoun}`, value: "-", sub: "See recommended actions" },
      { label: "Est. time saved", value: "~2 hrs", sub: "vs manual review" },
      { label: "Coverage", value: "100%", sub: "Of tracked scope" },
    ],
    kpiTargets: cfg.kpiHints.map((hint) => ({
      name: hint,
      contributionType: "direct",
      targetDelta: { value: "Improvement", horizon: "quarter" },
      attribution: `Enterprise GPT surfaces ${cfg.signalNoun} and drives coordinated response.`,
    })),
  };
}

// ==========================================================================
// Outputs generation (Prepare -> Review -> Deliver artifacts)
// ==========================================================================

/**
 * Generate the outputs (preparingMessage, reviewSteps, preflightChecks,
 * channels, reports) for a catalog entry that does not have an explicit
 * outputs seed.
 *
 * If the user has walked the Builder and the entry has a `draftSnapshot`,
 * we prefer their configured plan items and tasks for reviewSteps so the
 * activation page stays in sync with Builder Step 4 (AI Plan) and Step 5
 * (Plan Breakdown). Falls back to category-generic content otherwise.
 */
export function generateOutputsForEntry(entry) {
  if (!entry?.id) return null;
  const cfg = configForBusinessArea(entry.businessArea);
  const title = entry.title || "Use case";
  const slugId = String(entry.id).toLowerCase().replace(/[^a-z0-9]+/g, "-");
  const draftPlanItems = Array.isArray(entry.draftSnapshot?.step4?.planItems)
    ? entry.draftSnapshot.step4.planItems
    : [];

  const slackMsg = {
    channel: cfg.slackChannel,
    author: "Enterprise GPT",
    timestamp: "Today",
    body: [
      `${title} - workflow ready for review.`,
      "",
      `Enterprise GPT reviewed ${cfg.reviewNoun} and prepared actions for ${cfg.owner}.`,
      "",
      "See the attached brief for detail and recommended next steps.",
    ].join("\n"),
  };

  const emailMsg = {
    from: "Enterprise GPT <no-reply@enterprisegpt.company.com>",
    to: cfg.emailTo,
    cc: "",
    subject: `${title} - action recommended`,
    sentAt: "Today",
    body: [
      `Hi ${cfg.owner},`,
      "",
      `Enterprise GPT prepared the ${title} workflow and drafted actions for your review.`,
      "",
      "Highlights:",
      `  - Data reviewed across ${cfg.reviewNoun}`,
      `  - ${cfg.signalNoun} surfaced for follow-up`,
      "  - Recommended actions attached (PDF)",
      "",
      "Enterprise GPT",
    ].join("\n"),
    attachment: `${slugId}-brief.pdf`,
  };

  const csvHeaders = [
    { key: "field", label: "Field" },
    { key: "value", label: "Value" },
  ];
  const csvRows = [
    { field: "Use case", value: title },
    { field: "Business area", value: entry.businessArea || "-" },
    { field: "Sample question", value: entry.sampleQuestion || (entry.items && entry.items[0]) || "-" },
    { field: "Prepared for", value: cfg.owner },
    { field: "Notification channel", value: cfg.slackChannel },
  ];

  const reviewStepsFromDraft = draftPlanItems.map((pi) => ({
    title: String(pi.title || "Prepared step"),
    description: pi.description ? String(pi.description) : undefined,
  }));
  // Same computation the Builder Step 4 uses, so the activation page
  // stays in sync with what the Builder would show for this use case.
  // Passing the full catalog entry lets resolveContextualPlanItems use the
  // per-catalog-id override or entry-derived generator instead of falling
  // to the generic category template (which used to render identically for
  // every use case in the same business area).
  const categorySlug = BUSINESS_AREA_LABEL_TO_CATEGORY[entry.businessArea] || "";
  const contextualItems = resolveContextualPlanItems(
    entry.sampleQuestion || "",
    categorySlug,
    entry.draftSnapshot?.step3?.confirmedSources?.filter((s) => s.enabled) || [],
    entry.draftSnapshot?.step3?.confirmedKnowledge?.filter((k) => k.enabled) || [],
    title,
    entry
  );

  return {
    preparingMessage: `Enterprise GPT is running the ${title} analysis and drafting actions for review...`,
    // Priority: user's actual draft plan items > shared contextual computation
    // (identical to Builder Step 4). Never a hand-rolled generic list.
    reviewSteps:
      reviewStepsFromDraft.length > 0
        ? reviewStepsFromDraft
        : contextualItems.map((pi) => ({
            title: String(pi.title || "Prepared step"),
            description: pi.description ? String(pi.description) : undefined,
          })),
    preflightChecks: [
      {
        id: "sources",
        title: "Data sources fresh",
        status: "pass",
        detail: "All configured sources for this use case were refreshed within the current cycle.",
      },
      {
        id: "authority",
        title: "Approval authority in scope",
        status: "pass",
        detail: `Actions are within ${cfg.owner} authority and do not require higher-level escalation.`,
      },
      {
        id: "distribution",
        title: "Distribution list current",
        status: "pass",
        detail: `Slack channel and email distribution list synced with the organisational directory.`,
      },
    ],
    channels: [
      { kind: "slack", title: `Slack · ${cfg.slackChannel}`, message: slackMsg },
      { kind: "email", title: `Email · ${cfg.emailTo}`, message: emailMsg },
    ],
    reports: [
      {
        id: `${slugId}-brief-pdf`,
        title: `${title} - action brief`,
        description: `One-page recommendation prepared for ${cfg.owner}.`,
        format: "pdf",
        filename: `${slugId}-brief.pdf`,
        build: () =>
          buildStandardPdfBrief({
            title,
            subtitle: `${WEEK_LABEL} · Prepared for ${cfg.owner}`,
            headline: `Enterprise GPT reviewed ${cfg.reviewNoun} and prepared a recommended action. See the summary and next steps below.`,
            sections: [
              {
                type: "text",
                heading: "Recommendation",
                body: `Execute the ${title} workflow and notify ${cfg.owner}. ${cfg.signalNoun} identified this cycle.`,
              },
              {
                type: "bullets",
                heading: "Next steps",
                items: [
                  `Approve or reject in the activation flow.`,
                  `On approval, ${cfg.slackChannel} and ${cfg.emailTo} are notified.`,
                  `Impact is tracked in the Impact Dashboard for this use case.`,
                ],
              },
            ],
            footer: STANDARD_FOOTER,
          }),
      },
      {
        id: `${slugId}-detail-csv`,
        title: `${title} - detail export`,
        description: "Structured export of the use case parameters and signal detail.",
        format: "csv",
        filename: `${slugId}-detail.csv`,
        build: () => csvBlob(csvHeaders, csvRows),
      },
    ],
  };
}
