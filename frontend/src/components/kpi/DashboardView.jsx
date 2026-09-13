import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { AlertTriangle, ArrowLeft, RefreshCw, Trash2 } from "lucide-react";
import { DemoAreaSpark, DemoAreaSparkWithAxes } from "../shared/DemoSparkCharts";
import { CHART_KINDS, labelForRefresh, tilesToWrite } from "../../storage/dashboards";
import {
 deleteDashboardApi,
 fetchDashboard,
 fetchDashboardData,
 updateDashboardApi,
} from "../../services/dashboards-api";

/**
 * A saved dashboard, wired to the real `dashboards.py` (D2, #233): `GET /dashboards/{id}` for
 * the tiles, `GET /dashboards/{id}/data` for their values from gold. Every number here is a
 * computed value against a confirmed definition -- there is no model call at render time, and
 * there must never be one.
 *
 * `series`/`delta` ship empty from the API today (`dashboards/data.py`'s own docstring: a real
 * series needs a gold-history reader, #227/E1, not built yet) -- a `line`/`bar` tile says so
 * rather than falling back to the old fabricated sparkline, which would look like a real trend
 * computed from a real number. `number`/`table` tiles are fully real: they only ever needed the
 * latest value, which `read_gold` already returns.
 */

function formatValue(unit, value) {
 if (value === null || value === undefined) return "—";
 const u = String(unit || "").toLowerCase();
 if (u.includes("%") || u.includes("rate") || u.includes("percent")) return `${value.toFixed(1)}%`;
 if (u.includes("day")) return `${Math.round(value)} days`;
 if (u.includes("currency") || u.includes("$")) return `$${(value * 12.4).toFixed(0)}k`;
 return value.toFixed(1);
}

/** Why a tile has no value yet -- `TileData.status` names it, so the tile says so rather than
 * showing a blank or a zero. */
const STATUS_MESSAGE = {
 never_computed: "Not computed yet.",
 stale: "Data is stale.",
 failed: "Computation failed.",
 not_scheduled: "Not on a computation schedule.",
};

function Tile({ tile, data, onChartChange }) {
 const value = data?.value ?? null;
 const series = data?.series || [];
 const rows = data?.rows || [];
 const hasTrend = series.length > 1;
 // A `table` chart with a `grain_value` (or whose gold read is a single row) collapses to a
 // scalar `value` with `rows: []` -- verified against a real dashboard while wiring this
 // (`dashboards/data.py`'s `_tile_data`: `target_row` resolved, so `table_rows` stays empty).
 // Showing "no rows" there would bury a real number; the scalar line covers it instead.
 const showScalar = tile.chart !== "table" || rows.length === 0;

 return (
  <div className="flex flex-col gap-3 rounded-xl border border-border bg-surface p-4">
   <div className="flex items-start justify-between gap-2">
    <div className="min-w-0">
     <p className="truncate text-sm font-semibold text-text-primary">{tile.title}</p>
    </div>
    {/* Presentation is a per-tile choice made while looking at the result, which is why it is
        here and not in the creation modal. The values are the contract's `ChartKind`. */}
    <select
     aria-label={`Chart type for ${tile.title}`}
     value={tile.chart}
     onChange={(e) => onChartChange(e.target.value)}
     className="shrink-0 rounded-md border border-border bg-surface-secondary px-1.5 py-1 text-[11px] font-medium text-text-secondary focus:border-primary focus:outline-none"
    >
     {CHART_KINDS.map((t) => (
      <option key={t.value} value={t.value}>{t.label}</option>
     ))}
    </select>
   </div>

   {showScalar && (
    <div className="flex items-baseline gap-2">
     <span className="text-2xl font-bold tracking-tight text-text-primary">
      {formatValue(data?.unit, value)}
     </span>
     {tile.chart !== "number" && tile.chart !== "table" && data?.delta != null && (
      <span className={`text-xs font-semibold ${data.delta >= 0 ? "text-status-success" : "text-status-critical"}`}>
       {data.delta >= 0 ? "▲" : "▼"} {Math.abs(data.delta).toFixed(1)}%
      </span>
     )}
    </div>
   )}

   {value === null && data?.status && data.status !== "fresh" && (
    <p className="text-xs text-text-muted">{STATUS_MESSAGE[data.status] || "No data yet."}</p>
   )}

   {tile.chart === "line" && (
    hasTrend ? (
     <DemoAreaSpark
      data={series.map((p) => p.value ?? 0)}
      viewHeight={34}
      maxHeight={90}
      stroke="#6941c6"
      showGrid={false}
      showDots={false}
     />
    ) : (
     <p className="text-xs text-text-muted">Not enough history yet to chart a trend.</p>
    )
   )}
   {tile.chart === "bar" && (
    hasTrend ? (
     <DemoAreaSparkWithAxes
      data={series.slice(-6).map((p) => p.value ?? 0)}
      viewHeight={44}
      stroke="#6941c6"
      fillOpacityTop={0.22}
     />
    ) : (
     <p className="text-xs text-text-muted">Not enough history yet to chart a trend.</p>
    )
   )}
   {/* `rows.length === 0` is already covered above: the scalar line (a real single-row
       result collapsed by the backend, or "-" when there's truly nothing) and the status
       message when one applies. */}
   {tile.chart === "table" && rows.length > 0 && (
    <table className="w-full text-xs">
     <tbody>
      {rows.map((row, i) => (
       <tr key={i} className="border-t border-border">
        {Object.entries(row).map(([key, cell]) => (
         <td key={key} className="py-1 text-text-muted first:text-left last:text-right last:font-medium last:text-text-primary">
          {String(cell)}
         </td>
        ))}
       </tr>
      ))}
     </tbody>
    </table>
   )}
  </div>
 );
}

export default function DashboardView() {
 const { dashboardId } = useParams();
 const navigate = useNavigate();
 const [dashboardState, setDashboardState] = useState({ status: "loading", dashboard: null, error: null });
 const [dataState, setDataState] = useState({ status: "loading", byTileId: {}, error: null });
 const [actionError, setActionError] = useState(null);

 const loadData = useCallback((id) => {
  setDataState({ status: "loading", byTileId: {}, error: null });
  fetchDashboardData(id)
   .then((data) => {
    const byTileId = Object.fromEntries((data.tiles || []).map((t) => [t.tile_id, t]));
    setDataState({ status: "ready", byTileId, error: null });
   })
   .catch((error) => setDataState({ status: "error", byTileId: {}, error }));
 }, []);

 useEffect(() => {
  let cancelled = false;
  setDashboardState({ status: "loading", dashboard: null, error: null });
  fetchDashboard(dashboardId)
   .then((dashboard) => {
    if (cancelled) return;
    setDashboardState({ status: "ready", dashboard, error: null });
    loadData(dashboardId);
   })
   .catch((error) => {
    if (!cancelled) setDashboardState({ status: "error", dashboard: null, error });
   });
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

 const setChart = async (tileId, chart) => {
  setActionError(null);
  const tiles = tilesToWrite(dashboard.tiles.map((t) => (t.id === tileId ? { ...t, chart } : t)));
  try {
   // `DashboardWrite.name` is required on every write, PATCH included (the contract's own
   // `updateDashboard` example always sends it: `{name: ..., visibility: ...}`) - only
   // `description`/`tiles`/`filters`/`refresh_schedule`/`visibility` are genuinely optional.
   const updated = await updateDashboardApi(dashboard.id, { name: dashboard.name, tiles });
   setDashboardState({ status: "ready", dashboard: updated, error: null });
   loadData(dashboard.id);
  } catch (err) {
   setActionError(err?.message || "Could not change the chart type.");
  }
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
       {labelForRefresh(dashboard.refresh_schedule)}
      </span>
      <span>{dashboard.tiles.length} metrics</span>
     </p>
    </div>
    <div className="flex flex-wrap items-center gap-2">
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

   <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
    {dashboard.tiles.map((tile) => (
     <Tile
      key={tile.id}
      tile={tile}
      data={dataState.byTileId[tile.id]}
      onChartChange={(chart) => setChart(tile.id, chart)}
     />
    ))}
   </div>

   {/* Said once, plainly, rather than implied: the figures are computed, not generated. */}
   <p className="text-xs text-text-muted">
    Every figure is computed from your connected data against the metric definition in Configure.
    Change a definition there and every dashboard using it follows.
   </p>
  </div>
 );
}
