import { getMiddlebyPrimarySpec } from "./middleby-screen-data";
import {
  MIDDLEBY_CROSS_SOURCE_EXPLANATION,
  MIDDLEBY_DEMO_WINDOW_LABELS,
} from "./middleby-dataset-info";

function uniqStrings(values) {
  return [...new Set(values.filter((v) => v != null && String(v).trim() !== ""))];
}

const CROSS_SOURCE_PATH_RE =
  /browse_vs_quotes|executive_headlines|weekly_anomaly|unified_metrics|executive_growth/i;
const GA_EXPORT_PATH_RE = /Traffic|ga4|uc17_nl_chart_brand_sessions/i;

/**
 * Map a data file path (+ optional spec) to a human source/window line for cards and footers.
 */
export function labelFromFilePath(filePath, spec = null) {
  const p = String(filePath || "");
  if (p.includes("from_real_hubspot")) return MIDDLEBY_DEMO_WINDOW_LABELS.hubspot;
  if (p.includes("logrocket")) return MIDDLEBY_DEMO_WINDOW_LABELS.logrocket;
  if (CROSS_SOURCE_PATH_RE.test(p)) return MIDDLEBY_DEMO_WINDOW_LABELS.crossSource;
  if (GA_EXPORT_PATH_RE.test(p)) return MIDDLEBY_DEMO_WINDOW_LABELS.ga4;
  if (p.includes("shop_middleby") || p.includes("/catalog/")) return MIDDLEBY_DEMO_WINDOW_LABELS.shopCatalog;

  const mode = String(spec?.dataMode || "").toLowerCase();
  if (mode === "mixed") return MIDDLEBY_DEMO_WINDOW_LABELS.blended;
  if (mode === "synthetic" || mode === "blocked") return MIDDLEBY_DEMO_WINDOW_LABELS.illustrative;
  if (p) return MIDDLEBY_DEMO_WINDOW_LABELS.illustrative;
  return null;
}

/**
 * Zone F honesty badges for an insight session (spec + loaded rows).
 * @returns {Array<{ id: string, label: string, detail?: string }>}
 */
export function buildDataHonestyBadges(spec, rows = [], entry = null) {
  const badges = [];

  const typeLabel = resolveDataTypeLabel(spec, rows);
  if (typeLabel) {
    badges.push({ id: "data_type", label: typeLabel });
  }

  const anonNotes = uniqStrings(rows.map((r) => r.anon_note));
  if (anonNotes.length) {
    badges.push({ id: "anon_note", label: "Anonymised", detail: anonNotes[0] });
  }

  const segmentMethods = uniqStrings(rows.map((r) => r.segment_method));
  if (segmentMethods.length) {
    badges.push({
      id: "segment_method",
      label: "Derived segment",
      detail: rows.find((r) => r.notes)?.notes || segmentMethods[0],
    });
  }

  const demoWindows = uniqStrings(rows.map((r) => r.demo_window));
  if (demoWindows.length) {
    for (const w of demoWindows.slice(0, 3)) {
      badges.push({ id: `demo_window_${w}`, label: "Demo window", detail: w });
    }
  }

  const revenueMethods = uniqStrings(rows.map((r) => r.revenue_method));
  if (revenueMethods.length) {
    badges.push({
      id: "revenue_method",
      label: "Estimated revenue",
      detail: revenueMethods[0],
    });
  }

  if (
    !revenueMethods.length &&
    spec?.chartType === "treemap" &&
    (spec?.metricColumns || []).some((c) => /est_revenue/i.test(String(c)))
  ) {
    badges.push({
      id: "treemap_revenue_formula",
      label: "Estimated revenue",
      detail: "Quote count × catalog median list price",
    });
  }

  const sourceLine = labelFromFilePath(spec?.filePath, spec);
  if (sourceLine && !isRedundantSourceDetail(sourceLine, typeLabel)) {
    badges.push({ id: "source", label: "Source", detail: sourceLine });
  }

  const dataSources = uniqStrings(rows.map((r) => r.data_source));
  if (dataSources.length && !anonNotes.length) {
    badges.push({ id: "data_source", label: "Data source", detail: dataSources.join(" · ") });
  }

  if (String(spec?.designNotes || "").toLowerCase().includes("spam")) {
    badges.push({ id: "spam_filter", label: "Spam filtered", detail: "Test and spam deals excluded from counts" });
  }

  if (entry?.analyticsSheet === "Existing") {
    badges.push({ id: "sheet", label: "Existing capability", detail: "Shipped analytics pattern from prior rollout" });
  }

  return dedupeHonestyBadges(badges);
}

function resolveDataTypeLabel(spec, rows = []) {
  const mode = String(spec?.dataMode || "").toLowerCase();
  if (mode === "real") return "Export snapshot";
  if (mode === "synthetic") return "Illustrative sample";
  if (mode === "mixed") return "Blended sources";
  if (mode === "blocked") return "Illustrative sample";

  const exampleFlags = uniqStrings(rows.map((r) => r.example_data));
  if (!exampleFlags.length) return mode ? "Analytics sample" : null;

  const hasSynthetic = exampleFlags.some((f) => /synthetic|example/i.test(f));
  const hasReal = exampleFlags.some((f) => /real|none/i.test(f) && !/synthetic/i.test(f));
  if (hasSynthetic && hasReal) return "Blended sources";
  if (hasSynthetic) return "Illustrative sample";
  if (hasReal) return "Export slice";
  return null;
}

function isRedundantSourceDetail(detail, typeLabel) {
  const d = String(detail || "").toLowerCase();
  if (!d || !typeLabel) return false;
  if (typeLabel === "Illustrative sample" && /illustrative/.test(d)) return true;
  if (typeLabel === "Blended sources" && /blended|windows vary|windows differ/.test(d)) return true;
  if (detail === MIDDLEBY_DEMO_WINDOW_LABELS.illustrative) return true;
  if (detail === MIDDLEBY_DEMO_WINDOW_LABELS.blended) return true;
  return false;
}

/** Drop duplicate label-only pills (e.g. two "Blended sources" chips). */
function dedupeHonestyBadges(badges) {
  const seenLabelOnly = new Set();
  return badges.filter((b) => {
    if (b.detail) return true;
    if (seenLabelOnly.has(b.label)) return false;
    seenLabelOnly.add(b.label);
    return true;
  });
}

/** True when this chart blends multiple source windows or mixed export types. */
export function isMiddlebyBlendedSession(spec, rows = []) {
  const mode = String(spec?.dataMode || "").toLowerCase();
  if (mode === "mixed") return true;

  const exampleFlags = uniqStrings(rows.map((r) => r.example_data));
  if (exampleFlags.some((f) => /^mixed$/i.test(f))) return true;
  if (exampleFlags.some((f) => /synthetic|example/i.test(f)) && exampleFlags.some((f) => /real|none/i.test(f))) {
    return true;
  }

  const demoWindows = uniqStrings(rows.map((r) => r.demo_window));
  if (demoWindows.length > 1) return true;
  if (demoWindows.some((w) => /windows differ/i.test(String(w)))) return true;

  return false;
}

export function getMiddlebyCrossSourceFooterNote() {
  const { lead, detail, join } = MIDDLEBY_CROSS_SOURCE_EXPLANATION;
  return { lead, detail, join };
}

export function formatCatalogSourceLine(catalogId) {
  const spec = getMiddlebyPrimarySpec(catalogId);
  if (!spec?.filePath) return null;
  return labelFromFilePath(spec.filePath, spec);
}

/** One-line footer for data-grounded playground chat replies. */
export function formatMiddlebyChatSourceFooter(catalogId, spec = null) {
  const primary = spec || getMiddlebyPrimarySpec(catalogId);
  const source =
    formatCatalogSourceLine(catalogId) ||
    (primary?.filePath ? labelFromFilePath(primary.filePath, primary) : null);
  if (!source) return "";
  const anon = /hubspot/i.test(source) ? " (anonymised)" : "";
  return `\n\nBased on ${source}${anon}.`;
}
