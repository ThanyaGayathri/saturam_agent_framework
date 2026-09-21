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
  comparison: suggestion ? (suggestion.comparison ?? null) : (spec.comparison ?? null),
  schedule: suggestion?.schedule || spec.schedule,
  window: suggestion?.window ?? spec.window ?? null,
  notify: (suggestion?.recipients || []).join(", ") || spec.notify,
  // Both kinds produce a report and a dashboard (decided 18 Sep); the diagnostic's differ in shape.
  outputs: ["report", "dashboard"],
  // A title the person typed stays; one an earlier suggestion proposed follows the new ask.
  title: spec.origin?.title === "authored" && spec.title ? spec.title : suggestion?.title || spec.title || "",
  analytics: analyticsFromSuggestion(suggestion),
 };
 // A diagnostic ask is the diagnostic kind (K25): one KPI, the change decomposed. The
 // template is set here so the form switches its fields; the person can switch it back.
 if (suggestion?.kind === "diagnostic") {
  patch.template = "metric_diagnosis";
  patch.metricIds = ids.slice(0, 1).length ? ids.slice(0, 1) : spec.metricIds.slice(0, 1);
  patch.dimensions = [];
 } else if (suggestion?.kind === "at_risk") {
  // The at-risk kind (K26): the context KPIs, no comparison, no breakdowns; the churn
  // window and the outcome keep their defaults unless the words named them.
  patch.template = "at_risk";
  patch.comparison = null;
  patch.dimensions = [];
  const days = (suggestion.title || "").match(/(\d{2,3})\s*days?/);
  if (days) patch.churnWindow = Number(days[1]);
  patch.outcome = /\balert(s)?\b/i.test(suggestion.title || "") && !/recommend/i.test(suggestion.title || "") ? "alert" : /recommend/i.test(suggestion.title || "") && !/\balert/i.test(suggestion.title || "") ? "recommend" : "both";
 } else if (suggestion) {
  patch.template = null;
 }
 const understood = [];
 if (suggestion?.kind === "at_risk") understood.push(`who or what is at risk: every open quote scored by a model trained on your own history, each at-risk one given a play from the catalogue, ${{ recommend: "recommendations", alert: "alerts", both: "recommendations and alerts" }[patch.outcome]} handed to Decision Intelligence; ${ids.length} context KPI${ids.length === 1 ? "" : "s"} beside the list`);
 else if (suggestion?.kind === "diagnostic") understood.push(`a diagnosis of ${patch.metricIds[0] || "one KPI"}: the change decomposed across every dimension the data carries`);
 else if (ids.length) understood.push(`${ids.length} KPI${ids.length === 1 ? "" : "s"} the data supports: ${ids.slice(0, 4).join(", ")}${ids.length > 4 ? "…" : ""}`);
 if (patch.dimensions?.length) understood.push(`broken down by ${patch.dimensions.map((d) => d.replace(/_/g, " ")).join(", ")}`);
 if (suggestion?.comparison_reason) understood.push(suggestion.comparison ? `compared with ${suggestion.comparison === "prior_year" ? "the same period last year" : "the period before"} - ${suggestion.comparison_reason}` : `not compared: ${suggestion.comparison_reason}`);
 if (suggestion?.schedule_reason) understood.push(`${suggestion.schedule} - ${suggestion.schedule_reason}`);
 if (suggestion?.window) understood.push(`the numbers cover the last ${suggestion.window} day${suggestion.window === 1 ? "" : "s"}`);

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
  compared: !!comparison.comparison && comparison.comparison !== "none",
  computedNow: comparison.computed_now || [],
  kpis: (comparison.kpis || []).map((k) => ({ id: k.identifier, name: k.name, unit: k.unit, grain: k.grain, current: k.current, baseline: k.baseline, delta: k.delta, deltaPct: k.delta_pct, rows: k.rows?.length || 0, reason: k.reason })),
  backfilled: comparison.backfilled || [],
  explanation: result.explanation || null,
  rejected: result.explanation_rejected || null,
  grounding: result.grounding || null,
  delivery: result.delivery || null,
  inApp: result.in_app || null,
  // The diagnostic kind (K25): the decomposition the sentence was written from.
  diagnosis: result.diagnosis || null,
  // The at-risk kind (K26): the scoring, the plays and the alerts.
  risk: result.risk || null,
  recommendations: result.recommendation_result || null,
  alerts: result.alerts || null,
 };
}

/**
 * Per-field suggestions from the platform's answer, in the shape the form's Field renders
 * (`{lead, options: [{label, why, patch}], preferred}`): the person sees the reason on the
 * field itself and clicks Use, or edits. Nothing is applied until they do.
 */
export function analyticsSuggestionFor(field, spec) {
 const a = spec?.analytics;
 if (!a || !(a.kpis || []).length) return null;
 const ids = a.kpis.map((k) => k.identifier);
 switch (field) {
  case "metrics": case "metric": {
   const missing = ids.filter((id) => !(spec.metricIds || []).includes(id));
   if (!missing.length) return null;
   const names = a.kpis.filter((k) => missing.includes(k.identifier)).map((k) => k.name);
   const label = `${missing.length} KPI${missing.length === 1 ? "" : "s"} the data supports`;
   return {
    show: true,
    lead: `From your goal and the registry: ${names.slice(0, 5).join(", ")}${names.length > 5 ? ` and ${names.length - 5} more` : ""}.`,
    options: [{ label, why: `${names.slice(0, 3).join(", ")}${names.length > 3 ? ", …" : ""}, each chosen by words in your goal`, patch: { metricIds: [...new Set([...(spec.metricIds || []), ...missing])], metricTerms: [] } }],
    preferred: label,
   };
  }
  case "dimensions": {
   const dims = (a.dimensionsSuggested || []).filter((d) => !(spec.dimensions || []).includes(d));
   if (!dims.length) return null;
   const label = dims.map((d) => d.replace(/_/g, " ")).join(", ");
   return { show: true, lead: null, options: [{ label, why: "the breakdowns the suggested KPIs offer", patch: { dimensions: [...new Set([...(spec.dimensions || []), ...dims])] } }], preferred: label };
  }
  case "comparison": {
   if (spec.comparison === a.comparison) return null;
   const label = a.comparison === "prior_year" ? "The same period last year" : "The period before";
   return { show: true, lead: null, options: [{ label, why: a.comparisonReason || "", patch: { comparison: a.comparison } }], preferred: label };
  }
  case "outputs": {
   const wanted = (a.outputs || []).filter((o) => o !== "alert");
   if (!wanted.length || wanted.every((o) => (spec.outputs || []).includes(o))) return null;
   const label = wanted.join(" + ");
   return { show: true, lead: null, options: [{ label, why: a.kind === "diagnostic" ? "a question run once: the report alone" : "a standing report and a dashboard composed from the same KPIs", patch: { outputs: wanted } }], preferred: label };
  }
  case "recipient": {
   const to = (a.recipients || []).join(", ");
   if (!to || spec.notify === to) return null;
   return { show: true, lead: null, options: [{ label: to, why: "named in your goal", patch: { notify: to } }], preferred: to };
  }
  case "schedule": {
   if (!a.schedule || spec.schedule === a.schedule || !a.scheduleReason) return null;
   return { show: true, lead: null, options: [{ label: a.schedule, why: a.scheduleReason, patch: { schedule: a.schedule } }], preferred: a.schedule };
  }
  default:
   return null;
 }
}

/** The platform's answer kept beside the spec, applied to nothing: what the fields suggest from. */
export function analyticsFromSuggestion(suggestion) {
 return {
  kind: suggestion?.kind || "monitoring",
  kpis: suggestion?.kpis || [],
  dimensionsSuggested: suggestion?.dimensions || [],
  uncovered: suggestion?.uncovered || [],
  notes: suggestion?.notes || [],
  comparison: suggestion?.comparison || "prior_period",
  comparisonReason: suggestion?.comparison_reason || "",
  schedule: suggestion?.schedule || "",
  scheduleReason: suggestion?.schedule_reason || "",
  outputs: suggestion?.outputs || [],
  recipients: suggestion?.recipients || [],
  title: suggestion?.title || "",
  forGoal: suggestion?._goal || "",
 };
}
