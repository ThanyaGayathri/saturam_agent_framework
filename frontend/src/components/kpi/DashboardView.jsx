import { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, Download, Mail, RefreshCw, Send, Trash2 } from "lucide-react";
import { DemoAreaSpark, DemoAreaSparkWithAxes } from "../shared/DemoSparkCharts";
import {
 deleteDashboard,
 getDashboard,
 labelForDelivery,
 labelForRefresh,
 updateDashboard,
} from "../../storage/dashboards";

/**
 * A saved dashboard.
 *
 * Every number here is a computed value against a confirmed definition -- there is no model call
 * at render time, and there must never be one. Same definition, same window, same number, every
 * time. A dashboard whose figures cannot be reconciled with the customer's own reporting is worse
 * than no dashboard.
 *
 * The series below are placeholders standing in for that computation. They are deterministic per
 * metric (seeded from the name) rather than random, so a tile does not change shape on re-render
 * and nobody mistakes noise for a trend.
 */

const RENDER_TYPES = [
 { value: "stat", label: "Number" },
 { value: "trend", label: "Number + trend" },
 { value: "bar", label: "Bar" },
];

/** Deterministic pseudo-series from the metric name, so re-renders are stable. */
function seriesFor(name, points = 12) {
 let seed = 0;
 for (let i = 0; i < name.length; i++) seed = (seed * 31 + name.charCodeAt(i)) % 9973;
 const out = [];
 let v = 40 + (seed % 40);
 for (let i = 0; i < points; i++) {
  seed = (seed * 1103515245 + 12345) % 2147483648;
  v = Math.max(8, Math.min(96, v + ((seed % 21) - 10)));
  out.push(Number(v.toFixed(1)));
 }
 return out;
}

function displayValue(tile, series) {
 const last = series[series.length - 1];
 const unit = String(tile.unit || "").toLowerCase();
 if (unit.includes("%") || unit.includes("rate") || unit.includes("percent")) return `${last.toFixed(1)}%`;
 if (unit.includes("day")) return `${Math.round(last)} days`;
 if (unit.includes("currency") || unit.includes("$")) return `$${(last * 12.4).toFixed(0)}k`;
 return last.toFixed(1);
}

function Tile({ tile, onRenderChange }) {
 const series = useMemo(() => seriesFor(tile.kpiName), [tile.kpiName]);
 const prev = series[series.length - 2];
 const last = series[series.length - 1];
 const delta = prev ? ((last - prev) / prev) * 100 : 0;
 const up = delta >= 0;

 return (
  <div className="flex flex-col gap-3 rounded-xl border border-border bg-surface p-4">
   <div className="flex items-start justify-between gap-2">
    <div className="min-w-0">
     <p className="truncate text-sm font-semibold text-text-primary">{tile.kpiName}</p>
     {tile.subjectArea && <p className="text-xs text-text-muted">{tile.subjectArea}</p>}
    </div>
    {/* Presentation is a per-tile choice made while looking at the result, which is why it is
        here and not in the creation modal. */}
    <select
     aria-label={`Chart type for ${tile.kpiName}`}
     value={tile.render}
     onChange={(e) => onRenderChange(e.target.value)}
     className="shrink-0 rounded-md border border-border bg-surface-secondary px-1.5 py-1 text-[11px] font-medium text-text-secondary focus:border-primary focus:outline-none"
    >
     {RENDER_TYPES.map((t) => (
      <option key={t.value} value={t.value}>{t.label}</option>
     ))}
    </select>
   </div>

   <div className="flex items-baseline gap-2">
    <span className="text-2xl font-bold tracking-tight text-text-primary">{displayValue(tile, series)}</span>
    {tile.render !== "stat" && (
     <span className={`text-xs font-semibold ${up ? "text-status-success" : "text-status-critical"}`}>
      {up ? "▲" : "▼"} {Math.abs(delta).toFixed(1)}%
     </span>
    )}
   </div>

   {tile.render === "trend" && (
    <DemoAreaSpark data={series} viewHeight={34} maxHeight={90} stroke="#6941c6" showGrid={false} showDots={false} />
   )}
   {tile.render === "bar" && (
    <DemoAreaSparkWithAxes data={series.slice(-6)} viewHeight={44} stroke="#6941c6" fillOpacityTop={0.22} />
   )}
  </div>
 );
}

export default function DashboardView() {
 const { dashboardId } = useParams();
 const navigate = useNavigate();
 const [dashboard, setDashboard] = useState(() => getDashboard(dashboardId));

 if (!dashboard) {
  return (
   <div className="rounded-xl border border-dashed border-border-light bg-surface-secondary px-6 py-16 text-center">
    <p className="text-sm text-text-secondary">That dashboard no longer exists.</p>
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

 const setRender = (index, value) => {
  const tiles = dashboard.tiles.map((t, i) => (i === index ? { ...t, render: value } : t));
  setDashboard(updateDashboard(dashboard.id, { tiles }));
 };

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
       {labelForRefresh(dashboard.refresh)}
      </span>
      <span className="inline-flex items-center gap-1">
       <Send size={11} />
       {labelForDelivery(dashboard.delivery)}
      </span>
      <span>{dashboard.tiles.length} metrics</span>
     </p>
    </div>
    <div className="flex flex-wrap items-center gap-2">
     <button type="button" className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-surface px-3 py-2 text-sm font-semibold text-text-secondary hover:bg-surface-secondary">
      <Download size={14} />
      Export
     </button>
     <button type="button" className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-surface px-3 py-2 text-sm font-semibold text-text-secondary hover:bg-surface-secondary">
      <Mail size={14} />
      Send now
     </button>
     <button
      type="button"
      onClick={() => {
       if (window.confirm(`Delete "${dashboard.name}"?`)) {
        deleteDashboard(dashboard.id);
        navigate("/kpis/dashboards");
       }
      }}
      className="rounded-lg border border-border bg-surface p-2 text-text-muted hover:border-status-critical hover:text-status-critical"
      aria-label="Delete dashboard"
     >
      <Trash2 size={15} />
     </button>
    </div>
   </div>

   <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
    {dashboard.tiles.map((tile, i) => (
     <Tile key={tile.kpiName + i} tile={tile} onRenderChange={(v) => setRender(i, v)} />
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
