import { useState } from "react";
import { CalendarClock, RefreshCw, Send } from "lucide-react";
import Modal from "../ui/Modal";
import { REFRESH_OPTIONS, DELIVERY_OPTIONS } from "../../storage/dashboards";

/**
 * Creation asks three things and no more: a name, how often the numbers refresh, and whether it
 * gets sent to anyone.
 *
 * Deliberately NOT here: a chart type per metric. With eight metrics selected that is eight
 * decisions before the user has seen a single number, and they are decisions best made while
 * looking at the result. Each tile gets a sensible default from its unit and can be changed on
 * the dashboard itself.
 *
 * Refresh and delivery are two questions, not one. How fresh the data is and who receives it are
 * owned by different people and change independently.
 */
export default function CreateDashboardModal({ rows, onCancel, onCreate }) {
 const [name, setName] = useState("");
 const [refresh, setRefresh] = useState("daily");
 const [delivery, setDelivery] = useState("none");

 const submit = (e) => {
  e.preventDefault();
  onCreate({ name, refresh, delivery });
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
      {rows.length} metric{rows.length === 1 ? "" : "s"}
     </p>
     <div className="mt-2 flex flex-wrap gap-1.5">
      {rows.map((r) => (
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

    <div>
     <label htmlFor="db-delivery" className="flex items-center gap-1.5 text-sm font-semibold text-text-primary">
      <Send size={14} className="text-text-muted" />
      Send it to people
     </label>
     <select
      id="db-delivery"
      value={delivery}
      onChange={(e) => setDelivery(e.target.value)}
      className="mt-1.5 w-full rounded-lg border border-border bg-surface-default px-3 py-2 text-sm text-text-primary focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
     >
      {DELIVERY_OPTIONS.map((o) => (
       <option key={o.value} value={o.value}>{o.label}</option>
      ))}
     </select>
     <p className="mt-1.5 text-xs text-text-muted">
      Separate from refresh on purpose — a dashboard can update hourly and still only be emailed on Mondays.
     </p>
    </div>

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
      className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-primary-hover"
     >
      <CalendarClock size={15} />
      Create dashboard
     </button>
    </div>
   </form>
  </Modal>
 );
}
