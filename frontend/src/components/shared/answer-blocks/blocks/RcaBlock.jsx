// Structured Root Cause Analysis block.
//
// Shows candidate causes ranked by confidence, each with evidence rows,
// estimated impact, and a suggested fix. Deeper than a recommendation list
// because it makes the *causal reasoning* explicit  -  the audience can see
// what the AI thinks led to the observed effect, not just what to do.

import { Search, CheckCircle2, AlertTriangle, HelpCircle, ChevronRight, Wrench } from "lucide-react";
import BlockShell from "../BlockShell";

const CONFIDENCE_META = {
  high: { label: "High confidence", tone: "text-status-success", bg: "bg-status-success/10", ring: "ring-status-success/25", icon: CheckCircle2 },
  medium: { label: "Reasonable confidence", tone: "text-primary", bg: "bg-primary-light/60 dark:bg-primary/15", ring: "ring-primary/25", icon: AlertTriangle },
  low: { label: "Best guess", tone: "text-status-warning", bg: "bg-status-warning/10", ring: "ring-status-warning/30", icon: HelpCircle },
};

const PRIORITY_LABELS = {
  high: "Do this first",
  medium: "Worth doing",
  low: "Nice to have",
};

function CauseCard({ candidate, index }) {
  const meta = CONFIDENCE_META[candidate.confidence] || CONFIDENCE_META.medium;
  const Icon = meta.icon;
  return (
    <div className="rounded-xl border border-border bg-surface-default p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 items-start gap-2">
          <span
            className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary-light text-primary ring-1 ring-primary/20 dark:bg-primary/15"
            aria-hidden
          >
            <span className="text-xs font-semibold">{index + 1}</span>
          </span>
          <div className="min-w-0">
            <p className="text-sm font-semibold text-text-primary">{candidate.cause}</p>
            {candidate.estimatedImpact ? (
              <p className="mt-0.5 text-xs text-text-secondary">
                Estimated impact: <span className="font-semibold text-text-primary">{candidate.estimatedImpact}</span>
              </p>
            ) : null}
          </div>
        </div>
        <span
          className={`inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium ring-1 ${meta.bg} ${meta.tone} ${meta.ring}`}
          title="How much to trust this as the cause"
        >
          <Icon size={10} />
          {meta.label}
        </span>
      </div>

      {candidate.evidence?.length ? (
        <div className="mt-3 rounded-lg bg-surface-secondary/60 p-2">
          <p className="text-[10px] font-medium uppercase tracking-wide text-text-muted">
            Evidence we found
          </p>
          <ul className="mt-1 space-y-1">
            {candidate.evidence.map((e, i) => (
              <li key={i} className="flex items-start gap-1.5 text-xs text-text-secondary">
                <ChevronRight size={11} className="mt-0.5 shrink-0 text-text-muted" />
                <span className="min-w-0 flex-1">{e}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {candidate.suggestedFix ? (
        <div className="mt-2 flex items-start gap-2 rounded-lg border border-primary/25 bg-primary-light/30 p-2 dark:bg-primary/10">
          <Wrench size={12} className="mt-0.5 shrink-0 text-primary" aria-hidden />
          <div className="min-w-0 flex-1">
            <p className="text-[10px] font-medium uppercase tracking-wide text-primary">Suggested fix</p>
            <p className="mt-0.5 text-xs text-text-primary">{candidate.suggestedFix}</p>
            {candidate.fixPriority ? (
              <span className="mt-1 inline-block rounded-full bg-primary/15 px-2 py-0.5 text-[10px] font-medium text-primary">
                {PRIORITY_LABELS[candidate.fixPriority] || candidate.fixPriority}
              </span>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}

export default function RcaBlock({ block }) {
  const causes = block.candidateCauses || [];
  if (!causes.length) return null;

  return (
    <BlockShell
      block={block}
      title="Likely causes"
      subtitle={block.question ? `Answering: "${block.question}"` : "Ranked by how well the evidence fits"}
      bare
    >
      <div className="rounded-xl border border-border bg-surface-secondary/40 p-3">
        <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-text-primary">
          <Search size={12} className="text-primary" />
          What we think caused it
        </p>
        <div className="space-y-2">
          {causes.map((c, i) => (
            <CauseCard key={i} candidate={c} index={i} />
          ))}
        </div>
      </div>
    </BlockShell>
  );
}
