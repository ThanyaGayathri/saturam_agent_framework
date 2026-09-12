import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { CheckCircle2, LineChart, ListChecks } from "lucide-react";
import { Button, Card } from "./ui";
import { USE_CASE_LAUNCH_SUMMARY_KEY } from "../storage/keys";
import { parseLaunchSummaryPayload } from "../domain/use-case-launch-handoff";

export default function UseCaseLaunchSummary() {
  const [payload, setPayload] = useState(undefined);

  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(USE_CASE_LAUNCH_SUMMARY_KEY);
      const parsed = parseLaunchSummaryPayload(raw);
      setPayload(parsed ?? null);
    } catch {
      setPayload(null);
    }
  }, []);

  if (payload === undefined) {
    return (
      <div className="mx-auto max-w-[720px] px-8 py-12">
        <p className="text-sm text-text-secondary">Loading launch summary…</p>
      </div>
    );
  }

  if (!payload) {
    return (
      <div className="mx-auto max-w-[720px] px-8 py-12">
        <h1 className="text-xl font-semibold text-text-primary">No launch data</h1>
        <p className="mt-2 text-sm text-text-secondary">
          Open the use case builder, complete Step 10, and choose <strong>Launch Use Case</strong> to see a run summary
          here.
        </p>
        <div className="mt-6 flex flex-wrap gap-3">
          <Button variant="primary" to="/use-cases/gallery">
            Browse gallery
          </Button>
          <Button variant="secondary" to="/use-cases/builder/step-10">
            Builder Step 10
          </Button>
        </div>
      </div>
    );
  }

  const { catalogEntry, achievements, completedAt, execution } = payload;
  const dashboardHref = `/use-cases/dashboard?catalogId=${encodeURIComponent(catalogEntry.id)}`;

  return (
    <div className="mx-auto max-w-[720px] px-8 py-10">
      <div className="flex flex-col items-center text-center">
        <div className="flex h-14 w-14 items-center justify-center rounded-full bg-status-success-light">
          <CheckCircle2 size={32} className="text-status-success" strokeWidth={2} />
        </div>
        <h1 className="mt-4 text-2xl font-semibold tracking-tight text-text-primary">Use case executed</h1>
        <p className="mt-2 text-sm text-text-secondary">
          Plan run finished for{" "}
          <span className="font-semibold text-text-primary">{catalogEntry.title}</span>
        </p>
        <p className="mt-1 text-xs text-text-muted">
          Completed{" "}
          {completedAt
            ? new Date(completedAt).toLocaleString(undefined, {
                dateStyle: "medium",
                timeStyle: "short",
              })
            : ""}
        </p>
      </div>

      <Card className="mt-8">
        <div className="mb-4 flex items-center gap-2">
          <ListChecks size={20} className="text-primary" />
          <h2 className="text-sm font-semibold text-text-primary">What was achieved</h2>
        </div>
        <ul className="space-y-3 text-sm text-text-secondary">
          {achievements.map((line, i) => (
            <li key={i} className="flex gap-2 leading-snug">
              <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-primary" />
              <span>{line}</span>
            </li>
          ))}
        </ul>
        {execution ? (
          <div className="mt-6 grid grid-cols-2 gap-3 border-t border-border pt-6 sm:grid-cols-4">
            <div className="rounded-lg bg-surface-secondary px-3 py-2">
              <div className="text-xs font-semibold text-text-muted">Plan stages</div>
              <div className="text-lg font-semibold text-text-primary">{execution.planItemCount}</div>
            </div>
            <div className="rounded-lg bg-surface-secondary px-3 py-2">
              <div className="text-xs font-semibold text-text-muted">Sources</div>
              <div className="text-lg font-semibold text-text-primary">{execution.enabledSources}</div>
            </div>
            <div className="rounded-lg bg-surface-secondary px-3 py-2">
              <div className="text-xs font-semibold text-text-muted">KPIs</div>
              <div className="text-lg font-semibold text-text-primary">{execution.kpiCount}</div>
            </div>
            <div className="rounded-lg bg-surface-secondary px-3 py-2">
              <div className="text-xs font-semibold text-text-muted">Chart</div>
              <div className="text-sm font-semibold capitalize text-text-primary">{execution.chartType}</div>
            </div>
          </div>
        ) : null}
      </Card>

      <div className="mt-8 flex flex-col items-stretch gap-3 sm:flex-row sm:justify-center">
        <Button variant="primary" size="lg" to={dashboardHref} className="gap-2">
          <LineChart size={18} strokeWidth={2} />
          View impact over time
        </Button>
        <Button variant="secondary" size="lg" to="/use-cases">
          Back to templates
        </Button>
      </div>

      <p className="mt-8 text-center text-xs text-text-muted">
        This prototype simulates a successful run.{" "}
        <Link to="/use-cases/gallery" className="font-semibold text-primary hover:text-primary-hover">
          Gallery
        </Link>
      </p>
    </div>
  );
}
