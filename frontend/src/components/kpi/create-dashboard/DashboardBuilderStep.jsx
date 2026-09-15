import { AlertTriangle, ArrowLeft, CalendarClock, Sparkles, X } from "lucide-react";
import MetricPicker from "./MetricPicker";

const AI_BADGE = (
 <span className="inline-flex items-center gap-1 rounded-full bg-primary-light px-2 py-0.5 text-[11px] font-semibold text-primary">
  <Sparkles size={9} />
  suggested
 </span>
);

/**
 * The last step for both paths: name, metrics, one optional `business_area` filter, schedule,
 * then Create. `aiSuggested` only changes the chrome (badges, the back-to-purpose link) - the
 * fields underneath are the same editable state either way, which is the point: an AI
 * suggestion is a prefilled starting point, not a different shape of form.
 */
export default function DashboardBuilderStep({
 name,
 setName,
 refresh,
 setRefresh,
 refreshOptions,
 metrics,
 setMetrics,
 businessAreaFilter,
 setBusinessAreaFilter,
 businessAreaOptions,
 readyKpis,
 aiSuggested,
 onBack,
 onChangeMode,
 onCancel,
 onSubmit,
 submitting,
 error,
 notReadyCount = 0,
}) {
 const selectedIdentifiers = new Set(metrics.map((m) => m.identifier));
 const canSubmit = metrics.length > 0 && !submitting;

 const removeMetric = (identifier) => {
  setMetrics(metrics.filter((m) => m.identifier !== identifier));
 };
 const addMetric = (kpi) => {
  setMetrics([...metrics, kpi]);
 };

 return (
  <form
   onSubmit={(e) => {
    e.preventDefault();
    if (canSubmit) onSubmit();
   }}
  >
   <div className="mb-4 flex items-center justify-between">
    {aiSuggested ? (
     <button
      type="button"
      onClick={onBack}
      className="inline-flex items-center gap-1 text-xs font-semibold text-primary hover:text-primary-hover"
     >
      <ArrowLeft size={13} />
      Back to purpose (regenerate)
     </button>
    ) : onChangeMode ? (
     <button
      type="button"
      onClick={onChangeMode}
      className="inline-flex items-center gap-1 text-xs font-semibold text-primary hover:text-primary-hover"
     >
      <ArrowLeft size={13} />
      Change mode
     </button>
    ) : (
     <span />
    )}
    {aiSuggested && (
     <span className="inline-flex items-center gap-1.5 rounded-full bg-primary-light px-2.5 py-1 text-xs font-semibold text-primary">
      <Sparkles size={11} />
      AI generated — edit anything
     </span>
    )}
   </div>

   <div className="mb-5">
    <label htmlFor="db-name" className="block text-sm font-semibold text-text-primary">
     Name
    </label>
    <input
     id="db-name"
     value={name}
     onChange={(e) => setName(e.target.value)}
     placeholder="e.g. Inventory health — weekly"
     className="mt-1.5 w-full rounded-lg border border-border bg-surface-default px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
    />
   </div>

   <div className="mb-5">
    <p className="mb-2 flex items-center gap-2 text-sm font-semibold text-text-primary">
     {metrics.length} metric{metrics.length === 1 ? "" : "s"}
     {aiSuggested && metrics.length > 0 && AI_BADGE}
    </p>
    <div className="flex flex-wrap items-center gap-2">
     {metrics.map((kpi) => (
      <span
       key={kpi.identifier}
       className="inline-flex items-center gap-1.5 rounded-full bg-surface-tertiary py-1 pl-3 pr-1.5 text-xs font-medium text-text-secondary"
      >
       {kpi.name}
       <button
        type="button"
        onClick={() => removeMetric(kpi.identifier)}
        aria-label={`Remove ${kpi.name}`}
        className="rounded-full p-0.5 text-text-muted hover:bg-surface-secondary hover:text-text-primary"
       >
        <X size={11} />
       </button>
      </span>
     ))}
     <MetricPicker
      readyKpis={readyKpis}
      selectedIdentifiers={selectedIdentifiers}
      onAdd={addMetric}
     />
    </div>
    <p className="mt-2 text-xs text-text-muted">
     Each gets a chart type from its unit. You can change any of them on the dashboard.
    </p>
   </div>

   {notReadyCount > 0 && (
    <div className="mb-5 flex items-start gap-2 rounded-lg border border-status-warning/30 bg-status-warning/10 px-3 py-2.5 text-xs text-text-secondary">
     <AlertTriangle size={15} className="mt-0.5 shrink-0 text-status-warning" />
     <p>
      {notReadyCount} metric{notReadyCount === 1 ? " isn't" : "s aren't"} linked to a
      registered KPI yet, so {notReadyCount === 1 ? "it" : "they"} can't be added to a live
      dashboard and {notReadyCount === 1 ? "was" : "were"} left out of the selection below.
     </p>
    </div>
   )}

   <div className="mb-5">
    <p className="mb-2 flex items-center gap-2 text-sm font-semibold text-text-primary">
     Filter
     {aiSuggested && businessAreaFilter && AI_BADGE}
    </p>
    <select
     value={businessAreaFilter || ""}
     onChange={(e) => setBusinessAreaFilter(e.target.value || null)}
     className="w-full rounded-lg border border-border bg-surface-default px-3 py-2 text-sm text-text-primary focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
    >
     <option value="">No filter</option>
     {businessAreaOptions.map((area) => (
      <option key={area} value={area}>
       Business area = {area}
      </option>
     ))}
    </select>
   </div>

   {metrics.length === 0 && (
    <div className="mb-5 flex items-start gap-2 rounded-lg border border-status-warning/30 bg-status-warning/10 px-3 py-2.5 text-xs text-text-secondary">
     <AlertTriangle size={15} className="mt-0.5 shrink-0 text-status-warning" />
     <p>Add at least one metric to create the dashboard.</p>
    </div>
   )}

   <div className="mb-0">
    <label htmlFor="db-refresh" className="flex items-center gap-1.5 text-sm font-semibold text-text-primary">
     Refresh the numbers
    </label>
    <select
     id="db-refresh"
     value={refresh}
     onChange={(e) => setRefresh(e.target.value)}
     className="mt-1.5 w-full rounded-lg border border-border bg-surface-default px-3 py-2 text-sm text-text-primary focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
    >
     {refreshOptions.map((o) => (
      <option key={o.value} value={o.value}>
       {o.label}
      </option>
     ))}
    </select>
   </div>

   {error && (
    <p className="mt-5 rounded-lg border border-status-critical/30 bg-status-critical/10 px-3 py-2 text-xs font-medium text-status-critical">
     {error}
    </p>
   )}

   <div className="mt-5 flex items-center justify-end gap-2 border-t border-border pt-4">
    <button
     type="button"
     onClick={onCancel}
     className="rounded-lg px-3.5 py-2 text-sm font-semibold text-text-secondary hover:text-text-primary"
    >
     Cancel
    </button>
    <button
     type="submit"
     disabled={!canSubmit}
     className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-50"
    >
     <CalendarClock size={15} />
     {submitting ? "Creating…" : "Create dashboard"}
    </button>
   </div>
  </form>
 );
}
