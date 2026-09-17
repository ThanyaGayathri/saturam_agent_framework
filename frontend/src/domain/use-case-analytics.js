/**
 * Analytics Automation on the form (K23): the platform's suggestion applied to the spec, the
 * outputs a report can have, and what an Inform run's result looks like to the screens.
 *
 * The suggestion (`POST /use-cases/analytics/suggest`) is the form's intelligence for this
 * category: it is drawn from the governed KPI registry and the gold the deployment holds, so
 * a KPI it names is one the data can report on. The spec keeps the registry identifiers as
 * `metricIds` and the suggestion beside them (`analytics`) so the form can say why each one
 * was chosen and what the ask said that nothing covers.
 */

export const REPORT_OUTPUTS = [
 { value: "report", label: "Report by email + in-app", hint: "The narrative and the numbers, every run, as an HTML email and in the app." },
 { value: "dashboard", label: "Dashboard", hint: "Composed from the KPIs at activation under KPIs → Dashboards; refreshed nightly." },
 { value: "alert", label: "Alert", hint: "Not in this release: choose 'Only when a KPI crosses a threshold' on the category card instead." },
];

/** The spec patch a platform suggestion produces, and the lines to say what was filled. */
export function patchFromSuggestion(suggestion, spec) {
 const ids = (suggestion?.kpis || []).map((k) => k.identifier).filter(Boolean);
 const patch = {
  metricIds: ids.length ? ids : spec.metricIds,
  metricTerms: [],
  dimensions: suggestion?.dimensions?.length ? suggestion.dimensions : spec.dimensions,
  comparison: suggestion?.comparison || spec.comparison || "prior_period",
  schedule: suggestion?.schedule || spec.schedule,
  notify: (suggestion?.recipients || []).join(", ") || spec.notify,
  outputs: suggestion?.outputs?.length ? suggestion.outputs.filter((o) => o !== "alert") : spec.outputs,
  title: spec.title || suggestion?.title || "",
  analytics: {
   kind: suggestion?.kind || "monitoring",
   kpis: suggestion?.kpis || [],
   uncovered: suggestion?.uncovered || [],
   notes: suggestion?.notes || [],
   comparisonReason: suggestion?.comparison_reason || "",
   scheduleReason: suggestion?.schedule_reason || "",
  },
 };
 const understood = [];
 if (ids.length) understood.push(`${ids.length} KPI${ids.length === 1 ? "" : "s"} the data supports: ${ids.slice(0, 4).join(", ")}${ids.length > 4 ? "…" : ""}`);
 if (patch.dimensions?.length) understood.push(`broken down by ${patch.dimensions.map((d) => d.replace(/_/g, " ")).join(", ")}`);
 if (suggestion?.comparison_reason) understood.push(`compared with ${suggestion.comparison === "prior_year" ? "the same period last year" : "the period before"} — ${suggestion.comparison_reason}`);
 if (suggestion?.schedule_reason) understood.push(`${suggestion.schedule} — ${suggestion.schedule_reason}`);
 if (patch.outputs?.length) understood.push(`outputs: ${patch.outputs.join(" + ")}`);
 if (suggestion?.recipients?.length) understood.push(`to ${suggestion.recipients.join(", ")}`);
 if (suggestion?.uncovered?.length) understood.push(`nothing registered covers: ${suggestion.uncovered.slice(0, 6).join(", ")}`);
 return { patch, understood };
}

/** Whether this spec is an Analytics Automation the platform runs (Inform over the registry). */
export const isAnalyticsInform = (spec) => spec?.category === "analytics_automation" && spec?.shape === "inform";

/** An Inform run's result, as the screens read it. */
export function reportResult(result) {
 if (!result || !result.report) return null;
 const comparison = result.comparison_result || {};
 return {
  title: result.report.title,
  headline: result.report.headline,
  html: result.report.html,
  asOf: comparison.as_of,
  comparisonLabel: comparison.comparison_label,
  kpis: (comparison.kpis || []).map((k) => ({ id: k.identifier, name: k.name, unit: k.unit, grain: k.grain, current: k.current, baseline: k.baseline, delta: k.delta, deltaPct: k.delta_pct, rows: k.rows?.length || 0, reason: k.reason })),
  backfilled: comparison.backfilled || [],
  explanation: result.explanation || null,
  rejected: result.explanation_rejected || null,
  grounding: result.grounding || null,
  delivery: result.delivery || null,
  inApp: result.in_app || null,
 };
}
