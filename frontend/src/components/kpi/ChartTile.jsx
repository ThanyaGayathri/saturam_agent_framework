import { useEffect, useState } from "react";
import { AlertTriangle, ShieldAlert, ShieldCheck } from "lucide-react";
import { DemoAreaSpark, DemoBarsWithAxes } from "../shared/DemoSparkCharts";

/**
 * One chart on a dashboard, rendered from a `Tile` (or a composed chart of the same shape)
 * and a `TileData`-shaped result - the real one from `GET /dashboards/{id}/data`, or the
 * preview a composed chart arrived with (`storage/dashboards.js`'s `tileDataFromPreview`).
 * Used by the review step of the create flow and by the saved dashboard, so both render one
 * shape: title, the chart, the description under it, and a source line saying where the
 * numbers come from. Every number here is computed against a stored definition - there is no
 * model call at render time, and there must never be one.
 *
 * `actions` renders in the header (hover-revealed on the dashboard). `onDescriptionChange`,
 * when given, makes the description editable in place (click, edit, blur to save).
 */

const CURRENCY_SYMBOL = { usd: "$", eur: "€", gbp: "£", inr: "₹" };

/** A value in the KPI's own unit: money compact past ten thousand ($9.67M, $587K), a
 *  rate with its sign, days as days, counts with separators. */
function formatValue(unit, value) {
 if (value === null || value === undefined) return "—";
 const u = String(unit || "").toLowerCase().trim();
 const n = Number(value);
 if (Number.isNaN(n)) return String(value);
 if (u.includes("%") || u.includes("rate") || u.includes("percent")) {
  return `${n.toFixed(1)}%`;
 }
 if (u.includes("day")) return `${Math.round(n)} days`;
 const symbol = CURRENCY_SYMBOL[u] || (u.includes("currency") || u.includes("$") ? "$" : u.includes("£") ? "£" : u.includes("€") ? "€" : null);
 if (symbol) {
  const a = Math.abs(n);
  if (a >= 1_000_000) return `${symbol}${(n / 1_000_000).toFixed(2)}M`;
  if (a >= 10_000) return `${symbol}${Math.round(n / 1000).toLocaleString()}K`;
  return `${symbol}${n.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
 }
 if (Number.isInteger(n)) return n.toLocaleString();
 return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

/** Whether a fall in this KPI is good news. The registry carries no polarity yet, so the
 *  name decides: stalled, churn, at-risk, lost, days to close, cycle length, cost, returns
 *  are better lower; everything else better higher. */
function lowerIsBetter(tile) {
 const t = `${tile?.title || ""} ${tile?.kpi_identifier || ""}`.toLowerCase();
 return /stall|churn|at[-_ ]risk|lost|lapse|days[-_ ]to[-_ ]close|cycle[-_ ]length|recency|cost|return|complaint|stockout|overdue|late|delay/.test(t);
}

/** The delta with its meaning: green when the move is good for this KPI, red when bad. */
function DeltaBadge({ tile, unit, delta }) {
 if (delta == null || Number.isNaN(Number(delta))) return null;
 const d = Number(delta);
 const good = lowerIsBetter(tile) ? d <= 0 : d >= 0;
 if (d === 0) return <span className="text-xs font-semibold text-text-muted">— no change</span>;
 return (
  <span className={`text-xs font-semibold ${good ? "text-status-success" : "text-status-critical"}`} title={`${d > 0 ? "Up" : "Down"} since the previous business date${lowerIsBetter(tile) ? " · lower is better" : ""}`}>
   {d > 0 ? "▲" : "▼"} {formatValue(unit, Math.abs(d))}
  </span>
 );
}

/** Why a tile has no value yet -- `TileData.status` names it, so the tile says so rather than
 * showing a blank or a zero. */
const STATUS_MESSAGE = {
 never_computed: "Not computed yet.",
 stale: "Data is stale.",
 failed: "The last computation failed.",
 not_scheduled: "Not on a computation schedule.",
};

const GUARD_PILL = {
 ok: { label: "Guard ok", className: "border-status-success/40 bg-status-success/10 text-status-success", Icon: ShieldCheck },
 rewritten: { label: "Rewritten", className: "border-status-warning/40 bg-status-warning/10 text-status-warning", Icon: ShieldAlert },
 refused: { label: "Refused", className: "border-status-critical/40 bg-status-critical/10 text-status-critical", Icon: ShieldAlert },
};

export function GuardPill({ guard }) {
 if (!guard?.status) return null;
 const spec = GUARD_PILL[guard.status] || GUARD_PILL.ok;
 const Icon = spec.Icon;
 return (
  <span
   title={guard.detail || undefined}
   className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-semibold ${spec.className}`}
  >
   <Icon size={11} />
   {spec.label}
  </span>
 );
}

/** Horizontal bars for a category x axis - the one shape `DemoSparkCharts.jsx` has no
 * component for. Drawn to one scale, labels from the rows themselves. */
/** The chart's own hue: the series tokens in fixed order, one per tile, so six charts on a
 *  dashboard are six entities and not one shape repeated. */
export const chartHue = (index) => `var(--chart-${(Math.max(0, Number(index) || 0) % 6) + 1})`;

function CategoryBars({ rows, xColumn, yColumn, unit, hue }) {
 const items = rows
  .map((row) => ({ label: String(row[xColumn] ?? "—"), value: Number(row[yColumn]) }))
  .filter((d) => !Number.isNaN(d.value))
  .slice(0, 8);
 if (!items.length) return null;
 const max = Math.max(...items.map((d) => Math.abs(d.value)), 0) || 1;
 const rowH = 18;
 const height = items.length * rowH;
 const labelW = 88;
 const width = 320;
 return (
  <svg viewBox={`0 0 ${width} ${height}`} className="block h-auto w-full" role="img">
   {items.map((d, i) => {
    const y = i * rowH;
    const w = Math.max(1, ((width - labelW - 56) * Math.abs(d.value)) / max);
    return (
     <g key={`${d.label}-${i}`}>
      <text x={labelW - 8} y={y + rowH / 2 + 3.5} textAnchor="end" className="fill-text-muted" style={{ fontSize: 9.5 }}>
       {d.label.length > 16 ? `${d.label.slice(0, 15)}…` : d.label}
      </text>
      <rect x={labelW} y={y + 3} width={w} height={rowH - 6} rx={2} style={{ fill: hue || "var(--chart-1)" }} />
      <text x={labelW + w + 5} y={y + rowH / 2 + 3.5} className="fill-text-secondary" style={{ fontSize: 9.5, fontVariantNumeric: "tabular-nums" }}>
       {formatValue(unit, d.value)}
      </text>
     </g>
    );
   })}
  </svg>
 );
}

function SourceLine({ tile }) {
 if (tile.source === "query") {
  const tables = tile.query?.source_tables || [];
  return (
   <span className="inline-flex flex-wrap items-center gap-1 text-[11px] text-text-muted">
    <span className="h-1.5 w-1.5 rounded-full bg-status-success" />
    Composed from
    {tables.length ? (
     tables.map((t) => (
      <code key={t} className="rounded bg-surface-tertiary px-1 text-[10px] text-text-secondary">{t}</code>
     ))
    ) : (
     <span>a query</span>
    )}
   </span>
  );
 }
 return (
  <span className="inline-flex items-center gap-1 text-[11px] text-text-muted">
   <span className="h-1.5 w-1.5 rounded-full bg-primary" />
   From metric <b className="font-semibold text-text-secondary">{tile.kpi_identifier}</b>
   {tile.grain_value ? <code className="rounded bg-surface-tertiary px-1 text-[10px]">{tile.grain_value}</code> : null}
  </span>
 );
}

function EditableDescription({ text, onChange }) {
 const [editing, setEditing] = useState(false);
 const [draft, setDraft] = useState(text || "");
 useEffect(() => {
  if (!editing) setDraft(text || "");
 }, [text, editing]);

 if (!onChange) {
  return text ? <p className="text-xs leading-relaxed text-text-secondary">{text}</p> : null;
 }
 if (!editing) {
  return (
   <button
    type="button"
    onClick={() => setEditing(true)}
    title="Edit description"
    className="w-full rounded px-0.5 text-left text-xs leading-relaxed text-text-secondary hover:bg-surface-secondary"
   >
    {text || <span className="italic text-text-muted">Add a description…</span>}
   </button>
  );
 }
 return (
  <textarea
   autoFocus
   rows={3}
   value={draft}
   onChange={(e) => setDraft(e.target.value)}
   onBlur={() => {
    setEditing(false);
    if (draft.trim() !== (text || "").trim()) onChange(draft.trim());
   }}
   className="w-full resize-none rounded-md border border-border bg-surface-default px-2 py-1 text-xs leading-relaxed text-text-primary focus:border-primary focus:outline-none"
  />
 );
}

export default function ChartTile({
 tile,
 data,
 guard,
 actions,
 onDescriptionChange,
 placeholder,
 className = "",
 hue: hueIndex = 0,
}) {
 const hue = chartHue(hueIndex);
 const value = data?.value ?? null;
 // A `SeriesPoint.value` of `null` means the formula returned NULL for that date -- a real
 // gap, not a zero -- so it's dropped rather than charted as 0 (`dashboards/data.py`'s own
 // docstring draws this line between "no row" and "row with a NULL value").
 const series = (data?.series || []).filter((p) => p.value != null);
 const rows = data?.rows || [];
 const columns = data?.columns?.length ? data.columns : rows.length ? Object.keys(rows[0]) : [];
 const hasTrend = series.length > 1;
 const isQuery = tile.source === "query";
 // A KPI tile at a grain with no grain_value carries the breakdown across the grain as rows
 // (dashboards/data.py, K22): the first column is the grain key, the last is the value.
 const xColumn = tile.query?.x?.column || (!isQuery && rows.length > 0 ? columns[0] : undefined);
 const yColumn = tile.query?.y?.[0]?.column || columns[columns.length - 1];
 const categoryBars = tile.chart === "bar" && !hasTrend && rows.length > 0 && xColumn && (isQuery || value === null);
 const showScalar =
  tile.chart === "number" || (tile.chart !== "table" && !hasTrend && !categoryBars && value !== null);
 const statusMessage =
  data?.status && data.status !== "fresh" ? STATUS_MESSAGE[data.status] || "No data yet." : null;

 return (
  <div className={`group flex flex-col gap-2.5 rounded-xl border border-border bg-surface p-4 ${className}`}>
   <div className="flex items-start justify-between gap-2">
    <div className="min-w-0">
     <p className="line-clamp-2 text-sm font-semibold leading-snug text-text-primary" title={tile.title}>{tile.title || "Untitled chart"}</p>
     <p className="text-[11px] text-text-muted">
      {tile.chart}
      {data?.computed_at ? ` · computed ${new Date(data.computed_at).toLocaleString()}` : ""}
     </p>
    </div>
    {actions ? (
     <div className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100">
      {actions}
     </div>
    ) : null}
   </div>

   {placeholder ? (
    <p className="rounded-lg border border-dashed border-border-light px-3 py-4 text-center text-xs text-text-muted">{placeholder}</p>
   ) : (
    <>
     {tile.chart === "number" && (
      <div className="flex items-baseline gap-2">
       <span className="text-2xl font-bold tracking-tight text-text-primary" style={{ fontVariantNumeric: "tabular-nums" }}>
        {formatValue(data?.unit, value)}
       </span>
       <DeltaBadge tile={tile} unit={data?.unit} delta={data?.delta} />
      </div>
     )}

     {tile.chart === "line" &&
      (hasTrend ? (
       <div>
        <div className="mb-1 flex items-baseline gap-2">
         <span className="text-lg font-semibold text-text-primary" style={{ fontVariantNumeric: "tabular-nums" }}>{formatValue(data?.unit, value)}</span>
         <DeltaBadge tile={tile} unit={data?.unit} delta={data?.delta} />
        </div>
        <DemoAreaSpark data={series.map((p) => p.value)} viewHeight={34} maxHeight={90} stroke={hue} showGrid={false} showDots={false} />
       </div>
      ) : showScalar ? (
       <span className="text-2xl font-bold tracking-tight text-text-primary">{formatValue(data?.unit, value)}</span>
      ) : !statusMessage ? (
       <p className="text-xs text-text-muted">Not enough history yet to chart a trend.</p>
      ) : null)}

     {tile.chart === "bar" &&
      (hasTrend ? (
       <DemoBarsWithAxes data={series.slice(-12).map((p) => p.value)} viewHeight={44} barFill={hue} />
      ) : categoryBars ? (
       <CategoryBars rows={rows} xColumn={xColumn} yColumn={yColumn} unit={data?.unit} hue={hue} />
      ) : showScalar ? (
       <span className="text-2xl font-bold tracking-tight text-text-primary">{formatValue(data?.unit, value)}</span>
      ) : !statusMessage ? (
       <p className="text-xs text-text-muted">Not enough history yet to chart a trend.</p>
      ) : null)}

     {tile.chart === "table" &&
      (rows.length > 0 ? (
       <div className="overflow-x-auto">
        <table className="w-full text-xs">
         {columns.length > 0 && (
          <thead>
           <tr>
            {columns.map((c) => (
             <th key={c} className="border-b border-border py-1 pr-2 text-left text-[10px] font-semibold uppercase tracking-wide text-text-muted last:text-right">{c}</th>
            ))}
           </tr>
          </thead>
         )}
         <tbody>
          {rows.slice(0, 10).map((row, i) => (
           <tr key={i} className="border-t border-border first:border-t-0">
            {(columns.length ? columns : Object.keys(row)).map((key) => (
             <td key={key} className="py-1 pr-2 text-text-muted last:pr-0 last:text-right last:font-medium last:text-text-primary" style={{ fontVariantNumeric: "tabular-nums" }}>
              {row[key] === null || row[key] === undefined ? "—" : String(row[key])}
             </td>
            ))}
           </tr>
          ))}
         </tbody>
        </table>
       </div>
      ) : value !== null ? (
       <span className="text-2xl font-bold tracking-tight text-text-primary">{formatValue(data?.unit, value)}</span>
      ) : null)}

     {statusMessage && value === null && rows.length === 0 && (
      <p className="flex items-center gap-1 text-xs text-text-muted">
       <AlertTriangle size={12} />
       {statusMessage}
      </p>
     )}
    </>
   )}

   {(tile.description || onDescriptionChange) && (
    <div className="border-t border-border pt-2">
     <EditableDescription text={tile.description} onChange={onDescriptionChange} />
    </div>
   )}

   <div className="flex flex-wrap items-center justify-between gap-2">
    <SourceLine tile={tile} />
    {guard ? <GuardPill guard={guard} /> : null}
   </div>
   {guard?.status && guard.status !== "ok" && guard.detail ? (
    <p className={`rounded-md border px-2 py-1.5 text-[11px] leading-relaxed ${guard.status === "refused" ? "border-status-critical/30 bg-status-critical/5 text-text-secondary" : "border-status-warning/30 bg-status-warning/5 text-text-secondary"}`}>
     {guard.detail}
    </p>
   ) : null}
  </div>
 );
}
