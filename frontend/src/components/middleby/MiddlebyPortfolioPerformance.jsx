import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { BarChart3, ChevronDown, ChevronUp, ShieldCheck, TrendingDown, TrendingUp } from "lucide-react";
import Breadcrumb from "../shared/Breadcrumb";
import TourLaunchButton from "../../tours/TourLaunchButton";
import { DemoAreaSparkWithAxes, DemoMiniDeltaSpark, formatChartNumber } from "../shared/DemoSparkCharts";
import AnalyticsChartRenderer from "./AnalyticsChartRenderer";
import PortfolioExtendedSections from "./PortfolioExtendedSections";
import {
  loadPortfolioPerformanceSnapshot,
  PORTFOLIO_CHART_TABS,
  PORTFOLIO_COMMERCE_TABS,
  getPortfolioChartSeries,
} from "../../domain/middleby-financial-demo-data";
import {
  catalogPageInner,
  catalogPageShell,
  catalogH1,
  catalogSubtitle,
} from "../shared/useCaseCatalogPageStyles";

function KpiCard({ label, value, sub, delta, improving, spark, illustrative = false }) {
  const deltaColor = improving
    ? "bg-status-success-light text-status-success"
    : "bg-status-error-light text-status-error";
  return (
    <div
      className={`rounded-xl border bg-surface-default px-4 py-3 shadow-sm ${
        illustrative ? "border-amber-200/80 dark:border-amber-500/30" : "border-border"
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <p className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">{label}</p>
        {illustrative ? (
          <span className="shrink-0 rounded-full border border-amber-200 bg-amber-50 px-1.5 py-0.5 text-[9px] font-semibold text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/15 dark:text-amber-100">
            Sample
          </span>
        ) : null}
      </div>
      <p className="mt-1 text-2xl font-semibold tabular-nums text-text-primary">{value}</p>
      {sub ? <p className="mt-0.5 text-xs text-text-muted">{sub}</p> : null}
      {delta ? (
        <span
          className={`mt-2 inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold ${deltaColor}`}
        >
          {improving ? <TrendingUp className="h-3 w-3" /> : <TrendingDown className="h-3 w-3" />}
          {delta}
        </span>
      ) : null}
      {spark?.length ? (
        <div className="mt-2 w-full min-w-0">
          <DemoMiniDeltaSpark data={spark} improving={improving ?? true} viewHeight={28} maxHeight={56} />
        </div>
      ) : null}
    </div>
  );
}

function IllustrativeKpiCard({ kpi }) {
  const deltaColor = kpi.improving
    ? "bg-status-success-light text-status-success"
    : "bg-status-error-light text-status-error";
  return (
    <div className="rounded-xl border border-amber-200/80 bg-surface-default px-4 py-3 shadow-sm dark:border-amber-500/30">
      <div className="flex items-start justify-between gap-2">
        <p className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">{kpi.label}</p>
        <span className="shrink-0 rounded-full border border-amber-200 bg-amber-50 px-1.5 py-0.5 text-[9px] font-semibold text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/15 dark:text-amber-100">
          Sample
        </span>
      </div>
      <p className="mt-1 text-2xl font-semibold tabular-nums text-text-primary">{kpi.value}</p>
      {kpi.priorValue ? (
        <p className="mt-0.5 text-xs text-text-muted">
          {kpi.priorLabel}: {kpi.priorValue}
        </p>
      ) : null}
      {kpi.scopeSub ? <p className="mt-0.5 text-[10px] text-amber-800 dark:text-amber-100">{kpi.scopeSub}</p> : null}
      {kpi.delta ? (
        <span
          className={`mt-2 inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold ${deltaColor}`}
        >
          {kpi.improving ? <TrendingUp className="h-3 w-3" /> : <TrendingDown className="h-3 w-3" />}
          Var prior {kpi.delta}
        </span>
      ) : null}
      {kpi.spark?.length ? (
        <div className="mt-2 w-full min-w-0">
          <DemoMiniDeltaSpark data={kpi.spark} improving={kpi.improving ?? true} viewHeight={28} maxHeight={56} />
        </div>
      ) : null}
    </div>
  );
}

const MONTHLY_TREND_HEIGHT = 168;

function MonthlyBarChart({ series, title = null }) {
  const values = useMemo(() => series.map((s) => s.value), [series]);
  if (!series.length) {
    return <p className="text-sm text-text-muted">No data for this period.</p>;
  }
  return (
    <div className="w-full min-w-0">
      {title ? (
        <p className="mb-4 text-sm font-medium text-text-primary">{title}</p>
      ) : (
        <div className="mb-2" aria-hidden />
      )}
      <DemoAreaSparkWithAxes
        data={values}
        viewWidth={520}
        viewHeight={MONTHLY_TREND_HEIGHT}
        maxHeight={MONTHLY_TREND_HEIGHT + 48}
        className="w-full"
        captionForIndex={(i) => series[i]?.month_label ?? `Period ${i + 1}`}
        formatPoint={(v, i) => series[i]?.value_fmt ?? formatChartNumber(v)}
      />
    </div>
  );
}

function TabSwitcher({ tabs, activeId, onChange }) {
  return (
    <div className="flex flex-wrap gap-1 rounded-lg border border-border bg-surface-secondary/50 p-1">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          type="button"
          onClick={() => onChange(tab.id)}
          className={`rounded-md px-3 py-1.5 text-xs font-semibold transition ${
            activeId === tab.id
              ? "bg-primary text-white shadow-sm"
              : "text-text-secondary hover:text-text-primary"
          }`}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}

/**
 * Hybrid portfolio performance board: real pipeline/demand + illustrative commerce sample.
 */
export default function MiddlebyPortfolioPerformance() {
  const [year, setYear] = useState("2026");
  const [quarter, setQuarter] = useState(null);
  const [l1, setL1] = useState("all");
  const [loading, setLoading] = useState(true);
  const [snapshot, setSnapshot] = useState(null);
  const [operationalTab, setOperationalTab] = useState("pipeline");
  const [commerceTab, setCommerceTab] = useState("gmv");
  const [commerceOpen, setCommerceOpen] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    const data = await loadPortfolioPerformanceSnapshot({ year, quarter, l1 });
    setSnapshot(data);
    setLoading(false);
  }, [year, quarter, l1]);

  useEffect(() => {
    load();
  }, [load]);

  const operationalSeries = useMemo(
    () => getPortfolioChartSeries(snapshot, operationalTab, "operational"),
    [snapshot, operationalTab]
  );
  const commerceSeries = useMemo(
    () => getPortfolioChartSeries(snapshot, commerceTab, "commerce"),
    [snapshot, commerceTab]
  );

  const operationalTabMeta = PORTFOLIO_CHART_TABS.find((t) => t.id === operationalTab);

  return (
    <div className={catalogPageShell}>
      <div className={catalogPageInner}>
        <Breadcrumb
          items={[
            { label: "Home", to: "/playground" },
            { label: "Use Cases", to: "/analytics" },
            { label: "Portfolio performance" },
          ]}
        />

        <div className="mt-4">
          <div className="flex items-center gap-3">
            <h1 className={catalogH1}>Portfolio performance</h1>
            <TourLaunchButton tourId="middleby-performance" label="Take a quick tour" />
          </div>
          <p className={catalogSubtitle}>
            Pipeline, demand, and category signals from HubSpot, GA, and LogRocket samples.
          </p>
        </div>

        <div className="mt-6 flex flex-wrap items-end gap-4 rounded-xl border border-border bg-surface-default p-4" data-tour-perf="filters">
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">Year</p>
            <div className="mt-1 flex flex-wrap gap-1">
              {(snapshot?.years || ["2025", "2026"]).map((y) => (
                <button
                  key={y}
                  type="button"
                  onClick={() => setYear(y)}
                  className={`rounded-md px-3 py-1.5 text-xs font-semibold ${
                    year === y
                      ? "bg-primary text-white"
                      : "border border-border text-text-secondary hover:text-text-primary"
                  }`}
                >
                  {y}
                </button>
              ))}
            </div>
          </div>
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">Quarter</p>
            <div className="mt-1 flex flex-wrap gap-1">
              <button
                type="button"
                onClick={() => setQuarter(null)}
                className={`rounded-md px-3 py-1.5 text-xs font-semibold ${
                  !quarter
                    ? "bg-primary text-white"
                    : "border border-border text-text-secondary hover:text-text-primary"
                }`}
              >
                Full year
              </button>
              {(snapshot?.quarters || ["Q1", "Q2", "Q3", "Q4"]).map((q) => (
                <button
                  key={q}
                  type="button"
                  onClick={() => setQuarter(q)}
                  className={`rounded-md px-3 py-1.5 text-xs font-semibold ${
                    quarter === q
                      ? "bg-primary text-white"
                      : "border border-border text-text-secondary hover:text-text-primary"
                  }`}
                >
                  {q}
                </button>
              ))}
            </div>
          </div>
          <div className="min-w-[200px]">
            <label className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">
              Category L1
            </label>
            <select
              value={l1}
              onChange={(e) => setL1(e.target.value)}
              className="mt-1 w-full rounded-lg border border-border bg-surface-default px-3 py-2 text-sm text-text-primary"
            >
              {(snapshot?.l1Options || ["all"]).map((opt) => (
                <option key={opt} value={opt}>
                  {opt === "all" ? "All categories" : opt}
                </option>
              ))}
            </select>
          </div>
        </div>

        {loading ? (
          <div className="mt-8 space-y-4 animate-pulse" aria-busy="true">
            <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-4">
              {[1, 2, 3, 4].map((i) => (
                <div key={i} className="h-24 rounded-xl bg-surface-tertiary" />
              ))}
            </div>
            <div className="h-56 rounded-xl bg-surface-tertiary" />
          </div>
        ) : snapshot ? (
          <div className="mt-8 space-y-10">
            {snapshot.categoryScoped && snapshot.categoryFilterLabel ? (
              <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-primary/30 bg-primary-light/50 px-4 py-3 dark:bg-primary/10">
                <p className="text-sm text-text-primary">
                  <span className="font-semibold">Category filter:</span> {snapshot.categoryFilterLabel}
                  {snapshot.categorySharePct != null ? (
                    <span className="text-text-secondary"> · {snapshot.categorySharePct}% of portfolio pipeline</span>
                  ) : null}
                </p>
                <button
                  type="button"
                  onClick={() => setL1("all")}
                  className="text-xs font-semibold text-primary hover:underline"
                >
                  Clear filter
                </button>
              </div>
            ) : null}

            <section data-tour-perf="kpis">
              <h2 className="text-sm font-semibold uppercase tracking-wide text-text-muted">
                Pipeline and demand
              </h2>
              <p className="mt-1 text-sm text-text-secondary">{snapshot.headline}</p>
              <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                {snapshot.operationalKpis.map((kpi) => (
                  <KpiCard key={kpi.label} {...kpi} />
                ))}
              </div>
            </section>

            <section className="rounded-xl border border-border bg-surface-default shadow-sm" data-tour-perf="trends">
              <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-5 py-4">
                <div className="flex items-center gap-2">
                  <BarChart3 className="h-4 w-4 text-primary" aria-hidden />
                  <h2 className="text-sm font-semibold text-text-primary">
                    Operational trends ({snapshot.periodLabel})
                  </h2>
                </div>
                <TabSwitcher tabs={PORTFOLIO_CHART_TABS} activeId={operationalTab} onChange={setOperationalTab} />
              </div>
              <div className="p-5">
                <MonthlyBarChart
                  series={operationalSeries}
                  title={operationalTabMeta?.label || "Trend"}
                />
              </div>
            </section>

            <PortfolioExtendedSections />

            <section className="rounded-xl border border-amber-200/80 bg-amber-50/30 dark:border-amber-500/25 dark:bg-amber-500/5" data-tour-perf="commerce">
              <button
                type="button"
                onClick={() => setCommerceOpen((o) => !o)}
                className="flex w-full items-center justify-between gap-3 px-5 py-4 text-left"
              >
                <div>
                  <h2 className="text-sm font-semibold text-text-primary">
                    Commerce metrics (illustrative sample)
                  </h2>
                  <p className="mt-1 text-xs text-text-secondary">
                    Sample GMV, margin, orders, and AOV for executive layout. Requires Adobe Commerce
                    order export for production values.
                  </p>
                </div>
                {commerceOpen ? (
                  <ChevronUp className="h-5 w-5 shrink-0 text-text-muted" aria-hidden />
                ) : (
                  <ChevronDown className="h-5 w-5 shrink-0 text-text-muted" aria-hidden />
                )}
              </button>
              {commerceOpen ? (
                <div className="border-t border-amber-200/60 px-5 pb-5 pt-4 dark:border-amber-500/20">
                  <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                    {snapshot.illustrativeKpis.map((kpi) => (
                      <IllustrativeKpiCard key={kpi.label} kpi={kpi} />
                    ))}
                  </div>
                  <div className="mt-6 rounded-xl border border-border bg-surface-default p-5">
                    <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                      <p className="text-sm font-medium text-text-primary">Commerce trend by month</p>
                      <TabSwitcher tabs={PORTFOLIO_COMMERCE_TABS} activeId={commerceTab} onChange={setCommerceTab} />
                    </div>
                    <MonthlyBarChart series={commerceSeries} />
                  </div>
                </div>
              ) : null}
            </section>

            <section className="rounded-xl border border-border bg-surface-default p-5 shadow-sm" data-tour-perf="category">
              <h2 className="text-sm font-semibold text-text-primary">Category demand (estimated revenue)</h2>
              <p className="mt-1 text-xs text-text-muted">
                Quote count × catalog median list price. Not actual invoiced GMV.
              </p>
              <div className="mt-4">
                <AnalyticsChartRenderer
                  key={`treemap-${snapshot.filters.l1}`}
                  spec={snapshot.treemap.spec}
                  rows={snapshot.treemap.rows}
                />
              </div>
            </section>

            {snapshot.categoryMix.length > 0 ? (
              <section className="rounded-xl border border-border bg-surface-default shadow-sm" data-tour-perf="category-mix">
                <h2 className="border-b border-border px-5 py-4 text-sm font-semibold text-text-primary">
                  {snapshot.categoryMixLevel === "l2" ? "L2 category mix" : "Category mix"}
                </h2>
                <div className="overflow-x-auto">
                  <table className="min-w-full divide-y divide-border text-left text-sm">
                    <thead className="bg-surface-secondary">
                      <tr>
                        <th className="px-4 py-2 font-semibold text-text-secondary">
                          {snapshot.categoryMixLevel === "l2" ? "L2 category" : "L1 category"}
                        </th>
                        <th className="px-4 py-2 font-semibold text-text-secondary">Quotes</th>
                        <th className="px-4 py-2 font-semibold text-text-secondary">Est. pipeline</th>
                        <th className="px-4 py-2 font-semibold text-text-secondary">Pipeline mix</th>
                        <th className="px-4 py-2 font-semibold text-text-secondary">Quote mix</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border">
                      {snapshot.categoryMix.map((row) => (
                        <tr key={row.l2 || row.l1}>
                          <td className="px-4 py-2.5 font-medium text-text-primary">
                            {row.l2 || row.l1}
                          </td>
                          <td className="px-4 py-2.5 tabular-nums text-text-primary">{row.quote_count}</td>
                          <td className="px-4 py-2.5 tabular-nums text-text-primary">{row.est_revenue_fmt}</td>
                          <td className="px-4 py-2.5 tabular-nums text-text-muted">{row.pipeline_mix_pct}</td>
                          <td className="px-4 py-2.5 tabular-nums text-text-muted">{row.quote_mix_pct}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            ) : null}

            <footer className="rounded-xl border border-border bg-surface-secondary/60 px-4 py-4">
              <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-text-muted">
                <ShieldCheck className="h-3.5 w-3.5" aria-hidden />
                Data honesty
              </div>
              <ul className="mt-3 space-y-2 text-sm text-text-secondary">
                <li>{snapshot.honesty.operational}</li>
                <li>{snapshot.honesty.revenue}</li>
                <li className="font-medium text-amber-900 dark:text-amber-100">{snapshot.honesty.illustrative}</li>
              </ul>
              <p className="mt-3 text-xs text-text-muted">
                Roadmap: Adobe Commerce order export unlocks true GMV, margin, and AOV on this board.
              </p>
            </footer>
          </div>
        ) : null}

        <p className="mt-8 text-sm text-text-muted">
          <Link to="/analytics/monitor/demand-intelligence" className="font-medium text-primary hover:underline">
            Open Demand Intelligence monitor
          </Link>
          {" · "}
          <Link to="/analytics" className="font-medium text-primary hover:underline">
            Browse use cases
          </Link>
        </p>
      </div>
    </div>
  );
}
