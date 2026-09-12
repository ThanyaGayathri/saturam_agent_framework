/**
 * Middleby screen data spec with P0 extensions (merged over auto-generated base).
 * Keeps sync-middleby-data.mjs output untouched.
 */
import {
  MIDDLEBY_SCREEN_DATA_SPEC as BASE_SPEC,
} from "./middleby-screen-data-spec";

const UC04_SLA_SPEC = {
  sheet: "Use cases",
  ucNumber: 4,
  catalogId: "middleby-uc-04",
  useCaseName: "Sales Rep / Dealer Copilot",
  sampleQuestion: "Which quotes require immediate follow-up today?",
  chartType: "sla_gauge",
  chartRole: "secondary",
  parentChartRole: "primary",
  drillInto: null,
  dimensionColumns: ["brand"],
  metricColumns: ["days_open"],
  filePath: "/middleby/data/from_real_hubspot/sla_aging.json",
  dataMode: "mixed",
  designNotes: "Quote aging vs dealer SLA threshold; days_open derived from timeframe when created_date absent.",
};

function buildMergedSpec() {
  const specs = BASE_SPEC.map((s) => ({ ...s }));
  const primary = specs.find(
    (s) => s.catalogId === "middleby-uc-04" && s.chartRole === "primary"
  );
  if (primary && !primary.drillInto) {
    primary.drillInto = "secondary";
  }
  if (!specs.some((s) => s.catalogId === "middleby-uc-04" && s.chartRole === "secondary")) {
    specs.push(UC04_SLA_SPEC);
  }
  return specs;
}

const MERGED_SPEC = buildMergedSpec();

export const MIDDLEBY_SCREEN_DATA_SPEC = MERGED_SPEC;

export function getMiddlebySpecsForCatalog(catalogId) {
  return MIDDLEBY_SCREEN_DATA_SPEC.filter((s) => s.catalogId === catalogId);
}

export function getMiddlebyPrimarySpec(catalogId) {
  const found = getMiddlebySpecsForCatalog(catalogId).find((s) => s.chartRole === "primary");
  if (found) return found;
  if (String(catalogId || "").startsWith("user-")) {
    return {
      sheet: "Custom",
      catalogId,
      useCaseName: "Custom analytics use case",
      chartType: "kpi_cards",
      chartRole: "primary",
      dimensionColumns: ["metric"],
      metricColumns: ["value"],
      filePath: "/middleby/data/uc23_unified_metrics_summary.json",
      dataMode: "mixed",
      designNotes: "Summary metrics for user-created analytics use cases.",
    };
  }
  return null;
}
