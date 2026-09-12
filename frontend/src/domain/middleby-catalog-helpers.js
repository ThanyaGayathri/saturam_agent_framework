import { formatCatalogSourceLine } from "./middleby-data-honesty";
import { getMiddlebyCrossSourceKind, getMiddlebyCrossSourceMeta } from "./middleby-dataset-info";
import { getMiddlebyPrimarySpec } from "./middleby-screen-data";
import { FEATURED_IMPACT } from "./featured-use-cases";
import {
  getMiddlebyCatalogTierIds,
  MIDDLEBY_FUTURE_USE_CASE_IDS,
  MIDDLEBY_RECOMMENDED_DEMO_IDS,
  compareMiddlebyDemoCatalogOrder,
  resolveMiddlebyDemoTier,
} from "./middleby-catalog-demo-tiers";
import {
  catalogHasPocAutomation,
  getPocAutomationCatalogIds,
  getPocPlaybooksForCatalogId,
  POC_AUTOMATION_CATALOG_PRIORITY,
} from "./middleby-automation-playbooks";

function normalizeLine(value) {
  return String(value || "").trim().toLowerCase();
}

/** Customer-facing problem line for analytics cards (distinct from sample question). */
export function resolveMiddlebyCardProblemSignal(entry) {
  if (!entry) return "";
  const sample = String(entry.sampleQuestion || "").trim();
  const featured = FEATURED_IMPACT[entry.id];
  if (featured && normalizeLine(featured) !== normalizeLine(sample)) return featured;

  const designNote = String(entry.items?.[0] || "").trim();
  if (designNote && normalizeLine(designNote) !== normalizeLine(sample) && !isInternalDesignNote(designNote)) {
    return designNote;
  }

  return "";
}

function isInternalDesignNote(text) {
  return /until .* ship|example until|wire|drill-down|footnote|blocked|label funnel|spam.test deals/i.test(text);
}

const CHART_EVIDENCE_LABELS = {
  sla_gauge: "SLA gauge",
  dual_axis_bar: "Side-by-side bar",
  priority_table: "Worklist table",
  ranked_table: "Ranked list",
  kpi_cards: "KPI cards",
  alert_list: "Weekly alerts",
  journey_timeline: "Journey timeline",
  recommendation_table: "Suggestion cards",
  bar: "Bar chart",
  stacked_bar: "Stacked bar",
  line: "Line chart",
  table: "Data table",
  sankey: "Brand flow",
  geo_map: "Regional map",
  treemap: "Category treemap",
  deploy_line: "Trend with releases",
};

export function formatEvidenceChartLabel(catalogId) {
  const spec = getMiddlebyPrimarySpec(catalogId);
  if (!spec?.chartType) return null;
  const friendly = CHART_EVIDENCE_LABELS[spec.chartType];
  if (friendly) return `${friendly} on open`;
  const fallback = spec.chartType.replace(/_/g, " ");
  return `${fallback} on open`;
}

export function enrichCatalogEntry(entry) {
  if (!entry) return entry;
  const crossSourceKind = entry.crossSourceKind || getMiddlebyCrossSourceKind(entry.id);
  const crossSourceMeta = getMiddlebyCrossSourceMeta(crossSourceKind);
  const problemSignal = resolveMiddlebyCardProblemSignal(entry);
  const pocPlaybooks = getPocPlaybooksForCatalogId(entry.id);
  return {
    ...entry,
    crossSourceKind,
    crossSourcePill: crossSourceMeta?.pill || null,
    crossSourceCaption: crossSourceMeta?.caption || null,
    problemSignal,
    sourceLine: formatCatalogSourceLine(entry.id),
    impactLine: problemSignal,
    evidenceLabel: formatEvidenceChartLabel(entry.id),
    automationReady: pocPlaybooks.length > 0,
    automationPlaybookCount: pocPlaybooks.length,
    automationSummaries: pocPlaybooks.map((p) => ({
      id: p.id,
      label: p.label,
      summary: p.automationSummary,
    })),
    automationLine:
      pocPlaybooks.length === 1
        ? pocPlaybooks[0].automationSummary
        : pocPlaybooks.map((p) => p.automationSummary).filter(Boolean).join(" "),
    demoTier: resolveMiddlebyDemoTier(entry.id),
  };
}

/** Split catalog into automation-ready (demo flow) vs other use cases. */
export function splitAutomationCatalogEntries(entries) {
  const list = (entries || []).map(enrichCatalogEntry);
  const automationReady = [];
  const other = [];
  const priority = new Map(POC_AUTOMATION_CATALOG_PRIORITY.map((id, i) => [id, i]));

  for (const entry of list) {
    if (entry.automationReady && entry.demoTier === "recommended") automationReady.push(entry);
    else other.push(entry);
  }

  automationReady.sort(
    (a, b) => (priority.get(a.id) ?? 999) - (priority.get(b.id) ?? 999)
  );

  return { automationReady, other };
}

/** Sort enriched entries: automation-ready first, then POC priority order. */
export function sortCatalogAutomationFirst(entries) {
  const priority = new Map(POC_AUTOMATION_CATALOG_PRIORITY.map((id, i) => [id, i]));
  return [...(entries || [])].sort((a, b) => {
    const aAuto = a.automationReady ? 0 : 1;
    const bAuto = b.automationReady ? 0 : 1;
    if (aAuto !== bAuto) return aAuto - bAuto;
    return (priority.get(a.id) ?? 999) - (priority.get(b.id) ?? 999);
  });
}

export { getPocAutomationCatalogIds, catalogHasPocAutomation };

export {
  getMiddlebyRecommendedDemoUseCases,
  getMiddlebyClientKpiUseCases,
  getMiddlebyAdditionalUseCases,
  getMiddlebyFutureUseCases,
  getMiddlebyFuturePhaseLabel,
  getMiddlebyClientKpiPhaseLabel,
  getMiddlebyCatalogPhaseLabel,
  isMiddlebyAspirationalUseCase,
  MIDDLEBY_ADDITIONAL_USE_CASES_SUBTITLE,
  MIDDLEBY_CLIENT_KPI_USE_CASES_SUBTITLE,
  MIDDLEBY_FUTURE_USE_CASES_SUBTITLE,
  MIDDLEBY_RECOMMENDED_DEMO_IDS,
  MIDDLEBY_CLIENT_KPI_USE_CASE_IDS,
  MIDDLEBY_FUTURE_USE_CASE_IDS,
  MIDDLEBY_ASPIRATIONAL_USE_CASE_IDS,
  getMiddlebyCatalogTierIds,
} from "./middleby-catalog-demo-tiers";

const tierExcludedIds = getMiddlebyCatalogTierIds();

/** Entries for sheet groups (excludes recommended, future, and featured-strip duplicates). */
export function filterMiddlebyCatalogSheetEntries(entries, extraExcludeIds = []) {
  const exclude = new Set([...tierExcludedIds, ...extraExcludeIds]);
  return (entries || []).filter((e) => !exclude.has(e.id));
}

/** Group catalog entries by analytics sheet only (readiness stays in seed/Excel, not UI). */
export function groupMiddlebyCatalogEntries(entries) {
  const list = filterMiddlebyCatalogSheetEntries(entries || []).map(enrichCatalogEntry);
  const bySheet = new Map();

  for (const entry of list) {
    const sheet = entry.analyticsSheet || "Use cases";
    if (!bySheet.has(sheet)) bySheet.set(sheet, []);
    bySheet.get(sheet).push(entry);
  }

  return [...bySheet.entries()]
    .map(([sheet, items]) => ({
      sheet,
      items: items.sort(compareMiddlebyDemoCatalogOrder),
    }))
    .sort((a, b) => {
      if (a.sheet === "Use cases") return -1;
      if (b.sheet === "Use cases") return 1;
      return a.sheet.localeCompare(b.sheet);
    });
}

export function getDrillTabLabel(spec) {
  if (!spec) return "View";
  const { catalogId, chartRole, chartType, dimensionColumns = [] } = spec;

  const byCatalog = {
    "middleby-uc-02:secondary": "LogRocket errors",
    "middleby-uc-04:secondary": "Quote aging",
    "middleby-uc-08:secondary": "Quote impact",
    "middleby-uc-11:secondary": "By brand",
    "middleby-uc-11:tertiary": "Deal table",
    "middleby-uc-25:secondary": "Cart detail",
    "middleby-uc-26:secondary": "By brand",
    "middleby-uc-26:tertiary": "Weekly trend",
    "middleby-ex-01:secondary": "Weekly trend",
  };
  const catalogKey = catalogId && chartRole ? `${catalogId}:${chartRole}` : "";
  if (byCatalog[catalogKey]) return byCatalog[catalogKey];

  if (chartRole === "secondary") {
    if (chartType === "stacked_bar" && dimensionColumns.includes("brand")) return "By brand";
    if (chartType === "bar" && dimensionColumns.includes("error_type")) return "LogRocket errors";
    if (chartType === "bar") return "Error breakdown";
    if (chartType === "line") return "Weekly trend";
    if (chartType === "table") return "Detail table";
    return "Breakdown";
  }
  if (chartRole === "tertiary") {
    if (chartType === "table") return "Deal table";
    if (chartType === "stacked_area") return "Weekly trend";
    return "Details";
  }
  return chartRole;
}
