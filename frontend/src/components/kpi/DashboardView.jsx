import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { AlertTriangle, ArrowLeft, Pencil, RefreshCw, Sparkles, Trash2 } from "lucide-react";
import Modal from "../ui/Modal";
import ChartTile from "./ChartTile";
import UseCaseLatestReport from "../usecase/UseCaseLatestReport";
import ChartEditorPanel from "./create-dashboard/ChartEditorPanel";
import { REFRESH_OPTIONS, labelForRefresh, spanForChart, tileWriteFromChart } from "../../storage/dashboards";
import {
 addDashboardTile,
 deleteDashboardApi,
 deleteDashboardTile,
 fetchDashboard,
 fetchDashboardData,
 refreshDashboardApi,
 updateDashboardApi,
 updateDashboardTile,
} from "../../services/dashboards-api";

/**
 * A saved dashboard, wired to the real `dashboards.py` (D2 #233; composed charts, 1.2.0):
 * `GET /dashboards/{id}` for the tiles, `GET /dashboards/{id}/data` for their values -- `kpi`
 * tiles from gold, `query` tiles from their cached result. Every number here is computed
 * against a stored definition or a guarded, cached query -- there is no model call at render
 * time, and there must never be one.
 *
 * Each tile can be edited (the same chart editor the create flow uses) or deleted in place,
 * with stable ids so one edit never remounts the others; its description is editable inline.
 * A prompt bar at the bottom adds a chart by description (runs the composer scoped to this
 * dashboard) or opens the editor blank on an empty Enter.
 */
export default function DashboardView() {
 const { dashboardId } = useParams();
 const navigate = useNavigate();
 const [dashboardState, setDashboardState] = useState({ status: "loading", dashboard: null, error: null });
 const [dataState, setDataState] = useState({ status: "loading", byTileId: {}, error: null });
 const [actionError, setActionError] = useState(null);
 const [busy, setBusy] = useState(false);
 const [editingTileId, setEditingTileId] = useState(null);
 const [addPrompt, setAddPrompt] = useState("");
 // Holds the draft chart object once the person hits Enter on the add-chart bar, `null`
 // otherwise. Never POSTed directly (round-1 review, finding #4: this used to build a
 // `TileWrite` straight from the raw prompt text -- with an empty `sql` and no `position`,
 // guaranteed to 422 both client- and server-side) -- it only opens `ChartEditorPanel` on a
 // local draft, exactly like the empty-Enter case already did; `onSave` there is the one
 // place that ever calls `addDashboardTile`, once the person has written and (for a `query`
 // chart) previewed real SQL.
 const [addingChart, setAddingChart] = useState(null);
 // The refresh-schedule cadence picked but not yet confirmed -- `null` means the select just
 // shows `dashboard.refresh_schedule`. Kept separate from `dashboard` so Cancel is a no-op,
 // not a second write to undo the first (reapplies #272's editable-schedule control, silently
 // reverted to read-only text by this PR per round-1 review's issue-level comment).
 const [pendingSchedule, setPendingSchedule] = useState(null);

 // `dashboardId` this component is currently showing, readable inside async callbacks that
 // closed over an older value -- lets `loadData` (and the dashboard fetch) tell a stale
 // response apart from the current one without a second `cancelled` flag of its own.
 const currentDashboardIdRef = useRef(dashboardId);
 currentDashboardIdRef.current = dashboardId;

 const loadData = useCallback((id) => {
  setDataState({ status: "loading", byTileId: {}, error: null });
  fetchDashboardData(id)
   .then((data) => {
    if (currentDashboardIdRef.current !== id) return;
    const byTileId = Object.fromEntries((data.tiles || []).map((t) => [t.tile_id, t]));
    setDataState({ status: "ready", byTileId, error: null });
   })
   .catch((error) => {
    if (currentDashboardIdRef.current !== id) return;
    setDataState({ status: "error", byTileId: {}, error });
   });
 }, []);

 useEffect(() => {
  let cancelled = false;
  setDashboardState({ status: "loading", dashboard: null, error: null });
  // A pending/in-flight edit belongs to the dashboard being left, not the one being opened --
  // without this, an unconfirmed schedule pick survives a back/forward navigation (no `key`
  // on this route, so the component isn't guaranteed to remount) and the confirm modal
  // reopens on the new dashboard showing the old one's leftover value.
  setPendingSchedule(null);
  setBusy(false);
  // Both requests only need `dashboardId`, so they fire together rather than sequentially
  // (`/data` used to wait for the dashboard fetch to resolve first, doubling open latency).
  fetchDashboard(dashboardId)
   .then((dashboard) => {
    if (cancelled) return;
    setDashboardState({ status: "ready", dashboard, error: null });
   })
   .catch((error) => {
    if (!cancelled) setDashboardState({ status: "error", dashboard: null, error });
   });
  loadData(dashboardId);
  return () => {
   cancelled = true;
  };
 }, [dashboardId, loadData]);

 if (dashboardState.status === "loading") {
  return <p className="text-sm text-text-secondary">Loading dashboard…</p>;
 }

 if (dashboardState.status === "error") {
  const notFound = dashboardState.error?.status === 404;
  return (
   <div className="rounded-xl border border-dashed border-border-light bg-surface-secondary px-6 py-16 text-center">
    <p className="text-sm text-text-secondary">
     {notFound
      ? "That dashboard no longer exists."
      : dashboardState.error?.message || "Couldn't load this dashboard."}
    </p>
    <button
     type="button"
     onClick={() => navigate("/kpis/dashboards")}
     className="mt-3 text-sm font-semibold text-primary hover:underline"
    >
     Back to dashboards
    </button>
   </div>
  );
 }

 const { dashboard } = dashboardState;
 const editingTile = editingTileId ? dashboard.tiles.find((t) => t.id === editingTileId) : null;

 const handleDelete = async () => {
  if (!window.confirm(`Delete "${dashboard.name}"?`)) return;
  setActionError(null);
  try {
   await deleteDashboardApi(dashboard.id);
   navigate("/kpis/dashboards");
  } catch (err) {
   setActionError(err?.message || "Could not delete the dashboard.");
  }
 };

 const handleRefresh = async () => {
  setActionError(null);
  setBusy(true);
  try {
   const data = await refreshDashboardApi(dashboard.id);
   const byTileId = Object.fromEntries((data.tiles || []).map((t) => [t.tile_id, t]));
   setDataState({ status: "ready", byTileId, error: null });
  } catch (err) {
   setActionError(err?.message || "Could not refresh this dashboard.");
  } finally {
   setBusy(false);
  }
 };

 // Picking a new schedule opens a confirmation modal rather than saving straight away (the
 // change affects every future refresh, not just this view) -- `cancelScheduleChange` and
 // `confirmScheduleChange` below are what it calls.
 const handleScheduleChange = (e) => {
  const next = e.target.value;
  // Always set, never conditionally skipped: re-selecting the dashboard's current value must
  // clear a stale `pendingSchedule` from an earlier, since-abandoned pick, not leave the
  // select/modal stuck showing it.
  setPendingSchedule(next === dashboard.refresh_schedule ? null : next);
 };

 const cancelScheduleChange = () => setPendingSchedule(null);

 const confirmScheduleChange = async () => {
  const refreshSchedule = pendingSchedule;
  // Staleness guard: a PATCH that resolves after the person has navigated to a different
  // dashboard must not apply its (now stale) result on top of that one's state.
  const dashboardIdAtCall = dashboard.id;
  setPendingSchedule(null);
  setActionError(null);
  setBusy(true);
  try {
   const updated = await updateDashboardApi(dashboard.id, {
    name: dashboard.name,
    refresh_schedule: refreshSchedule,
   });
   if (currentDashboardIdRef.current !== dashboardIdAtCall) return;
   setDashboardState({ status: "ready", dashboard: updated, error: null });
  } catch (err) {
   if (currentDashboardIdRef.current !== dashboardIdAtCall) return;
   setActionError(err?.message || "Could not change the schedule.");
  } finally {
   setBusy(false);
  }
 };

 const saveTileEdit = async (updated) => {
  setActionError(null);
  setBusy(true);
  try {
   const write = tileWriteFromChart(updated, 0);
   const saved = await updateDashboardTile(dashboard.id, editingTileId, {
    title: write.title,
    chart: write.chart,
    description: write.description,
    ...(write.query ? { query: write.query } : {}),
   });
   setDashboardState({ status: "ready", dashboard: saved, error: null });
   setEditingTileId(null);
   loadData(dashboard.id);
  } catch (err) {
   setActionError(err?.message || "Could not save that chart.");
  } finally {
   setBusy(false);
  }
 };

 const deleteTile = async (tileId) => {
  if (!window.confirm("Remove this chart?")) return;
  setActionError(null);
  setBusy(true);
  try {
   await deleteDashboardTile(dashboard.id, tileId);
   setDashboardState({
    status: "ready",
    dashboard: { ...dashboard, tiles: dashboard.tiles.filter((t) => t.id !== tileId) },
    error: null,
   });
   setEditingTileId(null);
  } catch (err) {
   setActionError(err?.message || "Could not remove that chart.");
  } finally {
   setBusy(false);
  }
 };

 const saveDescription = async (tile, description) => {
  try {
   const saved = await updateDashboardTile(dashboard.id, tile.id, { description });
   setDashboardState({ status: "ready", dashboard: saved, error: null });
  } catch (err) {
   setActionError(err?.message || "Could not save that description.");
  }
 };

 const addChartFromPrompt = () => {
  const text = addPrompt.trim();
  setAddPrompt("");
  // No `id` here (round-2 review, finding #1): `tileWriteFromChart` sends whatever `id` a
  // chart carries, and `_tile_from_write`'s `write.id or f"tl_{...}"` only mints a fresh one
  // when the client sent none -- a hardcoded placeholder here (this used to be `"__new__"`)
  // reused that exact literal id on every add-by-prompt call, so the *second* one on any
  // given dashboard collided with the first and 409'd permanently. Leaving `id` unset lets
  // the server mint a real one every time, same as the empty-Enter blank-chart path already
  // did (it never set one either).
  setAddingChart({
   title: text || "New chart",
   chart: "table",
   source: "query",
   query: { sql: "" },
   description: "",
  });
 };

 // The semantic layer (Neo4j) and warehouse dependencies behind `/data` currently surface as a
 // raw 500 rather than a graceful 503 (#233's own verified finding, flagged as a backend
 // follow-up, not fixed here) -- shown as one clear message either way, not a crash.
 const dataUnavailable = dataState.status === "error";

 return (
  <div className="space-y-5">
   <div className="flex flex-wrap items-start justify-between gap-3">
    <div>
     <button
      type="button"
      onClick={() => navigate("/kpis/dashboards")}
      className="mb-1 inline-flex items-center gap-1 text-xs font-semibold text-text-muted hover:text-text-primary"
     >
      <ArrowLeft size={13} />
      Dashboards
     </button>
     <h2 className="text-xl font-bold text-text-primary">{dashboard.name}</h2>
     <p className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-text-muted">
      <span className="inline-flex items-center gap-1">
       <RefreshCw size={11} />
       <select
        aria-label="Refresh schedule"
        value={pendingSchedule ?? dashboard.refresh_schedule}
        onChange={handleScheduleChange}
        disabled={busy}
        className="rounded-md border border-border bg-surface-secondary px-1.5 py-1 text-[11px] font-medium text-text-secondary focus:border-primary focus:outline-none disabled:opacity-50"
       >
        {REFRESH_OPTIONS.map((o) => (
         <option key={o.value} value={o.value}>{o.label}</option>
        ))}
       </select>
      </span>
      <span>{dashboard.tiles.length} chart{dashboard.tiles.length === 1 ? "" : "s"}</span>
     </p>
    </div>
    <div className="flex flex-wrap items-center gap-2">
     <button
      type="button"
      onClick={handleRefresh}
      disabled={busy}
      className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-surface px-3 py-2 text-xs font-semibold text-text-secondary hover:border-primary/40 hover:text-primary disabled:opacity-50"
     >
      <RefreshCw size={13} />
      Refresh now
     </button>
     <button
      type="button"
      onClick={handleDelete}
      className="rounded-lg border border-border bg-surface p-2 text-text-muted hover:border-status-critical hover:text-status-critical"
      aria-label="Delete dashboard"
     >
      <Trash2 size={15} />
     </button>
    </div>
   </div>

   {actionError && (
    <p className="rounded-lg border border-status-critical/30 bg-status-critical/10 px-3 py-2 text-xs font-medium text-status-critical">
     {actionError}
    </p>
   )}

   {dataUnavailable && (
    <div className="flex items-start gap-2 rounded-lg border border-status-critical/30 bg-status-critical/5 px-3 py-2.5 text-xs text-text-secondary">
     <AlertTriangle size={15} className="mt-0.5 shrink-0 text-status-critical" />
     <p>
      Couldn't reach the data layer for this dashboard's numbers (semantic layer or warehouse
      unavailable). Tile layout below is real; the values aren't loaded.
     </p>
    </div>
   )}

   {/* A dashboard a use case composed (K23/K25) opens on that use case's latest report: the
       sentence its last run wrote, above the tiles it refreshes. Nothing for a hand-built one. */}
   {dashboard.owner === "use_case" && <UseCaseLatestReport title={dashboard.name} />}

   <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
    {dashboard.tiles.map((tile, index) => (
     <ChartTile
      key={tile.id}
      tile={tile}
      hue={index}
      data={dataState.byTileId[tile.id]}
      className={spanForChart(tile.chart) === 2 ? "sm:col-span-2 xl:col-span-2" : ""}
      onDescriptionChange={(text) => saveDescription(tile, text)}
      actions={
       <>
        <button
         type="button"
         onClick={() => setEditingTileId(tile.id)}
         aria-label={`Edit ${tile.title}`}
         className="rounded-md p-1.5 text-text-muted hover:bg-surface-tertiary hover:text-text-primary"
        >
         <Pencil size={13} />
        </button>
        <button
         type="button"
         onClick={() => deleteTile(tile.id)}
         aria-label={`Delete ${tile.title}`}
         className="rounded-md p-1.5 text-text-muted hover:bg-status-critical/10 hover:text-status-critical"
        >
         <Trash2 size={13} />
        </button>
       </>
      }
     />
    ))}
   </div>

   <div className="flex items-center gap-2.5 rounded-lg border border-dashed border-border-light px-3.5 py-2.5">
    <span className="flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-full bg-primary-light text-primary">
     <Sparkles size={12} />
    </span>
    <input
     value={addPrompt}
     onChange={(e) => setAddPrompt(e.target.value)}
     onKeyDown={(e) => {
      if (e.key === "Enter") {
       e.preventDefault();
       addChartFromPrompt();
      }
     }}
     placeholder='Add a chart — describe it, e.g. "late deliveries by supplier, top 10"'
     className="min-w-0 flex-1 bg-transparent text-sm text-text-primary placeholder:text-text-muted focus:outline-none"
    />
    <kbd className="rounded border border-border-strong px-1.5 py-0.5 font-mono text-[11px] text-text-muted">⏎</kbd>
   </div>

   {/* Said once, plainly, rather than implied: the figures are computed, not generated. */}
   <p className="text-xs text-text-muted">
    Metric charts are computed against the definition in Configure; composed charts read
    through the warehouse read guard on this dashboard's refresh schedule. Nothing here runs a
    query on open.
   </p>

   {pendingSchedule && (
    <Modal title="Change refresh schedule?" onClose={cancelScheduleChange}>
     <p className="text-sm text-text-secondary">
      This dashboard will refresh{" "}
      <span className="font-semibold text-text-primary">{labelForRefresh(pendingSchedule)}</span>{" "}
      from now on.
     </p>
     <div className="mt-5 flex items-center justify-end gap-2 border-t border-border pt-4">
      <button
       type="button"
       onClick={cancelScheduleChange}
       className="rounded-lg px-3.5 py-2 text-sm font-semibold text-text-secondary hover:text-text-primary"
      >
       Cancel
      </button>
      <button
       type="button"
       onClick={confirmScheduleChange}
       className="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-primary-hover"
      >
       Save schedule
      </button>
     </div>
    </Modal>
   )}

   {editingTile && (
    <ChartEditorPanel
     chart={editingTile}
     onClose={() => setEditingTileId(null)}
     onSave={saveTileEdit}
     onDelete={() => deleteTile(editingTile.id)}
    />
   )}

   {addingChart && !editingTile && (
    <ChartEditorPanel
     chart={addingChart}
     onClose={() => setAddingChart(null)}
     onSave={async (chart) => {
      setBusy(true);
      try {
       const write = tileWriteFromChart(chart, dashboard.tiles.length);
       const saved = await addDashboardTile(dashboard.id, write);
       setDashboardState({ status: "ready", dashboard: saved, error: null });
       setAddingChart(null);
       loadData(dashboard.id);
      } catch (err) {
       setActionError(err?.message || "Could not add that chart.");
      } finally {
       setBusy(false);
      }
     }}
    />
   )}
  </div>
 );
}
