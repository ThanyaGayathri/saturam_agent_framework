/**
 * Enterprise data sources: datasets grouped by connector/source, linked to registry KPI names.
 * Read-only seed for KPI Gallery; KPI rows reference dataset ids via `datasetIds`.
 * Gallery source systems (SAP, Snowflake, PostgreSQL, Salesforce) each have a matching read-only
 * seed under Configure → Internal data sources (`CONFIGURE_SEED_INTERNAL` in configure-workspace.js).
 */

import { MIDDLEBY_KPI_DATA_CATALOG_DATASETS } from "./middleby-kpi-data-catalog";
import { MEDIA_KPI_DATA_CATALOG_DATASETS } from "./media-kpi-data-catalog";
import { isMediaProfile, isMiddlebyProfile } from "./profile-capabilities";

/** @typedef {{ id: string, sourceId: string, sourceLabel: string, technicalName: string, description: string, rowCountLabel: string, lastSyncLabel: string, status: "healthy" | "stale", attributeTags: string[], businessTags: string[], linkedKpiNames: string[] }} DataCatalogDataset */

/** @type {DataCatalogDataset[]} */
export const KPI_DATA_CATALOG_DATASETS = [
  {
    id: "ds-sap-inv-snapshot",
    sourceId: "sap",
    sourceLabel: "SAP",
    technicalName: "sap_prod.inventory.sku_snapshot",
    description: "SKU-level inventory snapshots, updated hourly.",
    rowCountLabel: "1.2M rows",
    lastSyncLabel: "2h ago",
    status: "healthy",
    attributeTags: ["sku_id", "quantity_on_hand", "dc_id", "snapshot_ts"],
    businessTags: ["Supply Chain", "Inventory"],
    linkedKpiNames: ["Inventory Turnover", "Stockout Rate", "Days of Stock Remaining", "Safety Stock Coverage"],
  },
  {
    id: "ds-sap-sales-trans",
    sourceId: "sap",
    sourceLabel: "SAP",
    technicalName: "sap_prod.sales.transaction_line",
    description: "Store and e-com sales lines with discounts and returns.",
    rowCountLabel: "8.4M rows",
    lastSyncLabel: "15m ago",
    status: "healthy",
    attributeTags: ["txn_id", "sku_id", "net_sales", "channel"],
    businessTags: ["Retail Performance", "POS"],
    linkedKpiNames: ["Gross Revenue", "Conversion Rate"],
  },
  {
    id: "ds-sap-cart-events",
    sourceId: "sap",
    sourceLabel: "SAP",
    technicalName: "sap_prod.ecom.cart_events",
    description: "Cart add, checkout start, and abandonment events by session.",
    rowCountLabel: "620K rows",
    lastSyncLabel: "4d ago",
    status: "stale",
    attributeTags: ["session_id", "step", "device", "abandoned"],
    businessTags: ["E-Commerce"],
    linkedKpiNames: ["Cart Abandonment Rate", "Conversion Rate"],
  },
  {
    id: "ds-sf-cc-sla",
    sourceId: "salesforce",
    sourceLabel: "Salesforce",
    technicalName: "sf.service_cloud.case_metrics",
    description: "Case queue times, first response, and SLA breach flags.",
    rowCountLabel: "340K rows",
    lastSyncLabel: "1h ago",
    status: "healthy",
    attributeTags: ["case_id", "queue", "first_response_min"],
    businessTags: ["Support"],
    linkedKpiNames: ["Contact Centre SLA Compliance", "Net Promoter Score (NPS)"],
  },
  {
    id: "ds-snow-forecast",
    sourceId: "snowflake",
    sourceLabel: "Snowflake",
    technicalName: "snowflake.demand.forecast_weekly",
    description: "Weekly SKU-location demand forecast and error bands.",
    rowCountLabel: "2.1M rows",
    lastSyncLabel: "6h ago",
    status: "healthy",
    attributeTags: ["sku_id", "location_id", "forecast_qty", "wape"],
    businessTags: ["Inventory", "Merchandise"],
    linkedKpiNames: ["Days of Stock Remaining", "Stockout Rate", "Inventory Turnover"],
  },
  {
    id: "ds-pg-orders",
    sourceId: "postgres",
    sourceLabel: "PostgreSQL",
    technicalName: "retail_db.orders.fulfillment_line",
    description: "Fulfillment lines with promised and actual ship dates.",
    rowCountLabel: "4.8M rows",
    lastSyncLabel: "30m ago",
    status: "healthy",
    attributeTags: ["order_id", "line_id", "fill_rate", "ship_date"],
    businessTags: ["Orders"],
    linkedKpiNames: ["Order Fill Rate", "On-Time Delivery Rate"],
  },
  {
    id: "ds-pg-margin",
    sourceId: "postgres",
    sourceLabel: "PostgreSQL",
    technicalName: "retail_db.finance.margin_by_category",
    description: "Category margin rollups with promo and markdown allocation.",
    rowCountLabel: "890K rows",
    lastSyncLabel: "3h ago",
    status: "healthy",
    attributeTags: ["category_id", "gross_margin_pct", "markdown_pct"],
    businessTags: ["Finance", "Pricing"],
    linkedKpiNames: ["Gross Margin Rate", "Gross Margin by Category"],
  },
  {
    id: "ds-pg-sellthrough",
    sourceId: "postgres",
    sourceLabel: "PostgreSQL",
    technicalName: "retail_db.pricing.sku_sell_through",
    description: "Weekly SKU sell-through, weeks of cover and markdown depth by season.",
    rowCountLabel: "640K rows",
    lastSyncLabel: "2h ago",
    status: "healthy",
    attributeTags: ["sku_id", "season_id", "weeks_of_cover", "sell_through_pct", "markdown_pct"],
    businessTags: ["Pricing", "Merchandising"],
    linkedKpiNames: ["Sell-Through Rate", "Markdown Rate"],
  },
  {
    id: "ds-snow-traffic",
    sourceId: "snowflake",
    sourceLabel: "Snowflake",
    technicalName: "snowflake.web.analytics_sessions",
    description: "Site and app sessions with landing page and funnel step.",
    rowCountLabel: "12M rows",
    lastSyncLabel: "45m ago",
    status: "healthy",
    attributeTags: ["session_id", "landing", "funnel_step"],
    businessTags: ["E-Commerce"],
    linkedKpiNames: ["Conversion Rate", "Cart Abandonment Rate"],
  },
  {
    id: "ds-sap-supplier",
    sourceId: "sap",
    sourceLabel: "SAP",
    technicalName: "sap_procurement.supplier_leadtime",
    description: "PO to GR lead times by supplier and category.",
    rowCountLabel: "210K rows",
    lastSyncLabel: "1d ago",
    status: "healthy",
    attributeTags: ["supplier_id", "category", "lead_time_days"],
    businessTags: ["Supply Chain"],
    linkedKpiNames: ["Supplier Lead Time", "Safety Stock Coverage"],
  },
];

export function getDatasetById(id, profileId) {
  let list = KPI_DATA_CATALOG_DATASETS;
  if (profileId) {
    if (isMiddlebyProfile(profileId)) list = MIDDLEBY_KPI_DATA_CATALOG_DATASETS;
    else if (isMediaProfile(profileId)) list = MEDIA_KPI_DATA_CATALOG_DATASETS;
  }
  return list.find((d) => d.id === id) || null;
}

export function summarizeDatasetsForKpi(datasetIds) {
  if (!Array.isArray(datasetIds) || datasetIds.length === 0) return [];
  return datasetIds.map((id) => getDatasetById(id)).filter(Boolean);
}

/** Group datasets by source for gallery sections. */
export function groupDatasetsBySource(datasets = KPI_DATA_CATALOG_DATASETS) {
  const map = new Map();
  for (const d of datasets) {
    if (!map.has(d.sourceId)) map.set(d.sourceId, { sourceId: d.sourceId, sourceLabel: d.sourceLabel, datasets: [] });
    map.get(d.sourceId).datasets.push(d);
  }
  return [...map.values()];
}

/** Profile-scoped datasets for KPI Add/Edit and gallery. */
export function kpiDatasetsForProfile(profileId) {
  if (isMiddlebyProfile(profileId)) return MIDDLEBY_KPI_DATA_CATALOG_DATASETS;
  if (isMediaProfile(profileId)) return MEDIA_KPI_DATA_CATALOG_DATASETS;
  return KPI_DATA_CATALOG_DATASETS;
}

export function groupDatasetsBySourceForProfile(profileId) {
  return groupDatasetsBySource(kpiDatasetsForProfile(profileId));
}

export function catalogStats(profileId) {
  const datasets = kpiDatasetsForProfile(profileId);
  const n = datasets.length;
  const attrs = datasets.reduce((s, d) => s + (d.attributeTags?.length || 0), 0);
  const kpisLinked = new Set();
  for (const d of datasets) {
    for (const k of d.linkedKpiNames || []) kpisLinked.add(k);
  }
  return { datasetCount: n, attributeCount: attrs, linkedKpiCount: kpisLinked.size };
}
