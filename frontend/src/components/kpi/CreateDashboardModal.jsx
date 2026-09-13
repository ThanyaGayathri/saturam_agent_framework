import { useState } from "react";
import { AlertTriangle, CalendarClock, RefreshCw } from "lucide-react";
import Modal from "../ui/Modal";
import { DEFAULT_REFRESH_SCHEDULE, REFRESH_OPTIONS, kpiIsWireReady } from "../../storage/dashboards";

/**
 * Creation asks two things and no more: a name, and how often the numbers refresh.
 *
 * Deliberately NOT here: a chart type per metric. With eight metrics selected that is eight
 * decisions before the user has seen a single number, and they are decisions best made while
 * looking at the result. Each tile gets a sensible default from its unit and can be changed on
 * the dashboard itself.
 *
 * Also not here any more: delivery (who a dashboard is emailed to, and when). D1 (#226) kept it
 * as a frontend-only field, noting the API had no counterpart yet. D2 (#233) wires dashboards
 * to the API for real, so there is no longer anywhere in this browser to keep a setting for a
 * resource that now lives server-side - flagged again here (`storage/dashboards.js`), not
 * silently dropped.
 *
 * A tile can only be created against a *registered* KPI - `TileWrite.kpi_identifier` is
 * required and non-null. The seed catalogue has no identifier on any card yet (#232 starts
 * supplying real ones), so `readyRows` below is almost always empty today. That is the honest
 * state of the product, not a bug in this modal: it says so, rather than fabricating an
 * identifier or silently creating an empty dashboard.
 */
export default function CreateDashboardModal({ rows, onCancel, onCreate }) {
 const [name, setName] = useState("");
 const [refresh, setRefresh] = useState(DEFAULT_REFRESH_SCHEDULE);
 const [submitting, setSubmitting] = useState(false);
 const [error, setError] = useState(null);

 const readyRows = rows.filter((r) => kpiIsWireReady(r.kpi));
 const notReadyCount = rows.length - readyRows.length;
 const canSubmit = readyRows.length > 0 && !submitting;

 const submit = async (e) => {
  e.preventDefault();
  if (!canSubmit) return;
  setSubmitting(true);
  setError(null);
  try {
   await onCreate({ name, refresh, readyRows });
  } catch (err) {
   setError(err?.message || "Could not create the dashboard.");
   setSubmitting(false);
  }
 };

 return (
  <Modal title="Create dashboard" onClose={onCancel}>
   <form onSubmit={submit} className="space-y-5">
    <div>
     <label htmlFor="db-name" className="block text-sm font-semibold text-text-primary">
      Name
     </label>
     <input
      id="db-name"
      autoFocus
      value={name}
      onChange={(e) => setName(e.target.value)}
      placeholder="e.g. Inventory health — weekly"
      className="mt-1.5 w-full rounded-lg border border-border bg-surface-default px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
     />
    </div>

    <div>
     <p className="text-sm font-semibold text-text-primary">
      {readyRows.length} metric{readyRows.length === 1 ? "" : "s"}
      {notReadyCount > 0 && <span className="font-normal text-text-muted"> of {rows.length} selected</span>}
     </p>
     <div className="mt-2 flex flex-wrap gap-1.5">
      {readyRows.map((r) => (
       <span
        key={r.kpi.name}
        className="rounded-full bg-surface-tertiary px-2.5 py-0.5 text-xs font-medium text-text-secondary"
       >
        {r.kpi.name}
       </span>
      ))}
     </div>
     <p className="mt-2 text-xs text-text-muted">
      Each gets a chart type from its unit. You can change any of them on the dashboard.
     </p>
    </div>

    {notReadyCount > 0 && (
     <div className="flex items-start gap-2 rounded-lg border border-status-warning/30 bg-status-warning/10 px-3 py-2.5 text-xs text-text-secondary">
      <AlertTriangle size={15} className="mt-0.5 shrink-0 text-status-warning" />
      <p>
       {notReadyCount} metric{notReadyCount === 1 ? " isn't" : "s aren't"} linked to a registered
       KPI yet, so {notReadyCount === 1 ? "it" : "they"} can't be added to a live dashboard and
       {notReadyCount === 1 ? " is" : " are"} left out.
       {readyRows.length === 0 && " None of the selected metrics can be added yet."}
      </p>
     </div>
    )}

    <div>
     <label htmlFor="db-refresh" className="flex items-center gap-1.5 text-sm font-semibold text-text-primary">
      <RefreshCw size={14} className="text-text-muted" />
      Refresh the numbers
     </label>
     <select
      id="db-refresh"
      value={refresh}
      onChange={(e) => setRefresh(e.target.value)}
      className="mt-1.5 w-full rounded-lg border border-border bg-surface-default px-3 py-2 text-sm text-text-primary focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
     >
      {REFRESH_OPTIONS.map((o) => (
       <option key={o.value} value={o.value}>{o.label}</option>
      ))}
     </select>
    </div>

    {error && (
     <p className="rounded-lg border border-status-critical/30 bg-status-critical/10 px-3 py-2 text-xs font-medium text-status-critical">
      {error}
     </p>
    )}

    <div className="flex items-center justify-end gap-2 border-t border-border pt-4">
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
  </Modal>
 );
}
