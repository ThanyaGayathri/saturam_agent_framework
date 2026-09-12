import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, ArrowRight, BarChart3, LayoutDashboard } from "lucide-react";
import Breadcrumb from "../shared/Breadcrumb";
import MiddlebyAutomationFlowStepper from "./MiddlebyAutomationFlowStepper";
import MiddlebyAutomationFlowAutomateStep from "./MiddlebyAutomationFlowAutomateStep";
import AnalyticsChartRenderer from "./AnalyticsChartRenderer";
import {
  getAutomationPlaybook,
  playbookRequiresApproval,
} from "../../domain/middleby-automation-playbooks";
import { resolvePlaybookLiveContext } from "../../domain/middleby-automation-impact";
import { resetPlaybookDemoState } from "../../domain/middleby-automation-state";
import { loadMiddlebyMonitorSnapshot } from "../../domain/middleby-monitor-data";
import { getMiddlebyPrimarySpec } from "../../domain/middleby-screen-data";
import { loadMiddlebyDataset } from "../../domain/middleby-analytics-data";
import { buildRoleLensAnswer } from "../../domain/middleby-role-lens";
import { MIDDLEBY_USE_CASE_CATALOG_SEED } from "../../domain/middleby-demo-seed";
import { DEMO_ROLE_EXECUTIVE } from "../../domain/demo-profiles";

function KpiTile({ label, value }) {
  return (
    <div className="rounded-xl border border-border bg-surface-default px-4 py-3 shadow-sm">
      <p className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums text-text-primary">{value}</p>
    </div>
  );
}

function FlowNav({ onBack, onContinue, continueLabel, continueDisabled, showBack = true }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-6">
      {showBack ? (
        <button
          type="button"
          onClick={onBack}
          className="inline-flex items-center gap-2 rounded-lg border border-border px-4 py-2.5 text-sm font-semibold text-text-primary hover:bg-surface-secondary"
        >
          <ArrowLeft className="h-4 w-4" aria-hidden />
          Back
        </button>
      ) : (
        <span />
      )}
      <button
        type="button"
        onClick={onContinue}
        disabled={continueDisabled}
        className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2.5 text-sm font-semibold text-white hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-50"
      >
        {continueLabel}
        <ArrowRight className="h-4 w-4" aria-hidden />
      </button>
    </div>
  );
}

function parseInitialFlow(entryPoint) {
  if (entryPoint === "monitor") return { stepIndex: 1, maxReached: 1 };
  if (entryPoint === "automate") return { stepIndex: 2, maxReached: 2 };
  if (entryPoint === "insight") return { stepIndex: 2, maxReached: 2 };
  return { stepIndex: 0, maxReached: 0 };
}

/**
 * Guided Analyze → Monitor → Automate flow on one screen (no page hops between steps).
 */
export default function MiddlebyAutomationFlow({
  patternId,
  catalogId: catalogIdProp,
  useCaseTitle: useCaseTitleProp,
  embedded = false,
  entryPoint = "hub",
}) {
  const playbook = useMemo(() => getAutomationPlaybook(patternId), [patternId]);
  const hasApprovalGate = playbookRequiresApproval(playbook);

  const resolvedCatalogId = catalogIdProp || playbook?.catalogIds?.[0] || null;
  const catalogEntry = useMemo(
    () => MIDDLEBY_USE_CASE_CATALOG_SEED.find((e) => e.id === resolvedCatalogId) ?? null,
    [resolvedCatalogId]
  );
  const useCaseTitle = useCaseTitleProp || catalogEntry?.title || playbook?.analyzeTitle || "";

  const initial = useMemo(() => parseInitialFlow(entryPoint), [entryPoint]);
  const [stepIndex, setStepIndex] = useState(initial.stepIndex);
  const [maxReached, setMaxReached] = useState(initial.maxReached);
  const flowRef = useRef(null);

  const [liveContext, setLiveContext] = useState(null);
  const [analyzeLoading, setAnalyzeLoading] = useState(true);
  const [analyze, setAnalyze] = useState({ spec: null, rows: [], blocked: false, answer: null });
  const [monitorLoading, setMonitorLoading] = useState(true);
  const [monitorSnapshot, setMonitorSnapshot] = useState(null);

  useEffect(() => {
    if (!playbook) return;
    let cancelled = false;
    resolvePlaybookLiveContext(playbook.id).then((ctx) => {
      if (!cancelled) setLiveContext(ctx);
    });
    return () => {
      cancelled = true;
    };
  }, [playbook]);

  useEffect(() => {
    if (!resolvedCatalogId) {
      setAnalyzeLoading(false);
      return;
    }
    let cancelled = false;
    async function loadAnalyze() {
      setAnalyzeLoading(true);
      const spec = getMiddlebyPrimarySpec(resolvedCatalogId);
      const entry = MIDDLEBY_USE_CASE_CATALOG_SEED.find((e) => e.id === resolvedCatalogId);
      if (!spec?.filePath) {
        if (!cancelled) setAnalyzeLoading(false);
        return;
      }
      const data = await loadMiddlebyDataset(spec.filePath);
      const rows = data.rows || [];
      const answer = buildRoleLensAnswer(entry, spec, rows, DEMO_ROLE_EXECUTIVE, resolvedCatalogId);
      if (!cancelled) {
        setAnalyze({ spec, rows, blocked: data.blocked, answer });
        setAnalyzeLoading(false);
      }
    }
    loadAnalyze();
    return () => {
      cancelled = true;
    };
  }, [resolvedCatalogId]);

  useEffect(() => {
    if (!playbook?.monitorId) {
      setMonitorLoading(false);
      return;
    }
    let cancelled = false;
    async function loadMonitor() {
      setMonitorLoading(true);
      const snap = await loadMiddlebyMonitorSnapshot(playbook.monitorId);
      if (!cancelled) {
        setMonitorSnapshot(snap);
        setMonitorLoading(false);
      }
    }
    loadMonitor();
    return () => {
      cancelled = true;
    };
  }, [playbook?.monitorId]);

  const signalRecap =
    liveContext?.trigger || analyze.answer?.summary || playbook?.trigger || "";
  const monitorRecap = monitorSnapshot?.headline || "";

  const goToStep = useCallback((index) => {
    setStepIndex(index);
    flowRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

  const advance = useCallback(() => {
    const next = Math.min(stepIndex + 1, 2);
    setStepIndex(next);
    setMaxReached((m) => Math.max(m, next));
    flowRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [stepIndex]);

  const restartFlow = useCallback(() => {
    if (playbook) {
      resetPlaybookDemoState(playbook.id, playbook.decisionCardId);
    }
    const insightOnly = embedded && entryPoint === "insight";
    if (!insightOnly) {
      setStepIndex(0);
      setMaxReached(2);
    }
    flowRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [playbook, embedded, entryPoint]);

  if (!playbook) {
    return (
      <div className={embedded ? "" : "mx-auto max-w-3xl px-6 py-10"}>
        <p className="text-sm text-text-secondary">Automation workflow not found.</p>
      </div>
    );
  }

  const analyzeJourney = playbook.journey.filter((s) => s.phase === "analyze");
  const monitorJourney = playbook.journey.filter((s) => s.phase === "monitor");
  const heroChart = monitorSnapshot?.heroCharts?.[0] ?? null;
  const insightOnlyEmbed = embedded && entryPoint === "insight";

  const automateStep = (
    <MiddlebyAutomationFlowAutomateStep
      patternId={patternId}
      signalRecap={signalRecap}
      monitorRecap={monitorRecap}
      onRestartFlow={insightOnlyEmbed ? undefined : restartFlow}
      hideStepOrdinal={insightOnlyEmbed}
      compactInsight={insightOnlyEmbed}
    />
  );

  const flowBody = insightOnlyEmbed ? (
    <div ref={flowRef} className="space-y-6">
      {automateStep}
    </div>
  ) : (
    <div ref={flowRef} className="space-y-6">
      <MiddlebyAutomationFlowStepper
        activeIndex={stepIndex}
        maxReached={maxReached}
        onSelect={goToStep}
        compact={embedded}
      />

      {stepIndex === 0 ? (
        <div className="space-y-6">
          <header>
            <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">
              Step 1 of 3 · Analyze
            </p>
            <h2 className="mt-1 text-lg font-semibold text-text-primary">{useCaseTitle}</h2>
            <p className="mt-2 text-sm text-text-secondary">
              {catalogEntry?.sampleQuestion ||
                "Review the insight evidence that surfaced this automation opportunity."}
            </p>
          </header>

          {analyzeJourney.map((step, i) => (
            <div
              key={i}
              className="flex gap-3 rounded-lg border border-border bg-surface-secondary/30 px-4 py-3"
            >
              <BarChart3 className="mt-0.5 h-4 w-4 shrink-0 text-primary" aria-hidden />
              <div>
                <p className="text-sm font-medium text-text-primary">{step.title}</p>
                <p className="mt-0.5 text-xs text-text-secondary">{step.detail}</p>
              </div>
            </div>
          ))}

          <section className="rounded-xl border border-primary/25 bg-primary-light/40 px-5 py-4 dark:bg-primary/10">
            <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">
              Signal detected
            </p>
            <p className="mt-2 text-base font-medium text-text-primary">
              {signalRecap}
            </p>
          </section>

          {analyzeLoading ? (
            <p className="text-sm text-text-muted">Loading insight evidence…</p>
          ) : analyze.answer?.summary ? (
            <section className="rounded-xl border border-border bg-surface-default p-5 shadow-sm">
              <h3 className="text-sm font-semibold text-text-primary">What we found</h3>
              <p className="mt-2 text-sm leading-relaxed text-text-secondary">{analyze.answer.summary}</p>
              {analyze.answer.items?.length > 0 ? (
                <ul className="mt-4 space-y-2">
                  {analyze.answer.items.slice(0, 6).map((item, i) => (
                    <li
                      key={i}
                      className="flex items-start justify-between gap-3 rounded-lg border border-border bg-surface-secondary/40 px-3 py-2 text-sm"
                    >
                      <span className="text-text-primary">{item.label}</span>
                      <span className="shrink-0 font-medium tabular-nums text-text-secondary">
                        {item.value}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : null}
            </section>
          ) : null}

          {analyze.spec && !analyzeLoading ? (
            <section className="rounded-xl border border-border bg-surface-default p-5 shadow-sm">
              <h3 className="text-sm font-semibold text-text-primary">Evidence chart</h3>
              <div className="mt-4">
                <AnalyticsChartRenderer
                  spec={analyze.spec}
                  rows={analyze.rows}
                  blocked={analyze.blocked}
                />
              </div>
            </section>
          ) : null}

          <FlowNav
            showBack={false}
            onBack={() => {}}
            onContinue={advance}
            continueLabel="Continue to Monitor"
          />
        </div>
      ) : null}

      {stepIndex === 1 ? (
        <div className="space-y-6">
          <header>
            <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">
              Step 2 of 3 · Monitor
            </p>
            <h2 className="mt-1 text-lg font-semibold text-text-primary">{playbook.monitorTitle}</h2>
            <p className="mt-2 text-sm text-text-secondary">
              Confirm the signal on the business monitor before any actions run.
            </p>
          </header>

          <section className="rounded-xl border border-border bg-surface-secondary/50 px-4 py-3 text-sm">
            <p className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">
              Carried from Analyze
            </p>
            <p className="mt-1 text-text-primary">{signalRecap}</p>
          </section>

          {monitorJourney.map((step, i) => (
            <div
              key={i}
              className="flex gap-3 rounded-lg border border-border bg-surface-secondary/30 px-4 py-3"
            >
              <LayoutDashboard className="mt-0.5 h-4 w-4 shrink-0 text-violet-600 dark:text-violet-300" aria-hidden />
              <div>
                <p className="text-sm font-medium text-text-primary">{step.title}</p>
                <p className="mt-0.5 text-xs text-text-secondary">{step.detail}</p>
              </div>
            </div>
          ))}

          {monitorLoading ? (
            <p className="text-sm text-text-muted">Loading monitor snapshot…</p>
          ) : monitorSnapshot ? (
            <>
              <section className="rounded-xl border border-violet-200/60 bg-violet-50/50 px-5 py-4 dark:border-violet-500/25 dark:bg-violet-500/10">
                <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">
                  Monitor headline
                </p>
                <p className="mt-2 text-base font-medium text-text-primary">{monitorRecap}</p>
              </section>

              {monitorSnapshot.kpis?.length > 0 ? (
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                  {monitorSnapshot.kpis.slice(0, 4).map((kpi) => (
                    <KpiTile key={kpi.label} label={kpi.label} value={kpi.value} />
                  ))}
                </div>
              ) : null}

              {heroChart ? (
                <section className="rounded-xl border border-border bg-surface-default p-5 shadow-sm">
                  <h3 className="text-sm font-semibold text-text-primary">{heroChart.title}</h3>
                  <div className="mt-4">
                    <AnalyticsChartRenderer
                      spec={heroChart.spec}
                      rows={heroChart.rows}
                      blocked={heroChart.blocked}
                      releaseRows={heroChart.releaseRows}
                    />
                  </div>
                </section>
              ) : null}

              {monitorSnapshot.highlights?.length > 0 ? (
                <section className="rounded-xl border border-border bg-surface-secondary/40 px-4 py-4">
                  <h3 className="text-xs font-semibold uppercase tracking-wide text-text-muted">
                    Related signals
                  </h3>
                  <ul className="mt-2 space-y-2 text-sm text-text-secondary">
                    {monitorSnapshot.highlights.map((line, i) => (
                      <li key={i}>{line}</li>
                    ))}
                  </ul>
                </section>
              ) : null}
            </>
          ) : (
            <p className="text-sm text-text-secondary">Monitor data is not available for this workflow.</p>
          )}

          <FlowNav
            onBack={() => goToStep(0)}
            onContinue={advance}
            continueLabel="Continue to Automate"
          />
        </div>
      ) : null}

      {stepIndex === 2 ? (
        <div className="space-y-6">
          {automateStep}
          <div className="border-t border-border pt-6">
            <button
              type="button"
              onClick={() => goToStep(1)}
              className="inline-flex items-center gap-2 rounded-lg border border-border px-4 py-2.5 text-sm font-semibold text-text-primary hover:bg-surface-secondary"
            >
              <ArrowLeft className="h-4 w-4" aria-hidden />
              Back to Monitor
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );

  if (embedded) {
    return insightOnlyEmbed ? flowBody : (
      <div>
        <div className="mb-4">
          <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">
            {playbook.owner} ·{" "}
            {hasApprovalGate ? "Needs approval" : "Runs on demand"}
          </p>
          <h2 className="text-lg font-semibold text-text-primary">{playbook.label}</h2>
          <p className="mt-1 text-sm text-text-secondary">
            Walk through Analyze, Monitor, and Automate in one guided flow.
          </p>
        </div>
        {flowBody}
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-3xl px-6 py-10">
      <Breadcrumb
        items={[
          { label: "Home", to: "/playground" },
          { label: "Automation", to: "/analytics/automation" },
          { label: playbook.label || "Automation flow" },
        ]}
      />
      <div className="mt-4 mb-2">
        <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">
          Guided automation flow · {playbook.owner}
        </p>
        <h1 className="mt-1 text-2xl font-semibold text-text-primary">{playbook.label}</h1>
        <p className="mt-2 text-sm text-text-secondary">
          Three steps on one screen: detect the signal, confirm on the business monitor, then run
          the follow-up workflow.
        </p>
      </div>
      {flowBody}
      <p className="mt-8 text-center text-xs text-text-muted">
        <Link to="/analytics/automation" className="font-semibold text-primary hover:underline">
          Back to automation hub
        </Link>
      </p>
    </div>
  );
}
