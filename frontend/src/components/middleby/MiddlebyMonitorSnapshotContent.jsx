import { BarChart3 } from "lucide-react";
import AnalyticsChartRenderer from "./AnalyticsChartRenderer";
import MiddlebyDataHonestyFooter from "./MiddlebyDataHonestyFooter";
import { isMiddlebyBlendedSession } from "../../domain/middleby-data-honesty";

function KpiTile({ label, value }) {
  return (
    <div className="rounded-xl border border-border bg-surface-default px-4 py-3 shadow-sm">
      <p className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums text-text-primary">{value}</p>
    </div>
  );
}

function pickHonestyChart(charts = []) {
  if (!charts.length) return null;
  return charts.find((c) => isMiddlebyBlendedSession(c.spec, c.rows)) ?? charts[0];
}

/**
 * Shared monitor snapshot body (hub page + insight session Monitor tab).
 */
export default function MiddlebyMonitorSnapshotContent({
  snapshot,
  loading,
  honestyEntry = null,
  /** per-chart: footer under each chart; once: single footer at bottom */
  honestyMode = "once",
}) {
  if (loading) {
    return (
      <div className="space-y-4 animate-pulse" aria-busy="true">
        <div className="grid gap-3 sm:grid-cols-4">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="h-20 rounded-xl bg-surface-tertiary" />
          ))}
        </div>
        <div className="h-48 rounded-xl bg-surface-tertiary" />
      </div>
    );
  }

  if (!snapshot) {
    return <p className="text-sm text-text-secondary">Monitor data is not available.</p>;
  }

  const honestyChart = pickHonestyChart(snapshot.heroCharts);

  return (
    <>
      <section>
        <h2 className="text-sm font-semibold uppercase tracking-wide text-text-muted">Summary</h2>
        <p className="mt-2 text-base leading-relaxed text-text-primary">{snapshot.headline}</p>
      </section>

      {snapshot.kpis?.length > 0 ? (
        <div className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {snapshot.kpis.map((kpi) => (
            <KpiTile key={kpi.label} label={kpi.label} value={kpi.value} />
          ))}
        </div>
      ) : null}

      {snapshot.heroCharts?.map((chart) => (
        <section
          key={chart.key}
          className="mt-8 rounded-xl border border-border bg-surface-default p-5 shadow-sm"
        >
          <div className="mb-4 flex items-center gap-2">
            <BarChart3 className="h-4 w-4 text-primary" aria-hidden />
            <h2 className="text-sm font-semibold text-text-primary">{chart.title}</h2>
          </div>
          {chart.spec?.honestyCaption ? (
            <p className="mb-3 text-xs text-text-muted">{chart.spec.honestyCaption}</p>
          ) : null}
          <AnalyticsChartRenderer
            spec={chart.spec}
            rows={chart.rows}
            blocked={chart.blocked}
            releaseRows={chart.releaseRows}
          />
          {honestyMode === "per-chart" ? (
            <MiddlebyDataHonestyFooter spec={chart.spec} rows={chart.rows} entry={honestyEntry} />
          ) : null}
        </section>
      ))}

      {snapshot.highlights?.length > 0 ? (
        <section className="mt-6 rounded-xl border border-border bg-surface-secondary/40 px-4 py-4">
          <h2 className="text-xs font-semibold uppercase tracking-wide text-text-muted">Signals</h2>
          <ul className="mt-2 space-y-2 text-sm text-text-secondary">
            {snapshot.highlights.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
        </section>
      ) : null}

      {honestyMode === "once" && honestyChart ? (
        <MiddlebyDataHonestyFooter
          spec={honestyChart.spec}
          rows={honestyChart.rows}
          entry={honestyEntry}
        />
      ) : null}
    </>
  );
}
