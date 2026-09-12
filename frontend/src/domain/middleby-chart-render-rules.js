import { parseNum, rowLabel } from "./middleby-analytics-data";

/** Documented rendering predicates (proposal §4.5 Table 5). */
export const MIDDLEBY_CHART_RENDER_RULES = {
  donutSmallN: {
    id: "donut_small_n",
    description: "Use horizontal bars when the smallest slice is under 10 and the ratio exceeds 10:1.",
  },
  barTopNOther: {
    id: "bar_top_n_other",
    description: "Collapse bar chart dimensions beyond top N into an Other bucket (default N=12).",
    defaultTopN: 12,
  },
  barMinMetric: {
    id: "bar_min_metric",
    description: "Drop bar rows with zero or null primary metric before ranking.",
  },
};

export function shouldUseBarInsteadOfDonut(rows, metricCol) {
  const vals = (rows || []).map((r) => parseNum(r[metricCol]) ?? 0).filter((v) => v > 0);
  if (vals.length < 2) return true;
  const sorted = [...vals].sort((a, b) => a - b);
  const min = sorted[0];
  const max = sorted[sorted.length - 1];
  if (min < 10 && max / Math.max(min, 1) > 10) return true;
  return false;
}

/**
 * Rank rows by primary metric, keep top N, bucket remainder as Other.
 */
export function applyTopNOther(rows, spec, topN = MIDDLEBY_CHART_RENDER_RULES.barTopNOther.defaultTopN) {
  if (!rows?.length || !spec?.metricColumns?.[0]) return rows || [];
  const metricCol = spec.metricColumns[0];
  const dims = spec.dimensionColumns || [];

  const ranked = [...rows]
    .map((row) => ({
      row,
      value: parseNum(row[metricCol]) ?? 0,
    }))
    .filter((x) => x.value > 0)
    .sort((a, b) => b.value - a.value);

  if (ranked.length <= topN) return ranked.map((x) => x.row);

  const head = ranked.slice(0, topN);
  const tail = ranked.slice(topN);
  const otherValue = tail.reduce((s, x) => s + x.value, 0);
  const otherRow = { [metricCol]: String(otherValue) };
  if (dims[0]) otherRow[dims[0]] = `Other (${tail.length} regions)`;
  otherRow._topNCollapsed = true;

  return [...head.map((x) => x.row), otherRow];
}

export function prepareChartRows(rows, spec) {
  if (!rows?.length || !spec) return rows || [];
  const type = spec.chartType || "table";
  const collapsible =
    type === "bar" ||
    type === "histogram" ||
    type === "grouped_bar" ||
    type === "table" ||
    type === "ranked_table" ||
    type === "priority_table";
  if (collapsible && spec.metricColumns?.[0] && spec.dimensionColumns?.length) {
    return applyTopNOther(rows, spec);
  }
  return rows;
}

export function topNOtherFootnote(rows, spec, topN = MIDDLEBY_CHART_RENDER_RULES.barTopNOther.defaultTopN) {
  const metricCol = spec?.metricColumns?.[0];
  if (!metricCol) return null;
  const positive = (rows || []).filter((r) => (parseNum(r[metricCol]) ?? 0) > 0);
  if (positive.length <= topN) return null;
  return `Showing top ${topN} by ${metricCol.replace(/_/g, " ")}; remaining regions grouped as Other.`;
}
