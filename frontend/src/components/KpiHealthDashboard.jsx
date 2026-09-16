import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Activity,
  AlertCircle,
  AlertTriangle,
  ArrowUpRight,
  DollarSign,
  Gauge,
  Minus,
  Package,
  ShoppingCart,
  TrendingDown,
  TrendingUp,
  Truck,
  Users,
} from "lucide-react";
import { useDemoProfile } from "../context/DemoProfileContext";
import { useUseCaseCatalog } from "../hooks/useUseCaseCatalog";
import { getUseCasesForRole } from "../domain/featured-use-cases";
import { getKpiNamesForCatalogId } from "../domain/kpi-usecase-links";
import { DemoAreaSpark, formatChartNumber } from "./shared/DemoSparkCharts";
import { fetchKpiList, fetchKpiHealth, fetchKpiSeries, computeKpi } from "../services/kpis-api";
import KpiTrend, { formatKpiValue, sameDimension } from "./kpi/KpiTrend";

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

/** Short label for a `KpiHealth.status` other than `fresh` (fresh needs no callout). */
const HEALTH_STATUS_LABEL = {
  stale: "Stale",
  never_computed: "Not computed yet",
  failed: "Failed",
  not_scheduled: "Not scheduled",
};

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

/**
 * Build the top-of-dashboard card list from:
 *   1. anomaly-flagged KPIs (so they read as "watch this"), then
 *   2. KPIs linked to the current role's use cases,
 *   3. any other active, trendable registry KPI, most recently adopted first,
 * filtered to KPIs marked active in the registry and capped at 4.
 *
 * Tier 3 exists because 1 and 2 are both static, demo-seeded name lists (the role/use-case
 * catalogue and `ANOMALIES` below) -- a KPI a customer actually adopted via "Add to my
 * metrics" from a live suggestion run has whatever name the agent gave it, which will not
 * generally match either list. Without this tier, a registry with real KPIs in it renders
 * the "No KPIs added yet" empty state, which is exactly backwards: this dashboard's whole
 * point (#280) is to reflect real adopted KPIs, not only ones that happen to share a name
 * with the seed demo catalogue.
 */
function buildRoleSummaryKpiNames({
  activeKpiNames,
  roleUseCases,
  anomalyKpiNames,
  recentlyAdoptedNames = [],
  limit = 4,
}) {
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
  // Fill whatever is still empty with real, active KPIs the curated lists above never named.
  for (const name of recentlyAdoptedNames) {
    if (!seen.has(name)) {
      seen.add(name);
      out.push(name);
      if (out.length >= limit) return out;
    }
  }
  return out;
}

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

export default function KpiHealthDashboard() {
  const navigate = useNavigate();
  const { profileId, role } = useDemoProfile();
  const { entries: catalogEntries } = useUseCaseCatalog();
  const [range, setRange] = useState("7d");
  const days = range === "30d" ? 30 : 7;

  // The real registry (`GET /kpis`) -- only KPIs actually adopted via "Add to my metrics"
  // (KPI Discovery's `createKpi`) land here, which is what makes this dashboard a reflection
  // of that selection rather than an independent catalog.
  const [registryState, setRegistryState] = useState({ status: "loading", kpis: [] });
  useEffect(() => {
    let cancelled = false;
    fetchKpiList()
      .then((kpis) => {
        if (!cancelled) setRegistryState({ status: "ready", kpis });
      })
      .catch(() => {
        if (!cancelled) setRegistryState({ status: "error", kpis: [] });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Freshness per KPI (`GET /kpis/health`), keyed by identifier. Supplementary to the registry
  // fetch above -- a failure here just means no staleness badge, not a blocked dashboard.
  const [healthState, setHealthState] = useState({ status: "loading", byIdentifier: {} });
  useEffect(() => {
    let cancelled = false;
    fetchKpiHealth()
      .then((rows) => {
        if (cancelled) return;
        setHealthState({
          status: "ready",
          byIdentifier: Object.fromEntries((rows || []).map((h) => [h.identifier, h])),
        });
      })
      .catch(() => {
        if (!cancelled) setHealthState({ status: "error", byIdentifier: {} });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // A `rowset` KPI has no single value to trend or spark -- same gate `KpiTrend` itself applies.
  const trendableKpis = useMemo(
    () => registryState.kpis.filter((k) => k.result_shape !== "rowset"),
    [registryState.kpis]
  );
  const kpiByName = useMemo(() => new Map(trendableKpis.map((k) => [k.name, k])), [trendableKpis]);
  const activeKpiNames = useMemo(() => new Set(trendableKpis.map((k) => k.name)), [trendableKpis]);
  // Tier 3 of buildRoleSummaryKpiNames: every active, trendable KPI the registry actually
  // has, most recently adopted/updated first -- what a live registry with no curated-catalogue
  // match still has to show.
  const recentlyAdoptedNames = useMemo(
    () =>
      [...trendableKpis]
        .sort((a, b) => new Date(b.updated_at || 0) - new Date(a.updated_at || 0))
        .map((k) => k.name),
    [trendableKpis]
  );

  const summaryCards = useMemo(() => {
    const roleUseCases = getUseCasesForRole(catalogEntries, profileId, role);
    const anomalyKpiNames = ANOMALIES.map((a) => a.kpiName);
    const names = buildRoleSummaryKpiNames({
      activeKpiNames,
      roleUseCases,
      anomalyKpiNames,
      recentlyAdoptedNames,
      limit: 4,
    });
    return names.map((name, i) => {
      const kpi = kpiByName.get(name);
      const health = kpi ? healthState.byIdentifier[kpi.identifier] : null;
      return {
        name,
        identifier: kpi?.identifier || null,
        unit: kpi?.unit,
        subjectArea: kpi?.subject_area || "",
        icon: KPI_ICON_BY_NAME[name] || Activity,
        tone: TONE_CYCLE[i % TONE_CYCLE.length],
        healthStatus: health?.status || null,
        warn: health?.status === "stale" || health?.status === "failed",
      };
    });
  }, [
    catalogEntries,
    profileId,
    role,
    activeKpiNames,
    recentlyAdoptedNames,
    kpiByName,
    healthState.byIdentifier,
  ]);

  // One KPI's series (`GET /kpis/{id}/series`), keyed by identifier and shared between the
  // summary cards' sparklines and the trend panel below -- both read the same `days` window,
  // so there is only ever one fetch per identifier per range, not one per surface.
  const [seriesById, setSeriesById] = useState({});
  const seriesAbortRefs = useRef(new Map());
  const patchSeries = useCallback((identifier, patch) => {
    setSeriesById((prev) => ({ ...prev, [identifier]: { ...(prev[identifier] || {}), ...patch } }));
  }, []);
  const loadSeries = useCallback(
    (identifier, dimension) => {
      if (!identifier) return;
      // A dedicated `:load` slot -- distinct from `computeSeries`'s `:compute` slot below --
      // so an in-flight "Compute today's value" POST is never silently aborted by an unrelated
      // reload for the same identifier (e.g. the range/role summary-cards effect).
      const abortKey = `${identifier}:load`;
      seriesAbortRefs.current.get(abortKey)?.abort();
      const controller = new AbortController();
      seriesAbortRefs.current.set(abortKey, controller);
      // A range toggle or role switch re-fetches an identifier that may already be `ready` for
      // the *same* dimension -- keep showing its last value/sparkline (and `KpiTrend`'s own
      // chart) while that refetch is in flight instead of flashing back to a loading skeleton.
      // A dimension switch (`onPickDimension`) requests a different slice of the same
      // identifier, though, so it does not qualify for that suppression and shows the loading
      // state like a first load would.
      setSeriesById((prev) => {
        const entry = prev[identifier];
        const sameSlice = entry?.status === "ready" && sameDimension(dimension, entry?.data?.dimension);
        if (sameSlice) return prev;
        return {
          ...prev,
          [identifier]: {
            ...(entry || {}),
            status: "loading",
            error: null,
            retry: "load",
            // The dimension this in-flight request is actually for -- read back on retry after
            // a failure, since a failed fetch never reaches the `.then` that would otherwise
            // stamp it onto `data.dimension`.
            pendingDimension: dimension,
          },
        };
      });
      fetchKpiSeries(identifier, { days, dimension, signal: controller.signal })
        .then((data) => {
          if (controller.signal.aborted) return;
          patchSeries(identifier, { status: "ready", data, error: null, pendingDimension: undefined });
        })
        .catch((err) => {
          if (err?.name === "AbortError") return;
          patchSeries(identifier, {
            status: "error",
            error: err?.detail || err?.message || "Couldn't load values.",
            retry: "load",
            pendingDimension: dimension,
          });
        });
    },
    [patchSeries, days]
  );
  const computeSeries = useCallback(
    (identifier) => {
      if (!identifier) return;
      const abortKey = `${identifier}:compute`;
      seriesAbortRefs.current.get(abortKey)?.abort();
      const controller = new AbortController();
      seriesAbortRefs.current.set(abortKey, controller);
      patchSeries(identifier, { status: "computing", error: null, retry: "compute" });
      computeKpi(identifier, { signal: controller.signal })
        .then((result) => {
          if (controller.signal.aborted) return;
          if (result?.status === "failed") {
            patchSeries(identifier, {
              status: "error",
              error: result.error || "the formula failed.",
              retry: "compute",
            });
            return;
          }
          loadSeries(identifier);
        })
        .catch((err) => {
          if (err?.name === "AbortError") return;
          patchSeries(identifier, {
            status: "error",
            error: err?.detail || err?.message || "Couldn't compute it.",
            retry: "compute",
          });
        });
    },
    [patchSeries, loadSeries]
  );
  useEffect(
    () => () => {
      for (const controller of seriesAbortRefs.current.values()) controller.abort();
    },
    []
  );

  // The identifier set alone, not `summaryCards` itself: `summaryCards` (above) also depends on
  // `catalogEntries` and `healthState.byIdentifier`, both of which can get a new array/object
  // reference (a Supabase realtime refresh, `healthState` resolving) without the underlying set
  // of shown KPIs changing -- keying the reload effect below off `summaryCards` directly would
  // abort+restart every visible card's in-flight series fetch on each such unrelated reference
  // change.
  const summaryIdentifiersKey = useMemo(
    () => summaryCards.map((c) => c.identifier).filter(Boolean).join(","),
    [summaryCards]
  );

  // Load (or reload, on a range/identifier-set change) every summary card's series in one pass.
  useEffect(() => {
    for (const card of summaryCards) {
      if (card.identifier) loadSeries(card.identifier);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed by summaryIdentifiersKey (the stable identifier set) + days; summaryCards itself is read fresh from the closure, not a dep.
  }, [summaryIdentifiersKey, days]);

  // Trend panel follows the summary shortlist so switching role also updates the KPI options
  // here. First card with a real identifier is selected by default.
  const trendOptions = summaryCards.filter((c) => c.identifier);
  const [trendKpiName, setTrendKpiName] = useState("");
  const effectiveTrendCard =
    trendOptions.find((c) => c.name === trendKpiName) || trendOptions[0] || null;
  const trendIdentifier = effectiveTrendCard?.identifier || null;
  const trendSeries = trendIdentifier ? seriesById[trendIdentifier] : null;

  if (registryState.status === "loading") {
    return <p className="text-sm text-text-secondary">Loading dashboard…</p>;
  }

  return (
    <div className="w-full min-w-0">
      <p className="mb-4 text-sm text-text-secondary">
        Track KPIs added via "Add to my metrics" in Discovery.
      </p>
      {registryState.status === "error" && (
        <div className="mb-4 flex items-center gap-2 rounded-xl border border-status-warning/30 bg-status-warning/10 px-4 py-2.5 text-sm text-text-secondary">
          <AlertTriangle size={15} className="shrink-0 text-status-warning" />
          Couldn't load your KPI registry — try again shortly.
        </div>
      )}
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
            No KPIs added yet. Go to Discovery and use "Add to my metrics" to see them tracked here.
          </p>
        ) : null}
        {summaryCards.map((card) => {
          const tone = TONE[card.tone];
          const series = card.identifier ? seriesById[card.identifier] : null;
          const seriesStatus = series?.status || "loading";
          const seriesData = series?.data;
          const points = (seriesData?.points || []).filter(
            (p) => p.value !== null && p.value !== undefined
          );
          // Gated on the value itself, not `seriesStatus`: a background refresh that fails
          // (range/role toggle re-fetching an already-`ready` identifier) never clears
          // `seriesData` -- only a `.catch` that patches `status`/`error` -- so the last known
          // value stays valid and worth showing even while `seriesStatus` is `"error"`.
          const hasValue = seriesData?.value !== null && seriesData?.value !== undefined;
          const staleAfterError = seriesStatus === "error" && hasValue;
          const delta = seriesData?.delta;
          const deltaDirection = delta > 0 ? "up" : delta < 0 ? "down" : "flat";
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
                    {hasValue
                      ? formatKpiValue(card.unit, seriesData.value)
                      : seriesStatus === "loading"
                        ? "…"
                        : seriesStatus === "error"
                          ? "—"
                          : "N/A"}
                  </p>
                  {hasValue && delta !== null && delta !== undefined ? (
                    <p
                      className={`mt-1 inline-flex items-center gap-1 text-xs font-semibold ${
                        deltaDirection === "up"
                          ? "text-status-success"
                          : deltaDirection === "down"
                            ? "text-status-critical"
                            : "text-text-muted"
                      }`}
                    >
                      {deltaDirection === "up" ? (
                        <TrendingUp size={12} />
                      ) : deltaDirection === "down" ? (
                        <TrendingDown size={12} />
                      ) : (
                        <Minus size={12} />
                      )}
                      {formatKpiValue(card.unit, Math.abs(delta))} vs previous night
                    </p>
                  ) : null}
                </div>
                <div className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${tone.bg} ${tone.text}`}>
                  <Icon size={16} />
                </div>
              </div>
              <div className="-mx-1 mt-4 min-w-0 w-full">
                {points.length > 1 ? (
                  <Spark data={points.map((p) => p.value)} stroke={tone.stroke} />
                ) : (
                  <p
                    className={`py-2 text-[11px] ${
                      seriesStatus === "error" ? "text-status-critical" : "text-text-muted"
                    }`}
                  >
                    {seriesStatus === "loading"
                      ? "Loading…"
                      : seriesStatus === "error"
                        ? "Couldn't load."
                        : "Not enough data yet."}
                  </p>
                )}
              </div>
              {staleAfterError ? (
                <p className="mt-1 text-[11px] font-semibold text-status-critical">
                  Couldn't refresh -- showing the last known value.
                </p>
              ) : null}
              {card.subjectArea ? (
                <p className="mt-1 text-[11px] text-text-muted">{card.subjectArea}</p>
              ) : null}
              {card.healthStatus && card.healthStatus !== "fresh" ? (
                <p
                  className={`mt-1 text-[11px] font-semibold ${
                    card.warn ? "text-status-warning" : "text-text-muted"
                  }`}
                >
                  {HEALTH_STATUS_LABEL[card.healthStatus] || card.healthStatus}
                </p>
              ) : null}
            </div>
          );
        })}
      </div>

      {/* Trend (full width) */}
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
                <p className="text-[12px] text-text-muted">Nightly movement for the selected KPI, from gold</p>
              </div>
            </div>
            {trendOptions.length > 0 ? (
              <select
                className="rounded-lg border border-border bg-surface-default px-3 py-1.5 text-xs font-semibold text-text-primary shadow-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
                value={effectiveTrendCard?.name || ""}
                onChange={(e) => setTrendKpiName(e.target.value)}
                aria-label="KPI for trend"
              >
                {trendOptions.map((c) => (
                  <option key={c.name} value={c.name}>
                    {c.name}
                  </option>
                ))}
              </select>
            ) : null}
          </div>

          {trendIdentifier ? (
            <KpiTrend
              state={trendSeries || { status: "loading" }}
              unit={effectiveTrendCard?.unit}
              resultShape={effectiveTrendCard?.result_shape || "scalar"}
              onRetry={() => {
                const entry = seriesById[trendIdentifier];
                if (entry?.retry === "compute") computeSeries(trendIdentifier);
                // `pendingDimension` is the dimension the *failed* request was actually for --
                // `entry.data.dimension` is only the last *successfully* loaded one, which is
                // stale (the previous dimension, not the one that just failed) after a
                // dimension switch fails.
                else loadSeries(trendIdentifier, entry?.pendingDimension ?? entry?.data?.dimension ?? undefined);
              }}
              onCompute={() => computeSeries(trendIdentifier)}
              onPickDimension={(dimension) => loadSeries(trendIdentifier, dimension)}
            />
          ) : (
            <p className="mt-3 rounded-xl border border-dashed border-border-light bg-surface-secondary px-4 py-6 text-center text-sm text-text-muted">
              No KPIs added yet. Add one from Discovery ("Add to my metrics") to see its trend here.
            </p>
          )}

          <div className="mt-4 flex items-center justify-end border-t border-border pt-3 text-[12px] text-text-muted">
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
      </div>
    </div>
  );
}
