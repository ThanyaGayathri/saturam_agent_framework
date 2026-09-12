import { parseNum } from "../../domain/middleby-analytics-data";
import { DemoAreaSpark, formatChartNumber } from "../shared/DemoSparkCharts";

const BAR_COLORS = ["#6941c6", "#7f56d9", "#9e77ed", "#b692f6", "#d6bbfb", "#44337a"];

function formatColumnLabel(col) {
  return String(col || "")
    .replace(/_example/g, "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Simplified sankey: top brand-pair flows as proportional bars. */
export function SankeyChart({ rows, spec }) {
  const sourceCol = spec?.dimensionColumns?.[0] || "brand_a";
  const targetCol = spec?.dimensionColumns?.[1] || "brand_b";
  const metricCol = spec?.metricColumns?.[0] || "session_count";
  const flows = [...rows]
    .map((r) => ({
      source: r[sourceCol],
      target: r[targetCol],
      value: parseNum(r[metricCol]) ?? 0,
    }))
    .filter((f) => f.source && f.target && f.value > 0)
    .sort((a, b) => b.value - a.value)
    .slice(0, 8);
  const max = Math.max(...flows.map((f) => f.value), 1);

  return (
    <div className="space-y-3">
      {flows.map((f, i) => (
        <div key={`${f.source}-${f.target}-${i}`} className="space-y-1">
          <div className="flex items-center justify-between text-xs">
            <span className="font-medium text-text-primary">
              {f.source} → {f.target}
            </span>
            <span className="tabular-nums text-text-muted">{f.value} sessions</span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-surface-tertiary">
            <div
              className="h-full rounded-full"
              style={{
                width: `${Math.round((f.value / max) * 100)}%`,
                backgroundColor: BAR_COLORS[i % BAR_COLORS.length],
              }}
            />
          </div>
        </div>
      ))}
      <p className="text-xs text-text-muted">Multi-brand browse overlap in the sample window.</p>
    </div>
  );
}

/** Geo map proxy: ranked regions/states as bars (no map library). */
export function GeoMapChart({ rows, spec }) {
  const regionCol =
    spec?.dimensionColumns?.find((c) => /state|zip|region/i.test(c)) ||
    spec?.dimensionColumns?.[0] ||
    "state";
  const metricCol = spec?.metricColumns?.[0] || "quote_count";
  const ranked = [...rows]
    .map((r) => ({
      label: r[regionCol],
      value: parseNum(r[metricCol]) ?? 0,
    }))
    .filter((r) => r.label)
    .sort((a, b) => b.value - a.value)
    .slice(0, 12);
  const max = Math.max(...ranked.map((r) => r.value), 1);

  return (
    <div className="space-y-2">
      {ranked.map((r, i) => (
        <div key={`${r.label}-${i}`} className="flex items-center gap-3 text-sm">
          <span className="w-24 shrink-0 truncate font-medium text-text-primary">{r.label}</span>
          <div className="min-w-0 flex-1">
            <div className="h-2 overflow-hidden rounded-full bg-surface-tertiary">
              <div
                className="h-full rounded-full bg-primary/80"
                style={{ width: `${Math.round((r.value / max) * 100)}%` }}
              />
            </div>
          </div>
          <span className="w-8 shrink-0 text-right tabular-nums text-text-muted">{r.value}</span>
        </div>
      ))}
      <p className="text-xs text-text-muted">Quote volume by delivery region from HubSpot sample.</p>
    </div>
  );
}

/** Treemap proxy: nested rectangles by L1/L2 size. */
export function TreemapChart({ rows, spec }) {
  const l1Col = spec?.dimensionColumns?.find((c) => /l1/i.test(c)) || spec?.dimensionColumns?.[0];
  const l2Col = spec?.dimensionColumns?.find((c) => /l2/i.test(c)) || spec?.dimensionColumns?.[1];
  const metricCol =
    spec?.metricColumns?.find((c) => /revenue|sessions|quotes/i.test(c)) || spec?.metricColumns?.[0];
  const items = [...rows]
    .map((r) => ({
      l1: r[l1Col],
      l2: r[l2Col],
      value: parseNum(r[metricCol]) ?? 0,
      label: l2Col ? `${r[l1Col]} · ${r[l2Col]}` : r[l1Col],
    }))
    .filter((r) => r.value > 0)
    .sort((a, b) => b.value - a.value)
    .slice(0, 10);
  const total = items.reduce((s, x) => s + x.value, 0) || 1;

  return (
    <div className="flex flex-wrap gap-2">
      {items.map((item, i) => {
        const pct = Math.max(12, Math.round((item.value / total) * 100));
        return (
          <div
            key={`${item.label}-${i}`}
            className="flex min-h-[4rem] min-w-[8rem] flex-col justify-between rounded-lg border border-border px-3 py-2"
            style={{
              flexGrow: pct,
              flexBasis: `${Math.min(pct, 40)}%`,
              backgroundColor: `${BAR_COLORS[i % BAR_COLORS.length]}22`,
            }}
          >
            <span className="text-[11px] font-medium leading-tight text-text-primary">{item.label}</span>
            <span className="text-sm font-semibold tabular-nums text-text-primary">
              {formatChartNumber(item.value)}
            </span>
          </div>
        );
      })}
      <p className="w-full text-xs text-text-muted">
        Sized by {formatColumnLabel(metricCol)}. Estimated revenue uses quote count × catalog median list price.
      </p>
    </div>
  );
}

/** Line chart with vertical deploy markers from release_calendar rows. */
export function DeployLineChart({ rows, spec, releases = [] }) {
  const weekCol =
    spec?.dimensionColumns?.find((c) => /week|date/i.test(c)) || spec?.dimensionColumns?.[0];
  const metricCol = spec?.metricColumns?.[0] || "value";
  const series = rows
    .map((r) => ({ week: String(r[weekCol] || ""), value: parseNum(r[metricCol]) ?? 0 }))
    .filter((r) => r.week);
  const values = series.map((s) => s.value);
  const releaseWeeks = releases.map((r) => String(r.release_date || r.release_week || ""));

  return (
    <div>
      <DemoAreaSpark data={values.length ? values : [0]} viewHeight={120} maxHeight={220} className="w-full" />
      <div className="mt-3 flex flex-wrap gap-2">
        {series.slice(0, 8).map((s) => (
          <span
            key={s.week}
            className={`rounded-md px-2 py-0.5 text-[10px] font-medium ${
              releaseWeeks.some((rw) => s.week.startsWith(rw.slice(0, 7)))
                ? "bg-amber-100 text-amber-900 dark:bg-amber-500/20 dark:text-amber-100"
                : "bg-surface-secondary text-text-muted"
            }`}
          >
            {s.week.slice(5)}: {formatChartNumber(s.value)}
          </span>
        ))}
      </div>
      {releases.length > 0 ? (
        <ul className="mt-3 space-y-1 text-xs text-text-secondary">
          {releases.slice(0, 6).map((r, i) => (
            <li key={i}>
              <span className="font-semibold text-amber-800 dark:text-amber-200">
                {r.release_tier === "minor" ? "Patch " : "Deploy "}
              </span>
              {r.release_date || r.release_week}: {r.label || r.version}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
