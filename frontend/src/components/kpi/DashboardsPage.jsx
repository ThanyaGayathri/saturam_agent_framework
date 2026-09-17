import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { LayoutDashboard, Plus, RefreshCw, AlertTriangle } from "lucide-react";
import { buildDashboardWritePayload, labelForRefresh } from "../../storage/dashboards";
import { createDashboardApi, fetchDashboardList } from "../../services/dashboards-api";
import CreateDashboardModal from "./CreateDashboardModal";

/**
 * Dashboards the caller can see, newest first. `list_visible_to` (`dashboards/store.py`)
 * orders ascending by `created_at` (oldest first), so this sorts descending client-side
 * rather than rendering the API's order straight through. Real data, not `localStorage`
 * (D2, #233): a dashboard exists here only because the API has it.
 */
export default function DashboardsPage() {
 const navigate = useNavigate();
 const [state, setState] = useState({ status: "loading", dashboards: [], error: null });
 // Non-null opens the create-dashboard flow. This page's entry point always starts it fresh
 // (`rows={null}`, so the flow's own mode picker runs) - the discovery grid's own entry point
 // (`KpiDiscoveryPage.jsx`) is the one that skips straight to the manual builder with rows
 // already picked.
 const [showCreate, setShowCreate] = useState(false);

 const createDashboard = async ({ name, refresh, charts, businessAreaFilter }) => {
  const created = await createDashboardApi(
   buildDashboardWritePayload({ name, refresh, charts, businessAreaFilter }),
  );
  setShowCreate(false);
  navigate(`/kpis/dashboards/${created.id}`);
 };

 useEffect(() => {
  let cancelled = false;
  fetchDashboardList()
   .then((dashboards) => {
    if (cancelled) return;
    const newestFirst = [...dashboards].sort(
     (a, b) => new Date(b.created_at) - new Date(a.created_at),
    );
    setState({ status: "ready", dashboards: newestFirst, error: null });
   })
   .catch((error) => {
    if (!cancelled) setState({ status: "error", dashboards: [], error });
   });
  return () => {
   cancelled = true;
  };
 }, []);

 const createButton = (
  <button
   type="button"
   onClick={() => setShowCreate(true)}
   className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-primary-hover"
  >
   <Plus size={15} />
   Create dashboard
  </button>
 );

 const modal = showCreate && (
  <CreateDashboardModal
   rows={null}
   onCancel={() => setShowCreate(false)}
   onCreate={createDashboard}
  />
 );

 if (state.status === "loading") {
  return (
   <>
    <p className="text-sm text-text-secondary">Loading dashboards…</p>
    {modal}
   </>
  );
 }

 if (state.status === "error") {
  return (
   <>
    <div className="flex flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-status-critical/40 bg-status-critical/5 px-6 py-16 text-center">
     <AlertTriangle size={22} className="text-status-critical" />
     <p className="text-sm font-semibold text-text-primary">Couldn't load your dashboards</p>
     {/* `ApiError.detail` is the FastAPI `detail` string alone (already formatted friendly by
        `formatErrorDetail` in `lib/api.js`); `.message` is the raw `"{status} {path}: {detail}"`
        form meant for logs/dev tools, not a user-facing subtext. */}
     <p className="max-w-sm text-xs text-text-muted">
      {state.error?.detail || "The dashboard service didn't respond."}
     </p>
    </div>
    {modal}
   </>
  );
 }

 const { dashboards } = state;

 if (dashboards.length === 0) {
  return (
   <>
    <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-border-light bg-surface-secondary px-6 py-20 text-center">
     <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl border border-border bg-surface">
      <LayoutDashboard size={24} className="text-text-secondary" />
     </div>
     <h3 className="text-lg font-bold text-text-primary">No dashboards yet</h3>
     {/* The empty state routes to the thing that fills it, rather than describing the problem. */}
     <p className="mt-1.5 max-w-sm text-sm text-text-muted">
      Pick the metrics your data supports, then build a dashboard from them in one step.
     </p>
     <div className="mt-6">{createButton}</div>
     <button
      type="button"
      onClick={() => navigate("/kpis/discover")}
      className="mt-3 text-xs font-semibold text-text-secondary hover:text-text-primary"
     >
      or see what my data supports first
     </button>
    </div>
    {modal}
   </>
  );
 }

 return (
  <>
   <div className="mb-4 flex justify-end">{createButton}</div>
   <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
    {dashboards.map((d) => (
     <button
      key={d.id}
      type="button"
      onClick={() => navigate(`/kpis/dashboards/${d.id}`)}
      className="flex flex-col gap-2.5 rounded-xl border border-border bg-surface p-4 text-left transition-shadow hover:shadow-md"
     >
      <h3 className="font-bold text-text-primary">{d.name}</h3>
      <p className="text-xs text-text-secondary">
       {d.tiles.length} metric{d.tiles.length === 1 ? "" : "s"}
      </p>
      <div className="mt-auto flex flex-wrap gap-1.5 pt-1">
       <span className="inline-flex items-center gap-1 rounded-full bg-surface-tertiary px-2 py-0.5 text-[11px] font-medium text-text-secondary">
        <RefreshCw size={10} />
        {labelForRefresh(d.refresh_schedule)}
       </span>
      </div>
     </button>
    ))}
   </div>
   {modal}
  </>
 );
}
