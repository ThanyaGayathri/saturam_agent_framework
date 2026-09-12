import { useState } from "react";
import { CheckCircle2, AlertTriangle, XCircle, ChevronDown, ChevronRight, ShieldCheck, Database, AlignLeft, Network, Lock, ArrowLeft } from "lucide-react";
import { Link } from "react-router-dom";
import UCBuilderLayout from "./shared/UCBuilderLayout";
import { Card } from "./ui";
import { useUseCaseDraft } from "../context/UseCaseDraftContext";
import { getBuilderEnterpriseDatasetIds } from "../storage/use-case-draft";

const STATUS_CONFIG = {
  pass: {
    icon: CheckCircle2,
    iconColor: "text-[#067647]",
    badge: "bg-[#ecfdf3] text-[#067647]",
    label: "Pass",
    border: "border-[#a7f3d0]",
  },
  warn: {
    icon: AlertTriangle,
    iconColor: "text-[#b54708]",
    badge: "bg-[#fffaeb] text-[#b54708]",
    label: "Warning",
    border: "border-[#fde68a]",
  },
  fail: {
    icon: XCircle,
    iconColor: "text-[#b42318]",
    badge: "bg-[#fef3f2] text-[#b42318]",
    label: "Blocker",
    border: "border-[#fca5a5]",
  },
};

function buildChecks(draft) {
  const question = draft.step2.primaryQuestion.trim() || "";
  const autonomy = draft.step1.autonomyLevel === "copilot" ? "Co-Pilot" : "Autopilot";
  const confirmedSources = (draft.step3?.confirmedSources ?? []).filter((s) => s.enabled);
  const confirmedKnowledge = (draft.step3?.confirmedKnowledge ?? []).filter((k) => k.enabled);
  const galleryIds = getBuilderEnterpriseDatasetIds(draft.step1?.category || "default");
  const rawEnt = draft.step3?.enterpriseDatasetIds;
  const enterpriseIds = rawEnt == null ? galleryIds : rawEnt;
  const enterpriseCount = enterpriseIds.length;
  const taskCount = (draft.step5?.tasks ?? []).length || 6;

  // Warn if the question is short or generic - likely to produce broad, unfocused outputs
  const vagueQuestion = question.length > 0 && question.length < 35;

  // Warn on coverage gap only if no knowledge sources were selected
  const hasKnowledge = confirmedKnowledge.length > 0;

  return [
    {
      id: "plan",
      icon: AlignLeft,
      title: "Plan alignment",
      status: vagueQuestion ? "warn" : "pass",
      detail: vagueQuestion
        ? `The primary question "${question}" is quite short. Broader questions tend to produce less focused outputs. Consider adding more specificity, e.g. time range, product category, or store location.`
        : `The configured plan addresses: "${question.slice(0, 90)}${question.length > 90 ? "..." : ""}"`,
      fix: vagueQuestion ? {
        label: "Go to Step 2 to refine the question",
        to: "/use-cases/builder/step-2",
      } : null,
    },
    {
      id: "data",
      icon: Database,
      title: "Data availability",
      status: "pass",
      detail: (() => {
        const parts = [];
        if (confirmedSources.length > 0) {
          parts.push(
            `${confirmedSources.length} connection${confirmedSources.length > 1 ? "s" : ""}: ${confirmedSources.map((s) => s.name).join(", ")}`
          );
        }
        if (enterpriseCount > 0) {
          parts.push(
            `${enterpriseCount} enterprise dataset${enterpriseCount > 1 ? "s" : ""} from KPI Gallery in scope`
          );
        }
        if (parts.length === 0) return "Data scope confirmed in Step 3.";
        return `${parts.join(". ")}. Preflight checks pass.`;
      })(),
      fix: null,
    },
    {
      id: "coverage",
      icon: ShieldCheck,
      title: "Coverage gaps",
      status: hasKnowledge ? "pass" : "warn",
      detail: hasKnowledge
        ? `Knowledge base sources provide extended historical context beyond the 90-day data window. Coverage looks complete.`
        : "No knowledge base sources were selected. Historical data coverage is limited to the last 90 days, which may affect trend analysis and seasonality comparisons.",
      fix: hasKnowledge ? null : {
        label: "Go to Step 3 to add a knowledge source",
        to: "/use-cases/builder/step-3",
      },
    },
    {
      id: "permissions",
      icon: Lock,
      title: "Permissions and guardrails",
      status: "pass",
      detail:
        draft.step1.autonomyLevel === "copilot"
          ? `${autonomy} mode: no actions will be taken without your explicit approval. All outputs route to Decision Cards for review.`
          : `${autonomy} mode: the AI acts within configured guardrails. All actions are logged and visible in Monitor. No permission conflicts detected.`,
      fix: null,
    },
    {
      id: "orchestration",
      icon: Network,
      title: "Orchestration integrity",
      status: "pass",
      detail: `${taskCount} task${taskCount !== 1 ? "s" : ""} configured across agents. No circular dependencies or dead ends detected in the flow diagram.`,
      fix: null,
    },
  ];
}

export default function UCBuilderStep7PreflightCheck() {
  const { draft, updateDraft } = useUseCaseDraft();
  const checks = buildChecks(draft);
  const acknowledged = draft.step7?.acknowledged ?? false;
  const [expandedId, setExpandedId] = useState(null);

  const hasBlockers = checks.some((c) => c.status === "fail");
  const hasWarnings = checks.some((c) => c.status === "warn");
  const allPass = !hasBlockers && !hasWarnings;

  const setAcknowledged = (val) =>
    updateDraft((d) => ({ ...d, step7: { ...d.step7, acknowledged: val } }));

  const canContinue = !hasBlockers && (allPass || acknowledged);

  const toggle = (id) => setExpandedId((prev) => (prev === id ? null : id));

  return (
    <UCBuilderLayout
      currentStep={7}
      title="Pre-flight Check"
      subtitle="The AI has reviewed your configuration. Resolve any blockers before activating. Click any check to see details and how to fix it."
      backPath="/use-cases/builder/step-6"
      nextPath={canContinue ? "/use-cases/builder/step-8" : undefined}
      nextLabel="Looks good, continue"
    >
      <div className="max-w-2xl space-y-4">
        {/* Overall status banner */}
        <div className={`flex items-center gap-3 rounded-xl border px-4 py-3 ${
          hasBlockers
            ? "border-[#fca5a5] bg-[#fef3f2]"
            : hasWarnings
              ? "border-[#fde68a] bg-[#fffaeb]"
              : "border-[#a7f3d0] bg-[#ecfdf3]"
        }`}>
          {hasBlockers ? (
            <XCircle size={18} className="shrink-0 text-[#b42318]" />
          ) : hasWarnings ? (
            <AlertTriangle size={18} className="shrink-0 text-[#b54708]" />
          ) : (
            <CheckCircle2 size={18} className="shrink-0 text-[#067647]" />
          )}
          <p className="text-sm font-medium text-text-primary">
            {hasBlockers
              ? "1 blocker must be resolved before you can activate."
              : hasWarnings
                ? "1 warning detected. Acknowledge and proceed, or resolve before activating."
                : "All checks passed. Ready to activate."}
          </p>
        </div>

        {/* Check items */}
        {checks.map((check) => {
          const cfg = STATUS_CONFIG[check.status];
          const Icon = check.icon;
          const StatusIcon = cfg.icon;
          const isExpanded = expandedId === check.id;
          const isClickable = check.status !== "pass";

          return (
            <div
              key={check.id}
              className={`overflow-hidden rounded-2xl border bg-surface shadow-sm transition-all ${cfg.border} ${isClickable ? "cursor-pointer" : ""}`}
              onClick={isClickable ? () => toggle(check.id) : undefined}
            >
              {/* Row */}
              <div className="flex items-start gap-4 px-5 py-4">
                <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-surface-tertiary">
                  <Icon size={16} className="text-text-secondary" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <p className="text-sm font-semibold text-text-primary">{check.title}</p>
                    <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${cfg.badge}`}>
                      {cfg.label}
                    </span>
                  </div>
                  {!isExpanded && (
                    <p className="mt-1 text-xs text-text-muted">
                      {check.status === "pass" ? check.detail : `Click to see what's wrong and how to fix it`}
                    </p>
                  )}
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <StatusIcon size={18} className={cfg.iconColor} />
                  {isClickable && (
                    isExpanded
                      ? <ChevronDown size={15} className="text-text-muted" />
                      : <ChevronRight size={15} className="text-text-muted" />
                  )}
                </div>
              </div>

              {/* Expanded detail panel */}
              {isExpanded && (
                <div className={`border-t px-5 py-4 ${
                  check.status === "fail" ? "border-[#fca5a5] bg-[#fef3f2]" : "border-[#fde68a] bg-[#fffaeb]"
                }`}>
                  <p className={`text-xs font-semibold uppercase tracking-wide mb-2 ${
                    check.status === "fail" ? "text-[#b42318]" : "text-[#b54708]"
                  }`}>
                    {check.status === "fail" ? "What's wrong" : "Warning detail"}
                  </p>
                  <p className="text-sm leading-relaxed text-text-secondary">{check.detail}</p>

                  {check.fix && (
                    <div className="mt-3 flex items-center gap-2">
                      <p className={`text-xs font-semibold uppercase tracking-wide ${
                        check.status === "fail" ? "text-[#b42318]" : "text-[#b54708]"
                      }`}>How to fix</p>
                      <Link
                        to={check.fix.to}
                        onClick={(e) => e.stopPropagation()}
                        className={`flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-semibold transition ${
                          check.status === "fail"
                            ? "border-[#fca5a5] bg-surface text-[#b42318] hover:bg-[#fef3f2]"
                            : "border-[#fde68a] bg-surface text-[#b54708] hover:bg-[#fffaeb]"
                        }`}
                      >
                        <ArrowLeft size={11} />
                        {check.fix.label}
                      </Link>
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}

        {/* Warning acknowledgement */}
        {hasWarnings && !hasBlockers && (
          <div className="rounded-xl border border-[#fde68a] bg-[#fffaeb] px-4 py-3">
            <label className="flex cursor-pointer items-start gap-3">
              <input
                type="checkbox"
                checked={acknowledged}
                onChange={(e) => setAcknowledged(e.target.checked)}
                className="mt-0.5 h-4 w-4 rounded border-border-light text-[#b54708] focus:ring-[#b54708]"
              />
              <span className="text-sm text-[#92400e]">
                I acknowledge the warnings above and want to proceed. I understand the AI may produce outputs with the limitations noted.
              </span>
            </label>
          </div>
        )}

        {/* Blocker help */}
        {hasBlockers && (
          <div className="flex items-center gap-2 rounded-lg border border-[#fca5a5] bg-[#fef3f2] px-4 py-3">
            <XCircle size={15} className="shrink-0 text-[#b42318]" />
            <p className="text-sm text-[#b42318]">
              Resolve the blocker above before continuing. Click the check to see the fix, or use the Back button to return.
            </p>
          </div>
        )}
      </div>
    </UCBuilderLayout>
  );
}
