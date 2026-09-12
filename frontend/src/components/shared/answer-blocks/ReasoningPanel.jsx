// Top-of-message explainability panel. Renders when the assistant message
// carries a `reasoning` payload  -  collapses by default so audiences can
// choose to open it, but the header is always visible so nobody misses that
// the reasoning is available.

import { useState } from "react";
import { HelpCircle, ChevronDown, ChevronUp, AlertTriangle, ListChecks } from "lucide-react";

// Only surface an "Example numbers" pill for illustrative data  -  a confidence
// grading on every answer would just add doubt in a demo audience.
const ILLUSTRATIVE_STYLE = "bg-status-warning/10 text-status-warning ring-status-warning/30";

export default function ReasoningPanel({ reasoning }) {
  const [open, setOpen] = useState(false);
  if (!reasoning || !reasoning.summary) return null;

  const isIllustrative = reasoning.confidence === "illustrative";

  return (
    <div className="rounded-xl border border-border bg-surface-secondary/40 p-3">
      <div className="flex items-start gap-2">
        <span
          className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary-light text-primary ring-1 ring-primary/20 dark:bg-primary/15"
          aria-hidden
        >
          <HelpCircle size={12} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-xs font-semibold text-text-primary">Why you're seeing this</p>
            {isIllustrative ? (
              <span
                className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium ring-1 ${ILLUSTRATIVE_STYLE}`}
                title="This answer uses example numbers to show the layout. Real Middleby data would replace them once the underlying signals are live."
              >
                <AlertTriangle size={10} />
                Example numbers
              </span>
            ) : null}
          </div>
          <p className="mt-1 text-sm text-text-secondary">{reasoning.summary}</p>
          {(reasoning.steps?.length || reasoning.caveats?.length) ? (
            <button
              type="button"
              onClick={() => setOpen((o) => !o)}
              className="mt-2 inline-flex items-center gap-1 text-[11px] font-medium text-primary hover:underline"
              aria-expanded={open}
            >
              {open ? (
                <>
                  Hide the steps <ChevronUp size={11} />
                </>
              ) : (
                <>
                  Show the steps <ChevronDown size={11} />
                </>
              )}
            </button>
          ) : null}
          {open && reasoning.steps?.length ? (
            <div className="mt-2 rounded-lg bg-surface-default p-3 dark:bg-surface-secondary">
              <p className="mb-1 flex items-center gap-1 text-[11px] font-semibold text-text-primary">
                <ListChecks size={11} />
                How we built this
              </p>
              <ol className="space-y-1 pl-4 text-xs text-text-secondary">
                {reasoning.steps.map((step, i) => (
                  <li key={i} className="list-decimal">{step}</li>
                ))}
              </ol>
            </div>
          ) : null}
          {open && reasoning.caveats?.length ? (
            <div className="mt-2 rounded-lg border border-status-warning/25 bg-status-warning/5 p-3">
              <p className="mb-1 flex items-center gap-1 text-[11px] font-semibold text-status-warning">
                <AlertTriangle size={11} />
                Keep in mind
              </p>
              <ul className="space-y-1 pl-4 text-xs text-text-secondary">
                {reasoning.caveats.map((c, i) => (
                  <li key={i} className="list-disc">{c}</li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
