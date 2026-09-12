import { useCallback, useEffect, useMemo, useState } from "react";
import { CheckCircle2, ClipboardCheck, RefreshCw, Sparkles, XCircle } from "lucide-react";
import { DemoMiniDeltaSpark } from "../shared/DemoSparkCharts";
import {
  getPlaybook,
  getAutomationOutputs,
  playbookRequiresApproval,
} from "../../domain/automation-registry";
import { resolvePlaybookLiveContext } from "../../domain/middleby-automation-impact";
import {
  getMiddlebyDecisionSeedCards,
  MIDDLEBY_DECISIONS_KEY,
} from "../../domain/middleby-decision-seeds";
import {
  readDeliveredAt,
  readSimulatedRun,
  resetPlaybookDemoState,
  writeDeliveredAt,
  writeSimulatedRun,
} from "../../domain/middleby-automation-state";
import AutomationStartOverControl from "./AutomationStartOverControl";
import AutomationOutputPanel from "../shared/AutomationOutputPanel";
import AiPreparingLoader from "../shared/AiPreparingLoader";
import AutomationPhaseStepper from "../shared/AutomationPhaseStepper";
import AutomationPreflightChecks, {
  preflightIsBlocked,
} from "../shared/AutomationPreflightChecks";
import AutomationKpiTargets from "../shared/AutomationKpiTargets";

/**
 * Phase labels differ by playbook type:
 *   - Workflow (has approval gate): the third phase is "Approved" because
 *     what matters is the human decision that led to the action being taken.
 *   - Analytics (no approval gate): the third phase is "Delivered" because
 *     the artifact was sent to stakeholders.
 */
const WORKFLOW_PHASES = [
  { id: "preparing", label: "Prepare", sub: "AI drafts" },
  { id: "review", label: "Review", sub: "Check the action" },
  { id: "delivered", label: "Approved", sub: "Action taken" },
];
const ANALYTICS_PHASES = [
  { id: "preparing", label: "Prepare", sub: "AI drafts" },
  { id: "review", label: "Review", sub: "Check drafts" },
  { id: "delivered", label: "Delivered", sub: "Sent & logged" },
];

const DONE_BY_PHASE = {
  preparing: [],
  review: ["preparing"],
  delivered: ["preparing", "review"],
};

function ImpactTile({ label, value, sub }) {
  return (
    <div className="rounded-xl border border-border bg-surface-default px-4 py-3 shadow-sm">
      <p className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums text-text-primary">{value}</p>
      {sub ? <p className="mt-0.5 text-xs text-text-muted">{sub}</p> : null}
    </div>
  );
}

function ImpactSparkCard({ label, before, after, improving }) {
  return (
    <div className="rounded-xl border border-border bg-surface-default px-4 py-3 shadow-sm">
      <p className="text-xs font-semibold text-text-primary">{label}</p>
      <div className="mt-2 grid grid-cols-2 gap-3">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">Before</p>
          <div className="mt-1 w-full min-w-0">
            <DemoMiniDeltaSpark data={before} improving={false} viewHeight={32} maxHeight={48} strokeWidth={1.8} />
          </div>
        </div>
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">After</p>
          <div className="mt-1 w-full min-w-0">
            <DemoMiniDeltaSpark
              data={after}
              improving={improving}
              viewHeight={32}
              maxHeight={48}
              strokeWidth={1.8}
            />
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * Automate step, restructured as a 3-phase flow.
 *
 *   Prepare  - AI runs read-only prep (pull data, rank, draft artifacts)
 *   Review   - human sees the drafts + pre-flight, then approves or rejects
 *   Delivered - success state with impact and delivered artifacts
 *
 * Approval is asked only when the human has something concrete to review.
 * The mini-stepper at the top mirrors the Use Case Builder's step-sidebar
 * grammar so both flows feel like the same product.
 */
export default function MiddlebyAutomationFlowAutomateStep({
  patternId,
  signalRecap,
  monitorRecap,
  onRestartFlow,
  hideStepOrdinal = false,
  compactInsight = false,
}) {
  const playbook = useMemo(() => getPlaybook(patternId), [patternId]);
  const hasApprovalGate = playbookRequiresApproval(playbook);
  const outputs = useMemo(() => getAutomationOutputs(playbook?.id), [playbook]);
  const [cardStatus, setCardStatus] = useState("pending");
  const [simulatedRun, setSimulatedRun] = useState(false);
  const [liveContext, setLiveContext] = useState(null);
  const [prepDone, setPrepDone] = useState(false);
  const [preflightAcked, setPreflightAcked] = useState(false);
  const [regenTick, setRegenTick] = useState(0);
  const [deliveredAt, setDeliveredAt] = useState(null);

  const decisionCard = useMemo(() => {
    if (!playbook?.decisionCardId) return null;
    // Prefer inline decisionCard on the playbook (retail/GFI style),
    // fall back to Middleby's decision-seed registry.
    if (playbook.decisionCard) return playbook.decisionCard;
    return getMiddlebyDecisionSeedCards().find((c) => c.id === playbook.decisionCardId) ?? null;
  }, [playbook]);

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

  const syncApprovalState = useCallback(() => {
    if (!playbook) return;
    if (playbook.decisionCardId) {
      try {
        const raw = sessionStorage.getItem(MIDDLEBY_DECISIONS_KEY);
        const cards = raw ? JSON.parse(raw) : getMiddlebyDecisionSeedCards();
        const found = cards.find((c) => c.id === playbook.decisionCardId);
        if (found) setCardStatus(found.status);
      } catch {
        /* ignore */
      }
    } else {
      setSimulatedRun(readSimulatedRun(playbook.id));
    }
    setDeliveredAt(readDeliveredAt(playbook.id));
  }, [playbook]);

  useEffect(() => {
    syncApprovalState();
  }, [syncApprovalState]);

  const persistCardStatus = useCallback(
    (status) => {
      if (!playbook?.decisionCardId) return;
      try {
        const raw = sessionStorage.getItem(MIDDLEBY_DECISIONS_KEY);
        const cards = raw ? JSON.parse(raw) : getMiddlebyDecisionSeedCards();
        const found = cards.some((c) => c.id === playbook.decisionCardId);
        const next = found
          ? cards.map((c) => (c.id === playbook.decisionCardId ? { ...c, status } : c))
          : [...cards, { ...(playbook.decisionCard || { id: playbook.decisionCardId }), status }];
        sessionStorage.setItem(MIDDLEBY_DECISIONS_KEY, JSON.stringify(next));
      } catch {
        /* ignore */
      }
      setCardStatus(status);
    },
    [playbook]
  );

  const isDelivered = cardStatus === "approved" || (simulatedRun && !hasApprovalGate);
  const isRejected = cardStatus === "rejected";

  // Preparing loader: only when we are not already in a terminal state.
  useEffect(() => {
    if (!playbook || isDelivered || isRejected) {
      setPrepDone(true);
      return undefined;
    }
    setPrepDone(false);
    const t = setTimeout(() => setPrepDone(true), 1500);
    return () => clearTimeout(t);
  }, [playbook, regenTick, isDelivered, isRejected]);

  const handleApprove = useCallback(() => {
    if (!playbook) return;
    const now = new Date();
    writeDeliveredAt(playbook.id, now);
    setDeliveredAt(now);
    persistCardStatus("approved");
  }, [playbook, persistCardStatus]);

  const handleReject = useCallback(() => {
    persistCardStatus("rejected");
  }, [persistCardStatus]);

  const handleSendItNow = useCallback(() => {
    if (!playbook) return;
    const now = new Date();
    writeSimulatedRun(playbook.id, true);
    writeDeliveredAt(playbook.id, now);
    setSimulatedRun(true);
    setDeliveredAt(now);
  }, [playbook]);

  const handleReset = useCallback(() => {
    if (!playbook) return;
    resetPlaybookDemoState(playbook.id, playbook.decisionCardId);
    setCardStatus("pending");
    setSimulatedRun(false);
    setPreflightAcked(false);
    setPrepDone(false);
    setDeliveredAt(null);
    setRegenTick((t) => t + 1);
  }, [playbook]);

  const handleRegenerate = useCallback(() => {
    setPrepDone(false);
    setRegenTick((t) => t + 1);
  }, []);

  if (!playbook) {
    return <p className="text-sm text-text-secondary">Automation workflow not found.</p>;
  }

  // Derive phase from state.
  let phase;
  if (isDelivered) phase = "delivered";
  else if (!prepDone) phase = "preparing";
  else phase = "review";

  const impactTiles = liveContext?.impact?.length ? liveContext.impact : playbook.impact;
  const impactSparks = liveContext?.impactSparks?.length ? liveContext.impactSparks : [];
  const reviewSteps = outputs?.reviewSteps ?? [];
  const fallbackReviewSteps = reviewSteps.length
    ? reviewSteps
    : (playbook.automatedActions ?? []).map((a) => ({
        title: `${a.what}${a.where ? ` in ${a.where}` : ""}`,
        description: a.detail,
      }));
  const preflightChecks = outputs?.preflightChecks ?? [];
  const preparingMessage =
    outputs?.preparingMessage ||
    `Enterprise GPT is running ${playbook.label.toLowerCase()} and drafting artifacts for review...`;
  const primaryCtaLabel = hasApprovalGate ? "Approve and send actions" : "Send it now";
  const preflightBlocked = preflightIsBlocked(preflightChecks, preflightAcked);

  return (
    <div className="space-y-6">
      <header>
        {!hideStepOrdinal ? (
          <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">
            Step 3 of 3 · Automate
          </p>
        ) : null}
        <h2 className={`${hideStepOrdinal ? "" : "mt-1 "}text-lg font-semibold text-text-primary`}>
          {playbook.label}
        </h2>
        <p className="mt-2 text-sm text-text-secondary">
          {compactInsight
            ? "Enterprise GPT prepares the actions. You review the drafts, then approve."
            : `Prepared for ${playbook.owner}. Enterprise GPT drafts the actions, you review the artifacts, then approve.`}
        </p>
      </header>

      {/* Once the flow is Delivered/Approved, the phase stepper becomes
          visual noise - the use case is done running, we want it to read
          like a "configured and running" status page instead of a
          walk-through. Show the stepper only during Prepare / Review. */}
      {phase !== "delivered" ? (
        <AutomationPhaseStepper
          phases={hasApprovalGate ? WORKFLOW_PHASES : ANALYTICS_PHASES}
          currentId={phase}
          doneIds={DONE_BY_PHASE[phase] || []}
        />
      ) : null}

      {(signalRecap || monitorRecap) && !compactInsight ? (
        <section className="rounded-xl border border-border bg-surface-secondary/50 px-4 py-3 text-sm">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">
            Flow recap
          </p>
          {signalRecap ? <p className="mt-1 text-text-primary">Analyze: {signalRecap}</p> : null}
          {monitorRecap ? <p className="mt-1 text-text-secondary">Monitor: {monitorRecap}</p> : null}
        </section>
      ) : null}

      {phase === "preparing" ? (
        <AiPreparingLoader message={preparingMessage} />
      ) : null}

      {phase === "review" && !isRejected ? (
        <>
          <section className="rounded-xl border border-border bg-surface-default p-5 shadow-sm">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <Sparkles className="h-4 w-4 text-primary" aria-hidden />
                <h3 className="text-sm font-semibold text-text-primary">
                  What Enterprise GPT prepared
                </h3>
                <span className="inline-flex rounded-full bg-primary-light px-2 py-0.5 text-[10px] font-semibold text-primary dark:bg-primary/15">
                  Auto-generated
                </span>
              </div>
              <button
                type="button"
                onClick={handleRegenerate}
                className="inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium text-text-secondary hover:bg-surface-secondary/60"
              >
                <RefreshCw className="h-3.5 w-3.5" aria-hidden />
                Regenerate
              </button>
            </div>
            <ol className="mt-4 space-y-3">
              {fallbackReviewSteps.map((item, i) => (
                <li key={i} className="flex gap-3 rounded-lg border border-border bg-surface-secondary/40 p-3">
                  <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary text-xs font-semibold text-white">
                    {i + 1}
                  </span>
                  <div>
                    <p className="text-sm font-medium text-text-primary">{item.title}</p>
                    {item.description ? (
                      <p className="mt-0.5 text-xs text-text-secondary">{item.description}</p>
                    ) : null}
                  </div>
                </li>
              ))}
            </ol>
          </section>

          {hasApprovalGate && decisionCard ? (
            <section className="rounded-xl border border-primary/35 bg-primary-light/50 p-5 dark:border-primary/40 dark:bg-primary/10">
              <div className="flex items-center gap-2">
                <ClipboardCheck className="h-4 w-4 text-primary" aria-hidden />
                <h3 className="text-sm font-semibold text-text-primary">Ready for your approval</h3>
              </div>
              <p className="mt-2 text-sm text-text-secondary">
                Enterprise GPT has prepared the action below. Review the reasoning and the exact
                action, then use the buttons at the bottom of the page to approve or reject.
              </p>

              {decisionCard.reasoning ? (
                <>
                  <p className="mt-4 text-[10px] font-semibold uppercase tracking-wide text-text-muted">
                    Why this is recommended
                  </p>
                  <p className="mt-1 text-sm text-text-secondary">{decisionCard.reasoning}</p>
                </>
              ) : null}

              <p className="mt-4 text-[10px] font-semibold uppercase tracking-wide text-text-muted">
                What will happen if you approve
              </p>
              <p className="mt-1 rounded-lg border border-border bg-surface-default px-3 py-2.5 text-sm font-medium text-text-primary">
                {decisionCard.action}
              </p>

              <p className="mt-4 text-xs text-text-muted">
                Once approved, Enterprise GPT executes the action and sends the notifications
                automatically. You will see the delivered messages, HubSpot updates, and downloadable
                reports in the next step.
              </p>
            </section>
          ) : outputs ? (
            <AutomationOutputPanel outputs={outputs} runLabel={playbook.label} mode="draft" />
          ) : (
            <section className="rounded-xl border border-dashed border-border bg-surface-secondary/30 px-4 py-3 text-xs text-text-muted">
              Draft artifact previews are not yet seeded for this playbook. The approve action still
              runs the workflow and produces impact.
            </section>
          )}

          {preflightChecks.length ? (
            <section className="rounded-xl border border-border bg-surface-default p-5 shadow-sm">
              <AutomationPreflightChecks
                checks={preflightChecks}
                acknowledged={preflightAcked}
                onAcknowledgeChange={setPreflightAcked}
              />
            </section>
          ) : null}

          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-5">
            <button
              type="button"
              onClick={handleReject}
              className="inline-flex items-center gap-2 rounded-lg border border-border px-4 py-2.5 text-sm font-semibold text-status-error hover:bg-status-error-light"
            >
              <XCircle className="h-4 w-4" aria-hidden />
              Reject
            </button>
            <button
              type="button"
              onClick={hasApprovalGate ? handleApprove : handleSendItNow}
              disabled={preflightBlocked}
              className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-primary-hover disabled:opacity-60"
            >
              <CheckCircle2 className="h-4 w-4" aria-hidden />
              {primaryCtaLabel}
            </button>
          </div>
        </>
      ) : null}

      {isRejected ? (
        <section className="rounded-xl border border-border bg-surface-default p-5 shadow-sm">
          <div className="flex items-start gap-3">
            <XCircle className="mt-0.5 h-5 w-5 shrink-0 text-status-error" aria-hidden />
            <div>
              <p className="text-sm font-semibold text-text-primary">Rejected</p>
              <p className="mt-1 text-sm text-text-secondary">
                No actions were executed. Nothing was sent to Slack, email, or HubSpot.
              </p>
            </div>
          </div>
          <div className="mt-4">
            <AutomationStartOverControl onClick={handleReset} />
          </div>
        </section>
      ) : null}

      {phase === "delivered" ? (
        <>
          <section className="rounded-xl border border-status-success/40 bg-status-success-light/50 px-5 py-4 dark:bg-status-success/10">
            <div className="flex items-start gap-3">
              <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-status-success" aria-hidden />
              <div>
                <p className="text-sm font-semibold text-text-primary">
                  Configured and running
                </p>
                <p className="mt-1 text-sm text-text-secondary">
                  {hasApprovalGate
                    ? `${playbook.label} was approved and the action has been taken. Notifications are queued for ${playbook.owner}. Subsequent runs will follow the same approval flow.`
                    : `${playbook.label} ran successfully and is on its schedule. Delivered to ${playbook.owner}.`}
                </p>
              </div>
            </div>
            <div className="mt-4 border-t border-status-success/25 pt-4">
              <AutomationStartOverControl onClick={handleReset} compact />
            </div>
          </section>

          <section>
            <h3 className="text-sm font-semibold uppercase tracking-wide text-text-muted">Impact</h3>
            <div className="mt-3 grid gap-3 sm:grid-cols-3">
              {impactTiles.map((tile) => (
                <ImpactTile key={tile.label} label={tile.label} value={tile.value} sub={tile.sub} />
              ))}
            </div>
            {impactSparks.length > 0 ? (
              <div className="mt-4 grid gap-3 sm:grid-cols-2">
                {impactSparks.map((spark) => (
                  <ImpactSparkCard
                    key={spark.label}
                    label={spark.label}
                    before={spark.before}
                    after={spark.after}
                    improving={spark.improving}
                  />
                ))}
              </div>
            ) : null}
          </section>

          {outputs ? (
            <AutomationOutputPanel
              outputs={outputs}
              runLabel={playbook.label}
              mode="delivered"
              deliveredAt={deliveredAt}
              hideChannels={hasApprovalGate}
            />
          ) : null}

          {playbook.kpiTargets?.length ? (
            <AutomationKpiTargets kpiTargets={playbook.kpiTargets} />
          ) : null}

          {onRestartFlow ? (
            <div className="border-t border-border pt-4 space-y-1">
              <button
                type="button"
                onClick={onRestartFlow}
                className="text-sm font-semibold text-primary hover:underline"
              >
                Run this again from the start
              </button>
              <p className="text-xs text-text-muted">
                Returns to Analyze and clears this workflow so you can run the full insight to
                action path again.
              </p>
            </div>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
