// Wraps a chart with a title, description, and pin-to-report affordance.
// Reuses the existing chart primitives so the demo stays visually consistent.
//
// Supported chartKind values: "spark", "bar", "line", "funnel", "pie",
// "sankey", "treemap". Unknown kinds fall back to a tabular value list so the
// content never disappears from the UI.

import { useMemo } from "react";
import { DemoAreaSpark, formatChartNumber } from "../../../shared/DemoSparkCharts";
import { SankeyChart, TreemapChart, DeployLineChart } from "../../../middleby/MiddlebyExtendedCharts";
import BlockShell from "../BlockShell";

const BAR_COLORS = ["#6941c6", "#7f56d9", "#9e77ed", "#b692f6", "#d6bbfb", "#44337a"];

function toNumber(v) {
  if (typeof v === "number") return v;
  if (typeof v === "string") {
    const cleaned = v.replace(/[,%$]/g, "");
    const n = Number(cleaned);
    return Number.isFinite(n) ? n : 0;
  }
  return 0;
}

function SimpleBar({ data, xKey, yKey, height = 180 }) {
  const rows = data.map((row) => ({ label: String(row[xKey] ?? ""), value: toNumber(row[yKey]) }));
  const max = Math.max(1, ...rows.map((r) => r.value));
  // A percentage height only resolves against a parent with an explicit (non-percentage,
  // non-auto) height -- the per-bar wrapper here is an auto-sized flex column, so `height:
  // X%` on the bar itself computed against nothing and every bar silently collapsed to the
  // 4px `minHeight` floor, whatever its real value. Observed directly: a real 10-brand
  // dataset with values from 295 to 938 rendered as ten identical flat lines. Reserving a
  // fixed pixel height for the bar area and computing each bar's height in pixels, in JS,
  // sidesteps the whole percentage-resolution question rather than fighting it in CSS.
  const labelHeight = 28;
  const barAreaHeight = Math.max(20, height - labelHeight);
  return (
    <div style={{ height }}>
      <div className="flex items-end gap-2" style={{ height: barAreaHeight }}>
        {rows.map((r, i) => (
          <div
            key={`${r.label}-${i}`}
            className="flex flex-1 min-w-0 flex-col justify-end"
            style={{ height: barAreaHeight }}
          >
            <div
              className="w-full rounded-t transition-all"
              style={{
                height: Math.max(4, (r.value / max) * barAreaHeight),
                backgroundColor: BAR_COLORS[i % BAR_COLORS.length],
              }}
              title={`${r.label}: ${formatChartNumber(r.value)}`}
              aria-label={`${r.label}: ${formatChartNumber(r.value)}`}
            />
          </div>
        ))}
      </div>
      <div className="mt-1 flex gap-2">
        {rows.map((r, i) => (
          <span
            key={`${r.label}-${i}`}
            className="flex-1 min-w-0 truncate text-center text-[10px] text-text-muted"
            title={r.label}
          >
            {r.label}
          </span>
        ))}
      </div>
    </div>
  );
}

function SimpleFunnel({ data, labelKey, valueKey }) {
  const rows = data.map((row) => ({
    label: String(row[labelKey] ?? ""),
    value: toNumber(row[valueKey]),
  }));
  const first = rows[0]?.value || 1;
  return (
    <div className="space-y-2">
      {rows.map((r, i) => {
        const pct = Math.max(6, (r.value / first) * 100);
        const dropOff = i > 0 ? rows[i - 1].value - r.value : 0;
        return (
          <div key={`${r.label}-${i}`} className="flex items-center gap-3">
            <div className="w-40 truncate text-xs text-text-secondary">{r.label}</div>
            <div className="relative flex-1 rounded-full bg-surface-secondary" style={{ height: 20 }}>
              <div
                className="absolute inset-y-0 left-0 rounded-full"
                style={{ width: `${pct}%`, backgroundColor: BAR_COLORS[i % BAR_COLORS.length] }}
              />
              <span className="absolute inset-y-0 left-3 flex items-center text-[11px] font-medium text-text-primary">
                {formatChartNumber(r.value)}
              </span>
            </div>
            {dropOff > 0 ? (
              <div className="w-24 text-right text-[11px] text-status-critical">
                Dropped {formatChartNumber(dropOff)}
              </div>
            ) : (
              <div className="w-24" />
            )}
          </div>
        );
      })}
    </div>
  );
}

function SimplePie({ data, labelKey, valueKey, size = 160 }) {
  const rows = data.map((row) => ({
    label: String(row[labelKey] ?? ""),
    value: toNumber(row[valueKey]),
  }));
  const total = rows.reduce((s, r) => s + r.value, 0) || 1;
  let acc = 0;
  const radius = size / 2 - 6;
  const cx = size / 2;
  const cy = size / 2;

  return (
    <div className="flex items-center gap-4">
      <svg width={size} height={size} role="img" aria-label="Distribution chart">
        {rows.map((r, i) => {
          const start = (acc / total) * Math.PI * 2 - Math.PI / 2;
          acc += r.value;
          const end = (acc / total) * Math.PI * 2 - Math.PI / 2;
          const x1 = cx + radius * Math.cos(start);
          const y1 = cy + radius * Math.sin(start);
          const x2 = cx + radius * Math.cos(end);
          const y2 = cy + radius * Math.sin(end);
          const largeArc = end - start > Math.PI ? 1 : 0;
          const d = `M ${cx},${cy} L ${x1},${y1} A ${radius},${radius} 0 ${largeArc} 1 ${x2},${y2} Z`;
          return <path key={`${r.label}-${i}`} d={d} fill={BAR_COLORS[i % BAR_COLORS.length]} />;
        })}
      </svg>
      <div className="min-w-0 flex-1 space-y-1">
        {rows.map((r, i) => (
          <div key={`${r.label}-${i}`} className="flex items-center gap-2 text-xs text-text-secondary">
            <span
              className="h-2 w-2 shrink-0 rounded-full"
              style={{ backgroundColor: BAR_COLORS[i % BAR_COLORS.length] }}
              aria-hidden
            />
            <span className="min-w-0 flex-1 truncate">{r.label}</span>
            <span className="font-medium text-text-primary">
              {Math.round((r.value / total) * 100)}%
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

/**
 * A real line chart: every point plotted, labeled by its own `label` (a month, a week,
 * whatever the data actually names), hover shows that label -- not `DemoAreaSpark`'s
 * generic "WEEK N", which is a different component built for a small inline glyph with no
 * per-point labels or ticks. `chartKind: "spark"` still uses that one; `"line"` is the
 * user's explicit ask for a real trend chart and needs its own.
 */
function SimpleLine({ data, xKey, yKey, height = 180 }) {
  const rows = data.map((row) => ({ label: String(row[xKey] ?? ""), value: toNumber(row[yKey]) }));
  const values = rows.map((r) => r.value);
  const max = Math.max(...values);
  const min = Math.min(0, ...values);
  const range = max - min || 1;
  const width = 100;
  const step = rows.length > 1 ? width / (rows.length - 1) : 0;
  const points = rows.map((r, i) => ({
    ...r,
    x: rows.length > 1 ? i * step : width / 2,
    y: 100 - ((r.value - min) / range) * 100,
  }));
  const path = points.map((p) => `${p.x},${p.y}`).join(" ");
  return (
    <div style={{ height }}>
      <svg
        viewBox="0 0 100 100"
        preserveAspectRatio="none"
        className="h-[calc(100%-20px)] w-full overflow-visible"
        role="img"
        aria-label="Trend chart"
      >
        <polyline points={path} fill="none" stroke={BAR_COLORS[0]} strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
        {points.map((p, i) => (
          <circle key={`${p.label}-${i}`} cx={p.x} cy={p.y} r="2" fill={BAR_COLORS[0]} vectorEffect="non-scaling-stroke">
            <title>{`${p.label}: ${formatChartNumber(p.value)}`}</title>
          </circle>
        ))}
      </svg>
      <div className="mt-1 flex justify-between" style={{ height: 20 }}>
        {rows.map((r, i) => (
          <span
            key={`${r.label}-${i}`}
            className="flex-1 truncate text-center text-[10px] text-text-muted"
            title={`${r.label}: ${formatChartNumber(r.value)}`}
          >
            {r.label}
          </span>
        ))}
      </div>
    </div>
  );
}

function renderChart(block) {
  const kind = block.chartKind;
  const cfg = block.config || {};
  const data = Array.isArray(block.data) ? block.data : [];

  if (!data.length) {
    return <p className="text-sm text-text-muted">No data available for this view.</p>;
  }

  if (kind === "spark") {
    const yKey = cfg.yKey || Object.keys(data[0] || {}).find((k) => typeof data[0][k] === "number") || "value";
    const series = data.map((row) => toNumber(row[yKey]));
    return (
      <DemoAreaSpark
        data={series}
        height={cfg.height || 120}
        color={cfg.color}
      />
    );
  }
  if (kind === "line") {
    return <SimpleLine data={data} xKey={cfg.xKey || "label"} yKey={cfg.yKey || "value"} height={cfg.height} />;
  }
  if (kind === "bar") {
    return <SimpleBar data={data} xKey={cfg.xKey || "label"} yKey={cfg.yKey || "value"} height={cfg.height} />;
  }
  if (kind === "funnel") {
    return <SimpleFunnel data={data} labelKey={cfg.xKey || "stage"} valueKey={cfg.yKey || "value"} />;
  }
  if (kind === "pie") {
    return <SimplePie data={data} labelKey={cfg.xKey || "label"} valueKey={cfg.yKey || "value"} />;
  }
  if (kind === "sankey") {
    return <SankeyChart rows={data} spec={cfg} />;
  }
  if (kind === "treemap") {
    return <TreemapChart rows={data} spec={cfg} />;
  }
  if (kind === "release") {
    return <DeployLineChart rows={data} spec={cfg} releases={cfg.releases || []} />;
  }

  // Unknown kind → tabular fallback so the block still renders.
  const keys = Object.keys(data[0] || {}).slice(0, 4);
  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="min-w-full divide-y divide-border text-left text-sm">
        <thead className="bg-surface-secondary">
          <tr>
            {keys.map((k) => (
              <th key={k} className="px-3 py-2 font-semibold text-text-secondary">
                {k}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {data.slice(0, 8).map((row, i) => (
            <tr key={i}>
              {keys.map((k) => (
                <td key={k} className="px-3 py-2 text-text-primary">
                  {typeof row[k] === "number" ? formatChartNumber(row[k]) : String(row[k])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function ChartBlock({ block, onPinToggle }) {
  const rendered = useMemo(() => renderChart(block), [block]);
  return (
    <BlockShell
      block={block}
      title={block.title}
      subtitle={block.description}
      onPinToggle={onPinToggle}
    >
      {rendered}
    </BlockShell>
  );
}
