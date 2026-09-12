import { BarChart3 } from "lucide-react";
import AnalyticsChartRenderer from "./AnalyticsChartRenderer";
import { getDrillTabLabel } from "../../domain/middleby-catalog-helpers";

/**
 * Full-width chart evidence panel for an insight session.
 */
export default function InsightSessionChartPanel({
  activeSpec,
  dataset,
  loading,
  chartTitle,
  drillSpecs,
  activeRole,
  onSelectRole,
  crossSourceCaption,
  designNotes,
  onBrandClick,
  onSkuClick,
  releaseRows,
}) {
  return (
    <section className="rounded-xl border border-border bg-surface-default p-5 shadow-sm">
      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-2">
          <BarChart3 className="h-4 w-4 text-primary" aria-hidden />
          <h2 className="text-sm font-semibold capitalize text-text-primary">{chartTitle}</h2>
        </div>
        {drillSpecs.length > 0 ? (
          <div className="flex flex-wrap gap-1">
            <button
              type="button"
              onClick={() => onSelectRole("primary")}
              className={`rounded-md px-2 py-1 text-xs font-medium ${
                activeRole === "primary"
                  ? "bg-primary text-white"
                  : "bg-surface-secondary text-text-secondary hover:text-text-primary"
              }`}
            >
              Overview
            </button>
            {drillSpecs.map((d) => (
              <button
                key={d.chartRole}
                type="button"
                onClick={() => onSelectRole(d.chartRole)}
                className={`rounded-md px-2 py-1 text-xs font-medium ${
                  activeRole === d.chartRole
                    ? "bg-primary text-white"
                    : "bg-surface-secondary text-text-secondary hover:text-text-primary"
                }`}
              >
                {getDrillTabLabel(d)}
              </button>
            ))}
          </div>
        ) : null}
      </div>

      {crossSourceCaption ? (
        <p className="mb-4 text-xs text-text-secondary">{crossSourceCaption}</p>
      ) : null}

      {loading ? (
        <div className="space-y-3 animate-pulse" aria-busy="true" aria-label="Loading chart data">
          <div className="h-4 w-2/3 rounded bg-surface-tertiary" />
          <div className="h-48 rounded-xl bg-surface-tertiary sm:h-56" />
          <div className="h-4 w-1/2 rounded bg-surface-tertiary" />
        </div>
      ) : (
        <div className="min-h-[12rem]">
          <AnalyticsChartRenderer
            spec={activeSpec}
            rows={dataset.rows}
            blocked={dataset.blocked}
            blockedMessage={dataset.message}
            onBrandClick={onBrandClick}
            onSkuClick={onSkuClick}
            releaseRows={releaseRows}
          />
        </div>
      )}

      {designNotes && activeRole === "primary" ? (
        <p className="mt-4 text-xs text-text-muted">{designNotes}</p>
      ) : null}
    </section>
  );
}
