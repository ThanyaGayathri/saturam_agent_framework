import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Activity,
  AlertCircle,
  ArrowUpRight,
  BarChart2,
  DollarSign,
  Gauge,
  Package,
  ShoppingCart,
  Sparkles,
  TrendingDown,
  TrendingUp,
  Truck,
  Users,
} from "lucide-react";
import { useKpiRegistry } from "../hooks/useKpiRegistry";
import { useDemoProfile } from "../context/DemoProfileContext";
import { useUseCaseCatalog } from "../hooks/useUseCaseCatalog";
import { loadUseCaseCatalog, saveUseCaseCatalog } from "../storage/use-cases-catalog";
import { openCatalogUseCaseInBuilderChat } from "../domain/open-use-case-from-catalog";
import { getUseCasesForRole } from "../domain/featured-use-cases";
import { getKpiNamesForCatalogId } from "../domain/kpi-usecase-links";
import { getSeedDecisionCardsForCatalog } from "../domain/decision-cards-catalog";
import { COPILOT_DECISIONS_KEY } from "../storage/keys";
import { DemoAreaSpark, DemoAreaSparkWithAxes, formatChartNumber } from "./shared/DemoSparkCharts";

/** Seeded catalog IDs: anomalies tie to these use cases for demo storytelling. */
const KPI_DASHBOARD_USE_CASES = {
  forecastAndMargin: "gallery-uc-11",
  cartRecovery: "gallery-uc-05",
  stockout: "gallery-uc-01",
};

const ANOMALIES = [
  {
    id: "a1",
    severity: "critical",
    kpiName: "Gross Margin Rate",
    alert: "Gross margin rate fell 1.2 pts week over week, breaching the pricing guard band.",
    insight:
      "Slow-moving apparel SKUs are carrying higher markdown exposure while sell-through on seasonal categories lags plan.",
    workflow: "Tighten demand forecast and reorder alignment for seasonal categories.",
    useCaseId: KPI_DASHBOARD_USE_CASES.forecastAndMargin,
    copilotQuery: "kpi-dashboard",
  },
  {
    id: "a2",
    severity: "warning",
    kpiName: "Cart Abandonment Rate",
    alert: "Cart abandonment rate rose 6% on mobile checkout versus prior week.",
    insight:
      "Session traces show a 400ms regression on the payment step correlated with the abandonment spike.",
    workflow: "Run recovery campaign and escalate checkout performance review.",
    useCaseId: KPI_DASHBOARD_USE_CASES.cartRecovery,
    copilotQuery: "kpi-dashboard",
  },
  {
    id: "a3",
    severity: "warning",
    kpiName: "Stockout Rate",
    alert: "Stockout incidents increased in top revenue SKUs across two regions.",
    insight: "Inbound variability from two suppliers coincides with higher forecast error in those lanes.",
    workflow: "Prioritize replenishment and supplier escalation for affected SKUs.",
    useCaseId: KPI_DASHBOARD_USE_CASES.stockout,
    copilotQuery: "kpi-dashboard",
  },
  {
    id: "a4",
    severity: "warning",
    kpiName: "Conversion Rate",
    alert: "Site conversion rate dropped 4% versus the four-week average.",
    insight: "New landing pages saw a bounce spike on mobile carriers; category browsing sessions are shorter.",
    workflow: "Roll back the mobile landing template and rerun the A/B guardrails.",
    useCaseId: "gallery-uc-05",
    copilotQuery: "kpi-dashboard",
  },
  {
    id: "a5",
    severity: "critical",
    kpiName: "Order Fill Rate",
    alert: "Fill rate slipped below 92% at two regional DCs for three consecutive days.",
    insight: "Inbound truck delays and a pick-slot bottleneck at DC-04 are the biggest contributors.",
    workflow: "Reallocate replenishment routes and escalate carrier SLA review.",
    useCaseId: "gallery-uc-02",
    copilotQuery: "kpi-dashboard",
  },
  {
    id: "a6",
    severity: "warning",
    kpiName: "Labor Cost as % of Revenue",
    alert: "Labor ratio climbed 1.4 pts in weekend evening shifts.",
    insight: "Traffic-per-hour is flat but scheduling remained at peak levels through the shift tail.",
    workflow: "Reforecast staffing against the latest traffic curves.",
    useCaseId: "gallery-uc-04",
    copilotQuery: "kpi-dashboard",
  },
  {
    id: "a7",
    severity: "warning",
    kpiName: "Sell-Through Rate",
    alert: "Sell-through on the new seasonal SKUs is trailing plan by 8 pts.",
    insight: "Two mid-tier categories are cannibalizing the launch; markdown pressure is building.",
    workflow: "Rebalance display and preemptive markdown on lagging categories.",
    useCaseId: "gallery-uc-03",
    copilotQuery: "kpi-dashboard",
  },
];

const SEVERITY_META = {
  critical: {
    accent: "bg-status-critical-accent",
    chipBg: "bg-status-critical-light dark:bg-status-critical/15",
    chipText: "text-status-critical",
    label: "Critical",
  },
  warning: {
    accent: "bg-status-warning-dot",
    chipBg: "bg-status-warning-light dark:bg-status-warning/15",
    chipText: "text-status-warning dark:text-status-warning-accent",
    label: "Warning",
  },
};

/** Lookup for known KPI names. Anything not here falls back to Activity. */
const KPI_ICON_BY_NAME = {
  "Gross Revenue": DollarSign,
  "Gross Margin Rate": TrendingDown,
  "Gross Margin by Category": TrendingDown,
  "Inventory Turnover": Package,
  "Days of Stock Remaining": Package,
  "Stockout Rate": Package,
  "Conversion Rate": Users,
  "Cart Abandonment Rate": ShoppingCart,
  "Avg Checkout Wait Time": ShoppingCart,
  "POS Transaction Failure Rate": AlertCircle,
  "Fraud Detection Rate": AlertCircle,
  "Return Rate": Package,
  "Return Processing Time": Package,
  "Order Fill Rate": Truck,
  "On-Time Delivery Rate": Truck,
  "Supplier Lead Time": Truck,
  "Sell-Through Rate": Gauge,
  "Markdown Rate": TrendingDown,
  "Sales per Labor Hour": Users,
  "Labor Cost as % of Revenue": Users,
  "Overtime Rate": Users,
  "Foot Traffic": Users,
  "Net Promoter Score (NPS)": Users,
  "Shrinkage Rate": Package,
  "Contact Centre SLA Compliance": Users,
};

const TONE_CYCLE = ["purple", "amber", "blue", "green"];

/** Deterministic sparkline seeded by KPI name + point count. Same name at
 *  different lengths keeps the same starting value so the tile doesn't jump
 *  when the range toggle changes the point count. */
function mockSpark(name, points = 12) {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  const trendUp = (h & 1) === 0;
  let v = 30 + (h % 50);
  const out = [];
  for (let i = 0; i < points; i++) {
    v += (trendUp ? 1 : -1) * (((h >> (i % 8)) & 7) - 3);
    if (v < 15) v = 15 + (h & 7);
    if (v > 95) v = 95 - (h & 7);
    out.push(v);
  }
  return out;
}

const TONE = {
  purple: { bg: "bg-primary-light dark:bg-surface-tertiary dark:ring-1 dark:ring-primary/25", text: "text-primary", stroke: "#6941c6", fill: "rgba(105,65,198,0.12)" },
  amber: {
    bg: "bg-status-warning-light dark:bg-status-warning/15",
    text: "text-status-warning",
    stroke: "#dc6803",
    fill: "rgba(220,104,3,0.14)",
  },
  blue: { bg: "bg-primary-light dark:bg-surface-tertiary dark:ring-1 dark:ring-primary/25", text: "text-primary", stroke: "#6941c6", fill: "rgba(105,65,198,0.12)" },
  green: {
    bg: "bg-status-success-light dark:bg-status-success/15",
    text: "text-status-success",
    stroke: "#12b76a",
    fill: "rgba(18,183,106,0.14)",
  },
};

function flattenActiveKpis(categories) {
  const out = [];
  for (const cat of categories || []) {
    for (const sec of cat.sections || []) {
      for (const k of sec.kpis || []) {
        if (k.active !== false) out.push(k);
      }
    }
  }
  return out;
}

function pickSummaryKpis(kpis, names) {
  const map = new Map(kpis.map((k) => [k.name, k]));
  return names.map((n) => map.get(n)).filter(Boolean);
}

/**
 * Build the top-of-dashboard card list from:
 *   1. anomaly-flagged KPIs (so they read as "watch this"), then
 *   2. KPIs linked to the current role's use cases,
 * filtered to KPIs marked active in the registry and capped at 4.
 */
function buildRoleSummaryKpiNames({ activeKpiNames, roleUseCases, anomalyKpiNames, limit = 4 }) {
  const seen = new Set();
  const out = [];
  // Role-scoped KPIs first so switching role visibly changes the shortlist.
  for (const uc of roleUseCases) {
    for (const name of getKpiNamesForCatalogId(uc.id, 5)) {
      if (activeKpiNames.has(name) && !seen.has(name)) {
        seen.add(name);
        out.push(name);
        if (out.length >= limit) return out;
      }
    }
  }
  // Fill remaining slots with anomaly-flagged KPIs (still filtered to active).
  for (const name of anomalyKpiNames) {
    if (activeKpiNames.has(name) && !seen.has(name)) {
      seen.add(name);
      out.push(name);
      if (out.length >= limit) return out;
    }
  }
  return out;
}

/** Deterministic mock value + delta per KPI. Delta magnitude expands over the
 *  longer window so 30d shows a larger swing than 7d without changing the
 *  headline value (same KPI, same read). */
function mockSummary(kpiName, range = "7d") {
  let h = 0;
  for (let i = 0; i < kpiName.length; i++) h = (h * 31 + kpiName.charCodeAt(i)) >>> 0;
  const base = 100 + (h % 900) / 10;
  const baseDelta = ((h >> 3) % 17) / 10 - 0.8;
  const rangeScale = range === "30d" ? 3.2 : 1;
  const delta = baseDelta * rangeScale;
  const isPct = kpiName.includes("Rate") || kpiName.includes("Margin");
  return {
    display: isPct ? `${base.toFixed(1)}%` : base.toFixed(2),
    delta,
  };
}

const RANGE_COMPARE_LABEL = {
  "7d": "vs prior week",
  "30d": "vs prior 4 weeks",
};

/** Compact sparkline */
function Spark({ data, stroke }) {
  const fillOpacityTop =
    stroke === "#12b76a" ? 0.17 : stroke === "#dc6803" ? 0.19 : 0.2;
  return (
    <DemoAreaSpark
      data={data}
      viewHeight={34}
      maxHeight={90}
      stroke={stroke}
      fillOpacityTop={fillOpacityTop}
      showGrid={false}
      showDots={false}
      padTop={7}
      padBottom={27}
      formatPoint={(v) => formatChartNumber(v)}
    />
  );
}

/** Trend chart with axes, edge to edge inside the card padding band */
function TrendLine({ data, captionForIndex }) {
  return (
    <div className="-mx-5 mt-4 min-w-0 w-[calc(100%+2.5rem)] max-w-none">
      <DemoAreaSparkWithAxes
        data={data}
        viewHeight={40}
        stroke="#6941c6"
        fillOpacityTop={0.22}
        formatPoint={(v) => `${formatChartNumber(v)}%`}
        captionForIndex={captionForIndex}
      />
    </div>
  );
}

export default function KpiHealthDashboard() {
  const navigate = useNavigate();
  const { profileId, role, storageKeys } = useDemoProfile();
  const { data, hydrated } = useKpiRegistry();
  const { entries: catalogEntries, setData: setCatalogData } = useUseCaseCatalog();
  const [range, setRange] = useState("7d");

  const activeKpis = useMemo(() => flattenActiveKpis(data?.categories), [data?.categories]);
  const activeKpiNames = useMemo(() => new Set(activeKpis.map((k) => k.name)), [activeKpis]);

  const summaryCards = useMemo(() => {
    const roleUseCases = getUseCasesForRole(catalogEntries, profileId, role);
    const anomalyKpiNames = ANOMALIES.map((a) => a.kpiName);
    const names = buildRoleSummaryKpiNames({
      activeKpiNames,
      roleUseCases,
      anomalyKpiNames,
      limit: 4,
    });
    const anomalySet = new Set(anomalyKpiNames);
    const sparkPoints = range === "30d" ? 12 : 7;
    return names.map((name, i) => ({
      name,
      icon: KPI_ICON_BY_NAME[name] || Activity,
      tone: TONE_CYCLE[i % TONE_CYCLE.length],
      spark: mockSpark(name, sparkPoints),
      warn: anomalySet.has(name),
    }));
  }, [catalogEntries, profileId, role, activeKpiNames, range]);

  const summaryKpis = useMemo(
    () => pickSummaryKpis(activeKpis, summaryCards.map((c) => c.name)),
    [activeKpis, summaryCards]
  );

  // Anomaly feed follows the role: only surface anomalies whose KPI is in the
  // shortlist above. If none intersect, fall back to all anomalies so the demo
  // still shows the pattern rather than an empty feed.
  const scopedAnomalies = useMemo(() => {
    const names = new Set(summaryCards.map((c) => c.name));
    const matched = ANOMALIES.filter((a) => names.has(a.kpiName));
    return matched.length ? matched : ANOMALIES;
  }, [summaryCards]);

  // Trend chart follows the summary shortlist so switching role also updates
  // the KPI options here. First card is selected by default; if none are
  // available we fall back to a static label.
  const trendOptions = summaryCards.map((c) => c.name);
  const [trendKpiName, setTrendKpiName] = useState("");
  const effectiveTrendKpi = trendOptions.includes(trendKpiName)
    ? trendKpiName
    : trendOptions[0] || "Gross Margin Rate";
  const trendData =
    range === "7d"
      ? [72, 70.5, 68.2, 69.1, 66.8, 65.5, 64.9]
      : [74, 73, 71.5, 70.8, 69.2, 68.1, 67, 66.5, 65, 64.5, 63.8, 63];

  const openPlaygroundExploration = (useCaseId) => {
    const cat = loadUseCaseCatalog(storageKeys.USE_CASE_CATALOG, profileId);
    const entry = cat.entries?.find((e) => e.id === useCaseId);
    if (entry) openCatalogUseCaseInBuilderChat(navigate, entry);
  };

  /**
   * Approve + run: promote the seed use case to a user-active catalog entry so
   * it shows up under User built use cases, then jump into the Co-Pilot decision
   * feed with the anomaly context.
   */
  const activateAndReview = (a) => {
    // Update the hook's state so its save effect persists to localStorage AND
    // any other consumer of useUseCaseCatalog (e.g. the Gallery page) picks up
    // the new status without waiting for a full remount.
    setCatalogData((prev) => {
      const found = (prev.entries || []).some((e) => e.id === a.useCaseId);
      if (!found) return prev;
      return {
        ...prev,
        entries: prev.entries.map((e) =>
          e.id === a.useCaseId
            ? { ...e, source: "user", status: "active", updatedAt: new Date().toISOString() }
            : e
        ),
      };
    });
    // Belt-and-suspenders: also write directly, in case the Copilot page mounts
    // before the hook's save effect flushes.
    let useCaseTitle = "";
    try {
      const cat = loadUseCaseCatalog(storageKeys.USE_CASE_CATALOG, profileId);
      const entry = (cat.entries || []).find((e) => e.id === a.useCaseId);
      useCaseTitle = entry?.title || "";
      if (entry) {
        saveUseCaseCatalog(
          {
            ...cat,
            entries: cat.entries.map((e) =>
              e.id === a.useCaseId
                ? { ...e, source: "user", status: "active", updatedAt: new Date().toISOString() }
                : e
            ),
          },
          storageKeys.USE_CASE_CATALOG
        );
      }
    } catch {
      /* ignore */
    }
    // Seed decision cards for THIS use case if none exist yet, so the Co-Pilot
    // feed shows only the actions tied to the anomaly's workflow — not the
    // mixed queue from prior activations.
    try {
      const raw = localStorage.getItem(COPILOT_DECISIONS_KEY);
      const all = raw ? JSON.parse(raw) : [];
      const alreadySeeded = all.some((c) => c.useCaseId === a.useCaseId);
      if (!alreadySeeded) {
        const seeded = getSeedDecisionCardsForCatalog(a.useCaseId, useCaseTitle);
        if (seeded.length > 0) {
          localStorage.setItem(COPILOT_DECISIONS_KEY, JSON.stringify([...all, ...seeded]));
        }
      }
    } catch {
      /* ignore */
    }
    // Pass useCaseId so the feed filters to just this workflow's decisions,
    // not the entire pending queue across every activated use case.
    navigate(
      `/copilot/decisions?source=${encodeURIComponent(a.copilotQuery)}&anomaly=${encodeURIComponent(a.id)}&useCaseId=${encodeURIComponent(a.useCaseId)}`
    );
  };

  if (!hydrated) {
    return <p className="text-sm text-text-secondary">Loading dashboard…</p>;
  }

  return (
    <div className="w-full min-w-0">
      <p className="mb-4 text-sm text-text-secondary">
        Track KPIs from the Registry. Automate responses from the anomaly feed below.
      </p>
      <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <span className="inline-flex w-fit items-center gap-1.5 rounded-full bg-[#ecfdf3] px-2.5 py-1 text-[11px] font-semibold text-[#067647] ring-1 ring-[#d1fadf]">
          <span className="h-1.5 w-1.5 rounded-full bg-[#12b76a]" aria-hidden />
          Live
        </span>
        <div className="flex flex-wrap items-center gap-2 sm:justify-end" data-tour-kpi-dash="range">
          {/* Segmented range control */}
          <div className="inline-flex rounded-lg border border-border bg-surface-default p-0.5 shadow-sm">
            {[
              { key: "7d", label: "7 days" },
              { key: "30d", label: "30 days" },
            ].map((t) => (
              <button
                key={t.key}
                type="button"
                onClick={() => setRange(t.key)}
                className={`rounded-md px-3 py-1.5 text-xs font-semibold transition ${
                  range === t.key
                    ? "bg-primary-light text-primary dark:bg-surface-tertiary dark:ring-1 dark:ring-primary/30"
                    : "text-text-secondary hover:text-text-primary"
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Summary KPI cards */}
      <div className="mb-8 grid gap-4 sm:grid-cols-2 xl:grid-cols-4" data-tour-kpi-dash="tiles">
        {summaryCards.length === 0 ? (
          <p className="col-span-full text-sm text-text-secondary">
            No active KPIs linked to the current role. Enable KPIs from the Registry, or switch role from the profile switcher.
          </p>
        ) : null}
        {summaryCards.map((card) => {
          const tone = TONE[card.tone];
          const info = summaryKpis.find((k) => k.name === card.name);
          const { display, delta } = mockSummary(card.name, range);
          const up = delta >= 0;
          const Icon = card.icon;
          return (
            <div
              key={card.name}
              className={`relative overflow-hidden rounded-2xl border bg-surface-default p-5 shadow-sm transition hover:shadow-[0_10px_24px_-10px_rgba(16,24,40,0.1)] dark:hover:shadow-[0_10px_24px_-10px_rgba(0,0,0,0.35)] ${
                card.warn ? "border-status-warning-border dark:border-status-warning/50" : "border-border"
              }`}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-[11px] font-semibold uppercase tracking-[0.4px] text-text-muted">
                    {card.name}
                  </p>
                  <p className="mt-1.5 text-[28px] font-semibold leading-tight tracking-tight text-text-primary">
                    {display}
                  </p>
                  <p
                    className={`mt-1 inline-flex items-center gap-1 text-xs font-semibold ${
                      up ? "text-status-success" : "text-status-critical"
                    }`}
                  >
                    {up ? <TrendingUp size={12} /> : <TrendingDown size={12} />}
                    {Math.abs(delta).toFixed(1)}% {RANGE_COMPARE_LABEL[range] || RANGE_COMPARE_LABEL["7d"]}
                  </p>
                </div>
                <div className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${tone.bg} ${tone.text}`}>
                  <Icon size={16} />
                </div>
              </div>
              <div className="-mx-1 mt-4 min-w-0 w-full">
                <Spark data={card.spark} stroke={tone.stroke} />
              </div>
              {info ? (
                <p className="mt-1 text-[11px] text-text-muted">{info.subjectArea || info.level || ""}</p>
              ) : null}
            </div>
          );
        })}
      </div>

      {/* Trend + anomaly feed (stacked rows, full width) */}
      <div className="flex w-full min-w-0 flex-col gap-6">
        {/* Trend analysis */}
        <div className="flex min-h-0 min-w-0 flex-col rounded-2xl border border-border bg-surface-default p-5 shadow-sm" data-tour-kpi-dash="trend">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary-light text-primary dark:bg-surface-tertiary dark:ring-1 dark:ring-primary/25">
                <Activity size={17} aria-hidden />
              </div>
              <div>
                <h3 className="text-[15px] font-semibold tracking-tight text-text-primary">Trend analysis</h3>
                <p className="text-[12px] text-text-muted">Week-over-week movement for the selected KPI</p>
              </div>
            </div>
            <select
              className="rounded-lg border border-border bg-surface-default px-3 py-1.5 text-xs font-semibold text-text-primary shadow-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
              value={effectiveTrendKpi}
              onChange={(e) => setTrendKpiName(e.target.value)}
              aria-label="KPI for trend"
            >
              {trendOptions.length === 0 ? (
                <option value={effectiveTrendKpi}>{effectiveTrendKpi}</option>
              ) : (
                trendOptions.map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))
              )}
            </select>
          </div>

          <div className="flex items-baseline gap-3">
            <span className="text-[28px] font-semibold tracking-tight text-text-primary">
              {trendData[trendData.length - 1].toFixed(1)}%
            </span>
            <span className="inline-flex items-center gap-1 rounded-full bg-status-critical-light px-2 py-0.5 text-[11px] font-semibold text-status-critical dark:bg-status-critical/15">
              <TrendingDown size={11} />
              {Math.abs(trendData[0] - trendData[trendData.length - 1]).toFixed(1)} pts
            </span>
            <span className="text-[12px] text-text-muted">vs target 65%</span>
          </div>

          <TrendLine
            data={trendData}
            captionForIndex={(i, n) =>
              range === "7d"
                ? (["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][i] ?? `Day ${i + 1}`)
                : n <= 12
                  ? `W${i + 1}`
                  : `P${i + 1}`
            }
          />

          <div className="mt-4 flex items-center justify-between border-t border-border pt-3 text-[12px] text-text-muted">
            <span>Illustrative trend · not live data</span>
            <button
              type="button"
              onClick={() => navigate("/monitor")}
              className="inline-flex items-center gap-1 font-semibold text-primary hover:underline"
            >
              Open Monitor for live ops view
              <ArrowUpRight size={13} />
            </button>
          </div>
        </div>

        {/* AI anomaly feed */}
        <div
          className="flex min-h-0 min-w-0 flex-col rounded-2xl border border-border bg-surface-default shadow-sm"
          data-tour-kpi-dash="anomalies"
        >
          <div className="shrink-0 border-b border-border bg-gradient-to-b from-surface-page-top to-surface px-4 pb-3 pt-4 sm:px-5 sm:pt-5 dark:from-surface-secondary dark:to-surface">
            <div className="flex items-start justify-between gap-3">
              <div className="flex min-w-0 items-start gap-2.5">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-primary-light to-primary-light/80 text-primary shadow-sm ring-1 ring-primary/25 dark:from-primary/25 dark:to-primary/15 dark:ring-primary/30">
                  <Sparkles size={18} strokeWidth={1.75} aria-hidden />
                </div>
                <div className="min-w-0">
                  <h3 className="text-[15px] font-semibold tracking-tight text-text-primary">AI anomaly feed</h3>
                  <p className="mt-0.5 text-[12px] leading-relaxed text-text-secondary">
                    Signals linked to library use cases for follow-up
                  </p>
                </div>
              </div>
              <span className="shrink-0 rounded-full border border-status-warning-border/80 bg-status-warning-light px-2.5 py-1 text-[11px] font-bold tabular-nums text-status-warning shadow-sm dark:bg-status-warning/15">
                {scopedAnomalies.length} open
              </span>
            </div>
          </div>

          <div className="min-h-0 flex-1 grid grid-cols-1 gap-4 p-3 sm:p-4 md:grid-cols-2 xl:grid-cols-3 md:gap-4">
            {scopedAnomalies.map((a) => {
              const sev = SEVERITY_META[a.severity] || SEVERITY_META.warning;
              return (
                <div
                  key={a.id}
                  className="group relative flex min-h-0 min-w-0 flex-col overflow-hidden rounded-xl border border-border bg-surface-secondary shadow-sm ring-1 ring-border transition hover:border-border-light hover:bg-surface-default hover:shadow-md dark:bg-surface-default"
                >
                  <span className={`absolute inset-y-0 left-0 w-[3px] ${sev.accent}`} aria-hidden />
                  <div className="pl-3.5 pr-3 py-3 sm:pl-4 sm:pr-3.5 sm:py-3.5">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span
                        className={`inline-flex items-center gap-0.5 rounded-md px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide ${sev.chipBg} ${sev.chipText}`}
                      >
                        <AlertCircle size={10} className="opacity-90" />
                        {sev.label}
                      </span>
                      <span className="inline-flex max-w-full items-center gap-1 rounded-md border border-primary/30 bg-surface-default px-1.5 py-0.5 text-primary">
                        <BarChart2 size={10} className="shrink-0" aria-hidden />
                        <span className="min-w-0 truncate text-[11px] font-semibold leading-tight text-primary">
                          {a.kpiName}
                        </span>
                      </span>
                    </div>
                    <p className="mt-2 text-[13px] font-semibold leading-snug text-text-primary">{a.alert}</p>
                    <div className="mt-2 rounded-lg border border-primary/25 bg-gradient-to-b from-surface to-primary-light px-2.5 py-2 dark:from-surface-secondary dark:to-primary/15">
                      <p className="text-[10px] font-bold uppercase tracking-wide text-primary">AI insight</p>
                      <p className="mt-0.5 text-[12px] leading-relaxed text-text-primary">{a.insight}</p>
                    </div>
                    <p className="mt-2 text-[12px] leading-relaxed text-text-secondary">
                      <span className="font-semibold text-text-primary">Suggested: </span>
                      {a.workflow}
                    </p>
                    <div className="mt-2.5 flex flex-wrap items-center gap-1.5 border-t border-dashed border-border pt-2.5">
                      <button
                        type="button"
                        onClick={() => openPlaygroundExploration(a.useCaseId)}
                        className="inline-flex items-center justify-center gap-0.5 rounded-lg px-2 py-1.5 text-[11px] font-semibold text-primary transition hover:bg-primary-light-hover"
                      >
                        Explore
                        <ArrowUpRight size={12} className="opacity-80" />
                      </button>
                      <button
                        type="button"
                        onClick={() => activateAndReview(a)}
                        className="inline-flex items-center justify-center gap-1 rounded-lg bg-primary px-2.5 py-1.5 text-[11px] font-semibold text-white shadow-sm transition hover:bg-primary-hover sm:px-3"
                      >
                        Approve and run
                      </button>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
