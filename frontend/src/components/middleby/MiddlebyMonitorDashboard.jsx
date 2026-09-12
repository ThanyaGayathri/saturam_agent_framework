import { useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, ChevronRight, ClipboardCheck, LayoutDashboard, Zap } from "lucide-react";
import Breadcrumb from "../shared/Breadcrumb";
import MiddlebyMonitorSnapshotContent from "./MiddlebyMonitorSnapshotContent";
import { useUseCaseCatalog } from "../../hooks/useUseCaseCatalog";
import { enrichCatalogEntry } from "../../domain/middleby-catalog-helpers";
import { getMiddlebyMonitor, monitorPath, MIDDLEBY_MONITORS, MIDDLEBY_MONITOR_DEMAND_INTELLIGENCE } from "../../domain/middleby-monitor-manifest";
import { loadMiddlebyMonitorSnapshot } from "../../domain/middleby-monitor-data";
import { getPatternsForMonitor } from "../../domain/middleby-decision-seeds";
import { PORTFOLIO_PERFORMANCE_PATH } from "../../domain/middleby-financial-demo-data";

/**
 * Cross-UC monitor dashboard (Middleby profile only).
 */
export default function MiddlebyMonitorDashboard() {
  const { monitorId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const { entries } = useUseCaseCatalog();
  const monitor = useMemo(() => getMiddlebyMonitor(monitorId), [monitorId]);
  const returnTo = location.state?.returnTo;
  const returnLabel = location.state?.returnLabel || "Use case";

  const [loading, setLoading] = useState(true);
  const [snapshot, setSnapshot] = useState(null);

  const automationPatterns = useMemo(
    () => (monitor ? getPatternsForMonitor(monitor.id, monitor).filter((p) => p.poc) : []),
    [monitor]
  );

  useEffect(() => {
    if (!monitorId || !monitor) return;
    let cancelled = false;
    async function load() {
      setLoading(true);
      const data = await loadMiddlebyMonitorSnapshot(monitorId);
      if (!cancelled) {
        setSnapshot(data);
        setLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [monitorId, monitor]);

  if (!monitor) {
    return (
      <div className="mx-auto max-w-3xl px-4 py-10">
        <p className="text-sm text-text-secondary">Monitor not found.</p>
        <Link to="/analytics/monitor" className="mt-4 inline-block text-sm font-semibold text-primary">
          Back to monitors
        </Link>
      </div>
    );
  }

  const linkedEntries = monitor.catalogIds
    .map((id) => enrichCatalogEntry(entries.find((e) => e.id === id)))
    .filter(Boolean);

  const openAutomationFlow = (pattern) => {
    const catalogId =
      pattern.catalogIds.find((id) => monitor.catalogIds.includes(id)) || pattern.catalogIds[0];
    const qs = new URLSearchParams({
      entry: "monitor",
      catalogId,
    });
    if (returnTo) {
      qs.set("returnTo", returnTo);
      qs.set("returnLabel", returnLabel);
    }
    navigate(`/analytics/automation/${encodeURIComponent(pattern.id)}?${qs.toString()}`, {
      state: returnTo ? { returnTo, returnLabel } : undefined,
    });
  };

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6 lg:px-8">
      <Breadcrumb
        items={[
          { label: "Home", to: "/playground" },
          { label: "Monitors", to: "/analytics/monitor" },
          { label: monitor.title },
        ]}
      />

      {returnTo ? (
        <button
          type="button"
          onClick={() => navigate(returnTo)}
          className="mt-4 inline-flex items-center gap-2 rounded-lg border border-primary/30 bg-primary-light px-3 py-2 text-sm font-semibold text-primary hover:bg-primary/15 dark:bg-primary/10"
        >
          <ArrowLeft className="h-4 w-4" aria-hidden />
          Back to {returnLabel}
        </button>
      ) : null}

      <div className="mt-4 flex flex-wrap items-start justify-between gap-4 border-b border-border pb-4">
        <div className="max-w-2xl">
          <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">{monitor.persona}</p>
          <h1 className="mt-1 text-xl font-semibold text-text-primary sm:text-2xl">{monitor.title}</h1>
          <p className="mt-2 text-sm leading-relaxed text-text-secondary">{monitor.heroQuestion}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {MIDDLEBY_MONITORS.filter((m) => m.phase === "active").map((m) => (
            <button
              key={m.id}
              type="button"
              onClick={() =>
                navigate(monitorPath(m.id), {
                  state: returnTo ? { returnTo, returnLabel } : undefined,
                })
              }
              className={`rounded-lg px-3 py-1.5 text-xs font-semibold ${
                m.id === monitor.id
                  ? "bg-primary text-white"
                  : "border border-border bg-surface-secondary text-text-secondary hover:text-text-primary"
              }`}
            >
              {m.title}
            </button>
          ))}
        </div>
      </div>

      <div className="mt-8">
        <MiddlebyMonitorSnapshotContent
          snapshot={snapshot}
          loading={loading}
          honestyEntry={linkedEntries[0] ?? null}
          honestyMode="once"
        />
      </div>

      {monitor.id === MIDDLEBY_MONITOR_DEMAND_INTELLIGENCE ? (
        <section className="mt-8 rounded-xl border border-primary/25 bg-primary-light/40 px-5 py-4 dark:bg-primary/10">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold text-text-primary">Portfolio performance</h2>
              <p className="mt-1 text-sm text-text-secondary">
                Roll up category pipeline, operational trends, and illustrative commerce sample on
                one leadership board.
              </p>
            </div>
            <Link
              to={PORTFOLIO_PERFORMANCE_PATH}
              className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-primary/30 bg-surface-default px-3 py-2 text-xs font-semibold text-primary hover:bg-primary/10"
            >
              <LayoutDashboard className="h-3.5 w-3.5" aria-hidden />
              View portfolio performance
            </Link>
          </div>
        </section>
      ) : null}

      {automationPatterns.length > 0 ? (
        <section className="mt-8 rounded-xl border border-primary/20 bg-primary-light/40 px-5 py-4 dark:bg-primary/10">
          <div className="flex items-center gap-2">
            <Zap className="h-4 w-4 text-primary" aria-hidden />
            <h2 className="text-sm font-semibold text-text-primary">Follow-up workflows</h2>
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            {automationPatterns.map((pattern) => (
              <button
                key={pattern.id}
                type="button"
                onClick={() => openAutomationFlow(pattern)}
                className="inline-flex items-center gap-1.5 rounded-lg border border-primary/30 bg-surface-default px-3 py-2 text-xs font-semibold text-primary hover:bg-primary/10"
              >
                <ClipboardCheck className="h-3.5 w-3.5" aria-hidden />
                {pattern.label}
              </button>
            ))}
          </div>
        </section>
      ) : null}

      <section className="mt-8">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-text-muted">
          Underlying use cases
        </h2>
        <ul className="mt-3 divide-y divide-border rounded-xl border border-border bg-surface-default">
          {linkedEntries.map((entry) => (
            <li key={entry.id}>
              <button
                type="button"
                onClick={() =>
                  navigate(`/playground/insight/${encodeURIComponent(entry.id)}`, {
                    state: entry.sampleQuestion
                      ? { initialQuestion: entry.sampleQuestion }
                      : undefined,
                  })
                }
                className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left hover:bg-surface-secondary/60"
              >
                <div className="min-w-0">
                  <p className="text-sm font-medium text-text-primary">{entry.title}</p>
                  {entry.sampleQuestion ? (
                    <p className="mt-0.5 truncate text-xs text-text-muted">{entry.sampleQuestion}</p>
                  ) : null}
                </div>
                <ChevronRight className="h-4 w-4 shrink-0 text-text-muted" aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
