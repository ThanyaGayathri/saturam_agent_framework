import { Link, useSearchParams, Navigate } from "react-router-dom";
import { ArrowRight, ClipboardCheck } from "lucide-react";
import Breadcrumb from "../shared/Breadcrumb";
import TourLaunchButton from "../../tours/TourLaunchButton";
import { getPocPlaybooks, playbookRequiresApproval } from "../../domain/middleby-automation-playbooks";

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
    <div className="mx-auto max-w-3xl px-6 py-10">
      <Breadcrumb
        items={[
          { label: "Home", to: "/playground" },
          { label: "Automation" },
        ]}
      />

      <div className="mt-4 mb-8">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-semibold text-text-primary">Business process automation</h1>
          <TourLaunchButton tourId="middleby-automation" label="Take a quick tour" />
        </div>
        <p className="mt-2 text-sm text-text-secondary">
          Exploratory workflow examples for demos: what Enterprise GPT could automate vs what your
          team still owns. Not validated Middleby production automation. Open one for the full
          Analyze, Monitor, and Automate path with approval and impact.
        </p>
      </div>

      <ol className="mb-8 grid gap-3 sm:grid-cols-3" data-tour-automation="steps">
        {[
          { n: 1, title: "Analyze", detail: "Insight evidence and signal" },
          { n: 2, title: "Monitor", detail: "Business KPIs and hero chart" },
          { n: 3, title: "Automate", detail: "What runs, approval, impact" },
        ].map((s) => (
          <li
            key={s.n}
            className="rounded-xl border border-border bg-surface-default px-4 py-3 text-sm"
          >
            <span className="text-xs font-bold text-primary">Step {s.n}</span>
            <p className="mt-1 font-semibold text-text-primary">{s.title}</p>
            <p className="mt-0.5 text-xs text-text-muted">{s.detail}</p>
          </li>
        ))}
      </ol>

      <section className="mt-8" data-tour-automation="workflows">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-text-muted">
          Automation workflows
        </h2>
        <ul className="mt-4 space-y-3">
          {playbooks.map((pb) => (
            <li key={pb.id}>
              <Link
                to={`/analytics/automation/${pb.id}`}
                className="flex items-center justify-between gap-3 rounded-xl border border-border bg-surface-default px-4 py-4 transition hover:border-primary/35"
              >
                <div className="min-w-0">
                  <p className="text-sm font-semibold text-text-primary">
                    {pb.label}
                  </p>
                  <p className="mt-1 text-xs text-text-secondary line-clamp-2">{pb.trigger}</p>
                  {pb.automationSummary ? (
                    <p className="mt-2 text-xs font-medium leading-relaxed text-primary line-clamp-2">
                      {pb.automationSummary}
                    </p>
                  ) : null}
                  <p className="mt-2 text-[11px] text-text-muted">
                    {pb.analyzeTitle} → {pb.monitorTitle} → {pb.runSteps.length} actions
                  </p>
                  <p className="mt-1.5 text-[10px] font-semibold uppercase tracking-wide text-text-muted">
                    {playbookRequiresApproval(pb) ? "Needs approval" : "Runs on demand"}
                  </p>
                </div>
                <ArrowRight className="h-4 w-4 shrink-0 text-primary" aria-hidden />
              </Link>
            </li>
          ))}
        </ul>
      </section>

      <section className="mt-10 rounded-xl border border-border bg-surface-secondary/40 p-5" data-tour-automation="queue">
        <div className="flex items-center gap-2">
          <ClipboardCheck className="h-4 w-4 text-primary" aria-hidden />
          <h2 className="text-sm font-semibold text-text-primary">Approval queue</h2>
        </div>
        <p className="mt-2 text-sm text-text-secondary">
          Pending approvals across all workflows. Open a workflow above for the full Analyze,
          Monitor, and Automate walkthrough.
        </p>
        <Link
          to="/analytics/automation/queue"
          className="mt-3 inline-flex items-center gap-1.5 text-sm font-semibold text-primary hover:underline"
        >
          Open approval queue
          <ArrowRight className="h-4 w-4" aria-hidden />
        </Link>
      </section>
    </div>
  );
}
