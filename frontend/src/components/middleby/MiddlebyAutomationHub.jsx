import { Link, useSearchParams, Navigate } from "react-router-dom";
import { ArrowRight, ClipboardCheck } from "lucide-react";
import Breadcrumb from "../shared/Breadcrumb";
import TourLaunchButton from "../../tours/TourLaunchButton";
import { getPocPlaybooks, playbookRequiresApproval } from "../../domain/middleby-automation-playbooks";
import { EnterpriseProductMark } from "../shared/EnterpriseProductMark";
import {
  catalogPageShell,
  catalogPageInner,
  catalogHeaderRow,
  catalogHeaderText,
  catalogH1,
  catalogSubtitle,
} from "../shared/useCaseCatalogPageStyles";
import {
  useCaseCardSurface,
  useCaseCardBody,
  useCaseCardBadge,
  useCaseCardTitle,
  useCaseCardDescription,
  useCaseCardFooter,
  useCaseCardArrow,
} from "../shared/useCaseCardStyles";

/**
 * Automation hub: playbooks (end-to-end journeys) + approval queue.
 */
export default function MiddlebyAutomationHub() {
  const [searchParams] = useSearchParams();
  const pattern = searchParams.get("pattern");
  const card = searchParams.get("card");

  if (pattern) {
    const qs = card ? `?card=${encodeURIComponent(card)}` : "";
    return <Navigate to={`/analytics/automation/${encodeURIComponent(pattern)}${qs}`} replace />;
  }

  const playbooks = getPocPlaybooks();

  return (
    <div className={catalogPageShell}>
      <div className={catalogPageInner}>
        <Breadcrumb
          items={[
            { label: "Home", to: "/playground" },
            { label: "Automation" },
          ]}
        />

        <div className={catalogHeaderRow}>
          <div className={catalogHeaderText}>
            <div className="flex items-center gap-3">
              <h1 className={catalogH1}>Business process automation</h1>
              <TourLaunchButton tourId="middleby-automation" label="Take a quick tour" />
            </div>
            <p className={catalogSubtitle}>
              Workflow examples showing what Enterprise GPT can automate and what your team still
              owns. Open one for the full Analyze, Monitor and Automate path, with approval and
              impact.
            </p>
          </div>
        </div>

        {/* The three steps read as tiles, on the same footing as the Performance KPI cards. */}
        <ol className="mb-8 grid gap-3 sm:grid-cols-3" data-tour-automation="steps">
          {[
            { n: 1, title: "Analyze", detail: "Insight evidence and signal" },
            { n: 2, title: "Monitor", detail: "Business KPIs and hero chart" },
            { n: 3, title: "Automate", detail: "What runs, approval, impact" },
          ].map((s) => (
            <li
              key={s.n}
              className="rounded-xl border border-border bg-surface-default px-4 py-3 shadow-sm"
            >
              <span className="text-xs font-semibold uppercase tracking-wide text-text-muted">
                Step {s.n}
              </span>
              <p className="mt-1 text-2xl font-semibold text-text-primary">{s.title}</p>
              <p className="mt-0.5 text-sm text-text-secondary">{s.detail}</p>
            </li>
          ))}
        </ol>

        <section className="mt-8" data-tour-automation="workflows">
          <h2 className="text-xl font-semibold text-text-primary">Automation workflows</h2>
          <div className="mt-4 grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {playbooks.map((pb) => {
              const needsApproval = playbookRequiresApproval(pb);
              return (
                <Link key={pb.id} to={`/analytics/automation/${pb.id}`} className={useCaseCardSurface}>
                  <div className={useCaseCardBody}>
                    <div className="min-h-0 flex-1">
                      <h3 className={useCaseCardTitle}>{pb.label}</h3>
                      <div className="mt-2 flex flex-wrap items-center gap-2">
                        <span
                          className={`${useCaseCardBadge} ${
                            needsApproval
                              ? "border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/15 dark:text-amber-100"
                              : "border-border bg-surface text-text-secondary"
                          }`}
                        >
                          {needsApproval ? "Needs approval" : "Runs on demand"}
                        </span>
                      </div>
                      <p className={useCaseCardDescription}>{pb.trigger}</p>
                      {pb.automationSummary ? (
                        <p className="mt-2 text-xs font-medium leading-relaxed text-primary line-clamp-2">
                          {pb.automationSummary}
                        </p>
                      ) : null}
                      <p className="mt-2 text-xs text-text-muted">
                        {pb.analyzeTitle} → {pb.monitorTitle} → {pb.runSteps.length} actions
                      </p>
                    </div>
                    <div className={useCaseCardFooter}>
                      <EnterpriseProductMark size="sm" tone="brand" />
                      <ArrowRight className={useCaseCardArrow} strokeWidth={2.5} aria-hidden />
                    </div>
                  </div>
                </Link>
              );
            })}
          </div>
        </section>

        <section className="mt-10 rounded-xl border border-border bg-surface-secondary/40 p-5" data-tour-automation="queue">
          <div className="flex items-center gap-2">
            <ClipboardCheck className="h-4 w-4 text-primary" aria-hidden />
            <h2 className="text-xl font-semibold text-text-primary">Approval queue</h2>
          </div>
          <p className="mt-2 max-w-2xl text-base leading-6 text-text-secondary">
            Pending approvals across all workflows. Open a workflow above for the full Analyze,
            Monitor, and Automate walkthrough.
          </p>
          <Link
            to="/analytics/automation/queue"
            className="mt-4 inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-primary-hover"
          >
            Open approval queue
            <ArrowRight className="h-4 w-4" aria-hidden />
          </Link>
        </section>
      </div>
    </div>
  );
}
