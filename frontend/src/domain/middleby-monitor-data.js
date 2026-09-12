import { loadMiddlebyDataset, aggregateMetric, parseNum } from "./middleby-analytics-data";
import { getMiddlebyPrimarySpec } from "./middleby-screen-data";
import {
  getMiddlebyMonitor,
  MIDDLEBY_MONITOR_PIPELINE_HEALTH,
  MIDDLEBY_MONITOR_CUSTOMER_EXPERIENCE,
  MIDDLEBY_MONITOR_DEMAND_INTELLIGENCE,
  MIDDLEBY_MONITOR_BRAND_PORTFOLIO,
} from "./middleby-monitor-manifest";
import { MONITOR_CHART_SPECS, RELEASE_CALENDAR_PATH } from "./middleby-monitor-chart-specs";
import { loadSlaAgingRows, summarizeSlaAging } from "./middleby-sla-aging";

const CHART_TITLES = {
  slaGauge: "Quote aging vs SLA",
  deliveryGeo: "Quotes by delivery region",
  brandOverlap: "Multi-brand browse overlap",
  categoryTreemap: "Category demand (estimated revenue)",
  browseQuoteGap: "Browse vs quote gap by brand",
  errorDeployLine: "Error sessions vs site releases",
  uxErrors: "Top UX errors by sessions affected",
};

async function loadCatalogRows(catalogId) {
  const spec = getMiddlebyPrimarySpec(catalogId);
  if (!spec?.filePath) return { spec: null, rows: [] };
  const data = await loadMiddlebyDataset(spec.filePath);
  return { spec, rows: data.rows || [], blocked: data.blocked };
}

async function loadHeroChart(specKey, { title } = {}) {
  const base = MONITOR_CHART_SPECS[specKey];
  if (!base) return null;
  const data = await loadMiddlebyDataset(base.filePath);
  let rows = data.rows || [];
  if (base.filterMetric) {
    rows = rows.filter((r) => r.metric === base.filterMetric);
  }
  const chart = {
    key: specKey,
    title: title || CHART_TITLES[specKey] || specKey,
    spec: { ...base, title: title || CHART_TITLES[specKey] },
    rows,
    blocked: data.blocked,
    releaseRows: [],
  };
  if (specKey === "errorDeployLine") {
    const rel = await loadMiddlebyDataset(RELEASE_CALENDAR_PATH);
    chart.releaseRows = rel.rows || [];
  }
  return chart;
}

async function loadPipelineHealthSnapshot(monitor) {
  const [uc04, uc06, uc10, uc13, slaRows, slaChart] = await Promise.all([
    loadCatalogRows("middleby-uc-04"),
    loadCatalogRows("middleby-uc-06"),
    loadCatalogRows("middleby-uc-10"),
    loadCatalogRows("middleby-uc-13"),
    loadSlaAgingRows(),
    loadHeroChart("slaGauge"),
  ]);

  const urgent = uc04.rows.filter((r) => String(r.timeframe || "").includes("0 - 1"));
  const consult = urgent.filter((r) => String(r.consultation || "").toLowerCase() === "yes");
  const sla = summarizeSlaAging(slaRows);

  const dealerQuotes = aggregateMetric(uc06.rows, "quote_count");
  const topDealer = [...uc06.rows]
    .map((r) => ({
      label: r.dealer_or_rep_group || r.rep_group,
      v: parseNum(r.quote_count) ?? 0,
    }))
    .sort((a, b) => b.v - a.v)[0];

  const consultYes = uc10.rows.filter(
    (r) => String(r.consultation_requested || r.consultation || "").toLowerCase() === "yes"
  ).length;
  const consultNo = uc10.rows.filter(
    (r) => String(r.consultation_requested || r.consultation || "").toLowerCase() === "no"
  ).length;

  const stalledStages = uc13.rows.filter((r) => {
    const stage = String(r.deal_stage || r.stage || "").toLowerCase();
    return stage.includes("awaiting") || stage.includes("contact");
  }).length;

  if (slaChart) {
    slaChart.rows = slaRows;
  }

  return {
    monitor,
    headline: `${urgent.length} quotes need near-term follow-up; ${consult.length} requested consultation.`,
    kpis: [
      { label: "Urgent quotes (0-1 mo)", value: String(urgent.length) },
      { label: "Consultation requested", value: String(consult.length) },
      { label: "Over SLA (14+ days)", value: String(sla.overSlaCount) },
      { label: "Dealer quotes (sample)", value: dealerQuotes != null ? String(dealerQuotes) : "-" },
    ],
    sla,
    heroCharts: slaChart ? [slaChart] : [],
    highlights: [
      topDealer?.label
        ? `${topDealer.label} leads quote volume in the dealer sample (${topDealer.v} quotes).`
        : null,
      consultYes + consultNo > 0
        ? `Consultation split: ${consultYes} yes vs ${consultNo} no in HubSpot sample.`
        : null,
      stalledStages > 0
        ? `${stalledStages} deals in awaiting/contact stages in pipeline heatmap sample.`
        : null,
    ].filter(Boolean),
    catalogIds: monitor.catalogIds,
  };
}

async function loadCustomerExperienceSnapshot(monitor) {
  const [deployChart, uxChart, uc08] = await Promise.all([
    loadHeroChart("errorDeployLine"),
    loadHeroChart("uxErrors"),
    loadCatalogRows("middleby-uc-08"),
  ]);

  const topError = [...uc08.rows]
    .map((r) => ({
      label: r.error_type,
      v: parseNum(r.sessions_affected_example) ?? 0,
    }))
    .sort((a, b) => b.v - a.v)[0];

  const totalAffected = uc08.rows.reduce(
    (s, r) => s + (parseNum(r.sessions_affected_example) ?? 0),
    0
  );

  return {
    monitor,
    headline: topError
      ? `${topError.label} affects ${topError.v.toLocaleString()} sessions in the UX sample.`
      : "Site experience trends tracked against release calendar.",
    kpis: [
      { label: "Error types (sample)", value: String(uc08.rows.length) },
      { label: "Sessions affected", value: totalAffected.toLocaleString() },
      { label: "Site releases tracked", value: String(deployChart?.releaseRows?.length ?? 0) },
      { label: "Top error share", value: topError ? `${Math.round((topError.v / Math.max(totalAffected, 1)) * 100)}%` : "-" },
    ],
    heroCharts: [deployChart, uxChart].filter(Boolean),
    highlights: [
      topError ? `${topError.label} is the largest UX drag in the LogRocket sample.` : null,
      deployChart?.releaseRows?.length
        ? `${deployChart.releaseRows.length} site releases mapped for deploy regression checks.`
        : null,
    ].filter(Boolean),
    catalogIds: monitor.catalogIds,
  };
}

async function loadDemandIntelligenceSnapshot(monitor) {
  const [treemap, geo, gap, uc18] = await Promise.all([
    loadHeroChart("categoryTreemap"),
    loadHeroChart("deliveryGeo"),
    loadHeroChart("browseQuoteGap"),
    loadCatalogRows("middleby-uc-18"),
  ]);

  const gapRows = uc18.rows
    .map((r) => ({
      brand: r.brand,
      sessions: parseNum(r.ga_sessions) ?? 0,
      quotes: parseNum(r.hubspot_quotes) ?? 0,
      gap: (parseNum(r.ga_sessions) ?? 0) - (parseNum(r.hubspot_quotes) ?? 0) * 50,
    }))
    .sort((a, b) => b.gap - a.gap);
  const topGap = gapRows[0];

  return {
    monitor,
    headline: topGap
      ? `${topGap.brand} shows the widest browse-to-quote gap (${topGap.sessions.toLocaleString()} sessions, ${topGap.quotes} quotes).`
      : "Demand and conversion signals across categories, regions, and brands.",
    kpis: [
      { label: "Brands in gap view", value: String(uc18.rows.length) },
      { label: "Top gap brand", value: topGap?.brand || "-" },
      { label: "Regions ranked", value: String(geo?.rows?.length ?? 0) },
      { label: "Category rows", value: String(treemap?.rows?.length ?? 0) },
    ],
    heroCharts: [treemap, geo, gap].filter(Boolean),
    highlights: [
      topGap
        ? `${topGap.brand}: ${topGap.sessions.toLocaleString()} GA sessions vs ${topGap.quotes} HubSpot quotes.`
        : null,
      treemap?.rows?.length
        ? `Category treemap sized by estimated revenue (quote count × catalog list price).`
        : null,
    ].filter(Boolean),
    catalogIds: monitor.catalogIds,
  };
}

async function loadBrandPortfolioSnapshot(monitor) {
  const [sankey, gap, uc15] = await Promise.all([
    loadHeroChart("brandOverlap"),
    loadHeroChart("browseQuoteGap"),
    loadCatalogRows("middleby-uc-15"),
  ]);

  const multiBrandSessions = uc15.rows.filter((r) => (parseNum(r.brand_count) ?? 0) >= 2).length;
  const topFlow = [...(sankey?.rows || [])]
    .map((r) => ({
      label: `${r.brand_a} → ${r.brand_b}`,
      v: parseNum(r.session_count) ?? 0,
    }))
    .sort((a, b) => b.v - a.v)[0];

  return {
    monitor,
    headline: topFlow
      ? `Strongest cross-brand path: ${topFlow.label} (${topFlow.v} sessions in sample).`
      : "Portfolio view across multi-brand browse and quote conversion.",
    kpis: [
      { label: "Multi-brand sessions", value: String(multiBrandSessions) },
      { label: "Brand-pair flows", value: String(sankey?.rows?.length ?? 0) },
      { label: "Brands in gap view", value: String(gap?.rows?.length ?? 0) },
      { label: "Top overlap path", value: topFlow ? topFlow.label.replace(" → ", " / ") : "-" },
    ],
    heroCharts: [sankey, gap].filter(Boolean),
    highlights: [
      topFlow ? `${topFlow.label} is the top multi-brand overlap in the browse sample.` : null,
      multiBrandSessions > 0
        ? `${multiBrandSessions} sessions viewed 2+ brands before quoting.`
        : null,
    ].filter(Boolean),
    catalogIds: monitor.catalogIds,
  };
}

/**
 * Load KPI snapshot for a Middleby monitor (deterministic aggregates only).
 */
export async function loadMiddlebyMonitorSnapshot(monitorId) {
  const monitor = getMiddlebyMonitor(monitorId);
  if (!monitor) return null;

  switch (monitorId) {
    case MIDDLEBY_MONITOR_PIPELINE_HEALTH:
      return loadPipelineHealthSnapshot(monitor);
    case MIDDLEBY_MONITOR_CUSTOMER_EXPERIENCE:
      return loadCustomerExperienceSnapshot(monitor);
    case MIDDLEBY_MONITOR_DEMAND_INTELLIGENCE:
      return loadDemandIntelligenceSnapshot(monitor);
    case MIDDLEBY_MONITOR_BRAND_PORTFOLIO:
      return loadBrandPortfolioSnapshot(monitor);
    default:
      return {
        monitor,
        headline: monitor.heroQuestion,
        kpis: [],
        heroCharts: [],
        highlights: [],
        catalogIds: monitor.catalogIds,
      };
  }
}
