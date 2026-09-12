export const BUSINESS_AREAS = [
  {
    id: "inventory",
    label: "Inventory",
    subjectAreas: ["Revenue", "Stock Health", "Replenishment", "Supplier Management", "Shrinkage & Loss"],
  },
  {
    id: "orders",
    label: "Orders",
    subjectAreas: ["Order Fulfillment", "Fraud Detection", "Delivery Management", "Exceptions"],
  },
  {
    id: "merchandise",
    label: "Merchandise",
    subjectAreas: ["Assortment Planning", "Planogram Compliance", "Category Performance", "Vendor Management"],
  },
  {
    id: "store-ops",
    label: "Store Ops",
    subjectAreas: ["Labor & Staffing", "Foot Traffic", "Task Compliance", "KPI Monitoring"],
  },
  {
    id: "e-com",
    label: "E-Com",
    subjectAreas: ["Conversion", "Conversion Optimisation", "Traffic Sources", "Site Performance", "Product Discovery"],
  },
  {
    id: "pos",
    label: "POS",
    subjectAreas: ["Transaction Health", "Checkout Efficiency", "Payment Methods", "Staff Performance"],
  },
  {
    id: "support",
    label: "Support",
    subjectAreas: ["Customer Satisfaction", "Returns Management", "Contact Centre", "Wait Times", "Escalations"],
  },
  {
    id: "pricing",
    label: "Pricing",
    subjectAreas: ["Profitability", "Markdown Optimisation", "Competitor Pricing", "Sell-Through", "Promotions"],
  },
  {
    id: "finance",
    label: "Finance",
    subjectAreas: ["Gross Margin", "Shrinkage & Loss Prevention", "Budget vs Actuals", "Cost Efficiency"],
  },
  {
    id: "workforce",
    label: "Workforce",
    subjectAreas: ["Labor Cost", "Overtime", "Scheduling Compliance", "Productivity"],
  },
];

/**
 * Media & OTT business areas.
 *
 * Uses the same canonical IDs as retail so use case `businessAreaId`, KPI
 * `businessAreaId`, process `businessAreaId`, and alert `businessAreaId`
 * lookups all resolve identically across profiles. Only the labels and
 * subject-area lists are re-flavored for media & entertainment.
 */
export const MEDIA_BUSINESS_AREAS = [
  {
    id: "merchandise",
    label: "Content",
    subjectAreas: [
      "Trend & Intelligence",
      "Content Performance",
      "Content Library",
      "Publishing & Launch",
      "Editorial & Team Ops",
    ],
  },
  {
    id: "e-com",
    label: "OTT Platform",
    subjectAreas: ["Engagement", "Discovery", "AEO / GEO", "Market Signals"],
  },
  {
    id: "store-ops",
    label: "Broadcast",
    subjectAreas: ["Playout Health", "CDN Delivery"],
  },
  {
    // Subscribers folds signup, plans, and care into one lifecycle-owning
    // top-level area — that's how OTT orgs typically structure this.
    id: "support",
    label: "Subscribers",
    subjectAreas: [
      "Signup Funnel",
      "Payments",
      "Plans & Bundles",
      "Care & Escalations",
      "Content Integrity",
      "Competitor Pricing",
    ],
  },
  {
    id: "finance",
    label: "Monetization",
    subjectAreas: ["Ad Yield", "Content ROI"],
  },
];

export function allBusinessAreaOptions() {
  return [
    { value: "", label: "All Business Area" },
    ...BUSINESS_AREAS.map((b) => ({ value: b.id, label: b.label })),
  ];
}

/** Select options for KPI add/edit (no “All” row). */
export function businessAreaFormOptions() {
  return BUSINESS_AREAS.map((b) => ({ value: b.id, label: b.label }));
}

export function subjectAreaFormOptions(businessAreaId) {
  if (!businessAreaId) return [];
  const ba = BUSINESS_AREAS.find((b) => b.id === businessAreaId);
  return (ba?.subjectAreas ?? []).map((s) => ({ value: s, label: s }));
}

export function subjectAreaOptionsForBusinessArea(businessAreaId) {
  if (!businessAreaId) {
    const all = BUSINESS_AREAS.flatMap((b) => b.subjectAreas);
    const uniq = Array.from(new Set(all));
    return [{ value: "", label: "All Subject Area" }, ...uniq.map((s) => ({ value: s, label: s }))];
  }
  const ba = BUSINESS_AREAS.find((b) => b.id === businessAreaId);
  const list = ba ? ba.subjectAreas : [];
  return [{ value: "", label: "All Subject Area" }, ...list.map((s) => ({ value: s, label: s }))];
}

export function inferBusinessAreaIdForSubjectArea(subjectAreaName) {
  const needle = String(subjectAreaName || "").toLowerCase();
  if (!needle) return "";
  for (const ba of BUSINESS_AREAS) {
    for (const s of ba.subjectAreas) {
      if (String(s).toLowerCase() === needle) return ba.id;
    }
  }
  return "";
}

