import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { AlertTriangle, ArrowLeft, Check, Filter, Pencil, RefreshCw, Sparkles, Trash2 } from "lucide-react";
import Modal from "../ui/Modal";
import ChartTile from "./ChartTile";
import UseCaseLatestReport from "../usecase/UseCaseLatestReport";
import ChartEditorPanel from "./create-dashboard/ChartEditorPanel";
import AiGeneratingStep from "./create-dashboard/AiGeneratingStep";
import DashboardFilterBar from "./DashboardFilterBar";
import { useDashboardSuggestionRun } from "../../hooks/useDashboardSuggestionRun";
import { REFRESH_OPTIONS, labelForRefresh, spanForChart, tileWriteFromChart } from "../../storage/dashboards";
import {
 addDashboardTile,
 deleteDashboardApi,
 deleteDashboardTile,
 fetchDashboard,
 fetchDashboardData,
 fetchDashboardFilterColumns,
 refreshDashboardApi,
 updateDashboardApi,
 updateDashboardTile,
} from "../../services/dashboards-api";

/** Every filter selection lives in the URL as one repeated `f.<table>.<column>=value` param
 * per selected value - per-viewer, shareable, bookmarkable view-time state (R13.1), same
 * justification `KpiDiscoveryPage.jsx` already documents for its own search params. Never a
 * comma-joined list: a distinct value can itself contain a comma (a real city, "Dallas, TX"),
 * the same reason `fetchDashboardData` sends the wire request this way. A column left out, or
 * with no values, is "All" and is dropped from the query. */
const FILTER_PARAM_PREFIX = "f.";

function selectedFiltersFromParams(searchParams) {
 const result = {};
 for (const key of new Set(searchParams.keys())) {
  if (!key.startsWith(FILTER_PARAM_PREFIX)) continue;
  const values = searchParams.getAll(key).filter(Boolean);
  if (values.length > 0) result[key.slice(FILTER_PARAM_PREFIX.length)] = values;
 }
 return result;
}

/**
 * A saved dashboard, wired to the real `dashboards.py` (D2 #233; composed charts, 1.2.0):
 * `GET /dashboards/{id}` for the tiles, `GET /dashboards/{id}/data` for their values -- `kpi`
 * tiles from gold, `query` tiles from their cached result. Every number here is computed
 * against a stored definition or a guarded, cached query -- there is no model call at render
 * time, and there must never be one.
 *
 * Each tile can be edited (the same chart editor the create flow uses) or deleted in place,
 * with stable ids so one edit never remounts the others; its description is editable inline.
 * A prompt bar at the bottom adds a chart by description: Enter on a non-empty prompt starts
 * one `dashboard_suggestion` run (`POST /dashboards/suggestions`, the same run-then-poll seam
 * the create flow's AI path uses) with a budget of one chart, and opens the editor on the
 * composed chart -- SQL, axes, guard verdict and preview rows already filled in -- so the
 * person reviews and saves rather than writes. An empty Enter opens the editor blank, for
 * writing a query by hand.
 *
 * The run is deliberately *not* narrowed to this dashboard's own metrics and tables: with no
 * `kpi_identifiers`/`table_names` the backend shows the agent every ready KPI and the tables
 * behind them (its documented chat-prompt-alone fallback). A person adding a chart here is
 * usually asking for something the dashboard doesn't have yet -- narrowed to the existing
 * tiles' tables, the agent (rightly, by its refusal rule) answered "no chart: the only table
 * available is crm_contact" to a prompt about quote requests, which is a worse outcome than
 * a wider read.
 *
 * The refresh schedule and the dashboard's filter columns are each editable from the header
 * with the same shape: a pick is staged, a modal names what it will become (shared state, seen
 * by every viewer, not a per-viewer setting), and only Save actually PATCHes it. The schedule
 * is a select plus a confirm modal; the filter columns are a checklist modal (the same
 * candidate list the create flow's `DashboardBuilderStep.jsx` offers, here from
 * `GET /dashboards/{id}/filter-columns`) whose Save is the confirmation. The two are mutually
 * locked while either is mid-edit, so only one modal is ever open at a time. Which *values*
 * a viewer picks in those filters is a different thing -- per-viewer URL state, see
 * `FILTER_PARAM_PREFIX`.
 */

export default function DashboardView() {
 const { dashboardId } = useParams();
 const navigate = useNavigate();
 const [searchParams, setSearchParams] = useSearchParams();
 const selectedFilters = selectedFiltersFromParams(searchParams);
 const filtersKey = searchParams.toString();
 const [dashboardState, setDashboardState] = useState({ status: "loading", dashboard: null, error: null });
 const [dataState, setDataState] = useState({ status: "loading", byTileId: {}, error: null });
 const [actionError, setActionError] = useState(null);
 const [busy, setBusy] = useState(false);
 const [editingTileId, setEditingTileId] = useState(null);
 const [addPrompt, setAddPrompt] = useState("");
 // Holds the draft chart the editor is open on, `null` otherwise: the chart the composer
 // wrote for the prompt, or a blank `query` chart on an empty Enter. Never POSTed directly
 // (round-1 review, finding #4: this used to build a `TileWrite` straight from the raw prompt
 // text -- with an empty `sql` and no `position`, guaranteed to 422 both client- and
 // server-side) -- `ChartEditorPanel`'s `onSave` is the one place that ever calls
 // `addDashboardTile`, once the person has reviewed (and, for edited SQL, previewed) it.
 const [addingChart, setAddingChart] = useState(null);
 // The composition run behind a non-empty prompt, and the prompt it was started for (shown
 // while it runs, and put back in the bar on "Change the purpose"). Before this, Enter on a
 // description only opened the editor blank with the text as its *title* -- the description
 // itself went nowhere, which read as "nothing happens".
 const composeRun = useDashboardSuggestionRun();
 const [composePurpose, setComposePurpose] = useState("");
 // Set when a run finished without a usable chart: the composer's own `notes` on why. Kept
 // apart from `composeRun.error` (which is a failed *run*) because the run succeeded here --
 // it just had nothing to draft, and the note says what was missing.
 const [composeEmptyNote, setComposeEmptyNote] = useState(null);
 // Drops any in-flight/failed run and its purpose/notes -- shared by the dashboard-switch
 // effect below, "Change the purpose", and dismissing the empty-result note, so a future
 // field added to the compose flow's state (already three pieces: the run, its purpose, its
 // empty-result note) only has to be added at this one call site. Defined here, above every
 // early return, rather than down with the rest of the plain handlers: the dashboard-switch
 // effect just below calls it on every render including the initial `loading` one, and a
 // `const` declared after an early return is never reached on that render -- calling it from
 // an effect that *is* reached throws "Cannot access before initialization", not a caught
 // error inside the callback but one that unmounts the whole component.
 const resetCompose = () => {
  composeRun.reset();
  setComposePurpose("");
  setComposeEmptyNote(null);
 };
 // The refresh-schedule cadence picked but not yet confirmed -- `null` means the select just
 // shows `dashboard.refresh_schedule`. Kept separate from `dashboard` so Cancel is a no-op,
 // not a second write to undo the first (reapplies #272's editable-schedule control, silently
 // reverted to read-only text by this PR per round-1 review's issue-level comment).
 const [pendingSchedule, setPendingSchedule] = useState(null);
 // The filter-column editor: open or not, the `{table, column}` list staged in it (nothing is
 // written until "Save filters"), and the candidate columns fetched for it. Same "not yet
 // saved" role as `pendingSchedule`. Candidates are fetched on every open rather than once:
 // unlike the business-area list this replaced (a deployment-wide registry), they depend on
 // this dashboard's own query tiles, which can change between opens as charts are added.
 const [filtersEditorOpen, setFiltersEditorOpen] = useState(false);
 const [stagedFilterColumns, setStagedFilterColumns] = useState([]);
 const [filterCandidates, setFilterCandidates] = useState({ status: "idle", columns: [], error: null });

 // `dashboardId` this component is currently showing, readable inside async callbacks that
 // closed over an older value -- lets `loadData` (and the dashboard fetch) tell a stale
 // response apart from the current one without a second `cancelled` flag of its own.
 const currentDashboardIdRef = useRef(dashboardId);
 currentDashboardIdRef.current = dashboardId;

 const loadData = useCallback((id, filters) => {
  setDataState({ status: "loading", byTileId: {}, error: null });
  fetchDashboardData(id, { filters })
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
  // without this, an unconfirmed schedule or filter pick survives a back/forward navigation
  // (no `key` on this route, so the component isn't guaranteed to remount) and the confirm
  // modal reopens on the new dashboard showing the old one's leftover value.
  setPendingSchedule(null);
  setFiltersEditorOpen(false);
  setBusy(false);
  // Same for a composition run and its draft: they were started for the dashboard being
  // left, and its composed chart must not land on the one being opened.
  resetCompose();
  setAddingChart(null);
  // The dashboard's own definition and its tile data are two separate effects (this one, and
  // the one just below) so a filter change - which only ever needs to redo the data half - does
  // not also reset `pendingSchedule`/`addingChart`/the compose run below. They still both fire
  // on the very same render when `dashboardId` changes (mount or switch), so opening a
  // dashboard costs one round trip's worth of latency, not two sequential ones.
  fetchDashboard(dashboardId)
   .then((dashboard) => {
    if (cancelled) return;
    setDashboardState({ status: "ready", dashboard, error: null });
   })
   .catch((error) => {
    if (!cancelled) setDashboardState({ status: "error", dashboard: null, error });
   });
  return () => {
   cancelled = true;
  };
  // `resetCompose` closes over a stable callback (`composeRun.reset` never changes identity);
  // listing the whole `composeRun` object as a dep would re-run this effect (and refetch the
  // dashboard) on every poll tick.
  // eslint-disable-next-line react-hooks/exhaustive-deps
 }, [dashboardId]);

 // Reloads the tile data whenever the dashboard changes OR the URL's filter selection does.
 // Re-parses `filtersKey` rather than depending on `selectedFilters` directly, which is a
 // fresh object every render and would re-run this on every render rather than only when the
 // filters actually change.
 useEffect(() => {
  loadData(dashboardId, selectedFiltersFromParams(new URLSearchParams(filtersKey)));
 }, [dashboardId, filtersKey, loadData]);

 // Once the run succeeds, hand its first chart to the editor -- nothing is saved until the
 // person clicks Save there. The budget asked for one chart; the agent may still return
 // none (its refusal rule for a purpose it cannot serve from the context it was shown), in
 // which case its `notes` are the answer to show, not a blank editor.
 useEffect(() => {
  if (composeRun.status !== "ready") return;
  const result = composeRun.result;
  const chart = (result?.charts || [])[0];
  if (!chart) {
   setComposeEmptyNote(
    result?.notes?.trim() ||
     "The composer couldn't draft a chart for that from this dashboard's metrics and tables.",
   );
   composeRun.reset();
   return;
  }
  setAddingChart(chart);
  composeRun.reset();
  // `composeRun.reset` is stable; `status`/`result` are the values that matter.
  // eslint-disable-next-line react-hooks/exhaustive-deps
 }, [composeRun.status, composeRun.result]);

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
 const currentFilterColumns = dashboard.filters || [];
 // What the filter editor lists: the fetched candidates plus whatever the dashboard already
 // declares, so a saved filter column stays visible (and un-checkable) even if it has since
 // dropped out of the candidate set -- the semantic layer moves; a saved filter doesn't
 // reactively follow it. The server (`_validate_filters`) stays the source of truth on Save.
 const filterEditorOptions = Array.from(
  new Map(
   [...filterCandidates.columns, ...currentFilterColumns].map((c) => [`${c.table}.${c.column}`, c]),
  ).values(),
 ).sort((a, b) => a.table.localeCompare(b.table) || a.column.localeCompare(b.column));

 // `next` is the complete filter map (`DashboardFilterBar` always hands back the whole thing,
 // not a per-column patch) - every existing `f.*` param is replaced, not merged, so clearing a
 // column (an empty list) actually removes its param instead of leaving a stale `f.x=` behind.
 const handleFiltersChange = (next) => {
  const params = new URLSearchParams(searchParams);
  for (const key of Array.from(params.keys())) {
   if (key.startsWith(FILTER_PARAM_PREFIX)) params.delete(key);
  }
  for (const [key, values] of Object.entries(next)) {
   for (const value of values || []) {
    if (value) params.append(`${FILTER_PARAM_PREFIX}${key}`, value);
   }
  }
  setSearchParams(params, { replace: true });
 };

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
   await refreshDashboardApi(dashboard.id);
   // `refreshDashboardApi` recomputes the *cached* result every viewer reads by default; a
   // filter in view here is live, per-viewer state (see `loadData`'s own `filters` argument)
   // that this call knows nothing about, so re-fetch through the same filtered path rather
   // than trusting its (always-unfiltered) response directly -- otherwise "Refresh now" while
   // a filter is active would silently drop it from what's shown.
   loadData(dashboard.id, selectedFilters);
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

 // Same stage-then-save shape as the schedule select above, for the same reason: the filter
 // columns are shared state, not a per-viewer setting. Opening stages the current list and
 // fetches the candidates; nothing is written until `confirmFiltersEdit`.
 const openFiltersEditor = () => {
  const dashboardIdAtCall = dashboard.id;
  setStagedFilterColumns(currentFilterColumns.map(({ table, column }) => ({ table, column })));
  setFilterCandidates({ status: "loading", columns: [], error: null });
  setFiltersEditorOpen(true);
  fetchDashboardFilterColumns(dashboard.id)
   .then((columns) => {
    if (currentDashboardIdRef.current !== dashboardIdAtCall) return;
    setFilterCandidates({ status: "ready", columns: columns || [], error: null });
   })
   .catch((err) => {
    if (currentDashboardIdRef.current !== dashboardIdAtCall) return;
    setFilterCandidates({
     status: "error",
     columns: [],
     error: err?.message || "Couldn't load the columns this dashboard can filter by.",
    });
   });
 };

 const cancelFiltersEdit = () => setFiltersEditorOpen(false);

 const isStagedFilterColumn = (table, column) =>
  stagedFilterColumns.some((f) => f.table === table && f.column === column);

 const toggleStagedFilterColumn = (table, column) => {
  setStagedFilterColumns((prev) =>
   prev.some((f) => f.table === table && f.column === column)
    ? prev.filter((f) => !(f.table === table && f.column === column))
    : [...prev, { table, column }],
  );
 };

 const confirmFiltersEdit = async () => {
  const nextFilters = stagedFilterColumns.map(({ table, column }) => ({ table, column }));
  // Staleness guard, same as `confirmScheduleChange`.
  const dashboardIdAtCall = dashboard.id;
  setFiltersEditorOpen(false);
  setActionError(null);
  setBusy(true);
  try {
   const updated = await updateDashboardApi(dashboard.id, {
    name: dashboard.name,
    filters: nextFilters,
   });
   if (currentDashboardIdRef.current !== dashboardIdAtCall) return;
   setDashboardState({ status: "ready", dashboard: updated, error: null });
   // A value selection for a column that is no longer declared is ignored server-side
   // (`get_dashboard_data` only honours declared filters), but left in the URL it would
   // linger in the address bar and any bookmark of it -- drop it, which also re-fetches the
   // data through the (now narrower) filter set.
   const kept = new Set((updated.filters || []).map((f) => `${f.table}.${f.column}`));
   const pruned = Object.fromEntries(Object.entries(selectedFilters).filter(([key]) => kept.has(key)));
   if (Object.keys(pruned).length !== Object.keys(selectedFilters).length) handleFiltersChange(pruned);
  } catch (err) {
   if (currentDashboardIdRef.current !== dashboardIdAtCall) return;
   setActionError(err?.message || "Could not change the filters.");
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
   loadData(dashboard.id, selectedFilters);
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

 const composeChart = (text) => {
  setActionError(null);
  setComposeEmptyNote(null);
  setComposePurpose(text);
  composeRun.start({ purpose: text, chartBudget: 1 });
 };

 const composing = composeRun.status === "loading";
 const composeFailed = composeRun.status === "error";

 const addChartFromPrompt = () => {
  const text = addPrompt.trim();
  if (composing) return;
  setAddPrompt("");
  if (text) {
   composeChart(text);
   return;
  }
  // No `id` on either draft (round-2 review, finding #1): `tileWriteFromChart` sends whatever
  // `id` a chart carries, and `_tile_from_write`'s `write.id or f"tl_{...}"` only mints a
  // fresh one when the client sent none -- a hardcoded placeholder here (this used to be
  // `"__new__"`) reused that exact literal id on every add call, so the *second* one on any
  // given dashboard collided with the first and 409'd permanently. A composed chart carries
  // none either (`DashboardChartSuggestion` has no `id`), so the server mints one every time.
  setAddingChart({
   title: "New chart",
   chart: "table",
   source: "query",
   query: { sql: "" },
   description: "",
  });
 };

 // "Change the purpose": drop the run (or its failure) and put the text back in the bar.
 // Reads `composePurpose` before `resetCompose` clears it -- the plain closure variable is
 // unaffected by scheduling that state update, so the order here doesn't matter, but keeping
 // the read first avoids relying on that.
 const editComposePurpose = () => {
  setAddPrompt(composePurpose);
  resetCompose();
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
        // Locked against the *other* pending edit below rather than just `busy`: without
        // this, opening the filter editor then also picking a schedule before confirming
        // would leave two pending edits racing to open/replace the same modal slot.
        disabled={busy || filtersEditorOpen}
        className="rounded-md border border-border bg-surface-secondary px-1.5 py-1 text-[11px] font-medium text-text-secondary focus:border-primary focus:outline-none disabled:opacity-50"
       >
        {REFRESH_OPTIONS.map((o) => (
         <option key={o.value} value={o.value}>{o.label}</option>
        ))}
       </select>
      </span>
      <button
       type="button"
       onClick={openFiltersEditor}
       disabled={busy || pendingSchedule !== null}
       aria-label="Edit dashboard filters"
       className="inline-flex items-center gap-1 rounded-md border border-border bg-surface-secondary px-1.5 py-1 text-[11px] font-medium text-text-secondary hover:border-primary/40 hover:text-primary focus:border-primary focus:outline-none disabled:opacity-50"
      >
       <Filter size={11} />
       {currentFilterColumns.length === 0
        ? "No filters"
        : `${currentFilterColumns.length} filter${currentFilterColumns.length === 1 ? "" : "s"}`}
       <Pencil size={10} />
      </button>
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

   <DashboardFilterBar
    dashboardId={dashboard.id}
    filters={dashboard.filters}
    selected={selectedFilters}
    onChange={handleFiltersChange}
   />

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

   <div className="grid grid-flow-row-dense grid-cols-1 items-start gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
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
         // Composing shows its progress inline, under the grid -- unlike `editingTile` and
         // `addingChart` (each a full-screen `SlidePanel` with a click-catching backdrop),
         // nothing here blocks a click on an existing tile's pencil while a run is in
         // flight. Without this, opening one here while a run is still going would strand
         // its composed chart: the hand-off effect calls `setAddingChart`, but
         // `{addingChart && !editingTile && (...)}` below only renders it once this tile's
         // editor is closed, so the composed chart would pop open unannounced afterward,
         // its "Composing…" status having quietly vanished in the meantime.
         disabled={composing}
         title={composing ? "Wait for the chart being composed to finish." : undefined}
         aria-label={`Edit ${tile.title}`}
         className="rounded-md p-1.5 text-text-muted hover:bg-surface-tertiary hover:text-text-primary disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent"
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
     disabled={composing}
     aria-label="Add a chart"
     placeholder={
      composing
       ? "Composing a chart…"
       : 'Add a chart: describe it, e.g. "late deliveries by supplier, top 10"'
     }
     className="min-w-0 flex-1 bg-transparent text-sm text-text-primary placeholder:text-text-muted focus:outline-none disabled:opacity-60"
    />
    <kbd className="rounded border border-border-strong px-1.5 py-0.5 font-mono text-[11px] text-text-muted">⏎</kbd>
   </div>

   {(composing || composeFailed) && (
    <div className="rounded-lg border border-border-light bg-surface px-4 py-3">
     <AiGeneratingStep
      purpose={composePurpose}
      stage={composeRun.stage}
      error={composeFailed ? composeRun.error : null}
      onBack={editComposePurpose}
      onRetry={() => composeChart(composePurpose)}
     />
     {composing && (
      <div className="mt-2 flex justify-end">
       <button
        type="button"
        onClick={editComposePurpose}
        className="text-xs font-semibold text-text-secondary hover:text-text-primary"
       >
        Cancel
       </button>
      </div>
     )}
    </div>
   )}

   {composeEmptyNote && (
    <div className="flex items-start gap-2 rounded-lg border border-status-warning/30 bg-status-warning/5 px-3 py-2.5 text-xs text-text-secondary">
     <AlertTriangle size={15} className="mt-0.5 shrink-0 text-status-warning" />
     <div className="min-w-0 flex-1">
      <p>
       No chart for "{composePurpose}". {composeEmptyNote}
      </p>
      <div className="mt-2 flex items-center gap-3">
       <button
        type="button"
        onClick={editComposePurpose}
        className="font-semibold text-primary hover:underline"
       >
        Change the description
       </button>
       <button
        type="button"
        onClick={resetCompose}
        className="font-semibold text-text-secondary hover:text-text-primary"
       >
        Dismiss
       </button>
      </div>
     </div>
    </div>
   )}

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

   {filtersEditorOpen && (
    <Modal title="Dashboard filters" onClose={cancelFiltersEdit}>
     <p className="text-sm text-text-secondary">
      Pick the columns viewers can filter this dashboard by. Each checked column is a dropdown
      above the charts, for every viewer, from now on.
     </p>
     <div className="mt-3">
      {filterCandidates.status === "loading" ? (
       <p className="rounded-lg border border-dashed border-border-light px-3 py-2.5 text-xs text-text-muted">
        Loading columns…
       </p>
      ) : filterCandidates.status === "error" ? (
       <p className="rounded-lg border border-status-critical/30 bg-status-critical/10 px-3 py-2 text-xs font-medium text-status-critical">
        {filterCandidates.error}
       </p>
      ) : filterEditorOptions.length === 0 ? (
       <p className="rounded-lg border border-dashed border-border-light px-3 py-2.5 text-xs text-text-muted">
        Add a chart with a custom SQL query to make some of its columns available as filters.
       </p>
      ) : (
       <div className="max-h-72 overflow-y-auto rounded-lg border border-border">
        {filterEditorOptions.map(({ table, column }, index) => {
         const selected = isStagedFilterColumn(table, column);
         return (
          <button
           key={`${table}.${column}`}
           type="button"
           onClick={() => toggleStagedFilterColumn(table, column)}
           className={`flex w-full items-center gap-2.5 px-3 py-2 text-left ${
            index > 0 ? "border-t border-border" : ""
           } ${selected ? "bg-primary-light" : "hover:bg-surface-secondary"}`}
          >
           <span
            className={`flex h-4 w-4 shrink-0 items-center justify-center rounded ${
             selected ? "bg-primary" : "border border-border-light"
            }`}
           >
            {selected && <Check size={11} className="text-white" strokeWidth={3} />}
           </span>
           <span
            className={`flex-1 text-sm ${selected ? "font-semibold text-primary" : "font-medium text-text-tertiary"}`}
           >
            {column}
           </span>
           <span className="font-mono text-[11px] text-text-muted">
            {table}.{column}
           </span>
          </button>
         );
        })}
       </div>
      )}
     </div>
     <div className="mt-5 flex items-center justify-end gap-2 border-t border-border pt-4">
      <button
       type="button"
       onClick={cancelFiltersEdit}
       className="rounded-lg px-3.5 py-2 text-sm font-semibold text-text-secondary hover:text-text-primary"
      >
       Cancel
      </button>
      <button
       type="button"
       onClick={confirmFiltersEdit}
       // Saving while the candidates are still loading would write a list the person has
       // not yet been able to see or change; a failed load still allows Save, since the
       // staged list is the dashboard's own current one and un-checking those needs no
       // candidates.
       disabled={filterCandidates.status === "loading"}
       className="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-50"
      >
       Save filters
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
       loadData(dashboard.id, selectedFilters);
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
