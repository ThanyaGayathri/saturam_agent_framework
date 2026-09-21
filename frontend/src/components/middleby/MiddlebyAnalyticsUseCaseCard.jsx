import { ArrowRight, ChevronRight, Gauge, Zap } from "lucide-react";
import { EnterpriseProductMark } from "../shared/EnterpriseProductMark";
import {
  useCaseCardSurface,
  useCaseCardBody,
  useCaseCardBadge,
  useCaseCardTitle,
  useCaseCardDescription,
  useCaseCardChipRow,
  useCaseCardFooter,
  useCaseCardArrow,
} from "../shared/useCaseCardStyles";

/**
 * Analytics use-case card: title, prominent sample question, optional context line.
 */
export default function MiddlebyAnalyticsUseCaseCard({
  title,
  problemSignal,
  sampleQuestion,
  actionType,
  sourceLine,
  crossSourcePill,
  impactLine,
  evidenceLabel,
  automationReady = false,
  automationPlaybookCount = 0,
  automationLine = "",
  phaseLabel = "",
  kpiNames,
  compact = false,
  onAnalyze,
}) {
  const handleOpen = () => onAnalyze?.();
  const context = (problemSignal || impactLine || "").trim();
  const question = (sampleQuestion || "").trim();
  const primaryQuestion = question || context;
  const showContext =
    question && context && question.toLowerCase() !== context.toLowerCase();

  if (compact) {
    return (
      <button
        type="button"
        onClick={handleOpen}
        className="group flex w-full items-center gap-3 rounded-lg border border-border bg-surface-default px-3.5 py-2.5 text-left transition hover:border-primary/35 hover:bg-primary-light/40 dark:hover:bg-primary/10"
      >
        <span className="min-w-0 flex-1 truncate text-sm font-medium text-text-primary">{title}</span>
        <ChevronRight
          className="h-4 w-4 shrink-0 text-text-muted transition group-hover:text-primary"
          aria-hidden
        />
      </button>
    );
  }

  const hasBadges = Boolean(actionType || automationReady);

  return (
    <button type="button" onClick={handleOpen} className={useCaseCardSurface}>
      <div className={useCaseCardBody}>
        <div className="min-h-0 flex-1">
          {/* Title first, on its own line: the badges used to share the line with it and
              squeezed a 14px title into whatever was left. */}
          <h3 className={useCaseCardTitle}>{title}</h3>
          {hasBadges ? (
            <div className="mt-2 flex flex-wrap items-center gap-2">
              {actionType ? (
                <span className={`${useCaseCardBadge} border-border bg-surface text-text-secondary`}>
                  {actionType}
                </span>
              ) : null}
              {automationReady ? (
                <span
                  className={`${useCaseCardBadge} border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/15 dark:text-amber-100`}
                >
                  <Zap className="h-3 w-3" aria-hidden />
                  Workflow{automationPlaybookCount > 1 ? ` · ${automationPlaybookCount}` : ""}
                </span>
              ) : null}
            </div>
          ) : null}
          {primaryQuestion ? (
            <p className={useCaseCardDescription}>{primaryQuestion}</p>
          ) : null}
          {showContext ? (
            <p className="mt-2 text-xs leading-relaxed text-text-muted line-clamp-2">{context}</p>
          ) : null}
          {automationReady && automationLine ? (
            <p className="mt-2 text-xs font-medium leading-relaxed text-primary line-clamp-3">
              Example workflow: {automationLine}
            </p>
          ) : null}
          {sourceLine ? (
            <p className="mt-2 text-xs text-text-muted">Source: {sourceLine}</p>
          ) : null}
          {Array.isArray(kpiNames) && kpiNames.length > 0 ? (
            <div className={useCaseCardChipRow}>
              <span
                className="inline-flex items-center gap-1 text-[10px] font-semibold uppercase tracking-[0.05em] text-text-muted"
                title="KPIs this use case moves"
              >
                <Gauge className="h-3 w-3" strokeWidth={2} aria-hidden />
                Moves
              </span>
              {kpiNames.map((k) => (
                <span
                  key={k}
                  title={k}
                  className="inline-flex max-w-full items-center gap-1 rounded-md border border-[#a9f0d1] bg-[#ecfdf3] px-1.5 py-0.5 text-[11px] font-medium text-[#067647] dark:border-[#067647]/35 dark:bg-[#067647]/10 dark:text-[#6ce9a6]"
                >
                  <span className="truncate">{k}</span>
                </span>
              ))}
            </div>
          ) : null}
        </div>
        <div className={useCaseCardFooter}>
          <EnterpriseProductMark size="sm" tone="brand" />
          <ArrowRight className={useCaseCardArrow} strokeWidth={2.5} aria-hidden />
        </div>
      </div>
    </button>
  );
}
