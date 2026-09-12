import {
 getMiddlebyPrimarySpec,
 getMiddlebySpecsForCatalog,
 MIDDLEBY_SCREEN_DATA_SPEC,
} from "./middleby-screen-data";
import { MIDDLEBY_DEMO_WINDOW } from "./middleby-dataset-info";

const dataCache = new Map();

export function getSpecByChartRole(catalogId, chartRole = "primary") {
 return getMiddlebySpecsForCatalog(catalogId).find((s) => s.chartRole === chartRole) ?? null;
}

export function getDrillSpecs(catalogId, parentRole = "primary") {
 const parent = getSpecByChartRole(catalogId, parentRole);
 if (!parent?.drillInto) return [];
 return getMiddlebySpecsForCatalog(catalogId).filter(
 (s) => s.parentChartRole === parentRole && s.chartRole === parent.drillInto
 );
}

export async function loadMiddlebyDataset(filePath) {
 if (!filePath) return { blocked: false, rows: [] };
 if (dataCache.has(filePath)) return dataCache.get(filePath);

 const res = await fetch(filePath);
 const json = await res.json();
 let out;
 if (json && typeof json === "object" && !Array.isArray(json) && json.blocked) {
 out = { blocked: true, message: String(json.message || "Data not available for this view."), rows: [] };
 } else {
 out = { blocked: false, rows: Array.isArray(json) ? json : [] };
 }
 dataCache.set(filePath, out);
 return out;
}

export function parseNum(value) {
 if (value == null || value === "") return null;
 const cleaned = String(value).replace(/,/g, "").replace(/[^\d.-]/g, "");
 if (cleaned === "" || cleaned === "-" || cleaned === ".") return null;
 const n = Number(cleaned);
 return Number.isFinite(n) ? n : null;
}

const BRAND_COLUMN_DENY =
  /multi_brand|new_to_brand|repeat_brand|brand_session|brands?_visited|brand_count/i;

/** True for columns that hold a brand name (not boolean multi-brand flags). */
export function isBrandColumnKey(col) {
  const c = String(col || "")
    .toLowerCase()
    .replace(/\s+/g, "_");
  if (BRAND_COLUMN_DENY.test(c)) return false;
  if (c === "brand" || c === "brand_name" || c === "brand_slug") return true;
  if (/^top_brand/.test(c) || /_brand_interest$/.test(c)) return true;
  return false;
}

export function isLikelyBrandNameValue(value) {
  const v = String(value ?? "").trim();
  if (!v || v === "-") return false;
  const lower = v.toLowerCase();
  if (lower === "yes" || lower === "no" || lower === "true" || lower === "false") return false;
  if (/^\d+$/.test(v)) return false;
  return true;
}

export function findBrandColumn(columns = []) {
  return columns.find((c) => isBrandColumnKey(c)) || null;
}

export function isSkuColumnKey(col) {
  const c = String(col || "")
    .toLowerCase()
    .replace(/\s+/g, "_");
  return c === "sku" || c === "sku_id" || c === "product_sku" || /^sku_/.test(c);
}

/** True when a cell value should stay literal text (not coerced through parseNum). */
export function isTextCellValue(value) {
 const s = String(value ?? "").trim();
 if (!s) return true;
 if (/[a-zA-Z]/.test(s)) return true;
 return false;
}

export function rowLabel(row, dimensionColumns) {
 if (!row || !dimensionColumns?.length) return "-";
 const parts = dimensionColumns.map((col) => row[col]).filter((v) => v != null && String(v).trim() !== "");
 return parts.length ? parts.join(" · ") : "-";
}

export function primaryMetricValue(row, metricColumns) {
 if (!metricColumns?.length) return null;
 for (const col of metricColumns) {
 const n = parseNum(row[col]);
 if (n != null) return { col, value: n, raw: row[col] };
 }
 const col = metricColumns[0];
 return row[col] != null ? { col, value: null, raw: row[col] } : null;
}

export function aggregateMetric(rows, metricCol) {
 let sum = 0;
 let count = 0;
 for (const row of rows) {
 const n = parseNum(row[metricCol]);
 if (n != null) {
 sum += n;
 count += 1;
 }
 }
 return count ? sum : null;
}

// Column names ending in _usd (or containing "dollar") represent USD dollar
// amounts — the formatter adds a $ prefix and compacts large values.
const USD_COLUMN_RE = /_usd$|dollar/i;

function formatColumnLabel(col) {
 return String(col || "")
 .replace(/_example/g, "")
 .replace(/_usd$/i, " ($)")
 .replace(/_/g, " ")
 .replace(/\b\w/g, (c) => c.toUpperCase());
}

function formatMetricValue(n, { column } = {}) {
 if (n == null || !Number.isFinite(n)) return "-";
 const col = String(column || "").toLowerCase();
 if (USD_COLUMN_RE.test(col)) {
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `$${Math.round(n / 1_000).toLocaleString()}K`;
  return `$${Math.round(n).toLocaleString()}`;
 }
 if (/score/i.test(col) || (n >= 0 && n <= 100 && col.includes("intent"))) {
 const r = Math.round(n * 10) / 10;
 return Number.isInteger(r) ? String(r) : r.toFixed(1);
 }
 if (Math.abs(n) >= 100) return Math.round(n).toLocaleString();
 if (Math.abs(n - Math.round(n)) < 0.05) return String(Math.round(n));
 const oneDecimal = Math.round(n * 10) / 10;
 return Number.isInteger(oneDecimal) ? String(oneDecimal) : oneDecimal.toFixed(1);
}

/** Format a table cell for analytics charts (scores as whole numbers, counts without float noise). */
export function formatCellValue(column, value) {
 if (value == null || value === "") return "-";
 if (isTextCellValue(value)) return String(value).trim();
 const n = parseNum(value);
 if (n == null) return String(value).trim();
 return formatMetricValue(n, { column });
}

const INTENT_HIGH_THRESHOLD = 70;

function isHighIntentRow(row, scoreCol) {
 const tier = String(row?.tier || "").trim().toLowerCase();
 if (tier === "high") return true;
 const score = parseNum(row?.[scoreCol]);
 return score != null && score >= INTENT_HIGH_THRESHOLD;
}

export function buildKpiChips(spec, rows) {
 if (!spec || !rows?.length) return [];
 const chips = [];
 const metrics = spec.metricColumns || [];

 if (spec.chartType === "ranked_table") {
 const scoreCol = metrics.find((c) => /score/i.test(c)) || metrics[0];
 const pagesCol = metrics.find((c) => /pages/i.test(c)) || "pages_viewed";
 const high = rows.filter((r) => isHighIntentRow(r, scoreCol));
 chips.push({ label: "High intent", value: String(high.length) });
 chips.push({ label: "Sessions in sample", value: String(rows.length) });
 if (high.length && rows[0]?.[pagesCol] != null) {
 const avgPages =
 high.reduce((sum, r) => sum + (parseNum(r[pagesCol]) ?? 0), 0) / high.length;
 chips.push({ label: "Avg pages (high)", value: formatMetricValue(avgPages, { column: pagesCol }) });
 }
 return chips;
 }

 if ((spec.chartType === "funnel" || spec.chartType === "journey_timeline") && metrics[0]) {
  const col = metrics[0];
  const first = parseNum(rows[0]?.[col]) ?? 0;
  const last = parseNum(rows[rows.length - 1]?.[col]) ?? 0;
  const isJourney = spec.chartType === "journey_timeline";
  const chips = [
    { label: isJourney ? "Entered" : "Top of funnel", value: formatMetricValue(first, { column: col }) },
    { label: isJourney ? "Reached quote" : "Completed", value: formatMetricValue(last, { column: col }) },
  ];
  if (first > 0) {
   chips.push({ label: "Conversion", value: `${Math.round((last / first) * 100)}%` });
  }
  return chips;
 }

 if (spec.chartType === "kpi_cards" || spec.chartType === "comparison_cards") {
 for (const row of rows.slice(0, 4)) {
 const label = row.metric || row.signal || row.source || "Metric";
 chips.push({ label: String(label), value: String(row.value ?? "-") });
 }
 return chips;
 }

 if (spec.chartType === "donut" && metrics[0]) {
 const total = aggregateMetric(rows, metrics[0]) ?? 0;
 const top = [...rows]
 .map((r) => ({ label: rowLabel(r, spec.dimensionColumns), value: parseNum(r[metrics[0]]) ?? 0 }))
 .sort((a, b) => b.value - a.value)[0];
 if (top) chips.push({ label: top.label, value: String(top.value) });
 chips.push({ label: "Total", value: String(total) });
 return chips.slice(0, 3);
 }

 if (metrics[0]) {
 const total = aggregateMetric(rows, metrics[0]);
 if (total != null) {
 const col = metrics[0];
 const isScoreCol = /score/i.test(col);
 if (!isScoreCol || spec.chartType === "donut" || spec.chartType === "bar") {
 chips.push({ label: formatColumnLabel(col), value: formatMetricValue(total) });
 }
 }
 }
 if (metrics[1]) {
 const total2 = aggregateMetric(rows, metrics[1]);
 if (total2 != null) {
 chips.push({ label: formatColumnLabel(metrics[1]), value: formatMetricValue(total2) });
 }
 }
 if (!chips.length && rows.length) {
 chips.push({ label: "Rows", value: String(rows.length) });
 }
 return chips.slice(0, 3);
}

export function buildInsightHeadline(entry, spec, rows) {
 const q = entry?.sampleQuestion || entry?.problemSignal || entry?.title || "Summary";
 if (!rows?.length) return `No sample rows matched for: ${q}`;
 if (spec?.chartType === "blocked_placeholder") {
 return "This view needs additional exports before we can chart site search behavior.";
 }

 const metrics = spec?.metricColumns || [];
 const dims = spec?.dimensionColumns || [];

 if ((spec?.chartType === "funnel" || spec?.chartType === "journey_timeline") && metrics[0]) {
 const first = parseNum(rows[0]?.[metrics[0]]);
 const last = parseNum(rows[rows.length - 1]?.[metrics[0]]);
 if (first != null && last != null && first > 0) {
 const pct = Math.round((last / first) * 100);
 const startLabel = String(rows[0]?.[dims[dims.length - 1]] || rows[0]?.[dims[0]] || "the first step").toLowerCase();
 const endLabel = String(rows[rows.length - 1]?.[dims[dims.length - 1]] || "the final step").toLowerCase();
 return `Only ${pct}% of sessions made it from ${startLabel} to ${endLabel} (${formatMetricValue(last)} of ${formatMetricValue(first)}). The biggest drop-off is worth investigating.`;
 }
 }

 if (spec?.chartType === "donut" && metrics[0]) {
 const sorted = [...rows]
 .map((r) => ({ label: rowLabel(r, dims), v: parseNum(r[metrics[0]]) ?? 0 }))
 .sort((a, b) => b.v - a.v);
 const total = sorted.reduce((s, x) => s + x.v, 0);
 if (total > 0 && sorted[0]) {
 const pct = Math.round((sorted[0].v / total) * 100);
 return `${sorted[0].label} accounts for ${pct}% of the sample (${sorted[0].v} of ${total} total).`;
 }
 }

 if (spec?.chartType === "dual_axis_bar" && dims[0]) {
 const ranked = [...rows]
 .map((r) => ({
 label: r[dims[0]],
 sessions: parseNum(r[metrics[0]]) ?? 0,
 quotes: parseNum(r[metrics[1]]) ?? 0,
 }))
 .sort((a, b) => b.sessions - a.sessions);
 const gap = ranked.find((r) => r.sessions > 500 && r.quotes <= 3);
 if (gap) {
 return `${gap.label} shows high browse volume (${gap.sessions} sessions) but only ${gap.quotes} quotes in the sample window.`;
 }
 }

 if (spec?.chartType === "priority_table") {
 const urgent = rows.filter((r) => String(r.timeframe || "").includes("0 - 1"));
 return `${urgent.length} quotes in the sample need near-term follow-up (0-1 month timeframe).`;
 }

 if (metrics[0]) {
 const top = [...rows]
 .map((r) => ({ label: rowLabel(r, dims), v: parseNum(r[metrics[0]]) ?? 0 }))
 .sort((a, b) => b.v - a.v)[0];
 if (top) return `Top signal: ${top.label} (${formatMetricValue(top.v, { column: metrics[0] })} ${formatColumnLabel(metrics[0]).toLowerCase()}).`;
 }

 return `Sample data loaded for ${rows.length} rows. ${q}`;
}

/**
 * Structured answer: summary plus all qualifying rows (threshold-based), not only the top one.
 * @returns {{ summary: string, items: Array<{ label: string, value: string, detail?: string, metricLabel?: string }> }}
 */
export function buildInsightAnswer(entry, spec, rows) {
 const q = entry?.sampleQuestion || entry?.problemSignal || entry?.title || "Summary";
 if (!rows?.length) {
 return { summary: `No sample rows matched for: ${q}`, items: [] };
 }
 if (spec?.chartType === "blocked_placeholder") {
 return {
 summary: "This view needs additional exports before we can chart site search behavior.",
 items: [],
 };
 }

 const metrics = spec?.metricColumns || [];
 const dims = spec?.dimensionColumns || [];

 if (spec?.chartType === "ranked_table") {
 const scoreCol = metrics.find((c) => /score/i.test(c)) || metrics[0];
 const ranked = [...rows]
 .map((r) => ({
 row: r,
 score: parseNum(r[scoreCol]),
 label: rowLabel(r, dims),
 tier: String(r.tier || "").trim(),
 }))
 .filter((r) => r.score != null)
 .sort((a, b) => (b.score ?? 0) - (a.score ?? 0));

 const qualifying = ranked.filter((r) => isHighIntentRow(r.row, scoreCol));
 const thresholdNote = `score ≥ ${INTENT_HIGH_THRESHOLD} or High tier`;
 const summary = qualifying.length
 ? `${qualifying.length} session${qualifying.length === 1 ? "" : "s"} crossed the high-intent threshold (${thresholdNote}) and ${qualifying.length === 1 ? "is" : "are"} strong quote candidates in the sample window.`
 : `No sessions in the sample crossed the high-intent threshold (${thresholdNote}).`;

 const items = qualifying.map((r) => ({
 label: r.label,
 value: formatMetricValue(r.score, { column: scoreCol }),
 metricLabel: formatColumnLabel(scoreCol),
 detail: r.tier ? `${r.tier} intent` : undefined,
 }));

 return { summary, items };
 }

 if (spec?.chartType === "priority_table") {
 const urgent = rows.filter((r) => {
 const tf = String(r.timeframe || "");
 return tf.includes("0 - 1") || tf.includes("0-1");
 });
 const summary = `${urgent.length} quote${urgent.length === 1 ? "" : "s"} in the sample need near-term dealer follow-up (0-1 month timeframe).`;
 const items = urgent.slice(0, 15).map((r) => ({
 label: String(r.deal_name || rowLabel(r, dims)),
 value: String(r.timeframe || "-"),
 metricLabel: "Timeframe",
 detail: [r.brand, r.stage].filter(Boolean).join(" · ") || undefined,
 }));
 return { summary, items };
 }

 if (spec?.chartType === "dual_axis_bar" && dims[0]) {
 const ranked = [...rows]
 .map((r) => ({
 label: r[dims[0]],
 sessions: parseNum(r[metrics[0]]) ?? 0,
 quotes: parseNum(r[metrics[1]]) ?? 0,
 }))
 .sort((a, b) => b.sessions - a.sessions);
 const gaps = ranked.filter((r) => r.sessions > 500 && r.quotes <= 3);
 if (gaps.length) {
 return {
 summary: `${gaps.length} brand${gaps.length === 1 ? "" : "s"} show high browse volume with three or fewer quotes in the sample window.`,
 items: gaps.map((g) => ({
 label: String(g.label),
 value: String(g.quotes),
 metricLabel: "Quotes",
 detail: `${g.sessions} GA sessions`,
 })),
 };
 }
 }

 return { summary: buildInsightHeadline(entry, spec, rows), items: [] };
}

export function buildRecommendedActions(entry, spec, rows) {
 const title = entry?.title || "this topic";
 const catalogId = entry?.id;
 const actions = [];

 if (catalogId === "middleby-uc-04" || catalogId === "middleby-uc-06") {
  const urgent = rows?.filter((r) => String(r.timeframe || "").includes("0 - 1")) || [];
  const top = urgent[0] || rows?.[0];
  const dealId = top?.deal_id;
  actions.push({
   text: "Open the top urgent quote in HubSpot to assign dealer follow-up.",
   action: {
    type: "link",
    label: "Open in HubSpot",
    href: dealId
     ? `https://app.hubspot.com/contacts/24144444/deal/${dealId}`
     : "https://app.hubspot.com/contacts/24144444/objects/0-3/views/all/list",
   },
  });
  actions.push({
   text: "Copy a dealer follow-up brief for the urgent worklist.",
   action: {
    type: "clipboard",
    label: "Copy follow-up brief",
    getText: () => {
     const lines = (urgent.length ? urgent : rows || []).slice(0, 10).map((r) => {
      const name = r.deal_name || r.brand || "Quote";
      return `- ${name} · ${r.timeframe || "n/a"} · ${r.rep_group || "unassigned"}`;
     });
     return `Middleby dealer follow-up (${title})\n\n${lines.join("\n")}\n\nSource: HubSpot, ${MIDDLEBY_DEMO_WINDOW}`;
    },
   },
  });
 }

 if (catalogId === "middleby-uc-02") {
  actions.push({
   text: "Open the drop-off pages in LogRocket to see why quote carts stall.",
   action: { type: "link", label: "Open in LogRocket", href: "https://app.logrocket.com/" },
  });
  actions.push({
   text: "Copy the funnel summary for the digital team.",
   action: {
    type: "clipboard",
    label: "Copy funnel summary",
    getText: () =>
     `Middleby RFQ funnel (${title})\n\n${(rows || [])
      .map((r) => `- ${r.step_label}: ${r.session_count_example} sessions`)
      .join("\n")}\n\nBiggest leaks: quote cart and quote form.\nSource: GA4 + HubSpot, ${MIDDLEBY_DEMO_WINDOW}.`,
   },
  });
 }
 if (catalogId === "middleby-uc-18") {
  const gaps = [...(rows || [])]
   .map((r) => ({ brand: r.brand, s: parseNum(r.ga_sessions) ?? 0, q: parseNum(r.hubspot_quotes) ?? 0 }))
   .sort((a, b) => b.s - b.q * 200 - (a.s - a.q * 200))
   .slice(0, 6);
  actions.push({
   text: "Copy a brand-gap nudge for the marketing and merchandising team.",
   action: {
    type: "clipboard",
    label: "Copy brand-gap nudge",
    getText: () =>
     `Middleby brands to investigate (high traffic, low quote capture)\n\n${gaps
      .map((g) => `- ${g.brand}: ${g.s} sessions, ${g.q} quotes`)
      .join("\n")}\n\nSource: GA4 + HubSpot, ${MIDDLEBY_DEMO_WINDOW}.`,
   },
  });
 }
 if (catalogId === "middleby-uc-08") {
  const top = [...(rows || [])]
   .map((r) => ({ e: r.error_type, s: parseNum(r.sessions_affected_example) ?? 0, owner: r.suggested_owner }))
   .sort((a, b) => b.s - a.s)
   .slice(0, 6);
  actions.push({
   text: "Open the top UX issue in LogRocket to review affected sessions.",
   action: { type: "link", label: "Open in LogRocket", href: "https://app.logrocket.com/" },
  });
  actions.push({
   text: "Copy an incident summary for the #commerce-incidents channel.",
   action: {
    type: "clipboard",
    label: "Copy incident summary",
    getText: () =>
     `Middleby UX issues affecting quotes\n\n${top
      .map((t) => `- ${t.e}: ${t.s} sessions affected (owner: ${t.owner || "Digital"})`)
      .join("\n")}\n\nSource: LogRocket, ${MIDDLEBY_DEMO_WINDOW}.`,
   },
  });
 }
 if (catalogId === "middleby-uc-16") {
  actions.push({
   text: "Copy the Friday executive briefing to send to leadership.",
   action: {
    type: "clipboard",
    label: "Copy weekly briefing",
    getText: () =>
     `Middleby weekly briefing (${MIDDLEBY_DEMO_WINDOW})\n\n${(rows || [])
      .map((r) => `- ${String(r.direction || "flat").toUpperCase()} ${r.signal}: ${r.detail || ""}`)
      .join("\n")}`,
   },
  });
 }
 if (catalogId === "middleby-uc-05") {
  actions.push({
   text: "Copy the customer journey summary.",
   action: {
    type: "clipboard",
    label: "Copy journey summary",
    getText: () =>
     `Middleby customer journey (${title})\n\n${(rows || [])
      .map((r) => `${r.step_order}. ${r.step_label}: ${r.session_count_example} sessions`)
      .join("\n")}\n\nSource: GA4 + LogRocket + HubSpot + Salesforce, ${MIDDLEBY_DEMO_WINDOW}.`,
   },
  });
 }

 if (catalogId === "middleby-ns-01") {
  actions.push({
   text: "Copy the ranked recovery plan for the digital and merchandising leads.",
   action: {
    type: "clipboard",
    label: "Copy recovery plan",
    getText: () =>
     `Middleby revenue recovery plan (ranked by recoverable pipeline)\n\n${(rows || [])
      .map((r) => `- ${r.fix}: $${Number(parseNum(r.recoverable_pipeline_usd) || 0).toLocaleString()} (${r.effort} effort, ${r.owner})`)
      .join("\n")}\n\nSource: LogRocket + GA4 + HubSpot, ${MIDDLEBY_DEMO_WINDOW}.`,
   },
  });
 }
 if (catalogId === "middleby-ns-02") {
  actions.push({
   text: "Copy today's prioritized account worklist for the sales team.",
   action: {
    type: "clipboard",
    label: "Copy worklist",
    getText: () =>
     `Middleby next best actions (${(rows || []).length} accounts)\n\n${(rows || [])
      .slice(0, 10)
      .map((r) => `- ${r.account} (${r.brand}) [${r.opportunity_score}] ${r.recommended_action}`)
      .join("\n")}\n\nSource: GA4 + HubSpot + LogRocket + Salesforce, ${MIDDLEBY_DEMO_WINDOW}.`,
   },
  });
 }
 if (catalogId === "middleby-ns-03") {
  actions.push({
   text: "Copy the brand momentum summary for the weekly stand-up.",
   action: {
    type: "clipboard",
    label: "Copy momentum summary",
    getText: () =>
     `Middleby brand momentum (${MIDDLEBY_DEMO_WINDOW})\n\n${(rows || [])
      .map((r) => `- ${r.brand}: ${r.wow_change} (${r.direction})`)
      .join("\n")}`,
   },
  });
 }

 actions.push({
  text: `Review the evidence chart and validate the pattern with your team for ${title}.`,
 });

 if (spec?.drillInto) {
  actions.push({
   text: 'Use the breakdown tabs to open the next chart layer for this use case.',
  });
 }
 if (spec?.chartType === "priority_table") {
  actions.push({
   text: "Sort the worklist by timeframe and consultation flag, then assign dealer follow-up.",
  });
 }
 if (spec?.chartType === "ranked_table") {
  actions.push({
   text: "Route high-intent sessions to dealer outreach before lower-tier browse-only traffic.",
  });
 }
 if (spec?.chartType === "funnel") {
  actions.push({
   text: "Inspect the largest funnel drop-off step and pair with error impact data.",
  });
 }
 if (rows?.length > 10 && actions.length < 5) {
  actions.push({
   text: "Export the underlying sample slice for a deeper workshop with digital and sales ops.",
  });
 }
 return actions.slice(0, 4);
}

// Chart types whose values are time-series, formatted, or textual, so a period
// multiplier does not apply.
const PERIOD_NON_SCALING_CHARTS = new Set([
  "line",
  "stacked_area",
  "deploy_line",
  "kpi_cards",
  "comparison_cards",
  "alert_list",
]);

// Metric columns that are rates/scores/indices, not volumes, so they must not
// be multiplied when the period changes.
const RATE_LIKE_COLUMN = /score|index|rate|share|percent|pct|ratio|avg|average|momentum|timeframe|consultation|direction|effort|owner|note/i;

/**
 * Scale count-type metric values by a period factor so a use case answers for
 * the selected window (week x1, month x~4.3, etc.). Bounded to the spec's
 * metric columns; rates, scores, currency-formatted, and textual values are
 * left untouched, as are time-series and KPI-card charts.
 */
export function scaleMiddlebyRowsForPeriod(rows, spec, factor) {
  if (!Array.isArray(rows) || !rows.length) return rows || [];
  if (!factor || factor === 1) return rows;
  if (PERIOD_NON_SCALING_CHARTS.has(spec?.chartType)) return rows;
  const metricCols = (spec?.metricColumns || []).filter((c) => !RATE_LIKE_COLUMN.test(String(c)));
  if (!metricCols.length) return rows;
  return rows.map((row) => {
    const next = { ...row };
    for (const col of metricCols) {
      const raw = row[col];
      if (raw == null) continue;
      const s = String(raw).trim();
      // Only scale pure integers/decimals (commas allowed); skip $, %, letters.
      if (!/^-?[\d,]+(\.\d+)?$/.test(s)) continue;
      const n = Number(s.replace(/,/g, ""));
      if (!Number.isFinite(n)) continue;
      next[col] = String(Math.round(n * factor));
    }
    return next;
  });
}

export function allMiddlebyCatalogIds() {
 const ids = new Set();
 for (const s of MIDDLEBY_SCREEN_DATA_SPEC) {
 if (s.chartRole === "primary") ids.add(s.catalogId);
 }
 return [...ids];
}

export { getMiddlebyPrimarySpec, getMiddlebySpecsForCatalog };
