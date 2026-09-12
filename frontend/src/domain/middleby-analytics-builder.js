import { createEmptyDraft } from "../storage/use-case-draft";
import { MIDDLEBY_PROFILE_ID, normalizeDemoProfileId } from "./demo-profiles";

export const ANALYTICS_BUILDER_STEPS = [
  {
    num: 1,
    label: "Basic details",
    sub: "Name and area",
    path: "/analytics/builder/step-1",
  },
  {
    num: 2,
    label: "Analytics question",
    sub: "Define intent",
    path: "/analytics/builder/step-2",
  },
  {
    num: 3,
    label: "Data sources",
    sub: "GA, HubSpot, LR",
    path: "/analytics/builder/step-3",
  },
  {
    num: 4,
    label: "KPIs and launch",
    sub: "Link metrics",
    path: "/analytics/builder/step-4",
  },
];

export const ANALYTICS_CATEGORY_OPTIONS = [
  { value: "", label: "Select analytics area" },
  { value: "rfq", label: "RFQ & Pipeline" },
  { value: "ux", label: "UX & Quality" },
  { value: "demand", label: "Demand & Catalog" },
  { value: "executive", label: "Executive & Portfolio" },
  { value: "dealer", label: "Dealer & Sales" },
  { value: "marketing", label: "Marketing & Channels" },
];

export const ANALYTICS_CATEGORY_TO_BUSINESS_AREA = {
  rfq: "Digital & RFQ",
  ux: "UX & Quality",
  demand: "Digital & RFQ",
  executive: "Executive",
  dealer: "Dealer & Sales",
  marketing: "Marketing",
};

export const ANALYTICS_CATEGORY_ACTION_TYPE = {
  rfq: "Analyze",
  ux: "Diagnose",
  demand: "Analyze",
  executive: "Summarize",
  dealer: "Prioritize",
  marketing: "Analyze",
};

export const ANALYTICS_QUESTION_CHIPS = {
  rfq: [
    "Why did RFQ conversion decrease this month?",
    "Which visitors are most likely to submit a quote in the next 7 days?",
    "Where are buyers dropping off in the quote funnel?",
    "Which quotes need dealer follow-up today?",
  ],
  ux: [
    "Which JavaScript errors affect the most buyer sessions?",
    "How did error sessions change after the last site release?",
    "Which browsers see the highest RFQ form friction?",
  ],
  demand: [
    "Which product categories show rising demand but low quotes?",
    "Which high-traffic SKUs have zero quote requests?",
    "Which brands have the largest browse vs quote gap?",
  ],
  executive: [
    "What changed this week across traffic, errors, and quotes?",
    "What were the top drivers of quote volume this quarter?",
    "Which categories contribute most to estimated pipeline?",
  ],
  dealer: [
    "Which dealer regions are losing the most RFQ opportunities?",
    "Which quotes are over SLA and need escalation?",
    "Which rep groups have the slowest follow-up?",
  ],
  marketing: [
    "Which channels drive deep product engagement vs bounce?",
    "Which campaigns bring buyers who request quotes?",
    "How does paid traffic compare to organic for quote intent?",
  ],
};

/** Middleby analytics data sources for builder step 3 (maps to Configure seeds). */
export const MIDDLEBY_ANALYTICS_DATA_SOURCES = [
  {
    id: "seed_middleby_ga4",
    name: "Google Analytics 4",
    description: "Brand traffic, sessions, and engagement aggregates.",
    iconKey: "Cloud",
  },
  {
    id: "seed_middleby_hubspot",
    name: "HubSpot CRM",
    description: "Quotes, dealers, stages. Named buyers keyed by email ID (McFadyen). No browse session IDs on deals in sample export.",
    iconKey: "Server",
  },
  {
    id: "seed_middleby_logrocket",
    name: "LogRocket",
    description: "Session replays and errors. SDK unique users only; no CRM customer sync. identify() at quote form for named sessions.",
    iconKey: "Server",
  },
  {
    id: "seed_middleby_shop_catalog",
    name: "Shop catalog (Akeneo path)",
    description: "SKU, brand, L1/L2/L3 from shop.middleby.com. Akeneo has no customer data (McFadyen).",
    iconKey: "Server",
  },
  {
    id: "seed_middleby_adobe_commerce",
    name: "Adobe Commerce",
    description: "Order and commerce metrics when export is connected.",
    iconKey: "Cloud",
  },
  {
    id: "seed_middleby_session_stitch",
    name: "Session stitch (go-live)",
    description: "Future join: ga_client_id + logrocket_session_id on HubSpot deal at quote submit; GA user_id when email known.",
    iconKey: "Cloud",
  },
];

export function createAnalyticsBuilderDraft() {
  const base = createEmptyDraft();
  return {
    ...base,
    builderKind: "analytics",
    step1: {
      title: "",
      description: "",
      category: "",
      tags: "Middleby, Analytics",
      autonomyLevel: "copilot",
    },
    step2: {
      primaryQuestion: "",
      supportingQuestions: [],
    },
    step3: {
      ...base.step3,
      confirmedSources: MIDDLEBY_ANALYTICS_DATA_SOURCES.map((s) => ({
        id: s.id,
        name: s.name,
        description: s.description,
        iconKey: s.iconKey,
        enabled: s.id === "seed_middleby_hubspot" || s.id === "seed_middleby_ga4",
        syncAge: "Sample export",
      })),
      confirmedKnowledge: [],
      suggestedSources: [],
      suggestedKnowledge: [],
    },
    analytics: {
      linkedKpiKeys: [],
    },
  };
}

export function flattenMiddlebyKpis(categories) {
  const rows = [];
  for (const cat of categories || []) {
    for (const sec of cat.sections || []) {
      for (const kpi of sec.kpis || []) {
        rows.push({
          key: `${sec.name}::${kpi.name}`,
          section: sec.name,
          kpi,
        });
      }
    }
  }
  return rows;
}

export function kpiRegistryBasePath(profileId) {
  // Unified: every profile uses the same /kpis/* hub with Registry, Data
  // catalog, and Dashboard tabs. Profile-specific KPI data still flows via
  // useKpiRegistry, so the layout adapts without needing a separate path.
  // eslint-disable-next-line no-unused-vars
  const _ = profileId;
  return "/kpis";
}
