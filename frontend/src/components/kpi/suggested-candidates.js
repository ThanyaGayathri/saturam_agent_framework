/**
 * Metrics proposed from the connected data that are NOT in the customer's registry yet.
 *
 * These are *candidates*, not results. A suggestion that only ever lives in a suggestion list
 * cannot be edited, governed, or reproduced -- and it would come back slightly different on the
 * next run -- so nothing should be built on one until it has been adopted into the registry.
 * Adopting is one click; until then the card is badged and cannot be selected for a dashboard.
 *
 * Static here. In production this is the model's output over the semantic layer, and each entry
 * arrives with the same shape, so the card does not change when the source does.
 *
 * Row shape matches `flattenKpiIndexRows` so discovery treats both inputs identically.
 */
export const SUGGESTED_KPI_CANDIDATES = [
 {
  suggested: true,
  categoryIndex: -1,
  sectionIndex: -1,
  kpiIndex: 0,
  categoryTitle: "Suggested",
  sectionName: "Replenishment",
  kpi: {
   name: "Supplier On-Time-In-Full (OTIF)",
   desc: "Share of purchase orders delivered complete and on the promised date.",
   unit: "%",
   level: "Operational",
   subjectArea: "Replenishment",
   businessAreaId: "inventory",
   inferred: true,
   // Present in the connected data, so this lands in "Ready to measure" once adopted.
   datasetIds: ["ds-sap-inv-snapshot"],
   suggestedReason:
    "Your SAP delivery records carry both promised and actual dates, which is everything this needs.",
  },
 },
 {
  suggested: true,
  categoryIndex: -1,
  sectionIndex: -1,
  kpiIndex: 1,
  categoryTitle: "Suggested",
  sectionName: "Profitability",
  kpi: {
   name: "Promotion Margin Erosion",
   desc: "Margin given away on promoted lines against the same lines sold at full price.",
   unit: "%",
   level: "Tactical",
   subjectArea: "Profitability",
   businessAreaId: "pricing",
   inferred: true,
   datasetIds: ["ds-sap-sales-trans"],
   suggestedReason:
    "Your transaction lines separate discount from net sales, so promoted and full-price margin can be compared directly.",
  },
 },
 {
  suggested: true,
  categoryIndex: -1,
  sectionIndex: -1,
  kpiIndex: 2,
  categoryTitle: "Suggested",
  sectionName: "Stock Health",
  kpi: {
   name: "Ageing Stock Value",
   desc: "Value of inventory held beyond its expected sell-through window.",
   unit: "currency",
   level: "Operational",
   subjectArea: "Stock Health",
   businessAreaId: "inventory",
   inferred: true,
   datasetIds: [],
   suggestedReason:
    "Proposed from your inventory snapshots, but the received date is not exposed on the snapshot table.",
  },
 },
];
