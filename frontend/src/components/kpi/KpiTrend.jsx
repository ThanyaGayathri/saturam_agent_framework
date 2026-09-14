import { useRef, useState } from "react";
import { AlertTriangle, Loader2 } from "lucide-react";
import { smoothLinePath } from "../shared/DemoSparkCharts";

/**
 * One registry KPI's value over its last 30 business dates, from gold (`GET /kpis/{id}/series`),
 * inside the detail panel. Every point is a real nightly materialisation of the KPI's own
 * formula; nothing here is illustrative and nothing is computed at render time.
 *
 * Unlike `DemoSparkCharts`, which spaces points evenly by index, this plots by *date*: the
 * x-axis is the whole window and a night with no row is simply absent, so a KPI that was
 * computed on two of the last thirty nights shows two dots thirty days' worth apart, not two
 * dots filling the width as if the history were complete.
 *
 * `state` is `{status, data, error, retry}` as `KpiDiscoveryPage.jsx` keeps it per identifier:
 * `loading` (the series fetch), `computing` (`POST /kpis/{id}/compute` in flight -- what "Add
 * to my metrics" starts so a new KPI has today's point at once), `ready` (`data` is the
 * `KpiSeries`), `error`. A `ready` series with no points is the api's honest "not computed
 * yet", and offers the compute rather than a blank.
 */

const VIEW_W = 320;
const VIEW_H = 128;
const PLOT = { x0: 40, x1: 312, y0: 12, y1: 98 };
const X_CAPTION_Y = 120;
const DAY_MS = 24 * 60 * 60 * 1000;
const STROKE = "#6941c6";
const MAX_CHIPS = 6;

const DATE_FORMAT = { month: "short", day: "numeric", timeZone: "UTC" };

/** A business date stamp (`...T23:59:59Z`) as "Sep 14". UTC on purpose: the stamp is the end
 * of a UTC business date, and a viewer west of Greenwich would otherwise read it a day early. */
export function formatBusinessDate(at) {
  if (!at) return "";
  const d = new Date(at);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString(undefined, DATE_FORMAT);
}

/** The KPI's value in its unit. Mirrors `DashboardView.jsx`'s `formatValue` for `%`; counts get
 * thousands separators; anything else keeps at most two decimals. */
export function formatKpiValue(unit, value) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const u = String(unit || "").toLowerCase();
  if (u.includes("%") || u.includes("percent")) return `${value.toFixed(1)}%`;
  return new Intl.NumberFormat(undefined, {
    maximumFractionDigits: Number.isInteger(value) ? 0 : 2,
  }).format(value);
}

/** Compact axis label: 1284 -> "1.3k", 0.67 -> "0.67". */
function formatAxis(value) {
  if (Math.abs(value) >= 1000) {
    return new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(value);
  }
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(value);
}

function sameDimension(a, b) {
  if (!a || !b) return !a && !b;
  const ka = Object.keys(a);
  const kb = Object.keys(b);
  return ka.length === kb.length && ka.every((k) => String(a[k]) === String(b[k]));
}

function dimensionLabel(dimension) {
  return Object.values(dimension || {})
    .map((v) => (v === null || v === undefined ? "—" : String(v)))
    .join(" · ");
}

/** The dated line: x by date across the window ending at `asOf`, y by value. */
function TrendChart({ points, unit, asOf, days }) {
  const ref = useRef(null);
  const [hover, setHover] = useState(null);

  const end = new Date(asOf).getTime();
  const start = end - Math.max(days - 1, 1) * DAY_MS;
  const plotted = points
    .filter((p) => p.value !== null && p.value !== undefined)
    .map((p) => ({ ...p, t: new Date(p.at).getTime() }))
    .filter((p) => !Number.isNaN(p.t));
  const values = plotted.map((p) => p.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const flat = max === min;
  const span = flat ? 1 : max - min;
  const midY = (PLOT.y0 + PLOT.y1) / 2;
  const pts = plotted.map((p) => ({
    x: PLOT.x0 + (Math.min(Math.max(p.t, start), end) - start) / (end - start || 1) * (PLOT.x1 - PLOT.x0),
    y: flat ? midY : PLOT.y1 - ((p.value - min) / span) * (PLOT.y1 - PLOT.y0),
    point: p,
  }));
  const line = pts.length > 1 ? smoothLinePath(pts) : "";
  const area = line ? `${line} L ${pts[pts.length - 1].x},${PLOT.y1} L ${pts[0].x},${PLOT.y1} Z` : "";
  const yTicks = flat
    ? [{ y: midY, label: formatAxis(min) }]
    : [
        { y: PLOT.y0, label: formatAxis(max) },
        { y: midY, label: formatAxis((max + min) / 2) },
        { y: PLOT.y1, label: formatAxis(min) },
      ];
  const xTicks = [
    { x: PLOT.x0, anchor: "start", label: formatBusinessDate(new Date(start).toISOString()) },
    { x: (PLOT.x0 + PLOT.x1) / 2, anchor: "middle", label: formatBusinessDate(new Date((start + end) / 2).toISOString()) },
    { x: PLOT.x1, anchor: "end", label: formatBusinessDate(asOf) },
  ];

  const onMove = (e) => {
    const el = ref.current;
    if (!el || pts.length === 0) return;
    const r = el.getBoundingClientRect();
    if (r.width <= 0) return;
    const vx = ((e.clientX - r.left) / r.width) * VIEW_W;
    let best = pts[0];
    for (const p of pts) if (Math.abs(p.x - vx) < Math.abs(best.x - vx)) best = p;
    setHover(best);
  };

  return (
    <div
      ref={ref}
      className="relative w-full min-w-0"
      onMouseMove={onMove}
      onMouseLeave={() => setHover(null)}
      data-testid="kpi-trend-chart"
    >
      <svg
        viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
        width="100%"
        className="block overflow-visible"
        preserveAspectRatio="xMidYMid meet"
        role="img"
        aria-label={`${pts.length} nightly value${pts.length === 1 ? "" : "s"} over the last ${days} days`}
      >
        {yTicks.map((t) => (
          <g key={t.y}>
            <line x1={PLOT.x0} x2={PLOT.x1} y1={t.y} y2={t.y} stroke="currentColor" className="text-border" strokeWidth="0.75" strokeDasharray="2 3" />
            <text x={PLOT.x0 - 6} y={t.y + 3} textAnchor="end" fontSize="9" fill="#717680">{t.label}</text>
          </g>
        ))}
        {xTicks.map((t) => (
          <text key={t.x} x={t.x} y={X_CAPTION_Y} textAnchor={t.anchor} fontSize="9" fill="#717680">{t.label}</text>
        ))}
        {area ? <path d={area} fill={STROKE} fillOpacity="0.12" /> : null}
        {line ? <path d={line} fill="none" stroke={STROKE} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /> : null}
        {pts.map((p) => (
          <circle
            key={p.point.at}
            cx={p.x}
            cy={p.y}
            r={hover?.point.at === p.point.at ? 3.6 : 2.6}
            fill={STROKE}
            stroke="#ffffff"
            strokeWidth="1.2"
          />
        ))}
        {hover ? (
          <line x1={hover.x} x2={hover.x} y1={PLOT.y0} y2={PLOT.y1} stroke={STROKE} strokeOpacity="0.4" strokeWidth="0.75" />
        ) : null}
      </svg>
      {hover ? (
        <div
          role="tooltip"
          className="pointer-events-none absolute z-20 whitespace-nowrap rounded-md bg-[#181d27] px-2.5 py-1.5 text-[11px] font-medium leading-snug text-white shadow-lg ring-1 ring-black/10"
          style={{
            left: `${(hover.x / VIEW_W) * 100}%`,
            top: `${(hover.y / VIEW_H) * 100}%`,
            transform: "translate(-50%, calc(-100% - 8px))",
          }}
        >
          {formatBusinessDate(hover.point.at)} · {formatKpiValue(unit, hover.point.value)}
        </div>
      ) : null}
    </div>
  );
}

function Skeleton() {
  return (
    <div className="mt-3 space-y-2" aria-hidden>
      <div className="h-7 w-24 animate-pulse rounded-md bg-surface-secondary" />
      <div className="h-[132px] w-full animate-pulse rounded-lg bg-surface-secondary" />
    </div>
  );
}

const STATUS_NOTE = {
  never_computed: "No values yet. Gold hasn't computed this KPI.",
  failed: "Gold couldn't read this KPI's values.",
  stale: "The latest value is stale.",
  not_scheduled: "This KPI is not on the nightly schedule.",
};

export default function KpiTrend({ state, unit, resultShape, onRetry, onCompute, onPickDimension }) {
  const status = state?.status || "loading";
  const data = state?.data || null;
  const points = data?.points || [];
  const hasPoints = points.some((p) => p.value !== null && p.value !== undefined);
  const dimensions = data?.dimensions || [];
  const valueUnit = data?.unit || unit;

  let body;
  if (resultShape === "rowset") {
    body = (
      <p className="mt-3 rounded-xl border border-dashed border-border-light bg-surface-secondary px-4 py-3 text-xs text-text-secondary">
        This KPI returns rows, not a single value, so there is no line to draw. Put it on a dashboard as a table to see them.
      </p>
    );
  } else if (status === "loading") {
    body = <Skeleton />;
  } else if (status === "computing") {
    body = (
      <>
        <p className="mt-3 flex items-center gap-2 text-sm text-text-secondary" role="status">
          <Loader2 size={14} className="shrink-0 animate-spin text-primary" />
          Computing today's value…
        </p>
        <Skeleton />
      </>
    );
  } else if (status === "error") {
    body = (
      <div className="mt-3">
        <p className="flex items-start gap-2 text-xs text-status-critical" role="alert">
          <AlertTriangle size={13} className="mt-0.5 shrink-0" />
          <span>
            {state?.retry === "compute" ? "Couldn't compute it: " : "Couldn't load values: "}
            {state?.error || "something went wrong."}
          </span>
        </p>
        {onRetry ? (
          <button type="button" onClick={onRetry} className="mt-2 text-xs font-semibold text-primary hover:underline">
            Try again
          </button>
        ) : null}
      </div>
    );
  } else if (!hasPoints) {
    body = (
      <div className="mt-3 rounded-xl border border-dashed border-border-light bg-surface-secondary px-4 py-6 text-center">
        <p className="text-sm text-text-muted">{STATUS_NOTE[data?.status] || STATUS_NOTE.never_computed}</p>
        {onCompute && (data?.status === "never_computed" || !data?.status) ? (
          <button type="button" onClick={onCompute} className="mt-2 text-sm font-semibold text-primary hover:underline">
            Compute today's value
          </button>
        ) : null}
      </div>
    );
  } else {
    const delta = data.delta;
    const single = points.filter((p) => p.value !== null && p.value !== undefined).length === 1;
    body = (
      <>
        <div className="mt-2 flex items-baseline gap-2">
          <span className="text-2xl font-bold tracking-tight text-text-primary">{formatKpiValue(valueUnit, data.value)}</span>
          {delta === null || delta === undefined ? (
            <span className="text-xs font-semibold text-text-muted" title="No previous night to compare with">—</span>
          ) : (
            <span className={`text-xs font-semibold ${delta > 0 ? "text-status-success" : delta < 0 ? "text-status-critical" : "text-text-muted"}`}>
              {delta > 0 ? "▲" : delta < 0 ? "▼" : "•"} {formatKpiValue(valueUnit, Math.abs(delta))} vs previous night
            </span>
          )}
        </div>
        {dimensions.length > 1 ? (
          <div className="mt-2 flex flex-wrap items-center gap-1.5" role="group" aria-label="Dimension">
            {dimensions.slice(0, MAX_CHIPS).map((d) => {
              const selected = sameDimension(d, data.dimension);
              return (
                <button
                  key={dimensionLabel(d)}
                  type="button"
                  aria-pressed={selected}
                  onClick={() => (!selected && onPickDimension ? onPickDimension(d) : undefined)}
                  className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold transition-colors ${
                    selected
                      ? "border-primary bg-primary-light text-primary dark:bg-primary/20"
                      : "border-border bg-surface text-text-secondary hover:border-border-light hover:text-text-primary"
                  }`}
                >
                  {dimensionLabel(d)}
                </button>
              );
            })}
            {dimensions.length > MAX_CHIPS ? (
              <span className="text-[11px] text-text-muted">+{dimensions.length - MAX_CHIPS} more</span>
            ) : null}
          </div>
        ) : null}
        <div className="mt-3">
          <TrendChart points={points} unit={valueUnit} asOf={data.as_of} days={data.days || 30} />
        </div>
        <p className="mt-2 text-[11px] text-text-muted">
          {single
            ? "First value computed. The line fills in one point per nightly run."
            : "One point per nightly run. Tonight's run adds the next."}
          {data.provenance_ref ? (
            <>
              {" "}· <span className="font-mono">{data.provenance_ref}</span>
            </>
          ) : null}
        </p>
      </>
    );
  }

  return (
    <section data-testid="kpi-trend">
      <div className="flex items-baseline justify-between gap-2">
        <p className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">Value · last {data?.days || 30} days</p>
        {data?.as_of ? (
          <p className="text-[11px] text-text-muted">Nightly · as of {formatBusinessDate(data.as_of)}</p>
        ) : null}
      </div>
      {body}
    </section>
  );
}
