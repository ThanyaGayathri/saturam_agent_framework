import { useCallback, useId, useMemo, useRef, useState } from "react";

const VIEW_W = 100;

const DEFAULT_X_INSET = 0;

/** Default visible formatter for tooltip lines */
export function formatChartNumber(v) {
  if (typeof v !== "number" || Number.isNaN(v)) return String(v);
  return Number.isInteger(v) ? String(v) : v.toFixed(1).replace(/\.0$/, "");
}

export function normalizeSeriesYs(data, padTop = 8, padBottom = 92, xInset = DEFAULT_X_INSET) {
  if (!data?.length) return [];
  const max = Math.max(...data);
  const min = Math.min(...data);
  const span = max - min || 1;
  const x0 = xInset;
  const x1 = VIEW_W - xInset;
  return data.map((v, i) => ({
    x: data.length <= 1 ? VIEW_W / 2 : x0 + (i / (data.length - 1)) * (x1 - x0),
    y: padTop + ((max - v) / span) * (padBottom - padTop),
  }));
}

/** Same as normalizeSeriesYs but plot width is explicit (for charts with axis gutters). */
export function normalizeSeriesYsInPlot(data, padTop, padBottom, plotX0, plotX1) {
  if (!data?.length) return [];
  const max = Math.max(...data);
  const min = Math.min(...data);
  const span = max - min || 1;
  const x0 = plotX0;
  const x1 = plotX1;
  return data.map((v, i) => ({
    x: data.length <= 1 ? (x0 + x1) / 2 : x0 + (i / (data.length - 1)) * (x1 - x0),
    y: padTop + ((max - v) / span) * (padBottom - padTop),
  }));
}

export function smoothLinePath(pts) {
  if (!pts.length) return "";
  if (pts.length === 1) return `M ${pts[0].x},${pts[0].y}`;
  if (pts.length === 2) return `M ${pts[0].x},${pts[0].y} L ${pts[1].x},${pts[1].y}`;
  let d = `M ${pts[0].x},${pts[0].y}`;
  for (let i = 0; i < pts.length - 1; i += 1) {
    const p0 = pts[Math.max(0, i - 1)];
    const p1 = pts[i];
    const p2 = pts[i + 1];
    const p3 = pts[Math.min(pts.length - 1, i + 2)];
    const cp1x = p1.x + (p2.x - p0.x) / 7;
    const cp1y = p1.y + (p2.y - p0.y) / 7;
    const cp2x = p2.x - (p3.x - p1.x) / 7;
    const cp2y = p2.y - (p3.y - p1.y) / 7;
    d += ` C ${cp1x},${cp1y} ${cp2x},${cp2y} ${p2.x},${p2.y}`;
  }
  return d;
}

function closedAreaUnderPath(linePath, pts, bottomY) {
  if (!linePath || pts.length < 2) return "";
  const first = pts[0];
  const last = pts[pts.length - 1];
  return `${linePath} L ${last.x},${bottomY} L ${first.x},${bottomY} Z`;
}

function gridLinesY(viewHeight) {
  const y1 = Math.round(viewHeight * 0.24 * 10) / 10;
  const y2 = Math.round(viewHeight * 0.5 * 10) / 10;
  const y3 = Math.round(viewHeight * 0.76 * 10) / 10;
  return [y1, y2, y3];
}

function defaultCaption(i, n) {
  if (n <= 8) return `Week ${i + 1}`;
  return `Period ${i + 1}`;
}

/**
 * Fills parent width; height follows aspect ratio so charts scale uniformly on resize.
 */
export function ResponsiveChartBox({
  viewWidth = VIEW_W,
  viewHeight,
  maxHeight,
  className = "",
  children,
}) {
  return (
    <div
      className={`relative w-full min-w-0 max-w-full ${className}`}
      style={{
        width: "100%",
        aspectRatio: `${viewWidth} / ${viewHeight}`,
        maxHeight: maxHeight != null ? maxHeight : undefined,
      }}
    >
      <svg
        viewBox={`0 0 ${viewWidth} ${viewHeight}`}
        width="100%"
        height="100%"
        className="absolute inset-0 block overflow-visible"
        preserveAspectRatio="xMidYMid meet"
        shapeRendering="geometricPrecision"
      >
        {children}
      </svg>
    </div>
  );
}

/** @deprecated Use ResponsiveChartBox */
export const SparkUniformFrame = ResponsiveChartBox;

export function ChartHoverWrap({ dataLength, renderTooltip, children }) {
  const ref = useRef(null);
  const [tip, setTip] = useState(null);
  const rtRef = useRef(renderTooltip);
  rtRef.current = renderTooltip;

  const onMove = useCallback(
    (e) => {
      const el = ref.current;
      if (!el || dataLength < 1) return;
      const r = el.getBoundingClientRect();
      if (r.width <= 0) return;
      const pctRaw = ((e.clientX - r.left) / r.width) * 100;
      const pctClamped = Math.max(0, Math.min(100, pctRaw));
      const idx =
        dataLength <= 1 ? 0 : Math.min(dataLength - 1, Math.round((pctClamped / 100) * (dataLength - 1)));
      const pctX = dataLength <= 1 ? 50 : (idx / (dataLength - 1)) * 100;
      setTip({ pctX, node: rtRef.current(idx) });
    },
    [dataLength],
  );

  const onLeave = useCallback(() => setTip(null), []);

  return (
    <div ref={ref} className="relative w-full min-w-0" onMouseMove={onMove} onMouseLeave={onLeave}>
      {children}
      {tip ? (
        <>
          <div
            className="pointer-events-none absolute bottom-0 top-0 z-[15] w-px bg-primary/40"
            style={{ left: `${tip.pctX}%`, transform: "translateX(-50%)" }}
            aria-hidden
          />
          <div
            role="tooltip"
            className="pointer-events-none absolute z-20 whitespace-nowrap rounded-md bg-[#181d27] px-2.5 py-1.5 text-[11px] font-medium leading-snug text-white shadow-lg ring-1 ring-black/10"
            style={{
              left: `${tip.pctX}%`,
              bottom: "100%",
              transform: "translate(-50%, -8px)",
            }}
          >
            {tip.node}
          </div>
        </>
      ) : null}
    </div>
  );
}

export function DemoAreaSpark({
  data,
  viewHeight = 48,
  maxHeight = 240,
  stroke = "#6941c6",
  fillOpacityTop = 0.2,
  fillOpacityBottom = 0,
  strokeWidth = 2.75,
  showGrid = true,
  showDots = true,
  dotRadius = 2.1,
  dotStrokeWidth = 1.35,
  padTop: padTopProp,
  padBottom: padBottomProp,
  className = "",
  formatPoint = formatChartNumber,
  captionForIndex = defaultCaption,
}) {
  const uid = useId().replace(/:/g, "");
  const gradId = `demo-area-${uid}`;
  const gridYs = gridLinesY(viewHeight);
  const padTop = padTopProp ?? Math.round((7.5 / 48) * viewHeight);
  const padBottom = padBottomProp ?? Math.round((40.5 / 48) * viewHeight);

  const pts = useMemo(() => normalizeSeriesYs(data, padTop, padBottom), [data, padTop, padBottom]);
  const linePath = useMemo(() => smoothLinePath(pts), [pts]);
  const areaPath = useMemo(
    () => closedAreaUnderPath(linePath, pts, viewHeight),
    [linePath, pts, viewHeight],
  );

  const chart = !pts.length ? null : (
    <ResponsiveChartBox viewHeight={viewHeight} maxHeight={maxHeight} className={className}>
      <defs>
        <linearGradient id={gradId} gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="0" y2={viewHeight}>
          <stop offset="0%" stopColor={stroke} stopOpacity={fillOpacityTop} />
          <stop offset="100%" stopColor={stroke} stopOpacity={fillOpacityBottom} />
        </linearGradient>
      </defs>
      {showGrid
        ? gridYs.map((y) => (
            <line
              key={y}
              x1="0"
              y1={y}
              x2={VIEW_W}
              y2={y}
              stroke="#eef0f4"
              strokeWidth="0.5"
              vectorEffect="non-scaling-stroke"
            />
          ))
        : null}
      <path d={areaPath} fill={`url(#${gradId})`} />
      <path
        d={linePath}
        fill="none"
        stroke={stroke}
        strokeWidth={strokeWidth}
        strokeLinejoin="round"
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />
      {showDots
        ? pts.map((p, idx) => (
            <circle
              key={idx}
              cx={p.x}
              cy={p.y}
              r={dotRadius}
              fill="#ffffff"
              stroke={stroke}
              strokeWidth={dotStrokeWidth}
              vectorEffect="non-scaling-stroke"
            />
          ))
        : null}
    </ResponsiveChartBox>
  );

  if (!pts.length) return null;

  return (
    <ChartHoverWrap
      dataLength={data.length}
      renderTooltip={(idx) => (
        <span className="flex flex-col gap-0.5">
          <span className="text-[10px] font-semibold uppercase tracking-wide text-[#d0d5da]">
            {captionForIndex(idx, data.length)}
          </span>
          <span className="text-xs font-semibold tabular-nums">{formatPoint(data[idx], idx)}</span>
        </span>
      )}
    >
      {chart}
    </ChartHoverWrap>
  );
}

function plotHorizontalGridYs(plotY0, plotY1) {
  const h = plotY1 - plotY0;
  return [
    Math.round((plotY0 + h * 0.22) * 10) / 10,
    Math.round((plotY0 + h * 0.5) * 10) / 10,
    Math.round((plotY0 + h * 0.78) * 10) / 10,
  ];
}

function xAxisLabelIndices(n) {
  if (n <= 0) return [];
  if (n <= 8) return [...Array(n).keys()];
  const mid = Math.floor((n - 1) / 2);
  return [0, mid, n - 1];
}

/**
 * Area chart with Y and X axes, full width of container. For dashboard trend panels.
 */
export function DemoAreaSparkWithAxes({
  data,
  viewWidth = 122,
  viewHeight = 64,
  maxHeight,
  stroke = "#6941c6",
  fillOpacityTop = 0.2,
  fillOpacityBottom = 0,
  strokeWidth = 2.5,
  showDots = true,
  dotRadius = 0.5,
  className = "",
  formatPoint = formatChartNumber,
  captionForIndex = defaultCaption,
  axisLabelFill = "#717680",
}) {
  const uid = useId().replace(/:/g, "");
  const gradId = `demo-area-axis-${uid}`;

  const plotX0 = 14;
  const plotX1 = viewWidth - 14;
  const plotY0 = 12;
  const plotY1 = viewHeight - 14;

  const gridYs = plotHorizontalGridYs(plotY0, plotY1);
  const pts = useMemo(
    () => normalizeSeriesYsInPlot(data, plotY0, plotY1, plotX0, plotX1),
    [data, plotY0, plotY1, plotX0, plotX1],
  );
  const linePath = useMemo(() => smoothLinePath(pts), [pts]);
  const areaPath = useMemo(
    () => closedAreaUnderPath(linePath, pts, plotY1),
    [linePath, pts, plotY1],
  );

  const { max, min, mid } = useMemo(() => {
    if (!data?.length) return { max: 0, min: 0, mid: 0 };
    const mx = Math.max(...data);
    const mn = Math.min(...data);
    return { max: mx, min: mn, mid: (mx + mn) / 2 };
  }, [data]);

  const xIndices = useMemo(() => xAxisLabelIndices(data.length), [data.length]);

  const axisColClass =
    "flex w-[2.85rem] shrink-0 flex-col justify-between self-stretch pt-[11px] pb-[13px] text-right text-sm font-semibold tabular-nums leading-none sm:w-12";

  const svgChart = !pts.length ? null : (
    <ResponsiveChartBox viewWidth={viewWidth} viewHeight={viewHeight} maxHeight={maxHeight} className={className}>
      <defs>
        <linearGradient id={gradId} gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="0" y2={plotY1}>
          <stop offset="0%" stopColor={stroke} stopOpacity={fillOpacityTop} />
          <stop offset="100%" stopColor={stroke} stopOpacity={fillOpacityBottom} />
        </linearGradient>
      </defs>
      <line
        x1={plotX0}
        y1={plotY0}
        x2={plotX0}
        y2={plotY1}
        stroke="#e9e9eb"
        strokeWidth={0.55}
        vectorEffect="non-scaling-stroke"
      />
      <line
        x1={plotX0}
        y1={plotY1}
        x2={plotX1}
        y2={plotY1}
        stroke="#e9e9eb"
        strokeWidth={0.55}
        vectorEffect="non-scaling-stroke"
      />
      {gridYs.map((y) => (
        <line
          key={y}
          x1={plotX0}
          y1={y}
          x2={plotX1}
          y2={y}
          stroke="#eef0f4"
          strokeWidth={0.45}
          vectorEffect="non-scaling-stroke"
        />
      ))}
      <path d={areaPath} fill={`url(#${gradId})`} />
      <path
        d={linePath}
        fill="none"
        stroke={stroke}
        strokeWidth={strokeWidth}
        strokeLinejoin="round"
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />
      {showDots
        ? pts.map((p, idx) => (
            <circle
              key={idx}
              cx={p.x}
              cy={p.y}
              r={dotRadius}
              fill="#ffffff"
              stroke={stroke}
              strokeWidth={0.85}
              vectorEffect="non-scaling-stroke"
            />
          ))
        : null}
    </ResponsiveChartBox>
  );

  if (!pts.length) return null;

  return (
    <ChartHoverWrap
      dataLength={data.length}
      renderTooltip={(idx) => (
        <span className="flex flex-col gap-0.5">
          <span className="text-[10px] font-semibold uppercase tracking-wide text-[#d0d5da]">
            {captionForIndex(idx, data.length)}
          </span>
          <span className="text-xs font-semibold tabular-nums">{formatPoint(data[idx], idx)}</span>
        </span>
      )}
    >
      <div className="flex w-full min-w-0 flex-col gap-2 px-3 sm:px-5 md:px-6">
        <div className="flex w-full min-w-0 items-stretch gap-3">
          <div className={`${axisColClass}`} style={{ color: axisLabelFill }}>
            <span className="block">{formatPoint(max)}</span>
            <span className="block">{formatPoint(mid)}</span>
            <span className="block">{formatPoint(min)}</span>
          </div>
          <div className="min-h-0 min-w-0 flex-1">{svgChart}</div>
        </div>
        <div className="flex w-full min-w-0 gap-3">
          <div className="w-[2.85rem] shrink-0 sm:w-12" aria-hidden />
          <div
            className="flex min-w-0 flex-1 justify-between gap-1 px-1 text-center text-sm font-semibold leading-none sm:gap-2 sm:px-2"
            style={{ color: axisLabelFill }}
          >
            {xIndices.map((idx) => (
              <span key={`x-${idx}`} className="min-w-0 truncate">
                {captionForIndex(idx, data.length)}
              </span>
            ))}
          </div>
        </div>
      </div>
    </ChartHoverWrap>
  );
}

/**
 * Bar chart with Y and X axes, full width of container -- the bar counterpart of
 * DemoAreaSparkWithAxes, for dashboard tiles whose chart type is "bar".
 */
export function DemoBarsWithAxes({
  data,
  viewWidth = 122,
  viewHeight = 64,
  maxHeight,
  barFill = "#6941c6",
  barOpacity = 0.85,
  barWidthFraction = 0.55,
  className = "",
  formatPoint = formatChartNumber,
  captionForIndex = defaultCaption,
  axisLabelFill = "#717680",
}) {
  const plotX0 = 14;
  const plotX1 = viewWidth - 14;
  const plotY0 = 12;
  const plotY1 = viewHeight - 14;

  const gridYs = plotHorizontalGridYs(plotY0, plotY1);
  const pts = useMemo(
    () => normalizeSeriesYsInPlot(data, plotY0, plotY1, plotX0, plotX1),
    [data, plotY0, plotY1, plotX0, plotX1],
  );

  const bars = useMemo(() => {
    if (!pts.length) return [];
    const n = pts.length;
    const slot = (plotX1 - plotX0) / n;
    const w = slot * barWidthFraction;
    return pts.map((p, i) => {
      const cx = plotX0 + slot * (i + 0.5);
      return { key: i, x: cx - w / 2, y: p.y, w, h: Math.max(0.35, plotY1 - p.y) };
    });
  }, [pts, plotX0, plotX1, plotY1, barWidthFraction]);

  const { max, min, mid } = useMemo(() => {
    if (!data?.length) return { max: 0, min: 0, mid: 0 };
    const mx = Math.max(...data);
    const mn = Math.min(...data);
    return { max: mx, min: mn, mid: (mx + mn) / 2 };
  }, [data]);

  const xIndices = useMemo(() => xAxisLabelIndices(data.length), [data.length]);

  const axisColClass =
    "flex w-[2.85rem] shrink-0 flex-col justify-between self-stretch pt-[11px] pb-[13px] text-right text-sm font-semibold tabular-nums leading-none sm:w-12";

  const svgChart = !pts.length ? null : (
    <ResponsiveChartBox viewWidth={viewWidth} viewHeight={viewHeight} maxHeight={maxHeight} className={className}>
      <line
        x1={plotX0}
        y1={plotY0}
        x2={plotX0}
        y2={plotY1}
        stroke="#e9e9eb"
        strokeWidth={0.55}
        vectorEffect="non-scaling-stroke"
      />
      <line
        x1={plotX0}
        y1={plotY1}
        x2={plotX1}
        y2={plotY1}
        stroke="#e9e9eb"
        strokeWidth={0.55}
        vectorEffect="non-scaling-stroke"
      />
      {gridYs.map((y) => (
        <line
          key={y}
          x1={plotX0}
          y1={y}
          x2={plotX1}
          y2={y}
          stroke="#eef0f4"
          strokeWidth={0.45}
          vectorEffect="non-scaling-stroke"
        />
      ))}
      {bars.map((b) => (
        <rect key={b.key} x={b.x} y={b.y} width={b.w} height={b.h} rx={0.85} fill={barFill} opacity={barOpacity} />
      ))}
    </ResponsiveChartBox>
  );

  if (!pts.length) return null;

  return (
    <ChartHoverWrap
      dataLength={data.length}
      renderTooltip={(idx) => (
        <span className="flex flex-col gap-0.5">
          <span className="text-[10px] font-semibold uppercase tracking-wide text-[#d0d5da]">
            {captionForIndex(idx, data.length)}
          </span>
          <span className="text-xs font-semibold tabular-nums">{formatPoint(data[idx], idx)}</span>
        </span>
      )}
    >
      <div className="flex w-full min-w-0 flex-col gap-2 px-3 sm:px-5 md:px-6">
        <div className="flex w-full min-w-0 items-stretch gap-3">
          <div className={`${axisColClass}`} style={{ color: axisLabelFill }}>
            <span className="block">{formatPoint(max)}</span>
            <span className="block">{formatPoint(mid)}</span>
            <span className="block">{formatPoint(min)}</span>
          </div>
          <div className="min-h-0 min-w-0 flex-1">{svgChart}</div>
        </div>
        <div className="flex w-full min-w-0 gap-3">
          <div className="w-[2.85rem] shrink-0 sm:w-12" aria-hidden />
          <div
            className="flex min-w-0 flex-1 justify-between gap-1 px-1 text-center text-sm font-semibold leading-none sm:gap-2 sm:px-2"
            style={{ color: axisLabelFill }}
          >
            {xIndices.map((idx) => (
              <span key={`x-${idx}`} className="min-w-0 truncate">
                {captionForIndex(idx, data.length)}
              </span>
            ))}
          </div>
        </div>
      </div>
    </ChartHoverWrap>
  );
}

export function DemoDualLineSpark({
  a,
  b,
  viewHeight = 48,
  maxHeight = 240,
  className = "",
  seriesLabels = ["Primary", "Secondary"],
  formatPointA = formatChartNumber,
  formatPointB = formatChartNumber,
  dotRadius = 1.2,
  dotStrokeWidth = 1,
}) {
  const gridYs = gridLinesY(viewHeight);

  const { ptsA, pathA, pathB } = useMemo(() => {
    const max = Math.max(...a, ...b);
    const min = Math.min(...a, ...b);
    const span = max - min || 1;
    const padTop = Math.round((7.5 / 48) * viewHeight);
    const padBottom = Math.round((40.5 / 48) * viewHeight);
    const x0 = DEFAULT_X_INSET;
    const x1 = VIEW_W - DEFAULT_X_INSET;
    const toPts = (series) => {
      const n = series.length;
      if (n <= 0) return [];
      if (n === 1) {
        const v = series[0];
        return [{ x: VIEW_W / 2, y: padTop + ((max - v) / span) * (padBottom - padTop) }];
      }
      return series.map((v, i) => ({
        x: x0 + (i / (n - 1)) * (x1 - x0),
        y: padTop + ((max - v) / span) * (padBottom - padTop),
      }));
    };
    const pa = toPts(a);
    const pb = toPts(b);
    return { ptsA: pa, pathA: smoothLinePath(pa), pathB: smoothLinePath(pb) };
  }, [a, b, viewHeight]);

  const n = Math.min(a.length, b.length);
  const chart = (
    <ResponsiveChartBox viewHeight={viewHeight} maxHeight={maxHeight} className={className}>
      {gridYs.map((y) => (
        <line
          key={y}
          x1="0"
          y1={y}
          x2={VIEW_W}
          y2={y}
          stroke="#eef0f4"
          strokeWidth="0.5"
          vectorEffect="non-scaling-stroke"
        />
      ))}
      <line
        x1="0"
        y1={viewHeight - 0.75}
        x2={VIEW_W}
        y2={viewHeight - 0.75}
        stroke="#e9e9eb"
        strokeWidth="0.55"
        vectorEffect="non-scaling-stroke"
      />
      <path
        d={pathB}
        fill="none"
        stroke="#d0d5dd"
        strokeWidth={2.35}
        strokeLinejoin="round"
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />
      <path
        d={pathA}
        fill="none"
        stroke="#6941c6"
        strokeWidth={2.75}
        strokeLinejoin="round"
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />
      {ptsA.map((p, idx) => (
        <circle
          key={idx}
          cx={p.x}
          cy={p.y}
          r={dotRadius}
          fill="#ffffff"
          stroke="#6941c6"
          strokeWidth={dotStrokeWidth}
          vectorEffect="non-scaling-stroke"
        />
      ))}
    </ResponsiveChartBox>
  );

  if (n < 1) return null;

  return (
    <ChartHoverWrap
      dataLength={n}
      renderTooltip={(idx) => (
        <span className="flex flex-col gap-1">
          <span className="text-[10px] font-semibold uppercase tracking-wide text-[#d0d5da]">
            {defaultCaption(idx, n)}
          </span>
          <span className="text-[11px] tabular-nums text-[#f5f5f6]">
            {seriesLabels[0]}: {formatPointA(a[idx], idx)}
          </span>
          <span className="text-[11px] tabular-nums text-[#f5f5f6]">
            {seriesLabels[1]}: {formatPointB(b[idx], idx)}
          </span>
        </span>
      )}
    >
      {chart}
    </ChartHoverWrap>
  );
}

/** Bars + gradient area + line + dots */
export function DemoComboSpark({
  data,
  viewHeight = 48,
  maxHeight = 240,
  stroke = "#6941c6",
  fillOpacityTop = 0.18,
  fillOpacityBottom = 0,
  strokeWidth = 2.5,
  showGrid = true,
  showDots = true,
  dotRadius = 1.35,
  dotStrokeWidth = 1,
  padTop: padTopProp,
  padBottom: padBottomProp,
  className = "",
  barFill = "#6941c6",
  barOpacity = 0.22,
  barWidthFraction = 0.44,
  formatPoint = formatChartNumber,
  captionForIndex = defaultCaption,
}) {
  const uid = useId().replace(/:/g, "");
  const gradId = `demo-combo-${uid}`;
  const gridYs = gridLinesY(viewHeight);
  const padTop = padTopProp ?? Math.round((7.5 / 48) * viewHeight);
  const padBottom = padBottomProp ?? Math.round((40.5 / 48) * viewHeight);

  const pts = useMemo(() => normalizeSeriesYs(data, padTop, padBottom), [data, padTop, padBottom]);
  const linePath = useMemo(() => smoothLinePath(pts), [pts]);
  const areaPath = useMemo(
    () => closedAreaUnderPath(linePath, pts, viewHeight),
    [linePath, pts, viewHeight],
  );

  const bars = useMemo(() => {
    if (!pts.length) return [];
    const n = pts.length;
    const slot = VIEW_W / n;
    const w = slot * barWidthFraction;
    return pts.map((p, i) => {
      const cx = slot * (i + 0.5);
      const x = cx - w / 2;
      const yTop = p.y;
      const h = Math.max(0.35, viewHeight - yTop);
      return { key: i, x, y: yTop, w, h };
    });
  }, [pts, viewHeight, barWidthFraction]);

  if (!pts.length) return null;

  const chart = (
    <ResponsiveChartBox viewHeight={viewHeight} maxHeight={maxHeight} className={className}>
      <defs>
        <linearGradient id={gradId} gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="0" y2={viewHeight}>
          <stop offset="0%" stopColor={stroke} stopOpacity={fillOpacityTop} />
          <stop offset="100%" stopColor={stroke} stopOpacity={fillOpacityBottom} />
        </linearGradient>
      </defs>
      {bars.map((b) => (
        <rect
          key={b.key}
          x={b.x}
          y={b.y}
          width={b.w}
          height={b.h}
          rx={0.85}
          fill={barFill}
          opacity={barOpacity}
        />
      ))}
      {showGrid
        ? gridYs.map((y) => (
            <line
              key={y}
              x1="0"
              y1={y}
              x2={VIEW_W}
              y2={y}
              stroke="#eef0f4"
              strokeWidth="0.5"
              vectorEffect="non-scaling-stroke"
            />
          ))
        : null}
      <path d={areaPath} fill={`url(#${gradId})`} />
      <path
        d={linePath}
        fill="none"
        stroke={stroke}
        strokeWidth={strokeWidth}
        strokeLinejoin="round"
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />
      {showDots
        ? pts.map((p, idx) => (
            <circle
              key={idx}
              cx={p.x}
              cy={p.y}
              r={dotRadius}
              fill="#ffffff"
              stroke={stroke}
              strokeWidth={dotStrokeWidth}
              vectorEffect="non-scaling-stroke"
            />
          ))
        : null}
    </ResponsiveChartBox>
  );

  return (
    <ChartHoverWrap
      dataLength={data.length}
      renderTooltip={(idx) => (
        <span className="flex flex-col gap-0.5">
          <span className="text-[10px] font-semibold uppercase tracking-wide text-[#d0d5da]">
            {captionForIndex(idx, data.length)}
          </span>
          <span className="text-xs font-semibold tabular-nums">{formatPoint(data[idx], idx)}</span>
        </span>
      )}
    >
      {chart}
    </ChartHoverWrap>
  );
}

export function DemoMiniDeltaSpark({
  data,
  improving,
  viewHeight = 30,
  maxHeight = 72,
  className = "",
  strokeWidth = 2,
  dotRadius = 0.95,
  dotStrokeWidth = 0.8,
  formatPoint = (v) => `${Math.round(Number(v) * 100)}%`,
  captionForIndex = defaultCaption,
}) {
  const stroke = improving ? "#059669" : "#dc2626";
  const pts = useMemo(() => normalizeSeriesYs(data, 5.5, viewHeight - 5.5), [data, viewHeight]);
  const path = useMemo(() => smoothLinePath(pts), [pts]);

  if (!pts.length) return null;

  const chart = (
    <ResponsiveChartBox viewHeight={viewHeight} maxHeight={maxHeight} className={className}>
      <path
        d={path}
        fill="none"
        stroke={stroke}
        strokeWidth={strokeWidth}
        strokeLinejoin="round"
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />
      {pts.map((p, idx) => (
        <circle
          key={idx}
          cx={p.x}
          cy={p.y}
          r={dotRadius}
          fill="#ffffff"
          stroke={stroke}
          strokeWidth={dotStrokeWidth}
          vectorEffect="non-scaling-stroke"
        />
      ))}
    </ResponsiveChartBox>
  );

  return (
    <ChartHoverWrap
      dataLength={data.length}
      renderTooltip={(idx) => (
        <span className="flex flex-col gap-0.5">
          <span className="text-[10px] font-semibold uppercase tracking-wide text-[#d0d5da]">
            {captionForIndex(idx, data.length)}
          </span>
          <span className="text-xs font-semibold tabular-nums">{formatPoint(data[idx], idx)}</span>
        </span>
      )}
    >
      {chart}
    </ChartHoverWrap>
  );
}
