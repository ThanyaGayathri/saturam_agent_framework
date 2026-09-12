import { Link, useNavigate } from "react-router-dom";
import { ArrowRight, LayoutDashboard, TrendingUp } from "lucide-react";
import Breadcrumb from "../shared/Breadcrumb";
import { MIDDLEBY_MONITORS, monitorPath } from "../../domain/middleby-monitor-manifest";
import { PORTFOLIO_PERFORMANCE_PATH } from "../../domain/middleby-financial-demo-data";
import {
  catalogPageInner,
  catalogPageShell,
  catalogH1,
  catalogSubtitle,
} from "../shared/useCaseCatalogPageStyles";
import TourLaunchButton from "../../tours/TourLaunchButton";

/**
 * Middleby-only monitor hub. Lists four cross-UC business monitors.
 */
export default function MiddlebyMonitorHub() {
  const navigate = useNavigate();

  return (
    <div className={catalogPageShell}>
      <div className={catalogPageInner}>
        <Breadcrumb
          items={[
            { label: "Home", to: "/playground" },
            { label: "Monitors" },
          ]}
        />

        <div className="mt-4">
          <div className="flex items-center gap-3">
            <h1 className={catalogH1}>Business monitors</h1>
            <TourLaunchButton tourId="middleby-monitor" label="Take a quick tour" />
          </div>
          <p className={catalogSubtitle}>
            Cross-use-case views for pipeline, experience, demand, and brand portfolio.
          </p>
        </div>

        <article className="mt-8 rounded-xl border border-primary/30 bg-primary-light/30 p-5 shadow-sm dark:bg-primary/10" data-tour-monitor="portfolio">
          <div className="flex items-start justify-between gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary/15">
              <TrendingUp className="h-5 w-5 text-primary" aria-hidden />
            </div>
            <span className="rounded-full bg-primary-light px-2 py-0.5 text-[10px] font-semibold uppercase text-primary dark:bg-primary/15">
              Demo
            </span>
          </div>
          <h2 className="mt-4 text-lg font-semibold text-text-primary">Portfolio performance</h2>
          <p className="mt-1 text-xs font-medium text-text-muted">Leadership / finance view</p>
          <p className="mt-3 text-sm leading-relaxed text-text-secondary">
            Hybrid board: real quote pipeline and demand signals plus illustrative commerce sample
            (GMV, margin, AOV) until Adobe order export is available.
          </p>
          <Link
            to={PORTFOLIO_PERFORMANCE_PATH}
            className="mt-4 inline-flex items-center gap-1.5 text-sm font-semibold text-primary hover:underline"
          >
            Open portfolio performance
            <ArrowRight className="h-4 w-4" aria-hidden />
          </Link>
        </article>

        <div className="mt-8 grid gap-4 md:grid-cols-2" data-tour-monitor="grid">
          {MIDDLEBY_MONITORS.map((monitor) => (
            <article
              key={monitor.id}
              className="rounded-xl border border-primary/25 bg-surface-default p-5 shadow-sm transition hover:border-primary/40"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary-light dark:bg-primary/15">
                  <LayoutDashboard className="h-5 w-5 text-primary" aria-hidden />
                </div>
                <span className="rounded-full bg-primary-light px-2 py-0.5 text-[10px] font-semibold uppercase text-primary dark:bg-primary/15">
                  Demo
                </span>
              </div>

              <h2 className="mt-4 text-lg font-semibold text-text-primary">{monitor.title}</h2>
              <p className="mt-1 text-xs font-medium text-text-muted">{monitor.persona}</p>
              <p className="mt-3 text-sm leading-relaxed text-text-secondary">{monitor.heroQuestion}</p>
              <p className="mt-3 text-xs text-text-muted">
                Combines {monitor.catalogIds.length} analytics use cases
              </p>

              <button
                type="button"
                onClick={() => navigate(monitorPath(monitor.id))}
                className="mt-4 inline-flex items-center gap-1.5 text-sm font-semibold text-primary hover:underline"
              >
                Open monitor
                <ArrowRight className="h-4 w-4" aria-hidden />
              </button>
            </article>
          ))}
        </div>

        <p className="mt-8 text-sm text-text-muted">
          <Link to="/analytics" className="font-medium text-primary hover:underline">
            Browse individual analytics use cases
          </Link>
        </p>
      </div>
    </div>
  );
}
