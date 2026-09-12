/**
 * McFadyen-aligned catalog tiers.
 * Each use case appears in exactly one section on /analytics.
 */

export const MIDDLEBY_ADDITIONAL_USE_CASES_SUBTITLE =
  "Additional analytics in the catalog. Supported with export snapshots or illustrative samples for deeper drill-down.";

export const MIDDLEBY_CLIENT_KPI_USE_CASES_SUBTITLE =
  "From first visit to closed deal — all the traffic, quote, and pipeline analytics grouped by where they land in the funnel.";

export const MIDDLEBY_FUTURE_USE_CASES_SUBTITLE =
  "Vision scenarios you can explore today with sample data — where the platform is headed once live feeds and richer HubSpot fields are in place.";

/**
 * Vivek McFadyen use cases of interest (five themes).
 */
export const MIDDLEBY_RECOMMENDED_DEMO_IDS = [
  "middleby-uc-02",
  "middleby-uc-18",
  "middleby-uc-05",
  "middleby-uc-08",
  "middleby-uc-16",
];

/**
 * Client reporting / KPI use cases, grouped by funnel stage so the catalog walks
 * left-to-right: Awareness -> Engagement -> Intent -> Conversion -> Closure.
 * Each stage sub-section renders under one heading on /analytics.
 */
export const MIDDLEBY_CLIENT_KPI_STAGES = [
  {
    id: "awareness",
    label: "Awareness",
    subtitle:
      "How buyers arrive and what they see first — traffic sources, top product pages, and site-search discovery.",
    ids: [
      "middleby-kpi-01", // Traffic Source and Device Analysis (GA4)
      "middleby-uc-03",  // Product Demand & Opportunity Discovery (category)
      "middleby-kpi-09", // Top Viewed Product Pages (GA4)
      "middleby-ex-04",  // Site Search & Zero-Result (Adobe)
    ],
  },
  {
    id: "engagement",
    label: "Engagement",
    subtitle:
      "How buyers behave once on site — bounce, depth, dwell, and guided-shopping tool completion.",
    ids: [
      "middleby-kpi-05", // Bounce and Engagement by Entry Page
      "middleby-kpi-04", // Guided Shopping Tool Conversion
    ],
  },
  {
    id: "intent",
    label: "Intent",
    subtitle: "Where buyers signal purchase intent — live chat inquiries and SKU-level interest.",
    ids: [
      "middleby-kpi-03", // Live Chat Buy Intent
      "middleby-uc-19",  // Catalog / SKU Gap - High Traffic, Low Quotes
    ],
  },
  {
    id: "conversion",
    label: "Conversion",
    subtitle:
      "Where quotes are formed and stall — RFQ drop-off, category quote volume, and pipeline stage.",
    ids: [
      "middleby-uc-02",  // RFQ Funnel Intelligence (drop-off) — also in Recommended
      "middleby-kpi-10", // Quotes by Product Category
      "middleby-kpi-13", // Segment × Category cross-cut (new)
      "middleby-uc-13",  // Quote Pipeline by Brand, Region & Stage
    ],
  },
  {
    id: "closure",
    label: "Closure",
    subtitle:
      "Where deals close, where they stall, and the HubSpot hygiene that keeps closed-won data usable.",
    ids: [
      "middleby-kpi-06", // PO Reference Coverage
      "middleby-kpi-02", // Closed-Won Rate and Lost Reasons
      "middleby-kpi-07", // Quotes by Sales Territory (kpi-12 folds in as a drill)
      "middleby-kpi-11", // Quotes by State
      "middleby-kpi-08", // Quotes by Customer Segment
      "middleby-uc-18",  // Product Interest vs RFQ Gap (also in Recommended)
    ],
  },
];

/**
 * Cards that shouldn't appear as top-level tiles in the funnel walk but must
 * still be reachable via a drill or a direct link — kpi-12 is folded into
 * kpi-07's Look-at-next strip as a Brand × Territory drill.
 */
const HIDDEN_DRILL_ONLY_IDS = new Set(["middleby-kpi-12"]);

export const MIDDLEBY_CLIENT_KPI_USE_CASE_IDS = MIDDLEBY_CLIENT_KPI_STAGES.flatMap(
  (s) => s.ids
);

/** Look up stage metadata for a given client-KPI catalog ID. */
export function getMiddlebyClientKpiStage(catalogId) {
  for (const stage of MIDDLEBY_CLIENT_KPI_STAGES) {
    if (stage.ids.includes(catalogId)) return stage;
  }
  return null;
}

/**
 * Aspirational future use cases: demoable vision scenarios (sample data).
 */
export const MIDDLEBY_ASPIRATIONAL_USE_CASE_IDS = [
  "middleby-ns-01",
  "middleby-ns-02",
  "middleby-ns-03",
  "middleby-ns-04",
];

/**
 * Phase 2 / needs-more-data future use cases. Hidden from the visible catalog
 * (they aren't demoable yet without live dealer / cart-line / auth-state feeds)
 * but tracked here so they don't leak into the "Additional" list either.
 */
export const MIDDLEBY_PHASE2_FUTURE_USE_CASE_IDS = [
  "middleby-uc-06",
  "middleby-ex-02",
  "middleby-ex-05",
  "middleby-uc-25",
  "middleby-uc-27",
  "middleby-uc-26",
  "middleby-uc-14",
];

/** Visible Future section: aspirational scenarios only. */
export const MIDDLEBY_FUTURE_USE_CASE_IDS = [...MIDDLEBY_ASPIRATIONAL_USE_CASE_IDS];

const interestIndex = new Map(MIDDLEBY_RECOMMENDED_DEMO_IDS.map((id, i) => [id, i]));
const clientKpiIndex = new Map(MIDDLEBY_CLIENT_KPI_USE_CASE_IDS.map((id, i) => [id, i]));
const futureIndex = new Map(MIDDLEBY_FUTURE_USE_CASE_IDS.map((id, i) => [id, i]));
const aspirationalSet = new Set(MIDDLEBY_ASPIRATIONAL_USE_CASE_IDS);
const clientKpiSet = new Set(MIDDLEBY_CLIENT_KPI_USE_CASE_IDS);
const futureSet = new Set(MIDDLEBY_FUTURE_USE_CASE_IDS);
const recommendedSet = new Set(MIDDLEBY_RECOMMENDED_DEMO_IDS);

const allTierIds = new Set([
  ...MIDDLEBY_RECOMMENDED_DEMO_IDS,
  ...MIDDLEBY_CLIENT_KPI_USE_CASE_IDS,
  ...MIDDLEBY_FUTURE_USE_CASE_IDS,
  ...MIDDLEBY_PHASE2_FUTURE_USE_CASE_IDS,
  ...HIDDEN_DRILL_ONLY_IDS,
]);

export function isMiddlebyAspirationalUseCase(catalogId) {
  return aspirationalSet.has(catalogId);
}

/** Card badge for Future-section entries. */
export function getMiddlebyFuturePhaseLabel(catalogId) {
  if (aspirationalSet.has(catalogId)) return "Aspirational";
  if (futureSet.has(catalogId)) return "Phase 2";
  return "";
}

/** Card badge for Client KPI section and search results. */
export function getMiddlebyClientKpiPhaseLabel(catalogId) {
  if (recommendedSet.has(catalogId)) return "★ Priority";
  if (clientKpiSet.has(catalogId)) return "Client KPI";
  return "";
}

/** Unified tier badge for catalog cards (Client KPI, Aspirational, Phase 2). */
export function getMiddlebyCatalogPhaseLabel(catalogId) {
  const client = getMiddlebyClientKpiPhaseLabel(catalogId);
  if (client) return client;
  return getMiddlebyFuturePhaseLabel(catalogId);
}

export function resolveMiddlebyDemoTier(catalogId) {
  // Recommended keeps priority for overlapping IDs (e.g. uc-02, uc-18).
  if (recommendedSet.has(catalogId)) return "recommended";
  if (clientKpiSet.has(catalogId)) return "client-kpi";
  if (futureSet.has(catalogId)) return "future";
  if (String(catalogId || "").startsWith("middleby-")) return "additional";
  return "standard";
}

function pickOrdered(entries, ids) {
  const byId = new Map((entries || []).map((e) => [e.id, e]));
  return ids.map((id) => byId.get(id)).filter(Boolean);
}

export function getMiddlebyRecommendedDemoUseCases(entries) {
  return pickOrdered(entries, MIDDLEBY_RECOMMENDED_DEMO_IDS);
}

/**
 * Client KPI section: include recommended IDs too so the funnel walk stays
 * complete. Duplicates render a ★ Priority pill via getMiddlebyClientKpiPhaseLabel.
 */
export function getMiddlebyClientKpiUseCases(entries) {
  return pickOrdered(entries, MIDDLEBY_CLIENT_KPI_USE_CASE_IDS);
}

export function getMiddlebyFutureUseCases(entries) {
  return pickOrdered(entries, MIDDLEBY_FUTURE_USE_CASE_IDS);
}

export function getMiddlebyAdditionalUseCases(entries) {
  return (entries || [])
    .filter((e) => e.source === "seed" && !allTierIds.has(e.id))
    .sort((a, b) => (a.galleryOrder ?? 999) - (b.galleryOrder ?? 999));
}

export function compareMiddlebyDemoCatalogOrder(a, b) {
  const tiers = { recommended: 0, "client-kpi": 1, additional: 2, future: 3, standard: 4 };
  const ta = tiers[resolveMiddlebyDemoTier(a.id)] ?? 4;
  const tb = tiers[resolveMiddlebyDemoTier(b.id)] ?? 4;
  if (ta !== tb) return ta - tb;

  const ca = clientKpiIndex.get(a.id);
  const cb = clientKpiIndex.get(b.id);
  if (ca != null && cb != null) return ca - cb;

  const fa = futureIndex.get(a.id);
  const fb = futureIndex.get(b.id);
  if (fa != null && fb != null) return fa - fb;

  const ai = interestIndex.get(a.id);
  const bi = interestIndex.get(b.id);
  if (ai != null && bi != null) return ai - bi;
  if (ai != null) return -1;
  if (bi != null) return 1;
  return (a.galleryOrder ?? 999) - (b.galleryOrder ?? 999);
}

export function getMiddlebyCatalogTierIds() {
  return allTierIds;
}
