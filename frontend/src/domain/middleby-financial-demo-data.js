/**
 * Portfolio Performance dashboard: hybrid real operational metrics + illustrative commerce sample.
 * Real: HubSpot quotes, GA browse, LogRocket errors, SLA aging, est_revenue treemap.
 * Illustrative: GMV/margin/orders/AOV (requires Adobe Commerce order export for production).
 */

import { loadMiddlebyDataset, parseNum } from "./middleby-analytics-data";
import { getMiddlebyPrimarySpec } from "./middleby-screen-data";
import { loadSlaAgingRows, summarizeSlaAging } from "./middleby-sla-aging";
import { MONITOR_CHART_SPECS } from "./middleby-monitor-chart-specs";

export const PORTFOLIO_PERFORMANCE_PATH = "/analytics/performance";

/** Executive briefing UCs that link to portfolio performance from Answer tab. */
export const PORTFOLIO_EXECUTIVE_CATALOG_IDS = ["middleby-uc-07", "middleby-uc-16"];

const OPERATIONAL_MONTHLY_PATH = "/middleby/data/portfolio_monthly_operational.json";
const ILLUSTRATIVE_COMMERCE_PATH = "/middleby/data/portfolio_illustrative_commerce.json";
const CATEGORY_PERFORMANCE_PATH = "/middleby/data/category_l1_performance.json";

const MONTH_TO_QUARTER = {
  "01": "Q1",
  "02": "Q1",
  "03": "Q1",
  "04": "Q2",
  "05": "Q2",
  "06": "Q2",
  "07": "Q3",
  "08": "Q3",
  "09": "Q3",
  "10": "Q4",
  "11": "Q4",
  "12": "Q4",
};

export const PORTFOLIO_CHART_TABS = [
  { id: "pipeline", label: "Pipeline value", metric: "est_pipeline_value", format: "money" },
  { id: "quotes", label: "Quotes", metric: "quote_count", format: "count" },
  { id: "browse", label: "Browse sessions", metric: "browse_sessions", format: "count" },
  { id: "errors", label: "Error sessions", metric: "error_sessions", format: "count" },
];

export const PORTFOLIO_COMMERCE_TABS = [
  { id: "gmv", label: "GMV", metric: "gmv", format: "money" },
  { id: "margin", label: "Margin", metric: "margin", format: "money" },
  { id: "orders", label: "Orders", metric: "orders", format: "count" },
  { id: "aov", label: "AOV", metric: "aov", format: "money" },
];

function quarterFromMonth(monthKey) {
  const mm = String(monthKey || "").slice(5, 7);
  return MONTH_TO_QUARTER[mm] || "Q1";
}

function yearFromMonth(monthKey) {
  return String(monthKey || "").slice(0, 4);
}

function formatMoney(n) {
  const v = Number(n);
  if (!Number.isFinite(v)) return "-";
  if (Math.abs(v) >= 1_000_000) return `$${(v / 1_000_000).toFixed(2)}M`;
  if (Math.abs(v) >= 1_000) return `$${(v / 1_000).toFixed(0)}K`;
  return `$${Math.round(v)}`;
}

function formatCount(n) {
  const v = Number(n);
  if (!Number.isFinite(v)) return "-";
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 10_000) return `${(v / 1_000).toFixed(1)}K`;
  return String(Math.round(v));
}

function formatMetricValue(value, format) {
  if (format === "money") return formatMoney(value);
  return formatCount(value);
}

function variancePct(current, prior) {
  const c = Number(current);
  const p = Number(prior);
  if (!Number.isFinite(c) || !Number.isFinite(p) || p === 0) return null;
  return ((c - p) / p) * 100;
}

function sumField(rows, field) {
  return rows.reduce((s, r) => s + (parseNum(r[field]) ?? 0), 0);
}

function filterByPeriod(rows, { year, quarter }) {
  return rows.filter((r) => {
    const y = yearFromMonth(r.month);
    const q = quarterFromMonth(r.month);
    if (year && y !== year) return false;
    if (quarter && q !== quarter) return false;
    return true;
  });
}

async function loadCatalogRows(catalogId) {
  const spec = getMiddlebyPrimarySpec(catalogId);
  if (!spec?.filePath) return [];
  const data = await loadMiddlebyDataset(spec.filePath);
  return data.rows || [];
}

function rollupCategoryL1(rows) {
  const byL1 = new Map();
  for (const r of rows) {
    const l1 = r.l1 || "Other";
    const prev = byL1.get(l1) || { l1, quote_count: 0, est_revenue: 0 };
    prev.quote_count += parseNum(r.quote_count) ?? 0;
    prev.est_revenue += parseNum(r.est_revenue) ?? 0;
    byL1.set(l1, prev);
  }
  const list = [...byL1.values()].sort((a, b) => b.est_revenue - a.est_revenue);
  const totalRev = list.reduce((s, r) => s + r.est_revenue, 0) || 1;
  const totalQuotes = list.reduce((s, r) => s + r.quote_count, 0) || 1;
  return list.map((r) => ({
    ...r,
    est_revenue_fmt: formatMoney(r.est_revenue),
    pipeline_mix_pct: `${((r.est_revenue / totalRev) * 100).toFixed(1)}%`,
    quote_mix_pct: `${((r.quote_count / totalQuotes) * 100).toFixed(1)}%`,
  }));
}

function rollupCategoryL2(rows, l1) {
  const filtered = rows.filter((r) => r.l1 === l1);
  const byL2 = new Map();
  for (const r of filtered) {
    const l2 = r.l2 || "Other";
    const prev = byL2.get(l2) || { l2, l1, quote_count: 0, est_revenue: 0 };
    prev.quote_count += parseNum(r.quote_count) ?? 0;
    prev.est_revenue += parseNum(r.est_revenue) ?? 0;
    byL2.set(l2, prev);
  }
  const list = [...byL2.values()].sort((a, b) => b.est_revenue - a.est_revenue);
  const totalRev = list.reduce((s, r) => s + r.est_revenue, 0) || 1;
  const totalQuotes = list.reduce((s, r) => s + r.quote_count, 0) || 1;
  return list.map((r) => ({
    ...r,
    est_revenue_fmt: formatMoney(r.est_revenue),
    pipeline_mix_pct: `${((r.est_revenue / totalRev) * 100).toFixed(1)}%`,
    quote_mix_pct: `${((r.quote_count / totalQuotes) * 100).toFixed(1)}%`,
  }));
}

function computeCategoryShares(rows) {
  const byL1 = new Map();
  let totalQuotes = 0;
  let totalRevenue = 0;
  for (const r of rows) {
    const l1 = r.l1 || "Other";
    const q = parseNum(r.quote_count) ?? 0;
    const rev = parseNum(r.est_revenue) ?? 0;
    totalQuotes += q;
    totalRevenue += rev;
    const prev = byL1.get(l1) || { quote_count: 0, est_revenue: 0 };
    prev.quote_count += q;
    prev.est_revenue += rev;
    byL1.set(l1, prev);
  }
  const shares = {};
  for (const [l1, agg] of byL1) {
    shares[l1] = {
      quoteShare: totalQuotes ? agg.quote_count / totalQuotes : 0,
      revenueShare: totalRevenue ? agg.est_revenue / totalRevenue : 0,
      quote_count: agg.quote_count,
      est_revenue: agg.est_revenue,
    };
  }
  return shares;
}

function scaleOperationalMonthly(rows, quoteShare, revenueShare) {
  return rows.map((r) => ({
    ...r,
    quote_count: String(Math.round((parseNum(r.quote_count) ?? 0) * quoteShare)),
    est_pipeline_value: String(Math.round((parseNum(r.est_pipeline_value) ?? 0) * revenueShare)),
    browse_sessions: String(Math.round((parseNum(r.browse_sessions) ?? 0) * quoteShare)),
  }));
}

function scaleCommerceMonthly(rows, revenueShare) {
  return rows.map((r) => ({
    ...r,
    gmv: String(Math.round((parseNum(r.gmv) ?? 0) * revenueShare)),
    margin: String(Math.round((parseNum(r.margin) ?? 0) * revenueShare)),
    orders: String(Math.round((parseNum(r.orders) ?? 0) * revenueShare)),
    prior_gmv: String(Math.round((parseNum(r.prior_gmv) ?? 0) * revenueShare)),
    prior_margin: String(Math.round((parseNum(r.prior_margin) ?? 0) * revenueShare)),
    prior_orders: String(Math.round((parseNum(r.prior_orders) ?? 0) * revenueShare)),
  }));
}

function buildOperationalKpis(operationalRows, slaSummary, urgentQuotes, consultQuotes, categoryScoped = false) {
  const quotes = sumField(operationalRows, "quote_count");
  const pipeline = sumField(operationalRows, "est_pipeline_value");
  const browse = sumField(operationalRows, "browse_sessions");
  const errors = sumField(operationalRows, "error_sessions");

  const priorHalf = operationalRows.slice(0, Math.floor(operationalRows.length / 2));
  const recentHalf = operationalRows.slice(Math.floor(operationalRows.length / 2));
  const priorQuotes = sumField(priorHalf, "quote_count") || 1;
  const recentQuotes = sumField(recentHalf, "quote_count");
  const quoteTrend = variancePct(recentQuotes, priorQuotes);

  const scopeNote = categoryScoped ? "Category-scoped estimate" : "Sample window";
  const portfolioNote = "Portfolio-wide (LogRocket sample)";

  return [
    {
      label: "Est. pipeline value",
      value: formatMoney(pipeline),
      sub: categoryScoped ? "Category share of portfolio pipeline" : "Quote count × catalog median list price",
      delta: quoteTrend != null ? `${quoteTrend > 0 ? "+" : ""}${quoteTrend.toFixed(1)}% vs prior half` : null,
      improving: quoteTrend != null ? quoteTrend > 0 : true,
      spark: operationalRows.map((r) => parseNum(r.est_pipeline_value) ?? 0),
    },
    {
      label: "HubSpot quotes",
      value: formatCount(quotes),
      sub: scopeNote,
      delta: quoteTrend != null ? `${quoteTrend > 0 ? "+" : ""}${quoteTrend.toFixed(1)}%` : null,
      improving: quoteTrend != null ? quoteTrend > 0 : true,
      spark: operationalRows.map((r) => parseNum(r.quote_count) ?? 0),
    },
    {
      label: "Urgent quotes (0-1 mo)",
      value: String(urgentQuotes),
      sub: categoryScoped ? "Scaled to category quote share" : "Near-term follow-up",
      improving: true,
      spark: null,
    },
    {
      label: "Over SLA (14+ days)",
      value: String(slaSummary.overSlaCount),
      sub: categoryScoped ? "Scaled to category quote share" : "Active quotes in aging sample",
      improving: false,
      spark: null,
    },
    {
      label: "Browse sessions",
      value: formatCount(browse),
      sub: categoryScoped ? "Category share of GA browse sample" : "GA sample projected",
      improving: true,
      spark: operationalRows.map((r) => parseNum(r.browse_sessions) ?? 0),
    },
    {
      label: "Error sessions",
      value: formatCount(errors),
      sub: portfolioNote,
      improving: false,
      spark: operationalRows.map((r) => parseNum(r.error_sessions) ?? 0),
    },
    {
      label: "Consultation requested",
      value: String(consultQuotes),
      sub: categoryScoped ? "Scaled to category quote share" : "HubSpot urgent slice",
      improving: true,
      spark: null,
    },
  ];
}

function buildIllustrativeKpis(commerceRows, categoryScoped = false) {
  const gmv = sumField(commerceRows, "gmv");
  const margin = sumField(commerceRows, "margin");
  const orders = sumField(commerceRows, "orders");
  const priorGmv = sumField(commerceRows, "prior_gmv");
  const priorMargin = sumField(commerceRows, "prior_margin");
  const priorOrders = sumField(commerceRows, "prior_orders");
  const aov = orders ? gmv / orders : 0;
  const priorAov = priorOrders ? priorGmv / priorOrders : 0;

  const gmvVar = variancePct(gmv, priorGmv);
  const marginVar = variancePct(margin, priorMargin);
  const ordersVar = variancePct(orders, priorOrders);
  const aovVar = variancePct(aov, priorAov);

  const scopeSub = categoryScoped ? "Illustrative · category share" : undefined;

  return [
    {
      label: "GMV",
      value: formatMoney(gmv),
      priorLabel: "Prior period",
      priorValue: formatMoney(priorGmv),
      delta: gmvVar != null ? `${gmvVar > 0 ? "+" : ""}${gmvVar.toFixed(1)}%` : null,
      improving: gmvVar != null ? gmvVar > 0 : true,
      illustrative: true,
      scopeSub,
      spark: commerceRows.map((r) => parseNum(r.gmv) ?? 0),
    },
    {
      label: "Margin",
      value: formatMoney(margin),
      priorLabel: "Prior period",
      priorValue: formatMoney(priorMargin),
      delta: marginVar != null ? `${marginVar > 0 ? "+" : ""}${marginVar.toFixed(1)}%` : null,
      improving: marginVar != null ? marginVar > 0 : true,
      illustrative: true,
      scopeSub,
      spark: commerceRows.map((r) => parseNum(r.margin) ?? 0),
    },
    {
      label: "Orders",
      value: formatCount(orders),
      priorLabel: "Prior period",
      priorValue: formatCount(priorOrders),
      delta: ordersVar != null ? `${ordersVar > 0 ? "+" : ""}${ordersVar.toFixed(1)}%` : null,
      improving: ordersVar != null ? ordersVar > 0 : true,
      illustrative: true,
      scopeSub,
      spark: commerceRows.map((r) => parseNum(r.orders) ?? 0),
    },
    {
      label: "AOV",
      value: formatMoney(aov),
      priorLabel: "Prior period",
      priorValue: formatMoney(priorAov),
      delta: aovVar != null ? `${aovVar > 0 ? "+" : ""}${aovVar.toFixed(1)}%` : null,
      improving: aovVar != null ? aovVar > 0 : true,
      illustrative: true,
      scopeSub,
      spark: commerceRows.map((r) => parseNum(r.aov) ?? 0),
    },
  ];
}

function chartRowsFromMonthly(rows, tab, source = "operational") {
  return rows.map((r) => ({
    month_label: r.month_label,
    value: parseNum(r[tab.metric]) ?? 0,
    value_fmt: formatMetricValue(r[tab.metric], tab.format),
    demo_window: r.demo_window,
    data_mode: r.data_mode,
  }));
}

/**
 * @param {{ year?: string, quarter?: string, l1?: string }} filters
 */
export async function loadPortfolioPerformanceSnapshot(filters = {}) {
  const year = filters.year || "2026";
  const quarter = filters.quarter || null;
  const l1Filter = filters.l1 || "all";

  const [operationalAll, commerceAll, categoryRows, slaRows, uc04] = await Promise.all([
    loadMiddlebyDataset(OPERATIONAL_MONTHLY_PATH),
    loadMiddlebyDataset(ILLUSTRATIVE_COMMERCE_PATH),
    loadMiddlebyDataset(CATEGORY_PERFORMANCE_PATH),
    loadSlaAgingRows(),
    loadCatalogRows("middleby-uc-04"),
  ]);

  const operationalRows = filterByPeriod(operationalAll.rows || [], { year, quarter });
  const commerceRows = filterByPeriod(commerceAll.rows || [], { year, quarter });
  const allCategoryRows = categoryRows.rows || [];
  const categoryShares = computeCategoryShares(allCategoryRows);
  const categoryScoped = l1Filter !== "all";
  const activeShare = categoryScoped ? categoryShares[l1Filter] : null;

  let scopedOperational = operationalRows;
  let scopedCommerce = commerceRows;
  let urgentCount = uc04.filter((r) => String(r.timeframe || "").includes("0 - 1")).length;
  let consultCount = uc04.filter((r) => {
    const tf = String(r.timeframe || "");
    const isUrgent = tf.includes("0 - 1") || tf.includes("0-1");
    return isUrgent && String(r.consultation || r.consultation_requested || "").toLowerCase() === "yes";
  }).length;
  let scopedSla = summarizeSlaAging(slaRows);

  if (categoryScoped && activeShare) {
    scopedOperational = scaleOperationalMonthly(
      operationalRows,
      activeShare.quoteShare,
      activeShare.revenueShare
    );
    scopedCommerce = scaleCommerceMonthly(commerceRows, activeShare.revenueShare);
    urgentCount = Math.max(1, Math.round(urgentCount * activeShare.quoteShare));
    consultCount = Math.max(0, Math.round(consultCount * activeShare.quoteShare));
    scopedSla = {
      ...scopedSla,
      overSlaCount: Math.max(0, Math.round(scopedSla.overSlaCount * activeShare.quoteShare)),
    };
  }

  const categoryMix =
    categoryScoped && l1Filter !== "all"
      ? rollupCategoryL2(allCategoryRows, l1Filter)
      : rollupCategoryL1(allCategoryRows);
  const treemapSpec = { ...MONITOR_CHART_SPECS.categoryTreemap };
  let treemapRows = allCategoryRows;
  if (categoryScoped) {
    treemapRows = treemapRows.filter((r) => r.l1 === l1Filter);
  }

  const periodLabel = quarter ? `${quarter} ${year}` : year;
  const sharePct = activeShare ? Math.round(activeShare.revenueShare * 100) : null;

  const headline = categoryScoped && activeShare
    ? `${l1Filter}: ${formatMoney(activeShare.est_revenue)} estimated pipeline (${sharePct}% of portfolio) in ${periodLabel}. True GMV requires Adobe order feed.`
    : `Quote pipeline is trending up in ${periodLabel}; estimated category value from catalog pricing. True GMV requires Adobe order feed.`;

  return {
    filters: { year, quarter, l1: l1Filter },
    periodLabel,
    categoryScoped,
    categoryFilterLabel: categoryScoped ? l1Filter : null,
    categorySharePct: sharePct,
    headline,
    operationalKpis: buildOperationalKpis(
      scopedOperational,
      scopedSla,
      urgentCount,
      consultCount,
      categoryScoped
    ),
    illustrativeKpis: buildIllustrativeKpis(scopedCommerce, categoryScoped),
    operationalMonthly: scopedOperational,
    commerceMonthly: scopedCommerce,
    categoryMix,
    categoryMixLevel: categoryScoped ? "l2" : "l1",
    treemap: {
      spec: treemapSpec,
      rows: treemapRows,
    },
    l1Options: ["all", ...new Set(allCategoryRows.map((r) => r.l1).filter(Boolean))],
    years: ["2025", "2026"],
    quarters: ["Q1", "Q2", "Q3", "Q4"],
    honesty: {
      operational: "Pipeline and demand metrics blend HubSpot quote samples, GA browse sessions, and LogRocket error counts.",
      illustrative:
        "Commerce metrics (GMV, margin, orders, AOV) are illustrative samples. Production values require Adobe Commerce order export.",
      revenue:
        "Estimated pipeline value uses quote count × catalog median list price. Not actual invoiced revenue.",
    },
  };
}

export function getPortfolioChartSeries(snapshot, tabId, source = "operational") {
  if (!snapshot) return [];
  const tabs = source === "commerce" ? PORTFOLIO_COMMERCE_TABS : PORTFOLIO_CHART_TABS;
  const tab = tabs.find((t) => t.id === tabId) || tabs[0];
  const rows = source === "commerce" ? snapshot.commerceMonthly : snapshot.operationalMonthly;
  return chartRowsFromMonthly(rows, tab, source);
}

export { formatMoney, formatCount, formatMetricValue };
