/** Monitor hero chart specs (Middleby profile only). */

export const MONITOR_CHART_SPECS = {
  slaGauge: {
    chartType: "sla_gauge",
    dimensionColumns: ["brand"],
    metricColumns: ["days_open"],
    filePath: "/middleby/data/from_real_hubspot/sla_aging.json",
    dataMode: "mixed",
  },
  deliveryGeo: {
    chartType: "geo_map",
    dimensionColumns: ["state"],
    metricColumns: ["quote_count"],
    filePath: "/middleby/data/from_real_hubspot/delivery_geo.json",
    dataMode: "real",
  },
  brandOverlap: {
    chartType: "sankey",
    dimensionColumns: ["brand_a", "brand_b"],
    metricColumns: ["session_count"],
    filePath: "/middleby/data/brand_pair_overlap.json",
    dataMode: "mixed",
  },
  categoryTreemap: {
    chartType: "treemap",
    dimensionColumns: ["l1", "l2"],
    metricColumns: ["est_revenue"],
    filePath: "/middleby/data/category_l1_performance.json",
    dataMode: "mixed",
    honestyCaption: "Estimated revenue: quote count × catalog median list price.",
  },
  browseQuoteGap: {
    chartType: "dual_axis_bar",
    dimensionColumns: ["brand"],
    metricColumns: ["ga_sessions", "hubspot_quotes"],
    filePath: "/middleby/data/uc18_browse_vs_quotes_by_brand.json",
    dataMode: "mixed",
  },
  errorDeployLine: {
    chartType: "deploy_line",
    dimensionColumns: ["week_start"],
    metricColumns: ["value"],
    filePath: "/middleby/data/weekly_kpi_trend.json",
    dataMode: "mixed",
    filterMetric: "logrocket_error_sessions",
  },
  uxErrors: {
    chartType: "bar",
    dimensionColumns: ["error_type"],
    metricColumns: ["sessions_affected_example"],
    filePath: "/middleby/data/uc08_ux_error_impact_example.json",
    dataMode: "mixed",
  },
};

export const RELEASE_CALENDAR_PATH = "/middleby/data/release_calendar.json";
