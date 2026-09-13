/**
 * Metrics proposed from the connected data that are NOT in the customer's registry yet.
 *
 * These are *candidates*, not results. A suggestion that only ever lives in a suggestion list
 * cannot be edited, governed, or reproduced -- and it would come back slightly different on the
 * next run -- so nothing should be built on one until it has been adopted into the registry.
 * Adopting is one click; until then the card is badged and cannot be selected for a dashboard.
 *
 * Static here, but in the shape the real thing arrives in: each entry is a `KpiSuggestion` exactly
 * as `GET /kpis/suggestions` returns it (`product/egpt_retail/api/openapi/schemas/kpis.yaml`), and
 * `rowFromSuggestion` is the only place that knows how a suggestion becomes a discovery row. When
 * this list is replaced by the real call, the card does not change.
 *
 * Two reasons travel with every suggestion, and they are not the same thing. `rationale` is the
 * agent's: why this metric follows from these tables. `feasibility` is the executor's: whether
 * those tables are actually in the semantic layer, computed as a set difference the model never
 * sees. The card shows them apart for that reason.
 */
export const SUGGESTED_KPI_SUGGESTIONS = {
 suggestions: [
  {
   identifier: "supplier__on_time_in_full_rate",
   name: "Supplier On-Time-In-Full (OTIF)",
   description: "Share of purchase orders delivered complete and on the promised date.",
   business_area: "supply_chain",
   concept: "Supplier",
   grain: "supplier",
   result_shape: "scalar",
   rationale:
    "supplier_deliveries carries promised_date, actual_date and qty_ordered against qty_received for every delivery, which is everything this needs.",
   derived_from_tables: ["supplier_deliveries"],
   confidence: "high",
   proposed_sql:
    "SELECT AVG(CASE WHEN actual_date <= promised_date AND qty_received >= qty_ordered THEN 1.0 ELSE 0.0 END) FROM supplier_deliveries WHERE supplier_id = {supplier_id}",
   feasibility: {
    kpi_id: "supplier__on_time_in_full_rate",
    verdict: "ready",
    missing_tables: [],
    rationale: "Every source table is present in the semantic snapshot.",
    semantic_snapshot: "demo",
   },
   proposed_by: { identifier: "kpi_suggestion", version: "1.0.0" },
  },
  {
   identifier: "promotion__margin_erosion_rate",
   name: "Promotion Margin Erosion",
   description: "Margin given away on promoted lines against the same lines sold at full price.",
   business_area: "sales",
   concept: "Promotion",
   grain: "overall",
   result_shape: "scalar",
   rationale:
    "sales_line separates discount_amount from net_sales, and promotion_calendar says which lines were on promotion when.",
   derived_from_tables: ["sales_line", "promotion_calendar"],
   confidence: "medium",
   proposed_sql:
    "SELECT SUM(s.discount_amount) / NULLIF(SUM(s.net_sales), 0) FROM sales_line s JOIN promotion_calendar p ON p.sku = s.sku AND s.sold_at BETWEEN p.starts_at AND p.ends_at",
   feasibility: {
    kpi_id: "promotion__margin_erosion_rate",
    verdict: "partial",
    missing_tables: ["promotion_calendar"],
    rationale: "promotion_calendar is not in the semantic snapshot; sales_line is.",
    semantic_snapshot: "demo",
   },
   proposed_by: { identifier: "kpi_suggestion", version: "1.0.0" },
  },
  {
   identifier: "stocklevel__ageing_stock_value",
   name: "Ageing Stock Value",
   description: "Value of inventory held beyond its expected sell-through window.",
   business_area: "inventory",
   concept: "StockLevel",
   grain: "product",
   result_shape: "rowset",
   rationale:
    "inventory_snapshot values what is on hand and goods_receipt dates when each lot arrived, which is what ageing needs.",
   derived_from_tables: ["inventory_snapshot", "goods_receipt"],
   confidence: "low",
   proposed_sql: null,
   feasibility: {
    kpi_id: "stocklevel__ageing_stock_value",
    verdict: "blocked",
    missing_tables: ["inventory_snapshot", "goods_receipt"],
    rationale: "Neither inventory_snapshot nor goods_receipt is in the semantic snapshot.",
    semantic_snapshot: "demo",
   },
   proposed_by: { identifier: "kpi_suggestion", version: "1.0.0" },
  },
 ],
 notes: "",
 semantic_snapshot: "demo",
};

/**
 * A `KpiSuggestion` as a discovery row. Row shape matches `flattenKpiIndexRows` so discovery
 * treats both inputs identically; the suggestion itself rides along untouched for the card to
 * read its reasons, evidence and confidence from.
 */
export function rowFromSuggestion(suggestion, kpiIndex) {
 return {
  suggested: true,
  suggestion,
  categoryIndex: -1,
  sectionIndex: -1,
  kpiIndex,
  categoryTitle: "Suggested",
  sectionName: suggestion.concept || "",
  kpi: {
   name: suggestion.name,
   desc: suggestion.description,
   subjectArea: suggestion.concept || undefined,
   businessAreaId: suggestion.business_area,
   inferred: true,
   // Evidence for a suggestion is the tables it cites, not workspace dataset ids.
   datasetIds: [],
  },
 };
}

export const SUGGESTED_KPI_CANDIDATES = SUGGESTED_KPI_SUGGESTIONS.suggestions.map(rowFromSuggestion);
