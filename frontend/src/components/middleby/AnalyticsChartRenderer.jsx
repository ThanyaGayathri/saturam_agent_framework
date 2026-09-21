import { useMemo } from "react";
import { DemoAreaSpark, formatChartNumber } from "../shared/DemoSparkCharts";
import { parseNum, rowLabel, formatCellValue, isTextCellValue, isBrandColumnKey, isLikelyBrandNameValue, isSkuColumnKey, findBrandColumn } from "../../domain/middleby-analytics-data";
import { prepareChartRows, shouldUseBarInsteadOfDonut, topNOtherFootnote } from "../../domain/middleby-chart-render-rules";
import { summarizeSlaAging } from "../../domain/middleby-sla-aging";
import { SankeyChart, GeoMapChart, TreemapChart, DeployLineChart } from "./MiddlebyExtendedCharts";

const BAR_COLORS = ["#6941c6", "#7f56d9", "#9e77ed", "#b692f6", "#d6bbfb", "#44337a"];

function formatColumnLabel(col) {
 return String(col || "")
 .replace(/_example/g, "")
 .replace(/_/g, " ")
 .replace(/\b\w/g, (c) => c.toUpperCase());
}

function BlockedView({ message }) {
 return (
 <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-6 text-sm text-amber-950 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100">
 <p className="font-semibold">Chart not available in this demo slice</p>
 <p className="mt-2 whitespace-pre-wrap text-amber-900/90 dark:text-amber-100/90">{message}</p>
 </div>
 );
}

const HIDDEN_TABLE_KEYS = new Set([
  "data_source",
  "example_data",
  "segment_method",
  "notes",
  "demo_window",
  "anon_note",
]);

function DataTable({ rows, spec, priority = false, maxRows = 12, onBrandClick, onSkuClick }) {
 const cols = useMemo(() => {
 if (!rows?.length) return [];
 const keys = new Set();
 for (const col of [...(spec.dimensionColumns || []), ...(spec.metricColumns || [])]) keys.add(col);
 for (const k of Object.keys(rows[0] || {})) {
 if (!HIDDEN_TABLE_KEYS.has(k)) keys.add(k);
 }
 return [...keys];
 }, [rows, spec]);

 const visible = rows.slice(0, maxRows);

 return (
 <div className="overflow-x-auto rounded-xl border border-border">
 <table className="min-w-full divide-y divide-border text-left text-sm">
 <thead className="bg-surface-secondary">
 <tr>
 {cols.map((col) => (
 <th key={col} className="px-3 py-2 font-semibold text-text-secondary">
 {formatColumnLabel(col)}
 </th>
 ))}
 </tr>
 </thead>
 <tbody className="divide-y divide-border bg-surface-default">
 {visible.map((row, i) => (
 <tr
 key={i}
 className={
 priority && String(row.timeframe || "").includes("0 - 1")
 ? "bg-primary-light/40 dark:bg-primary/10"
 : undefined
 }
 >
                  {cols.map((col) => {
                    const raw = row[col];
                    const display = formatCellValue(col, raw);
                    const isBrand =
                      isBrandColumnKey(col) &&
                      onBrandClick &&
                      isLikelyBrandNameValue(raw);
                    const isSku =
                      isSkuColumnKey(col) &&
                      onSkuClick &&
                      raw != null &&
                      String(raw).trim() !== "" &&
                      isLikelyBrandNameValue(raw);
                    const isNumericCol = !isTextCellValue(raw) && display !== "-";
                    return (
                      <td
                        key={col}
                        className={`max-w-[240px] truncate px-3 py-2 text-text-primary ${
                          isNumericCol ? "tabular-nums" : ""
                        }`}
                        title={String(raw ?? "")}
                      >
                        {isBrand ? (
                          <button
                            type="button"
                            className="font-medium text-primary hover:underline"
                            onClick={() => onBrandClick(String(raw).trim())}
                          >
                            {display}
                          </button>
                        ) : isSku ? (
                          <button
                            type="button"
                            className="font-mono text-primary hover:underline"
                            onClick={() => onSkuClick(String(raw).trim())}
                          >
                            {display}
                          </button>
                        ) : (
                          display
                        )}
                      </td>
                    );
                  })}
 </tr>
 ))}
 </tbody>
 </table>
 {rows.length > maxRows ? (
 <p className="border-t border-border px-3 py-2 text-xs text-text-muted">
 Showing {maxRows} of {rows.length} rows
 </p>
 ) : null}
 </div>
 );
}

function HorizontalBars({ rows, spec, metricIndex = 0, secondMetricIndex = null, onBrandClick, footnote = null }) {
 const metricCol = spec.metricColumns?.[metricIndex];
 const metricCol2 = secondMetricIndex != null ? spec.metricColumns?.[secondMetricIndex] : null;
 if (!metricCol || !rows?.length) return <p className="text-sm text-text-muted">No chart data</p>;

 const items = [...rows]
 .map((row) => ({
 label: rowLabel(row, spec.dimensionColumns),
 v1: parseNum(row[metricCol]) ?? 0,
 v2: metricCol2 ? parseNum(row[metricCol2]) ?? 0 : null,
 }))
 .sort((a, b) => b.v1 - a.v1);

 const max = Math.max(...items.map((x) => Math.max(x.v1, x.v2 ?? 0)), 1);
 const displayFootnote = footnote ?? topNOtherFootnote(rows, spec);
 const brandDim = findBrandColumn(spec.dimensionColumns || []);
 const labelUsesBrand =
   brandDim &&
   (spec.dimensionColumns?.[0] === brandDim ||
     (spec.dimensionColumns?.length === 1 && spec.dimensionColumns[0] === brandDim));

 return (
 <div className="space-y-3">
 {items.map((item, i) => (
 <div key={i}>
 <div className="mb-1 flex items-center justify-between gap-2 text-xs">
 <span className="truncate font-medium text-text-primary">
 {onBrandClick && labelUsesBrand && isLikelyBrandNameValue(item.label) ? (
 <button type="button" className="text-left hover:text-primary hover:underline" onClick={() => onBrandClick(item.label)}>
 {item.label}
 </button>
 ) : (
 item.label
 )}
 </span>
 <span className="shrink-0 text-text-secondary">
 {formatChartNumber(item.v1)}
 {item.v2 != null ? ` / ${formatChartNumber(item.v2)}` : ""}
 </span>
 </div>
 <div className="space-y-1">
 <div className="h-2.5 overflow-hidden rounded-full bg-surface-tertiary">
 <div
 className="h-full rounded-full"
 style={{ width: `${(item.v1 / max) * 100}%`, backgroundColor: BAR_COLORS[i % BAR_COLORS.length] }}
 />
 </div>
 {item.v2 != null ? (
 <div className="h-2 overflow-hidden rounded-full bg-surface-tertiary">
 <div
 className="h-full rounded-full bg-sky-500/80"
 style={{ width: `${(item.v2 / max) * 100}%` }}
 />
 </div>
 ) : null}
 </div>
 </div>
 ))}
 {displayFootnote ? <p className="text-xs text-text-muted">{displayFootnote}</p> : null}
 {metricCol2 ? (
 <p className="text-xs text-text-muted">
 Purple: {formatColumnLabel(metricCol)} · Blue: {formatColumnLabel(metricCol2)}
 </p>
 ) : null}
 </div>
 );
}

function FunnelChart({ rows, spec }) {
 const dim = spec.dimensionColumns?.[0];
 const metric = spec.metricColumns?.[0];
 if (!dim || !metric) return null;
 const max = Math.max(...rows.map((r) => parseNum(r[metric]) ?? 0), 1);

 return (
 <div className="space-y-2">
 {rows.map((row, i) => {
 const val = parseNum(row[metric]) ?? 0;
 const width = Math.max(12, (val / max) * 100);
 return (
 <div key={i} className="flex items-center gap-3">
 <div className="w-36 shrink-0 text-xs font-medium text-text-secondary">{row[dim]}</div>
 <div className="flex min-w-0 flex-1 items-center gap-2">
 <div className="h-8 rounded-md bg-primary/80" style={{ width: `${width}%`, minWidth: "2rem" }} />
 <span className="text-sm font-semibold text-text-primary">{formatChartNumber(val)}</span>
 </div>
 </div>
 );
 })}
 </div>
 );
}

function KpiCards({ rows }) {
 return (
 <div className="grid gap-3 sm:grid-cols-2">
 {rows.slice(0, 6).map((row, i) => (
 <div key={i} className="rounded-xl border border-border bg-surface-default px-4 py-3 shadow-sm">
 <div className="text-xs font-medium text-text-muted">{row.metric || row.signal || row.source || "Metric"}</div>
 <div className="mt-1 text-xl font-semibold text-text-primary">{row.value ?? "-"}</div>
 {row.demo_window ? <div className="mt-1 text-[11px] text-text-muted">{row.demo_window}</div> : null}
 </div>
 ))}
 </div>
 );
}

function AlertList({ rows }) {
 return (
 <div className="space-y-2">
 {rows.map((row, i) => (
 <div key={i} className="flex items-start gap-3 rounded-lg border border-border px-3 py-2">
 <span
 className={`mt-0.5 inline-flex rounded-full px-2 py-0.5 text-[10px] font-bold uppercase ${
 row.direction === "down"
 ? "bg-red-100 text-red-800"
 : row.direction === "up"
 ? "bg-emerald-100 text-emerald-800"
 : "bg-surface-secondary text-text-secondary"
 }`}
 >
 {row.direction || "flat"}
 </span>
 <div>
 <div className="text-sm font-medium text-text-primary">{row.signal}</div>
 {row.detail ? <div className="text-xs text-text-secondary">{row.detail}</div> : null}
 </div>
 </div>
 ))}
 </div>
 );
}

function JourneyTimeline({ rows, spec }) {
 const dim = spec.dimensionColumns || [];
 const metricCol = spec.metricColumns?.[0];
 return (
 <ol className="relative space-y-4 border-l-2 border-primary/30 pl-4">
 {rows.map((row, i) => {
  const stepNumber = dim[0] ? row[dim[0]] : (i + 1);
  const stepTitle = row.step_description || row.step_label || row[dim[1]] || `Step ${i + 1}`;
  const eventType = row.event_type_example || row.event_type || null;
  const metricVal = metricCol ? row[metricCol] : null;
  return (
   <li key={i}>
    <div className="absolute -left-[5px] mt-1.5 h-2.5 w-2.5 rounded-full bg-primary" />
    <div className="text-sm font-semibold text-text-primary">
     Step {stepNumber} - {stepTitle}
    </div>
    <div className="mt-0.5 text-xs text-text-secondary">
     {eventType ? (
      <span className="mr-2 rounded-full bg-surface-secondary px-2 py-0.5 text-[10px] font-medium text-text-muted">
       {eventType.replace(/_/g, " ")}
      </span>
     ) : null}
     {metricVal != null && metricVal !== "" ? `${metricVal} sessions` : null}
    </div>
   </li>
  );
 })}
 </ol>
 );
}

function HeatmapTable({ rows, spec }) {
 const metric = spec.metricColumns?.[0];
 const vals = rows.map((r) => parseNum(r[metric]) ?? 0);
 const max = Math.max(...vals, 1);
 return (
 <div className="overflow-x-auto rounded-xl border border-border">
 <table className="min-w-full text-sm">
 <thead>
 <tr className="bg-surface-secondary text-left">
 {spec.dimensionColumns?.map((c) => (
 <th key={c} className="px-3 py-2 font-semibold text-text-secondary">
 {formatColumnLabel(c)}
 </th>
 ))}
 {spec.metricColumns?.map((c) => (
 <th key={c} className="px-3 py-2 font-semibold text-text-secondary">
 {formatColumnLabel(c)}
 </th>
 ))}
 </tr>
 </thead>
 <tbody>
 {rows.slice(0, 20).map((row, i) => {
 const v = parseNum(row[metric]) ?? 0;
 const intensity = 0.15 + (v / max) * 0.55;
 return (
 <tr key={i}>
 {spec.dimensionColumns?.map((c) => (
 <td key={c} className="border-t border-border px-3 py-2">
 {row[c]}
 </td>
 ))}
 {spec.metricColumns?.map((c) => (
 <td
 key={c}
 className="border-t border-border px-3 py-2 font-medium"
 style={{ backgroundColor: `rgba(105, 65, 198, ${c === metric ? intensity : 0.08})` }}
 >
 {row[c]}
 </td>
 ))}
 </tr>
 );
 })}
 </tbody>
 </table>
 </div>
 );
}

function DonutChart({ rows, spec, onBrandClick }) {
 const metricCol = spec.metricColumns?.[0];
 if (!metricCol) return null;
 if (shouldUseBarInsteadOfDonut(rows, metricCol)) {
 return <HorizontalBars rows={rows} spec={spec} onBrandClick={onBrandClick} />;
 }

 const segments = rows
 .map((row, i) => ({
 label: rowLabel(row, spec.dimensionColumns),
 value: parseNum(row[metricCol]) ?? 0,
 color: BAR_COLORS[i % BAR_COLORS.length],
 }))
 .filter((s) => s.value > 0);

 const total = segments.reduce((s, x) => s + x.value, 0) || 1;
 let angle = 0;
 const slices = segments.map((seg) => {
 const sweep = (seg.value / total) * 360;
 const start = angle;
 angle += sweep;
 return { ...seg, start, sweep };
 });

 const polar = (deg, r = 40) => {
 const rad = ((deg - 90) * Math.PI) / 180;
 return { x: 50 + r * Math.cos(rad), y: 50 + r * Math.sin(rad) };
 };

 return (
 <div className="flex flex-col gap-4 sm:flex-row sm:items-center">
 <svg viewBox="0 0 100 100" className="mx-auto h-44 w-44 shrink-0" role="img" aria-label="Donut chart">
 {slices.map((slice, i) => {
 if (slice.sweep >= 359.9) {
 return <circle key={i} cx="50" cy="50" r="40" fill={slice.color} />;
 }
 const start = polar(slice.start);
 const end = polar(slice.start + slice.sweep);
 const large = slice.sweep > 180 ? 1 : 0;
 const d = `M 50 50 L ${start.x} ${start.y} A 40 40 0 ${large} 1 ${end.x} ${end.y} Z`;
 return <path key={i} d={d} fill={slice.color} />;
 })}
 <circle cx="50" cy="50" r="22" className="fill-surface-default" />
 </svg>
 <ul className="min-w-0 flex-1 space-y-2 text-sm">
 {segments.map((seg, i) => (
 <li key={i} className="flex items-center justify-between gap-2">
 <span className="flex min-w-0 items-center gap-2">
 <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ backgroundColor: seg.color }} />
 <span className="truncate text-text-primary">{seg.label}</span>
 </span>
 <span className="shrink-0 font-semibold tabular-nums text-text-secondary">
 {formatChartNumber(seg.value)} ({Math.round((seg.value / total) * 100)}%)
 </span>
 </li>
 ))}
 </ul>
 </div>
 );
}

function StackedBarChart({ rows, spec }) {
 const metrics = spec.metricColumns || [];
 if (metrics.length < 2) return <HorizontalBars rows={rows} spec={spec} />;

 const items = rows.slice(0, 12).map((row) => {
 const parts = metrics.map((col, i) => ({
 col,
 value: parseNum(row[col]) ?? 0,
 color: i === 0 ? BAR_COLORS[0] : "rgb(14 165 233 / 0.85)",
 }));
 const total = parts.reduce((s, p) => s + p.value, 0);
 return { label: rowLabel(row, spec.dimensionColumns), parts, total };
 });

 const max = Math.max(...items.map((x) => x.total), 1);

 return (
 <div className="space-y-3">
 {items.map((item, i) => (
 <div key={i}>
 <div className="mb-1 flex items-center justify-between gap-2 text-xs">
 <span className="truncate font-medium text-text-primary">{item.label}</span>
 <span className="shrink-0 text-text-secondary">{formatChartNumber(item.total)}</span>
 </div>
 <div className="flex h-3 overflow-hidden rounded-full bg-surface-tertiary">
 {item.parts.map((p, j) =>
 p.value > 0 ? (
 <div
 key={j}
 className="h-full"
 style={{ width: `${(p.value / max) * 100}%`, backgroundColor: p.color }}
 title={`${formatColumnLabel(p.col)}: ${formatChartNumber(p.value)}`}
 />
 ) : null
 )}
 </div>
 </div>
 ))}
 <p className="text-xs text-text-muted">
 {formatColumnLabel(metrics[0])} + {formatColumnLabel(metrics[1])} stacked per row
 </p>
 </div>
 );
}

function ScatterChart({ rows, spec }) {
 const [xCol, yCol] = spec.metricColumns || [];
 const labelCol = spec.dimensionColumns?.[0];
 if (!xCol || !yCol) return <DataTable rows={rows} spec={spec} maxRows={15} />;

 const points = rows
 .map((row) => ({
 label: labelCol ? row[labelCol] : rowLabel(row, spec.dimensionColumns),
 x: parseNum(row[xCol]) ?? 0,
 y: parseNum(row[yCol]) ?? 0,
 }))
 .filter((p) => p.x > 0 || p.y > 0);

 const maxX = Math.max(...points.map((p) => p.x), 1);
 const maxY = Math.max(...points.map((p) => p.y), 1);
 const pad = 8;
 const w = 100 - pad * 2;
 const h = 70 - pad * 2;

 return (
 <div>
 <svg viewBox="0 0 100 80" className="h-52 w-full" role="img" aria-label="Scatter chart">
 <line x1={pad} y1={80 - pad} x2={100 - pad} y2={80 - pad} className="stroke-border" strokeWidth="0.5" />
 <line x1={pad} y1={pad} x2={pad} y2={80 - pad} className="stroke-border" strokeWidth="0.5" />
 {points.map((p, i) => {
 const cx = pad + (p.x / maxX) * w;
 const cy = 80 - pad - (p.y / maxY) * h;
 return <circle key={i} cx={cx} cy={cy} r="2.2" fill="#6941c6" opacity="0.85" />;
 })}
 </svg>
 <div className="mt-1 flex justify-between text-[10px] text-text-muted">
 <span>{formatColumnLabel(xCol)}</span>
 <span>{formatColumnLabel(yCol)}</span>
 </div>
 <ul className="mt-3 max-h-32 space-y-1 overflow-y-auto text-xs text-text-secondary">
 {points.slice(0, 8).map((p, i) => (
 <li key={i} className="truncate">
 {p.label}: {formatChartNumber(p.x)} / {formatChartNumber(p.y)}
 </li>
 ))}
 </ul>
 </div>
 );
}

function StackedAreaChart({ rows, spec }) {
 const weekCol = spec.dimensionColumns?.find((c) => /week/i.test(c)) || spec.dimensionColumns?.[0];
 const seriesCol = spec.dimensionColumns?.find((c) => c !== weekCol) || spec.dimensionColumns?.[1];
 const metricCol = spec.metricColumns?.[0];
 if (!weekCol || !seriesCol || !metricCol) return <LineChart rows={rows} spec={spec} />;

 const weeks = [...new Set(rows.map((r) => String(r[weekCol])))].sort();
 const seriesNames = [...new Set(rows.map((r) => String(r[seriesCol])))];

 const matrix = weeks.map((week) => {
 const rowVals = seriesNames.map((name) => {
 const match = rows.find((r) => String(r[weekCol]) === week && String(r[seriesCol]) === name);
 return parseNum(match?.[metricCol]) ?? 0;
 });
 return { week, values: rowVals };
 });

 const totals = matrix.map((m) => m.values.reduce((s, v) => s + v, 0));
 const maxTotal = Math.max(...totals, 1);
 const h = 120;
 const w = 280;
 const padX = 24;
 const padY = 12;
 const plotW = w - padX * 2;

 const stacks = seriesNames.map((name, si) => {
 const tops = matrix.map((m, wi) => {
 const base = m.values.slice(0, si).reduce((s, v) => s + v, 0);
 const val = m.values[si];
 const x = padX + (wi / Math.max(matrix.length - 1, 1)) * plotW;
 const yBase = padY + h - (base / maxTotal) * h;
 const yTop = padY + h - ((base + val) / maxTotal) * h;
 return { x, yBase, yTop, val };
 });
 return { name, color: BAR_COLORS[si % BAR_COLORS.length], tops };
 });

 return (
 <div>
 <svg viewBox={`0 0 ${w} ${h + padY * 2}`} className="h-44 w-full">
 {stacks.map((stack, si) => {
 const pts = stack.tops;
 const area = pts
 .map((p, i) => `${i === 0 ? "M" : "L"} ${p.x} ${p.yTop}`)
 .join(" ");
 const base = [...pts].reverse().map((p) => `L ${p.x} ${p.yBase}`).join(" ");
 const d = `${area} ${base} Z`;
 return <path key={si} d={d} fill={stack.color} opacity={0.75 - si * 0.08} />;
 })}
 </svg>
 <div className="mt-2 flex flex-wrap gap-3 text-xs text-text-muted">
 {seriesNames.map((name, i) => (
 <span key={name} className="inline-flex items-center gap-1">
 <span className="h-2 w-2 rounded-full" style={{ backgroundColor: BAR_COLORS[i % BAR_COLORS.length] }} />
 {name}
 </span>
 ))}
 </div>
 </div>
 );
}

function LineChart({ rows, spec }) {
 const metric = spec.metricColumns?.[0];
 const dim = spec.dimensionColumns?.[0];
 if (!metric) return null;
 const series = rows.map((r) => parseNum(r[metric]) ?? 0);
 return (
 <div>
 <DemoAreaSpark data={series} viewHeight={120} maxHeight={220} className="w-full" />
 <div className="mt-2 flex flex-wrap gap-2 text-[11px] text-text-muted">
 {rows.slice(0, 8).map((r, i) => (
 <span key={i}>{dim ? r[dim] : `P${i + 1}`}</span>
 ))}
 </div>
 </div>
 );
}

function SlaGaugeChart({ rows }) {
  const summary = summarizeSlaAging(rows);
  const { buckets, thresholdDays, oldest, overSlaCount } = summary;
  const maxCount = Math.max(...buckets.map((b) => b.count), 1);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-3xl font-semibold tabular-nums text-text-primary">{overSlaCount}</p>
          <p className="text-sm text-text-secondary">quotes over {thresholdDays}-day SLA</p>
        </div>
        {oldest ? (
          <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs dark:border-amber-500/30 dark:bg-amber-500/10">
            <span className="font-semibold text-amber-950 dark:text-amber-100">Oldest open: </span>
            <span className="text-amber-900 dark:text-amber-100/90">
              {oldest.brand || "Quote"} · {oldest.days_open} days
            </span>
          </div>
        ) : null}
      </div>

      <div className="grid grid-cols-3 gap-3">
        {buckets.map((bucket, i) => {
          const overThreshold = bucket.min > thresholdDays;
          const nearThreshold = bucket.max >= thresholdDays && bucket.min <= thresholdDays;
          const barColor = overThreshold ? "#f04438" : nearThreshold ? "#f79009" : "#12b76a";
          return (
            <div
              key={bucket.label}
              className="rounded-xl border border-border bg-surface-secondary/50 px-3 py-3"
            >
              <p className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">
                {bucket.label}
              </p>
              <p className="mt-1 text-xl font-semibold tabular-nums text-text-primary">{bucket.count}</p>
              <div className="mt-2 h-2 overflow-hidden rounded-full bg-surface-tertiary">
                <div
                  className="h-full rounded-full transition-all"
                  style={{
                    width: `${Math.round((bucket.count / maxCount) * 100)}%`,
                    backgroundColor: barColor,
                  }}
                />
              </div>
              {i === 1 ? (
                <p className="mt-1 text-[10px] text-text-muted">SLA line at {thresholdDays}d</p>
              ) : null}
            </div>
          );
        })}
      </div>

      <p className="text-xs text-text-muted">
        Days open estimated from HubSpot timeframe when created_date is absent in the export sample.
      </p>
    </div>
  );
}

export default function AnalyticsChartRenderer({
  spec,
  rows,
  blocked,
  blockedMessage,
  onBrandClick,
  onSkuClick,
  releaseRows,
}) {
 if (blocked || spec?.chartType === "blocked_placeholder") {
 return <BlockedView message={blockedMessage || "Data not available for this chart."} />;
 }
 if (!rows?.length) {
 return <p className="text-sm text-text-secondary">No rows in the sample file for this chart.</p>;
 }

 const chartRows = prepareChartRows(rows, spec);
 const collapsedFootnote = topNOtherFootnote(rows, spec);
 const type = spec?.chartType || "table";

 switch (type) {
 case "funnel":
 return <FunnelChart rows={chartRows} spec={spec} />;
 case "bar":
 case "histogram":
 case "grouped_bar":
 return <HorizontalBars rows={chartRows} spec={spec} onBrandClick={onBrandClick} footnote={collapsedFootnote} />;
 case "donut":
 return <DonutChart rows={chartRows} spec={spec} onBrandClick={onBrandClick} />;
 case "stacked_bar":
 return <StackedBarChart rows={chartRows} spec={spec} />;
 case "dual_axis_bar":
 return <HorizontalBars rows={chartRows} spec={spec} metricIndex={0} secondMetricIndex={1} onBrandClick={onBrandClick} footnote={collapsedFootnote} />;
 case "line":
 return <LineChart rows={chartRows} spec={spec} />;
 case "stacked_area":
 return <StackedAreaChart rows={chartRows} spec={spec} />;
 case "scatter":
 return <ScatterChart rows={chartRows} spec={spec} />;
 case "kpi_cards":
 case "comparison_cards":
 return <KpiCards rows={chartRows} />;
 case "alert_list":
 return <AlertList rows={chartRows} />;
 case "journey_timeline":
 return <JourneyTimeline rows={chartRows} spec={spec} />;
 case "heatmap":
 return <HeatmapTable rows={chartRows} spec={spec} />;
 case "sla_gauge":
  return <SlaGaugeChart rows={chartRows} />;
 case "sankey":
  return <SankeyChart rows={chartRows} spec={spec} />;
 case "geo_map":
  return <GeoMapChart rows={chartRows} spec={spec} />;
 case "treemap":
  return <TreemapChart rows={chartRows} spec={spec} />;
 case "deploy_line":
  return <DeployLineChart rows={chartRows} spec={spec} releases={releaseRows || []} />;
 case "priority_table":
 return <DataTable rows={chartRows} spec={spec} priority maxRows={15} onBrandClick={onBrandClick} onSkuClick={onSkuClick} />;
 case "ranked_table":
 case "table":
 case "link_table":
 case "recommendation_table":
 return <DataTable rows={chartRows} spec={spec} maxRows={15} onBrandClick={onBrandClick} onSkuClick={onSkuClick} />;
 default:
 return <DataTable rows={chartRows} spec={spec} onBrandClick={onBrandClick} onSkuClick={onSkuClick} />;
 }
}
